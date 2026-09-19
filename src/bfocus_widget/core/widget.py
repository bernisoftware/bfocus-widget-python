"""`BFocusWidget`: o núcleo sem UI (BRIEF §3 e §4).

Consulta o launcher-state (thread própria), mantém o badge e a pílula, decide o banner,
fala com o host (Qt, Tk ou navegador) e chama os callbacks do integrador. Todo callback e
toda chamada ao host passam pelo `dispatcher` do host, ou seja, rodam na thread da interface.
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import logging
import threading
import webbrowser
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .badge import DOT, BadgeModel
from .config import BFocusConfig, Customer, User
from .host import EMBED_MESSAGE_TYPES, Dispatcher, WidgetHost, direct_dispatcher
from .http import IDENTITY_ERRORS, REQUEST_TIMEOUT, NetworkError, Transport, error_code_from_text, send, unwrap
from .notify import Notifier, notification_text, system_notifier
from .payload import embed_url, launcher_state_request, normalize_target, open_param
from .pill import ReleaseNotesState, pill_from
from .push import handle_push as _handle_push
from .push import push_register_request, push_unregister_request
from .storage import StateStore, scope_key

log = logging.getLogger("bfocus_widget")

_FIRST_CALL_WAIT = REQUEST_TIMEOUT + 5


class BFocusWidget:
    """Núcleo do widget. Uso típico:

        widget = BFocusWidget(BFocusConfig(...), on_badge_changed=...)
        host = BFocusQtHost(widget)   # ou BFocusTkHost(widget, root); sem host = modo navegador
        widget.start()                # = init: primeira consulta e polling
    """

    def __init__(
        self,
        config: BFocusConfig,
        *,
        on_badge_changed: Optional[Callable[[str], None]] = None,
        on_release_notes_changed: Optional[Callable[[ReleaseNotesState], None]] = None,
        on_error: Optional[Callable[[str, Optional[str]], None]] = None,
        on_open: Optional[Callable[[], None]] = None,
        on_close: Optional[Callable[[], None]] = None,
        host: Optional[WidgetHost] = None,
        dispatcher: Optional[Dispatcher] = None,
        notifier: Optional[Notifier] = None,
        transport: Optional[Transport] = None,
        browser_opener: Optional[Callable[[str], Any]] = None,
        store: Optional[StateStore] = None,
    ) -> None:
        self._cfg = config
        self.on_badge_changed = on_badge_changed
        self.on_release_notes_changed = on_release_notes_changed
        self.on_error = on_error
        self.on_open = on_open
        self.on_close = on_close
        self._host = host
        self._dispatch: Dispatcher = dispatcher or direct_dispatcher
        self._notifier: Notifier = notifier or system_notifier
        self._transport: Transport = transport or send
        self._browser_open = browser_opener or webbrowser.open
        self._store = store or StateStore(config.storage_dir)
        self._lock = threading.RLock()
        self._listeners: Dict[str, List[Callable[..., None]]] = {}
        self._badge = BadgeModel(last_seen=self._store.get_last_seen(scope_key(config)))
        self._pill: Optional[ReleaseNotesState] = None
        self._primary_color: Optional[str] = None
        self._last_state: Optional[dict] = None
        self._open = False
        self._running = False
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._force = False
        self._first_done = threading.Event()
        self._banner_ids: Optional[Tuple[str, ...]] = None
        self._reported: Set[str] = set()  # códigos já avisados (um aviso por código até um sucesso)
        self._push: Optional[Tuple[str, str]] = None

    # ── estado público ────────────────────────────────────────────────────────
    @property
    def config(self) -> BFocusConfig:
        """Configuração efetiva (com o userHash atual, que o provider pode trocar)."""
        return self._cfg

    @property
    def scope(self) -> str:
        return scope_key(self._cfg)

    @property
    def store(self) -> StateStore:
        return self._store

    @property
    def badge_label(self) -> str:
        return self._badge.label

    @property
    def last_seen(self) -> Optional[str]:
        return self._badge.last_seen

    @property
    def release_notes(self) -> ReleaseNotesState:
        return self._pill or ReleaseNotesState(color=self._primary_color)

    @property
    def primary_color(self) -> Optional[str]:
        return self._primary_color

    @property
    def last_state(self) -> Optional[dict]:
        """Último `data` do launcher-state (inclui `chat.active_conversation_id`)."""
        return self._last_state

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def started(self) -> bool:
        return self._running

    @property
    def host(self) -> Optional[WidgetHost]:
        return self._host

    @property
    def host_available(self) -> bool:
        return self._host is not None and bool(getattr(self._host, "available", True))

    def wait_first_call(self, timeout: float = _FIRST_CALL_WAIT) -> bool:
        """Bloqueia até a primeira consulta terminar (ela cria o usuário no bFocus; nada pode
        correr em paralelo). Sem `start()`, não espera."""
        if not self._running:
            return True
        return self._first_done.wait(timeout)

    # ── ligação com o host ────────────────────────────────────────────────────
    def attach_host(self, host: Optional[WidgetHost], dispatcher: Optional[Dispatcher] = None) -> None:
        self._host = host
        if dispatcher is not None:
            self._dispatch = dispatcher

    def set_dispatcher(self, dispatcher: Dispatcher) -> None:
        self._dispatch = dispatcher

    def subscribe(self, event: str, fn: Callable[..., None]) -> Callable[[], None]:
        """Ouvinte extra (os componentes de UI usam isto). Eventos: `badge`, `release_notes`,
        `branding`, `error`, `open`, `close`. Devolve a função que cancela."""
        self._listeners.setdefault(event, []).append(fn)

        def cancel() -> None:
            try:
                self._listeners.get(event, []).remove(fn)
            except ValueError:
                pass

        return cancel

    # ── ciclo de vida ─────────────────────────────────────────────────────────
    def start(self) -> "BFocusWidget":
        """`init`: faz a primeira consulta agora e depois a cada `poll_interval_seconds`."""
        with self._lock:
            if self._running:
                return self
            self._running = True
            self._stop = threading.Event()
            self._wake = threading.Event()
            self._first_done = threading.Event()
            stop, wake, first = self._stop, self._wake, self._first_done
        threading.Thread(target=self._loop, args=(stop, wake, first), name="bfocus-launcher-state", daemon=True).start()
        return self

    init = start

    def shutdown(self) -> None:
        """Para a consulta sem apagar nada (ex.: o app vai fechar)."""
        with self._lock:
            self._running = False
            self._stop.set()
            self._wake.set()

    def refresh(self) -> None:
        """Consulta o launcher-state agora."""
        with self._lock:
            self._force = True
        self._wake.set()

    # ── ações ─────────────────────────────────────────────────────────────────
    def open(self, target: object = None) -> None:
        """Abre o widget. `target`: 'list', 'new', 'chat', ('ticket', id) ou 'ticket:<id>'.
        Sem `target`, só mostra (a tela onde o usuário estava é preservada)."""
        nav = normalize_target(target) if target is not None else None
        if not self.host_available:
            self._browser_open(self.embed_url(mode="browser", open=open_param(nav)))
            return
        with self._lock:
            was_open = self._open
            self._open = True
            self._badge.open()
        host = self._host
        self._dispatch(lambda: host.show_tickets(nav))  # type: ignore[union-attr]
        if not was_open:
            self._emit("open")

    def close(self) -> None:
        """Fecha, mantendo a tela para reabrir rápido."""
        with self._lock:
            was_open = self._open
            self._open = False
            self._badge.close()
        if self.host_available:
            host = self._host
            self._dispatch(lambda: host.hide_tickets())  # type: ignore[union-attr]
        if was_open:
            self._emit("close")
            self._wake.set()  # fechar consulta na hora e volta ao intervalo

    def open_release_notes_history(self) -> None:
        if not self.host_available:
            # Modo navegador: com banner pendente, "abrir as novidades" mostra o banner.
            ids = list(self.release_notes.banner_ids)
            if ids:
                self._browser_open(self.embed_url(page="release-notes", mode="browser", view="banner", ids=ids))
            else:
                self._browser_open(self.embed_url(page="release-notes", mode="browser", view="history"))
            return
        host = self._host
        self._dispatch(lambda: host.show_history())  # type: ignore[union-attr]

    def logout(self) -> None:
        """Para a consulta, cancela o push, limpa o estado local e os dados do embed."""
        self.shutdown()
        cfg = self._cfg
        push, self._push = self._push, None
        if push:
            threading.Thread(target=self._send_quiet, args=(push_unregister_request(cfg, push[0]),), daemon=True).start()
        self._store.clear_scope(scope_key(cfg))
        with self._lock:
            old = self._badge.label
            self._badge = BadgeModel()
            self._pill = None
            self._banner_ids = None
            was_open, self._open = self._open, False
        if old:
            self._emit("badge", "")
        if was_open:
            self._emit("close")
        if self._host is not None:
            host = self._host
            self._dispatch(host.reset)

    def update_identity(
        self,
        user: object = None,
        customer: object = None,
        user_hash: Optional[str] = None,
    ) -> None:
        """Troca de usuário/cliente sem recriar o objeto: recria as telas e reinicia a consulta."""
        was_running = self._running
        self.shutdown()
        changes: Dict[str, Any] = {"user_hash": user_hash}
        if user is not None:
            changes["user"] = User.coerce(user)
        if customer is not None:
            changes["customer"] = Customer.coerce(customer)
        self._cfg = dataclasses.replace(self._cfg, **changes)
        with self._lock:
            old = self._badge.label
            self._badge = BadgeModel(last_seen=self._store.get_last_seen(scope_key(self._cfg)))
            self._pill = None
            self._banner_ids = None
        if old:
            self._emit("badge", "")
        if self._host is not None:
            host = self._host
            self._dispatch(host.reset)
        if was_running:
            self.start()
        if self._push:
            self.register_push_token(*self._push)

    def register_push_token(self, token: str, platform: str = "android") -> None:
        """Só faz sentido com FCM (mobile/híbrido). Espera a primeira consulta terminar."""
        self._push = (token, platform)
        req = push_register_request(self._cfg, token, platform)

        def run() -> None:
            self.wait_first_call()
            self._send_quiet(req)

        threading.Thread(target=run, name="bfocus-push", daemon=True).start()

    def handle_push(self, data: Any) -> bool:
        """`True` se a notificação é do bFocus; nesse caso abre o widget no item certo."""
        result = _handle_push(data)
        if result.handled and result.navigate is not None:
            self.open(result.navigate)
        return result.handled

    def embed_url(self, page: str = "embed", **kw: Any) -> str:
        """URL do embed com a identidade atual (Host Protocol v1 §1)."""
        return embed_url(self._cfg, page=page, **kw)

    # ── entrada do host ───────────────────────────────────────────────────────
    def dispatch_embed_message(self, raw: Any, surface: str = "tickets") -> Optional[dict]:
        """Mensagem do embed (string JSON ou dict). Trata o que é de estado e devolve a
        mensagem para o host cuidar do que é de tela (`ready`, `openExternal`, `download`).
        Tipo desconhecido ou malformado → `None` (ignorado)."""
        msg = raw
        if isinstance(msg, (bytes, bytearray)):
            msg = msg.decode("utf-8", errors="replace")
        if isinstance(msg, str):
            try:
                msg = json.loads(msg)
            except ValueError:
                return None
        if not isinstance(msg, dict) or msg.get("type") not in EMBED_MESSAGE_TYPES:
            return None
        payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
        kind = msg["type"]
        if kind == "bfocus:close":
            self.close()
        elif kind == "bfocus:unread":
            self.notify_unread(payload.get("count"))
        elif kind == "bfocus:seen":
            self.notify_seen(payload.get("latest_event_at"))
        elif kind in ("bfocus:branding", "bfocus:rn:branding"):
            self.notify_branding(payload.get("primary_color"))
        elif kind == "bfocus:error":
            self.notify_error(str(payload.get("code") or ""), payload.get("detail"))
        elif kind == "bfocus:rn:done":
            self.notify_banner_done()
        elif kind == "bfocus:rn:closeHistory":
            self.notify_history_closed()
        elif kind == "bfocus:ready":
            hp = payload.get("hp")
            if hp != 1:
                log.warning("bfocus: embed fala o Host Protocol %r; o pacote espera 1", hp)
        return msg

    def notify_unread(self, count: Any) -> None:
        """Não lidos do embed. Só vale com o widget aberto (Host Protocol §3)."""
        with self._lock:
            if not self._open:
                return
            old = self._badge.label
            self._badge.unread(count)
            new = self._badge.label
        self._label_changed(old, new)

    def notify_seen(self, latest_event_at: Optional[str]) -> None:
        if not latest_event_at:
            return
        with self._lock:
            self._badge.seen(latest_event_at)
        self._store.set_last_seen(self.scope, latest_event_at)

    def notify_branding(self, color: Optional[str]) -> None:
        if not color or color == self._primary_color:
            return
        self._primary_color = color
        self._emit("branding", color)
        if self._pill is not None:
            self._pill = dataclasses.replace(self._pill, color=color)
            self._emit("release_notes", self._pill)

    def notify_error(self, code: str, detail: Optional[str] = None) -> None:
        self._emit("error", code, detail)
        if code == "WIDGET_USER_HASH_INVALID" and self._cfg.user_hash_provider:
            # Hash novo do servidor do integrador e recarrega (fora da thread da UI).
            def run() -> None:
                new = self._call_provider()
                if new and new != self._cfg.user_hash:
                    self._set_user_hash(new)

            threading.Thread(target=run, name="bfocus-user-hash", daemon=True).start()

    def notify_banner_done(self) -> None:
        with self._lock:
            self._banner_ids = None
        if self._host is not None:
            host = self._host
            self._dispatch(host.hide_banner)
        self.refresh()

    def notify_history_closed(self) -> None:
        if self._host is not None:
            host = self._host
            self._dispatch(host.hide_history)

    # ── internos ──────────────────────────────────────────────────────────────
    def _emit(self, event: str, *args: Any) -> None:
        direct = {
            "badge": self.on_badge_changed,
            "release_notes": self.on_release_notes_changed,
            "error": self.on_error,
            "open": self.on_open,
            "close": self.on_close,
        }.get(event)
        fns = ([direct] if direct else []) + list(self._listeners.get(event, ()))
        if not fns:
            return

        def run() -> None:
            for fn in fns:
                try:
                    fn(*args)
                except Exception:  # noqa: BLE001 - callback do integrador não derruba o widget
                    log.exception("bfocus: callback %s falhou", event)

        self._dispatch(run)

    def _label_changed(self, old: str, new: str) -> None:
        if old == new:
            return
        self._emit("badge", new)
        if self._cfg.notifications and new == DOT and old != DOT:
            title, body = notification_text(self._cfg.locale or "pt_BR")
            try:
                self._notifier(title, body, lambda: self._dispatch(self.open))
            except Exception:  # noqa: BLE001
                log.debug("bfocus: notifier falhou", exc_info=True)

    def _send_quiet(self, req: Any) -> None:
        try:
            self._transport(req, REQUEST_TIMEOUT)
        except NetworkError:
            pass

    def _call_provider(self) -> Optional[str]:
        fn = self._cfg.user_hash_provider
        if fn is None:
            return None
        try:
            value = fn()
            if inspect.isawaitable(value):
                value = asyncio.run(_await(value))
        except Exception:  # noqa: BLE001
            log.warning("bfocus: user_hash_provider falhou", exc_info=True)
            return None
        return str(value) if value else None

    def _set_user_hash(self, new: str, reload_host: bool = True) -> None:
        self._cfg = dataclasses.replace(self._cfg, user_hash=new)
        if reload_host and self._host is not None:
            host = self._host
            self._dispatch(host.reload)

    def _loop(self, stop: threading.Event, wake: threading.Event, first_done: threading.Event) -> None:
        first = True
        try:
            if self._cfg.user_hash_provider:
                new = self._call_provider()  # "chamada no init"
                if new and new != self._cfg.user_hash:
                    self._set_user_hash(new, reload_host=False)
        except Exception:  # noqa: BLE001
            log.exception("bfocus: provider no init")
        while not stop.is_set():
            with self._lock:
                forced, self._force = self._force, False
                # Desktop: consulta enquanto o app estiver aberto, mesmo sem foco (BRIEF §4.1).
                should = first or forced or not self._open
            if should:
                try:
                    self._poll_once()
                except Exception:  # noqa: BLE001
                    log.exception("bfocus: launcher-state")
                finally:
                    if first:
                        first_done.set()
                first = False
            wake.wait(float(self._cfg.poll_interval_seconds))
            wake.clear()

    def _poll_once(self, retried: bool = False) -> None:
        cfg = self._cfg
        try:
            resp = self._transport(launcher_state_request(cfg), REQUEST_TIMEOUT)
        except NetworkError:
            return  # rede: silêncio, tenta no próximo ciclo
        if resp.status >= 500 or resp.status in (408, 429):
            return  # servidor/limite: silêncio, tenta no próximo ciclo
        if resp.status >= 400:
            code = error_code_from_text(resp.text) or f"HTTP_{resp.status}"
            if resp.status == 401 and code in IDENTITY_ERRORS and not retried and cfg.user_hash_provider:
                # Com provider: hash novo UMA vez e repete; onError só se a repetição falhar.
                new = self._call_provider()
                if new:
                    if new != cfg.user_hash:
                        self._set_user_hash(new)
                    self._poll_once(retried=True)
                    return
            self._report_error(code)
            return
        if not 200 <= resp.status < 300:
            return
        with self._lock:
            self._reported.clear()  # sucesso: um erro repetido volta a ser avisado
        try:
            data = unwrap(resp)
        except ValueError:
            return
        if isinstance(data, dict):
            self._apply_state(data)

    def _report_error(self, code: str) -> None:
        """Erro do launcher-state: avisado uma vez por código (não a cada ciclo)."""
        with self._lock:
            if code in self._reported:
                return
            self._reported.add(code)
        self._emit("error", code, None)

    def _apply_state(self, data: dict) -> None:
        self._last_state = data
        color = data.get("primary_color")
        if isinstance(color, str) and color and color != self._primary_color:
            self._primary_color = color
            self._emit("branding", color)
        tickets = data.get("tickets") if isinstance(data.get("tickets"), dict) else {}
        with self._lock:
            old_label, old_seen = self._badge.label, self._badge.last_seen
            self._badge.state(tickets.get("latest_event_at"))
            new_label, new_seen = self._badge.label, self._badge.last_seen
        if new_seen != old_seen:
            self._store.set_last_seen(self.scope, new_seen)
        self._label_changed(old_label, new_label)
        rn = data.get("release_notes") if isinstance(data.get("release_notes"), dict) else None
        pill = pill_from(rn, self._primary_color)
        if pill != self._pill:
            self._pill = pill
            self._emit("release_notes", pill)
        if pill.banner_ids and self._cfg.auto_show_release_banner:
            self._maybe_show_banner(pill.banner_ids)

    def _maybe_show_banner(self, ids: Tuple[str, ...]) -> None:
        with self._lock:
            if self._banner_ids is not None:
                return  # já aberto: não reabre nem empilha
            if not self.host_available:
                # Modo navegador: o banner não abre sozinho (exigiria modal). A pílula acende o
                # ponto e o banner aparece quando o usuário abrir as novidades.
                return
            self._banner_ids = ids
        host = self._host
        self._dispatch(lambda: host.show_banner(list(ids)))  # type: ignore[union-attr]


async def _await(value: Any) -> Any:
    return await value


def init(config: BFocusConfig, host: Optional[WidgetHost] = None, **kwargs: Any) -> BFocusWidget:
    """Atalho: cria o widget, liga o host (se houver) e começa a consulta."""
    widget = BFocusWidget(config, **kwargs)
    if host is not None:
        attach = getattr(host, "bind", None)
        if callable(attach):
            attach(widget)
        else:
            widget.attach_host(host)
    return widget.start()
