"""Push (BRIEF §4.4). No desktop não há FCM, mas o contrato é o mesmo: quem tiver o token
(ex.: app híbrido) registra, e `handle_push(data)` decide se a notificação é do bFocus."""
from __future__ import annotations

import dataclasses
import json
from typing import Any, Mapping, Optional

from .config import BFocusConfig
from .payload import HttpRequest, api_base, widget_headers


@dataclasses.dataclass(frozen=True)
class PushResult:
    handled: bool
    navigate: Optional[dict]


def push_register_request(cfg: BFocusConfig, token: str, platform: str) -> HttpRequest:
    body = {"token": token, "platform": platform, "app_id": cfg.app_id.lower()}  # a API normaliza para minúsculas
    return HttpRequest(
        "POST",
        f"{api_base(cfg)}/api/v1/widget/push/devices",
        widget_headers(cfg),
        json.dumps(body, ensure_ascii=False, separators=(",", ":")),
    )


def push_unregister_request(cfg: BFocusConfig, token: str) -> HttpRequest:
    return HttpRequest(
        "POST",
        f"{api_base(cfg)}/api/v1/widget/push/devices/unregister",
        widget_headers(cfg),
        json.dumps({"token": token}, ensure_ascii=False, separators=(",", ":")),
    )


def handle_push(data: Optional[Mapping[str, Any]]) -> PushResult:
    """`data` do FCM → se é do bFocus e para onde navegar (payload do `bfocus:navigate`)."""
    if not data or str(data.get("bfocus")) not in ("1", "True", "true"):
        return PushResult(False, None)
    kind = data.get("type")
    ticket_id = data.get("ticket_id")
    if kind == "chat.message":
        nav: dict = {"view": "chat"}
        if data.get("conversation_id"):
            nav["conversationId"] = str(data["conversation_id"])
        return PushResult(True, nav)
    if kind in ("ticket.reply", "ticket.status") and ticket_id:
        return PushResult(True, {"view": "ticket", "ticketId": str(ticket_id)})
    # Tipo novo (o protocolo só cresce): abre a lista em vez de ignorar.
    return PushResult(True, {"view": "list"})
