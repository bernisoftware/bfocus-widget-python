"""Host Tk: a UI nativa ligada ao `BFocusWidget` (sem WebView; Fase 6 do plano)."""
from __future__ import annotations

import logging
import queue
import tkinter as tk
from concurrent.futures import Future, ThreadPoolExecutor
from tkinter import filedialog
from typing import Any, Callable, List, Optional, Tuple

from ..client.api import WidgetApi
from ..core.config import BFocusConfig
from ..core.host import WidgetHost
from ..core.widget import BFocusWidget
from ..shared import Translator, tokens
from .viewmodels.app import AppVM
from .viewmodels.base import Runner
from .viewmodels.context import Ctx
from .viewmodels.read_state import ReadState
from .viewmodels.release_notes import BannerVM, HistoryVM
from .views.common import on_app_focus
from .views.panel import TicketsPanel
from .views.release_notes import BannerWindow, HistoryWindow

log = logging.getLogger("bfocus_widget")


class TkRunner(Runner):
    """Rede em threads; resultado de volta na thread do Tk (o Tk não é thread-safe)."""

    PUMP_MS = 30

    def __init__(self, root: tk.Misc, workers: int = 4) -> None:
        self.root = root
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bfocus-tk")
        self._queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self.errors: List[BaseException] = []
        self._alive = True
        self._pump()

    @classmethod
    def for_root(cls, widget: tk.Misc) -> "TkRunner":
        root = widget._root()  # type: ignore[attr-defined]
        runner = getattr(root, "_bfocus_runner", None)
        if runner is None or not runner._alive:
            runner = cls(root)
            root._bfocus_runner = runner  # type: ignore[attr-defined]
        return runner

    def _pump(self) -> None:
        while True:
            try:
                fn = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as err:  # noqa: BLE001
                self.errors.append(err)
                log.exception("bfocus tk: callback falhou")
        try:
            self.root.after(self.PUMP_MS, self._pump)
        except (tk.TclError, RuntimeError):
            self.close()

    def call_soon(self, fn: Callable[[], None]) -> None:
        self._queue.put(fn)

    def submit(self, fn: Callable[[], Any], on_ok: Optional[Callable[[Any], None]] = None,
               on_err: Optional[Callable[[BaseException], None]] = None) -> None:
        if not self._alive:
            return

        def done(f: "Future[Any]") -> None:
            err = f.exception()
            if err is not None:
                self.call_soon(lambda: on_err(err) if on_err else log.debug("bfocus tk: tarefa falhou: %s", err))
            elif on_ok is not None:
                self.call_soon(lambda: on_ok(f.result()))

        try:
            self._pool.submit(fn).add_done_callback(done)
        except RuntimeError:
            pass  # pool encerrado (janela fechando)

    def after(self, ms: int, fn: Callable[[], None]) -> Any:
        try:
            return self.root.after(int(ms), fn)
        except tk.TclError:
            return None

    def cancel(self, handle: Any) -> None:
        if handle is not None:
            try:
                self.root.after_cancel(handle)
            except (tk.TclError, ValueError):
                pass

    def close(self) -> None:
        self._alive = False
        self._pool.shutdown(wait=False)


