"""Host Qt contra o embed simulado (mock-embed.html, script=all) servido pelo mock-server.

Roda com QT_QPA_PLATFORM=offscreen por padrão; `BFOCUS_QT_WINDOWED=1` usa janelas de verdade.
"""
from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("PySide6.QtWebEngineWidgets", reason="PySide6 com QtWebEngine não instalado")

if not os.environ.get("BFOCUS_QT_WINDOWED"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from conftest import SCENARIOS, config_for  # noqa: E402

from bfocus_widget.qt import BFocusLauncherButton, BFocusQtHost, BFocusReleaseBadge  # noqa: E402  (antes do QApplication)
from bfocus_widget.qt.host import _BannerDialog  # noqa: E402
from bfocus_widget.core.storage import StateStore  # noqa: E402
from bfocus_widget import __version__  # noqa: E402
from bfocus_widget.core.widget import BFocusWidget  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtWebEngineCore import QWebEngineProfile  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

pytestmark = [pytest.mark.qt, pytest.mark.integration]


def flush_deletes(app, rounds=5):
    """Entrega os `deleteLater` pendentes. Fora de um `exec()` o Qt não roda os DeferredDelete
    sozinho: sem isto as views ficariam vivas até o fim do processo."""
    for _ in range(rounds):
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        time.sleep(0.01)
    app.processEvents()


@pytest.fixture(scope="module")
def qapp():
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication(["bfocus-tests"])
    yield app
    # Nada de Qt pode sobrar para a finalização do interpretador: janelas soltas (diálogos sem
    # pai) são destruídas aqui, na thread principal, com o QApplication ainda vivo.
    for w in QApplication.topLevelWidgets():
        w.close()
        w.deleteLater()
    flush_deletes(app)


@pytest.fixture
def qt_owned(qapp):
    """Destrói na ordem certa o que cada teste criou: páginas/views (hosts) ANTES do perfil.
    O perfil apagado com página viva é o "Release of profile requested but WebEnginePage still
    not deleted" e termina em SIGSEGV no fim do processo (QQuickWidget::~QQuickWidget)."""
    owned = {"widgets": [], "hosts": [], "profiles": []}
    yield owned
    for widget in owned["widgets"]:
        widget.shutdown()
    for host in owned["hosts"]:
        try:
            host._retry_timer.stop()
            host._ready_timer.stop()
            host._drop_views()  # tickets + banner + histórico (estes dois são janelas sem pai)
            host.hide()
            host.deleteLater()
        except RuntimeError:  # objeto C++ já destruído
            pass
    flush_deletes(qapp, rounds=20)  # o Chromium desmonta as páginas de forma assíncrona
    for profile in owned["profiles"]:
        try:
            profile.deleteLater()
        except RuntimeError:
            pass
    flush_deletes(qapp)


def pump(app, pred, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    app.processEvents()
    return pred()


def make_host(qt_owned, qapp, widget, **kw):
    # Perfil sem disco (off-the-record) para o teste não sujar o perfil real.
    profile = QWebEngineProfile(qapp)
    host = BFocusQtHost(widget, profile=profile, **kw)
    qt_owned["widgets"].append(widget)
    qt_owned["hosts"].append(host)
    qt_owned["profiles"].append(profile)
    return host


def make(server, qapp, qt_owned, tmp_path, **kw):
    cfg = config_for("min", apiBaseUrl=server.base, embedBaseUrl=server.base + "/v1", client=f"python/{__version__}")
    events = {"errors": [], "badge": []}
    widget = BFocusWidget(
        cfg, store=StateStore(tmp_path),
        on_error=lambda code, detail: events["errors"].append(code),
        on_badge_changed=lambda label: events["badge"].append(label),
        notifier=lambda *a: None, **kw,
    )
    host = make_host(qt_owned, qapp, widget, extra_params=[("script", "all")])
    opened = []
    host.external_opener = lambda url: opened.append(url.toString())
    host.save_path_provider = lambda name: str(tmp_path / name)
    got = []
    host.message_observers.append(lambda surface, msg: got.append((surface, msg)))
    return widget, host, events, opened, got


def test_tickets_bridge_and_host_messages(server, qapp, qt_owned, tmp_path):
    widget, host, events, opened, got = make(server, qapp, qt_owned, tmp_path)
    button = BFocusLauncherButton(widget)
    widget.start()
    assert widget.wait_first_call(10)
    widget.open()
    assert pump(qapp, lambda: any(m["type"] == "bfocus:close" for _, m in got)), got

    expected = [
        {k: (v if k != "payload" else {pk: (pv.replace("{origin}", server.base) if isinstance(pv, str) else pv) for pk, pv in v.items()})
         for k, v in m.items()}
        for m in SCENARIOS["mockEmbedScript"]["tickets"]
    ]
    assert [m for _, m in got] == expected
    assert all(s == "tickets" for s, _ in got)
    # Efeitos no pacote:
    assert widget.primary_color == "#123456"                 # branding
    assert "WIDGET_USER_HASH_INVALID" in events["errors"]     # error → onError
    assert "3" in events["badge"] and button.label == "3"     # unread (aberto)
    assert widget.last_seen == "2026-09-01T10:05:00+00:00"    # seen
    assert opened == ["https://example.com/docs"]             # openExternal
    assert not widget.is_open and not host.isVisible()        # close
    saved = tmp_path / "manual.pdf"
    assert pump(qapp, lambda: saved.exists() and saved.read_bytes().startswith(b"%PDF"), 10)  # download

    # Pacote → embed: open, navigate e close chegam ao embed (o embed registra em /__received).
    widget.open()
    widget.open(("ticket", "t-1"))
    widget.close()
    assert pump(qapp, lambda: len(server.log()["received"]) >= 5, 10), server.log()["received"]
    received = server.log()["received"]
    assert [m["type"] for m in received] == ["bfocus:open", "bfocus:close", "bfocus:open", "bfocus:navigate", "bfocus:close"]
    assert received[3]["payload"] == {"view": "ticket", "ticketId": "t-1"}
    # O que o embed enviou é exatamente o roteiro (visto pelo próprio embed).
    assert [m["type"] for m in server.log()["sent"]] == [m["type"] for m in expected]

    # Navegação para outra origem: cancelada e aberta no navegador do sistema.
    host._tickets.page().runJavaScript("document.getElementById('link-external').click()")
    assert pump(qapp, lambda: "https://example.com/external" in opened, 10)
    assert host._tickets.page().url().toString().startswith(server.base + "/v1/embed.html")
    button.deleteLater()
    widget.logout()
    assert pump(qapp, lambda: host._tickets is None, 5)


def test_banner_modal_and_history(server, qapp, qt_owned, tmp_path):
    server.scenario("banner")
    widget, host, events, opened, got = make(server, qapp, qt_owned, tmp_path)
    pill = BFocusReleaseBadge(widget)
    widget.start()
    assert pump(qapp, lambda: host._banner is not None, 10)
    server.scenario("default")  # depois da ciência, o servidor não manda mais os ids
    assert host._banner.isVisible() and host._banner.isModal()
    assert pump(qapp, lambda: host._banner is None, 15), got  # rn:done fecha o banner
    assert pump(qapp, lambda: len(server.log()["requests"]) >= 2, 10)  # e consulta de novo
    sent = server.log()["sent"]
    assert sent[0] == {"type": "bfocus:ready", "payload": {"hp": 1, "widget": "release-notes", "view": "banner"}}
    assert [m["type"] for _, m in got] == ["bfocus:ready", "bfocus:rn:branding", "bfocus:rn:done"]
    assert pill.label == "v4.2.0"

    got.clear()
    pill.click()  # tocar na pílula abre o histórico
    assert pump(qapp, lambda: host._history is not None and host._history.isVisible(), 10)
    assert pump(qapp, lambda: any(m["type"] == "bfocus:rn:closeHistory" for _, m in got), 10)
    assert pump(qapp, lambda: not host._history.isVisible(), 5)
    assert all(s == "history" for s, _ in got)
    widget.shutdown()
    pill.deleteLater()


def test_banner_dialog_cannot_be_dismissed(qapp):
    dlg = _BannerDialog(None)
    dlg.show()
    dlg.reject()
    dlg.close()
    qapp.processEvents()
    assert dlg.isVisible()
    dlg.allow_close = True
    dlg.close()
    qapp.processEvents()
    assert not dlg.isVisible()
    dlg.deleteLater()
    flush_deletes(qapp)


def test_offline_screen(qapp, qt_owned, tmp_path):
    # Porta fechada: a página não carrega → tela "sem conexão" com "tentar de novo".
    cfg = config_for("min", apiBaseUrl="http://127.0.0.1:9", embedBaseUrl="http://127.0.0.1:9/v1")
    widget = BFocusWidget(cfg, store=StateStore(tmp_path), notifier=lambda *a: None)
    host = make_host(qt_owned, qapp, widget)
    widget.open()
    assert pump(qapp, lambda: host._stack.currentWidget() is host._offline, 20)
    widget.close()
