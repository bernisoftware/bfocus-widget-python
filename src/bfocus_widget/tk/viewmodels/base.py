"""Base dos view-models: observável + um `Runner` que separa a lógica da thread da UI.

As telas Tk usam `TkRunner` (trabalho de rede em threads, resultado de volta na thread do
Tk). Os testes usam `SyncRunner`: tudo síncrono e um relógio manual para os timers.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional

log = logging.getLogger("bfocus_widget")


class Observable:
    def __init__(self) -> None:
        self._observers: List[Callable[[], None]] = []

    def subscribe(self, fn: Callable[[], None]) -> Callable[[], None]:
        self._observers.append(fn)

        def cancel() -> None:
            try:
                self._observers.remove(fn)
            except ValueError:
                pass

        return cancel

    def changed(self) -> None:
        for fn in list(self._observers):
            try:
                fn()
            except Exception:  # noqa: BLE001 - uma tela com erro não derruba as outras
                log.exception("bfocus tk: observador falhou")


class Runner:
    """Contrato de execução usado pelos view-models."""

    def submit(self, fn: Callable[[], Any], on_ok: Optional[Callable[[Any], None]] = None,
               on_err: Optional[Callable[[BaseException], None]] = None) -> None:
        """Roda `fn` fora da UI; `on_ok`/`on_err` voltam na thread da UI."""
        raise NotImplementedError

    def call_soon(self, fn: Callable[[], None]) -> None:
        """Agenda `fn` na thread da UI (pode ser chamado de qualquer thread)."""
        raise NotImplementedError

    def after(self, ms: int, fn: Callable[[], None]) -> Any:
        raise NotImplementedError

    def cancel(self, handle: Any) -> None:
        raise NotImplementedError


class SyncRunner(Runner):
    """Para testes: execução imediata e timers com relógio manual (`advance`)."""

    def __init__(self) -> None:
        self.now = 0
        self._seq = 0
        self._timers: List[list] = []

    def submit(self, fn, on_ok=None, on_err=None):  # type: ignore[override]
        try:
            result = fn()
        except Exception as err:  # noqa: BLE001
            if on_err is not None:
                on_err(err)
            else:
                log.exception("bfocus tk: tarefa falhou")
            return
        if on_ok is not None:
            on_ok(result)

    def call_soon(self, fn):  # type: ignore[override]
        fn()

    def after(self, ms, fn):  # type: ignore[override]
        self._seq += 1
        timer = [self.now + int(ms), self._seq, fn, True]
        self._timers.append(timer)
        return timer

    def cancel(self, handle):  # type: ignore[override]
        if handle is not None:
            handle[3] = False

    def pending(self) -> int:
        return sum(1 for t in self._timers if t[3])

    def advance(self, ms: int) -> None:
        target = self.now + int(ms)
        while True:
            due = [t for t in self._timers if t[3] and t[0] <= target]
            if not due:
                break
            timer = min(due, key=lambda t: (t[0], t[1]))
            timer[3] = False
            self.now = timer[0]
            timer[2]()
        self._timers = [t for t in self._timers if t[3]]
        self.now = target


class Periodic:
    """Atualização periódica de uma tela (ex.: lista a cada 30 s)."""

    def __init__(self, runner: Runner, interval_ms: int, fn: Callable[[], None]) -> None:
        self._runner = runner
        self._interval = interval_ms
        self._fn = fn
        self._handle: Any = None
        self.active = False

    def start(self, immediate: bool = False) -> None:
        if self.active:
            return
        self.active = True
        if immediate:
            self._fn()
        self._schedule()

    def _schedule(self) -> None:
        if self.active:
            self._handle = self._runner.after(self._interval, self._tick)

    def _tick(self) -> None:
        if not self.active:
            return
        self._fn()
        self._schedule()

    def stop(self) -> None:
        self.active = False
        self._runner.cancel(self._handle)
        self._handle = None
