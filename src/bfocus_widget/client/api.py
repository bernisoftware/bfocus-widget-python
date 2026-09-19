"""Cliente da API do widget (`/api/v1/widget/*`), espelho de `widget/src/embed/api.ts`.

Toda chamada leva a identidade (spec §1). Não há token de sessão. A primeira chamada do
usuário cria a conta dele: com um `BFocusWidget` ligado, cada chamada com usuário espera a
primeira consulta do launcher-state terminar (`gate`).
"""
from __future__ import annotations

import json
import mimetypes
import os
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Union
from urllib.parse import quote, urlencode

from ..core.config import BFocusConfig
from ..core.http import (
    REQUEST_TIMEOUT,
    UPLOAD_TIMEOUT,
    ApiError,
    HttpResponse,
    NetworkError,
    Transport,
    send,
    unwrap,
)
from ..core.payload import HttpRequest, api_base, launcher_state_request, widget_headers

ConfigSource = Union[BFocusConfig, Callable[[], BFocusConfig]]


def _seg(value: str) -> str:
    # Id no caminho: nada de "/" ou "?" vindo de dado externo.
    return quote(str(value), safe="")


class WidgetApi:
    def __init__(
        self,
        config: ConfigSource,
        *,
        gate: Optional[Callable[[], Any]] = None,
        transport: Optional[Transport] = None,
    ) -> None:
        self._config = config
        self._gate = gate
        self._transport: Transport = transport or send

    @classmethod
    def for_widget(cls, widget: Any, transport: Optional[Transport] = None) -> "WidgetApi":
        """Lê a identidade do widget a cada chamada (o userHash pode trocar) e respeita a
        primeira chamada serializada."""
        return cls(lambda: widget.config, gate=widget.wait_first_call, transport=transport)

    @property
    def config(self) -> BFocusConfig:
        return self._config() if callable(self._config) else self._config

    # ── transporte ────────────────────────────────────────────────────────────
    def _qs(self, **params: Optional[str]) -> str:
        items = [(k, v) for k, v in params.items() if v]
        return "?" + urlencode(items) if items else ""

    def _call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        with_user: bool = True,
        timeout: float = REQUEST_TIMEOUT,
        raw: Optional[bytes] = None,
        content_type: Optional[str] = None,
    ) -> Any:
        cfg = self.config
        if with_user and self._gate is not None:
            self._gate()
        headers = widget_headers(cfg, json_body=raw is None, with_user=with_user)
        if raw is not None and content_type:
            headers = {"Content-Type": content_type, **headers}
        data: Union[str, bytes, None]
        if raw is not None:
            data = raw
        elif method == "POST":
            data = json.dumps(body if body is not None else {}, ensure_ascii=False, separators=(",", ":"))
        else:
            data = None
        req = HttpRequest(method, api_base(cfg) + path, headers, data)
        # 1 nova tentativa automática (spec §1), só no que é idempotente (GET).
        attempts = 2 if method == "GET" else 1
        last: Optional[BaseException] = None
        for _ in range(attempts):
            try:
                resp = self._transport(req, timeout)
            except NetworkError as err:
                last = err
                continue
            if resp.status >= 500 and attempts > 1 and last is None:
                last = ApiError(resp.status, None, resp.text)
                continue
            return unwrap(resp)
        assert last is not None
        raise last

    def _get(self, path: str, **kw: Any) -> Any:
        return self._call("GET", path, **kw)

    def _post(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self._call("POST", path, body, **kw)

    # ── identidade e chamados ─────────────────────────────────────────────────
    def launcher_state(self) -> Any:
        cfg = self.config
        return unwrap(self._transport(launcher_state_request(cfg), REQUEST_TIMEOUT))

    def get_config(self) -> Dict[str, Any]:
        # /config não leva usuário: pode correr em paralelo com a primeira chamada.
        return self._get("/api/v1/widget/config", with_user=False)

    def session(self) -> Dict[str, Any]:
        return self._post("/api/v1/widget/session")

    def list_my_tickets(self) -> List[Dict[str, Any]]:
        return self._get("/api/v1/widget/tickets?page_size=50") or []

    def create_ticket(
        self,
        *,
        type: str,  # noqa: A002 - nome do campo da API
        title: str,
        priority: str,
        description: Optional[str] = None,
        department_id: Optional[str] = None,
        attachments: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"type": type, "title": title, "priority": priority}
        if description:
            body["description"] = description
        if department_id:
            body["department_id"] = department_id
        body["attachments"] = list(attachments or [])
        if self.config.product:
            body["product_slug"] = self.config.product  # classifica o chamado no produto
        return self._post("/api/v1/widget/tickets", body)

    def get_ticket(self, ticket_id: str) -> Dict[str, Any]:
        return self._get(f"/api/v1/widget/tickets/{_seg(ticket_id)}")

    def list_ticket_conversations(self, ticket_id: str) -> Optional[List[Dict[str, Any]]]:
        """Transcrições do chat (mais nova primeiro). `None` = servidor sem a rota (404)."""
        try:
            res = self._get(f"/api/v1/widget/tickets/{_seg(ticket_id)}/conversations")
        except ApiError as err:
            if err.status == 404:
                return None
            raise
        if isinstance(res, list):
            return res
        wrapped = res.get("conversations") if isinstance(res, dict) else None
        return wrapped if isinstance(wrapped, list) else []

    def add_interaction(
        self, ticket_id: str, content: str, attachments: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Responder um RESOLVIDO reabre; fechado/cancelado → 409 TICKET_CLOSED."""
        return self._post(
            f"/api/v1/widget/tickets/{_seg(ticket_id)}/interactions",
            {"content": content, "attachments": list(attachments or [])},
        )

    def submit_satisfaction(self, ticket_id: str, rating: int, comment: Optional[str] = None) -> None:
        """409 TICKET_NOT_RESOLVED quando o chamado ainda não foi resolvido."""
        self._post(
            f"/api/v1/widget/tickets/{_seg(ticket_id)}/satisfaction",
            {"rating": int(rating), "comment": (comment or "").strip() or None},
        )

    def download_attachment(self, attachment_id: str) -> Dict[str, Any]:
        """URL pré-assinada que baixa com o NOME ORIGINAL: `{url, filename, content_type}`."""
        return self._get(f"/api/v1/widget/attachments/{_seg(attachment_id)}/download")

    # ── release notes ─────────────────────────────────────────────────────────
    def list_release_notes(self) -> List[Dict[str, Any]]:
        """Fila do splash dos chamados, POR USUÁRIO e já com a regra do banner (as que exigem
        ciência + a mais recente que não exige). O `product` declarado cria o vínculo
        cliente↔produto; sem ele, vale o único produto ativo da empresa."""
        cfg = self.config
        return self._get("/api/v1/widget/release-notes" + self._qs(product=cfg.product, audience=cfg.audience)) or []

    def mark_release_note_read(self, note_id: str) -> None:
        """Legado (bundles antigos): ciência quando a release exige, senão "vista". O splash
        usa `mark_release_note_seen` / `acknowledge_release_note`."""
        self._post(f"/api/v1/widget/release-notes/{_seg(note_id)}/mark-read")

    def release_notes_changelog(self) -> Dict[str, Any]:
        cfg = self.config
        return self._get("/api/v1/widget/release-notes/changelog" + self._qs(product=cfg.product, audience=cfg.audience)) or {}

    def release_notes_pending(self) -> List[Dict[str, Any]]:
        cfg = self.config
        return self._get("/api/v1/widget/release-notes/pending" + self._qs(product=cfg.product, audience=cfg.audience)) or []

    def mark_release_note_seen(self, note_id: str) -> None:
        self._post(f"/api/v1/widget/release-notes/{_seg(note_id)}/seen")

    def acknowledge_release_note(self, note_id: str) -> None:
        """409 ACK_NOT_REQUIRED quando a release não exige ciência (para essas, `/seen`)."""
        self._post(f"/api/v1/widget/release-notes/{_seg(note_id)}/acknowledge")

    # ── chat ao vivo ──────────────────────────────────────────────────────────
    def chat_availability(self) -> Dict[str, Any]:
        return self._get("/api/v1/widget/chat/availability" + self._qs(product=self.config.product))

    def chat_active(self) -> Optional[Dict[str, Any]]:
        return self._get("/api/v1/widget/chat/conversations/active")

    def chat_create(
        self,
        content: str,
        attachments: Optional[Iterable[Dict[str, Any]]] = None,
        *,
        department_id: Optional[str] = None,
        origin_url: Optional[str] = None,
        continue_ticket_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "content": content,
            "attachments": list(attachments or []),
            "origin_url": origin_url,
            "continue_ticket_id": continue_ticket_id,
        }
        if department_id:
            body["department_id"] = department_id
        if self.config.product:
            body["product_slug"] = self.config.product
        return self._post("/api/v1/widget/chat/conversations", body)

    def chat_get(self, conversation_id: str) -> Dict[str, Any]:
        return self._get(f"/api/v1/widget/chat/conversations/{_seg(conversation_id)}")

    def chat_send(self, conversation_id: str, content: str, attachments: Optional[Iterable[Dict[str, Any]]] = None) -> Dict[str, Any]:
        return self._post(
            f"/api/v1/widget/chat/conversations/{_seg(conversation_id)}/messages",
            {"content": content, "attachments": list(attachments or [])},
        )

    def chat_close(self, conversation_id: str) -> Dict[str, Any]:
        return self._post(f"/api/v1/widget/chat/conversations/{_seg(conversation_id)}/close")

    def chat_handoff(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """"Falar com um atendente". Devolve a conversa quando ela vem no corpo."""
        res = self._post(f"/api/v1/widget/chat/conversations/{_seg(conversation_id)}/handoff")
        if isinstance(res, dict):
            conv = res.get("conversation")
            if isinstance(conv, dict) and isinstance(conv.get("id"), str):
                return conv
            if isinstance(res.get("id"), str) and isinstance(res.get("status"), str):
                return res
        return None

    def chat_stream_ticket(self, conversation_id: str) -> Dict[str, Any]:
        return self._post("/api/v1/widget/chat/stream-ticket", {"conversation_id": conversation_id})

    def chat_events(self, conversation_id: str, after: int) -> Dict[str, Any]:
        return self._get(f"/api/v1/widget/chat/conversations/{_seg(conversation_id)}/events?after={int(after)}")

    def chat_stream_url(self, ticket: str, last_event_id: Optional[int]) -> str:
        """Só o ticket (opaco, uso único) vai na URL: a identidade nunca."""
        params = [("ticket", ticket)]
        if last_event_id is not None:
            params.append(("last_event_id", str(last_event_id)))
        return f"{api_base(self.config)}/api/v1/stream?{urlencode(params)}"

    # ── arquivos ──────────────────────────────────────────────────────────────
    def upload(
        self,
        path: Union[str, "os.PathLike[str]", None] = None,
        *,
        data: Optional[bytes] = None,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Envia um arquivo (multipart) → `{url, filename, size, mime}`. 413 = acima de
        10 MB, 415 = tipo não aceito."""
        if path is not None:
            p = Path(path)
            data = p.read_bytes()
            filename = filename or p.name
        if data is None or not filename:
            raise ValueError("upload precisa de path ou de data + filename")
        ctype = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        boundary = "----bfocus" + uuid.uuid4().hex
        safe_name = filename.replace('"', "%22").replace("\r", "").replace("\n", "")
        body = b"".join([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'.encode("utf-8"),
            f"Content-Type: {ctype}\r\n\r\n".encode(),
            data,
            f"\r\n--{boundary}--\r\n".encode(),
        ])
        return self._call(
            "POST", "/api/v1/widget/upload", raw=body,
            content_type=f"multipart/form-data; boundary={boundary}", timeout=UPLOAD_TIMEOUT,
        )

    def fetch_bytes(self, url: str, timeout: float = UPLOAD_TIMEOUT) -> bytes:
        """Baixa uma URL pré-assinada/pública (anexo, logo, avatar). Sem os headers do
        widget: a URL já é a autorização."""
        resp: HttpResponse = self._transport(HttpRequest("GET", url, {}), timeout)
        if not 200 <= resp.status < 300:
            raise ApiError(resp.status, None, resp.text)
        return resp.body

    def download_to(self, url: str, dest: Union[str, "os.PathLike[str]"]) -> Path:
        target = Path(dest)
        target.write_bytes(self.fetch_bytes(url))
        return target
