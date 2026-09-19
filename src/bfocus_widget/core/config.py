"""Configuração do widget (BRIEF §3). Só stdlib."""
from __future__ import annotations

import dataclasses
import locale as _pylocale
import os
import subprocess
import sys
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlsplit

from .._version import __version__

DEFAULT_API_BASE_URL = "https://api.bfocus.com.br"
DEFAULT_EMBED_BASE_URL = "https://widget.bfocus.com.br/v1"
CLIENT_FAMILY = "python"
CLIENT = f"{CLIENT_FAMILY}/{__version__}"
AUDIENCES = ("external", "internal", "both")
LOCALES = ("pt_BR", "en", "es")

# Segredos que nunca podem ir num app (BRIEF §6): o pacote recusa na hora.
_SECRET_PREFIXES = ("bf_whs_", "bf_live_", "bf_sk_")
# http:// só vale para testes locais (BRIEF §6).
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def normalize_locale(value: Optional[str]) -> Optional[str]:
    """`pt*` → `pt_BR`, `es*` → `es`, resto → `en` (BRIEF §3)."""
    if value is None:
        return None
    v = str(value).strip().replace("-", "_").lower()
    if not v:
        return None
    if v.startswith("pt"):
        return "pt_BR"
    if v.startswith("es"):
        return "es"
    return "en"


