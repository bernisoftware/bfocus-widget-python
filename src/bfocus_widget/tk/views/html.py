"""HTML do conteúdo e editor rico na UI Tk (spec §8, com as diferenças aceitas da §10).

- `HtmlView`: HTML sanitizado desenhado num `tk.Text` com estilos (negrito, itálico,
  riscado, código, citação, listas, links que abrem no navegador do sistema).
- `html_widget`: usa o `tkinterweb` (se instalado) para o conteúdo longo das novidades;
  sem ele, ou se o Tkhtml não carregar, cai no `HtmlView`.
- `RichEditor`: negrito, itálico, riscado, lista, lista numerada, citação, código e link.
  O link abre um campo de URL sob a barra (sem diálogo): Enter aplica, Esc cancela, vazio
  remove o link, sem esquema vira `https://`; "remover link" aparece se a seleção já tem link.
  Imagem colada sobe e entra no texto quando o Pillow está instalado (lê a área de
  transferência); sem ele, colar imagem não é suportado.
"""
from __future__ import annotations

import os
import re
import tempfile
import tkinter as tk
import webbrowser
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..viewmodels.formatting import escape_html, is_html_empty
from ..viewmodels.html_text import html_to_segments
from .common import MUTED, SLATE, TEXT, WHITE, font

_INLINE = ("bold", "italic", "strike", "code")


def _count(text: tk.Text, *args: str) -> int:
    # Tk 8.6 devolve tupla; Tk 9 pode devolver int ou None.
    try:
        r = text.count(*args)
    except tk.TclError:
        return 0
    if isinstance(r, tuple):
        return int(r[0]) if r else 0
    return int(r or 0)


class HtmlView(tk.Text):
    def __init__(self, master: tk.Misc, html: str = "", *, bg: str = WHITE, fg: str = TEXT, px: int = 13,
                 link_color: str = "#2563eb", quote_color: str = "#6366F1", light: bool = False,
                 on_link: Optional[Callable[[str], None]] = None, image_label: str = "imagem",
                 auto_height: bool = True, **kw: Any) -> None:
        super().__init__(master, bg=bg, fg=fg, wrap="word", bd=0, highlightthickness=0, padx=0, pady=0,
                         height=1, width=1, cursor="arrow", relief="flat", font=font(master, px),
                         spacing3=2, insertwidth=0, **kw)
        self._on_link = on_link or (lambda url: webbrowser.open(url))
        self._image_label = image_label
        self._auto = auto_height
        self._links = 0
        sub = "#e2e8f0" if light else "#f1f5f9"
        self.tag_configure("bold", font=font(master, px, "bold"))
        self.tag_configure("italic", font=font(master, px, "italic"))
        self.tag_configure("bold_italic", font=font(master, px, "bold", "italic"))
        self.tag_configure("strike", overstrike=True)
        self.tag_configure("underline", underline=True)
        self.tag_configure("code", font=font(master, px - 1, "mono"), background=sub, foreground=TEXT if not light else fg)
        self.tag_configure("pre", font=font(master, px - 1, "mono"), background=sub, lmargin1=8, lmargin2=8)
        self.tag_configure("quote", foreground=("#e2e8f0" if light else MUTED), lmargin1=12, lmargin2=12)
        self.tag_configure("li", lmargin2=18)
        self.tag_configure("link", foreground=("#ffffff" if light else link_color), underline=True)
        self.render(html)
        if auto_height:
            self.bind("<Configure>", lambda _e: self._fit())

    def render(self, html: str) -> None:
        self.configure(state="normal")
        self.delete("1.0", "end")
        for seg in html_to_segments(html, self._image_label):
            tags = list(seg.tags)
            if "bold" in tags and "italic" in tags:
                tags = [t for t in tags if t not in ("bold", "italic")] + ["bold_italic"]
            if seg.href:
                self._links += 1
                name = f"href{self._links}"
                self.tag_bind(name, "<Button-1>", lambda _e, u=seg.href: self._on_link(u))
                self.tag_bind(name, "<Enter>", lambda _e: self.configure(cursor="hand2"))
                self.tag_bind(name, "<Leave>", lambda _e: self.configure(cursor="arrow"))
                tags.append(name)
            self.insert("end", seg.text, tuple(tags))
        self.configure(state="disabled")
        self._fit()

    def _fit(self) -> None:
        if not self._auto:
            return
        n = _count(self, "1.0", "end", "displaylines")
        self.configure(height=max(1, n))

    def metrics(self) -> Tuple[float, float, float]:
        """(topo, visível, total) em px — a trava de rolagem do banner usa isto."""
        total = float(_count(self, "1.0", "end", "ypixels"))
        visible = float(self.winfo_height())
        first, _last = self.yview()
        return first * total, visible, total


