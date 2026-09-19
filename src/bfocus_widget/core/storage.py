"""Estado local persistente: `last_seen` do badge e o "lido" de cada chamado (o que o web
guarda no localStorage). Um arquivo JSON no diretório de dados do usuário."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Optional, Union

from .config import BFocusConfig

APP_DIR = "bfocus-widget"
_MAX_READ_PER_SCOPE = 2000  # evita o arquivo crescer sem limite


def default_data_dir() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_DIR
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(base) / APP_DIR if base else home / APP_DIR
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else home / ".local" / "share") / APP_DIR


def scope_key(cfg: BFocusConfig) -> str:
    """Mesmo escopo do web: chave + usuário + cliente."""
    return f"{cfg.publishable_key}:{cfg.user.external_id}:{cfg.customer.external_id}"


class StateStore:
    FILE = "state.json"

    def __init__(self, directory: Union[str, Path, None] = None) -> None:
        self.directory = Path(directory) if directory else default_data_dir()
        self.path = self.directory / self.FILE
        self._lock = threading.Lock()

    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            # Grava num temporário e troca: um crash no meio não corrompe o arquivo.
            fd, tmp = tempfile.mkstemp(dir=str(self.directory), prefix=".state-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass  # disco cheio/somente leitura: o widget segue funcionando sem persistir

    # ── last_seen ──
    def get_last_seen(self, scope: str) -> Optional[str]:
        with self._lock:
            v = self._read().get("last_seen", {}).get(scope)
        return v if isinstance(v, str) else None

    def set_last_seen(self, scope: str, value: Optional[str]) -> None:
        with self._lock:
            data = self._read()
            ls = data.setdefault("last_seen", {})
            if value:
                ls[scope] = value
            else:
                ls.pop(scope, None)
            self._write(data)

    # ── lido por chamado (ms desde a época, como o web) ──
    def get_read(self, scope: str, ticket_id: str) -> int:
        with self._lock:
            v = self._read().get("read", {}).get(scope, {}).get(ticket_id)
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0

    def set_read(self, scope: str, ticket_id: str, ms: int) -> None:
        with self._lock:
            data = self._read()
            per = data.setdefault("read", {}).setdefault(scope, {})
            per.pop(ticket_id, None)
            per[ticket_id] = int(ms)
            while len(per) > _MAX_READ_PER_SCOPE:
                per.pop(next(iter(per)))
            self._write(data)

    def clear_scope(self, scope: str) -> None:
        with self._lock:
            data = self._read()
            data.get("last_seen", {}).pop(scope, None)
            data.get("read", {}).pop(scope, None)
            self._write(data)
