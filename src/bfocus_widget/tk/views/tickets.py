"""Telas de chamados: lista com cartão do chat e splash, novo chamado e detalhe."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Callable, Dict, List, Optional

from ..viewmodels.formatting import attachment_icon, is_html_empty, mix, tint
from ..viewmodels.tickets import PRIORITIES, NewTicketVM, TicketDetailVM, priority_color
from .common import (
    BORDER,
    MUTED,
    RED,
    SLATE,
    SUBTLE,
    SURFACE,
    TEXT,
    WHITE,
    FileCard,
    FlatButton,
    ScrollArea,
    bind_tree,
    chip,
    font,
    link_button,
    status_pill,
)
from .html import HtmlView, RichEditor, html_widget


class Screen(tk.Frame):
    """Tela que acompanha observáveis e cancela a inscrição ao ser destruída."""

    def __init__(self, master: tk.Misc, bg: str = SURFACE) -> None:
        super().__init__(master, bg=bg)
        self._subs: List[Callable[[], None]] = []

    def watch(self, obs: Any) -> None:
        self._subs.append(obs.subscribe(self._safe_update))

    def _safe_update(self) -> None:
        try:
            if self.winfo_exists():
                self.update_view()
        except tk.TclError:
            pass

    def update_view(self) -> None:
        pass

    def destroy(self) -> None:
        for cancel in self._subs:
            cancel()
        self._subs = []
        super().destroy()


class GradientHeader(tk.Canvas):
    """Cabeçalho em gradiente (135° da cor até 62 % com preto, como o web), texto no canvas."""

    def __init__(self, master: tk.Misc, color: str, lines: List[tuple], *, pad: tuple = (18, 16),
                 on_close: Optional[Callable[[], None]] = None) -> None:
        super().__init__(master, highlightthickness=0, bd=0, height=80, bg=color)
        self.color, self.lines, self.pad, self.on_close = color, lines, pad, on_close
        self._h = 0
        self.bind("<Configure>", lambda _e: self._draw())

    def set(self, color: str, lines: List[tuple]) -> None:
        self.color, self.lines = color, lines
        self._draw()

    def _draw(self) -> None:
        w = max(self.winfo_width(), 50)
        self.delete("all")
        end = mix(self.color, "#000000", 0.62)
        h = max(self._h, 60)
        for x in range(0, w + h, 3):
            self.create_line(x, 0, x - h, h, fill=mix(end, self.color, min(1.0, x / (w + h))), width=3)
        px, py = self.pad
        y = py
        text_w = w - 2 * px - (36 if self.on_close else 0)
        for text, fnt, fill in self.lines:
            if not text:
                continue
            item = self.create_text(px, y, anchor="nw", text=text, font=fnt, fill=fill, width=max(80, text_w))
            y = self.bbox(item)[3] + 4
        if self.on_close:
            box = self.create_rectangle(w - px - 30, py - 2, w - px + 2, py + 30, fill=mix("#ffffff", self.color, 0.15), outline="")
            x = self.create_text(w - px - 14, py + 14, text="✕", fill="#ffffff", font=font(self, 14))
            for item in (box, x):
                self.tag_bind(item, "<Button-1>", lambda _e: self.on_close())  # type: ignore[misc]
        new_h = y + py - 4
        if new_h != self._h:
            self._h = new_h
            self.configure(height=new_h)


def back_header(master: tk.Misc, text: str, command: Callable[[], None], primary: str) -> tk.Frame:
    head = tk.Frame(master, bg=WHITE)
    link_button(head, "← " + text, command, primary, WHITE).pack(anchor="w", padx=16, pady=(12, 0))
    return head


def dot(master: tk.Misc, color: str, bg: str, size: int = 8, ring: Optional[str] = None) -> tk.Canvas:
    c = tk.Canvas(master, width=size + 6, height=size + 6, bg=bg, highlightthickness=0, bd=0)
    if ring:
        c.create_oval(0, 0, size + 6, size + 6, fill=ring, outline="")
    c.create_oval(3, 3, size + 3, size + 3, fill=color, outline="")
    return c


# ── lista ────────────────────────────────────────────────────────────────────

class ChatCard(tk.Frame):
    """Cartão do chat no topo da lista (§5.1)."""

    _DOTS = {"live": "#10b981", "busy": "#f59e0b", "off": "#94a3b8"}

    def __init__(self, master: tk.Misc, app: Any) -> None:
        super().__init__(master, bg=WHITE, highlightthickness=1, highlightbackground=BORDER)
        card = app.chat_card()
        t = app.ctx.t
        inner = tk.Frame(self, bg=WHITE)
        inner.pack(fill="x", padx=14, pady=14)
        if card["mode"] == "loading":
            tk.Label(inner, text=t.t("loading"), bg=WHITE, fg=app.primary, font=font(self, 13)).pack()
            return
        top = tk.Frame(inner, bg=WHITE)
        top.pack(fill="x", pady=(0, 4))
        c = self._DOTS.get(card.get("dot"), "#94a3b8")
        dot(top, c, WHITE, ring=tint(c, 0.2) if card.get("dot") != "off" else None).pack(side="left")
        tk.Label(top, text=card["title"], bg=WHITE, fg=TEXT, font=font(self, 14, "bold")).pack(side="left", padx=(6, 0))
        if card.get("unread"):
            tk.Label(top, text=card["unread"], bg=app.primary, fg="#ffffff", font=font(self, 11, "bold"), padx=6).pack(side="right")
        tk.Label(inner, text=card["desc"], bg=WHITE, fg=MUTED, font=font(self, 12), wraplength=320, justify="left", anchor="w").pack(fill="x")
        if card.get("queue"):
            tk.Label(inner, text=card["queue"], bg=WHITE, fg=MUTED, font=font(self, 12, "bold"), anchor="w").pack(fill="x")
        action = {"active": lambda: app.set_view({"name": "chat"}), "online": app.open_chat, "busy": app.open_chat,
                  "offline": lambda: app.set_view({"name": "new"})}.get(card["mode"], lambda: None)
        FlatButton(inner, card["cta"], action, bg=app.primary, hover=mix(app.primary, "#000000", 0.9), pady=11).pack(fill="x", pady=(10, 0))
        if card.get("link"):
            link = FlatButton(inner, card["link"], lambda: app.set_view({"name": "new"}), bg=WHITE, fg=MUTED, bold=False, px=13, pady=4)
            link.pack(pady=(8, 0))


def ticket_row(master: tk.Misc, row: Dict[str, Any], primary: str, on_click: Callable[[], None]) -> tk.Frame:
    unread = row["unread"]
    bg = tint(primary, 0.03) if unread else WHITE
    f = tk.Frame(master, bg=bg, highlightthickness=1, highlightbackground=tint(primary, 0.31) if unread else BORDER, cursor="hand2")
    top = tk.Frame(f, bg=bg)
    top.pack(fill="x", padx=14, pady=(12, 4))
    tk.Label(top, text=row["number"], bg=bg, fg=MUTED, font=font(f, 11, "bold")).pack(side="left")
    if unread:
        dot(top, primary, bg, ring=tint(primary, 0.19, bg)).pack(side="right", padx=(6, 0))
    status_pill(top, row["status_label"], row["status_color"], bg).pack(side="right")
    tk.Label(f, text=row["title"], bg=bg, fg=TEXT, font=font(f, 14, "bold" if unread else "normal"), anchor="w",
             justify="left", wraplength=320).pack(fill="x", padx=14)
    tk.Label(f, text=row["date"], bg=bg, fg=primary if unread else SUBTLE, font=font(f, 11, "bold" if unread else "normal"),
             anchor="w").pack(fill="x", padx=14, pady=(4, 12))
    bind_tree(f, "<Button-1>", lambda _e: on_click())
    return f


class ListScreen(Screen):
    def __init__(self, master: tk.Misc, app: Any) -> None:
        super().__init__(master)
        self.app = app
        self.top = tk.Frame(self, bg=SURFACE)
        self.top.pack(fill="x", padx=16, pady=(16, 8))
        self.scroll = ScrollArea(self, SURFACE)
        self.scroll.pack(fill="both", expand=True, padx=8, pady=(0, 16))
        self._top_sig: Any = None
        self._rows_sig: Any = None
        self.watch(app.tickets)
        if app.chat is not None:
            self.watch(app.chat)
        self.update_view()

    def update_view(self) -> None:
        app, t = self.app, self.app.ctx.t
        top_sig = (app.show_chat_card, repr(app.chat_card()) if app.show_chat_card else None, app.primary)
        if top_sig != self._top_sig:
            self._top_sig = top_sig
            for w in self.top.winfo_children():
                w.destroy()
            if app.show_chat_card:
                ChatCard(self.top, app).pack(fill="x", pady=(0, 12))
                tk.Label(self.top, text=t.t("tab_list"), bg=SURFACE, fg=SLATE, font=font(self, 12, "bold"), anchor="w").pack(fill="x", padx=2)
            else:
                # O botão "Abrir chamado" some quando o cartão do chat aparece.
                FlatButton(self.top, "+ " + t.t("btn_new_ticket"), lambda: app.set_view({"name": "new"}), bg=app.primary,
                           hover=mix(app.primary, "#000000", 0.9), pady=12).pack(fill="x")
        vm = app.tickets
        rows = vm.rows()
        sig = (vm.loading, vm.error, vm.empty, repr(rows), app.primary)
        if sig == self._rows_sig:
            return
        self._rows_sig = sig
        self.scroll.clear()
        inner = self.scroll.inner
        if vm.loading and vm.tickets is None:
            tk.Label(inner, text=t.t("loading"), bg=SURFACE, fg=app.primary, font=font(self, 13)).pack(pady=32)
        if vm.error:
            box = tk.Frame(inner, bg=SURFACE)
            box.pack(pady=24)
            tk.Label(box, text=t.t("error_load"), bg=SURFACE, fg=MUTED, font=font(self, 13)).pack()
            FlatButton(box, "↻", vm.refresh, bg=WHITE, fg=SLATE, border=BORDER, padx=10, pady=4).pack(pady=8)
        if vm.empty:
            box = tk.Frame(inner, bg=SURFACE)
            box.pack(pady=48)
            tk.Label(box, text="💬", bg=SURFACE, font=font(self, 32)).pack()
            tk.Label(box, text=t.t("empty_state_title"), bg=SURFACE, fg=MUTED, font=font(self, 15, "bold")).pack(pady=(8, 4))
            tk.Label(box, text=t.t("empty_state_desc"), bg=SURFACE, fg=MUTED, font=font(self, 13)).pack()
        for row in rows:
            ticket_row(inner, row, app.primary, lambda rid=row["id"]: app.select_ticket(rid)).pack(fill="x", padx=8, pady=6)


class SplashOverlay(Screen):
    """Novidades por usuário, por cima da lista (§4): a que exige ciência trava (sem pular,
    uma por vez); as demais são cartões dispensáveis."""

    def __init__(self, master: tk.Misc, app: Any) -> None:
        super().__init__(master, bg=WHITE)
        self.app = app
        self._mode: Optional[str] = None
        self._body: Any = None
        self.watch(app.splash)
        self.update_view()

    def update_view(self) -> None:
        mode = self.app.splash.mode
        if mode != self._mode:
            if self._body is not None:
                self._body.destroy()
            self._mode, self._body = mode, None
            if mode == "gate":
                self._body = AckGate(self, self.app)
            elif mode == "cards":
                self._body = SplashCards(self, self.app)
            if self._body is not None:
                self._body.pack(fill="both", expand=True)
        if self._body is not None:
            self._body.update_view()


class AckGate(tk.Frame):
    """Ciência exigida: o espelho, dentro do widget, do gate interno do bFocus. Sem pular."""

    def __init__(self, master: tk.Misc, app: Any) -> None:
        super().__init__(master, bg=WHITE)
        self.app, self.vm = app, app.splash
        self.header = GradientHeader(self, app.primary, [])
        self.header.pack(fill="x")
        foot = tk.Frame(self, bg=WHITE, highlightthickness=1, highlightbackground=BORDER)
        foot.pack(side="bottom", fill="x")
        # A dica de rolagem fica no rodapé, acima do botão (como o web).
        self.hint = tk.Label(foot, text="", bg=WHITE, fg=MUTED, font=font(self, 11, "bold"), wraplength=340)
        self.button = FlatButton(foot, "", self.vm.acknowledge, bg=app.primary, px=13, pady=12, wraplength=340)
        self.button.pack(fill="x", padx=16, pady=12)
        self.error = tk.Label(foot, text="", bg=WHITE, fg="#b91c1c", font=font(self, 11), wraplength=340, justify="center")
        body = tk.Frame(self, bg=WHITE)
        body.pack(fill="both", expand=True)
        sb = tk.Scrollbar(body, orient="vertical")
        sb.pack(side="right", fill="y")
        self.html = HtmlView(body, "", bg=WHITE, fg="#334155", px=13, auto_height=False)
        self.html.pack(side="left", fill="both", expand=True, padx=(18, 8), pady=14)
        self.html.configure(yscrollcommand=lambda a, b: (sb.set(a, b), self._metrics()))
        sb.configure(command=self.html.yview)
        self._note_id: Optional[str] = None

    def _metrics(self) -> None:
        try:
            if self.winfo_exists() and self.vm.gate_note is not None:
                self.vm.content_metrics(*self.html.metrics())
        except tk.TclError:
            pass  # a tela saiu antes do "depois"

    def update_view(self) -> None:
        vm = self.vm
        note = vm.gate_note
        if note is None:
            return
        color = vm.color(note)
        product = (note.get("product") or {}).get("name") or ""
        self.header.set(color, [
            (vm.gate_eyebrow().upper(), font(self, 10, "bold"), "#ffffff"),
            (f"{product}   v{note.get('version')}".strip(), font(self, 12), "#ffffff"),
            (note.get("title") or "", font(self, 17, "bold"), "#ffffff"),
            (vm.gate_counter() or "", font(self, 11), "#ffffff"),
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
        self.button.configure(text=vm.ack_button_text())
        self.button.set_colors(color)
        self.button.set_enabled(not vm.ack_disabled)
        error = vm.ack_error_text()
        if error:
            self.error.configure(text=error)
            if not self.error.winfo_manager():
                self.error.pack(after=self.button, pady=(0, 10))
        elif self.error.winfo_manager():
            self.error.pack_forget()


class SplashCards(tk.Frame):
    """Novidades que não exigem ciência: "marcar como lida" ou "Pular por agora"."""

    def __init__(self, master: tk.Misc, app: Any) -> None:
        super().__init__(master, bg=WHITE)
        self.app = app
        t = app.ctx.t
        self.header = GradientHeader(self, app.primary, [])
        self.header.pack(fill="x")
        foot = tk.Frame(self, bg=WHITE, highlightthickness=1, highlightbackground=BORDER)
        foot.pack(side="bottom", fill="x")
        FlatButton(foot, t.t("splash_skip"), app.dismiss_splash, bg=WHITE, fg=MUTED, bold=False, px=12, pady=8,
                   wraplength=340).pack(fill="x", padx=16, pady=4)
        self.scroll = ScrollArea(self, SURFACE)
        self.scroll.pack(fill="both", expand=True)
        self._sig: Any = None

    def update_view(self) -> None:
        app, vm, t = self.app, self.app.splash, self.app.ctx.t
        self.header.set(app.primary, [
            ("✨  " + t.t("splash_eyebrow").upper(), font(self, 10, "bold"), "#ffffff"),
            (t.t("splash_title"), font(self, 17, "bold"), "#ffffff"),
            (vm.count_text(), font(self, 12), "#ffffff"),
        ])
        cards = vm.cards
        sig = (repr(cards), vm.expanded, vm.marking)
        if sig == self._sig:
            return
        self._sig = sig
        self.scroll.clear()
        for note in cards:
            color = vm.color(note)
            card = tk.Frame(self.scroll.inner, bg=WHITE, highlightthickness=1, highlightbackground=BORDER)
            card.pack(fill="x", padx=12, pady=(10, 0))
            meta = tk.Frame(card, bg=WHITE)
            meta.pack(fill="x", padx=14, pady=(12, 4))
            dot(meta, color, WHITE).pack(side="left")
            tk.Label(meta, text=(note.get("product") or {}).get("name") or "", bg=WHITE, fg=MUTED, font=font(self, 10)).pack(side="left")
            tk.Label(meta, text=" • ", bg=WHITE, fg=SUBTLE, font=font(self, 10)).pack(side="left")
            tk.Label(meta, text=f"v{note.get('version')}", bg="#f1f5f9", fg=SLATE, font=font(self, 10, "mono"), padx=6).pack(side="left")
            tk.Label(card, text=note.get("title") or "", bg=WHITE, fg=TEXT, font=font(self, 14, "bold"), anchor="w",
                     justify="left", wraplength=330).pack(fill="x", padx=14, pady=(0, 8))
            expanded = vm.expanded == note.get("id")
            if expanded:
                body = tk.Frame(card, bg=color)
                body.pack(fill="x", padx=14, pady=(0, 8))
                html_widget(body, note.get("description") or "", bg=SURFACE, prefer_web=True).pack(fill="x", padx=(3, 0))
            btns = tk.Frame(card, bg=WHITE)
            btns.pack(fill="x", padx=14, pady=(4, 12))
            FlatButton(btns, t.t("splash_collapse") if expanded else t.t("splash_expand"),
                       lambda nid=note.get("id"): vm.toggle(nid), bg="#f1f5f9", fg=SLATE, px=12, pady=7).pack(side="left", fill="x", expand=True)
            mark = FlatButton(btns, t.t("splash_mark_read"), lambda nid=note.get("id"): vm.mark_read(nid), bg=color, px=12, pady=7)
            mark.pack(side="left", fill="x", expand=True, padx=(6, 0))
            mark.set_enabled(not vm.marking)


# ── novo chamado ─────────────────────────────────────────────────────────────

def section_label(master: tk.Misc, text: str) -> tk.Label:
    return tk.Label(master, text=text, bg=SURFACE, fg=SLATE, font=font(master, 12, "bold"), anchor="w")


class NewTicketScreen(Screen):
    def __init__(self, master: tk.Misc, app: Any, vm: NewTicketVM) -> None:
        super().__init__(master)
        self.app, self.vm = app, vm
        t, primary = app.ctx.t, app.primary
        back_header(self, t.t("btn_back"), lambda: app.set_view({"name": "list"}), primary).pack(fill="x", ipady=6)
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")
        foot = tk.Frame(self, bg=WHITE)
        foot.pack(side="bottom", fill="x")
        tk.Frame(self, bg=BORDER, height=1).pack(side="bottom", fill="x")
        self.create = FlatButton(foot, t.t("btn_create"), vm.submit, bg=primary, pady=12)
        self.create.pack(fill="x", padx=12, pady=(12, 4))
        self.error = tk.Label(foot, text="", bg=WHITE, fg=RED, font=font(self, 12))
        self.error.pack(pady=(0, 8))
        scroll = ScrollArea(self, SURFACE)
        scroll.pack(fill="both", expand=True)
        body = tk.Frame(scroll.inner, bg=SURFACE)
        body.pack(fill="x", padx=16, pady=16)
        if vm.notice:
            tk.Label(body, text=vm.notice, bg="#fffbeb", fg="#92400e", font=font(self, 12), wraplength=330, justify="left",
                     highlightthickness=1, highlightbackground="#fde68a", padx=12, pady=10, anchor="w").pack(fill="x", pady=(0, 14))
        section_label(body, t.t("new_step_title")).pack(fill="x", pady=(4, 6))
        types = tk.Frame(body, bg=SURFACE)
        types.pack(fill="x", pady=(0, 16))
        self.type_chips = {}
        for i, tp in enumerate(vm.types):
            b = FlatButton(types, t.t(f"type_{tp}"), lambda v=tp: vm.set_type(v), bg=WHITE, fg=SLATE, border="#cbd5e1", px=12, padx=14, pady=6)
            b.grid(row=i // 3, column=i % 3, padx=(0, 6), pady=(0, 6), sticky="w")
            self.type_chips[tp] = b
        section_label(body, t.t("new_step_priority")).pack(fill="x", pady=(4, 6))
        prios = tk.Frame(body, bg=SURFACE)
        prios.pack(fill="x", pady=(0, 16))
        self.prio_chips = {}
        for p in PRIORITIES:
            b = FlatButton(prios, t.t(f"priority_{p}"), lambda v=p: vm.set_priority(v), bg=WHITE, fg=SLATE, border="#cbd5e1", px=12, padx=12, pady=6)
            b.pack(side="left", padx=(0, 6))
            self.prio_chips[p] = b
        if vm.show_departments:
            section_label(body, t.t("new_field_department")).pack(fill="x", pady=(4, 0))
            tk.Label(body, text=t.t("new_field_department_hint"), bg=SURFACE, fg=MUTED, font=font(self, 11), anchor="w").pack(fill="x", pady=(0, 6))
            names = [t.t("new_field_department_any")] + [d.get("name") or "" for d in vm.departments]
            ids = [""] + [str(d.get("id")) for d in vm.departments]
            combo = ttk.Combobox(body, values=names, state="readonly")
            combo.current(ids.index(vm.department_id) if vm.department_id in ids else 0)
            combo.bind("<<ComboboxSelected>>", lambda _e: vm.set_department(ids[combo.current()]))
            combo.pack(fill="x", pady=(0, 16))
        section_label(body, t.t("new_field_title")).pack(fill="x", pady=(4, 6))
        self.title_var = tk.StringVar(value=vm.title)
        self.title_var.trace_add("write", lambda *_: self._title_changed())
        entry = tk.Entry(body, textvariable=self.title_var, font=font(self, 14), relief="flat", highlightthickness=1,
                         highlightbackground="#cbd5e1", highlightcolor=primary, bg=WHITE, fg=TEXT, insertbackground=TEXT)
        entry.pack(fill="x", ipady=8, pady=(0, 12))
        section_label(body, t.t("new_field_description")).pack(fill="x", pady=(4, 6))
        RichEditor(body, t, placeholder=t.t("new_field_description_placeholder"), height=6, primary=primary,
                   on_change=vm.set_description, on_paste_image=vm.upload_inline_image,
                   initial_html=vm.description).pack(fill="x", pady=(0, 12))
        section_label(body, t.t("btn_attach")).pack(fill="x", pady=(4, 6))
        self.uploads = tk.Frame(body, bg=SURFACE)
        self.uploads.pack(fill="x")
        self.attach = FlatButton(body, "", self._pick, bg=WHITE, fg=SLATE, border="#cbd5e1", bold=False, px=13, pady=12)
        self.attach.pack(fill="x", pady=(6, 0))
        self.upload_error = tk.Label(body, text="", bg=SURFACE, fg=RED, font=font(self, 12), anchor="w", wraplength=330)
        self.upload_error.pack(fill="x", pady=(6, 0))
        self._uploads_sig: Any = None
        self.watch(vm)
        self.update_view()

    def _title_changed(self) -> None:
        value = self.title_var.get()
        if len(value) > 500:
            self.title_var.set(value[:500])  # até 500 caracteres
            return
        self.vm.set_title(value)

    def _pick(self) -> None:
        paths = filedialog.askopenfilenames(parent=self)
        if paths:
            self.vm.add_files(list(paths))

    def update_view(self) -> None:
        vm, t, primary = self.vm, self.app.ctx.t, self.app.primary
        for tp, b in self.type_chips.items():
            sel = vm.type == tp
            b.set_colors(primary if sel else WHITE, "#ffffff" if sel else SLATE)
            b.configure(highlightbackground=primary if sel else "#cbd5e1")
        for p, b in self.prio_chips.items():
            sel, c = vm.priority == p, priority_color(p)
            b.set_colors(c if sel else WHITE, "#ffffff" if sel else SLATE)
            b.configure(highlightbackground=c if sel else "#cbd5e1")
        sig = repr(vm.uploads)
        if sig != self._uploads_sig:
            self._uploads_sig = sig
            for w in self.uploads.winfo_children():
                w.destroy()
            for i, f in enumerate(vm.uploads):
                chip(self.uploads, f.get("filename") or "", lambda i=i: vm.remove_upload(i), attachment_icon(f.get("content_type"))).pack(fill="x", pady=(0, 6))
        self.attach.configure(text=("⏳ " + t.t("attach_uploading")) if vm.uploading else ("📎 " + t.t("attach_drag_hint")))
        self.attach.set_enabled(not vm.uploading)
        self.upload_error.configure(text=vm.upload_error or "")
        self.create.configure(text=("… " if vm.submitting else "") + t.t("btn_create"))
        self.create.set_enabled(vm.can_submit)
        self.error.configure(text=t.t("error_generic") if vm.error else "")


# ── detalhe ──────────────────────────────────────────────────────────────────

def detail_bubble(master: tk.Misc, b: Dict[str, Any], primary: str, on_download: Callable[[Dict[str, Any]], None],
                  runner: Any, api: Any) -> tk.Frame:
    staff = b["is_staff"]
    row = tk.Frame(master, bg=SURFACE)
    stack = tk.Frame(row, bg=SURFACE)
    stack.pack(side="left" if staff else "right", fill="x", expand=True, padx=(0, 40) if staff else (40, 0))
    if not is_html_empty(b["html"]):
        box_bg = WHITE if staff else primary
        box = tk.Frame(stack, bg=box_bg, highlightthickness=1 if staff else 0, highlightbackground=BORDER)
        box.pack(fill="x")
        HtmlView(box, b["html"], bg=box_bg, fg=TEXT if staff else "#ffffff", light=not staff).pack(fill="x", padx=12, pady=8)
    for a in b.get("attachments") or []:
        FileCard(stack, a, primary, on_download, runner, api).pack(fill="x", pady=(4, 0))
    tk.Label(stack, text=f"{b['author']} · {b['time']}", bg=SURFACE, fg=SUBTLE, font=font(master, 10)).pack(anchor="w" if staff else "e", pady=(2, 0))
    return row


class DetailScreen(Screen):
    def __init__(self, master: tk.Misc, app: Any, vm: TicketDetailVM) -> None:
        super().__init__(master)
        self.app, self.vm = app, vm
        t, primary = app.ctx.t, app.primary
        head = back_header(self, t.t("btn_back"), lambda: app.set_view({"name": "list"}), primary)
        head.pack(fill="x")
        self.head_row = tk.Frame(head, bg=WHITE)
        self.head_row.pack(fill="x", padx=16, pady=(6, 12))
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")
        self.foot = tk.Frame(self, bg=WHITE)
        self.foot.pack(side="bottom", fill="x")
        tk.Frame(self, bg=BORDER, height=1).pack(side="bottom", fill="x")
        self.body = ScrollArea(self, SURFACE)
        self.body.pack(fill="both", expand=True)
        self._head_sig: Any = None
        self._body_sig: Any = None
        self._foot_mode: Optional[str] = None
        self._draft_version = vm.draft_version
        self.editor: Optional[RichEditor] = None
        self.watch(vm)
        self.update_view()

    def update_view(self) -> None:
        vm, app, t = self.vm, self.app, self.app.ctx.t
        primary = app.primary
        tk_ = vm.ticket
        head_sig = (tk_.get("ticket_number"), tk_.get("title"), vm.status, repr(vm.previous))
        if head_sig != self._head_sig:
            self._head_sig = head_sig
            for w in self.head_row.winfo_children():
                w.destroy()
            if vm.data is not None:
                left = tk.Frame(self.head_row, bg=WHITE)
                left.pack(side="left", fill="x", expand=True)
                tk.Label(left, text=tk_.get("ticket_number") or "", bg=WHITE, fg=MUTED, font=font(self, 11, "bold"), anchor="w").pack(fill="x")
                tk.Label(left, text=tk_.get("title") or "", bg=WHITE, fg=TEXT, font=font(self, 15, "bold"), anchor="w",
                         justify="left", wraplength=260).pack(fill="x")
                if vm.previous:
                    link_button(left, "↳ " + vm.continuation_text(),
                                lambda pid=str(vm.previous.get("id")): app.set_view({"name": "detail", "id": pid}),
                                primary, WHITE, px=12).pack(anchor="w", pady=(4, 0))
                status_pill(self.head_row, vm.status_label, vm.status_color).pack(side="right", anchor="n", padx=(8, 0))
        body_sig = (vm.loading, vm.error, repr(vm.data), repr(vm.conversations), tuple(sorted(vm.expanded)), vm.conv_error)
        if body_sig != self._body_sig:
            self._body_sig = body_sig
            self._render_body()
        self._render_foot()

    def _download(self, att: Dict[str, Any]) -> None:
        self.vm.download(att)

    def _render_body(self) -> None:
        vm, app, t = self.vm, self.app, self.app.ctx.t
        self.body.clear()
        inner = tk.Frame(self.body.inner, bg=SURFACE)
        inner.pack(fill="x", padx=12, pady=12)
        runner, api = app.ctx.runner, app.ctx.api
        if vm.loading and vm.data is None:
            tk.Label(inner, text=t.t("loading"), bg=SURFACE, fg=app.primary, font=font(self, 13)).pack(pady=48)
            return
        if vm.error and vm.data is None:
            tk.Label(inner, text=t.t("error_load"), bg=SURFACE, fg=MUTED, font=font(self, 13)).pack(pady=(24, 8))
            link_button(inner, "← " + t.t("btn_back"), lambda: app.set_view({"name": "list"}), app.primary, SURFACE).pack()
            return
        bubbles = vm.bubbles()
        # A descrição inicial é a 1ª bolha; a conversa do chat vem logo depois dela.
        if bubbles and (vm.ticket.get("description") or (vm.data or {}).get("attachments")):
            detail_bubble(inner, bubbles[0], app.primary, self._download, runner, api).pack(fill="x", pady=(0, 8))
            bubbles = bubbles[1:]
        if vm.has_chat:
            tk.Label(inner, text=t.t("detail_chat_title").upper(), bg=SURFACE, fg=MUTED, font=font(self, 11, "bold"), anchor="w").pack(fill="x", pady=(12, 8))
            for sec in vm.chat_sections():
                self._chat_section(inner, sec)
        if vm.conv_error:
            row = tk.Frame(inner, bg=SURFACE)
            row.pack(pady=(4, 12))
            tk.Label(row, text=t.t("detail_chat_error"), bg=SURFACE, fg=MUTED, font=font(self, 12)).pack(side="left")
            link_button(row, " " + t.t("btn_retry"), vm.refresh_conversations, app.primary, SURFACE, px=12).pack(side="left")
        if vm.show_interactions_title:
            tk.Label(inner, text=t.t("detail_interactions_title").upper(), bg=SURFACE, fg=MUTED, font=font(self, 11, "bold"), anchor="w").pack(fill="x", pady=(12, 8))
        if vm.no_messages:
            tk.Label(inner, text=t.t("detail_no_messages"), bg=SURFACE, fg=SUBTLE, font=font(self, 13)).pack(pady=24)
        for b in bubbles:
            detail_bubble(inner, b, app.primary, self._download, runner, api).pack(fill="x", pady=(0, 8))

    def _chat_section(self, master: tk.Misc, sec: Dict[str, Any]) -> None:
        app, vm, t = self.app, self.vm, self.app.ctx.t
        box = tk.Frame(master, bg=WHITE, highlightthickness=1, highlightbackground=BORDER)
        box.pack(fill="x", pady=(0, 8))
        head = tk.Frame(box, bg=WHITE, cursor="hand2")
        head.pack(fill="x", padx=12, pady=10)
        tk.Label(head, text="💬", bg=tint(app.primary, 0.10), font=font(self, 15), width=2).pack(side="left")
        txt = tk.Frame(head, bg=WHITE)
        txt.pack(side="left", fill="x", expand=True, padx=(10, 0))
        tk.Label(txt, text=sec["title"], bg=WHITE, fg=TEXT, font=font(self, 13, "bold"), anchor="w").pack(fill="x")
        tk.Label(txt, text=sec["subtitle"], bg=WHITE, fg=MUTED, font=font(self, 11), anchor="w").pack(fill="x")
        tk.Label(head, text="▲" if sec["expanded"] else "▼", bg=WHITE, fg=MUTED, font=font(self, 12)).pack(side="right")
        bind_tree(head, "<Button-1>", lambda _e, cid=sec["id"]: vm.toggle_conversation(cid))
        if not sec["expanded"]:
            return
        panel = tk.Frame(box, bg=SURFACE)
        panel.pack(fill="x")
        if not sec["rows"]:
            tk.Label(panel, text=t.t("detail_chat_empty"), bg=SURFACE, fg=SUBTLE, font=font(self, 12)).pack(pady=(4, 10))
        for r in sec["rows"]:
            if r["kind"] == "system":
                sys_box = tk.Frame(panel, bg="#f1f5f9")
                sys_box.pack(padx=24, pady=(4, 8), fill="x")
                HtmlView(sys_box, r["html"], bg="#f1f5f9", fg=MUTED, px=12).pack(fill="x", padx=10, pady=6)
            else:
                detail_bubble(panel, r, app.primary, self._download, app.ctx.runner, app.ctx.api).pack(fill="x", padx=10, pady=(4, 4))

    def _render_foot(self) -> None:
        vm, t, primary = self.vm, self.app.ctx.t, self.app.primary
        mode = None if vm.data is None else ("closed" if vm.is_closed else "reply")
        if mode != self._foot_mode:
            self._foot_mode = mode
            for w in self.foot.winfo_children():
                w.destroy()
            self.editor = None
            if mode == "closed":
                # Fechado/cancelado: sem resposta, com o aviso.
                tk.Label(self.foot, text=t.t("detail_closed_notice"), bg=SURFACE, fg=SLATE, font=font(self, 12), wraplength=360,
                         justify="center", padx=16, pady=12).pack(fill="x")
            elif mode == "reply":
                self.hint = tk.Label(self.foot, text="", bg="#ecfdf5", fg="#065f46", font=font(self, 12), wraplength=350, justify="left",
                                     anchor="w", highlightthickness=1, highlightbackground="#a7f3d0", padx=10, pady=6)
                self.pending = tk.Frame(self.foot, bg=WHITE)
                self.pending.pack(fill="x", padx=12, pady=(12, 0))
                self.editor = RichEditor(self.foot, t, placeholder=t.t("detail_send_placeholder"), height=3, primary=primary,
                                         on_change=self._draft, on_paste_image=vm.upload_inline_image)
                self.editor.pack(fill="x", padx=12, pady=8)
                row = tk.Frame(self.foot, bg=WHITE)
                row.pack(fill="x", padx=12, pady=(0, 8))
                self.attach = FlatButton(row, "📎", self._pick, bg=WHITE, fg=MUTED, border="#cbd5e1", padx=8, pady=4, bold=False, px=16)
                self.attach.pack(side="left")
                self.send = FlatButton(row, t.t("btn_send"), vm.send, bg=primary, px=13, padx=16, pady=7)
                self.send.pack(side="right")
                self.send_error = tk.Label(self.foot, text="", bg=WHITE, fg=RED, font=font(self, 12), anchor="w")
                self.send_error.pack(fill="x", padx=12, pady=(0, 6))
        if mode != "reply":
            return
        if vm.is_resolved:
            self.hint.configure(text=vm.reopen_hint())
            if not self.hint.winfo_manager():  # janela escondida: ismapped mente
                self.hint.pack(fill="x", padx=12, pady=(12, 0), before=self.pending)
        elif self.hint.winfo_manager():
            self.hint.pack_forget()
        for w in self.pending.winfo_children():
            w.destroy()
        for i, f in enumerate(vm.pending):
            chip(self.pending, f.get("filename") or "", lambda i=i: vm.remove_pending(i), attachment_icon(f.get("content_type"))).pack(side="left", padx=(0, 6))
        if vm.draft_version != self._draft_version and self.editor is not None:
            self._draft_version = vm.draft_version
            self.editor.clear()
        self.attach.configure(text="⏳" if vm.uploading else "📎")
        self.attach.set_enabled(not vm.uploading)
        self.send.configure(text="…" if vm.sending else t.t("btn_send"))
        self.send.set_enabled(vm.can_send)
        self.send_error.configure(text=t.t("error_generic") if vm.send_error else "")

    def _draft(self, html: str) -> None:
        self.vm.set_draft(html)
        if self._foot_mode == "reply":
            self.send.set_enabled(self.vm.can_send)

    def _pick(self) -> None:
        path = filedialog.askopenfilename(parent=self)  # um anexo por vez
        if path:
            self.vm.attach(path)
