"""Formatação igual à do web (datas, bytes, ícones, HTML do composer, cores derivadas).

Datas: o web usa o idioma do navegador; aqui usamos o idioma do widget, com os mesmos
formatos do Intl (`dateStyle: short` + `timeStyle: short`) em pt-BR, en-US e es-ES.
"""
from __future__ import annotations

import re
from datetime import datetime, tzinfo
from typing import Optional

from ...core.badge import parse_instant

_MONTHS = {
    "pt_BR": ["jan.", "fev.", "mar.", "abr.", "mai.", "jun.", "jul.", "ago.", "set.", "out.", "nov.", "dez."],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    "es": ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"],
}


def to_local(iso: Optional[str], tz: Optional[tzinfo] = None) -> Optional[datetime]:
    dt = parse_instant(iso)
    return dt.astimezone(tz) if dt is not None else None


def _date(dt: datetime, locale: str) -> str:
    if locale == "en":
        return f"{dt.month}/{dt.day}/{dt.year % 100:02d}"
    if locale == "es":
        return f"{dt.day}/{dt.month}/{dt.year % 100:02d}"
    return f"{dt.day:02d}/{dt.month:02d}/{dt.year}"


def _clock(dt: datetime, locale: str) -> str:
    if locale == "en":
        h = dt.hour % 12 or 12
        return f"{h}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"
    return f"{dt.hour:02d}:{dt.minute:02d}"


def format_datetime(iso: Optional[str], locale: str, tz: Optional[tzinfo] = None) -> str:
    dt = to_local(iso, tz)
    if dt is None:
        return iso or ""
    return f"{_date(dt, locale)}, {_clock(dt, locale)}"


def format_day(iso: Optional[str], locale: str, tz: Optional[tzinfo] = None) -> str:
    dt = to_local(iso, tz)
    return _date(dt, locale) if dt is not None else (iso or "")


def format_chat_time(iso: Optional[str], locale: str, now: Optional[datetime] = None, tz: Optional[tzinfo] = None) -> str:
    """Hoje: só a hora; outro dia: data e hora (como o chat do web)."""
    dt = to_local(iso, tz)
    if dt is None:
        return ""
    ref = (now or datetime.now(dt.tzinfo)).astimezone(dt.tzinfo)
    return _clock(dt, locale) if ref.date() == dt.date() else format_datetime(iso, locale, tz)


def history_date(iso: Optional[str], locale: str, tz: Optional[tzinfo] = None) -> str:
    """`{day: 2-digit, month: short, year: numeric}` do histórico de versões."""
    dt = to_local(iso, tz)
    if dt is None:
        return ""
    mon = _MONTHS.get(locale, _MONTHS["pt_BR"])[dt.month - 1]
    if locale == "en":
        return f"{mon} {dt.day:02d}, {dt.year}"
    if locale == "es":
        return f"{dt.day:02d} {mon} {dt.year}"
    return f"{dt.day:02d} de {mon} de {dt.year}"


def pretty_bytes(n: Optional[int]) -> str:
    if n is None:
        return ""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def attachment_icon(mime: Optional[str]) -> str:
    m = (mime or "").lower()
    if not m:
        return "📄"
    if m.startswith("image/"):
        return "🖼️"
    if m.startswith("video/"):
        return "🎬"
    if m.startswith("audio/"):
        return "🎵"
    if "pdf" in m:
        return "📕"
    if "zip" in m or "compressed" in m:
        return "🗜️"
    if "sheet" in m or "excel" in m:
        return "📊"
    if "word" in m or "document" in m:
        return "📝"
    return "📄"


def is_html_empty(html: Optional[str], images_count: bool = True) -> bool:
    if not html:
        return True
    if images_count and re.search(r"<img\b", html, re.I):
        return False
    return re.sub(r"<[^>]+>", "", html).replace("&nbsp;", "").strip() == ""


def escape_html(s: str, quotes: bool = False) -> str:
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if quotes:
        s = s.replace('"', "&quot;").replace("'", "&#39;")
    return s


def text_to_html(text: str) -> str:
    """Texto do composer do chat → HTML (um <p> por linha), como o web."""
    trimmed = re.sub(r"^\s*\n+|\n+\s*$", "", text)
    if not trimmed.strip():
        return ""
    return "".join(f"<p>{escape_html(line, True)}</p>" if line.strip() else "<p></p>" for line in trimmed.split("\n"))


def initials(name: Optional[str]) -> str:
    parts = [p for p in (name or "?").strip().split() if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


def _rgb(color: str) -> tuple:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except (ValueError, IndexError):
        return 99, 102, 241  # índigo do bFocus


def mix(color: str, other: str, amount: float) -> str:
    """`amount` de `color` sobre `other` (o `color-mix`/transparência do CSS do web)."""
    a, b = _rgb(color), _rgb(other)
    return "#" + "".join(f"{round(x * amount + y * (1 - amount)):02x}" for x, y in zip(a, b))


def tint(color: str, alpha: float, bg: str = "#ffffff") -> str:
    return mix(color, bg, alpha)
