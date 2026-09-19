"""Painel do widget de chamados (400 × 620): cabeçalho do tenant e a tela da vez."""
from __future__ import annotations

import tkinter as tk
from typing import Any, Optional

from ..viewmodels.chat import ChatScreenVM
from ..viewmodels.formatting import mix
from ..viewmodels.tickets import NewTicketVM, TicketDetailVM
from .chat import ChatScreen
from .common import MUTED, SURFACE, WHITE, FlatButton, font, load_image, on_primary
from .tickets import DetailScreen, ListScreen, NewTicketScreen, SplashOverlay

PANEL_W, PANEL_H = 400, 620


class TicketsPanel(tk.Toplevel):
    def __init__(self, master: tk.Misc, app: Any) -> None:
        super().__init__(master)
        self.app = app
        t = app.ctx.t
        self.title(t.t("header_title"))
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{PANEL_W}x{PANEL_H}+{max(0, sw - PANEL_W - 24)}+{max(0, sh - PANEL_H - 96)}")
        self.minsize(340, 460)
        self.configure(bg=SURFACE)
        self.protocol("WM_DELETE_WINDOW", app.close)  # fechar só esconde: a tela é preservada
        self.header = tk.Frame(self, bg=app.primary)
        self.header.pack(fill="x")
        self.main = tk.Frame(self, bg=SURFACE)
        self.main.pack(fill="both", expand=True)
        self._header_sig: Any = None
        self._screen: Optional[tk.Widget] = None
        self._screen_key: Any = None
        self._overlay: Optional[SplashOverlay] = None
        self._unsub = app.subscribe(self._safe)
        self._safe()

    def _safe(self) -> None:
        try:
            if self.winfo_exists():
                self.update_view()
        except tk.TclError:
            pass

    def show(self) -> None:
        self.deiconify()
        self.lift()
        try:
            self.focus_force()
        except tk.TclError:
            pass

    def hide(self) -> None:
        self.withdraw()

    def _render_header(self) -> None:
        app, t = self.app, self.app.ctx.t
        sig = (app.primary, app.tenant_name, app.tenant_logo, app.customer_name, app.config is not None)
        if sig == self._header_sig:
            return
        self._header_sig = sig
        for w in self.header.winfo_children():
            w.destroy()
        p = app.primary
        self.header.configure(bg=p)
        row = tk.Frame(self.header, bg=p)
        row.pack(fill="x", padx=16, pady=12)
        # Logo do tenant; sem logo, a inicial num quadrado translúcido.
        logo = tk.Label(row, text=app.tenant_initial, bg=on_primary(p, 0.18), fg="#ffffff", font=font(self, 14, "bold"), width=2)
        logo.pack(side="left", padx=(0, 12))
        if app.tenant_logo:
            logo.configure(bg=WHITE)
            load_image(logo, app.ctx.runner, app.ctx.api, app.tenant_logo, 28)
        texts = tk.Frame(row, bg=p)
        texts.pack(side="left", fill="x", expand=True)
        tk.Label(texts, text=t.t("header_title"), bg=p, fg=on_primary(p, 0.8), font=font(self, 13), anchor="w").pack(fill="x")
        if app.tenant_name:
            tk.Label(texts, text=app.tenant_name, bg=p, fg="#ffffff", font=font(self, 15, "bold"), anchor="w").pack(fill="x")
        if app.customer_name:
            tk.Label(texts, text=app.customer_name, bg=p, fg=on_primary(p, 0.75), font=font(self, 11), anchor="w").pack(fill="x")
        FlatButton(row, "✕", app.close, bg=on_primary(p, 0.15), hover=on_primary(p, 0.3), px=14, padx=9, pady=4).pack(side="right")

    def _set_screen(self, key: Any, factory: Any) -> None:
        if key == self._screen_key and self._screen is not None:
            if hasattr(self._screen, "update_view"):
                self._screen.update_view()
            return
        if self._screen is not None:
            self._screen.destroy()
        self._screen_key = key
        self._screen = factory()
        self._screen.pack(fill="both", expand=True)

    def _message(self, text: str, detail: str = "", color: str = MUTED) -> tk.Frame:
        f = tk.Frame(self.main, bg=WHITE)
        box = tk.Frame(f, bg=WHITE)
        box.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(box, text=text, bg=WHITE, fg=color, font=font(self, 14), wraplength=330, justify="center").pack(padx=24)
        if detail:
            tk.Label(box, text=detail, bg=WHITE, fg="#888888", font=font(self, 11), wraplength=330, justify="center").pack(pady=(8, 0))
        return f

    def update_view(self) -> None:
        app, t = self.app, self.app.ctx.t
        self._render_header()
        state = app.state
        if state == "config_error":
            self._set_screen(("config_error", app.config_error_detail), lambda: self._message(t.t("error_load"), app.config_error_detail))
        elif state == "identity_error":
            # Identidade recusada: a tela inteira vira a mensagem (nunca lista vazia).
            self._set_screen(("identity_error",), lambda: self._message(t.t("error_identity"), color="#0f172a"))
        elif not app.ready or app.chat_loading:
            self._set_screen(("loading",), lambda: self._message(t.t("loading"), color=app.primary))
        else:
            name, page = app.view["name"], app.page
            if name == "list":
                self._set_screen(("list",), lambda: ListScreen(self.main, app))
            elif name == "new" and isinstance(page, NewTicketVM):
                self._set_screen(("new", id(page)), lambda: NewTicketScreen(self.main, app, page))
            elif name == "detail" and isinstance(page, TicketDetailVM):
                self._set_screen(("detail", id(page)), lambda: DetailScreen(self.main, app, page))
            elif name == "chat" and isinstance(page, ChatScreenVM):
                self._set_screen(("chat", id(page)), lambda: ChatScreen(self.main, app, page))
        if app.ready and app.splash_visible:
            if self._overlay is None:
                self._overlay = SplashOverlay(self.main, app)
                self._overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._overlay.lift()
        elif self._overlay is not None:
            self._overlay.destroy()
            self._overlay = None

    def destroy(self) -> None:
        self._unsub()
        super().destroy()
