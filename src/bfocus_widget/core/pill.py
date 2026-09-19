"""Pílula de versão a partir de `release_notes` do launcher-state (scenarios.pill)."""
from __future__ import annotations

import dataclasses
import re
from typing import Any, Mapping, Optional, Tuple

NO_VERSION = "—"


@dataclasses.dataclass(frozen=True)
class ReleaseNotesState:
    """O que `on_release_notes_changed` recebe: `label` (`v4.2.0` ou `—`), `dot` (novidade
    não vista) e `banner_ids` (fila do banner, na ordem). `color` é a cor da marca do tenant."""

    label: str = NO_VERSION
    dot: bool = False
    banner_ids: Tuple[str, ...] = ()
    color: Optional[str] = None

    def as_dict(self) -> dict:
        # Forma do contrato (camelCase), útil para quem serializa.
        return {"label": self.label, "dot": self.dot, "bannerIds": list(self.banner_ids)}


def version_label(version: Optional[str]) -> str:
    if not version or not str(version).strip():
        return NO_VERSION
    return "v" + re.sub(r"^[vV]", "", str(version).strip())


def pill_from(release_notes: Optional[Mapping[str, Any]], color: Optional[str] = None) -> ReleaseNotesState:
    rn = release_notes or {}
    ids = tuple(str(i) for i in (rn.get("banner_ids") or []) if i)
    return ReleaseNotesState(
        label=version_label(rn.get("badge_version")),
        dot=bool(rn.get("has_unseen")),
        banner_ids=ids,
        color=color,
    )
