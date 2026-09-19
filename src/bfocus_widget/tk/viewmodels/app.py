"""Raiz do widget de chamados (o `App.tsx` do embed): config, sessão, identidade e rotas."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ...core.http import is_identity_error
from ...shared import tokens
from .base import Observable, Periodic
from .chat import ChatScreenVM, ChatSessionVM, CsatVM
from .context import Ctx
from .release_notes import SplashVM
from .tickets import NewTicketVM, TicketDetailVM, TicketsListVM

SESSION_REFRESH_MS = 30_000


def view_for(target: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Payload de `bfocus:navigate`/`open()` → tela."""
    if not target:
        return None
    v = target.get("view")
    if v == "ticket" and target.get("ticketId"):
        return {"name": "detail", "id": str(target["ticketId"])}
    if v in ("chat", "new", "list"):
        return {"name": v}
    return None


class AppVM(Observable):
    def __init__(
        self,
        ctx: Ctx,
        *,
        show_release_notes: bool = True,
        customer_name: Optional[str] = None,
        on_unread: Callable[[int], None] = lambda n: None,
        on_seen: Callable[[str], None] = lambda ts: None,
        on_branding: Callable[[str], None] = lambda c: None,
        on_error: Callable[[str, Optional[str]], None] = lambda code, detail: None,
        on_close: Callable[[], None] = lambda: None,
        on_download: Callable[[str, str], None] = lambda url, name: None,
    ) -> None:
        super().__init__()
        self.ctx = ctx
        ctx.note_error = self.note_error
        ctx.primary = lambda: self.primary
        self.show_release_notes = show_release_notes
        self.customer_name = customer_name
        self._on_unread = on_unread
        self._on_seen = on_seen
        self._on_branding = on_branding
        self._on_error = on_error
        self._on_close = on_close
        self._on_download = on_download
        self.state = "loading"  # loading | config_error | identity_error | ready
        self.config: Optional[Dict[str, Any]] = None
        self.config_error_detail = ""
        self.identity_code: Optional[str] = None
        self.session: Optional[Dict[str, Any]] = None
        self._last_seen_posted: Optional[str] = None
        self.view: Dict[str, Any] = {"name": "list"}
        self.visible = False
        self._auto_routed = False
        self.tickets = TicketsListVM(ctx, on_unread)
        self.splash = SplashVM(ctx)
        # O splash cobre a lista: quando as novidades chegam, o painel precisa redesenhar.
        self.splash.subscribe(self.changed)
        self.chat: Optional[ChatSessionVM] = None
        self.page: Any = None  # VM da tela atual (novo/detalhe/chat)
        self.csat: Optional[CsatVM] = None
        self._session_periodic = Periodic(ctx.runner, SESSION_REFRESH_MS, self._load_session)
        self._chat_unsub: Optional[Callable[[], None]] = None

    # ── ciclo ──
    def start(self, target: Optional[Dict[str, Any]] = None) -> None:
        self.view = view_for(target) or {"name": "list"}
        self.ctx.runner.submit(self.ctx.api.get_config, self._config_ok, self._config_err)
        self._session_periodic.start(immediate=True)

    def dispose(self) -> None:
        self._session_periodic.stop()
        self.tickets.stop()
        self.splash.stop()
        self._dispose_page()
        if self.chat is not None:
            self.chat.dispose()
        if self._chat_unsub:
            self._chat_unsub()

    def note_error(self, err: BaseException) -> None:
        # 401 de identidade em qualquer chamada: a tela inteira vira `error_identity`.
        if is_identity_error(err) and self.state != "identity_error":
            self.identity_code = getattr(err, "code", None) or "WIDGET_USER_HASH_INVALID"
            self.state = "identity_error"
            self._on_error(self.identity_code, None)
            self._stop_screens()
            self.changed()

    def _config_ok(self, config: Dict[str, Any]) -> None:
        self.config = config or {}
        if self.state == "loading":
            self.state = "ready"
        self._on_branding(self.primary)
        enabled = bool((self.config.get("chat") or {}).get("enabled")) and self.state != "identity_error"
        self.chat = ChatSessionVM(self.ctx, enabled, on_tickets_changed=self._tickets_changed)
        self._chat_unsub = self.chat.subscribe(self._on_chat_change)
        self.chat.start()
        if self.view["name"] == "chat" and not enabled:
            self.view = {"name": "list"}  # chat pedido com o chat desligado: volta à lista
        self._enter_view()
        self.changed()

    def _config_err(self, err: BaseException) -> None:
        self.config_error_detail = str(err)
        if self.state != "identity_error":
            self.state = "config_error"
        self._on_error("WIDGET_CONFIG_FAILED", self.config_error_detail)
        self.changed()

    def _load_session(self) -> None:
        def ok(sess: Dict[str, Any]) -> None:
            self.session = sess or {}
            latest = self.session.get("latest_event_at")
            if latest and latest != self._last_seen_posted:
                self._last_seen_posted = latest
                self._on_seen(latest)  # sessão lida com o widget aberto (bfocus:seen)

        self.ctx.runner.submit(self.ctx.api.session, ok, self.note_error)

    # ── leitura ──
    @property
    def ready(self) -> bool:
        return self.state == "ready" and self.config is not None

    @property
    def primary(self) -> str:
        tenant = (self.config or {}).get("tenant") or {}
        # `or`: o backend pode mandar string vazia.
        return tenant.get("primary_color") or tokens()["brandFallback"]

    @property
    def tenant_name(self) -> str:
        return ((self.config or {}).get("tenant") or {}).get("name") or ""

    @property
    def tenant_logo(self) -> Optional[str]:
        return ((self.config or {}).get("tenant") or {}).get("logo_url") or None

    @property
    def tenant_initial(self) -> str:
        return (self.tenant_name[:1] or "?").upper()

    @property
    def chat_enabled(self) -> bool:
        return self.chat is not None and self.chat.enabled and self.state != "identity_error"

    @property
    def chat_loading(self) -> bool:
        return self.chat_enabled and not self.chat.ready  # type: ignore[union-attr]

    @property
    def show_chat_card(self) -> bool:
        return self.chat_enabled and self.chat.home_mode != "hidden"  # type: ignore[union-attr]

    @property
    def splash_dismissed(self) -> bool:
        return self.splash.dismissed

    @property
    def splash_visible(self) -> bool:
        # "Pular" só esconde os cartões dispensáveis: a ciência exigida aparece mesmo assim.
        return self.show_release_notes and self.view["name"] == "list" and self.splash.visible

    def chat_card(self) -> Dict[str, Any]:
        """Cartão do chat na lista (§5.1)."""
        from .chat import is_bot_availability, status_label  # noqa: PLC0415

        t = self.ctx.t
        chat = self.chat
        mode = chat.home_mode if chat else "hidden"
        if mode == "active" and chat and chat.conversation:
            c = chat.conversation
            unread = chat.unread
            return {"mode": "active", "dot": "live", "title": t.t("chat_active_title"),
                    "desc": f"{c.get('ticket_number', '')} · {status_label(t, c)}",
                    "unread": ("99+" if unread > 99 else str(unread)) if unread > 0 else None,
                    "cta": t.t("btn_chat_resume"), "link": None}
        avail = chat.availability if chat else None
        queue = (avail or {}).get("queue_size") or 0
        if mode == "online":
            desc = t.t("chat_online_desc_bot") if is_bot_availability(avail) else t.t("chat_online_desc")
            return {"mode": mode, "dot": "live", "title": t.t("chat_online_title"), "desc": desc,
                    "cta": "💬 " + t.t("btn_chat_now"), "link": t.t("btn_open_ticket_instead"), "queue": None}
        if mode == "busy":
            return {"mode": mode, "dot": "busy", "title": t.t("chat_busy_title"), "desc": t.t("chat_busy_desc"),
                    "queue": t.t("chat_queue_size", n=queue) if queue > 0 else None,
                    "cta": "💬 " + t.t("btn_chat_queue"), "link": t.t("btn_open_ticket_instead")}
        if mode == "offline":
            return {"mode": mode, "dot": "off", "title": t.t("chat_offline_title"), "desc": t.t("chat_offline_desc"),
                    "cta": "✉ " + t.t("btn_leave_message"), "link": None, "queue": None}
        return {"mode": mode}

    # ── visibilidade e navegação ──
    def set_visible(self, visible: bool) -> None:
        if visible == self.visible:
            return
        self.visible = visible
        if visible:
            self.splash.dismissed = False  # o splash volta a cada reabertura
            self._session_periodic.start(immediate=True)
            self._enter_view()
        else:
            self._session_periodic.stop()
            self._stop_screens()
            if self.chat is not None:
                self.chat.set_options(visible=False)
        self.changed()

    def navigate(self, target: Optional[Dict[str, Any]]) -> None:
        view = view_for(target)
        if view is not None:
            self.set_view(view)

    def set_view(self, view: Dict[str, Any]) -> None:
        if view.get("name") == "chat" and self.config is not None and not self.chat_enabled:
            view = {"name": "list"}
        self._stop_screens()
        self._dispose_page()
        self.view = view
        self._enter_view()
        self.changed()

    def close(self) -> None:
        self._on_close()

    def dismiss_splash(self) -> None:
        """"Pular por agora". A consulta continua: uma release com ciência publicada depois
        trava o widget do mesmo jeito."""
        self.splash.skip()
        self.changed()

    def on_focus(self) -> None:
        """O app voltou ao foco: confere as novidades na hora (o `refetchOnWindowFocus` do web)."""
        if self.visible and self.ready and self.show_release_notes and self.view["name"] == "list":
            self.splash.refresh()

    def select_ticket(self, ticket_id: str) -> None:
        # O chamado da conversa em andamento abre direto no chat.
        chat = self.chat
        if chat and chat.has_open_conversation and str((chat.conversation or {}).get("ticket_id")) == ticket_id:
            self.set_view({"name": "chat"})
        else:
            self.set_view({"name": "detail", "id": ticket_id})

    def open_chat(self) -> None:
        if self.chat and self.chat.terminal:
            self.chat.reset()
        self.set_view({"name": "chat"})

    def back_from_chat(self) -> None:
        if self.chat and self.chat.terminal:
            self.chat.reset()
        self.set_view({"name": "list"})

    def continue_chat(self) -> None:
        conv = (self.chat.conversation if self.chat else None) or {}
        if self.chat:
            self.chat.reset()
        self.set_view({"name": "chat", "draft": {"continueTicketId": conv.get("ticket_id"), "ticketNumber": conv.get("ticket_number")}})

    def view_chat_ticket(self) -> None:
        conv = (self.chat.conversation if self.chat else None) or {}
        if self.chat and self.chat.terminal:
            self.chat.reset()
        self.set_view({"name": "detail", "id": str(conv.get("ticket_id"))})

    def chat_offline(self, html: str, attachments: List[Dict[str, Any]]) -> None:
        self.set_view({"name": "new", "prefill": {"description": html, "attachments": attachments,
                                                  "notice": self.ctx.t.t("chat_offline_fallback")}})

    def csat_for(self, ticket_id: str) -> CsatVM:
        if self.csat is None or self.csat.ticket_id != ticket_id:
            self.csat = CsatVM(self.ctx, ticket_id)
        return self.csat

    # ── internos ──
    def _tickets_changed(self) -> None:
        self.tickets.refresh()
        self._load_session()

    def _on_chat_change(self) -> None:
        chat = self.chat
        if chat is None:
            return
        if not self._auto_routed and chat.ready:
            self._auto_routed = True
            # Com uma conversa aberta, o widget já abre direto nela (uma vez).
            if chat.has_open_conversation and self.view["name"] == "list":
                self.set_view({"name": "chat"})
                return
        self.changed()

    def _dispose_page(self) -> None:
        page, self.page = self.page, None
        if page is None:
            return
        for name in ("stop", "dispose"):
            fn = getattr(page, name, None)
            if callable(fn):
                fn()

    def _stop_screens(self) -> None:
        self.tickets.stop()
        self.splash.stop()
        if self.page is not None and hasattr(self.page, "stop"):
            self.page.stop()

    def _enter_view(self) -> None:
        if not self.ready:
            return
        name = self.view["name"]
        if self.chat is not None:
            self.chat.set_options(visible=self.visible, viewing=name == "chat", on_home=name == "list")
        if not self.visible:
            return
        if name == "list":
            self.tickets.start()
            if self.show_release_notes:
                self.splash.start()  # mesmo depois de "Pular": a ciência ainda pode chegar
        elif name == "new":
            if not isinstance(self.page, NewTicketVM):
                self.page = NewTicketVM(self.ctx, self.config or {}, self.view.get("prefill"),
                                        on_created=lambda tid: (self._tickets_changed(), self.set_view({"name": "detail", "id": tid})))
        elif name == "detail":
            if not isinstance(self.page, TicketDetailVM) or self.page.ticket_id != self.view["id"]:
                self._dispose_page()
                self.page = TicketDetailVM(self.ctx, self.view["id"], on_list_changed=self.tickets.refresh,
                                           on_download=self._on_download)
            self.page.start()
        elif name == "chat" and self.chat is not None:
            if not isinstance(self.page, ChatScreenVM):
                self.page = ChatScreenVM(self.ctx, self.chat, self.view.get("draft"), on_offline=self.chat_offline)
