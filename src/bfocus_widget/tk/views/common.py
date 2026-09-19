"""Peças visuais comuns da UI Tk: cores dos tokens, fontes em px, botões planos (o botão
nativo do macOS ignora a cor de fundo), área rolável, imagens e cartões de anexo."""
from __future__ import annotations

import base64
import io
import logging
import sys
import tkinter as tk
from tkinter import font as tkfont
from typing import Any, Callable, Dict, List, Optional

from ...shared import tokens
from ..viewmodels.formatting import attachment_icon, mix, pretty_bytes, tint

log = logging.getLogger("bfocus_widget")

_N = tokens()["neutral"]
TEXT = _N["text"]
MUTED = _N["muted"]
SUBTLE = _N["subtle"]
BORDER = _N["border"]
SURFACE = _N["surface"]
WHITE = _N["background"]
SLATE = "#475569"
RED = "#ef4444"
AMBER = "#f59e0b"

_families: Dict[str, str] = {}


def font(widget: tk.Misc, px: int, *styles: str) -> tuple:
    """Fonte do sistema em pixels (negativo = px no Tk), como os tamanhos do CSS do web."""
    key = str(widget.winfo_toplevel())
    if key not in _families:
        try:
            _families[key] = tkfont.nametofont("TkDefaultFont", root=widget).actual("family")
        except (tk.TclError, TypeError):
            _families[key] = "TkDefaultFont"
    family = "Courier" if "mono" in styles else _families[key]
    return (family, -abs(int(px))) + tuple(s for s in styles if s in ("bold", "italic", "overstrike", "underline"))


def on_primary(primary: str, alpha: float) -> str:
    """Branco translúcido sobre a cor da marca (o `rgba(255,255,255,a)` do web)."""
    return mix("#ffffff", primary, alpha)


class FlatButton(tk.Label):
    def __init__(self, master: tk.Misc, text: str = "", command: Optional[Callable[[], None]] = None, *,
                 bg: str = "#6366F1", fg: str = "#ffffff", hover: Optional[str] = None, border: Optional[str] = None,
                 disabled_bg: str = "#cbd5e1", disabled_fg: Optional[str] = None, px: int = 14, bold: bool = True,
                 padx: int = 16, pady: int = 10, anchor: str = "center", **kw: Any) -> None:
        super().__init__(master, text=text, bg=bg, fg=fg, font=font(master, px, "bold" if bold else "normal"),
                         padx=padx, pady=pady, cursor="hand2", anchor=anchor, bd=0,
                         highlightthickness=1 if border else 0, highlightbackground=border or bg, **kw)
        self._cmd = command
        self._bg, self._fg = bg, fg
        self._hover = hover or bg
        self._disabled_bg = disabled_bg
        self._disabled_fg = disabled_fg or fg
        self.enabled = True
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", lambda _e: self.enabled and self.configure(bg=self._hover))
        self.bind("<Leave>", lambda _e: self.enabled and self.configure(bg=self._bg))

    def _click(self, _e: Any = None) -> None:
        if self.enabled and self._cmd is not None:
            self._cmd()

    def invoke(self) -> None:
        self._click()

    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on)
        self.configure(bg=self._bg if on else self._disabled_bg, fg=self._fg if on else self._disabled_fg,
                       cursor="hand2" if on else "arrow")

    def set_colors(self, bg: str, fg: Optional[str] = None, hover: Optional[str] = None) -> None:
        self._bg = bg
        self._fg = fg or self._fg
        self._hover = hover or bg
        if self.enabled:
            self.configure(bg=bg, fg=self._fg)


