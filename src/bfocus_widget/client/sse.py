"""Parser de Server-Sent Events (formato do WHATWG, o mesmo que o EventSource entende)."""
from __future__ import annotations

import dataclasses
from typing import List, Optional


@dataclasses.dataclass(frozen=True)
class SseEvent:
    event: str
    data: str
    id: Optional[str]


class SseParser:
    """Recebe linha a linha (sem o fim de linha). Comentários (`: ping`) são o heartbeat do
    servidor e não viram evento. `last_id` persiste entre eventos, como no EventSource."""

    def __init__(self) -> None:
        self._data: List[str] = []
        self._event: Optional[str] = None
        self.last_id: Optional[str] = None

    def feed_line(self, line: str) -> Optional[SseEvent]:
        if line == "":
            if not self._data:
                self._event = None
                return None
            ev = SseEvent(self._event or "message", "\n".join(self._data), self.last_id)
            self._data = []
            self._event = None
            return ev
        if line.startswith(":"):
            return None
        field, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        if field == "data":
            self._data.append(value)
        elif field == "event":
            self._event = value
        elif field == "id":
            if "\0" not in value:
                self.last_id = value
        return None
