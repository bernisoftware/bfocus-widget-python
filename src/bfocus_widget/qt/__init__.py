"""Extra `[qt]`: o embed da CDN numa QWebEngineView (PySide6).

Importe este módulo ANTES de criar o `QApplication` (o QtWebEngine exige).
"""
from __future__ import annotations

from typing import Any, Optional, Tuple

from ..core.config import BFocusConfig
from ..core.widget import BFocusWidget
from .host import BFocusQtHost, EmbedView, QtDispatcher
from .widgets import BFocusLauncherButton, BFocusReleaseBadge

__all__ = ["BFocusQtHost", "EmbedView", "QtDispatcher", "BFocusLauncherButton", "BFocusReleaseBadge", "init"]


def init(config: BFocusConfig, parent: Optional[Any] = None, **kwargs: Any) -> Tuple[BFocusWidget, BFocusQtHost]:
    """Atalho: widget + host Qt ligados e a consulta já rodando."""
    widget = BFocusWidget(config, **kwargs)
    host = BFocusQtHost(widget, parent)
    widget.start()
    return widget, host
