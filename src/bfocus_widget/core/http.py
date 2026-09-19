"""HTTP mínimo sobre urllib (stdlib), com o envelope `{code, data, message}` da API."""
from __future__ import annotations

import dataclasses
import http.client
import json
import socket
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional, Union

from .payload import HttpRequest

REQUEST_TIMEOUT = 15.0   # chamada comum (spec §1)
UPLOAD_TIMEOUT = 120.0   # upload de arquivo
IDENTITY_ERRORS = ("WIDGET_USER_HASH_INVALID", "WIDGET_VERIFIED_SESSION_REQUIRED")


class NetworkError(Exception):
    """Sem resposta: `timeout` ou `network` (offline, DNS, conexão derrubada). Nunca 4xx/5xx."""

    def __init__(self, kind: str, cause: Optional[BaseException] = None) -> None:
        super().__init__("Request timed out" if kind == "timeout" else "Network request failed")
        self.kind = kind
        self.__cause__ = cause


class ApiError(Exception):
    """Erro HTTP da API. `code` é o código de máquina (`message`/`detail` do envelope)."""

    def __init__(self, status: int, code: Optional[str], text: str = "") -> None:
        super().__init__(f"{status}: {code or text[:200]}")
        self.status = status
        self.code = code
        self.text = text

    @property
    def is_identity(self) -> bool:
        return self.status == 401 and self.code in IDENTITY_ERRORS


def is_identity_error(err: BaseException) -> bool:
    return isinstance(err, ApiError) and err.is_identity


@dataclasses.dataclass
class HttpResponse:
    status: int
    headers: Dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text) if self.body else None


def error_code_from_text(text: str) -> Optional[str]:
    try:
        body = json.loads(text)
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    for field in ("message", "detail"):
        v = body.get(field)
        if isinstance(v, str) and v:
            return v
    return None


_opener = urllib.request.build_opener()

Transport = Callable[[HttpRequest, float], HttpResponse]


def send(req: HttpRequest, timeout: float = REQUEST_TIMEOUT) -> HttpResponse:
    """Faz a chamada. Status HTTP de erro volta como resposta (não exceção); só falta de
    resposta vira `NetworkError`. O corpo pode ser texto (JSON) ou bytes (multipart)."""
    body: Union[str, bytes, None] = req.body
    data = body.encode("utf-8") if isinstance(body, str) else body
    r = urllib.request.Request(req.url, data=data, method=req.method, headers=dict(req.headers))
    try:
        with _opener.open(r, timeout=timeout) as resp:
            return HttpResponse(resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read())
    except urllib.error.HTTPError as e:
        try:
            payload = e.read()
        except Exception:  # noqa: BLE001
            payload = b""
        headers = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
        return HttpResponse(e.code, headers, payload)
    except urllib.error.URLError as e:
        kind = "timeout" if isinstance(e.reason, (socket.timeout, TimeoutError)) else "network"
        raise NetworkError(kind, e) from e
    except (socket.timeout, TimeoutError) as e:
        raise NetworkError("timeout", e) from e
    except (OSError, http.client.HTTPException) as e:
        raise NetworkError("network", e) from e


def unwrap(resp: HttpResponse) -> Any:
    """Envelope `{data}` → data; status de erro → `ApiError`."""
    if not 200 <= resp.status < 300:
        raise ApiError(resp.status, error_code_from_text(resp.text), resp.text)
    if not resp.body:
        return None
    body = json.loads(resp.text)
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body
