"""Optional side panels: command history (right) and debug metadata (left)."""

from __future__ import annotations

import html
import json
from typing import Any

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from app.core.events import HistoryEntry
from app.i18n import tr
from app.ui import theme


def _short(text: str, limit: int = 34) -> str:
    return text if len(text) <= limit else "…" + text[-(limit - 1):]


class SidePanel(QWidget):
    """A translucent panel that slides in from one edge of the window."""

    WIDTH = 330

    def __init__(self, title: str, side: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.side = side
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.heading = QLabel(title)
        self.heading.setFont(theme.font(8.5, spacing=3.5))
        self.heading.setStyleSheet(f"color: {theme.TEXT_DIM}; background: transparent;")
        self.body = QLabel()
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setWordWrap(True)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.setStyleSheet("background: transparent;")
        scroll = QScrollArea()
        scroll.setWidget(self.body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll = scroll
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(self.heading)
        layout.addWidget(scroll, 1)
        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._open = False
        self.hide()

    @property
    def is_open(self) -> bool:
        return self._open

    def _rects(self) -> tuple[QRect, QRect]:
        parent = self.parentWidget()
        top, h = 40, parent.height() - 40 - 80
        w = min(self.WIDTH, parent.width() // 2)
        if self.side == "right":
            shown = QRect(parent.width() - w, top, w, h)
            hidden = QRect(parent.width(), top, w, h)
        else:
            shown = QRect(0, top, w, h)
            hidden = QRect(-w, top, w, h)
        return shown, hidden

    def relayout(self) -> None:
        shown, hidden = self._rects()
        self.setGeometry(shown if self._open else hidden)

    def toggle(self, on: bool | None = None) -> None:
        self._open = (not self._open) if on is None else on
        shown, hidden = self._rects()
        self._anim.stop()
        self._anim.setStartValue(self.geometry() if self.isVisible() else (hidden if self._open else shown))
        self._anim.setEndValue(shown if self._open else hidden)
        if self._open:
            self.show()
            self.raise_()
        else:
            self._anim.finished.connect(self._after_close)
        self._anim.start()

    def _after_close(self) -> None:
        try:
            self._anim.finished.disconnect(self._after_close)
        except (RuntimeError, TypeError):
            pass
        if not self._open:
            self.hide()

    def paintEvent(self, _e) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(6, 6, 8, 215))
        p.setPen(QColor(255, 255, 255, 22))
        x = 0 if self.side == "right" else self.width() - 1
        p.drawLine(x, 0, x, self.height())


class HistoryPanel(SidePanel):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(tr("HISTORY"), "right", parent)
        self._entries: list[HistoryEntry] = []

    def set_entries(self, entries: list[HistoryEntry]) -> None:
        self._entries = list(entries)[-60:]
        self._render()

    def add(self, entry: HistoryEntry) -> None:
        self._entries.append(entry)
        self._entries = self._entries[-60:]
        self._render()

    def _render(self) -> None:
        if not self._entries:
            self.body.setText(f"<span style='color:{theme.TEXT_FAINT}'>{tr('Nothing yet.')}</span>")
            return
        rows = []
        for e in reversed(self._entries):
            reply_color = theme.TEXT if e.success else theme.WARN
            rows.append(
                f"<p style='margin:0'><span style='color:{theme.TEXT_FAINT};font-size:7.5pt'>{tr('YOU')}</span><br>"
                f"<span style='color:{theme.TEXT_DIM};font-size:10.5pt'>{html.escape(e.user)}</span></p>"
                f"<p style='margin:4px 0 18px 0'><span style='color:{theme.TEXT_FAINT};font-size:7.5pt'>NEXUS</span><br>"
                f"<span style='color:{reply_color};font-size:10.5pt'>{html.escape(e.reply)}</span></p>")
        self.body.setText("".join(rows))


class DebugPanel(SidePanel):
    """Technical metadata only: transcript, tool, arguments, confidence, route, result."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__("DEBUG", "left", parent)
        self.body.setFont(theme.font(8.8, mono=True))
        self.info: dict[str, Any] = {}
        self._last: dict[str, Any] | None = None
        self.fps = 0.0

    def set_engine_info(self, info: dict[str, Any]) -> None:
        self.info = info
        self._render()

    def show_event(self, event: dict[str, Any]) -> None:
        self._last = event
        self._render()

    def _render(self) -> None:
        def block(label: str, value: str) -> str:
            return (f"<div style='color:{theme.TEXT_FAINT};font-size:7.5pt;letter-spacing:2px;margin-top:10px'>{label}</div>"
                    f"<pre style='color:{theme.TEXT};margin:2px 0;white-space:pre-wrap'>{html.escape(value)}</pre>")

        parts = []
        ev = self._last
        if ev:
            parts.append(block(tr("TRANSCRIPT"), f"{ev['transcript']}   ({ev['source']})"))
            if ev.get("needle_input") and ev["needle_input"] != ev["transcript"]:
                parts.append(block(tr("NEEDLE INPUT"), ev["needle_input"]))
            if not ev["calls"]:
                parts.append(block(tr("TOOL"), tr("none (no call proposed)")))
            for call in ev["calls"]:
                parts.append(block(tr("TOOL"), call["tool"]))
                parts.append(block(tr("ARGUMENTS"), json.dumps(call["arguments"], indent=2, ensure_ascii=False)))
                conf = call["confidence"]
                parts.append(block(tr("CONFIDENCE"), "n/a" if conf is None else f"{conf:.2f}"
                                   + ("  " + tr("(withheld by engine)") if call["suppressed"] else "")))
                parts.append(block(tr("ROUTE"), call["route"]))
                if call["result"] is not None:
                    parts.append(block(tr("RESULT"), json.dumps(call["result"], indent=2, ensure_ascii=False,
                                                                default=str)[:1200]))
            parts.append(block(tr("OUTCOME"), f"{ev['outcome']}: {ev['message']}"))
            t = ev.get("timings") or {}
            if t:
                parts.append(block(tr("TIMING"), f"needle  {t.get('needle_ms')} ms\ntools   {t.get('tools_ms')} ms\n"
                                             f"total   {t.get('total_ms')} ms\nsteps   {t.get('steps')}"))
        if self.info:
            parts.append(block(tr("ENGINE"), "\n".join(f"{k}: {_short(str(v))}" for k, v in self.info.items())))
        self.body.setText("".join(parts) or f"<span style='color:{theme.TEXT_FAINT}'>{tr('Run a command.')}</span>")