class BFocusTkHost(WidgetHost):
    """Liga a UI Tk ao núcleo: `BFocusTkHost(widget, root)` e depois `widget.start()`."""

    def __init__(self, widget: BFocusWidget, master: tk.Misc, *, api: Any = None, runner: Optional[Runner] = None) -> None:
        self.widget = widget
        self.master = master
        self.runner = runner or TkRunner.for_root(master)
        self.api = api or WidgetApi.for_widget(widget)
        # Ponto de extensão: onde salvar um anexo (padrão: "salvar como").
        self.save_path_provider: Callable[[str], Optional[str]] = self._ask_save_path
        self._app: Optional[AppVM] = None
        self._panel: Optional[TicketsPanel] = None
        self._banner: Optional[BannerWindow] = None
        self._history: Optional[HistoryWindow] = None
        widget.attach_host(self, self.runner.call_soon)
        on_app_focus(master, self.app_focused)

    def app_focused(self) -> None:
        """O app voltou ao foco (o "voltou à aba" do web): confere na hora o que pode travar."""
        if self._banner is None:
            self.widget.refresh()  # banner: uma release com ciência publicada agora abre já
        if self._app is not None:
            self._app.on_focus()  # splash dos chamados, se o widget estiver aberto na lista

    @property
    def t(self) -> Translator:
        return Translator(self.widget.config.locale or "pt_BR")

    @property
    def primary(self) -> str:
        if self._app is not None and self._app.config is not None:
            return self._app.primary
        return self.widget.primary_color or tokens()["brandFallback"]

    def _ctx(self) -> Ctx:
        locale = self.widget.config.locale or "pt_BR"
        return Ctx(api=self.api, runner=self.runner, t=Translator(locale),
                   read=ReadState(self.widget.store, self.widget.scope), locale=locale,
                   primary=lambda: self.primary)

    # ── WidgetHost ────────────────────────────────────────────────────────────
    def show_tickets(self, target: Optional[dict]) -> None:
        w = self.widget
        if self._app is None or self._panel is None:
            cfg: BFocusConfig = w.config
            self._app = AppVM(
                self._ctx(), show_release_notes=cfg.show_release_notes, customer_name=cfg.customer.name,
                on_unread=w.notify_unread, on_seen=w.notify_seen, on_branding=w.notify_branding,
                on_error=w.notify_error, on_close=w.close, on_download=self.download,
            )
            self._panel = TicketsPanel(self.master, self._app)
            self._app.start(target)
        elif target is not None:
            self._app.navigate(target)
        self._panel.show()
        self._app.set_visible(True)

    def hide_tickets(self) -> None:
        if self._panel is not None:
            self._panel.hide()
        if self._app is not None:
            self._app.set_visible(False)

    def show_banner(self, ids: List[str]) -> None:
        if self._banner is not None:
            return
        vm = BannerVM(self._ctx(), ids, on_done=self.widget.notify_banner_done)
        self._banner = BannerWindow(self.master, vm, self.primary)

    def hide_banner(self) -> None:
        banner, self._banner = self._banner, None
        if banner is not None:
            banner.close()

    def show_history(self) -> None:
        if self._history is not None:
            try:
                if self._history.winfo_exists():
                    self._history.reopen()
                    return
            except tk.TclError:
                pass
        brand = self._app.tenant_name if self._app is not None else ""
        vm = HistoryVM(self._ctx(), on_close=self.widget.notify_history_closed)
        self._history = HistoryWindow(self.master, vm, self.primary, brand)

    def hide_history(self) -> None:
        if self._history is not None:
            try:
                self._history.withdraw()
            except tk.TclError:
                self._history = None

    def reset(self) -> None:
        """logout()/troca de identidade: descarta telas e estado das telas."""
        if self._app is not None:
            self._app.dispose()
        for win in (self._panel, self._history):
            if win is not None:
                try:
                    win.destroy()
                except tk.TclError:
                    pass
        self.hide_banner()
        self._app = self._panel = self._history = None

    def reload(self) -> None:
        was_open = self._panel is not None and self._panel.winfo_viewable()
        self.reset()
        if was_open:
            self.show_tickets(None)

    # ── arquivos ──────────────────────────────────────────────────────────────
    def _ask_save_path(self, filename: str) -> Optional[str]:
        parent = self._panel or self.master
        return filedialog.asksaveasfilename(parent=parent, initialfile=filename, title=self.t.native("save_attachment")) or None

    def download(self, url: str, filename: str) -> None:
        """Baixa a URL pré-assinada com o nome original ("salvar como")."""
        if not url:
            return
        path = self.save_path_provider(filename)
        if path:
            self.runner.submit(lambda: self.api.download_to(url, path), None, lambda e: log.warning("bfocus: download falhou: %s", e))


def init(config: BFocusConfig, master: tk.Misc, **kwargs: Any) -> Tuple[BFocusWidget, BFocusTkHost]:
    """Atalho: widget + UI Tk ligados e a consulta já rodando."""
    widget = BFocusWidget(config, **kwargs)
    host = BFocusTkHost(widget, master)
    widget.start()
    return widget, host