class _FocusWatcher:
    """Um por raiz do Tk: avisa quando o APP volta ao foco depois de perdê-lo (o "voltou à
    aba" do web). Troca de foco entre campos e janelas do próprio app não conta."""

    def __init__(self, root: tk.Misc) -> None:
        self.root = root
        self.away = False
        self.callbacks: List[Callable[[], None]] = []
        root.bind_all("<FocusOut>", self._out, add="+")
        root.bind_all("<FocusIn>", self._in, add="+")

    def _out(self, _e: Any = None) -> None:
        try:
            self.root.after_idle(self._check)  # o foco novo só existe depois do evento
        except tk.TclError:
            pass

    def _check(self) -> None:
        try:
            if self.root.focus_get() is None:
                self.away = True
        except (tk.TclError, KeyError):
            pass

    def _in(self, _e: Any = None) -> None:
        if not self.away:
            return
        self.away = False
        for fn in list(self.callbacks):
            try:
                fn()
            except Exception:  # noqa: BLE001 - um ouvinte com erro não derruba os outros
                log.exception("bfocus tk: ouvinte de foco falhou")


def on_app_focus(widget: tk.Misc, callback: Callable[[], None]) -> Callable[[], None]:
    """Chama `callback` quando o app volta ao foco. Devolve a função que cancela."""
    root = widget._root()  # type: ignore[attr-defined]
    watcher = getattr(root, "_bfocus_focus", None)
    if watcher is None:
        watcher = _FocusWatcher(root)
        root._bfocus_focus = watcher  # type: ignore[attr-defined]
    watcher.callbacks.append(callback)

    def cancel() -> None:
        try:
            watcher.callbacks.remove(callback)
        except ValueError:
            pass

    return cancel


def link_button(master: tk.Misc, text: str, command: Callable[[], None], color: str, bg: str, px: int = 13, bold: bool = True) -> FlatButton:
    return FlatButton(master, text, command, bg=bg, fg=color, px=px, bold=bold, padx=0, pady=0, anchor="w")


