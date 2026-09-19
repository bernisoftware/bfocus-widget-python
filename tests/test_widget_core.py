"""Comportamento do núcleo (BRIEF §4) com um transporte falso: sem rede, sem UI."""
from __future__ import annotations

import base64
import json
import threading
import time

import pytest
from conftest import SCENARIOS, config_for

from bfocus_widget import __version__
from bfocus_widget.core.badge import parse_instant
from bfocus_widget.core.config import BFocusConfig, normalize_locale
from bfocus_widget.core.host import WidgetHost
from bfocus_widget.core.http import HttpResponse, NetworkError
from bfocus_widget.core.payload import normalize_target, open_param
from bfocus_widget.core.storage import StateStore
from bfocus_widget.core.widget import BFocusWidget

FIX = SCENARIOS["launcherStateFixtures"]


def ok(data):
    return HttpResponse(200, {}, json.dumps({"code": "OK", "data": data, "message": ""}).encode())


def err(status, code):
    return HttpResponse(status, {}, json.dumps({"code": "ERROR", "data": None, "message": code}).encode())


class FakeTransport:
    """Responde launcher-state com a fila `states` (repete o último) e registra tudo."""

    def __init__(self, *states):
        self.states = list(states) or [ok(FIX["default"])]
        self.requests = []
        self.gate = None  # threading.Event: segura a PRIMEIRA chamada até ser liberada
        self.lock = threading.Lock()

    def __call__(self, req, timeout):
        with self.lock:
            self.requests.append(req)
            first = len(self.requests) == 1
        if "launcher-state" in req.url:
            if first and self.gate is not None:
                self.gate.wait(5)
            with self.lock:
                item = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            if isinstance(item, BaseException):
                raise item
            return item
        return ok({"ok": True})

    def launcher_calls(self):
        return [r for r in self.requests if "launcher-state" in r.url]


class RecHost(WidgetHost):
    def __init__(self):
        self.calls = []

    def _rec(self, *a):
        self.calls.append(a)

    def show_tickets(self, target):
        self._rec("show_tickets", target)

    def hide_tickets(self):
        self._rec("hide_tickets")

    def show_banner(self, ids):
        self._rec("show_banner", tuple(ids))

    def hide_banner(self):
        self._rec("hide_banner")

    def show_history(self):
        self._rec("show_history")

    def hide_history(self):
        self._rec("hide_history")

    def reset(self):
        self._rec("reset")

    def reload(self):
        self._rec("reload")


