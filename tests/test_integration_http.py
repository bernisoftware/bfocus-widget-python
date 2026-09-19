"""Integração com o servidor simulado (BRIEF §5.2): cliente HTTP real, sem transporte falso."""
from __future__ import annotations

import base64
import json
import time

import pytest
from conftest import SCENARIOS, config_for

from bfocus_widget import __version__
from bfocus_widget.client.api import WidgetApi
from bfocus_widget.core.host import WidgetHost
from bfocus_widget.core.http import ApiError
from bfocus_widget.core.payload import launcher_state_request
from bfocus_widget.core.storage import StateStore
from bfocus_widget.core.widget import BFocusWidget

pytestmark = pytest.mark.integration

FIX = SCENARIOS["launcherStateFixtures"]


class RecHost(WidgetHost):
    def __init__(self):
        self.banners = []

    def show_banner(self, ids):
        self.banners.append(list(ids))


def cfg_for(server, **kw):
    return config_for("min", apiBaseUrl=server.base, embedBaseUrl=server.base + "/v1", client=f"python/{__version__}", **kw)


def wait_until(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def test_default_scenario_and_headers(server, tmp_path):
    cfg = cfg_for(server)
    widget = BFocusWidget(cfg, store=StateStore(tmp_path))
    widget.start()
    assert widget.wait_first_call(10)
    widget.shutdown()
    assert widget.release_notes.label == "v4.2.0"
    assert widget.primary_color == FIX["default"]["primary_color"]
    assert widget.badge_label == ""  # primeira visita só grava a base
    assert widget.last_seen == FIX["default"]["tickets"]["latest_event_at"]

    reqs = server.log()["requests"]
    assert len(reqs) == 1
    got = reqs[0]
    expected = launcher_state_request(cfg)
    assert got["method"] == "POST"
    assert got["path"] == "/api/v1/widget/launcher-state"
    assert got["body"] == "{}"
    for name, value in expected.headers.items():
        assert got["headers"][name.lower()] == value
    user = json.loads(base64.b64decode(got["headers"]["x-bfocus-widget-user"]))
    assert user == {"externalId": "USR-1", "locale": "pt_BR", "customer": {"externalId": "ACME-1"}}


def test_badge_lights_on_new_event(server, tmp_path):
    cfg = cfg_for(server)
    store = StateStore(tmp_path)
    store.set_last_seen(f"{cfg.publishable_key}:USR-1:ACME-1", "2026-09-01T11:00:00-03:00")  # = 14:00Z > 12:00Z
    labels = []
    widget = BFocusWidget(cfg, store=store, on_badge_changed=labels.append)
    widget.start()
    widget.wait_first_call(10)
    widget.shutdown()
    assert labels == []  # 12:00Z não é depois de 14:00Z
    store.set_last_seen(widget.scope, "2026-09-01T08:00:00-03:00")  # = 11:00Z < 12:00Z
    widget2 = BFocusWidget(cfg, store=store, on_badge_changed=labels.append)
    widget2.start()
    widget2.wait_first_call(10)
    widget2.shutdown()
    assert labels == ["•"]


def test_banner_scenario(server, tmp_path):
    server.scenario("banner")
    host = RecHost()
    widget = BFocusWidget(cfg_for(server, product="erp", audience="both"), host=host, store=StateStore(tmp_path))
    widget.start()
    widget.wait_first_call(10)
    widget.shutdown()
    assert host.banners == [FIX["banner"]["release_notes"]["banner_ids"]]
    assert widget.release_notes.dot is True
    req = server.log()["requests"][0]
    assert req["query"] == {"product": "erp", "audience": "both"}


def test_identity_error_scenario(server, tmp_path):
    server.scenario("identity_error")
    errors, provided = [], []

    def provider():
        provided.append(1)
        return f"h{len(provided)}"

    widget = BFocusWidget(
        cfg_for(server, userHashProvider=provider), store=StateStore(tmp_path),
        on_error=lambda code, detail: errors.append(code),
    )
    widget.start()
    widget.wait_first_call(10)
    widget.shutdown()
    # init chama o provider; o 401 pede um hash novo UMA vez e repete; a repetição também
    # é recusada → onError uma vez.
    assert errors == ["WIDGET_USER_HASH_INVALID"]
    assert len(provided) == 2
    reqs = server.log()["requests"]
    assert len(reqs) == 2
    hashes = [json.loads(base64.b64decode(r["headers"]["x-bfocus-widget-user"]))["userHash"] for r in reqs]
    assert hashes == ["h1", "h2"]


def test_push_register_and_unregister_on_logout(server, tmp_path):
    cfg = cfg_for(server)
    widget = BFocusWidget(cfg, store=StateStore(tmp_path))
    widget.start()
    widget.register_push_token("fcm-token-1", "android")
    assert wait_until(lambda: len(server.log()["requests"]) >= 2)
    widget.logout()
    assert wait_until(lambda: len(server.log()["requests"]) >= 3)
    reqs = server.log()["requests"]
    assert [r["path"] for r in reqs] == [
        "/api/v1/widget/launcher-state", "/api/v1/widget/push/devices", "/api/v1/widget/push/devices/unregister",
    ]
    assert json.loads(reqs[1]["body"]) == SCENARIOS["push"]["register"]["expect"]["json"]
    assert json.loads(reqs[2]["body"]) == {"token": "fcm-token-1"}


def test_widget_api_against_mock(server):
    api = WidgetApi(cfg_for(server))
    data = api.launcher_state()
    assert data == FIX["default"]
    with pytest.raises(ApiError) as exc:
        api.get_config()  # o servidor simulado não tem /config
    assert exc.value.status == 404
    assert api.fetch_bytes(server.base + "/files/manual.pdf").startswith(b"%PDF")
    headers = server.log()["requests"][-1]["headers"]
    assert "x-bfocus-widget-user" not in headers  # /config não leva usuário
    assert headers["x-bfocus-client"] == f"python/{__version__}"
