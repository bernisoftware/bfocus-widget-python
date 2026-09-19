"""Chamados: lista (§3.1), novo chamado (§3.2) e detalhe (§3.3) do widget-behavior-spec."""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional

from ...core.http import ApiError
from ...shared import tokens
from .base import Observable, Periodic
from .context import Ctx
from .formatting import format_datetime, format_day, is_html_empty
from .read_state import last_event_of

REFRESH_MS = 30_000
TITLE_MAX = 500
PRIORITIES = ("low", "medium", "high", "critical")
_MUTED = "#64748b"


def ticket_status_color(status: Optional[str]) -> str:
    return tokens()["ticketStatus"].get(status or "", _MUTED)


def priority_color(priority: str) -> str:
    return tokens()["priority"].get(priority, _MUTED)


def attachment_ref(res: Dict[str, Any]) -> Dict[str, Any]:
    """Retorno do /upload → referência citada no chamado/mensagem."""
    return {"url": res.get("url"), "filename": res.get("filename"), "size": res.get("size") or 0, "content_type": res.get("mime")}


def upload_error_text(ctx: Ctx, err: BaseException) -> str:
    if isinstance(err, ApiError) and err.status == 413:
        return ctx.t.native("attach_too_large")
    if isinstance(err, ApiError) and err.status == 415:
        return ctx.t.native("attach_type")
    return ctx.t.t("error_generic")


class TicketsListVM(Observable):
    """Até 50 chamados, mais recentes primeiro; atualiza a cada 30 s com a tela visível."""

    def __init__(self, ctx: Ctx, on_unread: Callable[[int], None] = lambda n: None) -> None:
        super().__init__()
        self.ctx = ctx
        self._on_unread = on_unread
        self.loading = True
        self.error = False
        self.tickets: Optional[List[Dict[str, Any]]] = None
        self._periodic = Periodic(ctx.runner, REFRESH_MS, self.refresh)

    def start(self) -> None:
        self._periodic.start(immediate=True)

    def stop(self) -> None:
        self._periodic.stop()

    def refresh(self) -> None:
        self.ctx.runner.submit(self.ctx.api.list_my_tickets, self._ok, self._err)

    def _ok(self, data: Any) -> None:
        self.tickets = list(data or [])
        self.loading = False
        self.error = False
        self._on_unread(self.unread_count())  # o badge do botão segue a lista (bfocus:unread)
        self.changed()

    def _err(self, err: BaseException) -> None:
        self.ctx.note_error(err)
        self.loading = False
        self.error = True
        self.changed()

    def is_unread(self, tk: Dict[str, Any]) -> bool:
        return self.ctx.read.is_unread(str(tk.get("id")), tk.get("last_event_at"), tk.get("created_at"))

    def unread_count(self) -> int:
        return sum(1 for tk in self.tickets or [] if self.is_unread(tk))

    @property
    def empty(self) -> bool:
        return self.tickets is not None and len(self.tickets) == 0

    def rows(self) -> List[Dict[str, Any]]:
        t = self.ctx.t
        out = []
        for tk in self.tickets or []:
            status = tk.get("status") or ""
            out.append({
                "id": str(tk.get("id")),
                "number": tk.get("ticket_number") or "",
                "title": tk.get("title") or "",
                "status": status,
                "status_label": t.t(f"status_{status}"),
                "status_color": ticket_status_color(status),
                "date": format_datetime(tk.get("last_event_at") or tk.get("updated_at"), self.ctx.locale),
                "unread": self.is_unread(tk),
            })
        return out


