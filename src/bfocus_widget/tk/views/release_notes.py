"""Janelas de release notes na UI Tk: banner (modal que não fecha) e histórico (§6)."""
from __future__ import annotations

import tkinter as tk
from typing import Any, Optional

from ..viewmodels.formatting import mix
from ..viewmodels.release_notes import BannerVM, HistoryVM
from .common import BORDER, MUTED, SLATE, SUBTLE, SURFACE, TEXT, WHITE, FlatButton, ScrollArea, font
from .html import HtmlView, html_widget
from .tickets import GradientHeader


class BannerWindow(tk.Toplevel):
    """Tela cheia, sem botão de fechar: o usuário só sai confirmando cada release."""

    def __init__(self, master: tk.Misc, vm: BannerVM, primary: str) -> None:
        super().__init__(master)
        self.vm = vm
        self.primary = primary
        t = vm.ctx.t
        self.title(t.rn("eyebrow"))
        self.configure(bg=mix("#0f172a", "#ffffff", 0.55))  # o fundo escurecido do overlay web
        self.protocol("WM_DELETE_WINDOW", lambda: None)  # nem o ✕ da janela fecha
        self.bind("<Escape>", lambda _e: "break")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{sw}x{sh}+0+0")
        try:
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.card = tk.Frame(self, bg=WHITE)
        self.card.place(relx=0.5, rely=0.5, anchor="center", width=min(560, sw - 32), height=min(sh - 32, 640))
        self.header = GradientHeader(self.card, primary, [], pad=(22, 20))
        self.header.pack(fill="x")
        foot = tk.Frame(self.card, bg=WHITE, highlightthickness=1, highlightbackground=BORDER)
        foot.pack(side="bottom", fill="x")
        # A dica de rolagem fica no rodapé: no fim do conteúdo só apareceria quando já não serve.
        self.hint = tk.Label(foot, text="", bg=WHITE, fg=SUBTLE, font=font(self, 12, "bold"))
        self.button = FlatButton(foot, "", vm.confirm, bg=primary, pady=13, wraplength=480)
        self.button.pack(fill="x", padx=18, pady=14)
        self.error = tk.Label(foot, text="", bg=WHITE, fg="#b91c1c", font=font(self, 12), wraplength=480, justify="center")
        body = tk.Frame(self.card, bg=WHITE)
        body.pack(fill="both", expand=True)
        sb = tk.Scrollbar(body, orient="vertical")
        sb.pack(side="right", fill="y")
        self.html = HtmlView(body, "", bg=WHITE, fg="#334155", px=14, auto_height=False)
        self.html.pack(side="left", fill="both", expand=True, padx=22, pady=18)
        self.html.configure(yscrollcommand=lambda a, b: (sb.set(a, b), self._metrics()))
        sb.configure(command=self.html.yview)
        self._note_id: Optional[str] = None
        self._unsub = vm.subscribe(self._safe)
        self.after(60, self._grab)
        vm.load()
        self._safe()

    def _grab(self) -> None:
        # Modal: o resto do app não recebe cliques enquanto o banner está aberto.
        try:
            self.grab_set()
            self.focus_force()
        except tk.TclError:
            if self.winfo_exists():
                self.after(100, self._grab)

    def _safe(self) -> None:
        try:
            if self.winfo_exists():
                self.update_view()
        except tk.TclError:
            pass

    def _metrics(self) -> None:
        if self.vm.note is not None:
            self.vm.content_metrics(*self.html.metrics())

    def update_view(self) -> None:
        vm = self.vm
        note = vm.note
        if note is None:
            return
        color = vm.color()
        self.header.set(color, [
            (vm.eyebrow().upper(), font(self, 10, "bold"), "#ffffff"),
            (f"{(note.get('product') or {}).get('name') or ''}   v{note.get('version')}", font(self, 12), "#ffffff"),
            (note.get("title") or "", font(self, 21, "bold"), "#ffffff"),
            (vm.counter() or "", font(self, 11), "#ffffff"),
        ])
        if note.get("id") != self._note_id:
            self._note_id = note.get("id")
            self.html.render(note.get("description") or "")
            self.html.yview_moveto(0)
            self.after(50, self._metrics)  # conteúdo que não rola libera na hora
        if vm.show_scroll_hint:
            self.hint.configure(text=vm.scroll_hint())
            if not self.hint.winfo_manager():
                self.hint.pack(before=self.button, pady=(10, 0))
        elif self.hint.winfo_manager():
            self.hint.pack_forget()
        self.button.configure(text=vm.button_text())
        self.button.set_colors(color)
        self.button.set_enabled(not vm.ack_disabled and not vm.submitting)
        error = vm.error_text()
        if error:
            self.error.configure(text=error)
            if not self.error.winfo_manager():
                self.error.pack(after=self.button, pady=(0, 12))
        elif self.error.winfo_manager():
            self.error.pack_forget()

    def close(self) -> None:
        self._unsub()
        try:
            self.grab_release()
            self.destroy()
        except tk.TclError:
            pass


