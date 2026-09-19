"""Extra `[tk]`: UI nativa completa em Tk (widget-behavior-spec.md), para onde não há WebView.

Os view-models (`bfocus_widget.tk.viewmodels`) não importam tkinter e são testáveis sem
janela; as telas ficam em `bfocus_widget.tk.views`. `tkinterweb` é opcional (HTML rico no
conteúdo das novidades); sem ele, o HTML é desenhado num `tk.Text` com estilos.
"""
from __future__ import annotations

from typing import Any

__all__ = ["BFocusTkHost", "TkRunner", "BFocusTkLauncherButton", "BFocusTkReleaseBadge", "init"]


def __getattr__(name: str) -> Any:
    # Import tardio: usar só os view-models não carrega o tkinter.
    if name in ("BFocusTkHost", "TkRunner", "init"):
        from . import host

        return getattr(host, name)
    if name in ("BFocusTkLauncherButton", "BFocusTkReleaseBadge"):
        from . import widgets

        return getattr(widgets, name)
    raise AttributeError(name)
