"""Tempo real do chat (spec §5.3), espelho de `widget/src/embed/chatStream.ts`.

    POST /widget/chat/stream-ticket → {mode, ticket, poll_interval_ms, last_event_id}
    mode=sse  → GET /api/v1/stream?ticket=…&last_event_id=…
    mode=poll → GET …/conversations/{id}/events?after=n a cada poll_interval_ms

- Ticket é de uso único: toda (re)conexão pede outro e passa o último id recebido.
- Espera exponencial com variação aleatória, até 30 s. Sem abrir em 20 s, tenta de novo.
- 4xx (exceto 408, 425 e 429) é fatal.
- Eventos repetidos são descartados pelo `id` (guarda os últimos 1000).
- `on_connected` a cada (re)conexão: quem usa recarrega a conversa inteira.
- Em modo poll, a cada 5 min tenta voltar ao SSE.

Roda numa thread própria; os callbacks saem dessa thread (quem usa faz o marshal para a UI).
"""
from __future__ import annotations

import http.client
import json
import logging
import random
import socket
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlsplit

from ..core.http import ApiError
from .sse import SseEvent, SseParser

log = logging.getLogger("bfocus_widget")

MAX_BACKOFF = 30.0
POLL_UPGRADE_SECONDS = 5 * 60
SEEN_LIMIT = 1000
SSE_OPEN_TIMEOUT = 20.0
# O servidor manda ": ping" periodicamente; silêncio além disto = conexão morta.
SSE_READ_TIMEOUT = 60.0

# Um stream por conversa no processo (o web faz o mesmo no globalThis).
_active: Dict[str, "ChatStream"] = {}
_active_lock = threading.Lock()


def is_fatal(err: BaseException) -> bool:
    return isinstance(err, ApiError) and 400 <= err.status < 500 and err.status not in (408, 425, 429)


