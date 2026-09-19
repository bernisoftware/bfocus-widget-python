"""bFocus Widget para apps desktop em Python.

- `bfocus_widget` (núcleo, só stdlib): `BFocusWidget`, `BFocusConfig`, badge, pílula, modo
  navegador e notificação do sistema.
- `bfocus_widget.qt` (extra `[qt]`): host com QWebEngineView + botão e pílula em Qt.
- `bfocus_widget.tk` (extra `[tk]`): UI nativa completa em Tk.
- `bfocus_widget.client`: cliente da API do widget (usado pela UI Tk).
"""
from ._version import __version__
from .core import (
    CLIENT,
    ApiError,
    BFocusConfig,
    BFocusWidget,
    Customer,
    NetworkError,
    ReleaseNotesState,
    User,
    WidgetHost,
    init,
)

__all__ = [
    "__version__",
    "CLIENT",
    "ApiError",
    "BFocusConfig",
    "BFocusWidget",
    "Customer",
    "NetworkError",
    "ReleaseNotesState",
    "User",
    "WidgetHost",
    "init",
]
