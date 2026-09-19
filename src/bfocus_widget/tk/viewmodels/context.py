"""O que todo view-model recebe: API, runner, textos, "lido" local e o destino dos erros."""
from __future__ import annotations

import dataclasses
import time
from typing import Any, Callable

from ...shared import Translator, tokens
from .base import Runner
from .read_state import ReadState


def _ignore(_err: BaseException) -> None:
    pass


def _fallback() -> str:
    return tokens()["brandFallback"]


@dataclasses.dataclass
class Ctx:
    api: Any
    runner: Runner
    t: Translator
    read: ReadState
    locale: str = "pt_BR"
    # Todo erro de chamada passa por aqui: 401 de identidade vira a tela `error_identity`.
    note_error: Callable[[BaseException], None] = _ignore
    primary: Callable[[], str] = _fallback
    clock: Callable[[], float] = time.monotonic