def wait_until(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def make(tmp_path, transport=None, host=None, **cfg):
    cfg.setdefault("poll_interval_seconds", 60)
    cfg.setdefault("client", f"python/{__version__}")
    events = []
    widget = BFocusWidget(
        config_for("min", **cfg),
        host=host,
        transport=transport or FakeTransport(),
        store=StateStore(tmp_path),
        on_badge_changed=lambda label: events.append(("badge", label)),
        on_release_notes_changed=lambda st: events.append(("rn", st)),
        on_error=lambda code, detail: events.append(("error", code)),
        on_open=lambda: events.append(("open",)),
        on_close=lambda: events.append(("close",)),
        notifier=lambda title, body, click: events.append(("notify", title, body, click)),
        browser_opener=lambda url: events.append(("browser", url)),
    )
    return widget, events


def test_first_call_creates_baseline_and_pill(tmp_path):
    t = FakeTransport(ok(FIX["default"]))
    widget, events = make(tmp_path, t)
    widget.start()
    assert widget.wait_first_call(3)
    widget.shutdown()
    assert widget.badge_label == ""
    assert widget.last_seen == FIX["default"]["tickets"]["latest_event_at"]
    assert StateStore(tmp_path).get_last_seen(widget.scope) == widget.last_seen
    assert widget.release_notes.label == "v4.2.0"
    assert widget.primary_color == "#0EA5E9"
    assert ("rn", widget.release_notes) in events


def test_no_parallel_call_before_first(tmp_path):
    """O registro de push espera a primeira consulta terminar (ela cria o usuário)."""
    t = FakeTransport(ok(FIX["default"]))
    t.gate = threading.Event()
    widget, _ = make(tmp_path, t)
    widget.start()
    widget.register_push_token("tok", "android")
    time.sleep(0.2)
    assert len(t.requests) == 1 and "launcher-state" in t.requests[0].url
    t.gate.set()
    assert wait_until(lambda: len(t.requests) == 2)
    assert t.requests[1].url.endswith("/api/v1/widget/push/devices")
    widget.shutdown()


def test_polls_only_while_closed(tmp_path):
    t = FakeTransport(ok(FIX["default"]))
    widget, events = make(tmp_path, t, host=RecHost(), poll_interval_seconds=0.05)
    widget.start()
    assert wait_until(lambda: len(t.launcher_calls()) >= 3)
    widget.open()
    time.sleep(0.1)
    n = len(t.launcher_calls())
    time.sleep(0.3)
    assert len(t.launcher_calls()) <= n + 1  # no máximo a que já estava em voo
    n = len(t.launcher_calls())
    widget.close()  # fechar consulta na hora
    assert wait_until(lambda: len(t.launcher_calls()) > n)
    widget.shutdown()
    assert ("open",) in events and ("close",) in events


def test_refresh_forces_a_call_even_when_open(tmp_path):
    t = FakeTransport(ok(FIX["default"]))
    widget, _ = make(tmp_path, t, host=RecHost())
    widget.start()
    widget.wait_first_call(3)
    widget.open()
    widget.refresh()
    assert wait_until(lambda: len(t.launcher_calls()) == 2)
    widget.shutdown()


def test_network_errors_and_5xx_are_silent(tmp_path):
    t = FakeTransport(NetworkError("timeout"), HttpResponse(503, {}, b"down"), ok(FIX["default"]))
    widget, events = make(tmp_path, t, poll_interval_seconds=0.02)
    widget.start()
    assert wait_until(lambda: widget.release_notes.label == "v4.2.0")
    widget.shutdown()
    assert not [e for e in events if e[0] == "error"]


def test_identity_retry_with_new_hash_succeeds_silently(tmp_path):
    calls = []

    def provider():
        calls.append(1)
        return "hash-novo-" + str(len(calls))

    t = FakeTransport(ok(FIX["default"]))
    host = RecHost()
    widget, events = make(tmp_path, t, host=host, user_hash_provider=provider)
    widget.start()
    widget.wait_first_call(3)
    assert len(calls) == 1  # chamado no init
    assert widget.config.user_hash == "hash-novo-1"
    first_user = json.loads(base64.b64decode(t.launcher_calls()[0].headers["X-bFocus-Widget-User"]))
    assert first_user["userHash"] == "hash-novo-1"
    # O servidor recusa: hash novo UMA vez e repete; a repetição passa → sem onError.
    t.states = [err(401, "WIDGET_USER_HASH_INVALID"), ok(FIX["default"])]
    widget.refresh()
    assert wait_until(lambda: len(t.launcher_calls()) == 3)
    time.sleep(0.05)
    widget.shutdown()
    assert not [e for e in events if e[0] == "error"]
    assert len(calls) == 2
    retry_user = json.loads(base64.b64decode(t.launcher_calls()[2].headers["X-bFocus-Widget-User"]))
    assert retry_user["userHash"] == "hash-novo-2"
    assert ("reload",) in host.calls  # identidade mudou: o host recarrega


def test_identity_error_after_refused_retry_once_per_code(tmp_path):
    t = FakeTransport(err(401, "WIDGET_USER_HASH_INVALID"))
    widget, events = make(tmp_path, t, user_hash_provider=lambda: "h")
    errors = lambda: [e for e in events if e[0] == "error"]  # noqa: E731
    widget.start()
    widget.wait_first_call(3)
    assert len(t.launcher_calls()) == 2 and errors() == [("error", "WIDGET_USER_HASH_INVALID")]
    widget.refresh()  # mesmo código no ciclo seguinte: não avisa de novo
    assert wait_until(lambda: len(t.launcher_calls()) == 4)
    time.sleep(0.05)
    assert len(errors()) == 1
    t.states = [ok(FIX["default"])]
    widget.refresh()
    assert wait_until(lambda: len(t.launcher_calls()) == 5)
    t.states = [err(401, "WIDGET_USER_HASH_INVALID")]
    widget.refresh()  # depois de um sucesso, volta a avisar
    assert wait_until(lambda: len(t.launcher_calls()) == 7)
    time.sleep(0.05)
    widget.shutdown()
    assert len(errors()) == 2


def test_other_4xx_reach_on_error_once_per_code(tmp_path):
    t = FakeTransport(err(403, "WIDGET_ORIGIN_NOT_ALLOWED"))
    widget, events = make(tmp_path, t)
    widget.start()
    widget.wait_first_call(3)
    widget.refresh()
    assert wait_until(lambda: len(t.launcher_calls()) == 2)
    t.states = [HttpResponse(404, {}, b"")]
    widget.refresh()
    assert wait_until(lambda: len(t.launcher_calls()) == 3)
    t.states = [HttpResponse(429, {}, b"")]
    widget.refresh()
    assert wait_until(lambda: len(t.launcher_calls()) == 4)
    time.sleep(0.05)
    widget.shutdown()
    assert [e[1] for e in events if e[0] == "error"] == ["WIDGET_ORIGIN_NOT_ALLOWED", "HTTP_404"]


def test_verified_session_required_without_provider(tmp_path):
    t = FakeTransport(err(401, "WIDGET_VERIFIED_SESSION_REQUIRED"))
    widget, events = make(tmp_path, t)
    widget.start()
    widget.wait_first_call(3)
    widget.shutdown()
    assert events == [("error", "WIDGET_VERIFIED_SESSION_REQUIRED")]
    assert len(t.launcher_calls()) == 1


def test_banner_opens_once_and_done_refreshes(tmp_path):
    t = FakeTransport(ok(FIX["banner"]))
    host = RecHost()
    widget, _ = make(tmp_path, t, host=host)
    widget.start()
    widget.wait_first_call(3)
    ids = tuple(FIX["banner"]["release_notes"]["banner_ids"])
    assert host.calls.count(("show_banner", ids)) == 1
    widget.refresh()
    assert wait_until(lambda: len(t.launcher_calls()) == 2)
    time.sleep(0.05)
    assert host.calls.count(("show_banner", ids)) == 1  # não reabre o mesmo conjunto aberto
    t.states = [ok(FIX["default"])]
    widget.dispatch_embed_message({"type": "bfocus:rn:done"}, surface="banner")
    assert ("hide_banner",) in host.calls
    assert wait_until(lambda: len(t.launcher_calls()) == 3)  # consulta de novo
    widget.shutdown()


def test_banner_disabled(tmp_path):
    host = RecHost()
    widget, _ = make(tmp_path, FakeTransport(ok(FIX["banner"])), host=host, auto_show_release_banner=False)
    widget.start()
    widget.wait_first_call(3)
    widget.shutdown()
    assert not [c for c in host.calls if c[0] == "show_banner"]
    assert widget.release_notes.banner_ids  # a pílula ainda informa


def test_browser_mode(tmp_path):
    widget, events = make(tmp_path, FakeTransport(ok(FIX["banner"])))
    browser = lambda: [e[1] for e in events if e[0] == "browser"]  # noqa: E731
    widget.open(("ticket", "t-9"))
    widget.open_release_notes_history()
    urls = browser()
    assert urls[0].startswith(f"https://widget.bfocus.com.br/v1/embed.html#host=browser&hp=1&client=python%2F{__version__}")
    assert urls[0].endswith("&open=ticket%3At-9")
    assert "release-notes.html#host=browser" in urls[1] and urls[1].endswith("&view=history")
    widget.start()
    widget.wait_first_call(3)
    widget.refresh()
    time.sleep(0.2)
    widget.shutdown()
    assert len(browser()) == 2  # o banner NÃO abre sozinho no modo navegador
    assert widget.release_notes.dot is True  # a pílula acende o ponto
    widget.open_release_notes_history()  # abrir as novidades mostra o banner pendente
    assert "view=banner&ids=11111111-1111-1111-1111-111111111111%2C22222222" in browser()[-1]


def test_desktop_notification_on_dot(tmp_path):
    t = FakeTransport(ok(FIX["default"]))
    host = RecHost()
    widget, events = make(tmp_path, t, host=host, notifications=True, locale="en")
    StateStore(tmp_path).set_last_seen(widget.scope, "2026-09-01T10:00:00+00:00")
    widget._badge.last_seen = "2026-09-01T10:00:00+00:00"
    widget.start()
    widget.wait_first_call(3)
    widget.shutdown()
    notes = [e for e in events if e[0] == "notify"]
    assert len(notes) == 1
    assert notes[0][1:3] == ("Support", "There are updates on your tickets.")
    notes[0][3]()  # clicar abre o widget (sem alvo: mostra onde estava)
    assert ("show_tickets", None) in host.calls


def test_embed_messages(tmp_path):
    host = RecHost()
    widget, events = make(tmp_path, host=host)
    assert widget.dispatch_embed_message("not json") is None
    assert widget.dispatch_embed_message({"type": "bfocus:novidade"}) is None
    assert widget.dispatch_embed_message('{"type":"other"}') is None
    widget.dispatch_embed_message({"type": "bfocus:unread", "payload": {"count": 5}})
    assert widget.badge_label == ""  # fechado: unread não vale
    widget.open()
    widget.dispatch_embed_message({"type": "bfocus:unread", "payload": {"count": 5}})
    assert widget.badge_label == "5"
    widget.dispatch_embed_message({"type": "bfocus:branding", "payload": {"primary_color": "#123456"}})
    assert widget.primary_color == "#123456"
    widget.dispatch_embed_message({"type": "bfocus:error", "payload": {"code": "WIDGET_CONFIG_FAILED", "detail": "x"}})
    assert ("error", "WIDGET_CONFIG_FAILED") in events
    widget.dispatch_embed_message({"type": "bfocus:rn:closeHistory"})
    assert ("hide_history",) in host.calls
    widget.dispatch_embed_message({"type": "bfocus:close"})
    assert not widget.is_open and ("hide_tickets",) in host.calls
    assert widget.dispatch_embed_message({"type": "bfocus:ready", "payload": {"hp": 1, "widget": "tickets"}})["type"] == "bfocus:ready"


def test_logout_clears_everything(tmp_path):
    host = RecHost()
    widget, events = make(tmp_path, FakeTransport(ok(FIX["default"])), host=host)
    widget.start()
    widget.wait_first_call(3)
    widget.open()
    widget.dispatch_embed_message({"type": "bfocus:unread", "payload": {"count": 2}})
    widget.store.set_read(widget.scope, "t1", 123)
    widget.logout()
    assert widget.badge_label == "" and not widget.started
    assert StateStore(tmp_path).get_last_seen(widget.scope) is None
    assert StateStore(tmp_path).get_read(widget.scope, "t1") == 0
    assert ("reset",) in host.calls


def test_last_seen_survives_restart(tmp_path):
    widget, _ = make(tmp_path, FakeTransport(ok(FIX["default"])))
    widget.start()
    widget.wait_first_call(3)
    widget.shutdown()
    again, _ = make(tmp_path, FakeTransport(ok(FIX["default"])))
    assert again.last_seen == FIX["default"]["tickets"]["latest_event_at"]


def test_release_notes_client_routes():
    """Splash por usuário: a lista leva produto e público; lida = /seen, ciência = /acknowledge."""
    from bfocus_widget.client.api import WidgetApi

    tr = FakeTransport()
    WidgetApi(config_for("full", client=f"python/{__version__}"), transport=tr).list_release_notes()
    api = WidgetApi(config_for("min", client=f"python/{__version__}"), transport=tr)
    api.list_release_notes()
    api.mark_release_note_seen("n/1")
    api.acknowledge_release_note("n1")
    urls = [(r.method, r.url.split("/api/v1/widget", 1)[1]) for r in tr.requests]
    assert urls == [
        ("GET", "/release-notes?product=erp&audience=both"),
        ("GET", "/release-notes"),
        ("POST", "/release-notes/n%2F1/seen"),
        ("POST", "/release-notes/n1/acknowledge"),
    ]


def test_helpers():
    assert parse_instant("2026-09-01T10:00:00Z") == parse_instant("2026-09-01T07:00:00.000-03:00")
    assert parse_instant("2026-09-01T10:00:00.1234567+00:00") is not None
    assert parse_instant("lixo") is None
    assert normalize_locale("pt-PT") == "pt_BR" and normalize_locale("es_MX") == "es" and normalize_locale("fr") == "en"
    assert normalize_target(None) == {"view": "list"}
    assert normalize_target("ticket:abc") == {"view": "ticket", "ticketId": "abc"}
    assert open_param({"view": "ticket", "ticketId": "x"}) == "ticket:x"
    assert open_param({"view": "list"}) is None
    with pytest.raises(ValueError):
        normalize_target("history")


@pytest.mark.parametrize("bad", [
    {"publishableKey": "bf_whs_segredo"},
    {"publishableKey": "bf_sk_live"},
    {"userHash": "bf_live_x"},
    {"apiBaseUrl": "http://api.example.com"},
    {"embedBaseUrl": "ftp://x"},
    {"appId": ""},
    {"audience": "todo-mundo"},
])
def test_config_rejects(bad):
    data = dict(SCENARIOS["configs"]["min"])
    data.update(bad)
    with pytest.raises(ValueError):
        BFocusConfig.from_dict(data)


def test_config_accepts_localhost_http():
    cfg = config_for("min", apiBaseUrl="http://127.0.0.1:9", embedBaseUrl="http://localhost:9/v1")
    assert cfg.parent_origin == "app://com.empresa.erp"
    assert cfg.client == "flutter/0.1.0"  # o cenário fixa o client
    assert BFocusConfig.from_dict({k: v for k, v in SCENARIOS["configs"]["min"].items() if k != "client"}).client == f"python/{__version__}"
