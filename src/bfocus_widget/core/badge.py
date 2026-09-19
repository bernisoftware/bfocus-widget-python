"""Modelo do badge do botão (scenarios.badge), idêntico ao widget.js.

- `state(latest)`: só com o widget FECHADO. latest nulo: nada. Sem last_seen: grava e não
  acende. latest > last_seen (comparado como INSTANTE, não como texto): label "•".
- `unread(count)`: label = count (>99 = "99+"), 0 = "".
- `seen(ts)`: last_seen = ts.
- `open`/`close`: aberto, o `state` é ignorado (o embed é quem manda no badge).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

DOT = "•"

_FRACTION = re.compile(r"\.(\d+)")


def parse_instant(value: Optional[str]) -> Optional[datetime]:
    """ISO 8601 → datetime com fuso. Aceita `Z` e frações de qualquer tamanho (o
    `fromisoformat` do Python 3.9 não aceita). Sem fuso = UTC, como o servidor."""
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    s = _FRACTION.sub(lambda m: "." + (m.group(1) + "000000")[:6], s, count=1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def unread_label(count: object) -> str:
    try:
        n = int(count or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return ""
    return "99+" if n > 99 else str(n)


class BadgeModel:
    def __init__(self, last_seen: Optional[str] = None, label: str = "", is_open: bool = False) -> None:
        self.last_seen = last_seen
        self.label = label
        self.is_open = is_open

    def state(self, latest_event_at: Optional[str]) -> None:
        if self.is_open or not latest_event_at:
            return
        latest = parse_instant(latest_event_at)
        if latest is None:
            return
        seen = parse_instant(self.last_seen)
        if seen is None:
            # Primeira visita (ou valor gravado ilegível): só grava a base, sem acender.
            self.last_seen = latest_event_at
            return
        if latest > seen:
            self.label = DOT

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        self.is_open = False

    def unread(self, count: object) -> None:
        self.label = unread_label(count)

    def seen(self, latest_event_at: Optional[str]) -> None:
        if latest_event_at:
            self.last_seen = latest_event_at
