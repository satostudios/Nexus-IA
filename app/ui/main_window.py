"""The NEXUS window: the Core fills the background; everything else floats over it."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import QApplication, QWidget

from app.audio.tts import SapiSynthesizer
from app.config.settings import ACTIVATION_HOLD, Settings
from app.core.assistant import Assistant
from app.core.events import ConfirmationRequest, HistoryEntry
from app.core.state import NexusState
from app import paths
from app.i18n import tr
from app.ui import theme
from app.ui.app_icon import window_icon
from app.ui.command_input import CommandInput
from app.ui.confirmation import ConfirmationView
from app.ui.history_panel import DebugPanel, HistoryPanel
from app.ui.nexus_core import NexusCore, PainterCore, create_core
from app.ui.settings_window import SettingsWindow
from app.ui.status import StatusView
from app.ui.title_bar import TitleBar

log = logging.getLogger("nexus.ui")
EDGE = 6


class MainWindow(QWidget):
    def __init__(self, settings: Settings, assistant: Assistant) -> None:
        super().__init__()
        self.settings = settings
        self.assistant = assistant
        self.setWindowTitle("NEXUS")
        self.setWindowIcon(window_icon())
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
                            | Qt.WindowType.WindowMinMaxButtonsHint)
        self.setMinimumSize(560, 460)
        self.resize(1000, 700)
        self.setStyleSheet(theme.stylesheet())
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), Qt.GlobalColor.black)
        self.setPalette(pal)

        self.core = create_core(assistant.audio.levels, self)
        if isinstance(self.core, NexusCore):
            self.core.gl_failed.connect(self._fallback_core)
        self.title = TitleBar(self)
        self.status = StatusView(self)
        self.confirm_view = ConfirmationView(self)
        self.input = CommandInput(self)
        self.history = HistoryPanel(self)
        self.debug = DebugPanel(self)
        self.history.set_entries(assistant.history.entries())
        self.input.set_history(assistant.history.user_commands())
        self._last_debug: dict[str, Any] | None = None
        self._settings_window: SettingsWindow | None = None

        self.input.mic.setEnabled(False)  # enabled when speech recognition has loaded
        self._wire()
        self._shortcuts()
        self._level_timer = QTimer(self)
        self._level_timer.timeout.connect(self._update_mic_level)
        self._level_timer.start(33)
        QApplication.instance().installEventFilter(self)  # type: ignore[union-attr]
        self.input.line.setFocus()

    # ============================================================ wiring
    def _wire(self) -> None:
        a = self.assistant
        a.state.changed.connect(self._on_state)
        a.startup_progress.connect(self.status.progress_update)
        a.voice_available.connect(lambda _ok: self.status.set_background_task(None))
        a.startup_finished.connect(self._on_started)
        a.transcript.connect(lambda text, _src: self.status.show_transcript(text))
        a.reply.connect(self._on_reply)
        a.notice.connect(self.status.show_notice)
        a.confirmation_requested.connect(self._on_confirm_request)
        a.confirmation_closed.connect(self._close_confirmation)
        a.pulse.connect(lambda kind: self.core.pulse(kind))
        a.debug_event.connect(self._on_debug)
        a.history_added.connect(self._on_history)
        a.voice_available.connect(self.input.mic.setEnabled)
        a.voice_mode_changed.connect(self.input.set_voice_mode)
        a.job_progress.connect(self.status.set_job)

        self.confirm_view.answered.connect(self._on_confirm_answer)
        self.input.submitted.connect(a.submit_text)
        self.input.mic_clicked.connect(self._mic_clicked)
        self.input.mic_pressed.connect(self._mic_pressed)
        self.input.escape.connect(a.cancel)

        self.title.minimize.connect(self.showMinimized)
        self.title.toggle_maximize.connect(self._toggle_max)
        self.title.close.connect(self.close)
        self.title.open_settings.connect(self.open_settings)
        self.title.toggle_history.connect(lambda: self.history.toggle())

    def _shortcuts(self) -> None:
        def sc(keys: str, fn) -> None:  # noqa: ANN001
            s = QShortcut(QKeySequence(keys), self)
            s.setContext(Qt.ShortcutContext.WindowShortcut)
            s.activated.connect(fn)

        sc("Ctrl+Space", self.assistant.toggle_listening)
        sc("Ctrl+H", lambda: self.history.toggle())
        sc("Ctrl+D", lambda: self.debug.toggle())
        sc("Ctrl+,", self.open_settings)
        sc("Ctrl+L", lambda: self.input.line.setFocus())
        sc("Escape", self.assistant.cancel)

    # ============================================================ slots
    def _on_state(self, state: NexusState, _old: NexusState) -> None:
        self.core.set_state(state)
        self.status.set_state(state)
        self.input.mic.set_listening(state is NexusState.LISTENING)

    def _on_started(self, ok: bool, message: str) -> None:
        self.status.boot_done(ok, message)
        self.debug.set_engine_info(self.assistant.engine.info)
        if ok and self.settings.debug.show_tool_calls:
            self.debug.toggle(True)
        self.input.line.setFocus()

    def _on_reply(self, message: str, success: bool, extra: list) -> None:
        meta = ""
        if self.settings.debug.show_confidence and self._last_debug:
            confs = [c for c in self._last_debug.get("calls", []) if c.get("confidence") is not None]
            if confs:
                c = confs[0]
                meta = f"{c['tool']} · {tr('confidence')} {c['confidence']:.2f}"
        self.status.show_reply(message, success, extra, meta)

    def _on_debug(self, event: dict) -> None:
        self._last_debug = event
        self.debug.show_event(event)

    def _on_history(self, entry: HistoryEntry) -> None:
        self.history.add(entry)

    def _on_confirm_request(self, request: ConfirmationRequest) -> None:
        self.status.hide()  # the question lives in the confirmation box
        self.confirm_view.ask(request)
        self._layout()

    def _close_confirmation(self) -> None:
        self.confirm_view.hide()
        self.status.show()

    def _on_confirm_answer(self, approved: bool) -> None:
        self._close_confirmation()
        self.assistant.confirm(approved)
        self.input.line.setFocus()

    def _mic_clicked(self) -> None:
        if self.settings.audio.activation_mode != ACTIVATION_HOLD:
            self.assistant.toggle_listening()

    def _mic_pressed(self, pressed: bool) -> None:
        if self.settings.audio.activation_mode == ACTIVATION_HOLD:
            if pressed:
                self.assistant.start_listening()
            else:
                self.assistant.stop_listening()

    def _update_mic_level(self) -> None:
        if self.assistant.audio.listening:
            self.input.mic.set_level(float(self.assistant.audio.mic_analyzer.levels().volume))

    def _fallback_core(self, message: str) -> None:
        log.warning("switching to software core: %s", message)
        old = self.core
        self.core = PainterCore(self.assistant.audio.levels, self)
        self.core.set_state(self.assistant.state.state)
        old.hide()
        old.deleteLater()
        self.core.show()
        self.core.lower()
        self._layout()

    def open_settings(self) -> None:
        if self._settings_window is not None and self._settings_window.isVisible():
            return
        from app.ai.model_assets import ModelAssets

        assets = ModelAssets(paths.models_dir())
        win = SettingsWindow(self.settings, assets.installed_piper_voices, SapiSynthesizer().voices, self)
        win.saved.connect(self.assistant.apply_settings)
        win.test_voice.connect(self.assistant.test_voice)
        win.notice.connect(self.status.show_notice)
        win.finished.connect(lambda _r: self._after_settings())
        self._settings_window = win
        win.move(self.geometry().center() - QPoint(win.width() // 2, win.height() // 2))
        win.show()

    def _after_settings(self) -> None:
        from app.logging_setup import setup_logging

        setup_logging(self.settings.debug.enable_logs, self.settings.debug.log_level)
        if self.settings.debug.show_tool_calls and not self.debug.is_open:
            self.debug.toggle(True)
        self.input.line.setFocus()

    def _toggle_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    # =========================================================== layout
    def resizeEvent(self, e) -> None:  # noqa: ANN001
        super().resizeEvent(e)
        self._layout()

    def changeEvent(self, e: QEvent) -> None:
        if e.type() == QEvent.Type.WindowStateChange:
            self.title.set_maximized(self.isMaximized())
        super().changeEvent(e)

    def _layout(self) -> None:
        w, h = self.width(), self.height()
        self.core.setGeometry(0, 0, w, h)
        self.core.lower()
        self.title.setGeometry(0, 0, w, 40)
        side = max(28, int(w * 0.08))
        bar_h = self.input.height()
        self.input.setGeometry(side, h - bar_h - 26, w - 2 * side, bar_h)
        m = min(w, h) * 0.5
        core_bottom = int(h / 2 + 0.12 * m + 0.35 * m * 1.05)
        status_top = min(core_bottom + 18, h - bar_h - 150)
        self.status.setGeometry(side, status_top, w - 2 * side, h - bar_h - 26 - status_top)
        cw, ch = min(520, w - 2 * side), 150
        self.confirm_view.setGeometry((w - cw) // 2, h - bar_h - 26 - ch - 14, cw, ch)
        self.history.relayout()
        self.debug.relayout()
        for widget in (self.title, self.status, self.input, self.confirm_view):
            widget.raise_()
        if self.history.isVisible():
            self.history.raise_()
        if self.debug.isVisible():
            self.debug.raise_()

    # ============================================== frameless resizing
    def _edges(self, pos: QPoint) -> Qt.Edge:
        if self.isMaximized():
            return Qt.Edge(0)
        r = self.rect()
        edges = Qt.Edge(0)
        if pos.x() <= EDGE:
            edges |= Qt.Edge.LeftEdge
        if pos.x() >= r.width() - EDGE:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= EDGE:
            edges |= Qt.Edge.TopEdge
        if pos.y() >= r.height() - EDGE:
            edges |= Qt.Edge.BottomEdge
        return edges

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        et = event.type()
        if et in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress) and isinstance(obj, QWidget) \
                and obj.window() is self and isinstance(event, QMouseEvent):
            pos = self.mapFromGlobal(event.globalPosition().toPoint())
            edges = self._edges(pos)
            if et == QEvent.Type.MouseMove and not event.buttons():
                if edges in (Qt.Edge.LeftEdge, Qt.Edge.RightEdge):
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                elif edges in (Qt.Edge.TopEdge, Qt.Edge.BottomEdge):
                    self.setCursor(Qt.CursorShape.SizeVerCursor)
                elif edges in (Qt.Edge.TopEdge | Qt.Edge.LeftEdge, Qt.Edge.BottomEdge | Qt.Edge.RightEdge):
                    self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                elif edges in (Qt.Edge.TopEdge | Qt.Edge.RightEdge, Qt.Edge.BottomEdge | Qt.Edge.LeftEdge):
                    self.setCursor(Qt.CursorShape.SizeBDiagCursor)
                else:
                    self.unsetCursor()
            elif et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton and edges:
                handle = self.windowHandle()
                if handle is not None:
                    handle.startSystemResize(edges)
                    return True
        return super().eventFilter(obj, event)

    def closeEvent(self, e: QCloseEvent) -> None:
        self._level_timer.stop()
        try:
            self.core.cleanup()
        except Exception:
            pass
        self.assistant.shutdown()
        super().closeEvent(e)
