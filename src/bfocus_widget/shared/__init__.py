"""Textos e cores compartilhados com o widget web.

`i18n/*.json` e `tokens.json` são CÓPIAS de `widget/src/shared/` (rode
`scripts/sync_shared.py`; não edite à mão). `NATIVE` guarda só os textos que existem apenas
nos pacotes nativos (casca: carregando, sem conexão, salvar arquivo…).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

_DIR = Path(__file__).resolve().parent
LOCALES = ("pt_BR", "en", "es")

NATIVE: Dict[str, Dict[str, str]] = {
    "pt_BR": {
        "offline_title": "Sem conexão",
        "offline_desc": "Não foi possível carregar o suporte. Verifique sua internet.",
        "save_attachment": "Salvar anexo",
        "open_support": "Abrir suporte",
        "release_badge": "Novidades e versão",
        "image": "imagem",
        "attach_too_large": "Arquivo acima de 10 MB.",
        "attach_type": "Tipo de arquivo não aceito.",
    },
    "en": {
        "offline_title": "No connection",
        "offline_desc": "We couldn't load support. Check your internet connection.",
        "save_attachment": "Save attachment",
        "open_support": "Open support",
        "release_badge": "What's new and version",
        "image": "image",
        "attach_too_large": "File larger than 10 MB.",
        "attach_type": "File type not accepted.",
    },
    "es": {
        "offline_title": "Sin conexión",
        "offline_desc": "No se pudo cargar el soporte. Verifica tu conexión a internet.",
        "save_attachment": "Guardar adjunto",
        "open_support": "Abrir soporte",
        "release_badge": "Novedades y versión",
        "image": "imagen",
        "attach_too_large": "Archivo mayor de 10 MB.",
        "attach_type": "Tipo de archivo no aceptado.",
    },
}


@lru_cache(maxsize=None)
def strings(locale: str) -> Dict[str, Dict[str, str]]:
    name = locale if locale in LOCALES else "pt_BR"
    with open(_DIR / "i18n" / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=None)
def tokens() -> Dict[str, Any]:
    with open(_DIR / "tokens.json", encoding="utf-8") as f:
        return json.load(f)


def fill(s: str, **values: Any) -> str:
    """Interpola `{nome}`: fill(t('chat_queue_size'), n=3). Chave ausente fica como está."""
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), s)


class Translator:
    """Chave ausente cai para pt_BR (spec §9); ausente também lá, devolve a própria chave."""

    def __init__(self, locale: str) -> None:
        self.locale = locale if locale in LOCALES else "pt_BR"
        self._tickets = strings(self.locale)["tickets"]
        self._rn = strings(self.locale)["releaseNotes"]
        self._pt = strings("pt_BR")

    def t(self, key: str, **values: Any) -> str:
        s = self._tickets.get(key) or self._pt["tickets"].get(key) or key
        return fill(s, **values) if values else s

    def rn(self, key: str) -> str:
        return self._rn.get(key) or self._pt["releaseNotes"].get(key) or key

    def native(self, key: str) -> str:
        return NATIVE.get(self.locale, NATIVE["pt_BR"]).get(key) or NATIVE["pt_BR"].get(key, key)

    __call__ = t
