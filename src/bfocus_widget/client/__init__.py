"""Cliente da API do widget (o que o embed web faz em `widget/src/embed/api.ts` e
`chatStream.ts`), usado pela UI nativa Tk. Só stdlib."""
from .api import WidgetApi
from .chat_stream import ChatStream, is_fatal
from .sse import SseEvent, SseParser

__all__ = ["WidgetApi", "ChatStream", "is_fatal", "SseEvent", "SseParser"]
