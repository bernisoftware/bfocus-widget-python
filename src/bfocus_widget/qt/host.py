"""Host Qt: o embed da CDN numa `QWebEngineView` (Host Protocol v1).

- Ponte embed → pacote: `QWebChannel`. O pacote injeta `qwebchannel.js` e define
  `window.bFocusHost = {postMessage}`, depois chama `window.bFocusEmbed.flush()`.
  Só aceita mensagens quando a página está na origem de `embed_base_url`.
- Pacote → embed: `runJavaScript("window.bFocusEmbed && window.bFocusEmbed.receive(...)")`.
- A WebView é mantida entre aberturas (`bfocus:open`/`bfocus:close`); recriada só no
  `logout()` ou quando a identidade muda.
- Navegação para outra origem é cancelada e aberta no navegador do sistema.
- Banner de release notes: diálogo modal do tamanho da tela, sem botão de fechar, que só
  fecha no `bfocus:rn:done`.

Importe `bfocus_widget.qt` ANTES de criar o `QApplication` (exigência do QtWebEngine).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Iterable, List, Optional, Tuple

from PySide6.QtCore import QFile, QIODevice, QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.host import WidgetHost
from ..core.payload import embed_origin, open_param
from ..core.widget import BFocusWidget
from ..shared import Translator

log = logging.getLogger("bfocus_widget")

READY_TIMEOUT_MS = 20_000     # carregou mas o embed não disse "ready": trata como sem conexão
OFFLINE_RETRY_MS = 15_000     # nova tentativa automática na tela de sem conexão
PANEL_SIZE = (400, 620)       # mesmo painel do web (spec §2)

# Canal genérico do protocolo (§2): window.bFocusHost via QWebChannel.
_BOOT_JS = """
(function () {
  if (!window.qt || !qt.webChannelTransport || typeof QWebChannel === 'undefined') return;
  new QWebChannel(qt.webChannelTransport, function (ch) {
    var bridge = ch.objects.bfocusBridge;
    window.bFocusHost = {
      postMessage: function (s) { bridge.postMessage(typeof s === 'string' ? s : JSON.stringify(s)); }
    };
    if (window.bFocusEmbed && window.bFocusEmbed.flush) window.bFocusEmbed.flush();
  });
})();
"""

_CLEAR_STORAGE_JS = "try{localStorage.clear();sessionStorage.clear()}catch(e){}"


def _qwebchannel_source() -> str:
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if f.open(QIODevice.ReadOnly):
        try:
            return bytes(f.readAll()).decode("utf-8")
        finally:
            f.close()
    raise RuntimeError("qwebchannel.js não encontrado nos recursos do Qt (PySide6 sem QtWebChannel?)")


def _url_origin(url: QUrl) -> str:
    port = url.port()
    host = url.host()
    netloc = f"{host}:{port}" if port != -1 else host
    return f"{url.scheme()}://{netloc}"


class QtDispatcher(QObject):
    """Leva chamadas de threads de fundo (consulta do launcher-state) para a thread da UI."""

    _call = Signal(object)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._call.connect(self._run, Qt.QueuedConnection)

    @Slot(object)
    def _run(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001
            log.exception("bfocus: callback na thread da UI falhou")

    def __call__(self, fn: Callable[[], None]) -> None:
        if QThread.currentThread() is self.thread():
            self._run(fn)
        else:
            self._call.emit(fn)


class _Bridge(QObject):
    """Objeto exposto ao JS como `bfocusBridge`. Só texto entra."""

    def __init__(self, view: "EmbedView") -> None:
        super().__init__(view)
        self._view = view

    @Slot(str)
    def postMessage(self, message: str) -> None:  # noqa: N802 - nome do protocolo
        self._view._from_bridge(message)


class _ExternalPage(QWebEnginePage):
    """Página descartável para `target=_blank`/`window.open`: nada abre dentro do widget."""

    def __init__(self, profile: QWebEngineProfile, opener: Callable[[QUrl], None], parent: QObject) -> None:
        super().__init__(profile, parent)
        self._opener = opener

    def acceptNavigationRequest(self, url: QUrl, nav_type: Any, is_main_frame: bool) -> bool:  # noqa: N802
        if url.scheme() in ("http", "https", "mailto", "tel"):
            self._opener(url)
        QTimer.singleShot(0, self.deleteLater)
        return False


class _EmbedPage(QWebEnginePage):
    def __init__(self, profile: QWebEngineProfile, allowed_origin: str, opener: Callable[[QUrl], None], parent: QObject) -> None:
        super().__init__(profile, parent)
        self._allowed = allowed_origin
        self._opener = opener

    def acceptNavigationRequest(self, url: QUrl, nav_type: Any, is_main_frame: bool) -> bool:  # noqa: N802
        if url.scheme() in ("about", "data", "blob") or _url_origin(url) == self._allowed:
            return True
        # Outra origem: cancela. Clique do usuário no quadro principal vai para o navegador.
        if is_main_frame and url.scheme() in ("http", "https", "mailto", "tel"):
            self._opener(url)
        return False

    def createWindow(self, _type: Any) -> QWebEnginePage:  # noqa: N802
        return _ExternalPage(self.profile(), self._opener, self)

    def javaScriptConsoleMessage(self, level: Any, message: str, line: int, source: str) -> None:  # noqa: N802
        log.debug("bfocus embed console: %s (%s:%s)", message, source, line)


class EmbedView(QWebEngineView):
    """Uma superfície do embed (`tickets`, `banner` ou `history`) com a ponte instalada."""

    def __init__(self, host: "BFocusQtHost", surface: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.host = host
        self.surface = surface
        self.ready = False
        self._pending: List[dict] = []
        page = _EmbedPage(host.profile, host.allowed_origin, host.open_external_url, self)
        self.setPage(page)
        self._channel = QWebChannel(page)
        self._bridge = _Bridge(self)
        self._channel.registerObject("bfocusBridge", self._bridge)
        page.setWebChannel(self._channel, QWebEngineScript.MainWorld)
        scripts = page.scripts()
        for name, src, point in (
            ("bfocus-qwebchannel", host.qwebchannel_js, QWebEngineScript.DocumentCreation),
            ("bfocus-host", _BOOT_JS, QWebEngineScript.DocumentReady),
        ):
            s = QWebEngineScript()
            s.setName(name)
            s.setSourceCode(src)
            s.setInjectionPoint(point)
            s.setWorldId(QWebEngineScript.MainWorld)
            s.setRunsOnSubFrames(False)
            scripts.insert(s)
        self.loadStarted.connect(self._on_load_started)

    def _on_load_started(self) -> None:
        self.ready = False

    def _from_bridge(self, message: str) -> None:
        # A ponte só vale na origem do embed (a navegação já é travada; isto é a 2ª trava).
        if _url_origin(self.page().url()) != self.host.allowed_origin:
            log.warning("bfocus: mensagem da ponte recusada (origem %s)", self.page().url().toString())
            return
        self.host._on_message(self, message)

    def send(self, msg: dict) -> None:
        """Pacote → embed. Antes do `ready`, guarda e entrega depois."""
        if not self.ready:
            self._pending.append(msg)
            return
        js = "window.bFocusEmbed && window.bFocusEmbed.receive(" + json.dumps(msg, ensure_ascii=False) + ")"
        self.page().runJavaScript(js, QWebEngineScript.MainWorld)

    def mark_ready(self) -> None:
        self.ready = True
        pending, self._pending = self._pending, []
        for msg in pending:
            self.send(msg)


class _BannerDialog(QDialog):
    """Modal do tamanho da tela que o usuário não fecha (nem Esc, nem botão)."""

    def __init__(self, parent: Optional[QWidget]) -> None:
        super().__init__(parent, Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        self.setWindowModality(Qt.ApplicationModal)
        self.allow_close = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

    def reject(self) -> None:  # Esc
        if self.allow_close:
            super().reject()

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        if self.allow_close:
            super().closeEvent(event)
        else:
            event.ignore()


class _HistoryWindow(QWidget):
    def __init__(self, host: "BFocusQtHost") -> None:
        super().__init__(None, Qt.Window)
        self._host = host
        self.resize(420, 640)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        event.ignore()
        self.hide()


class BFocusQtHost(QWidget, WidgetHost):
    """Painel do widget (400 × 620). Sem `parent`, é uma janela própria; com `parent`, entra
    no layout do integrador. Liga-se ao `BFocusWidget` no construtor."""

    def __init__(
        self,
        widget: BFocusWidget,
        parent: Optional[QWidget] = None,
        *,
        profile: Optional[QWebEngineProfile] = None,
        extra_params: Optional[Iterable[Tuple[str, str]]] = None,
    ) -> None:
        super().__init__(parent, Qt.Window if parent is None else Qt.Widget)
        self.widget = widget
        self._t = Translator(widget.config.locale or "pt_BR")
        self._extra = list(extra_params or [])
        self.allowed_origin = embed_origin(widget.config)
        self.qwebchannel_js = _qwebchannel_source()
        # Perfil persistente e exclusivo: o localStorage do embed (estado de "lido") sobrevive.
        self.profile = profile or QWebEngineProfile("bfocus-widget", QApplication.instance())
        self.profile.downloadRequested.connect(self._on_download_requested)
        # Pontos de extensão (testes ou integradores): abrir link externo e escolher onde salvar.
        self.external_opener: Callable[[QUrl], Any] = QDesktopServices.openUrl
        self.save_path_provider: Callable[[str], Optional[str]] = self._ask_save_path
        self.message_observers: List[Callable[[str, dict], None]] = []
        self.downloads: List[Any] = []

        self._dispatcher = QtDispatcher(self)
        self.setWindowTitle(self._t.t("header_title"))
        if parent is None:
            self.resize(*PANEL_SIZE)
        self._stack = QStackedWidget(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)
        self._loading = QLabel(self._t.t("loading"), alignment=Qt.AlignCenter)
        self._offline = self._build_offline()
        self._stack.addWidget(self._loading)
        self._stack.addWidget(self._offline)
        self._tickets: Optional[EmbedView] = None
        self._pending_nav: Optional[dict] = None
        self._banner: Optional[_BannerDialog] = None
        self._banner_view: Optional[EmbedView] = None
        self._history: Optional[_HistoryWindow] = None
        self._history_view: Optional[EmbedView] = None
        self._ready_timer = QTimer(self, singleShot=True, interval=READY_TIMEOUT_MS)
        self._ready_timer.timeout.connect(self._show_offline)
        self._retry_timer = QTimer(self, singleShot=True, interval=OFFLINE_RETRY_MS)
        self._retry_timer.timeout.connect(self.retry)
        self._shown = False
        widget.attach_host(self, self._dispatcher)

    # ── telas auxiliares ──────────────────────────────────────────────────────
    def _build_offline(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.addStretch(1)
        title = QLabel(self._t.native("offline_title"), alignment=Qt.AlignCenter)
        title.setStyleSheet("font-size: 16px; font-weight: 600; color: #0f172a;")
        desc = QLabel(self._t.native("offline_desc"), alignment=Qt.AlignCenter, wordWrap=True)
        desc.setStyleSheet("color: #64748b;")
        btn = QPushButton(self._t.t("btn_retry"))
        btn.clicked.connect(self.retry)
        lay.addWidget(title)
        lay.addWidget(desc)
        lay.addWidget(btn, alignment=Qt.AlignCenter)
        lay.addStretch(1)
        return box

    def _show_offline(self) -> None:
        if self._tickets is not None and self._tickets.ready:
            return
        self._stack.setCurrentWidget(self._offline)
        if self._shown:
            self._retry_timer.start()

    def retry(self) -> None:
        """'Tentar de novo' da tela de sem conexão."""
        self._retry_timer.stop()
        if self._tickets is None:
            return
        self._stack.setCurrentWidget(self._loading)
        self._tickets.reload() if not self._tickets.url().isEmpty() else self._load_tickets(self._pending_nav)

    # ── WidgetHost ────────────────────────────────────────────────────────────
    def show_tickets(self, target: Optional[dict]) -> None:
        was_shown = self._shown
        self._shown = True
        if self._tickets is None:
            self._create_tickets(target)
        else:
            if not was_shown and self._tickets.ready:
                self._tickets.send({"type": "bfocus:open"})  # liga o stream do chat
            if target is not None:
                if self._tickets.ready:
                    self._tickets.send({"type": "bfocus:navigate", "payload": target})
                else:
                    self._pending_nav = target
        if self.parent() is None and not self.isVisible():
            self._place_near_corner()
        self.show()
        self.raise_()
        self.activateWindow()

    def hide_tickets(self) -> None:
        self._shown = False
        self._retry_timer.stop()
        if self._tickets is not None:
            # Obrigatório ao esconder sem destruir: desliga o stream do chat.
            self._tickets.send({"type": "bfocus:close"})
        self.hide()

    def show_banner(self, ids: List[str]) -> None:
        if self._banner is not None and self._banner.isVisible():
            return
        top = self.window() if self.parent() is not None else None
        dlg = _BannerDialog(top)
        view = EmbedView(self, "banner", dlg)
        dlg.layout().addWidget(view)
        view.load(QUrl(self.widget.embed_url(page="release-notes", view="banner", ids=ids, extra=self._extra)))
        screen = (top.screen() if top is not None else None) or QGuiApplication.primaryScreen()
        if screen is not None:
            dlg.setGeometry(screen.availableGeometry())
        self._banner, self._banner_view = dlg, view
        dlg.show()
        dlg.raise_()

    def hide_banner(self) -> None:
        dlg, self._banner = self._banner, None
        self._banner_view = None
        if dlg is not None:
            dlg.allow_close = True
            dlg.close()
            dlg.deleteLater()

    def show_history(self) -> None:
        if self._history is None:
            self._history = _HistoryWindow(self)
            self._history.setWindowTitle(self._t.rn("historyTitle"))
            self._history_view = EmbedView(self, "history", self._history)
            self._history.layout().addWidget(self._history_view)
            self._history_view.load(QUrl(self.widget.embed_url(page="release-notes", view="history", extra=self._extra)))
        self._history.show()
        self._history.raise_()
        self._history.activateWindow()

    def hide_history(self) -> None:
        if self._history is not None:
            self._history.hide()

    def reset(self) -> None:
        """logout(): limpa o localStorage/cookies/cache da origem e descarta as WebViews."""
        self._clear_origin_storage()
        self.profile.clearHttpCache()
        self.profile.cookieStore().deleteAllCookies()
        self._drop_views()
        self.hide()
        self._shown = False

    def reload(self) -> None:
        """Hash novo: a URL (payload) mudou, então recria as telas."""
        was_shown = self._shown
        self._drop_views()
        if was_shown:
            self._create_tickets(None)

    # ── internos ──────────────────────────────────────────────────────────────
    def _create_tickets(self, target: Optional[dict]) -> None:
        view = EmbedView(self, "tickets")
        view.loadFinished.connect(self._on_tickets_loaded)
        self._stack.addWidget(view)
        self._tickets = view
        self._load_tickets(target)

    def _load_tickets(self, target: Optional[dict]) -> None:
        if self._tickets is None:
            return
        self._pending_nav = None
        self._stack.setCurrentWidget(self._loading)
        self._tickets.load(QUrl(self.widget.embed_url(open=open_param(target), extra=self._extra)))

    def _on_tickets_loaded(self, ok: bool) -> None:
        if not ok:
            self._show_offline()
        elif self._tickets is not None and not self._tickets.ready:
            self._ready_timer.start()
            # Canal injetado depois do carregamento: força a entrega do que o embed guardou.
            self._tickets.page().runJavaScript(
                "window.bFocusEmbed && window.bFocusEmbed.flush && window.bFocusEmbed.flush()", QWebEngineScript.MainWorld,
            )

    def _drop_views(self) -> None:
        if self._tickets is not None:
            self._stack.removeWidget(self._tickets)
            self._tickets.deleteLater()
            self._tickets = None
        self._stack.setCurrentWidget(self._loading)
        if self._banner is not None:
            self.hide_banner()
        if self._history is not None:
            self._history.deleteLater()
            self._history = None
            self._history_view = None

    def _clear_origin_storage(self) -> None:
        # Página em branco com a origem do embed: limpa o localStorage mesmo sem nada carregado.
        page = QWebEnginePage(self.profile, self)

        def done(_ok: bool) -> None:
            page.runJavaScript(_CLEAR_STORAGE_JS, QWebEngineScript.MainWorld, lambda _r: page.deleteLater())

        page.loadFinished.connect(done)
        page.setHtml("<html></html>", QUrl(self.allowed_origin + "/"))

    def _place_near_corner(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        g = screen.availableGeometry()
        self.move(g.right() - self.width() - 24, g.bottom() - self.height() - 96)

    def _on_message(self, view: EmbedView, raw: str) -> None:
        msg = self.widget.dispatch_embed_message(raw, view.surface)
        if msg is None:
            return
        kind = msg["type"]
        payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
        if kind == "bfocus:ready":
            view.mark_ready()
            if view is self._tickets:
                self._ready_timer.stop()
                self._retry_timer.stop()
                self._stack.setCurrentWidget(view)
                if self._pending_nav is not None:
                    view.send({"type": "bfocus:navigate", "payload": self._pending_nav})
                    self._pending_nav = None
                view.send({"type": "bfocus:open" if self._shown else "bfocus:close"})
        elif kind == "bfocus:openExternal":
            url = QUrl(str(payload.get("url") or ""))
            if url.scheme() in ("http", "https", "mailto", "tel"):
                self.open_external_url(url)
        elif kind == "bfocus:download":
            self.download(str(payload.get("url") or ""), str(payload.get("filename") or ""))
        for obs in list(self.message_observers):
            try:
                obs(view.surface, msg)
            except Exception:  # noqa: BLE001
                log.exception("bfocus: observador de mensagem falhou")

    def open_external_url(self, url: QUrl) -> None:
        self.external_opener(url)

    def download(self, url: str, filename: str) -> None:
        """`bfocus:download`: baixa pela própria WebEngine com o nome dado (salvar como)."""
        qurl = QUrl(url)
        if qurl.scheme() not in ("http", "https"):
            return
        page = self._tickets.page() if self._tickets is not None else QWebEnginePage(self.profile, self)
        page.download(qurl, filename or os.path.basename(qurl.path()) or "download")

    def _ask_save_path(self, filename: str) -> Optional[str]:
        start = os.path.join(os.path.expanduser("~/Downloads"), filename)
        path, _ = QFileDialog.getSaveFileName(self.window(), self._t.native("save_attachment"), start)
        return path or None

    def _on_download_requested(self, item: Any) -> None:
        name = item.downloadFileName() or item.suggestedFileName() or "download"
        path = self.save_path_provider(name)
        if not path:
            item.cancel()
            return
        item.setDownloadDirectory(os.path.dirname(path))
        item.setDownloadFileName(os.path.basename(path))
        item.accept()
        self.downloads.append(item)

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        # Fechar a janela = fechar o widget (a WebView fica viva para reabrir rápido).
        if self.parent() is None:
            event.ignore()
            self.widget.close()
        else:
            super().closeEvent(event)
