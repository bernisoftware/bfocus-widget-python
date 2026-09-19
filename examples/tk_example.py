"""Exemplo mínimo com Tk: a UI nativa (sem WebView), botão e pílula de versão.

    pip install "bfocus-widget[tk]"
    BFOCUS_KEY=bf_pk_... BFOCUS_USER_HASH=<do seu servidor> python examples/tk_example.py
"""
from __future__ import annotations

import os
import tkinter as tk

from bfocus_widget import BFocusConfig, BFocusWidget, Customer, User
from bfocus_widget.tk import BFocusTkHost, BFocusTkLauncherButton, BFocusTkReleaseBadge


def main() -> None:
    root = tk.Tk()
    root.title("Meu ERP")
    root.geometry("900x600")

    widget = BFocusWidget(
        BFocusConfig(
            publishable_key=os.environ.get("BFOCUS_KEY", "bf_pk_demo"),
            app_id="com.exemplo.erp",
            user=User(external_id="USR-1", name="Ana Souza"),
            customer=Customer(external_id="ACME-1", name="Acme Ltda"),
            user_hash=os.environ.get("BFOCUS_USER_HASH"),  # calculado no SEU servidor
            notifications=True,
        ),
        on_error=lambda code, detail: print("bFocus:", code, detail or ""),
    )
    BFocusTkHost(widget, root)

    bar = tk.Frame(root)
    bar.pack(fill="x", padx=12, pady=8)
    tk.Label(bar, text="Meu ERP", font=("TkDefaultFont", 16, "bold")).pack(side="left")
    BFocusTkReleaseBadge(bar, widget).pack(side="right")
    BFocusTkLauncherButton(root, widget).place(relx=1.0, rely=1.0, x=-20, y=-20, anchor="se")

    widget.start()
    root.protocol("WM_DELETE_WINDOW", lambda: (widget.shutdown(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