def html_widget(master: tk.Misc, html: str, *, bg: str = WHITE, prefer_web: bool = True, height: int = 220, **kw: Any) -> tk.Widget:
    """Conteúdo das novidades: tkinterweb quando disponível, senão `HtmlView`."""
    if prefer_web and os.environ.get("BFOCUS_TK_HTML", "") != "text":
        try:
            from tkinterweb import HtmlFrame  # type: ignore  # noqa: PLC0415

            frame = HtmlFrame(master, messages_enabled=False, height=height)
            frame.load_html(f'<div style="font-family:sans-serif;font-size:13px;color:#334155">{html}</div>')
            frame.on_link_click(lambda url: webbrowser.open(url))
            return frame
        except Exception:  # noqa: BLE001 - Tkhtml ausente/incompatível: cai no Text
            pass
    return HtmlView(master, html, bg=bg, **kw)


class RichEditor(tk.Frame):
    """Editor rico mínimo sobre `tk.Text`. `to_html()` devolve o HTML (sem títulos)."""

    def __init__(self, master: tk.Misc, t: Any, *, placeholder: str = "", height: int = 5, primary: str = "#6366F1",
                 on_change: Optional[Callable[[str], None]] = None,
                 on_paste_image: Optional[Callable[[str, Callable[[str], None]], None]] = None,
                 initial_html: str = "") -> None:
        super().__init__(master, bg=WHITE, highlightthickness=1, highlightbackground="#cbd5e1", highlightcolor=primary)
        self._t = t
        self._on_change = on_change or (lambda html: None)
        self._on_paste_image = on_paste_image
        self._placeholder = placeholder
        self._ph = False
        self._links: Dict[str, str] = {}
        bar = tk.Frame(self, bg="#f8fafc")
        bar.pack(fill="x")
        for label, key, cmd in (
            ("B", "editor_bold", lambda: self._toggle("bold")),
            ("I", "editor_italic", lambda: self._toggle("italic")),
            ("S", "editor_strike", lambda: self._toggle("strike")),
            ("•", "editor_bullet_list", lambda: self._list("ul")),
            ("1.", "editor_ordered_list", lambda: self._list("ol")),
            ("❝", "editor_quote", self._quote),
            ("</>", "editor_code", lambda: self._toggle("code")),
            ("🔗", "editor_link", self._link),
        ):
            b = tk.Label(bar, text=label, bg="#f8fafc", fg=SLATE, font=font(self, 12, "bold"), padx=6, pady=3, cursor="hand2")
            b.pack(side="left")
            b.bind("<Button-1>", lambda _e, c=cmd: c())
            b.tooltip = t.t(key)  # type: ignore[attr-defined]
        self._build_link_field(primary)
        self.text = tk.Text(self, height=height, wrap="word", undo=True, bd=0, highlightthickness=0, padx=8, pady=6,
                            font=font(self, 14), fg=TEXT, bg=WHITE, insertbackground=TEXT)
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("bold", font=font(self, 14, "bold"))
        self.text.tag_configure("italic", font=font(self, 14, "italic"))
        self.text.tag_configure("strike", overstrike=True)
        self.text.tag_configure("code", font=font(self, 13, "mono"), background="#f1f5f9")
        self.text.tag_configure("quote", foreground=MUTED, lmargin1=12, lmargin2=12)
        self.text.tag_configure("link", foreground="#2563eb", underline=True)
        self.text.tag_configure("ph", foreground="#94a3b8")
        self.text.bind("<<Modified>>", self._modified)
        self.text.bind("<FocusIn>", lambda _e: self._hide_ph())
        self.text.bind("<FocusOut>", lambda _e: self._show_ph())
        mod = "Command" if self.tk.call("tk", "windowingsystem") == "aqua" else "Control"
        self.text.bind(f"<{mod}-b>", lambda _e: (self._toggle("bold"), "break")[1])
        self.text.bind(f"<{mod}-i>", lambda _e: (self._toggle("italic"), "break")[1])
        self.text.bind("<<Paste>>", self._paste)
        if initial_html:
            self.set_html(initial_html)
        else:
            self._show_ph()

    # ── placeholder ──
    def _show_ph(self) -> None:
        if not self._ph and not self.text.get("1.0", "end-1c").strip() and self._placeholder:
            self._ph = True
            self.text.insert("1.0", self._placeholder, ("ph",))
            self.text.edit_modified(False)

    def _hide_ph(self) -> None:
        if self._ph:
            self._ph = False
            self.text.delete("1.0", "end")
            self.text.edit_modified(False)

    def _modified(self, _e: Any = None) -> None:
        if self.text.edit_modified():
            self.text.edit_modified(False)
            if not self._ph:
                self._on_change(self.to_html())

    # ── formatação ──
    def _sel(self) -> Optional[Tuple[str, str]]:
        try:
            return self.text.index("sel.first"), self.text.index("sel.last")
        except tk.TclError:
            return None

    def _toggle(self, tag: str) -> None:
        sel = self._sel()
        if not sel or self._ph:
            return
        if tag in self.text.tag_names(sel[0]):
            self.text.tag_remove(tag, *sel)
        else:
            self.text.tag_add(tag, *sel)
        self._on_change(self.to_html())

    def _lines(self) -> range:
        sel = self._sel()
        a, b = (sel if sel else (self.text.index("insert"), self.text.index("insert")))
        return range(int(a.split(".")[0]), int(b.split(".")[0]) + 1)

    def _list(self, kind: str) -> None:
        if self._ph:
            self._hide_ph()
        for n, ln in enumerate(self._lines(), 1):
            line = self.text.get(f"{ln}.0", f"{ln}.end")
            m = re.match(r"^(• |\d+\. )", line)
            if m:
                self.text.delete(f"{ln}.0", f"{ln}.{len(m.group(0))}")
            if not m or (kind == "ul") != (m.group(0) == "• "):
                self.text.insert(f"{ln}.0", "• " if kind == "ul" else f"{n}. ")
        self._on_change(self.to_html())

    def _quote(self) -> None:
        for ln in self._lines():
            rng = (f"{ln}.0", f"{ln}.end")
            if "quote" in self.text.tag_names(rng[0]):
                self.text.tag_remove("quote", *rng)
            else:
                self.text.tag_add("quote", *rng)
        self._on_change(self.to_html())

    # ── link: campo sob a barra, como o editor web ──
    def _build_link_field(self, primary: str) -> None:
        self._link_bar = tk.Frame(self, bg="#f8fafc")
        self._link_var = tk.StringVar()
        self._link_entry = tk.Entry(self._link_bar, textvariable=self._link_var, font=font(self, 13), relief="flat",
                                    highlightthickness=1, highlightbackground="#cbd5e1", highlightcolor=primary)
        self._link_entry.pack(side="left", fill="x", expand=True, padx=(6, 4), pady=4, ipady=3)
        self._link_apply = tk.Label(self._link_bar, text=self._t.t("editor_link_apply"), bg=primary, fg="#ffffff",
                                    font=font(self, 12, "bold"), padx=8, pady=3, cursor="hand2")
        self._link_apply.pack(side="left", padx=(0, 4))
        self._link_apply.bind("<Button-1>", lambda _e: self.apply_link())
        self._link_remove = tk.Label(self._link_bar, text=self._t.t("editor_link_remove"), bg="#f8fafc", fg=SLATE,
                                     font=font(self, 12), padx=6, pady=3, cursor="hand2")
        self._link_remove.bind("<Button-1>", lambda _e: self.remove_link())
        self._link_entry.bind("<Return>", lambda _e: (self.apply_link(), "break")[1])
        self._link_entry.bind("<Escape>", lambda _e: (self.cancel_link(), "break")[1])
        self._link_range: Optional[Tuple[str, str]] = None

    def _link_names(self, a: str, b: str) -> List[str]:
        names = set()
        idx = a
        while self.text.compare(idx, "<", b):
            names.update(n for n in self.text.tag_names(idx) if n in self._links)
            idx = self.text.index(f"{idx}+1c")
        return sorted(names)

    def _link(self) -> None:
        sel = self._sel()
        if not sel or self._ph:
            return
        self._link_range = sel
        self.text.tag_add("sel", *sel)
        existing = self._link_names(*sel)
        self._link_var.set(self._links[existing[0]] if existing else "")
        if existing:
            self._link_remove.pack(side="left", padx=(0, 6))
        else:
            self._link_remove.pack_forget()
        self._link_entry.configure(fg=TEXT)
        self._link_bar.pack(fill="x", before=self.text)
        self._link_entry.focus_set()
        self._link_entry.icursor("end")

    @property
    def link_field_open(self) -> bool:
        return self._link_range is not None

    def _close_link_field(self) -> None:
        self._link_range = None
        self._link_bar.pack_forget()
        self.text.focus_set()

    def cancel_link(self) -> None:
        self._close_link_field()

    def apply_link(self) -> None:
        rng = self._link_range
        if rng is None:
            return
        url = self._link_var.get().strip()
        if not url:
            self.remove_link()  # vazio remove o link
            return
        if not re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I):
            url = "https://" + url  # sem esquema vira https://
        elif not re.match(r"^(https?|mailto|tel):", url, re.I):
            self._link_entry.configure(fg="#ef4444")  # esquema não permitido (ex.: javascript:)
            return
        self._strip_links(*rng)
        self._add_link(rng[0], rng[1], url)
        self._close_link_field()

    def remove_link(self) -> None:
        rng = self._link_range
        if rng is not None:
            self._strip_links(*rng)
            self._on_change(self.to_html())
        self._close_link_field()

    def _strip_links(self, a: str, b: str) -> None:
        for name in self._link_names(a, b):
            self.text.tag_remove(name, a, b)
        self.text.tag_remove("link", a, b)

    def _add_link(self, a: str, b: str, url: str) -> None:
        name = f"link:{len(self._links) + 1}"
        self._links[name] = url
        self.text.tag_add("link", a, b)
        self.text.tag_add(name, a, b)
        self._on_change(self.to_html())

    def _paste(self, _e: Any = None) -> Optional[str]:
        if self._on_paste_image is None:
            return None
        try:
            from PIL import ImageGrab  # type: ignore  # noqa: PLC0415

            img = ImageGrab.grabclipboard()
        except Exception:  # noqa: BLE001 - sem Pillow ou sem suporte na plataforma
            return None
        if img is None or not hasattr(img, "save"):
            return None
        fd, path = tempfile.mkstemp(suffix=".png", prefix="bfocus-paste-")
        os.close(fd)
        img.save(path, "PNG")
        self._hide_ph()
        mark = self.text.index("insert")
        label = f"[{self._t.native('image')}]"

        def insert(url: str) -> None:
            if not url or not self.winfo_exists():
                return
            self.text.insert(mark, label)
            self._add_link(mark, f"{mark}+{len(label)}c", "img:" + url)

        self._on_paste_image(path, insert)
        return "break"

    # ── HTML ──
    def is_empty(self) -> bool:
        return self._ph or is_html_empty(self.to_html())

    def clear(self) -> None:
        self._ph = False
        self.text.delete("1.0", "end")
        self._links.clear()
        self.text.edit_modified(False)
        if self.focus_get() is not self.text:
            self._show_ph()

    def set_html(self, html: str) -> None:
        self._ph = False
        self.text.delete("1.0", "end")
        for seg in html_to_segments(html, self._t.native("image")):
            tags = [t for t in seg.tags if t in _INLINE or t == "quote"]
            start = self.text.index("end-1c")
            self.text.insert("end", seg.text, tuple(tags))
            if seg.href:
                self._add_link(start, self.text.index("end-1c"), seg.href)
        self.text.edit_modified(False)
        self._on_change(self.to_html())

    def _inline(self, ln: int, start_col: int, line: str) -> str:
        out: List[str] = []
        run: List[str] = []
        prev: Optional[Tuple[Tuple[str, ...], Optional[str]]] = None

        def flush() -> None:
            if not run or prev is None:
                return
            tags, href = prev
            s = escape_html("".join(run))
            for tag, (o, c) in (("code", ("<code>", "</code>")), ("strike", ("<s>", "</s>")),
                                ("italic", ("<em>", "</em>")), ("bold", ("<strong>", "</strong>"))):
                if tag in tags:
                    s = o + s + c
            if href:
                if href.startswith("img:"):
                    s = f'<img src="{escape_html(href[4:], True)}">'
                else:
                    s = f'<a href="{escape_html(href, True)}">{s}</a>'
            out.append(s)
            run.clear()

        for col in range(start_col, len(line)):
            names = self.text.tag_names(f"{ln}.{col}")
            tags = tuple(t for t in _INLINE if t in names)
            href = next((self._links[n] for n in names if n in self._links), None)
            key = (tags, href)
            if key != prev:
                flush()
                prev = key
            run.append(line[col])
        flush()
        return "".join(out)

    def to_html(self) -> str:
        if self._ph:
            return ""
        out: List[str] = []
        open_list: Optional[str] = None
        last = int(self.text.index("end-1c").split(".")[0])
        for ln in range(1, last + 1):
            line = self.text.get(f"{ln}.0", f"{ln}.end")
            m = re.match(r"^(• |\d+\. )", line)
            kind = ("ul" if m.group(0) == "• " else "ol") if m else None
            inner = self._inline(ln, len(m.group(0)) if m else 0, line)
            if kind != open_list:
                if open_list:
                    out.append(f"</{open_list}>")
                if kind:
                    out.append(f"<{kind}>")
                open_list = kind
            if kind:
                out.append(f"<li><p>{inner}</p></li>")
            elif "quote" in self.text.tag_names(f"{ln}.0"):
                out.append(f"<blockquote><p>{inner}</p></blockquote>")
            else:
                out.append(f"<p>{inner}</p>")
        if open_list:
            out.append(f"</{open_list}>")
        html = "".join(out)
        return "" if is_html_empty(html) else html
