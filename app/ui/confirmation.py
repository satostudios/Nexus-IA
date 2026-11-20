"""Minimal confirmation prompt shown after Needle picks a guarded action."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.core.events import ConfirmationRequest
from app.i18n import tr
from app.ui import theme


class _Button(QPushButton):
    def __init__(self, text: str, primary: bool) -> None:
        super().__init__(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(theme.font(10.5, QFont.Weight.Medium, spacing=1.0))
        self.setFixedSize(112, 34)
        border = "rgba(255,255,255,0.55)" if primary else "rgba(255,255,255,0.18)"
        color = theme.TEXT if primary else theme.TEXT_DIM
        self.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {color}; border: 1px solid {border}; border-radius: 2px; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,0.08); color: {theme.TEXT}; }}"
            f"QPushButton:pressed {{ background: rgba(255,255,255,0.16); }}"
            f"QPushButton:focus {{ outline: none; border-color: rgba(255,255,255,0.8); }}")


class ConfirmationView(QWidget):
    answered = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.title = QLabel()
        self.title.setFont(theme.font(15, QFont.Weight.Normal))
        self.title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.title.setStyleSheet(f"color: {theme.TEXT}; background: transparent;")
        self.detail = QLabel()
        self.detail.setFont(theme.font(9.5, mono=True))
        self.detail.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet(f"color: {theme.TEXT_DIM}; background: transparent;")
        self.cancel = _Button(tr("Cancel"), False)
        self.ok = _Button(tr("Confirm"), True)
        self.cancel.clicked.connect(lambda: self.answered.emit(False))
        self.ok.clicked.connect(lambda: self.answered.emit(True))

        buttons = QHBoxLayout()
        buttons.setSpacing(14)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel)
        buttons.addWidget(self.ok)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 18, 28, 18)
        layout.setSpacing(8)
        layout.addWidget(self.title)
        layout.addWidget(self.detail)
        layout.addSpacing(8)
        layout.addLayout(buttons)
        self.hide()

    def ask(self, request: ConfirmationRequest) -> None:
        self.title.setText(request.title)
        self.detail.setText(request.detail)
        self.detail.setVisible(bool(request.detail))
        self.ok.setText(tr("Confirm"))
        self.show()
        self.raise_()
        # Destructive actions never default to Confirm: focus lands on Cancel.
        self.cancel.setFocus()

    def keyPressEvent(self, e) -> None:  # noqa: ANN001
        if e.key() == Qt.Key.Key_Escape:
            self.answered.emit(False)
            return
        super().keyPressEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(0, 0, 0, 238))
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 3, 3)
