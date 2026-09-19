"""View-models da UI Tk: toda a regra das telas, sem tkinter (testáveis sem janela)."""
from .app import AppVM, view_for
from .base import Observable, Periodic, Runner, SyncRunner
from .chat import (
    ChatScreenVM,
    ChatSessionVM,
    ComposerVM,
    CsatVM,
    closing_outcome,
    message_rows,
    status_label,
)
from .context import Ctx
from .read_state import ReadState, last_event_of
from .release_notes import BannerVM, HistoryVM, SplashVM
from .tickets import NewTicketVM, TicketDetailVM, TicketsListVM

__all__ = [
    "AppVM", "view_for", "Observable", "Periodic", "Runner", "SyncRunner",
    "ChatScreenVM", "ChatSessionVM", "ComposerVM", "CsatVM", "closing_outcome", "message_rows", "status_label",
    "Ctx", "ReadState", "last_event_of", "BannerVM", "HistoryVM", "SplashVM",
    "NewTicketVM", "TicketDetailVM", "TicketsListVM",
]
