"""View-models da UI Tk contra uma API simulada (fixtures), sem janela (spec §3 a §6)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bfocus_widget.core.http import ApiError, NetworkError
from bfocus_widget.core.storage import StateStore
from bfocus_widget.shared import NATIVE, Translator, strings, tokens
from bfocus_widget.tk.viewmodels import (
    AppVM,
    BannerVM,
    ChatScreenVM,
    ChatSessionVM,
    Ctx,
    CsatVM,
    HistoryVM,
    NewTicketVM,
    ReadState,
    SplashVM,
    SyncRunner,
    TicketDetailVM,
    TicketsListVM,
    closing_outcome,
    message_rows,
    status_label,
)
from bfocus_widget.tk.viewmodels.formatting import (
    format_chat_time,
    format_datetime,
    history_date,
    pretty_bytes,
    text_to_html,
    tint,
)
from bfocus_widget.tk.viewmodels.html_text import html_to_segments, segments_text

T0 = "2026-09-01T10:00:00+00:00"
T1 = "2026-09-01T10:05:00+00:00"


class FakeApi:
    """API simulada: guarda as chamadas; `fail[nome]` faz o método levantar a exceção."""

    def __init__(self):
        self.calls = []
        self.fail = {}
        self.hooks = {}  # nome → função chamada DURANTE a chamada (estado "enviando")
        self.config = {
            "tenant": {"name": "Acme", "primary_color": "#0EA5E9", "logo_url": None},
            "defaults": {"department_id": None, "type": None},
            "departments": [{"id": "d1", "name": "TI"}, {"id": "d2", "name": "Fin"}],
            "types": ["support", "bug", "request", "incident"],
            "priorities": ["low", "medium", "high", "critical"],
            "chat": {"enabled": True},
        }
        self.tickets = [
            {"id": "t1", "ticket_number": "T-1", "title": "Com resposta", "status": "in_progress",
             "created_at": T0, "updated_at": T1, "last_event_at": T1},
            {"id": "t2", "ticket_number": "T-2", "title": "Novo", "status": "open",
             "created_at": T0, "updated_at": T0, "last_event_at": T0},
        ]
        self.ticket = {
            "ticket": {"id": "t1", "ticket_number": "T-1", "title": "Com resposta", "status": "resolved",
                       "description": "<p>Descrição</p>", "created_at": T0, "updated_at": T1,
                       "auto_close_at": "2026-09-08T12:00:00+00:00",
                       "previous_ticket": {"id": "t0", "ticket_number": "T-0", "title": "Antigo"}},
            "interactions": [{"id": "i1", "content": "<p>Oi</p>", "author_name": "Ana", "author_is_staff": True, "created_at": T1}],
            "attachments": [{"id": "a1", "filename": "log.txt", "url": "u", "size": 10, "content_type": "text/plain"}],
        }
        self.conversations = []
        self.active = None
        self.availability = {"enabled": True, "status": "online", "queue_size": 0}
        self.pending = []
        self.changelog = {"products": [], "notes": []}
        self.release_notes = []
        self.session_data = {"latest_event_at": T1}

    def _c(self, name, *args):
        self.calls.append((name,) + args)
        if name in self.hooks:
            self.hooks[name]()
        if name in self.fail:
            raise self.fail[name]

    def names(self):
        return [c[0] for c in self.calls]

    def get_config(self):
        self._c("get_config")
        return self.config

    def session(self):
        self._c("session")
        return self.session_data

    def list_my_tickets(self):
        self._c("list_my_tickets")
        return self.tickets

    def create_ticket(self, **body):
        self._c("create_ticket", body)
        return {"id": "t9"}

    def get_ticket(self, tid):
        self._c("get_ticket", tid)
        return self.ticket

    def list_ticket_conversations(self, tid):
        self._c("list_ticket_conversations", tid)
        return self.conversations

    def add_interaction(self, tid, content, atts):
        self._c("add_interaction", tid, content, atts)
        return {"id": "i2"}

    def upload(self, path):
        self._c("upload", path)
        return {"url": f"https://cdn/{path}", "filename": path, "size": 3, "mime": "text/plain"}

    def download_attachment(self, aid):
        self._c("download_attachment", aid)
        return {"url": "https://s3/presigned", "filename": "log.txt"}

    def chat_active(self):
        self._c("chat_active")
        return self.active

    def chat_availability(self):
        self._c("chat_availability")
        return self.availability

    def chat_create(self, content, atts, origin_url=None, continue_ticket_id=None):
        self._c("chat_create", content, continue_ticket_id)
        return {"conversation": conv(), "messages": [msg("m1", "human", False, T0, content)]}

    def chat_get(self, cid):
        self._c("chat_get", cid)
        return {"conversation": conv(), "messages": []}

    def chat_send(self, cid, content, atts):
        self._c("chat_send", cid, content)
        return msg("m-sent", "human", False, T1, content)

    def chat_close(self, cid):
        self._c("chat_close", cid)
        return conv(status="closed", ticket_status="resolved")

    def chat_handoff(self, cid):
        self._c("chat_handoff", cid)
        return conv(status="queued", queue_position=2)

    def submit_satisfaction(self, tid, rating, comment):
        self._c("submit_satisfaction", tid, rating, comment)

    def release_notes_pending(self):
        self._c("release_notes_pending")
        return self.pending

    def acknowledge_release_note(self, nid):
        self._c("acknowledge", nid)
        self.release_notes = [n for n in self.release_notes if n["id"] != nid]

    def mark_release_note_seen(self, nid):
        self._c("seen", nid)
        self.release_notes = [n for n in self.release_notes if n["id"] != nid]

    def release_notes_changelog(self):
        self._c("changelog")
        return self.changelog

    def list_release_notes(self):
        self._c("list_release_notes")
        return self.release_notes

    def mark_release_note_read(self, nid):
        self._c("mark_read", nid)
        self.release_notes = [n for n in self.release_notes if n["id"] != nid]


def conv(status="active", **kw):
    c = {"id": "c1", "ticket_id": "t5", "ticket_number": "T-5", "status": status, "close_reason": None,
         "assigned_to": {"id": "u", "name": "Bruno", "avatar_url": None}, "first_response_at": T0, "queue_position": None}
    c.update(kw)
    return c


def msg(mid, kind, staff, at, content="<p>x</p>", name=None):
    return {"id": mid, "conversation_id": "c1", "content": content, "author_kind": kind, "author_name": name,
            "author_is_staff": staff, "created_at": at, "attachments": []}


class FakeStream:
    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


@pytest.fixture
def ctx(tmp_path):
    api = FakeApi()
    runner = SyncRunner()
    errors = []
    c = Ctx(api=api, runner=runner, t=Translator("pt_BR"), read=ReadState(StateStore(tmp_path), "k:u:c"),
            locale="pt_BR", note_error=errors.append)
    c.errors = errors  # type: ignore[attr-defined]
    return c


# ── lista ─────────────────────────────────────────────────────────────────────

def test_list_unread_rows_and_refresh(ctx):
    counts = []
    vm = TicketsListVM(ctx, counts.append)
    vm.start()
    rows = vm.rows()
    assert [r["unread"] for r in rows] == [True, False]  # nunca aberto: só depois de 1 s do created_at
    assert counts == [1]
    assert rows[0]["status_label"] == "Em andamento"
    assert rows[0]["status_color"] == tokens()["ticketStatus"]["in_progress"]
    assert rows[0]["date"] == format_datetime(T1, "pt_BR")
    ctx.read.mark_read("t1", T1)  # abrir → vira lido
    ctx.runner.advance(30_000)   # atualiza a cada 30 s
    assert ctx.api.names().count("list_my_tickets") == 2
    assert counts == [1, 0]
    vm.stop()
    ctx.runner.advance(60_000)
    assert ctx.api.names().count("list_my_tickets") == 2


def test_list_empty_and_error(ctx):
    ctx.api.tickets = []
    vm = TicketsListVM(ctx)
    vm.refresh()
    assert vm.empty
    ctx.api.fail["list_my_tickets"] = NetworkError("network")
    vm.refresh()
    assert vm.error


# ── novo chamado ──────────────────────────────────────────────────────────────

def test_new_ticket_flow(ctx):
    created = []
    vm = NewTicketVM(ctx, ctx.api.config, on_created=created.append)
    assert vm.type == "support" and vm.priority == "medium" and vm.show_departments
    assert not vm.can_submit
    vm.set_title("x" * 600)
    assert len(vm.title) == 500 and vm.can_submit
    vm.add_files(["a.png", "b.pdf"])
    assert [u["filename"] for u in vm.uploads] == ["a.png", "b.pdf"]
    vm.remove_upload(0)
    vm.set_description("<p></p>")
    vm.submit()
    body = [c for c in ctx.api.calls if c[0] == "create_ticket"][0][1]
    assert body["description"] is None and body["priority"] == "medium" and len(body["attachments"]) == 1
    assert created == ["t9"]


def test_new_ticket_prefill_and_upload_errors(ctx):
    vm = NewTicketVM(ctx, {"types": ["support"], "departments": [{"id": "d"}]},
                     prefill={"description": "<p>chat</p>", "attachments": [{"url": "u", "filename": "f"}], "notice": "aviso"})
    assert vm.notice == "aviso" and vm.description == "<p>chat</p>" and len(vm.uploads) == 1
    assert not vm.show_departments  # só com mais de um
    ctx.api.fail["upload"] = ApiError(413, "UPLOAD_TOO_LARGE")
    vm.add_files(["grande.zip"])
    assert vm.upload_error == NATIVE["pt_BR"]["attach_too_large"]
    ctx.api.fail["upload"] = ApiError(415, "UPLOAD_MIME_NOT_ALLOWED")
    vm.add_files(["x.exe"])
    assert vm.upload_error == NATIVE["pt_BR"]["attach_type"]


# ── detalhe ───────────────────────────────────────────────────────────────────

def test_detail_conversation_and_reply(ctx):
    downloads, list_changes = [], []
    vm = TicketDetailVM(ctx, "t1", on_list_changed=lambda: list_changes.append(1), on_download=lambda u, n: downloads.append((u, n)))
    vm.start()
    b = vm.bubbles()
    assert b[0]["author"] == "Você" and b[0]["html"] == "<p>Descrição</p>" and b[0]["attachments"]
    assert b[1]["author"] == "Ana" and b[1]["is_staff"]
    assert vm.is_resolved and not vm.is_closed
    assert vm.reopen_hint() == "Se o problema voltar, responda por aqui — o chamado é reaberto (até 08/09/2026)."
    assert vm.continuation_text() == "Continuação de T-0"
    assert ctx.read.last_read("t1") > 0 and list_changes  # abrir marca como lido
    vm.set_draft("<p>voltou</p>")
    vm.send()
    assert ("add_interaction", "t1", "<p>voltou</p>", []) in ctx.api.calls
    assert vm.draft == "" and vm.draft_version == 1
    vm.download({"id": "a1", "filename": "log.txt"})
    assert downloads == [("https://s3/presigned", "log.txt")]


def test_detail_closed_and_409(ctx):
    ctx.api.ticket["ticket"]["status"] = "closed"
    vm = TicketDetailVM(ctx, "t1")
    vm.start()
    assert vm.is_closed  # sem caixa de resposta; a tela mostra detail_closed_notice
    ctx.api.fail["add_interaction"] = ApiError(409, "TICKET_CLOSED")
    vm.set_draft("<p>x</p>")
    n = ctx.api.names().count("get_ticket")
    vm.send()
    assert vm.send_error and ctx.api.names().count("get_ticket") == n + 1


def test_detail_chat_transcript(ctx):
    ctx.api.conversations = [{"id": "cv", "status": "active", "created_at": T0, "assigned_to": {"name": "Bruno"},
                              "messages": [{"id": "1", "author_kind": "customer", "content": "a", "created_at": T0, "attachments": []},
                                           {"id": "2", "author_kind": "system", "content": "s", "created_at": T0, "attachments": []}]}]
    vm = TicketDetailVM(ctx, "t1")
    vm.start()
    sec = vm.chat_sections()[0]
    assert "com Bruno" in sec["title"] and sec["subtitle"] == "1 mensagem · em andamento"
    assert not sec["expanded"]
    vm.toggle_conversation("cv")
    assert vm.chat_sections()[0]["expanded"]
    ctx.runner.advance(30_000)  # conversa viva: recarrega a cada 30 s
    assert ctx.api.names().count("list_ticket_conversations") == 2
    assert vm.show_interactions_title


# ── chat ──────────────────────────────────────────────────────────────────────

def make_session(ctx):
    streams = {}

    def factory(cid):
        streams[cid] = FakeStream()
        return streams[cid]

    s = ChatSessionVM(ctx, True, stream_factory=factory)
    s.start()
    return s, streams


def test_chat_home_modes(ctx):
    s, _ = make_session(ctx)
    assert s.ready and s.home_mode == "loading"
    s.set_options(visible=True, on_home=True)
    assert s.home_mode == "online"
    ctx.api.availability = {"enabled": True, "status": "busy", "queue_size": 3}
    ctx.runner.advance(30_000)  # disponibilidade a cada 30 s
    assert s.home_mode == "busy"
    ctx.api.availability = {"enabled": False, "status": "offline", "queue_size": 0}
    s.refresh_availability()
    assert s.home_mode == "hidden"


def test_chat_resume_active_conversation(ctx):
    ctx.api.active = conv()
    s, streams = make_session(ctx)
    assert s.conversation["id"] == "c1" and s.home_mode == "active"
    s.set_options(visible=True)
    assert streams["c1"].started == 1  # stream só com o widget visível
    s.set_options(visible=False)
    assert streams["c1"].stopped >= 1


def test_chat_outbox_events_and_unread(ctx):
    s, streams = make_session(ctx)
    s.set_options(visible=True, viewing=True)
    results = []
    s.start_conversation("<p>oi</p>", [], None, results.append)
    assert results == ["ok"] and s.conversation["id"] == "c1" and streams["c1"].started >= 1
    # Envio otimista + confirmação pelo id.
    s.send_message("<p>2</p>", [])
    assert s.outbox == [] and any(m["id"] == "m-sent" for m in s.messages)
    # Falha → marcada; tentar de novo → confirma; descartar remove.
    ctx.api.fail["chat_send"] = NetworkError("network")
    s.send_message("<p>3</p>", [])
    assert s.outbox[0]["state"] == "failed"
    temp = s.outbox[0]["temp_id"]
    del ctx.api.fail["chat_send"]
    s.retry(temp)
    assert s.outbox == []
    ctx.api.fail["chat_send"] = NetworkError("network")
    s.send_message("<p>4</p>", [])
    s.discard(s.outbox[0]["temp_id"])
    assert s.outbox == []
    # Evento da equipe fora da tela da conversa conta como não lida; repetido não conta.
    s.set_options(viewing=False)
    ev = {"id": 10, "type": "message.created", "payload": {"message": msg("m9", "human", True, T1, name="Bruno")}}
    s._event(ev)
    s._event(ev)
    assert s.unread == 1
    s.set_options(viewing=True)
    assert s.unread == 0
    s._event({"id": 11, "type": "conversation.updated", "payload": {"conversation": conv(status="queued", queue_position=3)}})
    assert status_label(ctx.t, s.conversation) == "Na fila — posição 3"


def test_chat_stream_echo_replaces_optimistic(ctx):
    s, _ = make_session(ctx)
    s.start_conversation("<p>oi</p>", [], None, lambda r: None)
    s.outbox.append({"temp_id": "tmp-x", "content": "<p>eco</p>", "attachments": [], "state": "sending"})
    s._event({"id": 20, "type": "message.created", "payload": {"message": msg("m-eco", "human", False, T1)}})
    assert s.outbox == []


def test_chat_offline_and_close(ctx):
    s, streams = make_session(ctx)
    s.set_options(visible=True)
    ctx.api.fail["chat_create"] = ApiError(409, "CHAT_OFFLINE")
    offline = []
    screen = ChatScreenVM(ctx, s, on_offline=lambda html, atts: offline.append(html))
    screen.composer.set_text("linha 1\nlinha 2")
    screen.send()
    assert offline == ["<p>linha 1</p><p>linha 2</p>"]  # vai ao formulário com o texto digitado
    del ctx.api.fail["chat_create"]
    screen.composer.set_text("oi")
    screen.send()
    assert s.conversation is not None and screen.composer.text == ""
    screen.request_end()
    screen.end()
    assert s.terminal and screen.outcome() == "csat" and not screen.confirm_end
    assert streams["c1"].stopped >= 1  # encerrada: sem stream
    s.reset()
    assert s.conversation is None


def test_chat_handoff(ctx):
    s, _ = make_session(ctx)
    s.start_conversation("<p>oi</p>", [], None, lambda r: None)
    s._set_conversation(conv(status="bot", agent={"name": "Lia", "avatar_url": None}))
    screen = ChatScreenVM(ctx, s)
    assert screen.with_bot and screen.status_text() == "Com Lia, assistente virtual"
    screen.ask_human()
    assert screen.handoff == "requested" and s.conversation["status"] == "queued"
    ctx.api.fail["chat_handoff"] = ApiError(404, None)  # servidor sem a rota: manda o texto
    s._set_conversation(conv(status="bot"))
    screen.ask_human()
    assert any(c[0] == "chat_send" and "atendente" in c[2] for c in ctx.api.calls)


def test_closing_outcome_and_grouping(ctx):
    assert closing_outcome(conv(status="closed", ticket_status="resolved")) == "csat"
    assert closing_outcome(conv(status="closed", ticket_status="in_progress")) == "follow_up"
    assert closing_outcome(conv(status="closed", ticket_status="cancelled")) == "plain"
    assert closing_outcome(conv(status="closed", close_reason="transferred")) == "follow_up"
    assert closing_outcome(conv(status="abandoned", first_response_at=None)) == "plain"
    msgs = [msg("1", "human", True, "2026-09-01T10:00:00+00:00", name="Bruno"),
            msg("2", "human", True, "2026-09-01T10:01:00+00:00", name="Bruno"),
            msg("3", "human", True, "2026-09-01T10:04:00+00:00", name="Bruno"),
            msg("4", "bot", False, "2026-09-01T10:04:30+00:00")]
    rows = message_rows(msgs, {"name": "Lia", "avatar_url": None}, ctx.t, "pt_BR",
                        now=datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    assert rows[0]["meta"] is None and rows[0]["avatar"] == ("space",)  # agrupada (≤ 2 min)
    assert rows[1]["meta"][0] == "Bruno" and rows[1]["avatar"][0] == "agent"
    assert rows[2]["avatar"] == ("agent", None, "B")
    assert rows[3]["meta"][0] == "Lia" and rows[3]["avatar"][0] == "bot"


def test_csat(ctx):
    vm = CsatVM(ctx, "t5")
    vm.submit()
    assert "submit_satisfaction" not in ctx.api.names()  # sem nota não envia
    vm.set_rating(4)
    vm.set_comment("c" * 3000)
    assert len(vm.comment) == 2000
    ctx.api.fail["submit_satisfaction"] = NetworkError("timeout")
    vm.submit()
    assert vm.state == "error" and vm.error_msg == ctx.t.t("csat_error_timeout") and vm.button_text() == "Tentar de novo"
    ctx.api.fail["submit_satisfaction"] = ApiError(409, "TICKET_NOT_RESOLVED")
    vm.submit()
    assert vm.state == "not_resolved"
    del ctx.api.fail["submit_satisfaction"]
    vm.state = "idle"
    vm.submit()
    assert vm.state == "done"


# ── release notes ─────────────────────────────────────────────────────────────

def note(nid, ack, version="1.0.0", color="#22C55E"):
    return {"id": nid, "version": version, "title": f"T{nid}", "description": "<p>x</p>", "require_acknowledgement": ack,
            "published_at": "2026-09-14T12:00:00+00:00", "is_current": False, "product": {"name": "ERP", "color": color}}


def test_banner_gate_and_queue(ctx):
    ctx.api.pending = [note("a", True), note("b", False), note("c", True)]
    done = []
    vm = BannerVM(ctx, ["c", "a", "zzz"], lambda: done.append(1))
    vm.load()
    assert [n["id"] for n in vm.queue] == ["c", "a"]  # ordem da fila; id sumido é ignorado
    assert vm.counter() == "1 / 2" and vm.require_ack and vm.ack_disabled and vm.show_scroll_hint
    vm.confirm()
    assert "acknowledge" not in ctx.api.names()  # travado até rolar
    vm.content_metrics(top=100, visible=300, total=500)
    assert vm.ack_disabled
    vm.content_metrics(top=193, visible=300, total=500)  # a 7 px do fim
    assert not vm.ack_disabled
    ctx.api.fail["acknowledge"] = NetworkError("network")
    vm.confirm()
    assert vm.idx == 0  # falha de rede: a release fica
    del ctx.api.fail["acknowledge"]
    vm.confirm()
    assert vm.idx == 1 and vm.ack_disabled
    vm.content_metrics(top=0, visible=300, total=303)  # não rola: libera na hora
    vm.confirm()
    assert done == [1] and ("acknowledge", "a") in ctx.api.calls


def test_banner_submitting_and_error(ctx):
    ctx.api.pending = [note("a", True)]
    vm = BannerVM(ctx, ["a"], lambda: None)
    vm.load()
    vm.content_metrics(top=0, visible=300, total=300)
    during = []
    ctx.api.hooks["acknowledge"] = lambda: during.append((vm.button_text(), vm.error_text()))
    ctx.api.fail["acknowledge"] = NetworkError("network")
    vm.confirm()
    assert during == [("Registrando…", None)]  # "Registrando…" enquanto registra
    assert vm.error_text() == ctx.t.rn("ackError") and vm.button_text() == ctx.t.rn("ackButton") and vm.idx == 0
    del ctx.api.fail["acknowledge"]
    vm.confirm()
    assert during[-1] == ("Registrando…", None)  # tentar de novo apaga o aviso


def test_banner_dismiss_and_empty(ctx):
    ctx.api.pending = [note("b", False)]
    done = []
    vm = BannerVM(ctx, ["b"], lambda: done.append(1))
    vm.load()
    assert not vm.ack_disabled and vm.button_text() == "Entendi" and vm.eyebrow().endswith("Novidades pra você")
    vm.confirm()
    assert ("seen", "b") in ctx.api.calls and done == [1]
    ctx.api.pending = []
    empty = []
    BannerVM(ctx, ["x"], lambda: empty.append(1)).load()
    assert empty == [1]


def test_history(ctx):
    n1, n2, n3 = note("1", False, "4.10.0"), note("2", True, "4.9.1"), note("3", False, "4.2.0")
    n1["is_current"] = True
    ctx.api.changelog = {"products": [{"current_version": "4.10.0"}], "notes": [n3, n1, n2]}
    vm = HistoryVM(ctx)
    vm.load()
    rows = vm.notes()
    assert [r["version"] for r in rows] == ["v4.10.0", "v4.9.1", "v4.2.0"]
    assert rows[0]["is_current"] and rows[1]["require_ack"] and vm.current == "4.10.0"
    assert rows[0]["date"] == history_date("2026-09-14T12:00:00+00:00", "pt_BR")
    assert "seen" not in ctx.api.names() and "acknowledge" not in ctx.api.names()


def rn_at(nid, ack, published_at):
    n = note(nid, ack)
    n["published_at"] = published_at
    return n


def test_splash_cards_seen_and_skip(ctx):
    ctx.api.release_notes = [note("x", False), note("y", False)]
    vm = SplashVM(ctx)
    vm.start()
    assert vm.mode == "cards" and vm.count_text().startswith("2 novidades")
    vm.mark_read("x")
    assert ("seen", "x") in ctx.api.calls and "mark_read" not in ctx.api.names()  # por usuário, não o legado
    assert vm.count_text() == ctx.t.t("splash_count_one")
    vm.skip()
    assert vm.mode is None and not vm.visible  # "Pular por agora" some com os cartões


def test_splash_ack_gate(ctx):
    ctx.api.release_notes = [rn_at("new", True, "2026-09-10T12:00:00+00:00"), note("info", False),
                             rn_at("old", True, "2026-09-01T12:00:00+00:00")]
    vm = SplashVM(ctx)
    vm.start()
    # Ciência primeiro, uma por vez, mais antiga primeiro, com "1 / n".
    assert vm.mode == "gate" and vm.gate_note["id"] == "old" and vm.gate_counter() == "1 / 2"
    assert vm.gate_eyebrow() == "⚠ " + ctx.t.rn("ackEyebrow")
    vm.skip()
    assert vm.mode == "gate"  # sem pular
    assert vm.ack_disabled and vm.show_scroll_hint and vm.scroll_hint() == ctx.t.rn("scrollHint")
    vm.acknowledge()
    assert "acknowledge" not in ctx.api.names()  # travado até rolar
    vm.content_metrics(top=100, visible=300, total=500)
    assert vm.ack_disabled
    vm.content_metrics(top=193, visible=300, total=500)  # a 7 px do fim
    assert not vm.ack_disabled and not vm.show_scroll_hint and vm.ack_button_text() == ctx.t.rn("ackButton")
    during = []
    ctx.api.hooks["acknowledge"] = lambda: during.append((vm.ack_button_text(), vm.ack_disabled, vm.ack_error_text()))
    ctx.api.fail["acknowledge"] = NetworkError("network")
    vm.acknowledge()
    assert during == [("Registrando…", True, None)]
    assert vm.gate_note["id"] == "old" and vm.ack_error_text() == ctx.t.rn("ackError")  # a release fica, com o aviso
    del ctx.api.fail["acknowledge"]
    vm.acknowledge()
    assert during[-1] == ("Registrando…", True, None)  # tentar de novo apaga o aviso
    assert ("acknowledge", "old") in ctx.api.calls
    assert vm.gate_note["id"] == "new" and vm.gate_counter() is None
    assert vm.ack_disabled and vm.ack_error_text() is None  # a trava recomeça na próxima
    vm.content_metrics(top=0, visible=300, total=303)  # não rola: libera na hora
    vm.acknowledge()
    assert vm.gate_note is None and vm.cards and vm.mode is None  # o "Pular" continua valendo pros cartões
    vm.dismissed = False  # reabriu o widget
    assert vm.mode == "cards"


def test_splash_ack_after_skip_relocks_every_60s(ctx):
    ctx.api.release_notes = [note("info", False)]
    vm = SplashVM(ctx)
    vm.start()
    vm.skip()
    assert vm.mode is None
    ctx.api.release_notes = [note("info", False), note("urgent", True)]  # publicada depois do "Pular"
    ctx.runner.advance(59_000)
    assert ctx.api.names().count("list_release_notes") == 1 and vm.mode is None
    ctx.runner.advance(1_000)  # reconsulta a cada 60 s
    assert ctx.api.names().count("list_release_notes") == 2 and vm.mode == "gate"


# ── app ───────────────────────────────────────────────────────────────────────

def make_app(ctx, **kw):
    ev = {"unread": [], "seen": [], "branding": [], "error": [], "close": 0}
    app = AppVM(ctx, on_unread=ev["unread"].append, on_seen=ev["seen"].append, on_branding=ev["branding"].append,
                on_error=lambda code, detail: ev["error"].append(code), **kw)
    return app, ev


def test_app_ready_branding_seen_and_routes(ctx):
    app, ev = make_app(ctx)
    app.set_visible(True)
    app.start()
    assert app.state == "ready" and ev["branding"] == ["#0EA5E9"] and ev["seen"] == [T1]
    assert ev["unread"] == [1] and app.show_chat_card
    card = app.chat_card()
    assert card["mode"] == "online" and card["cta"].endswith("Conversar agora")
    app.navigate({"view": "ticket", "ticketId": "t1"})
    assert app.view == {"name": "detail", "id": "t1"} and isinstance(app.page, TicketDetailVM)
    app.set_view({"name": "new"})
    assert isinstance(app.page, NewTicketVM)
    app.page.set_title("x")
    app.page.submit()
    assert app.view == {"name": "detail", "id": "t9"}  # depois de criar, abre o detalhe


def test_app_auto_routes_to_active_chat(ctx):
    ctx.api.active = conv()
    app, _ = make_app(ctx)
    app.set_visible(True)
    app.start()
    assert app.view["name"] == "chat" and isinstance(app.page, ChatScreenVM)
    app.back_from_chat()
    app.select_ticket("t5")  # o chamado da conversa aberta vai direto ao chat
    assert app.view["name"] == "chat"


def test_app_chat_disabled_falls_back_to_list(ctx):
    ctx.api.config["chat"] = {"enabled": False}
    app, _ = make_app(ctx)
    app.set_visible(True)
    app.start({"view": "chat"})
    assert app.view == {"name": "list"} and not app.show_chat_card


def test_app_identity_error(ctx):
    ctx.api.fail["session"] = ApiError(401, "WIDGET_VERIFIED_SESSION_REQUIRED")
    app, ev = make_app(ctx)
    app.set_visible(True)
    app.start()
    assert app.state == "identity_error" and ev["error"] == ["WIDGET_VERIFIED_SESSION_REQUIRED"]


def test_app_config_error(ctx):
    ctx.api.fail["get_config"] = ApiError(403, "WIDGET_ORIGIN_NOT_ALLOWED")
    app, ev = make_app(ctx)
    app.start()
    assert app.state == "config_error" and ev["error"] == ["WIDGET_CONFIG_FAILED"]
    assert "WIDGET_ORIGIN_NOT_ALLOWED" in app.config_error_detail


def test_app_splash_returns_on_reopen(ctx):
    ctx.api.release_notes = [note("x", False)]
    app, _ = make_app(ctx)
    app.set_visible(True)
    app.start()
    assert app.splash_visible
    notified = []
    app.subscribe(lambda: notified.append(1))
    app.splash.refresh()
    assert notified  # novidades chegando redesenham o painel (o splash cobre a lista)
    app.dismiss_splash()
    assert not app.splash_visible
    app.set_visible(False)
    app.set_visible(True)
    assert app.splash_visible  # "pular por agora" some só até reabrir


def test_app_splash_gate_after_skip_and_focus(ctx):
    ctx.api.release_notes = [note("info", False)]
    app, _ = make_app(ctx)
    app.set_visible(True)
    app.start()
    app.dismiss_splash()
    assert not app.splash_visible and app.splash_dismissed
    count = lambda: ctx.api.names().count("list_release_notes")  # noqa: E731
    n = count()
    ctx.runner.advance(60_000)
    assert count() == n + 1  # "Pular" não para a consulta
    ctx.api.release_notes.append(note("urgent", True))
    app.on_focus()  # voltou ao foco: confere na hora
    assert count() == n + 2 and app.splash_visible and app.splash.mode == "gate"
    app.set_view({"name": "new"})
    assert not app.splash_visible  # como o web: só por cima da lista
    app.set_visible(False)
    app.on_focus()
    assert count() == n + 2  # fechado não consulta


# ── formatação, HTML e idiomas ────────────────────────────────────────────────

def test_formatting():
    utc = timezone.utc
    assert format_datetime("2026-09-14T18:03:00+00:00", "pt_BR", utc) == "14/09/2026, 18:03"
    assert format_datetime("2026-09-14T18:03:00+00:00", "en", utc) == "9/14/26, 6:03 PM"
    assert format_datetime("2026-09-14T18:03:00+00:00", "es", utc) == "14/9/26, 18:03"
    assert history_date("2026-09-14T18:03:00+00:00", "en", utc) == "Sep 14, 2026"
    assert history_date("2026-09-14T18:03:00+00:00", "pt_BR", utc) == "14 de set. de 2026"
    assert format_chat_time("2026-09-14T18:03:00+00:00", "pt_BR", now=datetime(2026, 9, 14, 20, tzinfo=utc), tz=utc) == "18:03"
    assert pretty_bytes(1536) == "1.5 KB"
    assert text_to_html("\n a <b>\n\nfim\n") == "<p> a &lt;b&gt;</p><p></p><p>fim</p>"
    assert tint("#6366F1", 0.1) == "#eff0fe"


def test_html_segments():
    segs = html_to_segments('<p>Olá <strong>forte</strong> <a href="https://x.y">link</a></p><p></p>'
                            '<ul><li>um</li><li>dois</li></ul><ol><li>a</li></ol><blockquote>q</blockquote><p><img src="https://i/p.png"></p>')
    text = segments_text(segs)
    assert text == "Olá forte link\n\n• um\n• dois\n1. a\nq\n[imagem]"
    assert any(s.text == "forte" and "bold" in s.tags for s in segs)
    assert any(s.text == "link" and s.href == "https://x.y" for s in segs)
    assert any(s.text == "q" and "quote" in s.tags for s in segs)
    assert segments_text(html_to_segments("linha 1\nlinha 2")) == "linha 1\nlinha 2"  # legado sem tags
    assert html_to_segments('<a href="javascript:alert(1)">x</a>')[0].href is None


def test_translations_have_no_portuguese_gaps():
    """Cenário 14: em en/es nenhum texto cai para o português."""
    pt = strings("pt_BR")
    for loc in ("en", "es"):
        d = strings(loc)
        for sec in ("tickets", "releaseNotes"):
            assert set(d[sec]) == set(pt[sec])
        assert set(NATIVE[loc]) == set(NATIVE["pt_BR"])
        assert Translator(loc).t("you") != "Você"
    assert Translator("en").t("header_title") == "Support" and Translator("es").rn("ackButton") != strings("pt_BR")["releaseNotes"]["ackButton"]
    assert Translator("en").t("chave_inexistente") == "chave_inexistente"
