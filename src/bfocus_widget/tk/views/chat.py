"""Tela do chat ao vivo (§5.2): cabeçalho, faixa do assistente, mensagens, composer, fim."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog
from typing import Any, Dict, Optional

from ..viewmodels.chat import ChatScreenVM, ComposerVM, CsatVM
from ..viewmodels.formatting import attachment_icon, is_html_empty, mix, tint
from .common import (
    AMBER,
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
    chip,
    font,
    link_button,
    load_image,
)
from .html import HtmlView
from .tickets import Screen, back_header, dot


def avatar_widget(master: tk.Misc, spec: Any, primary: str, runner: Any, api: Any, size: int = 24) -> tk.Widget:
    """Atendente: foto ou iniciais; assistente: a imagem dele ou um robô."""
    bg = master.cget("bg")
    if spec is None or spec[0] == "space":
        return tk.Frame(master, width=size, height=size, bg=bg)
    soft = tint(primary, 0.14)
    if spec[0] == "bot":
        lbl = tk.Label(master, text="🤖", bg=soft, font=font(master, max(10, size // 2)), width=2)
        load_image(lbl, runner, api, spec[1], size)
        return lbl
    lbl = tk.Label(master, text=spec[2] if len(spec) > 2 else "?", bg=soft, fg=primary, font=font(master, max(9, size // 2 - 2), "bold"), width=2)
    load_image(lbl, runner, api, spec[1], size)
    return lbl


class CsatWidget(Screen):
    def __init__(self, master: tk.Misc, vm: CsatVM, primary: str) -> None:
        super().__init__(master, bg=SURFACE)
        self.vm, self.primary = vm, primary
        self.configure(highlightthickness=1, highlightbackground=BORDER)
        self._hover = 0
        self._built_state: Optional[str] = None
        self.watch(vm)
        self.update_view()

    def update_view(self) -> None:
        vm, t = self.vm, self.vm.ctx.t
        group = vm.state if vm.state in ("done", "not_resolved") else ("form" if vm.rating else "stars")
        if group != self._built_state:
            self._built_state = group
            for w in self.winfo_children():
                w.destroy()
            if vm.state == "done":
                tk.Label(self, text=t.t("csat_thanks"), bg=SURFACE, fg=TEXT, font=font(self, 13, "bold")).pack(pady=10)
                return
            if vm.state == "not_resolved":
                tk.Label(self, text=t.t("csat_not_resolved"), bg=SURFACE, fg=SLATE, font=font(self, 13), wraplength=330).pack(padx=10, pady=10)
                return
            tk.Label(self, text=t.t("csat_title"), bg=SURFACE, fg=TEXT, font=font(self, 13, "bold")).pack(pady=(10, 6))
            stars = tk.Frame(self, bg=SURFACE)
            stars.pack()
            self.stars = []
            for n in range(1, 6):
                s = tk.Label(stars, text="★", bg=SURFACE, font=font(self, 26), cursor="hand2")
                s.pack(side="left", padx=2)
                s.bind("<Button-1>", lambda _e, n=n: vm.set_rating(n))
                s.bind("<Enter>", lambda _e, n=n: self._set_hover(n))
                self.stars.append(s)
            stars.bind("<Leave>", lambda _e: self._set_hover(0))
            if group == "form":
                self.comment = tk.Text(self, height=2, wrap="word", font=font(self, 13), highlightthickness=1, highlightbackground="#cbd5e1", bd=0)
                self.comment.insert("1.0", vm.comment)
                self.comment.bind("<KeyRelease>", lambda _e: vm.set_comment(self.comment.get("1.0", "end-1c")))
                self.comment.pack(fill="x", padx=10, pady=(8, 0))
                self.err = tk.Label(self, text="", bg=SURFACE, fg="#b91c1c", font=font(self, 12), wraplength=330, justify="left")
                self.err.pack(fill="x", padx=10, pady=(6, 0))
                self.submit = FlatButton(self, "", vm.submit, bg=self.primary, px=13, pady=10)
                self.submit.pack(fill="x", padx=10, pady=(8, 10))
            else:
                tk.Frame(self, bg=SURFACE, height=10).pack()
        if vm.state in ("done", "not_resolved"):
            return
        self._paint_stars()
        if group == "form":
            self.err.configure(text=vm.error_msg if vm.state == "error" else "")
            self.submit.configure(text=vm.button_text())
            self.submit.set_enabled(vm.state != "sending")
            self.comment.configure(state="disabled" if vm.state == "sending" else "normal")

    def _set_hover(self, n: int) -> None:
        self._hover = n
        self._paint_stars()

    def _paint_stars(self) -> None:
        shown = self._hover or self.vm.rating
        for i, s in enumerate(getattr(self, "stars", []), 1):
            try:
                s.configure(fg=AMBER if i <= shown else "#cbd5e1")
            except tk.TclError:
                pass


class Composer(tk.Frame):
    """Enter envia; Shift+Enter quebra a linha. Anexos pelo seletor (até 10)."""

    def __init__(self, master: tk.Misc, vm: ComposerVM, primary: str, on_send: Any) -> None:
        super().__init__(master, bg=WHITE)
        self.vm, self.on_send = vm, on_send
        t = vm.ctx.t
        self.disabled = False
        self._cleared = vm.cleared
        self.files = tk.Frame(self, bg=WHITE)
        self.files.pack(fill="x")
        row = tk.Frame(self, bg=WHITE)
        row.pack(fill="x")
        self.attach = FlatButton(row, "📎", self._pick, bg=WHITE, fg=MUTED, border="#cbd5e1", padx=8, pady=4, bold=False, px=16)
        self.attach.pack(side="left", anchor="s")
        self.send_btn = FlatButton(row, "➤", self._send, bg=primary, padx=10, pady=6, px=14)
        self.send_btn.pack(side="right", anchor="s")
        self.text = tk.Text(row, height=1, wrap="word", font=font(self, 14), highlightthickness=1, highlightbackground="#cbd5e1",
                            highlightcolor=primary, bd=0, padx=10, pady=6, fg=TEXT, bg=WHITE, insertbackground=TEXT)
        self.text.pack(side="left", fill="x", expand=True, padx=8)
        self._placeholder = t.t("chat_placeholder")
        self._ph = False
        self._show_ph()
        self.text.bind("<Return>", self._enter)
        self.text.bind("<Shift-Return>", self._newline)
        self.text.bind("<KeyRelease>", lambda _e: self._typed())
        self.text.bind("<FocusIn>", lambda _e: self._hide_ph())
        self.text.bind("<FocusOut>", lambda _e: self._show_ph())
        self.error = tk.Label(self, text="", bg=WHITE, fg=RED, font=font(self, 12), anchor="w")
        self.error.pack(fill="x")

    def _show_ph(self) -> None:
        if not self._ph and not self.text.get("1.0", "end-1c"):
            self._ph = True
            self.text.insert("1.0", self._placeholder)
            self.text.configure(fg=SUBTLE)

    def _hide_ph(self) -> None:
        if self._ph:
            self._ph = False
            self.text.delete("1.0", "end")
            self.text.configure(fg=TEXT)

    def _typed(self) -> None:
        if self._ph:
            return
        self.vm.set_text(self.text.get("1.0", "end-1c"))
        lines = int(self.text.index("end-1c").split(".")[0])
        self.text.configure(height=max(1, min(5, lines)))  # cresce até ~120 px
        self.send_btn.set_enabled(self.vm.can_send(self.disabled))

    def _enter(self, _e: Any) -> str:
        self._send()
        return "break"

    def _newline(self, _e: Any) -> str:
        self.text.insert("insert", "\n")
        self._typed()
        return "break"

    def _send(self) -> None:
        if not self._ph:
            self.vm.set_text(self.text.get("1.0", "end-1c"))
        self.on_send()

    def _pick(self) -> None:
        paths = filedialog.askopenfilenames(parent=self)
        if paths:
            self.vm.add_files(list(paths))

    def update_view(self, disabled: bool) -> None:
        vm = self.vm
        self.disabled = disabled
        for w in self.files.winfo_children():
            w.destroy()
        for i, f in enumerate(vm.files):
            chip(self.files, f.get("filename") or "", lambda i=i: vm.remove(i), attachment_icon(f.get("content_type"))).pack(side="left", padx=(0, 6), pady=(0, 8))
        if vm.cleared != self._cleared:
            self._cleared = vm.cleared
            self.text.delete("1.0", "end")
            self._ph = False
            self.text.configure(height=1, fg=TEXT)
            if self.focus_get() is not self.text:
                self._show_ph()
        self.attach.configure(text="⏳" if vm.uploading else "📎")
        self.text.configure(state="disabled" if disabled else "normal")
        self.send_btn.configure(text="…" if vm.busy else "➤")
        self.send_btn.set_enabled(vm.can_send(disabled))
        self.error.configure(text=vm.upload_error or "")


class ChatScreen(Screen):
    def __init__(self, master: tk.Misc, app: Any, vm: ChatScreenVM) -> None:
        super().__init__(master)
        self.app, self.vm = app, vm
        self.head = tk.Frame(self, bg=WHITE)
        self.head.pack(fill="x")
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")
        self.strip = tk.Frame(self, bg=SURFACE)
        self.strip.pack(fill="x")
        self.foot = tk.Frame(self, bg=WHITE)
        self.foot.pack(side="bottom", fill="x")
        tk.Frame(self, bg=BORDER, height=1).pack(side="bottom", fill="x")
        self.body = ScrollArea(self, SURFACE)
        self.body.pack(fill="both", expand=True)
        self.composer = Composer(self.foot, vm.composer, app.primary, vm.send)
        self.terminal: Optional[tk.Frame] = None
        self._sigs: Dict[str, Any] = {}
        self.watch(vm)
        self.update_view()

    def _changed(self, key: str, sig: Any) -> bool:
        if self._sigs.get(key) == sig:
            return False
        self._sigs[key] = sig
        return True

    def update_view(self) -> None:
        vm, app, t = self.vm, self.app, self.app.ctx.t
        conv = vm.conversation
        session = vm.session
        if self._changed("head", (repr(conv), vm.confirm_end, vm.ending, repr(vm.draft))):
            self._render_head()
        if self._changed("strip", (vm.with_bot, vm.handoff, vm.connection_banner())):
            for w in self.strip.winfo_children():
                w.destroy()
            if vm.with_bot:
                box = tk.Frame(self.strip, bg="#f0f9ff", highlightthickness=1, highlightbackground="#bae6fd")
                box.pack(fill="x")
                tk.Label(box, text=vm.handoff_text(), bg="#f0f9ff", fg="#075985", font=font(self, 12), wraplength=230, justify="left",
                         anchor="w").pack(side="left", fill="x", expand=True, padx=(16, 8), pady=8)
                b = FlatButton(box, "…" if vm.handoff == "sending" else t.t("btn_talk_to_human"), vm.ask_human, bg=WHITE,
                               fg=app.primary, border=app.primary, px=12, padx=12, pady=7)
                b.pack(side="right", padx=12, pady=8)
                b.set_enabled(vm.handoff != "sending")
            banner = vm.connection_banner()
            if banner:
                tk.Label(self.strip, text=banner, bg="#fffbeb", fg="#92400e", font=font(self, 12), wraplength=360, pady=6).pack(fill="x")
        rows = vm.rows()
        body_sig = (repr(rows), repr(session.outbox), repr(vm.first_pending), vm.start_error, session.loaded, session.terminal,
                    conv is None, vm.intro() if conv is None else None)
        if self._changed("body", body_sig):
            stick = self.body.near_bottom() or "body_once" not in self._sigs
            self._sigs["body_once"] = True
            self._render_body(rows)
            if stick:
                self.after_idle(self.body.to_bottom)
        if session.terminal and conv is not None:
            self.composer.pack_forget()
            if self._changed("terminal", (conv.get("id"), vm.outcome())):
                if self.terminal is not None:
                    self.terminal.destroy()
                self.terminal = self._render_terminal()
                self.terminal.pack(fill="x", padx=12, pady=10)
        else:
            if self.terminal is not None:
                self.terminal.destroy()
                self.terminal = None
                self._sigs.pop("terminal", None)
            if not self.composer.winfo_manager():
                self.composer.pack(fill="x", padx=12, pady=10)
            self.composer.update_view(disabled=conv is None and vm.first_pending is not None)

    def _render_head(self) -> None:
        vm, app, t = self.vm, self.app, self.app.ctx.t
        for w in self.head.winfo_children():
            w.destroy()
        back = back_header(self.head, t.t("btn_back"), app.back_from_chat, app.primary)
        back.pack(fill="x")
        row = tk.Frame(self.head, bg=WHITE)
        row.pack(fill="x", padx=16, pady=(6, 12))
        conv = vm.conversation
        if conv is None:
            if vm.draft.get("ticketNumber"):
                tk.Label(row, text=f"{t.t('chat_protocol')} {vm.draft['ticketNumber']}", bg=WHITE, fg=MUTED, font=font(self, 11, "bold"), anchor="w").pack(fill="x")
            tk.Label(row, text=vm.title(), bg=WHITE, fg=TEXT, font=font(self, 15, "bold"), anchor="w").pack(fill="x")
            return
        spec = vm.avatar()
        if spec is not None:
            avatar_widget(row, spec, app.primary, app.ctx.runner, app.ctx.api, size=32).pack(side="left", padx=(0, 10))
        texts = tk.Frame(row, bg=WHITE)
        texts.pack(side="left", fill="x", expand=True)
        tk.Label(texts, text=f"{t.t('chat_protocol')} {conv.get('ticket_number', '')}", bg=WHITE, fg=MUTED, font=font(self, 11, "bold"), anchor="w").pack(fill="x")
        status = tk.Frame(texts, bg=WHITE)
        status.pack(fill="x")
        color = vm.status_color()
        dot(status, color, WHITE).pack(side="left")
        tk.Label(status, text=vm.status_text(), bg=WHITE, fg=color, font=font(self, 13, "bold"), anchor="w").pack(side="left", padx=(4, 0))
        if not vm.session.terminal and not vm.confirm_end:
            FlatButton(row, t.t("btn_chat_end"), vm.request_end, bg=WHITE, fg=SLATE, border=BORDER, hover="#fef2f2",
                       px=12, padx=10, pady=6).pack(side="right")
        if vm.confirm_end and not vm.session.terminal:
            box = tk.Frame(self.head, bg="#fef2f2", highlightthickness=1, highlightbackground="#fecaca")
            box.pack(fill="x", padx=16, pady=(0, 10))
            tk.Label(box, text=t.t("chat_end_confirm"), bg="#fef2f2", fg="#7f1d1d", font=font(self, 13)).pack(side="left", padx=10, pady=8)
            end = FlatButton(box, "…" if vm.ending else t.t("btn_chat_end"), vm.end, bg=RED, px=12, padx=10, pady=6)
            end.pack(side="right", padx=(0, 10))
            end.set_enabled(not vm.ending)
            link_button(box, t.t("btn_cancel"), vm.cancel_end, MUTED, "#fef2f2", bold=False).pack(side="right", padx=10)

    def _system(self, master: tk.Misc, html: str, text: Optional[str] = None) -> None:
        box = tk.Frame(master, bg="#f1f5f9")
        box.pack(padx=24, pady=8, fill="x")
        if text is not None:
            tk.Label(box, text=text, bg="#f1f5f9", fg=MUTED, font=font(self, 12), wraplength=300, justify="center").pack(padx=10, pady=6)
        else:
            HtmlView(box, html, bg="#f1f5f9", fg=MUTED, px=12).pack(fill="x", padx=10, pady=6)

    def _bubble(self, master: tk.Misc, mine: bool, html: str, attachments: list, meta: Any, avatar: Any = None,
                pending: bool = False, failed: bool = False, item: Optional[Dict[str, Any]] = None) -> None:
        app, t = self.app, self.app.ctx.t
        primary = app.primary
        row = tk.Frame(master, bg=SURFACE)
        row.pack(fill="x", pady=(0, 6))
        if not mine and avatar is not None:
            col = tk.Frame(row, bg=SURFACE, width=24)
            col.pack(side="left", anchor="s", padx=(0, 6), pady=(0, 16))
            avatar_widget(col, avatar, primary, app.ctx.runner, app.ctx.api).pack()
        stack = tk.Frame(row, bg=SURFACE)
        stack.pack(side="right" if mine else "left", fill="x", expand=True, padx=(40, 0) if mine else (0, 40))
        if not is_html_empty(html):
            bg = (tint(primary, 0.7) if pending else primary) if mine else WHITE
            box = tk.Frame(stack, bg=bg, highlightthickness=2 if failed else (0 if mine else 1),
                           highlightbackground=RED if failed else BORDER)
            box.pack(anchor="e" if mine else "w", fill="x")
            HtmlView(box, html, bg=bg, fg="#ffffff" if mine else TEXT, light=mine).pack(fill="x", padx=12, pady=8)
        for a in attachments or []:
            clickable = None if (pending or failed) else self._download  # em envio: não baixa
            FileCard(stack, a, primary, clickable, app.ctx.runner, app.ctx.api).pack(anchor="e" if mine else "w", fill="x", pady=(4, 0))
        if failed and item is not None:
            m = tk.Frame(stack, bg=SURFACE)
            m.pack(anchor="e")
            tk.Label(m, text=t.t("chat_msg_failed") + " ", bg=SURFACE, fg=RED, font=font(self, 10)).pack(side="left")
            link_button(m, t.t("btn_retry"), lambda tid=item["temp_id"]: self.vm.session.retry(tid), RED, SURFACE, px=10).pack(side="left")
            tk.Label(m, text=" · ", bg=SURFACE, fg=RED, font=font(self, 10)).pack(side="left")
            link_button(m, t.t("btn_discard"), lambda tid=item["temp_id"]: self.vm.session.discard(tid), RED, SURFACE, px=10).pack(side="left")
        elif pending:
            tk.Label(stack, text=t.t("chat_msg_sending"), bg=SURFACE, fg=SUBTLE, font=font(self, 10)).pack(anchor="e")
        elif meta:
            author, when = meta
            m = tk.Frame(stack, bg=SURFACE)
            m.pack(anchor="e" if mine else "w")
            tk.Label(m, text=author, bg=SURFACE, fg=SUBTLE if mine else "#334155", font=font(self, 10 if mine else 11, "normal" if mine else "bold")).pack(side="left")
            tk.Label(m, text=f" · {when}", bg=SURFACE, fg=SUBTLE, font=font(self, 10)).pack(side="left")

    def _download(self, att: Dict[str, Any]) -> None:
        api, runner = self.app.ctx.api, self.app.ctx.runner
        runner.submit(lambda: api.download_attachment(str(att.get("id"))),
                      lambda res: self.app._on_download(res.get("url"), res.get("filename") or att.get("filename") or "download"),
                      self.app.ctx.note_error)

    def _render_body(self, rows: list) -> None:
        vm, t = self.vm, self.app.ctx.t
        self.body.clear()
        inner = tk.Frame(self.body.inner, bg=SURFACE)
        inner.pack(fill="x", padx=12, pady=12)
        session = vm.session
        if vm.conversation is None:
            self._system(inner, "", vm.intro())
            if vm.first_pending:
                p = vm.first_pending
                self._bubble(inner, True, p["content"], p["attachments"], None, pending=True)
            if vm.start_error:
                tk.Label(inner, text=t.t("error_generic"), bg=SURFACE, fg=RED, font=font(self, 12)).pack(pady=(8, 0))
            return
        if not session.loaded and not session.messages:
            tk.Label(inner, text=t.t("loading"), bg=SURFACE, fg=self.app.primary, font=font(self, 13)).pack(pady=32)
        for r in rows:
            if r["kind"] == "system":
                self._system(inner, r["html"])
            else:
                self._bubble(inner, r["mine"], r["html"], r["attachments"], r["meta"], r["avatar"])
        for o in session.outbox:
            self._bubble(inner, True, o["content"], o["attachments"], None, pending=o["state"] == "sending",
                         failed=o["state"] == "failed", item=o)
        if session.terminal:
            self._system(inner, "", t.t("chat_closed_notice"))

    def _render_terminal(self) -> tk.Frame:
        vm, app, t = self.vm, self.app, self.app.ctx.t
        conv = vm.conversation or {}
        frame = tk.Frame(self.foot, bg=WHITE)
        outcome = vm.outcome()
        if outcome == "csat":
            CsatWidget(frame, app.csat_for(str(conv.get("ticket_id"))), app.primary).pack(fill="x", pady=(0, 10))
        if outcome == "follow_up":
            box = tk.Frame(frame, bg=tint(app.primary, 0.06), highlightthickness=1, highlightbackground=tint(app.primary, 0.28))
            box.pack(fill="x", pady=(0, 10))
            tk.Label(box, text=t.t("chat_followup_notice", n=conv.get("ticket_number", "")), bg=tint(app.primary, 0.06), fg=TEXT,
                     font=font(self, 13), wraplength=330, justify="left", anchor="w").pack(fill="x", padx=12, pady=(12, 0))
            FlatButton(box, t.t("btn_view_ticket"), app.view_chat_ticket, bg=app.primary, pady=11).pack(fill="x", padx=12, pady=12)
        FlatButton(frame, t.t("btn_chat_continue"), app.continue_chat, bg=WHITE, fg=app.primary, border=app.primary, pady=11).pack(fill="x")
        if outcome != "follow_up":
            FlatButton(frame, f"{t.t('btn_view_ticket')} {conv.get('ticket_number', '')}", app.view_chat_ticket, bg=WHITE, fg=MUTED,
                       bold=False, px=13, pady=6).pack(pady=(6, 0))
        return frame
