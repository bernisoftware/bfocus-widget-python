"""Todos os casos aplicáveis de scenarios.json (BRIEF §5.1)."""
from __future__ import annotations

import json

import pytest
from conftest import SCENARIOS, config_for

from bfocus_widget.core.badge import BadgeModel
from bfocus_widget.core.host import WidgetHost
from bfocus_widget.core.payload import embed_url, launcher_state_request, user_payload_base64, user_payload_json
from bfocus_widget.core.pill import pill_from
from bfocus_widget.core.push import handle_push, push_register_request, push_unregister_request
from bfocus_widget.core.storage import StateStore
from bfocus_widget.core.widget import BFocusWidget


def _ids(cases):
    return [c["name"] for c in cases]


@pytest.mark.parametrize("case", SCENARIOS["userPayload"], ids=_ids(SCENARIOS["userPayload"]))
def test_user_payload(case):
    cfg = config_for(case["config"])
    assert user_payload_json(cfg) == case["json"]
    assert user_payload_base64(cfg) == case["base64"]


@pytest.mark.parametrize("case", SCENARIOS["embedUrl"], ids=_ids(SCENARIOS["embedUrl"]))
def test_embed_url(case):
    cfg = config_for(case["config"])
    url = embed_url(cfg, page=case["page"], mode=case["mode"], open=case.get("open"), view=case.get("view"), ids=case.get("ids"))
    assert url == case["expect"]


@pytest.mark.parametrize("case", SCENARIOS["launcherStateRequest"], ids=_ids(SCENARIOS["launcherStateRequest"]))
def test_launcher_state_request(case):
    req = launcher_state_request(config_for(case["config"]))
    exp = case["expect"]
    assert req.method == exp["method"]
    assert req.url == exp["url"]
    # Os cinco headers, com os mesmos nomes e na mesma ordem.
    assert list(req.headers.items()) == list(exp["headers"].items())
    assert req.body == exp["body"]


def _run_badge_model(case):
    m = BadgeModel(last_seen=case["initial"]["last_seen"], label=case["initial"]["label"])
    for step in case["steps"]:
        op = step["op"]
        if op == "state":
            m.state(step["latest_event_at"])
        elif op == "open":
            m.open()
        elif op == "close":
            m.close()
        elif op == "unread":
            m.unread(step["count"])
        elif op == "seen":
            m.seen(step["latest_event_at"])
        exp = step["expect"]
        if "label" in exp:
            assert m.label == exp["label"], f"{case['name']} / {op}"
        if "last_seen" in exp:
            assert m.last_seen == exp["last_seen"], f"{case['name']} / {op}"


@pytest.mark.parametrize("case", SCENARIOS["badge"], ids=_ids(SCENARIOS["badge"]))
def test_badge_model(case):
    _run_badge_model(case)


class _Host(WidgetHost):
    def __init__(self):
        self.calls = []

    def show_tickets(self, target):
        self.calls.append(("show_tickets", target))

    def hide_tickets(self):
        self.calls.append(("hide_tickets",))


@pytest.mark.parametrize("case", SCENARIOS["badge"], ids=_ids(SCENARIOS["badge"]))
def test_badge_through_widget(case, tmp_path):
    """Os mesmos passos, agora pelo BFocusWidget (callbacks, persistência e embed)."""
    store = StateStore(tmp_path)
    widget = BFocusWidget(config_for("min"), host=_Host(), store=store)
    widget._badge = BadgeModel(last_seen=case["initial"]["last_seen"], label=case["initial"]["label"])
    for step in case["steps"]:
        op = step["op"]
        if op == "state":
            widget._apply_state({"tickets": {"latest_event_at": step["latest_event_at"]}})
        elif op == "open":
            widget.open()
        elif op == "close":
            widget.close()
        elif op == "unread":
            widget.dispatch_embed_message(json.dumps({"type": "bfocus:unread", "payload": {"count": step["count"]}}))
        elif op == "seen":
            widget.dispatch_embed_message({"type": "bfocus:seen", "payload": {"latest_event_at": step["latest_event_at"]}})
        exp = step["expect"]
        if "label" in exp:
            assert widget.badge_label == exp["label"]
        if "last_seen" in exp:
            assert widget.last_seen == exp["last_seen"]
            if exp["last_seen"] is not None and widget.last_seen != case["initial"]["last_seen"]:
                assert store.get_last_seen(widget.scope) == exp["last_seen"]


@pytest.mark.parametrize("case", SCENARIOS["pill"], ids=_ids(SCENARIOS["pill"]))
def test_pill(case):
    st = pill_from(case["releaseNotes"])
    assert st.label == case["expect"]["label"]
    assert st.dot == case["expect"]["dot"]
    assert list(st.banner_ids) == case["expect"]["bannerIds"]
    assert st.as_dict() == case["expect"]


def test_push_register():
    case = SCENARIOS["push"]["register"]
    cfg = config_for(case["config"])
    req = push_register_request(cfg, case["token"], case["platform"])
    assert req.method == case["expect"]["method"]
    assert req.url == case["expect"]["url"]
    assert json.loads(req.body) == case["expect"]["json"]
    # Mesmos headers do launcher-state.
    assert req.headers == launcher_state_request(cfg).headers


def test_push_unregister():
    case = SCENARIOS["push"]["unregister"]
    req = push_unregister_request(config_for(case["config"]), case["token"])
    assert req.method == case["expect"]["method"]
    assert req.url == case["expect"]["url"]
    assert json.loads(req.body) == case["expect"]["json"]


@pytest.mark.parametrize("case", SCENARIOS["push"]["handle"], ids=_ids(SCENARIOS["push"]["handle"]))
def test_push_handle(case, tmp_path):
    res = handle_push(case["data"])
    assert res.handled == case["expect"]["handled"]
    assert res.navigate == case["expect"]["navigate"]
    # Pelo widget: tratado → abre o widget no item certo.
    host = _Host()
    widget = BFocusWidget(config_for("min"), host=host, store=StateStore(tmp_path))
    assert widget.handle_push(case["data"]) == case["expect"]["handled"]
    if case["expect"]["handled"]:
        assert host.calls == [("show_tickets", case["expect"]["navigate"])]
    else:
        assert host.calls == []
