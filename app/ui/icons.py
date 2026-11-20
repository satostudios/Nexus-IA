"""Vector icons drawn with QPainterPath (no emoji, no bitmaps)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Property, QPropertyAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QWidget

from app.i18n import tr
from app.ui import theme


def icon_path(name: str, r: QRectF) -> QPainterPath:
    """Outline path for ``name`` fitted in ``r``."""
    p = QPainterPath()
    y, w, h = r.y(), r.width(), r.height()
    cx, cy = r.center().x(), r.center().y()
    if name == "mic":
        cw, ch = w * 0.34, h * 0.52
        p.addRoundedRect(QRectF(cx - cw / 2, y + h * 0.06, cw, ch), cw / 2, cw / 2)
        arc = QRectF(cx - w * 0.30, y + h * 0.22, w * 0.60, h * 0.52)
        p.moveTo(arc.left(), arc.center().y())
        p.arcTo(arc, 180, 180)
        p.moveTo(cx, arc.bottom())
        p.lineTo(cx, y + h * 0.94)
        p.moveTo(cx - w * 0.16, y + h * 0.94)
        p.lineTo(cx + w * 0.16, y + h * 0.94)
    elif name == "stop":
        s = w * 0.42
        p.addRoundedRect(QRectF(cx - s / 2, cy - s / 2, s, s), 2, 2)
    elif name == "close":
        d = w * 0.30
        p.moveTo(cx - d, cy - d)
        p.lineTo(cx + d, cy + d)
        p.moveTo(cx + d, cy - d)
        p.lineTo(cx - d, cy + d)
    elif name == "minimize":
        p.moveTo(cx - w * 0.30, cy)
        p.lineTo(cx + w * 0.30, cy)
    elif name == "maximize":
        s = w * 0.56
        p.addRect(QRectF(cx - s / 2, cy - s / 2, s, s))
    elif name == "restore":
        s, o = w * 0.44, w * 0.10
        front = QRectF(cx - s / 2 - o / 2, cy - s / 2 + o / 2, s, s)
        p.addRect(front)
        bx, by = front.left() + o, front.top() - o
        p.moveTo(bx, front.top())
        p.lineTo(bx, by)
        p.lineTo(bx + s, by)
        p.lineTo(bx + s, by + s)
        p.lineTo(front.right(), by + s)
    elif name == "settings":
        outer, inner = w * 0.40, w * 0.28
        teeth = 8
        for i in range(teeth * 2 + 1):
            a = math.pi * 2 * i / (teeth * 2)
            rad = outer if i % 2 == 0 else inner
            pt = QPointF(cx + math.cos(a) * rad, cy + math.sin(a) * rad)
            if i == 0:
                p.moveTo(pt)
            else:
                p.lineTo(pt)
        p.addEllipse(QPointF(cx, cy), w * 0.12, w * 0.12)
    elif name == "history":
        rad = w * 0.36
        p.addEllipse(QPointF(cx, cy), rad, rad)
        p.moveTo(cx, cy - rad * 0.6)
        p.lineTo(cx, cy)
        p.lineTo(cx + rad * 0.45, cy + rad * 0.3)
    elif name == "debug":
        p.moveTo(cx - w * 0.12, cy - h * 0.28)
        p.lineTo(cx - w * 0.34, cy)
        p.lineTo(cx - w * 0.12, cy + h * 0.28)
        p.moveTo(cx + w * 0.12, cy - h * 0.28)
        p.lineTo(cx + w * 0.34, cy)
        p.lineTo(cx + w * 0.12, cy + h * 0.28)
    elif name == "send":
        p.moveTo(cx - w * 0.28, cy)
        p.lineTo(cx + w * 0.28, cy)
        p.moveTo(cx + w * 0.06, cy - h * 0.22)
        p.lineTo(cx + w * 0.28, cy)
        p.lineTo(cx + w * 0.06, cy + h * 0.22)
    return p


class IconButton(QAbstractButton):
    """Flat vector icon button: grey, brighter on hover."""

    def __init__(self, icon: str, tooltip: str = "", size: int = 28, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.icon = icon
        self.setToolTip(tooltip)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._hover = False
        self.color = QColor(theme.TEXT_DIM)
        self.hover_color = QColor(theme.TEXT)
        self.danger_hover = False

    def enterEvent(self, e) -> None:  # noqa: ANN001
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:  # noqa: ANN001
        self._hover = False
        self.update()

    def sizeHint(self) -> QSize:
        return self.size()

    def paintEvent(self, _e) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._hover and self.danger_hover:
            painter.fillRect(self.rect(), QColor(255, 255, 255, 18))
        color = self.hover_color if (self._hover or self.isChecked()) else self.color
        if not self.isEnabled():
            color = QColor(theme.TEXT_FAINT)
        pen = QPen(color, 1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        m = self.width() * 0.26
        painter.drawPath(icon_path(self.icon, QRectF(self.rect()).adjusted(m, m, -m, -m)))


class MicButton(QAbstractButton):
    """Microphone button with a live ring that follows the input level."""

    pressed_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(40, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(tr("Talk to NEXUS (Ctrl+Space)"))
        self._listening = False
        self._voice_mode = False
        self._level = 0.0
        self._hover = False
        self._phase = 0.0
        self._anim = QPropertyAnimation(self, b"phase", self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1600)
        self._anim.setLoopCount(-1)

    def get_phase(self) -> float:
        return self._phase

    def set_phase(self, v: float) -> None:
        self._phase = v
        self.update()

    phase = Property(float, get_phase, set_phase)

    def set_listening(self, on: bool) -> None:
        self._listening = on
        if on:
            self._anim.start()
        else:
            self._anim.stop()
            self._level = 0.0
        self.update()

    def set_voice_mode(self, on: bool) -> None:
        self._voice_mode = on
        self.update()

    def set_level(self, level: float) -> None:
        if self._listening:
            self._level = level
            self.update()

    def enterEvent(self, e) -> None:  # noqa: ANN001
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:  # noqa: ANN001
        self._hover = False
        self.update()

    def mousePressEvent(self, e) -> None:  # noqa: ANN001
        super().mousePressEvent(e)
        self.pressed_changed.emit(True)

    def mouseReleaseEvent(self, e) -> None:  # noqa: ANN001
        super().mouseReleaseEvent(e)
        self.pressed_changed.emit(False)

    def paintEvent(self, _e) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        if self._voice_mode and not self._listening:
            # Conversation mode stays visibly on while NEXUS thinks or talks.
            ring = QColor(theme.ACCENT)
            ring.setAlphaF(0.35)
            painter.setPen(QPen(ring, 1.2))
            painter.drawEllipse(c, 13, 13)
        if self._listening:
            base = 12 + 6 * self._level
            ring = QColor(theme.ACCENT)
            ring.setAlphaF(0.22 + 0.5 * self._level)
            painter.setPen(QPen(ring, 1.2))
            painter.drawEllipse(c, base, base)
            wave = QColor(theme.ACCENT)
            wave.setAlphaF(0.35 * (1 - self._phase))
            painter.setPen(QPen(wave, 1.0))
            painter.drawEllipse(c, 12 + 8 * self._phase, 12 + 8 * self._phase)
        color = QColor(theme.ACCENT) if (self._listening or self._voice_mode) else \
            QColor(theme.TEXT if self._hover else theme.TEXT_DIM)
        if not self.isEnabled():
            color = QColor(theme.TEXT_FAINT)
        pen = QPen(color, 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        s = 16
        painter.drawPath(icon_path("stop" if self._listening else "mic", QRectF(c.x() - s / 2, c.y() - s / 2, s, s)))