def detect_locale() -> str:
    """Idioma do sistema. Apps GUI no macOS/Windows costumam não ter LANG, daí as consultas
    específicas. Sem nada detectável, fica o padrão do widget (`pt_BR`)."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        val = os.environ.get(var)
        if val and val not in ("C", "POSIX") and not val.startswith("C."):
            return normalize_locale(val) or "pt_BR"
    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["defaults", "read", "-g", "AppleLocale"], capture_output=True, text=True, timeout=1,
            ).stdout.strip()
            if out:
                return normalize_locale(out) or "pt_BR"
        except (OSError, subprocess.SubprocessError):
            pass
    if os.name == "nt":
        try:
            import ctypes  # noqa: PLC0415

            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
            name = _pylocale.windows_locale.get(lang_id)
            if name:
                return normalize_locale(name) or "pt_BR"
        except Exception:  # noqa: BLE001
            pass
    try:
        loc = _pylocale.getlocale()[0]
    except (ValueError, TypeError):
        loc = None
    return normalize_locale(loc) or "pt_BR"


def _pick(src: Mapping[str, Any], *names: str) -> Optional[str]:
    for n in names:
        v = src.get(n)
        if v is not None and v != "":
            return str(v)
    return None


def _clean(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v)
    return s if s != "" else None


@dataclasses.dataclass(frozen=True)
class User:
    """Usuário final do integrador (`user` do init)."""

    external_id: str
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

    @classmethod
    def coerce(cls, value: Any) -> "User":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls(
                external_id=_pick(value, "externalId", "external_id") or "",
                name=_pick(value, "name"),
                email=_pick(value, "email"),
                phone=_pick(value, "phone"),
            )
        raise TypeError("user deve ser User ou dict com externalId")


@dataclasses.dataclass(frozen=True)
class Customer:
    """Empresa/cliente do usuário (`customer` do init)."""

    external_id: str
    name: Optional[str] = None
    document: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None

    @classmethod
    def coerce(cls, value: Any) -> "Customer":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls(
                external_id=_pick(value, "externalId", "external_id") or "",
                name=_pick(value, "name"),
                document=_pick(value, "document"),
                email=_pick(value, "email"),
                phone=_pick(value, "phone"),
                website=_pick(value, "website"),
            )
        raise TypeError("customer deve ser Customer ou dict com externalId")


def validate_base_url(url: str, field: str) -> str:
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.netloc:
        return url
    if parts.scheme == "http" and (parts.hostname or "") in _LOCAL_HOSTS:
        return url
    raise ValueError(f"{field}: use https:// (http:// só para 127.0.0.1/localhost em testes): {url!r}")


@dataclasses.dataclass
class BFocusConfig:
    """Configuração do `init` (BRIEF §3). Os nomes seguem o Python (snake_case);
    `BFocusConfig.from_dict` aceita também os nomes do contrato (camelCase)."""

    publishable_key: str
    app_id: str
    user: User
    customer: Customer
    user_hash: Optional[str] = None
    # Função (sem argumentos) que devolve o userHash vindo do SEU servidor. Pode ser síncrona
    # ou async; roda fora da thread da interface. Chamada de novo após WIDGET_USER_HASH_INVALID.
    user_hash_provider: Optional[Callable[[], Any]] = None
    product: Optional[str] = None
    audience: Optional[str] = None
    locale: Optional[str] = None
    show_release_notes: bool = True
    auto_show_release_banner: bool = True
    api_base_url: str = DEFAULT_API_BASE_URL
    embed_base_url: str = DEFAULT_EMBED_BASE_URL
    poll_interval_seconds: float = 60
    notifications: bool = False
    # Onde gravar o last_seen e o "lido" dos chamados; padrão: diretório de dados do usuário.
    storage_dir: Optional[str] = None
    # Família/versão do header X-bFocus-Client. Não altere: existe para os testes de conformidade.
    client: str = CLIENT

    def __post_init__(self) -> None:
        self.user = User.coerce(self.user)
        self.customer = Customer.coerce(self.customer)
        self.user_hash = _clean(self.user_hash)
        self.product = _clean(self.product)
        self.audience = _clean(self.audience)
        self.locale = normalize_locale(self.locale) or detect_locale()
        key = (self.publishable_key or "").strip()
        if not key.startswith("bf_pk_"):
            if key.startswith(_SECRET_PREFIXES):
                raise ValueError("publishable_key: isso é um SEGREDO do bFocus e nunca pode ir no app. Use a chave pública bf_pk_…")
            raise ValueError("publishable_key deve ser a chave pública bf_pk_…")
        self.publishable_key = key
        if self.user_hash and self.user_hash.startswith(_SECRET_PREFIXES):
            raise ValueError("user_hash recebeu um segredo. O hash é calculado no SEU servidor (sign_widget_identity).")
        self.app_id = (self.app_id or "").strip()
        if not self.app_id:
            raise ValueError("app_id é obrigatório no desktop (vira a origem app://<app_id>)")
        if not self.user.external_id:
            raise ValueError("user.external_id é obrigatório")
        if not self.customer.external_id:
            raise ValueError("customer.external_id é obrigatório")
        if self.audience is not None and self.audience not in AUDIENCES:
            raise ValueError(f"audience deve ser um de {AUDIENCES}")
        self.api_base_url = validate_base_url(self.api_base_url or DEFAULT_API_BASE_URL, "api_base_url")
        self.embed_base_url = validate_base_url(self.embed_base_url or DEFAULT_EMBED_BASE_URL, "embed_base_url")
        if float(self.poll_interval_seconds) <= 0:
            raise ValueError("poll_interval_seconds deve ser > 0")

    @property
    def parent_origin(self) -> str:
        return "app://" + self.app_id.lower()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BFocusConfig":
        """Aceita as chaves do contrato (camelCase, como em scenarios.json) ou snake_case."""
        aliases = {
            "publishableKey": "publishable_key",
            "appId": "app_id",
            "userHash": "user_hash",
            "userHashProvider": "user_hash_provider",
            "showReleaseNotes": "show_release_notes",
            "autoShowReleaseBanner": "auto_show_release_banner",
            "apiBaseUrl": "api_base_url",
            "embedBaseUrl": "embed_base_url",
            "pollIntervalSeconds": "poll_interval_seconds",
            "storageDir": "storage_dir",
        }
        names = {f.name for f in dataclasses.fields(cls)}
        kwargs: dict[str, Any] = {}
        for k, v in data.items():
            name = aliases.get(k, k)
            if name in names:
                kwargs[name] = v
        return cls(**kwargs)
