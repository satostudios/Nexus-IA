"""State label, transcript, concise reply and start-up progress under the Core."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QVBoxLayout, QWidget

from app.core.state import NexusState
from app.i18n import tr
from app.ui import theme


def _label(size: float, color: str, weight: QFont.Weight = QFont.Weight.Normal, spacing: float = 0.0,
           mono: bool = False) -> QLabel:
    lab = QLabel()
    lab.setFont(theme.font(size, weight, mono=mono, spacing=spacing))
    lab.setStyleSheet(f"color: {color}; background: transparent;")
    lab.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
    return lab


class _Progress(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(160, 2)
        self.value = -1.0
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(30)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.02) % 1.0
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(255, 255, 255, 25))
        w = self.width()
        if self.value < 0:
            x = int((self._phase * 1.4 - 0.4) * w)
            p.fillRect(max(0, x), 0, int(w * 0.4), 2, QColor(255, 255, 255, 150))
        else:
            p.fillRect(0, 0, int(w * self.value), 2, QColor(255, 255, 255, 170))

    def stop(self) -> None:
        self._timer.stop()


class StatusView(QWidget):
    """READY / LISTENING ... plus what was said and NEXUS's short reply."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.state_label = _label(9.5, theme.TEXT_DIM, QFont.Weight.Medium, spacing=4.5)
        self.transcript = _label(11.0, theme.TEXT_DIM)
        self.reply = _label(13.5, theme.TEXT, QFont.Weight.Light)
        self.extra = _label(9.5, theme.TEXT_FAINT, mono=True)
        self.meta = _label(8.5, theme.TEXT_FAINT, mono=True)
        self.job = _label(9.0, theme.ACCENT_VIOLET, mono=True)   # background writing job
        self.boot_title = _label(20, theme.TEXT, QFont.Weight.Light, spacing=10)
        self.boot_detail = _label(10.0, theme.TEXT_DIM)
        self.progress = _Progress()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(6)
        layout.addWidget(self.boot_title)
        layout.addWidget(self.state_label)
        layout.addWidget(self.job)
        layout.addSpacing(6)
        layout.addWidget(self.boot_detail)
        layout.addWidget(self.progress, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.transcript)
        layout.addWidget(self.reply)
        layout.addWidget(self.extra)
        layout.addWidget(self.meta)
        layout.addStretch(1)

        self._reply_fx = QGraphicsOpacityEffect(self.reply)
        self.reply.setGraphicsEffect(self._reply_fx)
        self._fade = QPropertyAnimation(self._reply_fx, b"opacity", self)
        self._fade.setDuration(1600)
        self._fade.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)
        self._generation = 0
        self._booted = False
        self.transcript.hide()
        self.reply.hide()
        self.extra.hide()
        self.meta.hide()
        self.job.hide()
        self.set_booting(tr("Preparing local AI core..."))

    # ------------------------------------------------------------ boot
    def set_booting(self, detail: str, fraction: float = -1.0) -> None:
        self.boot_title.setText("NEXUS")
        self.boot_title.show()
        self.state_label.hide()
        self.boot_detail.setText(detail)
        self.boot_detail.show()
        self.progress.value = fraction
        self.progress.show()

    def boot_done(self, ok: bool, message: str) -> None:
        self._booted = True
        self.boot_title.hide()
        self.progress.hide()
        self.progress.stop()
        self.state_label.show()
        if ok:
            self.boot_detail.hide()
        else:
            self.boot_detail.setStyleSheet(f"color: {theme.WARN}; background: transparent;")
            self.boot_detail.setText(message or tr("The AI core could not start."))

    def set_background_task(self, text: str | None) -> None:
        """Small line for work that continues after READY (e.g. voice model download)."""
        if text:
            self.meta.setText(text)
            self.meta.show()
        else:
            self.meta.hide()

    def set_job(self, text: str) -> None:
        """'✎ app_economia.py: 42 linhas' while NEXUS writes in the background."""
        if text:
            self.job.setText("✎  " + text)
            self.job.show()
        else:
            self.job.hide()

    def progress_update(self, detail: str, fraction: float = -1.0) -> None:
        if self._booted:
            self.set_background_task(detail)
        else:
            self.set_booting(detail, fraction)

    # ----------------------------------------------------------- state
    def set_state(self, state: NexusState) -> None:
        color = {NexusState.LISTENING: theme.ACCENT, NexusState.PROCESSING: theme.ACCENT_VIOLET,
                 NexusState.ERROR: theme.WARN, NexusState.CONFIRMATION: theme.WARN}.get(state, theme.TEXT_DIM)
        self.state_label.setStyleSheet(f"color: {color}; background: transparent;")
        self.state_label.setText(tr(state.label))
        if state is NexusState.LISTENING:
            self.transcript.hide()
            self.reply.hide()
            self.extra.hide()

    def _new_generation(self) -> None:
        """New content: cancel any fade/clear still pending for the previous reply."""
        self._generation += 1
        self._hide_timer.stop()
        self._fade.stop()
        self._reply_fx.setOpacity(1.0)

    def show_transcript(self, text: str) -> None:
        self._new_generation()
        self.transcript.setText(f"“{text}”" if text != "…" else "…")
        self.transcript.show()
        self.reply.hide()
        self.extra.hide()
        self.meta.hide()

    def show_reply(self, text: str, success: bool, extra: list[str], meta: str = "") -> None:
        self._new_generation()
        self.reply.setStyleSheet(f"color: {theme.TEXT if success else theme.WARN}; background: transparent;")
        self.reply.setText(text)
        self.reply.show()
        if extra:
            self.extra.setText("\n".join(_short(p) for p in extra[:3]))
            self.extra.show()
        else:
            self.extra.hide()
        if meta:
            self.meta.setText(meta)
            self.meta.show()
        else:
            self.meta.hide()
        self._hide_timer.start(9000 if extra else 7000)

    def show_notice(self, text: str) -> None:
        self.show_reply(text, True, [])

    def _fade_out(self) -> None:
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.start()
        generation = self._generation
        QTimer.singleShot(1650, lambda: self._clear(generation))

    def _clear(self, generation: int) -> None:
        if generation == self._generation and self._reply_fx.opacity() < 0.05:
            self.reply.hide()
            self.transcript.hide()
            self.extra.hide()
            self.meta.hide()


def _short(path: str, limit: int = 70) -> str:
    return path if len(path) <= limit else "…" + path[-(limit - 1):]
