"""Núcleo sem UI e sem dependências (só stdlib)."""
from .badge import DOT, BadgeModel, parse_instant, unread_label
from .config import (
    CLIENT,
    DEFAULT_API_BASE_URL,
    DEFAULT_EMBED_BASE_URL,
    BFocusConfig,
    Customer,
    User,
    detect_locale,
    normalize_locale,
)
from .host import EMBED_MESSAGE_TYPES, WidgetHost
from .http import ApiError, HttpResponse, NetworkError, is_identity_error
from .payload import (
    HttpRequest,
    embed_url,
    enc,
    launcher_state_request,
    normalize_target,
    open_param,
    user_payload_base64,
    user_payload_json,
    widget_headers,
)
from .pill import ReleaseNotesState, pill_from
from .push import PushResult, handle_push, push_register_request, push_unregister_request
from .storage import StateStore, scope_key
from .widget import BFocusWidget, init

__all__ = [
    "DOT", "BadgeModel", "parse_instant", "unread_label",
    "CLIENT", "DEFAULT_API_BASE_URL", "DEFAULT_EMBED_BASE_URL", "BFocusConfig", "Customer", "User",
    "detect_locale", "normalize_locale",
    "EMBED_MESSAGE_TYPES", "WidgetHost",
    "ApiError", "HttpResponse", "NetworkError", "is_identity_error",
    "HttpRequest", "embed_url", "enc", "launcher_state_request", "normalize_target", "open_param",
    "user_payload_base64", "user_payload_json", "widget_headers",
    "ReleaseNotesState", "pill_from",
    "PushResult", "handle_push", "push_register_request", "push_unregister_request",
    "StateStore", "scope_key",
    "BFocusWidget", "init",
]
