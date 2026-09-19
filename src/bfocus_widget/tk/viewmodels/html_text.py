"""HTML sanitizado do servidor → trechos com estilo para um `tk.Text` (spec §8 e §10).

Cobre o que o editor do bFocus produz: parágrafos, quebras, negrito, itálico, riscado,
sublinhado, código, bloco de código, citação, listas (com marcador/número), links e
imagens (viram um link "[imagem]"). Conteúdo legado sem tags vira texto com as quebras.
"""
from __future__ import annotations

import dataclasses
import re
from html.parser import HTMLParser
from typing import List, Optional, Tuple

_BLOCKS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "ul", "ol", "li", "table", "tr"}
_INLINE = {
    "strong": "bold", "b": "bold", "em": "italic", "i": "italic", "s": "strike", "strike": "strike",
    "del": "strike", "u": "underline", "code": "code", "h1": "bold", "h2": "bold", "h3": "bold",
    "h4": "bold", "h5": "bold", "h6": "bold",
}


@dataclasses.dataclass(frozen=True)
class Seg:
    text: str
    tags: Tuple[str, ...] = ()
    href: Optional[str] = None


class _Parser(HTMLParser):
    def __init__(self, image_label: str) -> None:
        super().__init__(convert_charrefs=True)
        self.out: List[Seg] = []
        self._inline: List[Tuple[str, str]] = []   # (tag html, estilo)
        self._href: List[Optional[str]] = []
        self._lists: List[List] = []                # [tipo, contador]
        self._quote = 0
        self._pre = 0
        self._para_has_text: List[bool] = []
        self._image_label = image_label

    # ── saída ──
    def _text_so_far_ends_nl(self) -> bool:
        for seg in reversed(self.out):
            if seg.text:
                return seg.text.endswith("\n")
        return True  # começo do documento conta como início de linha

    def _newline(self) -> None:
        if not self._text_so_far_ends_nl():
            self.out.append(Seg("\n"))

    def _tags(self) -> Tuple[str, ...]:
        tags = [style for _, style in self._inline]
        if self._quote:
            tags.append("quote")
        if self._pre:
            tags.append("pre")
        if self._href and self._href[-1]:
            tags.append("link")
        return tuple(dict.fromkeys(tags))

    def _emit(self, text: str, extra: Tuple[str, ...] = (), href: Optional[str] = None) -> None:
        if not text:
            return
        if self._para_has_text:
            self._para_has_text[-1] = True
        link = href or (self._href[-1] if self._href else None)
        self.out.append(Seg(text, self._tags() + extra, link))

    # ── parser ──
    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = dict(attrs)
        if tag == "br":
            self.out.append(Seg("\n"))
            return
        if tag in _BLOCKS:
            self._newline()
        if tag in ("p", "div") or tag.startswith("h") and tag[1:].isdigit():
            self._para_has_text.append(False)
        if tag in ("ul", "ol"):
            self._lists.append([tag, 0])
        elif tag == "li":
            depth = max(0, len(self._lists) - 1)
            marker = "• "
            if self._lists and self._lists[-1][0] == "ol":
                self._lists[-1][1] += 1
                marker = f"{self._lists[-1][1]}. "
            self._emit("    " * depth + marker, ("li",))
        elif tag == "blockquote":
            self._quote += 1
        elif tag == "pre":
            self._pre += 1
        elif tag == "a":
            href = a.get("href") or ""
            self._href.append(href if re.match(r"^(https?:|mailto:|tel:)", href, re.I) else None)
        elif tag == "img":
            src = a.get("src") or ""
            label = f"[{a.get('alt') or self._image_label}]"
            self._emit(label, ("link",), src if re.match(r"^https?:", src, re.I) else None)
        if tag in _INLINE:
            self._inline.append((tag, _INLINE[tag]))

    def handle_endtag(self, tag: str) -> None:
        if tag in _INLINE:
            for i in range(len(self._inline) - 1, -1, -1):
                if self._inline[i][0] == tag:
                    del self._inline[i]
                    break
        if tag == "a" and self._href:
            self._href.pop()
        elif tag in ("ul", "ol") and self._lists:
            self._lists.pop()
        elif tag == "blockquote" and self._quote:
            self._quote -= 1
        elif tag == "pre" and self._pre:
            self._pre -= 1
        if tag in ("p", "div") or tag.startswith("h") and tag[1:].isdigit():
            had = self._para_has_text.pop() if self._para_has_text else True
            if not had:
                # <p></p> do editor = linha em branco (o web preserva com um espaço zero).
                self.out.append(Seg("\n"))
                return
        if tag in _BLOCKS:
            self._newline()

    def handle_data(self, data: str) -> None:
        if self._pre:
            self._emit(data)
            return
        text = re.sub(r"\s+", " ", data)
        if self._text_so_far_ends_nl():
            text = text.lstrip()
        self._emit(text)


def html_to_segments(html: Optional[str], image_label: str = "imagem") -> List[Seg]:
    if not html:
        return []
    if not re.search(r"<[a-z][^>]*>", html, re.I):
        return [Seg(html)]  # legado: texto puro, quebras preservadas
    p = _Parser(image_label)
    p.feed(html)
    p.close()
    out = p.out
    # Sem quebras sobrando no fim.
    while out and out[-1].text.strip("\n") == "" and "\n" in out[-1].text:
        out.pop()
    return out


def segments_text(segs: List[Seg]) -> str:
    return "".join(s.text for s in segs)
