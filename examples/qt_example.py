"""Exemplo mínimo com Qt (PySide6): botão flutuante, pílula de versão e o widget na WebView.

    pip install "bfocus-widget[qt]"
    BFOCUS_KEY=bf_pk_... BFOCUS_USER_HASH=<do seu servidor> python examples/qt_example.py

Contra o servidor simulado do kit de conformidade (sem conta no bFocus):
    node widgets-native/conformance/mock-server.mjs 8787
    BFOCUS_MOCK=http://127.0.0.1:8787 python examples/qt_example.py
"""
from __future__ import annotations

import os
import sys

# O QtWebEngine exige o import ANTES do QApplication.
from bfocus_widget.qt import BFocusLauncherButton, BFocusQtHost, BFocusReleaseBadge

from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMainWindow, QVBoxLayout, QWidget

from bfocus_widget import BFocusConfig, BFocusWidget, Customer, User


def config() -> BFocusConfig:
    mock = os.environ.get("BFOCUS_MOCK")
    return BFocusConfig(
        publishable_key=os.environ.get("BFOCUS_KEY", "bf_pk_demo"),
        app_id="com.exemplo.erp",  # cadastre app://com.exemplo.erp em Integrações → Apps nativos
        user=User(external_id="USR-1", name="Ana Souza", email="ana@exemplo.com.br"),
        customer=Customer(external_id="ACME-1", name="Acme Ltda"),
        # O hash vem do SEU servidor (sign_widget_identity); nunca calcule no app.
        user_hash=os.environ.get("BFOCUS_USER_HASH"),
        notifications=True,
        **({"api_base_url": mock, "embed_base_url": mock + "/v1"} if mock else {}),
    )


def main() -> int:
    app = QApplication(sys.argv)
    widget = BFocusWidget(config(), on_error=lambda code, detail: print("bFocus:", code, detail or ""))
    host = BFocusQtHost(widget)  # janela 400 × 620; passe um parent para embutir no seu layout

    win = QMainWindow()
    win.setWindowTitle("Meu ERP")
    central = QWidget()
    col = QVBoxLayout(central)
    top = QHBoxLayout()
    top.addWidget(QLabel("Meu ERP"))
    top.addStretch(1)
    top.addWidget(BFocusReleaseBadge(widget))
    col.addLayout(top)
    col.addStretch(1)
    bottom = QHBoxLayout()
    bottom.addStretch(1)
    bottom.addWidget(BFocusLauncherButton(widget))
    col.addLayout(bottom)
    win.setCentralWidget(central)
    win.resize(900, 600)
    win.show()

    widget.start()  # primeira consulta + polling
    code = app.exec()
    widget.shutdown()
    del host
    return code


if __name__ == "__main__":
    sys.exit(main())