class ScrollArea(tk.Frame):
    """Canvas + frame interno + barra; a roda do mouse rola a área sob o ponteiro."""

    def __init__(self, master: tk.Misc, bg: str) -> None:
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.bar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.bind("<Enter>", lambda _e: self._wheel(True))
        self.bind("<Leave>", lambda _e: self._wheel(False))

    def _wheel(self, on: bool) -> None:
        try:
            if on:
                self.bind_all("<MouseWheel>", self._on_wheel)
                self.bind_all("<Button-4>", lambda _e: self.canvas.yview_scroll(-3, "units"))
                self.bind_all("<Button-5>", lambda _e: self.canvas.yview_scroll(3, "units"))
            else:
                for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                    self.unbind_all(seq)
        except tk.TclError:
            pass

    def _on_wheel(self, e: Any) -> None:
        step = -e.delta if sys.platform == "darwin" else -int(e.delta / 120) * 3
        self.canvas.yview_scroll(step, "units")

    def clear(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()

    def near_bottom(self, px: int = 80) -> bool:
        first, last = self.canvas.yview()
        total = max(1, self.inner.winfo_height())
        return (1.0 - last) * total < px

    def to_bottom(self) -> None:
        self.update_idletasks()
        self.canvas.yview_moveto(1.0)


def photo_from_bytes(data: bytes, max_w: int) -> Any:
    """Imagem → PhotoImage. Com Pillow: qualquer formato; sem ele: PNG e GIF (o que o Tk lê)."""
    try:
        from PIL import Image, ImageTk  # type: ignore  # noqa: PLC0415

        img = Image.open(io.BytesIO(data))
        img.thumbnail((max_w, max_w * 2))
        return ImageTk.PhotoImage(img)
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        return None
    if not (data.startswith(b"\x89PNG") or data[:6] in (b"GIF87a", b"GIF89a")):
        return None
    try:
        img = tk.PhotoImage(data=base64.b64encode(data).decode("ascii"))
    except tk.TclError:
        return None
    factor = -(-img.width() // max_w) if img.width() > max_w else 1
    return img.subsample(factor) if factor > 1 else img


def load_image(label: tk.Label, runner: Any, api: Any, url: Optional[str], max_w: int) -> None:
    """Baixa fora da UI e põe a imagem no label (se ele ainda existir)."""
    if not url:
        return

    def ok(data: bytes) -> None:
        try:
            if not label.winfo_exists():
                return
            img = photo_from_bytes(data, max_w)
            if img is not None:
                label.configure(image=img, text="")
                label.image = img  # type: ignore[attr-defined]  # referência: o Tk não guarda
        except tk.TclError:
            pass

    runner.submit(lambda: api.fetch_bytes(url), ok, lambda _e: None)


def bind_tree(widget: tk.Misc, seq: str, fn: Callable[[Any], Any]) -> None:
    widget.bind(seq, fn)
    for child in widget.winfo_children():
        bind_tree(child, seq, fn)


def status_pill(master: tk.Misc, text: str, color: str, bg: str = WHITE) -> tk.Label:
    return tk.Label(master, text=text.upper(), fg=color, bg=tint(color, 0.10, bg), font=font(master, 10, "bold"), padx=8, pady=2)


class FileCard(tk.Frame):
    """Anexo: imagem vira prévia; o resto, cartão com ícone. Clicar baixa com o nome original."""

    def __init__(self, master: tk.Misc, att: Dict[str, Any], primary: str, on_click: Optional[Callable[[Dict[str, Any]], None]],
                 runner: Any = None, api: Any = None, bg: str = WHITE) -> None:
        super().__init__(master, bg=bg, highlightthickness=1, highlightbackground=BORDER, cursor="hand2" if on_click else "arrow")
        mime = att.get("content_type") or ""
        name = att.get("filename") or ""
        size = att.get("size")
        if mime.startswith("image/") and att.get("url") and runner is not None:
            preview = tk.Label(self, text="🖼️", bg=bg, font=font(self, 28))
            preview.pack(fill="x")
            load_image(preview, runner, api, att.get("url"), 240)
            meta = f"{name}" + (f" · {pretty_bytes(size)}" if size else "")
            tk.Label(self, text=meta + ("  ⬇" if on_click else ""), bg=bg, fg=SLATE, font=font(self, 11), anchor="w", padx=10, pady=5).pack(fill="x")
        else:
            row = tk.Frame(self, bg=bg)
            row.pack(fill="x", padx=10, pady=8)
            tk.Label(row, text=attachment_icon(mime), bg=tint(primary, 0.10), font=font(self, 16), width=2).pack(side="left")
            info = tk.Frame(row, bg=bg)
            info.pack(side="left", fill="x", expand=True, padx=(10, 0))
            tk.Label(info, text=name, bg=bg, fg=TEXT, font=font(self, 13), anchor="w").pack(fill="x")
            sub = " · ".join(x for x in (pretty_bytes(size) if size else "", mime) if x)
            if sub:
                tk.Label(info, text=sub, bg=bg, fg=MUTED, font=font(self, 11), anchor="w").pack(fill="x")
            if on_click:
                tk.Label(row, text="⬇", bg=bg, fg=MUTED, font=font(self, 16)).pack(side="right")
        if on_click:
            bind_tree(self, "<Button-1>", lambda _e: on_click(att))


def chip(master: tk.Misc, text: str, on_remove: Callable[[], None], icon: str = "📄") -> tk.Frame:
    """Anexo pendente (antes de enviar), com ✕ para remover."""
    f = tk.Frame(master, bg="#f1f5f9", highlightthickness=1, highlightbackground="#cbd5e1")
    tk.Label(f, text=f"{icon} {text[:40]}", bg="#f1f5f9", fg=TEXT, font=font(master, 12)).pack(side="left", padx=(8, 2), pady=2)
    x = tk.Label(f, text="✕", bg="#f1f5f9", fg=MUTED, cursor="hand2", font=font(master, 12))
    x.pack(side="left", padx=(2, 8))
    x.bind("<Button-1>", lambda _e: on_remove())
    return f
