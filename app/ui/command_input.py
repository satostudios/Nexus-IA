"""The single-line command bar with the integrated microphone button."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QPainter, QColor
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QWidget

from app.i18n import tr
from app.ui import theme
from app.ui.icons import MicButton


class _Line(QLineEdit):
    history_up = Signal()
    history_down = Signal()
    escape = Signal()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Up:
            self.history_up.emit()
            return
        if e.key() == Qt.Key.Key_Down:
            self.history_down.emit()
            return
        if e.key() == Qt.Key.Key_Escape:
            self.escape.emit()
            return
        super().keyPressEvent(e)


class CommandInput(QWidget):
    submitted = Signal(str)
    mic_clicked = Signal()
    mic_pressed = Signal(bool)
    escape = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._history: list[str] = []
        self._cursor = 0
        self._focused = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedHeight(54)

        self.line = _Line(self)
        self.line.setPlaceholderText(tr("Type a command..."))
        self.line.setFrame(False)
        self.line.setFont(theme.font(12.5))
        self.line.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self.line.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none; color: {theme.TEXT};"
            f" selection-background-color: rgba(255,255,255,0.22); padding: 0 2px; }}")
        pal = self.line.palette()
        pal.setColor(pal.ColorRole.PlaceholderText, QColor(theme.TEXT_FAINT))
        self.line.setPalette(pal)
        self.line.returnPressed.connect(self._submit)
        self.line.history_up.connect(lambda: self._recall(-1))
        self.line.history_down.connect(lambda: self._recall(1))
        self.line.escape.connect(self.escape)
        self.line.installEventFilter(self)

        self.mic = MicButton(self)
        self.mic.clicked.connect(self.mic_clicked)
        self.mic.pressed_changed.connect(self.mic_pressed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 6, 0, 6)
        layout.setSpacing(10)
        layout.addWidget(self.line, 1)
        layout.addWidget(self.mic, 0)

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001
        if obj is self.line and event.type() in (event.Type.FocusIn, event.Type.FocusOut):
            self._focused = event.type() == event.Type.FocusIn
            self.update()
        return False

    def set_voice_mode(self, on: bool) -> None:
        self.mic.set_voice_mode(on)
        self.line.setPlaceholderText(tr("Voice mode: just talk. Say \"stop\" to finish.") if on
                                     else tr("Type a command..."))

    def set_history(self, commands: list[str]) -> None:
        self._history = [c for c in commands if c.strip()][-100:]
        self._cursor = len(self._history)

    def _submit(self) -> None:
        text = self.line.text().strip()
        if not text:
            return
        if not self._history or self._history[-1] != text:
            self._history.append(text)
        self._cursor = len(self._history)
        self.line.clear()
        self.submitted.emit(text)

    def _recall(self, step: int) -> None:
        if not self._history:
            return
        self._cursor = max(0, min(len(self._history), self._cursor + step))
        self.line.setText(self._history[self._cursor] if self._cursor < len(self._history) else "")

    def paintEvent(self, _e) -> None:  # noqa: ANN001
        painter = QPainter(self)
        color = QColor(255, 255, 255, 82 if self._focused else 32)
        painter.setPen(color)
        painter.drawLine(0, 0, self.width(), 0)
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
