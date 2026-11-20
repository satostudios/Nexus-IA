"""Minimal settings window: General, Audio, Voice, AI, System, Debug."""

from __future__ import annotations

import logging
import os
import time
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QSlider, QSpinBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from app import paths
from app.audio.audio_analyzer import AudioAnalyzer
from app.audio.microphone import TARGET_RATE, Microphone, list_input_devices, list_output_devices
from app.config import autostart
from app.config.settings import (ACTIVATION_CONVERSATION, ACTIVATION_HOLD, ACTIVATION_TOGGLE, DEFAULT_STT,
                                 DEFAULT_VOICES, Settings)
from app.i18n import tr
from app.ui import theme
from app.ui.icons import IconButton

log = logging.getLogger("nexus.settings_ui")

CURATED_VOICES = ["pt_BR-faber-medium", "pt_BR-cadu-medium", "pt_BR-jeff-medium", "pt_BR-edresson-low",
                  "en_US-lessac-medium", "en_US-ryan-medium", "en_US-amy-medium", "en_GB-alan-medium"]
STT_MODELS = ["small", "base", "tiny", "base.en", "small.en"]


def _style() -> str:
    return f"""
    QDialog {{ background: #050506; }}
    QLabel {{ color: {theme.TEXT_DIM}; background: transparent; }}
    QListWidget {{ background: transparent; border: none; color: {theme.TEXT_DIM}; outline: none; }}
    QListWidget::item {{ padding: 9px 12px; border-left: 1px solid transparent; }}
    QListWidget::item:selected {{ color: {theme.TEXT}; background: rgba(255,255,255,0.05);
                                  border-left: 1px solid rgba(255,255,255,0.7); }}
    QListWidget::item:hover {{ color: {theme.TEXT}; }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: rgba(255,255,255,0.04); color: {theme.TEXT}; border: 1px solid rgba(255,255,255,0.12);
        border-radius: 2px; padding: 5px 8px; min-height: 18px; selection-background-color: rgba(255,255,255,0.25); }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: rgba(255,255,255,0.35); }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{ background: #0c0c0e; color: {theme.TEXT}; border: 1px solid rgba(255,255,255,0.15);
        selection-background-color: rgba(255,255,255,0.12); }}
    QCheckBox {{ color: {theme.TEXT}; spacing: 10px; }}
    QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid rgba(255,255,255,0.35); border-radius: 2px; }}
    QCheckBox::indicator:checked {{ background: rgba(255,255,255,0.85); }}
    QSlider::groove:horizontal {{ height: 2px; background: rgba(255,255,255,0.15); }}
    QSlider::sub-page:horizontal {{ background: rgba(255,255,255,0.6); }}
    QSlider::handle:horizontal {{ background: {theme.TEXT}; width: 10px; height: 10px; margin: -4px 0; border-radius: 5px; }}
    QPushButton {{ background: transparent; color: {theme.TEXT}; border: 1px solid rgba(255,255,255,0.2);
        border-radius: 2px; padding: 6px 14px; }}
    QPushButton:hover {{ background: rgba(255,255,255,0.07); }}
    QPushButton#primary {{ border-color: rgba(255,255,255,0.6); }}
    """


class _Meter(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(4)
        self.level = 0.0

    def paintEvent(self, _e) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(255, 255, 255, 20))
        p.fillRect(0, 0, int(self.width() * self.level), self.height(), QColor(theme.ACCENT))