class ChatStream:
    def __init__(
        self,
        api: Any,
        conversation_id: str,
        on_event: Callable[[dict], None],
        on_status: Optional[Callable[[str], None]] = None,
        on_connected: Optional[Callable[[], None]] = None,
        on_fatal: Optional[Callable[[BaseException], None]] = None,
        *,
        rand: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
        sse_open_timeout: float = SSE_OPEN_TIMEOUT,
        sse_read_timeout: float = SSE_READ_TIMEOUT,
        poll_upgrade_seconds: float = POLL_UPGRADE_SECONDS,
    ) -> None:
        self.api = api
        self.conversation_id = conversation_id
        self._on_event = on_event
        self._on_status = on_status
        self._on_connected = on_connected
        self._on_fatal = on_fatal
        self._rand = rand
        self._clock = clock
        self._open_timeout = sse_open_timeout
        self._read_timeout = sse_read_timeout
        self._upgrade_after = poll_upgrade_seconds
        self._lock = threading.RLock()
        self._running = False
        self._gen = 0
        self._wake = threading.Event()
        self._conn: Optional[http.client.HTTPConnection] = None
        self._attempt = 0
        self._status = "idle"
        self.last_event_id: Optional[int] = None
        self._seen: "OrderedDict[int, None]" = OrderedDict()

    @property
    def status(self) -> str:
        return self._status

    @property
    def running(self) -> bool:
        return self._running

    # ── controle ──────────────────────────────────────────────────────────────
    def start(self) -> None:
        """Idempotente. Retoma de onde parou (mantém last_event_id e a deduplicação)."""
        with _active_lock:
            other = _active.get(self.conversation_id)
            _active[self.conversation_id] = self
        if other is not None and other is not self:
            other.stop()
        with self._lock:
            if self._running:
                return
            self._running = True
            self._attempt = 0
            self._gen += 1
            my = self._gen
            self._wake = threading.Event()
        threading.Thread(target=self._run, args=(my,), name=f"bfocus-chat-{self.conversation_id[:8]}", daemon=True).start()

    def stop(self) -> None:
        """Fecha o stream/polling e cancela reconexões pendentes."""
        with _active_lock:
            if _active.get(self.conversation_id) is self:
                del _active[self.conversation_id]
        with self._lock:
            self._running = False
            self._gen += 1
            self._wake.set()
            self._close_conn()
        self._set_status("idle")

    # ── internos ──────────────────────────────────────────────────────────────
    def _alive(self, my: int) -> bool:
        return self._running and my == self._gen

    def _sleep(self, my: int, seconds: float) -> bool:
        wake = self._wake
        wake.wait(max(0.0, seconds))
        return self._alive(my)

    def _set_status(self, status: str) -> None:
        if status == self._status:
            return
        self._status = status
        if self._on_status:
            try:
                self._on_status(status)
            except Exception:  # noqa: BLE001
                log.exception("bfocus chat: on_status")

    def _close_conn(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            # shutdown destrava o recv() que a thread do stream está esperando.
            if conn.sock is not None:
                conn.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            conn.close()
        except OSError:
            pass

    def _fail(self, my: int, err: BaseException) -> None:
        with self._lock:
            if my != self._gen:
                return
            self._running = False
            self._gen += 1
            self._close_conn()
        self._set_status("failed")
        if self._on_fatal:
            try:
                self._on_fatal(err)
            except Exception:  # noqa: BLE001
                log.exception("bfocus chat: on_fatal")

    def _backoff(self, my: int) -> bool:
        self._set_status("reconnecting")
        ceiling = min(MAX_BACKOFF, 1.0 * 2 ** self._attempt)
        self._attempt += 1
        return self._sleep(my, ceiling / 2 + self._rand() * (ceiling / 2))

    def _deliver(self, ev: dict) -> None:
        ev_id = ev.get("id")
        if isinstance(ev_id, int):
            if ev_id in self._seen:
                return
            self._seen[ev_id] = None
            if len(self._seen) > SEEN_LIMIT:
                self._seen.popitem(last=False)
            if self.last_event_id is None or ev_id > self.last_event_id:
                self.last_event_id = ev_id
        try:
            self._on_event(ev)
        except Exception:  # noqa: BLE001
            log.exception("bfocus chat: on_event")

    def _connected(self) -> None:
        if self._on_connected:
            try:
                self._on_connected()
            except Exception:  # noqa: BLE001
                log.exception("bfocus chat: on_connected")

    def _run(self, my: int) -> None:
        while self._alive(my):
            if self._status != "reconnecting":
                self._set_status("connecting")
            try:
                ticket = self.api.chat_stream_ticket(self.conversation_id)
            except Exception as err:  # noqa: BLE001
                if not self._alive(my):
                    return
                if is_fatal(err):
                    self._fail(my, err)
                    return
                if not self._backoff(my):
                    return
                continue
            if not self._alive(my):
                return
            if self.last_event_id is None and isinstance(ticket.get("last_event_id"), int):
                self.last_event_id = ticket["last_event_id"]
            if ticket.get("mode") == "sse":
                opened = self._run_sse(my, str(ticket.get("ticket") or ""))
                if not self._alive(my):
                    return
                if opened:
                    self._attempt = 0  # estava funcionando: reconecta logo
                if not self._backoff(my):
                    return
            else:
                if self._run_poll(my, ticket.get("poll_interval_ms")) != "upgrade":
                    return
                self._attempt = 0

    def _run_sse(self, my: int, ticket: str) -> bool:
        url = self.api.chat_stream_url(ticket, self.last_event_id)
        parts = urlsplit(url)
        cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        conn = cls(parts.hostname or "", parts.port, timeout=self._open_timeout)
        with self._lock:
            if not self._alive(my):
                return False
            self._conn = conn
        opened = False
        try:
            path = parts.path + ("?" + parts.query if parts.query else "")
            conn.request("GET", path, headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"})
            resp = conn.getresponse()
            if resp.status != 200 or "text/event-stream" not in (resp.getheader("Content-Type") or ""):
                return False  # ticket recusado etc.: o EventSource trataria como erro → ticket novo
            if conn.sock is not None:
                conn.sock.settimeout(self._read_timeout)
            opened = True
            self._attempt = 0
            self._set_status("live")
            self._connected()
            parser = SseParser()
            while self._alive(my):
                raw = resp.readline()
                if not raw:
                    break  # fim normal (duração máxima do servidor)
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                ev = parser.feed_line(line)
                if ev is not None and self._alive(my):
                    parsed = self._parse(ev)
                    if parsed is not None:
                        self._deliver(parsed)
        except (OSError, http.client.HTTPException, ValueError):
            pass
        finally:
            with self._lock:
                if self._conn is conn:
                    self._close_conn()
        return opened

    @staticmethod
    def _parse(ev: SseEvent) -> Optional[dict]:
        """`data` pode ser o Event completo ({id, type, created_at, payload}) ou só o payload."""
        try:
            data = json.loads(ev.data)
        except ValueError:
            return None
        header_id = int(ev.id) if ev.id and ev.id.isdigit() else None
        if isinstance(data, dict) and "type" in data and "payload" in data:
            raw_id = data.get("id", header_id)
            try:
                ev_id = int(raw_id) if raw_id is not None else None
            except (TypeError, ValueError):
                ev_id = header_id
            return {**data, "id": ev_id}
        return {
            "id": header_id,
            "type": ev.event,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": data if isinstance(data, dict) else {},
        }

    def _run_poll(self, my: int, poll_ms: Any) -> str:
        try:
            interval = max(1.0, float(poll_ms or 5000) / 1000.0)
        except (TypeError, ValueError):
            interval = 5.0
        upgrade_at = self._clock() + self._upgrade_after
        first = True
        failures = 0
        self._set_status("polling")
        while self._alive(my):
            if self._clock() >= upgrade_at:
                return "upgrade"
            try:
                res = self.api.chat_events(self.conversation_id, self.last_event_id or 0)
            except Exception as err:  # noqa: BLE001
                if not self._alive(my):
                    return "stop"
                if is_fatal(err):
                    self._fail(my, err)
                    return "stop"
                failures += 1
                self._set_status("reconnecting")
                if not self._sleep(my, min(MAX_BACKOFF, interval * 2 ** min(failures, 3))):
                    return "stop"
                continue
            if not self._alive(my):
                return "stop"
            for ev in (res or {}).get("events") or []:
                if isinstance(ev, dict):
                    self._deliver(ev)
            last = (res or {}).get("last_event_id")
            if isinstance(last, int) and (self.last_event_id is None or last > self.last_event_id):
                self.last_event_id = last
            failures = 0
            self._attempt = 0
            self._set_status("polling")
            if first:
                first = False
                self._connected()
            if not self._sleep(my, interval):
                return "stop"
        return "stop"