class NewTicketVM(Observable):
    def __init__(
        self,
        ctx: Ctx,
        config: Dict[str, Any],
        prefill: Optional[Dict[str, Any]] = None,
        on_created: Callable[[str], None] = lambda _id: None,
    ) -> None:
        super().__init__()
        self.ctx = ctx
        defaults = config.get("defaults") or {}
        prefill = prefill or {}
        self.types: List[str] = list(config.get("types") or [])
        self.departments: List[Dict[str, Any]] = list(config.get("departments") or [])
        self.type = defaults.get("type") or "support"
        self.priority = "medium"
        self.department_id = defaults.get("department_id") or ""
        self.title = ""
        self.description = prefill.get("description") or ""
        self.notice: Optional[str] = prefill.get("notice")
        self.uploads: List[Dict[str, Any]] = [
            {"url": a.get("url"), "filename": a.get("filename"), "size": a.get("size") or 0, "content_type": a.get("content_type")}
            for a in prefill.get("attachments") or []
        ]
        self.uploading = False
        self.upload_error: Optional[str] = None
        self.submitting = False
        self.error = False
        self._on_created = on_created

    @property
    def show_departments(self) -> bool:
        # Só quando há mais de um departamento para escolher.
        return len(self.departments) > 1

    @property
    def can_submit(self) -> bool:
        return bool(self.title.strip()) and not self.submitting

    def set_type(self, value: str) -> None:
        self.type = value
        self.changed()

    def set_priority(self, value: str) -> None:
        self.priority = value
        self.changed()

    def set_department(self, value: str) -> None:
        self.department_id = value or ""

    def set_title(self, value: str) -> None:
        self.title = (value or "")[:TITLE_MAX]
        self.changed()

    def set_description(self, html: str) -> None:
        self.description = html or ""

    def add_files(self, paths: Iterable[str]) -> None:
        paths = [p for p in paths if p]
        if not paths or self.uploading:
            return
        self.uploading = True
        self.upload_error = None
        self.changed()
        api = self.ctx.api

        def run():
            done: List[Dict[str, Any]] = []
            for p in paths:
                try:
                    done.append(attachment_ref(api.upload(p)))
                except Exception as err:  # noqa: BLE001 - para no primeiro erro, como o web
                    return done, err
            return done, None

        def ok(result):
            done, err = result
            self.uploads.extend(done)
            self.uploading = False
            if err is not None:
                self.ctx.note_error(err)
                self.upload_error = upload_error_text(self.ctx, err)
            self.changed()

        self.ctx.runner.submit(run, ok)

    def remove_upload(self, index: int) -> None:
        if 0 <= index < len(self.uploads):
            del self.uploads[index]
            self.changed()

    def upload_inline_image(self, path: str, on_url: Callable[[str], None]) -> None:
        """Imagem colada no editor: sobe e entra no texto."""
        self.ctx.runner.submit(lambda: self.ctx.api.upload(path), lambda res: on_url(res.get("url")), self.ctx.note_error)

    def submit(self) -> None:
        if not self.can_submit:
            return
        self.submitting = True
        self.error = False
        self.changed()
        api = self.ctx.api
        body = dict(
            type=self.type,
            title=self.title.strip(),
            priority=self.priority,
            description=None if is_html_empty(self.description) else self.description,
            department_id=self.department_id or None,
            attachments=list(self.uploads),
        )

        def err(e: BaseException) -> None:
            self.ctx.note_error(e)
            self.submitting = False
            self.error = True
            self.changed()

        def ok(ticket: Dict[str, Any]) -> None:
            self.submitting = False
            self.changed()
            self._on_created(str(ticket.get("id")))

        self.ctx.runner.submit(lambda: api.create_ticket(**body), ok, err)


def _is_live(conv: Dict[str, Any]) -> bool:
    return conv.get("status") not in ("closed", "abandoned")


