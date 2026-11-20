"""Frameless window chrome: drag area, wordmark, subtle controls."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from app.i18n import tr
from app.ui import theme
from app.ui.icons import IconButton


class TitleBar(QWidget):
    minimize = Signal()
    toggle_maximize = Signal()
    close = Signal()
    open_settings = Signal()
    toggle_history = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        mark = QLabel("NEXUS")
        mark.setFont(theme.font(8.5, QFont.Weight.Medium, spacing=4.0))
        mark.setStyleSheet(f"color: {theme.TEXT_FAINT}; background: transparent;")
        self.history_btn = IconButton("history", tr("History (Ctrl+H)"), 30)
        self.settings_btn = IconButton("settings", tr("Settings (Ctrl+,)"), 30)
        self.min_btn = IconButton("minimize", tr("Minimize"), 30)
        self.max_btn = IconButton("maximize", tr("Maximize"), 30)
        self.close_btn = IconButton("close", tr("Close"), 30)
        self.close_btn.danger_hover = True
        self.history_btn.clicked.connect(self.toggle_history)
        self.settings_btn.clicked.connect(self.open_settings)
        self.min_btn.clicked.connect(self.minimize)
        self.max_btn.clicked.connect(self.toggle_maximize)
        self.close_btn.clicked.connect(self.close)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 4, 8, 0)
        layout.setSpacing(2)
        layout.addWidget(mark)
        layout.addStretch(1)
        for b in (self.history_btn, self.settings_btn):
            layout.addWidget(b)
        layout.addSpacing(10)
        for b in (self.min_btn, self.max_btn, self.close_btn):
            layout.addWidget(b)

    def set_maximized(self, maximized: bool) -> None:
        self.max_btn.icon = "restore" if maximized else "maximize"
        self.max_btn.setToolTip(tr("Restore") if maximized else tr("Maximize"))
        self.max_btn.update()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximize.emit()
