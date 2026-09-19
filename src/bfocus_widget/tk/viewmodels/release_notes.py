"""Release notes: splash dentro dos chamados (§4), banner com ciência e histórico (§6)."""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from .base import Observable, Periodic
from .context import Ctx
from .formatting import history_date

SPLASH_REFRESH_MS = 60_000
GATE_BOTTOM_PX = 8     # libera o botão a 8 px do fim
GATE_NO_SCROLL_PX = 4  # conteúdo que não rola (diferença até 4 px) libera na hora


def semver_key(version: Optional[str]) -> tuple:
    parts = re.sub(r"^[vV]", "", version or "").split(".")
    nums = []
    for p in parts[:3]:
        m = re.match(r"\d+", p)
        nums.append(int(m.group(0)) if m else 0)
    return tuple(nums + [0] * (3 - len(nums)))


def reached_end(top: float, visible: float, total: float) -> bool:
    """Trava de rolagem: não rola (≤ 4 px) ou chegou a 8 px do fim."""
    return total - visible <= GATE_NO_SCROLL_PX or top + visible >= total - GATE_BOTTOM_PX


class SplashVM(Observable):
    """Novidades POR USUÁRIO dentro dos chamados. A fila vem pronta do servidor
    (`GET /widget/release-notes`), com a regra do banner: as que exigem ciência + a mais
    recente que não exige.

    - Exige ciência: como a ciência interna do bFocus (ReleaseNoteAckGate) — cobre o
      widget, uma por vez (mais antiga primeiro, "1 / n"), sem fechar nem pular, botão só
      depois de ler até o fim → `/acknowledge`.
    - Não exige: cartões dispensáveis ("marcar como lida" → `/seen`, ou "Pular por agora",
      que some com eles até o widget ser reaberto).

    Confere de novo a cada 60 s e quando o widget volta ao foco: uma release com ciência
    publicada depois de um "Pular" trava na hora."""

    def __init__(self, ctx: Ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self.notes: List[Dict[str, Any]] = []
        self.loading = True
        self.expanded: Optional[str] = None
        self.marking = False
        self.dismissed = False  # "Pular por agora" nesta abertura: só vale para o que não exige ciência
        self.acking = False
        self.ack_failed = False
        self.at_bottom = False
        self._gate_id: Optional[str] = None
        self._periodic = Periodic(ctx.runner, SPLASH_REFRESH_MS, self.refresh)

    def start(self) -> None:
        self._periodic.start(immediate=True)

    def stop(self) -> None:
        self._periodic.stop()

    def refresh(self) -> None:
        def ok(notes: Any) -> None:
            self.notes = list(notes or [])
            self.loading = False
            self._sync_gate()
            self.changed()

        def err(e: BaseException) -> None:
            self.ctx.note_error(e)  # falha de rede: fica a fila que já estava
            self.loading = False
            self.changed()

        self.ctx.runner.submit(self.ctx.api.list_release_notes, ok, err)

    # ── fila ──
    @property
    def ack_queue(self) -> List[Dict[str, Any]]:
        """As que exigem ciência, mais antiga primeiro (o servidor já manda assim; ordenar de
        novo é só defesa — o sort é estável)."""
        acks = [n for n in self.notes if n.get("require_acknowledgement")]
        return sorted(acks, key=lambda n: n.get("published_at") or "")

    @property
    def cards(self) -> List[Dict[str, Any]]:
        return [n for n in self.notes if not n.get("require_acknowledgement")]

    @property
    def gate_note(self) -> Optional[Dict[str, Any]]:
        q = self.ack_queue
        return q[0] if q else None

    @property
    def mode(self) -> Optional[str]:
        """`gate` (ciência, sem pular), `cards` (dispensáveis) ou `None` (nada a mostrar)."""
        if self.loading:
            return None
        if self.gate_note is not None:
            return "gate"
        if self.cards and not self.dismissed:
            return "cards"
        return None

    @property
    def visible(self) -> bool:
        return self.mode is not None

    @property
    def has_notes(self) -> bool:
        return not self.loading and bool(self.notes)

    def color(self, note: Dict[str, Any]) -> str:
        return (note.get("product") or {}).get("color") or self.ctx.primary()

    def skip(self) -> None:
        """"Pular por agora": some com os cartões até reabrir. A ciência não pula."""
        if not self.dismissed:
            self.dismissed = True
            self.changed()

    # ── cartões (não exigem ciência) ──
    def count_text(self) -> str:
        n = len(self.cards)
        return self.ctx.t.t("splash_count_one") if n == 1 else self.ctx.t.t("splash_count_many", n=n)

    def toggle(self, note_id: str) -> None:
        self.expanded = None if self.expanded == note_id else note_id
        self.changed()

    def mark_read(self, note_id: str) -> None:
        """"Marcar como lida" → `POST .../{id}/seen`, por usuário (não o `mark-read` legado)."""
        if self.marking:
            return
        self.marking = True
        self.changed()

        def ok(_r: Any) -> None:
            self.marking = False
            self.refresh()

        def err(e: BaseException) -> None:
            self.ctx.note_error(e)
            self.marking = False
            self.changed()

        self.ctx.runner.submit(lambda: self.ctx.api.mark_release_note_seen(note_id), ok, err)

    # ── ciência exigida ──
    def gate_eyebrow(self) -> str:
        return "⚠ " + self.ctx.t.rn("ackEyebrow")

    def gate_counter(self) -> Optional[str]:
        n = len(self.ack_queue)
        return f"1 / {n}" if n > 1 else None

    @property
    def ack_disabled(self) -> bool:
        return not self.at_bottom or self.acking

    @property
    def show_scroll_hint(self) -> bool:
        return not self.at_bottom

    def scroll_hint(self) -> str:
        return self.ctx.t.rn("scrollHint")

    def ack_button_text(self) -> str:
        return self.ctx.t.rn("ackSubmitting") if self.acking else self.ctx.t.rn("ackButton")

    def ack_error_text(self) -> Optional[str]:
        return self.ctx.t.rn("ackError") if self.ack_failed else None

    def content_metrics(self, top: float, visible: float, total: float) -> None:
        """A tela informa a rolagem (px) do conteúdo da release que exige ciência."""
        if self.at_bottom or self.gate_note is None:
            return
        if reached_end(top, visible, total):
            self.at_bottom = True
            self.changed()

    def acknowledge(self) -> None:
        note = self.gate_note
        if note is None or self.ack_disabled:
            return
        self.acking = True
        self.ack_failed = False  # tentar de novo apaga o aviso
        self.changed()
        note_id = str(note.get("id"))

        def ok(_r: Any) -> None:
            self.acking = False
            # Sai da fila na hora (a próxima já aparece travada) e confere com o servidor.
            self.notes = [n for n in self.notes if str(n.get("id")) != note_id]
            self._sync_gate()
            self.changed()
            self.refresh()

        def err(e: BaseException) -> None:
            self.ctx.note_error(e)
            self.acking = False
            self.ack_failed = True  # a release fica na tela, com o aviso
            self.changed()

        self.ctx.runner.submit(lambda: self.ctx.api.acknowledge_release_note(note_id), ok, err)

    def _sync_gate(self) -> None:
        # Outra release no topo da fila: a trava de rolagem e o aviso recomeçam.
        note = self.gate_note
        gate_id = str(note.get("id")) if note is not None else None
        if gate_id != self._gate_id:
            self._gate_id = gate_id
            self.at_bottom = False
            self.ack_failed = False


class BannerVM(Observable):
    """Uma release por vez ("i / n"), sem fechar. Com ciência: trava de rolagem."""

    def __init__(self, ctx: Ctx, ids: List[str], on_done: Callable[[], None]) -> None:
        super().__init__()
        self.ctx = ctx
        self.ids = [i for i in ids if i]
        self._on_done = on_done
        self.pending: List[Dict[str, Any]] = []
        self.loading = True
        self.idx = 0
        self.submitting = False
        self.failed = False
        self.at_bottom = False
        self._done = False

    def load(self) -> None:
        def ok(pending: Any) -> None:
            self.pending = list(pending or [])
            self.loading = False
            self.changed()
            if not self.queue:
                self._finish()

        def err(e: BaseException) -> None:
            self.ctx.note_error(e)
            self.loading = False
            self.changed()
            self._finish()

        self.ctx.runner.submit(self.ctx.api.release_notes_pending, ok, err)

    @property
    def queue(self) -> List[Dict[str, Any]]:
        # Na ordem da fila do launcher-state; ids que não estão mais pendentes somem.
        if not self.ids:
            return self.pending
        by_id = {n.get("id"): n for n in self.pending}
        return [by_id[i] for i in self.ids if i in by_id]

    @property
    def note(self) -> Optional[Dict[str, Any]]:
        q = self.queue
        return q[self.idx] if 0 <= self.idx < len(q) else None

    @property
    def require_ack(self) -> bool:
        return bool((self.note or {}).get("require_acknowledgement"))

    def color(self) -> str:
        return ((self.note or {}).get("product") or {}).get("color") or self.ctx.primary()

    def eyebrow(self) -> str:
        return ("⚠ " + self.ctx.t.rn("ackEyebrow")) if self.require_ack else ("✨ " + self.ctx.t.rn("eyebrow"))

    def counter(self) -> Optional[str]:
        n = len(self.queue)
        return f"{self.idx + 1} / {n}" if n > 1 else None

    def button_text(self) -> str:
        if self.submitting:
            return self.ctx.t.rn("ackSubmitting")
        return self.ctx.t.rn("ackButton") if self.require_ack else self.ctx.t.rn("dismissButton")

    def error_text(self) -> Optional[str]:
        return self.ctx.t.rn("ackError") if self.failed else None

    @property
    def ack_disabled(self) -> bool:
        return self.require_ack and not self.at_bottom

    @property
    def show_scroll_hint(self) -> bool:
        return self.require_ack and not self.at_bottom

    def scroll_hint(self) -> str:
        return self.ctx.t.rn("scrollHint")

    def content_metrics(self, top: float, visible: float, total: float) -> None:
        """A tela informa a rolagem (px). Não rola (≤ 4 px) ou chegou a 8 px do fim: libera."""
        if self.at_bottom:
            return
        if reached_end(top, visible, total):
            self.at_bottom = True
            self.changed()

    def confirm(self) -> None:
        note = self.note
        if note is None or self.submitting or self.ack_disabled:
            return
        self.submitting = True
        self.failed = False  # tentar de novo apaga o aviso
        self.changed()
        api = self.ctx.api
        fn = api.acknowledge_release_note if self.require_ack else api.mark_release_note_seen
        note_id = str(note.get("id"))

        def ok(_r: Any) -> None:
            self.submitting = False
            self._advance()

        def err(e: BaseException) -> None:
            self.ctx.note_error(e)
            self.submitting = False
            self.failed = True  # falha de rede: a release fica na tela, com o aviso
            self.changed()

        self.ctx.runner.submit(lambda: fn(note_id), ok, err)

    def _advance(self) -> None:
        if self.idx + 1 < len(self.queue):
            self.idx += 1
            self.at_bottom = False
            self.changed()
        else:
            self._finish()

    def _finish(self) -> None:
        if not self._done:
            self._done = True
            self._on_done()


class HistoryVM(Observable):
    """Changelog, versão mais alta primeiro; a atual em destaque. NÃO marca nada como visto."""

    def __init__(self, ctx: Ctx, on_close: Callable[[], None] = lambda: None) -> None:
        super().__init__()
        self.ctx = ctx
        self._on_close = on_close
        self.data: Optional[Dict[str, Any]] = None
        self.loading = True
        self.expanded: Optional[str] = None

    def load(self) -> None:
        def ok(data: Any) -> None:
            self.data = data or {}
            self.loading = False
            self.changed()

        def err(e: BaseException) -> None:
            self.ctx.note_error(e)
            self.loading = False
            self.changed()

        self.ctx.runner.submit(self.ctx.api.release_notes_changelog, ok, err)

    @property
    def current(self) -> Optional[str]:
        products = (self.data or {}).get("products") or []
        return products[0].get("current_version") if products else None

    def notes(self) -> List[Dict[str, Any]]:
        notes = list((self.data or {}).get("notes") or [])
        notes.sort(key=lambda n: n.get("published_at") or "", reverse=True)
        notes.sort(key=lambda n: semver_key(n.get("version")), reverse=True)
        out = []
        for n in notes:
            out.append({
                "id": n.get("id"),
                "version": f"v{n.get('version')}",
                "is_current": bool(n.get("is_current")),
                "require_ack": bool(n.get("require_acknowledgement")),
                "date": history_date(n.get("published_at"), self.ctx.locale),
                "title": n.get("title") or "",
                "html": n.get("description") or "",
                "color": (n.get("product") or {}).get("color") or self.ctx.primary(),
                "expanded": self.expanded == n.get("id"),
            })
        return out

    def toggle(self, note_id: str) -> None:
        self.expanded = None if self.expanded == note_id else note_id
        self.changed()

    def close(self) -> None:
        self._on_close()