class TicketDetailVM(Observable):
    def __init__(
        self,
        ctx: Ctx,
        ticket_id: str,
        on_list_changed: Callable[[], None] = lambda: None,
        on_download: Callable[[str, str], None] = lambda url, name: None,
    ) -> None:
        super().__init__()
        self.ctx = ctx
        self.ticket_id = ticket_id
        self._on_list_changed = on_list_changed
        self._on_download = on_download
        self.data: Optional[Dict[str, Any]] = None
        self.loading = True
        self.error = False
        self.conversations: Optional[List[Dict[str, Any]]] = None
        self.conv_error = False
        self.expanded: set = set()
        self.draft = ""
        self.draft_version = 0  # muda quando o editor tem de ser limpo
        self.pending: List[Dict[str, Any]] = []
        self.uploading = False
        self.sending = False
        self.send_error = False
        self._ticket_periodic = Periodic(ctx.runner, REFRESH_MS, self.refresh_ticket)
        self._conv_periodic = Periodic(ctx.runner, REFRESH_MS, self.refresh_conversations)

    # ── ciclo ──
    def start(self) -> None:
        self._ticket_periodic.start(immediate=True)
        if self.conversations is None and not self.conv_error:
            self.refresh_conversations()
        elif any(_is_live(c) for c in self.conversations or []):
            self._conv_periodic.start()

    def stop(self) -> None:
        self._ticket_periodic.stop()
        self._conv_periodic.stop()

    def refresh_ticket(self) -> None:
        self.ctx.runner.submit(lambda: self.ctx.api.get_ticket(self.ticket_id), self._ticket_ok, self._ticket_err)

    def _ticket_ok(self, data: Dict[str, Any]) -> None:
        self.data = data
        self.loading = False
        self.error = False
        self._mark_read()
        self.changed()

    def _ticket_err(self, err: BaseException) -> None:
        self.ctx.note_error(err)
        self.loading = False
        if self.data is None:
            self.error = True
        self.changed()

    def refresh_conversations(self) -> None:
        self.ctx.runner.submit(lambda: self.ctx.api.list_ticket_conversations(self.ticket_id), self._conv_ok, self._conv_err)

    def _conv_ok(self, convs: Optional[List[Dict[str, Any]]]) -> None:
        self.conversations = convs if convs is not None else []
        self.conv_error = False
        # Só recarrega sozinha enquanto houver conversa aberta.
        if any(_is_live(c) for c in self.conversations):
            self._conv_periodic.start()
        else:
            self._conv_periodic.stop()
        self._mark_read()
        self.changed()

    def _conv_err(self, err: BaseException) -> None:
        self.ctx.note_error(err)
        self.conv_error = True
        self.changed()

    def _mark_read(self) -> None:
        if self.data is None:
            return
        last = last_event_of(self.data, self.conversations or [])
        if last:
            self.ctx.read.mark_read(self.ticket_id, last)
        self._on_list_changed()  # o ponto de não lido apaga na lista

    # ── leitura ──
    @property
    def ticket(self) -> Dict[str, Any]:
        return (self.data or {}).get("ticket") or {}

    @property
    def status(self) -> str:
        return self.ticket.get("status") or ""

    @property
    def status_label(self) -> str:
        return self.ctx.t.t(f"status_{self.status}")

    @property
    def status_color(self) -> str:
        return ticket_status_color(self.status)

    @property
    def is_closed(self) -> bool:
        # Fechado/cancelado é final: sem resposta. Resolvido ainda aceita (responder reabre).
        return self.status in ("closed", "cancelled")

    @property
    def is_resolved(self) -> bool:
        return self.status == "resolved"

    @property
    def previous(self) -> Optional[Dict[str, Any]]:
        return self.ticket.get("previous_ticket") or None

    def continuation_text(self) -> str:
        prev = self.previous or {}
        return self.ctx.t.t("detail_continuation_of", n=prev.get("ticket_number", ""))

    def reopen_hint(self) -> str:
        at = self.ticket.get("auto_close_at")
        if at:
            return self.ctx.t.t("detail_reopen_hint", date=format_day(at, self.ctx.locale))
        return self.ctx.t.t("detail_reopen_hint_nodate")

    def bubbles(self) -> List[Dict[str, Any]]:
        t = self.ctx.t
        out: List[Dict[str, Any]] = []
        ticket = self.ticket
        atts = (self.data or {}).get("attachments") or []
        if ticket.get("description") or atts:
            # A descrição inicial é a primeira bolha "Você".
            out.append({"author": t.t("you"), "is_staff": False, "html": ticket.get("description") or "",
                        "time": format_datetime(ticket.get("created_at"), self.ctx.locale), "attachments": atts})
        for i in (self.data or {}).get("interactions") or []:
            staff = bool(i.get("author_is_staff"))
            out.append({
                "author": (i.get("author_name") or t.t("support_team")) if staff else t.t("you"),
                "is_staff": staff,
                "html": i.get("content") or "",
                "time": format_datetime(i.get("created_at"), self.ctx.locale),
                "attachments": i.get("attachments") or [],
            })
        return out

    @property
    def has_chat(self) -> bool:
        return bool(self.conversations)

    @property
    def show_interactions_title(self) -> bool:
        return self.has_chat and bool((self.data or {}).get("interactions"))

    @property
    def no_messages(self) -> bool:
        d = self.data or {}
        return not d.get("interactions") and not self.ticket.get("description") and not d.get("attachments") and not self.has_chat

    def chat_sections(self) -> List[Dict[str, Any]]:
        t = self.ctx.t
        out = []
        for c in self.conversations or []:
            msgs = sorted(c.get("messages") or [], key=lambda m: m.get("created_at") or "")
            count = sum(1 for m in msgs if m.get("author_kind") != "system")
            name = (c.get("assigned_to") or {}).get("name")
            title = format_datetime(c.get("created_at"), self.ctx.locale)
            if name:
                title += " · " + t.t("detail_chat_with", name=name)
            subtitle = t.t("detail_chat_count_one") if count == 1 else t.t("detail_chat_count", n=count)
            if _is_live(c):
                subtitle += " · " + t.t("detail_chat_ongoing")
            rows = []
            for m in msgs:
                kind = m.get("author_kind")
                if kind == "system":
                    rows.append({"kind": "system", "html": m.get("content") or ""})
                    continue
                mine = kind == "customer"
                author = t.t("you") if mine else (m.get("author_name") or (t.t("chat_bot") if kind == "bot" else t.t("support_team")))
                rows.append({
                    "kind": "msg", "author": author, "is_staff": not mine, "html": m.get("content") or "",
                    "time": format_datetime(m.get("created_at"), self.ctx.locale),
                    "attachments": [{"id": a.get("id"), "filename": a.get("filename"), "url": a.get("url") or "",
                                     "size": a.get("size"), "content_type": a.get("mime_type")} for a in m.get("attachments") or []],
                })
            out.append({"id": c.get("id"), "title": title, "subtitle": subtitle, "expanded": c.get("id") in self.expanded, "rows": rows})
        return out

    def toggle_conversation(self, conv_id: str) -> None:
        self.expanded.symmetric_difference_update({conv_id})
        self.changed()

    # ── resposta ──
    def set_draft(self, html: str) -> None:
        self.draft = html or ""

    @property
    def can_send(self) -> bool:
        return (not is_html_empty(self.draft) or bool(self.pending)) and not self.sending

    def attach(self, path: str) -> None:
        """Um anexo por vez (como o web)."""
        if not path or self.uploading:
            return
        self.uploading = True
        self.changed()

        def ok(res: Dict[str, Any]) -> None:
            self.pending.append(attachment_ref(res))
            self.uploading = False
            self.changed()

        def err(e: BaseException) -> None:
            self.ctx.note_error(e)
            self.uploading = False
            self.changed()

        self.ctx.runner.submit(lambda: self.ctx.api.upload(path), ok, err)

    def upload_inline_image(self, path: str, on_url: Callable[[str], None]) -> None:
        self.ctx.runner.submit(lambda: self.ctx.api.upload(path), lambda res: on_url(res.get("url")), self.ctx.note_error)

    def remove_pending(self, index: int) -> None:
        if 0 <= index < len(self.pending):
            del self.pending[index]
            self.changed()

    def send(self) -> None:
        if not self.can_send:
            return
        self.sending = True
        self.send_error = False
        self.changed()
        content, atts = self.draft, list(self.pending)

        def ok(_res: Any) -> None:
            self.sending = False
            self.draft = ""
            self.pending = []
            self.draft_version += 1
            self.changed()
            self.refresh_ticket()  # responder um resolvido o reabre: o status novo vem aqui
            self._on_list_changed()

        def err(e: BaseException) -> None:
            self.ctx.note_error(e)
            self.sending = False
            self.send_error = True
            self.changed()
            if isinstance(e, ApiError) and e.status == 409:
                # TICKET_CLOSED: foi fechado com a tela aberta → troca a caixa pelo aviso.
                self.refresh_ticket()

        self.ctx.runner.submit(lambda: self.ctx.api.add_interaction(self.ticket_id, content, atts), ok, err)

    def download(self, attachment: Dict[str, Any]) -> None:
        """Baixa com o NOME ORIGINAL (URL pré-assinada do endpoint de download)."""
        att_id = attachment.get("id")
        if not att_id:
            return
        self.ctx.runner.submit(
            lambda: self.ctx.api.download_attachment(str(att_id)),
            lambda res: self._on_download(res.get("url"), res.get("filename") or attachment.get("filename") or "download"),
            self.ctx.note_error,
        )
