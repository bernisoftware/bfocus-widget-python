"""Chat ao vivo (§5): sessão (port do `useChatSession.ts`), tela da conversa, composer e CSAT."""
from __future__ import annotations

import itertools
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from ...client.chat_stream import ChatStream
from ...core.badge import parse_instant
from ...core.http import ApiError, NetworkError
from ...shared import tokens
from .base import Observable, Periodic
from .context import Ctx
from .formatting import escape_html, format_chat_time, initials, text_to_html
from .tickets import attachment_ref

TERMINAL = ("closed", "abandoned")
OPEN_TICKET_STATUSES = ("open", "in_triage", "in_progress", "waiting_requester")
AVAILABILITY_MS = 30_000
AVAILABILITY_STALE_S = 10.0
QUEUE_NET_MS = 30_000
GROUP_MS = 120_000       # mensagens do mesmo autor dentro de 2 min ficam juntas
MAX_FILES = 10
COMMENT_MAX = 2000

_seq = itertools.count(1)


def _ms(iso: Optional[str]) -> float:
    dt = parse_instant(iso)
    return dt.timestamp() * 1000 if dt is not None else 0.0


def is_terminal(conv: Optional[Dict[str, Any]]) -> bool:
    return bool(conv) and conv.get("status") in TERMINAL  # type: ignore[union-attr]


def _agent(obj: Optional[Dict[str, Any]], *keys: str) -> Optional[Dict[str, Any]]:
    for k in keys:
        a = (obj or {}).get(k)
        if isinstance(a, dict) and isinstance(a.get("name"), str):
            return a
    return None


