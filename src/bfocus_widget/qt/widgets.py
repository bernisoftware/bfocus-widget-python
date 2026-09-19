"""Componentes Qt opcionais (BRIEF §3): botão flutuante com badge e pílula de versão.
O integrador pode usar os próprios e ligar só os callbacks do `BFocusWidget`."""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QAbstractButton, QWidget

from ..core.widget import BFocusWidget
from ..shared import Translator, tokens

BADGE_RED = "#ef4444"  # mesmo vermelho do badge do widget.js


def _brand(widget: BFocusWidget) -> QColor:
    # Sem cor do tenant, a identidade do bFocus (tokens.brandFallback).
    return QColor(widget.primary_color or tokens()["brandFallback"])


def _bubble_path() -> QPainterPath:
    """O balão do ícone do widget.js (viewBox 24×24)."""
    p = QPainterPath(QPointF(3, 11))
    p.cubicTo(3, 6.6, 7, 3, 12, 3)
    p.cubicTo(17, 3, 21, 6.6, 21, 11)
    p.cubicTo(21, 15.4, 17, 19, 12, 19)
    p.cubicTo(11, 19, 10, 18.9, 9.1, 18.6)
    p.lineTo(4, 21)
    p.lineTo(5.4, 16.6)
    p.cubicTo(4, 15.2, 3, 13.2, 3, 11)
    p.closeSubpath()
    return p


class BFocusLauncherButton(QAbstractButton):
    """Botão redondo de 56 px na cor do tenant, com o badge (`''`, `'•'`, `'N'`, `'99+'`)."""

    DIAMETER = 56

    def __init__(self, widget: BFocusWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.widget = widget
        t = Translator(widget.config.locale or "pt_BR")
        self._color = _brand(widget)
        self._label = widget.badge_label
        self._open = widget.is_open
        self.setFixedSize(self.DIAMETER + 8, self.DIAMETER + 8)  # folga para o badge
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(t.native("open_support"))
        self.setAccessibleName(t.native("open_support"))
        self.clicked.connect(self._toggle)
        self._subs = [
            widget.subscribe("badge", self._on_badge),
            widget.subscribe("branding", self._on_branding),
            widget.subscribe("open", lambda: self._on_open(True)),
            widget.subscribe("close", lambda: self._on_open(False)),
        ]
        self.destroyed.connect(lambda *_: [c() for c in self._subs])

    @property
    def label(self) -> str:
        return self._label

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.DIAMETER + 8, self.DIAMETER + 8)

    def _toggle(self) -> None:
        if self.widget.is_open:
            self.widget.close()
        else:
            self.widget.open()

    def _on_badge(self, label: str) -> None:
        self._label = label
        self.update()

    def _on_branding(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def _on_open(self, is_open: bool) -> None:
        self._open = is_open
        self.update()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        circle = QRectF(0, 8, self.DIAMETER, self.DIAMETER)
        color = self._color.lighter(108) if self.underMouse() else self._color
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(circle)
        # Ícone de 26 px no centro: balão (fechado) ou ✕ (aberto), como o widget.js.
        p.save()
        scale = 26 / 24
        p.translate(circle.center().x() - 13, circle.center().y() - 13)
        p.scale(scale, scale)
        pen = QPen(QColor("#ffffff"), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if self._open:
            p.drawLine(QPointF(6, 6), QPointF(18, 18))
            p.drawLine(QPointF(6, 18), QPointF(18, 6))
        else:
            p.drawPath(_bubble_path())
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#ffffff"))
            for x in (9, 12, 15):
                p.drawEllipse(QPointF(x, 11), 1, 1)
        p.restore()
        if self._label:
            font = QFont(self.font())
            font.setPixelSize(11)
            font.setBold(True)
            p.setFont(font)
            w = max(20, QFontMetrics(font).horizontalAdvance(self._label) + 10)
            rect = QRectF(self.width() - w, 0, w, 20)
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.setBrush(QColor(BADGE_RED))
            p.drawRoundedRect(rect, 10, 10)
            p.setPen(QColor("#ffffff"))
            p.drawText(rect, Qt.AlignCenter, self._label)
        p.end()


def _star() -> QPolygonF:
    pts = [(12, 2), (14.4, 9.4), (22, 9.4), (16, 13.8), (18.3, 21.2), (12, 16.6), (5.7, 21.2), (7.9, 13.8), (2, 9.4), (9.6, 9.4)]
    return QPolygonF([QPointF(x, y) for x, y in pts])


class BFocusReleaseBadge(QAbstractButton):
    """Pílula de versão: ★ `v4.2.0` (ou `—`) e um ponto quando há novidade. Abre o histórico."""

    def __init__(self, widget: BFocusWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.widget = widget
        t = Translator(widget.config.locale or "pt_BR")
        st = widget.release_notes
        self._label = st.label
        self._dot = st.dot
        self._color = _brand(widget)
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setBold(True)
        self.setFont(font)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(t.native("release_badge"))
        self.setAccessibleName(t.native("release_badge"))
        self.clicked.connect(widget.open_release_notes_history)
        self._subs = [
            widget.subscribe("release_notes", self._on_state),
            widget.subscribe("branding", self._on_branding),
        ]
        self.destroyed.connect(lambda *_: [c() for c in self._subs])
        self.setFixedSize(self.sizeHint())

    @property
    def label(self) -> str:
        return self._label

    @property
    def dot(self) -> bool:
        return self._dot

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(self.font())
        return QSize(10 + 14 + 6 + fm.horizontalAdvance(self._label) + 6 + 7 + 10, 26)

    def _on_state(self, st: Any) -> None:
        self._label, self._dot = st.label, st.dot
        if st.color:
            self._color = QColor(st.color)
        self.setFixedSize(self.sizeHint())
        self.update()

    def _on_branding(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(self._color.lighter(106) if self.underMouse() else self._color)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.save()
        p.translate(10, (r.height() - 14) / 2)
        p.scale(14 / 24, 14 / 24)
        p.setBrush(QColor("#ffffff"))
        p.drawPolygon(_star())
        p.restore()
        fm = QFontMetrics(self.font())
        p.setPen(QColor("#ffffff"))
        text_rect = QRectF(30, 0, fm.horizontalAdvance(self._label) + 2, r.height())
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, self._label)
        if self._dot:
            p.setPen(QPen(QColor("#ffffff"), 1.5))
            p.setBrush(QColor(BADGE_RED))
            p.drawEllipse(QPointF(text_rect.right() + 8, r.height() / 2), 3.5, 3.5)
        p.end()
