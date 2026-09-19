"""Contrato entre o núcleo (`BFocusWidget`) e quem mostra as telas.

- `bfocus_widget.qt.BFocusQtHost`: WebView (embed da CDN).
- `bfocus_widget.tk.BFocusTkHost`: UI nativa em Tk.
- Sem host (ou `available = False`): modo navegador.

O núcleo chama estes métodos sempre pelo `dispatcher` do host (thread da interface).
"""
from __future__ import annotations

from typing import Callable, List, Optional

# Mensagens que o embed pode mandar (Host Protocol v1 §3). Tipo desconhecido é ignorado.
EMBED_MESSAGE_TYPES = frozenset({
    "bfocus:ready",
    "bfocus:close",
    "bfocus:unread",
    "bfocus:seen",
    "bfocus:branding",
    "bfocus:error",
    "bfocus:openExternal",
    "bfocus:download",
    "bfocus:rn:branding",
    "bfocus:rn:done",
    "bfocus:rn:closeHistory",
})

Dispatcher = Callable[[Callable[[], None]], None]


def direct_dispatcher(fn: Callable[[], None]) -> None:
    fn()


class WidgetHost:
    """Base com implementações vazias: um host só sobrescreve o que suporta."""

    available: bool = True

    def show_tickets(self, target: Optional[dict]) -> None:
        """Mostra o widget de chamados. `target` = payload de `bfocus:navigate`, ou `None`
        para só mostrar (sem trocar a tela em que o usuário estava)."""

    def hide_tickets(self) -> None:
        """Esconde sem destruir (a tela é preservada para reabrir rápido)."""

    def show_banner(self, ids: List[str]) -> None:
        """Banner de release notes: modal que o usuário não fecha por gesto."""

    def hide_banner(self) -> None:
        pass

    def show_history(self) -> None:
        pass

    def hide_history(self) -> None:
        pass

    def reset(self) -> None:
        """logout(): descarta as telas e os dados locais da origem do embed."""

    def reload(self) -> None:
        """A identidade mudou (hash novo): recarrega as telas com o payload novo."""
