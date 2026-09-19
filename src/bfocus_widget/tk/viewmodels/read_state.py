"""Estado local de "lido" por chamado (o `readState.ts` do web, gravado no StateStore)."""
from __future__ import annotations

from typing import Any, Iterable, Optional

from ...core.badge import parse_instant
from ...core.storage import StateStore


def _ms(iso: Optional[str]) -> int:
    dt = parse_instant(iso)
    return int(dt.timestamp() * 1000) if dt is not None else 0


class ReadState:
    def __init__(self, store: StateStore, scope: str) -> None:
        self.store = store
        self.scope = scope

    def last_read(self, ticket_id: str) -> int:
        return self.store.get_read(self.scope, ticket_id)

    def mark_read(self, ticket_id: str, at_iso: Optional[str]) -> None:
        ms = _ms(at_iso)
        if ms and ms > self.last_read(ticket_id):
            self.store.set_read(self.scope, ticket_id, ms)

    def is_unread(self, ticket_id: str, last_event_at: Optional[str], created_at: Optional[str]) -> bool:
        """Não lido: `last_event_at` depois da última leitura local. Nunca aberto: só quando
        `last_event_at` passa 1 s de `created_at` (chamado novo sem resposta não acende)."""
        last = _ms(last_event_at)
        if not last:
            return False
        seen = self.last_read(ticket_id)
        if seen > 0:
            return last > seen
        created = _ms(created_at)
        if not created:
            return False
        return last > created + 1000


def last_event_of(data: Any, conversations: Optional[Iterable[Any]]) -> Optional[str]:
    """Evento mais recente visto no detalhe (interações + transcrição do chat), para o "não
    lido" da lista apagar também quando a novidade veio pelo chat."""
    candidates = [((data or {}).get("ticket") or {}).get("updated_at")]
    candidates += [i.get("created_at") for i in (data or {}).get("interactions") or []]
    for c in conversations or []:
        candidates += [m.get("created_at") for m in c.get("messages") or []]
    best, best_ms = None, 0
    for iso in candidates:
        ms = _ms(iso)
        if ms > best_ms:
            best, best_ms = iso, ms
    return best