def bot_agent_of(conv: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return _agent(conv, "agent", "ai_agent")


def availability_agent(avail: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return _agent(avail, "ai_agent", "agent")


def is_bot_availability(avail: Optional[Dict[str, Any]]) -> bool:
    if not avail:
        return False
    return avail.get("mode") in ("bot", "autonomous") or avail.get("autonomous") is True or availability_agent(avail) is not None


def closing_outcome(conv: Dict[str, Any]) -> str:
    """`csat` (resolvido: avaliação), `follow_up` (segue pelo chamado) ou `plain` (só o aviso)."""
    continues = conv.get("close_reason") in ("follow_up", "transferred")
    ticket_status = conv.get("ticket_status")
    if ticket_status:
        if conv.get("status") == "closed" and ticket_status in ("resolved", "closed"):
            return "csat"
        if continues or ticket_status in OPEN_TICKET_STATUSES:
            return "follow_up"
        return "plain"
    if continues:
        return "follow_up"
    return "csat" if conv.get("status") == "closed" and conv.get("first_response_at") else "plain"


def status_label(t: Any, conv: Dict[str, Any]) -> str:
    s = conv.get("status")
    if s == "queued":
        pos = conv.get("queue_position")
        return t.t("chat_status_queued_pos", n=pos) if pos else t.t("chat_status_queued")
    if s == "bot":
        bot = bot_agent_of(conv)
        return t.t("chat_status_bot_with", name=bot["name"]) if bot else t.t("chat_status_bot")
    if s == "active":
        name = (conv.get("assigned_to") or {}).get("name")
        return t.t("chat_status_active_with", name=name) if name else t.t("chat_status_active")
    if s == "waiting_customer":
        return t.t("chat_status_waiting_customer")
    return t.t("chat_status_closed")


def chat_status_color(status: Optional[str]) -> str:
    return tokens()["chatStatus"].get(status or "", "#64748b")


def author_key(m: Dict[str, Any]) -> str:
    if m.get("author_is_staff") or m.get("author_kind") != "human":
        return f"team:{m.get('author_kind')}:{m.get('author_name') or ''}"
    return "me"


def merge_messages(current: List[Dict[str, Any]], incoming: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """União por id, ordenada por created_at (estável). Mensagens nunca são apagadas."""
    by_id: Dict[str, Dict[str, Any]] = {}
    for m in current:
        by_id[m["id"]] = m
    for m in incoming:
        if m.get("id"):
            by_id[m["id"]] = m
    items = list(by_id.values())
    return [m for _, m in sorted(enumerate(items), key=lambda p: (_ms(p[1].get("created_at")), p[0]))]


def message_rows(messages: List[Dict[str, Any]], bot: Optional[Dict[str, Any]], t: Any, locale: str,
                 now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for i, m in enumerate(messages):
        if m.get("author_kind") == "system":
            rows.append({"kind": "system", "id": m.get("id"), "html": m.get("content") or ""})
            continue
        nxt = messages[i + 1] if i + 1 < len(messages) else None
        mine = not m.get("author_is_staff") and m.get("author_kind") == "human"
        is_bot = m.get("author_kind") == "bot"
        if mine:
            author = t.t("you")
        elif m.get("author_name"):
            author = m["author_name"]
        elif is_bot:
            author = (bot or {}).get("name") or t.t("chat_bot")
        else:
            author = t.t("support_team")
        grouped = (
            nxt is not None and nxt.get("author_kind") != "system" and author_key(nxt) == author_key(m)
            and _ms(nxt.get("created_at")) - _ms(m.get("created_at")) < GROUP_MS
        )
        if mine:
            avatar: Any = None
        elif grouped:
            avatar = ("space",)
        elif is_bot:
            avatar = ("bot", m.get("author_avatar_url") or (bot or {}).get("avatar_url"))
        else:
            avatar = ("agent", m.get("author_avatar_url"), initials(m.get("author_name")))
        rows.append({
            "kind": "msg", "id": m.get("id"), "mine": mine, "html": m.get("content") or "",
            "attachments": m.get("attachments") or [], "avatar": avatar,
            # Agrupada: rodapé (autor · hora) só na última do grupo.
            "meta": None if grouped else (author, format_chat_time(m.get("created_at"), locale, now)),
        })
    return rows


class ChatSessionVM(Observable):
    """Estado do chat no widget: retomada, conversa, mensagens (confirmadas + otimistas),
    tempo real e disponibilidade. Vive no app, então o stream segue enquanto o usuário
    navega pela lista."""

    def __init__(
        self,
        ctx: Ctx,
        enabled: bool,
        on_tickets_changed: Callable[[], None] = lambda: None,
        stream_factory: Optional[Callable[[str], Any]] = None,
    ) -> None:
        super().__init__()
        self.ctx = ctx
        self.enabled = enabled
        self._on_tickets_changed = on_tickets_changed
        self._stream_factory = stream_factory or self._default_stream
        self.visible = False
        self.viewing = False
        self.on_home = False
        self._active_applied = False
        self._reset_state(active_checked=False)
        self.availability: Optional[Dict[str, Any]] = None
        self.availability_error = False
        self._availability_at: Optional[float] = None
        self._avail = Periodic(ctx.runner, AVAILABILITY_MS, self.refresh_availability)
        self._queue_net = Periodic(ctx.runner, QUEUE_NET_MS, self._queue_tick)
        self._stream: Any = None
        self._stream_conv: Optional[str] = None

    def _reset_state(self, active_checked: bool) -> None:
        self.active_checked = active_checked
        self.conversation: Optional[Dict[str, Any]] = None
        self.loaded = False
        self.messages: List[Dict[str, Any]] = []
        self.outbox: List[Dict[str, Any]] = []
        self.conv_changed_at = 0.0
        self.unread = 0
        self.stream_status = "idle"

    # ── leitura ──
    @property
    def conv_id(self) -> Optional[str]:
        return (self.conversation or {}).get("id")

    @property
    def terminal(self) -> bool:
        return is_terminal(self.conversation)

    @property
    def has_open_conversation(self) -> bool:
        return self.conversation is not None and not self.terminal

    @property
    def ready(self) -> bool:
        return not self.enabled or self.active_checked

    @property
    def home_mode(self) -> str:
        if not self.enabled:
            return "hidden"
        if self.has_open_conversation:
            return "active"
        if self.availability:
            return self.availability.get("status", "offline") if self.availability.get("enabled") else "hidden"
        if self.availability_error:
            return "hidden"
        return "loading"

    # ── ciclo ──
    def start(self) -> None:
        if not self.enabled:
            return
        self.ctx.runner.submit(self.ctx.api.chat_active, self._active_ok, self._active_err)

    def set_options(self, visible: Optional[bool] = None, viewing: Optional[bool] = None, on_home: Optional[bool] = None) -> None:
        if visible is not None:
            self.visible = visible
        if viewing is not None:
            self.viewing = viewing
        if on_home is not None:
            self.on_home = on_home
        self._sync()

    def dispose(self) -> None:
        self._avail.stop()
        self._queue_net.stop()
        if self._stream is not None:
            self._stream.stop()
            self._stream = None

    def _default_stream(self, conv_id: str) -> ChatStream:
        r = self.ctx.runner

        def connected() -> None:
            if self.conv_id == conv_id:
                self.load_snapshot(conv_id)  # a cada (re)conexão, recarrega a conversa inteira

        return ChatStream(
            self.ctx.api, conv_id,
            on_event=lambda ev: r.call_soon(lambda: self._event(ev)),
            on_status=lambda s: r.call_soon(lambda: self._set_stream_status(s)),
            on_connected=lambda: r.call_soon(connected),
            on_fatal=lambda err: r.call_soon(lambda: self.ctx.note_error(err)),
        )

    def _active_ok(self, conv: Optional[Dict[str, Any]]) -> None:
        if self._active_applied:
            return
        self._active_applied = True
        if not conv or self.conversation:
            self.active_checked = True
        else:
            self._reset_state(active_checked=True)
            self.conversation = conv
            self.conv_changed_at = self.ctx.clock()
            self._conv_changed()
        self._sync()
        self.changed()

    def _active_err(self, err: BaseException) -> None:
        self.ctx.note_error(err)
        self._active_ok(None)

    def _conv_changed(self) -> None:
        cid = self.conv_id
        if self._stream_conv == cid:
            return
        if self._stream is not None:
            self._stream.stop()
            self._stream = None
        self._stream_conv = cid
        if cid:
            self._stream = self._stream_factory(cid)
            self.load_snapshot(cid)

    def _sync(self) -> None:
        want_avail = self.enabled and self.visible and self.on_home and not self.has_open_conversation
        if want_avail and not self._avail.active:
            stale = self._availability_at is None or self.ctx.clock() - self._availability_at > AVAILABILITY_STALE_S
            self._avail.start(immediate=stale)
        elif not want_avail and self._avail.active:
            self._avail.stop()
        if self._stream is not None:
            # Um stream por conversa, só com o widget visível.
            if self.visible and not self.terminal:
                self._stream.start()
            else:
                self._stream.stop()
        # Rede de segurança da posição na fila.
        want_q = bool(self.conv_id) and (self.conversation or {}).get("status") == "queued" and self.visible
        if want_q and not self._queue_net.active:
            self._queue_net.start()
        elif not want_q and self._queue_net.active:
            self._queue_net.stop()
        if self.viewing and self.visible and self.unread > 0:
            self.unread = 0
            self.changed()

    def _queue_tick(self) -> None:
        if self.conv_id:
            self.load_snapshot(self.conv_id)

    # ── dados ──
    def refresh_availability(self) -> None:
        def ok(avail: Dict[str, Any]) -> None:
            self.availability = avail
            self.availability_error = False
            self._availability_at = self.ctx.clock()
            self.changed()

        def err(e: BaseException) -> None:
            self.ctx.note_error(e)
            self.availability_error = True
            self.changed()

        self.ctx.runner.submit(self.ctx.api.chat_availability, ok, err)

    def load_snapshot(self, conv_id: str) -> None:
        requested = self.ctx.clock()
        self.ctx.runner.submit(
            lambda: self.ctx.api.chat_get(conv_id),
            lambda res: self._snapshot(conv_id, res, requested),
            self.ctx.note_error,
        )

    def _snapshot(self, conv_id: str, res: Dict[str, Any], requested_at: float) -> None:
        if self.conv_id != conv_id:
            return
        # Evento/POST mais novo que o pedido do snapshot vence (o snapshot pode estar velho).
        if self.conv_changed_at <= requested_at and res.get("conversation"):
            self.conversation = res["conversation"]
        self.messages = merge_messages(self.messages, res.get("messages") or [])
        self.loaded = True
        self._sync()
        self.changed()

    def _set_conversation(self, conv: Dict[str, Any]) -> None:
        if self.conversation and self.conversation.get("id") != conv.get("id"):
            return
        self.conversation = conv
        self.conv_changed_at = self.ctx.clock()
        self._sync()
        self.changed()

    def _set_stream_status(self, status: str) -> None:
        if status != self.stream_status:
            self.stream_status = status
            self.changed()

    def _event(self, ev: Dict[str, Any]) -> None:
        kind = ev.get("type")
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        if kind == "conversation.updated":
            conv = payload.get("conversation")
            if not isinstance(conv, dict) or not self.conversation or conv.get("id") != self.conv_id:
                return
            self.conversation = conv
            self.conv_changed_at = self.ctx.clock()
            self._sync()
            self.changed()
        elif kind == "message.created":
            msg = payload.get("message")
            if not isinstance(msg, dict) or not msg.get("id"):
                return
            known = any(m.get("id") == msg["id"] for m in self.messages)
            if not known and msg.get("author_kind") == "human" and not msg.get("author_is_staff"):
                # Minha mensagem chegou pelo stream antes da resposta do POST: ocupa o lugar da
                # otimista mais antiga em envio (o POST depois só confirma o id).
                for i, o in enumerate(self.outbox):
                    if o["state"] == "sending":
                        del self.outbox[i]
                        break
            from_team = bool(msg.get("author_is_staff")) or msg.get("author_kind") == "bot"
            self.messages = merge_messages(self.messages, [msg])
            if not known and from_team and not self.viewing:
                self.unread += 1
            self._sync()
            self.changed()

    # ── ações ──
    def _open(self, conv: Dict[str, Any], messages: List[Dict[str, Any]]) -> None:
        self._reset_state(active_checked=True)
        self._active_applied = True
        self.conversation = conv
        self.messages = merge_messages([], messages)
        self.loaded = True
        self.conv_changed_at = self.ctx.clock()
        self._conv_changed()
        self._sync()
        self.changed()

    def start_conversation(self, html: str, attachments: List[Dict[str, Any]], continue_ticket_id: Optional[str],
                           cb: Callable[[str], None]) -> None:
        def ok(res: Dict[str, Any]) -> None:
            self._open(res.get("conversation") or {}, res.get("messages") or [])
            self._on_tickets_changed()
            cb("ok")

        def err(e: BaseException) -> None:
            if isinstance(e, ApiError) and e.code == "CHAT_OFFLINE":
                self.refresh_availability()
                cb("offline")
                return
            self.ctx.note_error(e)
            cb("error")

        self.ctx.runner.submit(
            lambda: self.ctx.api.chat_create(html, attachments, origin_url=None, continue_ticket_id=continue_ticket_id or None),
            ok, err,
        )

    def send_message(self, html: str, attachments: List[Dict[str, Any]]) -> None:
        cid = self.conv_id
        if not cid:
            return
        item = {
            "temp_id": f"tmp-{int(time.time() * 1000)}-{next(_seq)}",
            "content": html,
            "attachments": list(attachments),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "state": "sending",
        }
        self.outbox.append(item)
        self.changed()
        self._post(cid, item)

    def _post(self, cid: str, item: Dict[str, Any]) -> None:
        temp = item["temp_id"]

        def ok(msg: Dict[str, Any]) -> None:
            self.outbox = [o for o in self.outbox if o["temp_id"] != temp]
            if isinstance(msg, dict) and msg.get("id"):
                self.messages = merge_messages(self.messages, [msg])
            self.changed()

        def err(e: BaseException) -> None:
            for o in self.outbox:
                if o["temp_id"] == temp:
                    o["state"] = "failed"
            self.changed()
            self.ctx.note_error(e)
            if isinstance(e, ApiError) and e.code == "CONVERSATION_CLOSED":
                self.load_snapshot(cid)

        self.ctx.runner.submit(lambda: self.ctx.api.chat_send(cid, item["content"], item["attachments"]), ok, err)

    def retry(self, temp_id: str) -> None:
        item = next((o for o in self.outbox if o["temp_id"] == temp_id), None)
        if item is None or not self.conv_id:
            return
        item["state"] = "sending"
        self.changed()
        self._post(self.conv_id, item)

    def discard(self, temp_id: str) -> None:
        self.outbox = [o for o in self.outbox if o["temp_id"] != temp_id]
        self.changed()

    def request_human(self, fallback_html: str, cb: Callable[[str], None]) -> None:
        """"Falar com um atendente". Servidor sem a rota (404 sem código): manda o texto."""
        cid = self.conv_id
        if not cid:
            cb("error")
            return

        def ok(conv: Optional[Dict[str, Any]]) -> None:
            if conv:
                self._set_conversation(conv)
            else:
                self.load_snapshot(cid)
            cb("ok")

        def err(e: BaseException) -> None:
            self.ctx.note_error(e)
            if isinstance(e, ApiError) and e.status == 404 and not e.code:
                self.send_message(fallback_html, [])
                cb("fallback")
                return
            if isinstance(e, ApiError) and e.code == "CONVERSATION_CLOSED":
                self.load_snapshot(cid)
            cb("error")

        self.ctx.runner.submit(lambda: self.ctx.api.chat_handoff(cid), ok, err)

    def close_conversation(self, cb: Callable[[bool], None]) -> None:
        cid = self.conv_id
        if not cid:
            cb(False)
            return

        def ok(conv: Dict[str, Any]) -> None:
            if isinstance(conv, dict):
                self._set_conversation(conv)
            self._on_tickets_changed()
            cb(True)

        def err(e: BaseException) -> None:
            self.ctx.note_error(e)
            cb(False)

        self.ctx.runner.submit(lambda: self.ctx.api.chat_close(cid), ok, err)

    def reset(self) -> None:
        self._reset_state(active_checked=True)
        self._active_applied = True
        self._conv_changed()
        self._availability_at = None  # invalida: a próxima vez na lista consulta de novo
        if self._avail.active:
            self.refresh_availability()
        self._sync()
        self.changed()


class ComposerVM(Observable):
    """Composer do chat: texto simples (cada linha vira <p>), até 10 anexos."""

    def __init__(self, ctx: Ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self.text = ""
        self.files: List[Dict[str, Any]] = []
        self.uploading = 0
        self.busy = False
        self.upload_error: Optional[str] = None
        self.cleared = 0  # a tela limpa o campo quando isto muda

    def set_text(self, text: str) -> None:
        self.text = text

    def can_send(self, disabled: bool = False) -> bool:
        return not disabled and not self.busy and self.uploading == 0 and (bool(self.text.strip()) or bool(self.files))

    def add_files(self, paths: List[str]) -> None:
        room = MAX_FILES - len(self.files) - self.uploading
        paths = [p for p in paths if p][: max(0, room)]
        if not paths:
            return
        self.upload_error = None
        self.uploading += len(paths)
        self.changed()
        for p in paths:
            def ok(res: Dict[str, Any]) -> None:
                self.files.append(attachment_ref(res))
                self.uploading -= 1
                self.changed()

            def err(e: BaseException) -> None:
                self.ctx.note_error(e)
                self.upload_error = self.ctx.t.t("error_generic")
                self.uploading -= 1
                self.changed()

            self.ctx.runner.submit(lambda p=p: self.ctx.api.upload(p), ok, err)

    def remove(self, index: int) -> None:
        if 0 <= index < len(self.files):
            del self.files[index]
            self.changed()

    def submit(self, on_send: Callable[[str, List[Dict[str, Any]], Callable[[bool], None]], None], disabled: bool = False) -> None:
        if not self.can_send(disabled):
            return
        html, files = text_to_html(self.text), list(self.files)
        self.busy = True
        self.changed()

        def done(ok: bool) -> None:
            self.busy = False
            if ok:
                self.text = ""
                self.files = []
                self.cleared += 1
            self.changed()

        on_send(html, files, done)


class ChatScreenVM(Observable):
    """Estado local da tela da conversa (nova conversa, encerrar, falar com atendente)."""

    def __init__(self, ctx: Ctx, session: ChatSessionVM, draft: Optional[Dict[str, Any]] = None,
                 on_offline: Callable[[str, List[Dict[str, Any]]], None] = lambda html, atts: None) -> None:
        super().__init__()
        self.ctx = ctx
        self.session = session
        self.draft = draft or {}
        self._on_offline = on_offline
        self.confirm_end = False
        self.ending = False
        self.first_pending: Optional[Dict[str, Any]] = None
        self.start_error = False
        self.handoff = "idle"
        self.composer = ComposerVM(ctx)
        self.composer.subscribe(self.changed)
        self._unsub = session.subscribe(self._on_session)
        self._on_session()

    def dispose(self) -> None:
        self._unsub()

    def _on_session(self) -> None:
        conv = self.session.conversation
        if conv and self.session.messages:
            # Visto aqui: a lista não acende o "não lido" por mensagens do chat.
            self.ctx.read.mark_read(str(conv.get("ticket_id")), self.session.messages[-1].get("created_at"))
        self.changed()

    # ── cabeçalho ──
    @property
    def conversation(self) -> Optional[Dict[str, Any]]:
        return self.session.conversation

    def title(self) -> str:
        return self.ctx.t.t("chat_continue_title" if self.draft.get("continueTicketId") else "chat_new_title")

    def intro(self) -> str:
        avail = self.session.availability
        if not is_bot_availability(avail):
            return self.ctx.t.t("chat_new_intro")
        name = (availability_agent(avail) or {}).get("name")
        return self.ctx.t.t("chat_new_intro_bot_named", name=name) if name else self.ctx.t.t("chat_new_intro_bot")

    @property
    def with_bot(self) -> bool:
        return (self.conversation or {}).get("status") == "bot" and not self.session.terminal

    def status_text(self) -> str:
        return status_label(self.ctx.t, self.conversation or {})

    def status_color(self) -> str:
        return chat_status_color((self.conversation or {}).get("status"))

    def avatar(self) -> Any:
        """('bot', url) com o assistente; ('agent', url, inicial) com atendente; senão None."""
        conv = self.conversation or {}
        if self.with_bot:
            return ("bot", (bot_agent_of(conv) or {}).get("avatar_url"))
        agent = conv.get("assigned_to")
        if agent:
            return ("agent", agent.get("avatar_url"), (agent.get("name") or "?")[:1].upper())
        return None

    def handoff_text(self) -> str:
        key = {"requested": "chat_handoff_requested", "error": "chat_handoff_error"}.get(self.handoff, "chat_bot_hint")
        return self.ctx.t.t(key)

    def connection_banner(self) -> Optional[str]:
        if self.session.terminal:
            return None
        if self.session.stream_status == "reconnecting":
            return self.ctx.t.t("chat_reconnecting")
        if self.session.stream_status == "failed":
            return self.ctx.t.t("chat_connection_failed")
        return None

    def outcome(self) -> Optional[str]:
        return closing_outcome(self.conversation or {}) if self.session.terminal else None

    def rows(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        return message_rows(self.session.messages, bot_agent_of(self.conversation), self.ctx.t, self.ctx.locale, now)

    # ── ações ──
    def send(self) -> None:
        disabled = self.conversation is None and self.first_pending is not None
        self.composer.submit(self._on_send, disabled=disabled)

    def _on_send(self, html: str, files: List[Dict[str, Any]], done: Callable[[bool], None]) -> None:
        if self.conversation is not None:
            self.session.send_message(html, files)
            done(True)
            return
        self.start_error = False
        self.first_pending = {"temp_id": "first", "content": html, "attachments": files, "state": "sending"}
        self.changed()

        def cb(result: str) -> None:
            self.first_pending = None
            if result == "offline":
                done(True)
                self._on_offline(html, files)  # 409 CHAT_OFFLINE: formulário com o texto digitado
            elif result == "error":
                self.start_error = True
                done(False)
            else:
                done(True)
            self.changed()

        self.session.start_conversation(html, files, self.draft.get("continueTicketId"), cb)

    def ask_human(self) -> None:
        if self.handoff == "sending":
            return
        self.handoff = "sending"
        self.changed()

        def cb(result: str) -> None:
            self.handoff = "error" if result == "error" else "requested"
            self.changed()

        self.session.request_human(f"<p>{escape_html(self.ctx.t.t('chat_handoff_text'))}</p>", cb)

    def request_end(self) -> None:
        self.confirm_end = True
        self.changed()

    def cancel_end(self) -> None:
        self.confirm_end = False
        self.changed()

    def end(self) -> None:
        if self.ending:
            return
        self.ending = True
        self.changed()

        def cb(ok: bool) -> None:
            self.ending = False
            if ok:
                self.confirm_end = False
            self.changed()

        self.session.close_conversation(cb)


class CsatVM(Observable):
    """Avaliação de 1 a 5 + comentário (até 2000). Nunca fica girando para sempre."""

    def __init__(self, ctx: Ctx, ticket_id: str) -> None:
        super().__init__()
        self.ctx = ctx
        self.ticket_id = ticket_id
        self.rating = 0
        self.comment = ""
        self.state = "idle"  # idle | sending | done | error | not_resolved
        self.error_msg = ""
        self._in_flight = False

    def set_rating(self, n: int) -> None:
        if self.state == "sending":
            return
        self.rating = max(1, min(5, int(n)))
        self.changed()

    def set_comment(self, text: str) -> None:
        self.comment = (text or "")[:COMMENT_MAX]

    def button_text(self) -> str:
        t = self.ctx.t
        if self.state == "sending":
            return t.t("csat_sending")
        return t.t("csat_retry") if self.state == "error" else t.t("csat_submit")

    def submit(self) -> None:
        if not self.rating or self._in_flight:
            return  # trava contra duplo clique
        self._in_flight = True
        self.state = "sending"
        self.error_msg = ""
        self.changed()

        def ok(_r: Any) -> None:
            self._in_flight = False
            self.state = "done"
            self.changed()

        def err(e: BaseException) -> None:
            self._in_flight = False
            if isinstance(e, ApiError) and e.status == 409 and e.code == "TICKET_NOT_RESOLVED":
                self.state = "not_resolved"
            else:
                if isinstance(e, NetworkError):
                    key = "csat_error_timeout" if e.kind == "timeout" else "csat_error_network"
                else:
                    self.ctx.note_error(e)
                    key = "csat_error_generic"
                self.error_msg = self.ctx.t.t(key)
                self.state = "error"
            self.changed()

        rating, comment = self.rating, self.comment
        self.ctx.runner.submit(lambda: self.ctx.api.submit_satisfaction(self.ticket_id, rating, comment), ok, err)