class SettingsWindow(QDialog):
    saved = Signal(bool)          # voice_changed (TTS voice, speech model or language)
    test_voice = Signal()
    notice = Signal(str)

    def __init__(self, settings: Settings, piper_voices: Callable[[], list[str]],
                 sapi_voices: Callable[[], list[str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._piper_voices = piper_voices
        self._sapi_voices = sapi_voices
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("NEXUS")
        self.setModal(True)
        self.resize(760, 540)
        self.setStyleSheet(_style())
        self.setFont(theme.font(10))

        title = QLabel(tr("SETTINGS"))
        title.setFont(theme.font(8.5, QFont.Weight.Medium, spacing=4.0))
        close = IconButton("close", tr("Close"), 30)
        close.clicked.connect(self.reject)
        head = QHBoxLayout()
        head.setContentsMargins(20, 10, 10, 0)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(close)

        self.nav = QListWidget()
        self.nav.setFixedWidth(150)
        self.pages = QStackedWidget()
        for name, page in ((tr("General"), self._general_page()), (tr("Audio"), self._audio_page()),
                           (tr("Voice"), self._voice_page()), (tr("AI"), self._ai_page()),
                           (tr("System"), self._system_page()), (tr("Debug"), self._debug_page())):
            self.nav.addItem(name)
            self.pages.addWidget(page)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)

        body = QHBoxLayout()
        body.setContentsMargins(10, 10, 20, 0)
        body.setSpacing(20)
        body.addWidget(self.nav)
        body.addWidget(self.pages, 1)

        self.note = QLabel("")
        self.note.setStyleSheet(f"color: {theme.TEXT_FAINT};")
        cancel = QPushButton(tr("Cancel"))
        save = QPushButton(tr("Save"))
        save.setObjectName("primary")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._save)
        foot = QHBoxLayout()
        foot.setContentsMargins(20, 10, 20, 16)
        foot.addWidget(self.note, 1)
        foot.addWidget(cancel)
        foot.addWidget(save)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addLayout(head)
        root.addLayout(body, 1)
        root.addLayout(foot)

        self._test_mic: Microphone | None = None
        self._analyzer = AudioAnalyzer(TARGET_RATE)
        self._meter_timer = QTimer(self)
        self._meter_timer.timeout.connect(self._update_meter)
        self._test_until = 0.0

    # ----------------------------------------------------------- pages
    @staticmethod
    def _form() -> tuple[QWidget, QFormLayout]:
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(0, 6, 0, 0)
        f.setHorizontalSpacing(18)
        f.setVerticalSpacing(12)
        f.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        return w, f

    def _general_page(self) -> QWidget:
        w, f = self._form()
        self.language = QComboBox()
        self.language.addItem(tr("Automatic (Windows)"), "auto")
        self.language.addItem("Português (Brasil)", "pt")
        self.language.addItem("English", "en")
        self._select(self.language, self.settings.general.language)
        self.user_name = QLineEdit(self.settings.system.user_name)
        self.user_name.setPlaceholderText(tr("Optional"))
        self.engine_web = QComboBox()
        for name, value in (("Google", "google"), ("Bing", "bing"), ("DuckDuckGo", "duckduckgo")):
            self.engine_web.addItem(name, value)
        self._select(self.engine_web, self.settings.ai.search_engine)
        f.addRow(tr("Language"), self.language)
        f.addRow(tr("Your name"), self.user_name)
        f.addRow(tr("Web search"), self.engine_web)
        hint = QLabel(tr("NEXUS understands and answers in the chosen language. Changing it switches the "
                         "voice and the speech model (downloaded once if needed)."))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT_FAINT};")
        f.addRow("", hint)
        return w

    def _audio_page(self) -> QWidget:
        s = self.settings.audio
        w, f = self._form()
        self.mic = QComboBox()
        self.mic.addItem(tr("System default"), "")
        for d in list_input_devices():
            self.mic.addItem(d.name + (f"  {tr('(default)')}" if d.is_default else ""), d.name)
        self._select(self.mic, s.input_device)
        self.out = QComboBox()
        self.out.addItem(tr("System default"), "")
        for name in list_output_devices():
            self.out.addItem(name, name)
        self._select(self.out, s.output_device)
        self.sens = QSlider(Qt.Orientation.Horizontal)
        self.sens.setRange(25, 400)
        self.sens.setValue(int(s.sensitivity * 100))
        self.rate = QComboBox()
        for r in (16000, 44100, 48000):
            self.rate.addItem(f"{r} Hz", r)
        self._select(self.rate, s.sample_rate)
        self.mode = QComboBox()
        self.mode.addItem(tr("Conversation (listen, answer, listen again)"), ACTIVATION_CONVERSATION)
        self.mode.addItem(tr("One command per click"), ACTIVATION_TOGGLE)
        self.mode.addItem(tr("Hold to talk"), ACTIVATION_HOLD)
        self._select(self.mode, s.activation_mode)
        self.silence = QSpinBox()
        self.silence.setRange(300, 4000)
        self.silence.setSingleStep(100)
        self.silence.setSuffix(" ms")
        self.silence.setValue(s.silence_timeout_ms)
        self.chime = QCheckBox(tr("Sound when listening starts"))
        self.chime.setChecked(s.chime)
        self.meter = _Meter()
        test = QPushButton(tr("Test microphone"))
        test.clicked.connect(self._start_mic_test)
        row = QHBoxLayout()
        row.addWidget(test)
        row.addWidget(self.meter, 1)
        f.addRow(tr("Microphone"), self.mic)
        f.addRow(tr("Speaker"), self.out)
        f.addRow(tr("Sensitivity"), self.sens)
        f.addRow(tr("Sample rate"), self.rate)
        f.addRow(tr("Activation"), self.mode)
        f.addRow(tr("Silence to stop"), self.silence)
        f.addRow("", self.chime)
        f.addRow("", row)
        return w

    def _voice_page(self) -> QWidget:
        v = self.settings.voice
        w, f = self._form()
        self.voice_on = QCheckBox(tr("Speak responses"))
        self.voice_on.setChecked(v.enabled)
        self.engine = QComboBox()
        self.engine.addItem(tr("Piper (neural, local)"), "piper")
        self.engine.addItem(tr("Windows voices (built in)"), "sapi")
        self._select(self.engine, v.engine)
        self.voice = QComboBox()
        self.engine.currentIndexChanged.connect(self._fill_voices)
        self._fill_voices()
        self.speed = QSlider(Qt.Orientation.Horizontal)
        self.speed.setRange(50, 200)
        self.speed.setValue(int(v.speed * 100))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(int(v.volume * 100))
        test = QPushButton(tr("Test voice"))
        test.clicked.connect(self._test_voice)
        f.addRow("", self.voice_on)
        f.addRow(tr("Engine"), self.engine)
        f.addRow(tr("Voice"), self.voice)
        f.addRow(tr("Speed"), self.speed)
        f.addRow(tr("Volume"), self.volume)
        f.addRow("", test)
        return w

    def _fill_voices(self) -> None:
        self.voice.clear()
        if self.engine.currentData() == "piper":
            lang = self.settings.lang()
            self.voice.addItem(tr("Language default ({name})", name=DEFAULT_VOICES[lang]), "")
            installed = self._piper_voices()
            for name in installed:
                self.voice.addItem(name, name)
            for name in CURATED_VOICES:
                if name not in installed:
                    self.voice.addItem(f"{name}  {tr('(download ~60 MB)')}", name)
            self._select(self.voice, self.settings.voice.piper_voice)
        else:
            for name in self._sapi_voices():
                self.voice.addItem(name, name)
            self._select(self.voice, self.settings.voice.sapi_voice)

    def _ai_page(self) -> QWidget:
        a = self.settings.ai
        w, f = self._form()
        self.model_path = QLineEdit(a.model_path)
        self.model_path.setPlaceholderText(tr("Base Needle 3 (downloaded automatically)"))
        self.index_path = QLineEdit(a.tool_index_path)
        self.index_path.setPlaceholderText(str(self.settings.tool_index_file()))
        self.exec_th = QDoubleSpinBox()
        self.exec_th.setRange(0.0, 1.0)
        self.exec_th.setSingleStep(0.05)
        self.exec_th.setValue(a.execute_threshold)
        self.ref_th = QDoubleSpinBox()
        self.ref_th.setRange(0.0, 1.0)
        self.ref_th.setSingleStep(0.05)
        self.ref_th.setValue(a.refuse_threshold)
        self.coverage = QCheckBox(tr("Only act on supported command phrasings"))
        self.coverage.setChecked(a.require_trigger_coverage)
        self.stt = QComboBox()
        self.stt.addItem(tr("Language default ({name})", name=DEFAULT_STT[self.settings.lang()]), "")
        for name in STT_MODELS:
            self.stt.addItem(name, name)
        self._select(self.stt, a.stt_model)
        f.addRow(tr("Needle model (.cact)"), self._with_browse(self.model_path, "Needle model (*.cact)"))
        f.addRow(tr("Tool index"), self._with_browse(self.index_path, "Tool index (*.idx)", save=True))
        f.addRow(tr("Act at confidence ≥"), self.exec_th)
        f.addRow(tr("Refuse below"), self.ref_th)
        f.addRow("", self.coverage)
        f.addRow(tr("Speech model"), self.stt)
        from app.ai.writer import WRITER_MODELS

        self.writer = QComboBox()
        for key, model in WRITER_MODELS.items():
            note = tr("best quality") if key == "qwen3.5-4b" else tr("faster")
            self.writer.addItem(f"{model.label} — {note} ({model.size / 1e9:.1f} GB)", key)
        self._select(self.writer, a.writer_model)
        self.code_editor = QLineEdit(a.code_editor)
        self.code_editor.setPlaceholderText(tr("Automatic (Antigravity, VS Code, Cursor… or Notepad)"))
        self.projects = QLineEdit(a.projects_folder)
        self.projects.setPlaceholderText(str(self.settings.output_folder("code").parent))
        f.addRow(tr("Writing model"), self.writer)
        f.addRow(tr("Code editor"), self.code_editor)
        f.addRow(tr("Save creations in"), self._with_browse(self.projects, "", folder=True))
        hint = QLabel(tr("Safe actions at or above the first threshold run immediately; between the two "
                         "NEXUS asks first. Deleting, moving, copying, renaming, shutdown and restart always ask. "
                         "Model changes apply after restart."))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT_FAINT};")
        f.addRow("", hint)
        return w

    def _system_page(self) -> QWidget:
        s = self.settings.system
        w, f = self._form()
        self.autostart = QCheckBox(tr("Start with Windows"))
        try:
            self.autostart.setChecked(s.start_with_windows and autostart.is_enabled())
        except OSError:
            self.autostart.setChecked(False)
        self.minimized = QCheckBox(tr("Start minimized"))
        self.minimized.setChecked(s.start_minimized)
        self.telemetry = QCheckBox(tr("Allow Needle's anonymous usage counts"))
        self.telemetry.setChecked(s.telemetry)
        f.addRow("", self.autostart)
        f.addRow("", self.minimized)
        f.addRow("", self.telemetry)
        return w

    def _debug_page(self) -> QWidget:
        d = self.settings.debug
        w, f = self._form()
        self.logs = QCheckBox(tr("Write detailed logs"))
        self.logs.setChecked(d.enable_logs)
        self.level = QComboBox()
        for lv in ("INFO", "DEBUG", "WARNING"):
            self.level.addItem(lv, lv)
        self._select(self.level, d.log_level)
        self.show_calls = QCheckBox(tr("Show tool calls (debug panel)"))
        self.show_calls.setChecked(d.show_tool_calls)
        self.show_conf = QCheckBox(tr("Show confidence under replies"))
        self.show_conf.setChecked(d.show_confidence)
        open_logs = QPushButton(tr("Open log folder"))
        open_logs.clicked.connect(lambda: os.startfile(str(paths.logs_dir())))  # type: ignore[attr-defined]
        open_data = QPushButton(tr("Open data folder"))
        open_data.clicked.connect(lambda: os.startfile(str(paths.config_dir())))  # type: ignore[attr-defined]
        row = QHBoxLayout()
        row.addWidget(open_logs)
        row.addWidget(open_data)
        row.addStretch(1)
        f.addRow("", self.logs)
        f.addRow(tr("Log level"), self.level)
        f.addRow("", self.show_calls)
        f.addRow("", self.show_conf)
        f.addRow("", row)
        return w

    # --------------------------------------------------------- helpers
    @staticmethod
    def _select(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    def _with_browse(self, edit: QLineEdit, filt: str, save: bool = False, folder: bool = False) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        btn = QPushButton("…")
        btn.setFixedWidth(36)

        def browse() -> None:
            if folder:
                path = QFileDialog.getExistingDirectory(self, "NEXUS", edit.text())
            elif save:
                path, _ = QFileDialog.getSaveFileName(self, "NEXUS", edit.text(), filt)
            else:
                path, _ = QFileDialog.getOpenFileName(self, "NEXUS", edit.text(), filt)
            if path:
                edit.setText(os.path.normpath(path))

        btn.clicked.connect(browse)
        row.addWidget(btn)
        return w

    def _start_mic_test(self) -> None:
        self._stop_mic_test()
        self._analyzer.reset()
        self._test_mic = Microphone(lambda b: self._analyzer.process(b), lambda m: self.note.setText(m))
        try:
            self._test_mic.start(self.mic.currentData() or "", TARGET_RATE)
        except Exception as exc:
            self.note.setText(str(exc))
            self._test_mic = None
            return
        self.note.setText(tr("Listening on {name}…", name=self._test_mic.device_name))
        self._test_until = time.monotonic() + 6
        self._meter_timer.start(33)

    def _update_meter(self) -> None:
        self.meter.level = self._analyzer.levels().volume
        self.meter.update()
        if time.monotonic() > self._test_until:
            self._stop_mic_test()
            self.note.setText("")

    def _stop_mic_test(self) -> None:
        self._meter_timer.stop()
        if self._test_mic is not None:
            self._test_mic.stop()
            self._test_mic = None
        self.meter.level = 0.0
        self.meter.update()

    def _test_voice(self) -> None:
        changed = self._apply()
        self.saved.emit(changed)
        QTimer.singleShot(2500 if changed else 200, self.test_voice.emit)

    # ------------------------------------------------------------ save
    def _apply(self) -> bool:
        s = self.settings
        old = (s.lang(), s.voice.engine, s.piper_voice_name(), s.voice.sapi_voice, s.stt_model_name())
        s.general.language = self.language.currentData()
        s.system.user_name = self.user_name.text().strip()
        s.ai.search_engine = self.engine_web.currentData()
        s.audio.input_device = self.mic.currentData() or ""
        s.audio.output_device = self.out.currentData() or ""
        s.audio.sensitivity = self.sens.value() / 100
        s.audio.sample_rate = int(self.rate.currentData())
        s.audio.activation_mode = self.mode.currentData()
        s.audio.silence_timeout_ms = self.silence.value()
        s.audio.chime = self.chime.isChecked()
        s.voice.enabled = self.voice_on.isChecked()
        s.voice.engine = self.engine.currentData()
        if s.voice.engine == "piper":
            s.voice.piper_voice = self.voice.currentData() or ""
        else:
            s.voice.sapi_voice = self.voice.currentData() or ""
        s.voice.speed = self.speed.value() / 100
        s.voice.volume = self.volume.value() / 100
        s.ai.model_path = self.model_path.text().strip()
        s.ai.tool_index_path = self.index_path.text().strip()
        s.ai.execute_threshold = self.exec_th.value()
        s.ai.refuse_threshold = self.ref_th.value()
        s.ai.require_trigger_coverage = self.coverage.isChecked()
        s.ai.stt_model = self.stt.currentData() or ""
        s.ai.writer_model = self.writer.currentData() or s.ai.writer_model
        s.ai.code_editor = self.code_editor.text().strip()
        s.ai.projects_folder = self.projects.text().strip()
        s.system.start_minimized = self.minimized.isChecked()
        s.system.telemetry = self.telemetry.isChecked()
        s.debug.enable_logs = self.logs.isChecked()
        s.debug.log_level = self.level.currentData()
        s.debug.show_tool_calls = self.show_calls.isChecked()
        s.debug.show_confidence = self.show_conf.isChecked()
        want_autostart = self.autostart.isChecked()
        if want_autostart != s.system.start_with_windows or want_autostart:
            try:
                autostart.set_enabled(want_autostart, s.system.start_minimized)
                s.system.start_with_windows = want_autostart
            except OSError:
                log.exception("could not change autostart")
                self.note.setText(tr("Couldn't change the Windows startup setting."))
        s.validate()
        s.save()
        new = (s.lang(), s.voice.engine, s.piper_voice_name(), s.voice.sapi_voice, s.stt_model_name())
        return old != new

    def _save(self) -> None:
        language_changed = self.language.currentData() != self.settings.general.language
        voice_changed = self._apply()
        self._stop_mic_test()
        self.saved.emit(voice_changed)
        if language_changed:
            self.notice.emit(tr("Language changes apply fully after restarting NEXUS."))
        self.accept()

    def reject(self) -> None:
        self._stop_mic_test()
        super().reject()

    # frameless drag
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 44 and self.windowHandle():
            self.windowHandle().startSystemMove()
        super().mousePressEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(5, 5, 6))
        p.setPen(QColor(255, 255, 255, 28))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))
