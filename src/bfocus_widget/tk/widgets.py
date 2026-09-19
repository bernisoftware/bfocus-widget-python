"""Componentes Tk opcionais: botão redondo com badge e pílula de versão (BRIEF §3)."""
from __future__ import annotations

import tkinter as tk
from typing import Any, Callable, List

from ..core.widget import BFocusWidget
from ..shared import Translator, tokens
from .host import TkRunner
from .views.common import font

BADGE_RED = "#ef4444"


def _parent_bg(master: tk.Misc) -> str:
    try:
        return str(master.cget("bg"))
    except tk.TclError:
        return "#f0f0f0"  # pai ttk não tem "bg"


def _pill(c: tk.Canvas, x0: float, y0: float, x1: float, y1: float, **kw: Any) -> None:
    r = (y1 - y0) / 2
    c.create_oval(x0, y0, x0 + 2 * r, y1, **kw)
    c.create_oval(x1 - 2 * r, y0, x1, y1, **kw)
    c.create_rectangle(x0 + r, y0, x1 - r, y1, **kw)


class _Linked(tk.Canvas):
    """Canvas que ouve o widget; os eventos voltam para a thread do Tk."""

    def _listen(self, widget: BFocusWidget, events: List[tuple]) -> None:
        runner = TkRunner.for_root(self)
        self._subs: List[Callable[[], None]] = []
        for name, fn in events:
            self._subs.append(widget.subscribe(name, lambda *a, fn=fn: runner.call_soon(lambda: self._alive() and fn(*a))))

    def _alive(self) -> bool:
        try:
            return bool(self.winfo_exists())
        except tk.TclError:
            return False

    def destroy(self) -> None:
        for c in getattr(self, "_subs", []):
            c()
        super().destroy()


class BFocusTkLauncherButton(_Linked):
    DIAMETER = 56

    def __init__(self, master: tk.Misc, widget: BFocusWidget) -> None:
        super().__init__(master, width=self.DIAMETER + 8, height=self.DIAMETER + 8, bg=_parent_bg(master),
                         highlightthickness=0, bd=0, cursor="hand2")
        self.widget = widget
        self.color = widget.primary_color or tokens()["brandFallback"]
        self.label = widget.badge_label
        self._open = widget.is_open
        self._hover = False
        self._listen(widget, [
            ("badge", lambda label: self._set(label=label)),
            ("branding", lambda color: self._set(color=color)),
            ("open", lambda: self._set(is_open=True)),
            ("close", lambda: self._set(is_open=False)),
        ])
        self.bind("<Button-1>", lambda _e: widget.close() if widget.is_open else widget.open())
        self.bind("<Enter>", lambda _e: self._set(hover=True))
        self.bind("<Leave>", lambda _e: self._set(hover=False))
        self.tooltip = Translator(widget.config.locale or "pt_BR").native("open_support")
        self._draw()

    def _set(self, **kw: Any) -> None:
        if "label" in kw:
            self.label = kw["label"]
        if "color" in kw:
            self.color = kw["color"]
        if "is_open" in kw:
            self._open = kw["is_open"]
        if "hover" in kw:
            self._hover = kw["hover"]
        self._draw()

    def _draw(self) -> None:
        from ..tk.viewmodels.formatting import mix  # noqa: PLC0415

        self.delete("all")
        d = self.DIAMETER
        fill = mix(self.color, "#ffffff", 0.92) if self._hover else self.color
        self.create_oval(0, 8, d, 8 + d, fill=fill, outline="")
        cx, cy = d / 2, 8 + d / 2
        if self._open:
            self.create_line(cx - 7, cy - 7, cx + 7, cy + 7, fill="#ffffff", width=2, capstyle="round")
            self.create_line(cx - 7, cy + 7, cx + 7, cy - 7, fill="#ffffff", width=2, capstyle="round")
        else:
            # Balão do widget.js: corpo oval, rabinho e três pontos.
            self.create_oval(cx - 10, cy - 9, cx + 10, cy + 8, outline="#ffffff", width=2)
            self.create_line(cx - 4, cy + 7, cx - 10, cy + 11, cx - 8, cy + 5, fill="#ffffff", width=2, joinstyle="round")
            for dx in (-4, 0, 4):
                self.create_oval(cx + dx - 1.2, cy - 1.2, cx + dx + 1.2, cy + 1.2, fill="#ffffff", outline="")
        if self.label:
            import tkinter.font as tkfont  # noqa: PLC0415

            f = font(self, 11, "bold")
            w = max(20, tkfont.Font(root=self, font=f).measure(self.label) + 10)
            x1 = d + 8
            _pill(self, x1 - w - 1, 0, x1 - 1, 20, fill="#ffffff", outline="")
            _pill(self, x1 - w + 1, 2, x1 - 3, 18, fill=BADGE_RED, outline="")
            self.create_text(x1 - w / 2 - 2, 10, text=self.label, fill="#ffffff", font=f)


class BFocusTkReleaseBadge(_Linked):
    """Pílula ★ `v4.2.0` (ou `—`) com ponto de novidade; clicar abre o histórico."""

    def __init__(self, master: tk.Misc, widget: BFocusWidget) -> None:
        super().__init__(master, height=26, width=90, bg=_parent_bg(master), highlightthickness=0, bd=0, cursor="hand2")
        self.widget = widget
        st = widget.release_notes
        self.label, self.dot = st.label, st.dot
        self.color = widget.primary_color or tokens()["brandFallback"]
        self._listen(widget, [
            ("release_notes", lambda st: self._state(st)),
            ("branding", lambda color: self._color(color)),
        ])
        self.bind("<Button-1>", lambda _e: widget.open_release_notes_history())
        self._draw()

    def _state(self, st: Any) -> None:
        self.label, self.dot = st.label, st.dot
        if st.color:
            self.color = st.color
        self._draw()

    def _color(self, color: str) -> None:
        self.color = color
        self._draw()

    def _draw(self) -> None:
        import tkinter.font as tkfont  # noqa: PLC0415

        self.delete("all")
        f = font(self, 12, "bold")
        tw = tkfont.Font(root=self, font=f).measure(self.label)
        w = 10 + 14 + 6 + tw + (6 + 7 if self.dot else 0) + 10
        self.configure(width=w)
        _pill(self, 0, 0, w - 1, 25, fill=self.color, outline="")
        pts = [(12, 2), (14.4, 9.4), (22, 9.4), (16, 13.8), (18.3, 21.2), (12, 16.6), (5.7, 21.2), (7.9, 13.8), (2, 9.4), (9.6, 9.4)]
        s = 14 / 24
        self.create_polygon([c for x, y in pts for c in (10 + x * s, 6 + y * s)], fill="#ffffff", outline="")
        self.create_text(30, 13, text=self.label, anchor="w", fill="#ffffff", font=f)
        if self.dot:
            x = 30 + tw + 9
            self.create_oval(x - 4, 9, x + 4, 17, fill=BADGE_RED, outline="#ffffff")