class HistoryWindow(tk.Toplevel):
    """Changelog: versão atual destacada, ⚠ nas que exigem ciência. Não marca nada como visto."""

    def __init__(self, master: tk.Misc, vm: HistoryVM, primary: str, brand: str = "") -> None:
        super().__init__(master)
        self.vm, self.primary, self.brand = vm, primary, brand
        t = vm.ctx.t
        self.title(t.rn("historyTitle"))
        self.geometry("420x640")
        self.configure(bg=WHITE)
        self.protocol("WM_DELETE_WINDOW", vm.close)
        self.header = GradientHeader(self, primary, [], on_close=vm.close)
        self.header.pack(fill="x")
        self.scroll = ScrollArea(self, SURFACE)
        self.scroll.pack(fill="both", expand=True)
        self._sig: Any = None
        self._unsub = vm.subscribe(self._safe)
        vm.load()
        self._safe()

    def _safe(self) -> None:
        try:
            if self.winfo_exists():
                self.update_view()
        except tk.TclError:
            pass

    def reopen(self) -> None:
        self.deiconify()
        self.lift()
        self.vm.load()

    def update_view(self) -> None:
        vm, t = self.vm, self.vm.ctx.t
        lines = [(self.brand, font(self, 11), "#ffffff"), (t.rn("historyTitle"), font(self, 17, "bold"), "#ffffff")]
        if vm.current:
            lines.append((f"{t.rn('currentLabel')}: v{vm.current}", font(self, 11), "#ffffff"))
        self.header.set(self.primary, lines)
        rows = vm.notes()
        sig = (vm.loading, repr(rows))
        if sig == self._sig:
            return
        self._sig = sig
        self.scroll.clear()
        inner = self.scroll.inner
        if vm.loading:
            tk.Label(inner, text="…", bg=SURFACE, fg=SUBTLE, font=font(self, 13)).pack(pady=24)
            return
        if not rows:
            tk.Label(inner, text=t.rn("emptyHistory"), bg=SURFACE, fg=SUBTLE, font=font(self, 13)).pack(pady=24)
        for r in rows:
            color = r["color"]
            card = tk.Frame(inner, bg=WHITE, highlightthickness=2 if r["is_current"] else 1,
                            highlightbackground=color if r["is_current"] else BORDER)
            card.pack(fill="x", padx=12, pady=(10, 0))
            top = tk.Frame(card, bg=WHITE)
            top.pack(fill="x", padx=14, pady=(12, 4))
            tk.Label(top, text=r["version"], bg=color if r["is_current"] else "#f1f5f9", fg="#ffffff" if r["is_current"] else SLATE,
                     font=font(self, 11, "mono", "bold"), padx=6).pack(side="left")
            if r["is_current"]:
                tk.Label(top, text=t.rn("currentLabel").upper(), bg=WHITE, fg=color, font=font(self, 10, "bold")).pack(side="left", padx=(6, 0))
            if r["require_ack"]:
                tk.Label(top, text="⚠", bg=WHITE, fg="#b45309", font=font(self, 11)).pack(side="left", padx=(6, 0))
            tk.Label(top, text=r["date"], bg=WHITE, fg=SUBTLE, font=font(self, 10)).pack(side="right")
            tk.Label(card, text=r["title"], bg=WHITE, fg=TEXT, font=font(self, 14, "bold"), anchor="w", justify="left",
                     wraplength=350).pack(fill="x", padx=14, pady=(0, 6))
            if r["expanded"]:
                body = tk.Frame(card, bg=color)
                body.pack(fill="x", padx=14, pady=(6, 8))
                html_widget(body, r["html"], bg=SURFACE, prefer_web=True).pack(fill="x", padx=(3, 0))
            FlatButton(card, t.rn("collapse") if r["expanded"] else t.rn("expand"), lambda nid=r["id"]: vm.toggle(nid),
                       bg="#f1f5f9", fg=SLATE, px=12, padx=10, pady=6).pack(anchor="w", padx=14, pady=(0, 12))

    def destroy(self) -> None:
        self._unsub()
        super().destroy()
