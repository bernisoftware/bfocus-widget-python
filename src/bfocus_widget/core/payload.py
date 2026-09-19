"""Codificação canônica do contrato: payload do usuário, URL do embed e request do
launcher-state. Tem de bater byte a byte com `widgets-native/conformance/generate.mjs`."""
from __future__ import annotations

import base64
import dataclasses
import json
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union
from urllib.parse import quote

from .config import DEFAULT_API_BASE_URL, BFocusConfig

_USER_KEYS = (("externalId", "external_id"), ("name", "name"), ("email", "email"), ("phone", "phone"))
_CUSTOMER_KEYS = (
    ("externalId", "external_id"),
    ("name", "name"),
    ("document", "document"),
    ("email", "email"),
    ("phone", "phone"),
    ("website", "website"),
)


@dataclasses.dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: Dict[str, str]
    body: Union[str, bytes, None] = None


def enc(value: str) -> str:
    """RFC 3986: só [A-Za-z0-9-._~] ficam; o resto vira %XX (espaço = %20)."""
    return quote(str(value), safe="-._~")


def trim_slash(url: str) -> str:
    return url.rstrip("/")


def _pick(obj: object, keys: Sequence[Tuple[str, str]]) -> dict:
    out: dict = {}
    for json_key, attr in keys:
        v = getattr(obj, attr, None)
        if v is not None and v != "":
            out[json_key] = v
    return out


def user_payload_json(cfg: BFocusConfig) -> str:
    """JSON compacto na ordem do contrato; campos vazios somem; locale sempre presente."""
    u = _pick(cfg.user, _USER_KEYS)
    if cfg.user_hash:
        u["userHash"] = cfg.user_hash
    u["locale"] = cfg.locale or "pt_BR"
    u["customer"] = _pick(cfg.customer, _CUSTOMER_KEYS)
    return json.dumps(u, ensure_ascii=False, separators=(",", ":"))


def user_payload_base64(cfg: BFocusConfig) -> str:
    """Base64 padrão (com + / =) sobre os bytes UTF-8; o embed decodifica com atob."""
    return base64.b64encode(user_payload_json(cfg).encode("utf-8")).decode("ascii")


def api_base(cfg: BFocusConfig) -> str:
    return trim_slash(cfg.api_base_url or DEFAULT_API_BASE_URL)


def embed_origin(cfg: BFocusConfig) -> str:
    """Origem (esquema://host[:porta]) do embed: a única aceita na ponte."""
    from urllib.parse import urlsplit  # noqa: PLC0415

    p = urlsplit(cfg.embed_base_url)
    return f"{p.scheme}://{p.netloc}"


def embed_url(
    cfg: BFocusConfig,
    *,
    page: str = "embed",
    mode: str = "native",
    open: Optional[str] = None,  # noqa: A002 - nome do parâmetro do contrato
    view: Optional[str] = None,
    ids: Optional[Iterable[str]] = None,
    extra: Optional[Iterable[Tuple[str, str]]] = None,
) -> str:
    """URL do embed com os parâmetros no fragmento (`#`), na ordem do contrato."""
    p: List[Tuple[str, str]] = [
        ("host", mode),
        ("hp", "1"),
        ("client", cfg.client),
        ("key", cfg.publishable_key),
        ("user", user_payload_base64(cfg)),
        ("locale", cfg.locale or "pt_BR"),
        ("parentOrigin", cfg.parent_origin),
    ]
    if cfg.product:
        p.append(("product", cfg.product))
    if cfg.audience:
        p.append(("audience", cfg.audience))
    if api_base(cfg) != DEFAULT_API_BASE_URL:
        p.append(("api", api_base(cfg)))
    if page == "embed" and cfg.show_release_notes is False:
        p.append(("showReleaseNotes", "0"))
    if open:
        p.append(("open", open))
    if view:
        p.append(("view", view))
    id_list = list(ids or [])
    if id_list:
        p.append(("ids", ",".join(id_list)))
    for k, v in extra or ():
        p.append((k, v))
    file = "embed.html" if page == "embed" else "release-notes.html"
    return f"{trim_slash(cfg.embed_base_url)}/{file}#" + "&".join(f"{k}={enc(v)}" for k, v in p)


def widget_headers(cfg: BFocusConfig, *, json_body: bool = True, with_user: bool = True) -> Dict[str, str]:
    """Os headers de toda chamada do widget, na ordem do contrato."""
    h: Dict[str, str] = {}
    if json_body:
        h["Content-Type"] = "application/json"
    h["X-bFocus-Widget-Key"] = cfg.publishable_key
    if with_user:
        h["X-bFocus-Widget-User"] = user_payload_base64(cfg)
    h["X-bFocus-Parent-Origin"] = cfg.parent_origin
    h["X-bFocus-Client"] = cfg.client
    return h


def launcher_state_request(cfg: BFocusConfig) -> HttpRequest:
    q = []
    if cfg.product:
        q.append(f"product={enc(cfg.product)}")
    if cfg.audience:
        q.append(f"audience={enc(cfg.audience)}")
    url = f"{api_base(cfg)}/api/v1/widget/launcher-state" + ("?" + "&".join(q) if q else "")
    return HttpRequest("POST", url, widget_headers(cfg), "{}")


def open_param(target: Optional[dict]) -> Optional[str]:
    """Payload de navegação → `open=` da URL (`ticket:<id>`, `chat`, `new`; lista = sem parâmetro)."""
    if not target:
        return None
    view = target.get("view")
    if view == "ticket" and target.get("ticketId"):
        return f"ticket:{target['ticketId']}"
    if view in ("chat", "new"):
        return view
    return None


def normalize_target(target: object = None) -> dict:
    """`open(target)` aceita `None`/'list', 'new', 'chat', 'ticket:<id>', ('ticket', id)
    ou o próprio payload de `bfocus:navigate`."""
    if target is None or target == "list":
        return {"view": "list"}
    if isinstance(target, dict):
        view = target.get("view")
        if view not in ("ticket", "chat", "new", "list"):
            raise ValueError(f"alvo desconhecido: {target!r}")
        return dict(target)
    if isinstance(target, (tuple, list)) and len(target) == 2 and target[0] == "ticket":
        return {"view": "ticket", "ticketId": str(target[1])}
    if isinstance(target, str):
        if target in ("new", "chat"):
            return {"view": target}
        if target.startswith("ticket:") and len(target) > 7:
            return {"view": "ticket", "ticketId": target[7:]}
    raise ValueError(f"alvo desconhecido: {target!r} (use 'list', 'new', 'chat' ou ('ticket', id))")
