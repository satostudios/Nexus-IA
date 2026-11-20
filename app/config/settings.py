"""User configuration, stored as JSON in ``%APPDATA%\\NEXUS\\config.json``.

Every value has a default so a missing or partial file is always valid. Unknown
keys are ignored and invalid values fall back to their defaults, so a hand-edited
file cannot stop NEXUS from starting.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app import paths

log = logging.getLogger("nexus.settings")

ACTIVATION_CONVERSATION = "conversation"  # click once: listen, answer, listen again... until turned off
ACTIVATION_TOGGLE = "toggle"              # click the mic for one command, stops on silence
ACTIVATION_HOLD = "hold"                  # hold the mic button while speaking

LANGUAGES = ("auto", "pt", "en")
DEFAULT_VOICES = {"pt": "pt_BR-faber-medium", "en": "en_US-lessac-medium"}
# Measured on pt-BR speech: whisper "base" misheard 6/10 commands, "small" 1-2/10 (~3 s each on an i5-1235U).
DEFAULT_STT = {"pt": "small", "en": "base.en"}


@dataclass
class GeneralSettings:
    language: str = "auto"          # "auto" (Windows UI language), "pt" or "en"


@dataclass
class AudioSettings:
    input_device: str = ""          # device name; "" = Windows default input
    output_device: str = ""         # device name; "" = Windows default output
    sensitivity: float = 1.0        # analyzer / voice-detection gain, 0.25 .. 4.0
    sample_rate: int = 16000        # capture rate; audio is delivered to STT at 16 kHz
    activation_mode: str = ACTIVATION_CONVERSATION
    silence_timeout_ms: int = 1000  # stop after this much trailing silence
    max_record_seconds: int = 20
    chime: bool = True              # short tone when NEXUS starts listening


@dataclass
class VoiceSettings:
    enabled: bool = True
    engine: str = "piper"           # "piper" or "sapi" (Windows built-in, fallback)
    piper_voice: str = ""           # "" = default voice for the language
    sapi_voice: str = ""
    speed: float = 1.0              # 0.5 .. 2.0
    volume: float = 0.9             # 0.0 .. 1.0


@dataclass
class AISettings:
    model_path: str = ""            # custom Needle .cact; "" = base Needle 3 weights
    tool_index_path: str = ""       # "" = %LOCALAPPDATA%\NEXUS\cache\needle_tools.idx
    execute_threshold: float = 0.30  # safe actions at/above this run immediately
    refuse_threshold: float = 0.10   # below this nothing runs (= the engine's own withholding floor)
    require_trigger_coverage: bool = True  # refuse requests outside the declared command surface
    max_steps: int = 5
    stt_model: str = ""             # "" = default for the language (pt: small, en: base.en)
    search_engine: str = "google"   # google | bing | duckduckgo
    # Writing model (write_text / write_code), see app/ai/writer.py
    writer_model: str = "qwen3.5-4b"  # qwen3.5-4b (quality) | qwen3.5-2b (speed)
    writer_threads: int = 0         # 0 = llama.cpp's own choice (fastest measured)
    writer_idle_minutes: int = 10   # stop the model after this long unused (frees ~3 GB RAM)
    code_editor: str = ""           # "" = first installed of Antigravity, VS Code, Cursor, ... Notepad
    projects_folder: str = ""       # "" = Documents\NEXUS


@dataclass
class SystemSettings:
    user_name: str = ""
    start_with_windows: bool = False
    start_minimized: bool = False
    telemetry: bool = False         # Needle's anonymous usage counts (off by default)


@dataclass
class FileSettings:
    extra_search_roots: list[str] = field(default_factory=list)
    default_create_folder: str = "Desktop"
    index_max_depth: int = 7


@dataclass
class DebugSettings:
    enable_logs: bool = True
    log_level: str = "INFO"
    show_tool_calls: bool = False
    show_confidence: bool = False


@dataclass
class Settings:
    general: GeneralSettings = field(default_factory=GeneralSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    ai: AISettings = field(default_factory=AISettings)
    system: SystemSettings = field(default_factory=SystemSettings)
    files: FileSettings = field(default_factory=FileSettings)
    debug: DebugSettings = field(default_factory=DebugSettings)

    _path: Path | None = field(default=None, repr=False, compare=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    # ------------------------------------------------------------------ io
    @classmethod
    def default_path(cls) -> Path:
        return paths.config_dir() / "config.json"

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or cls.default_path()
        settings = cls()
        settings._path = path
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                settings._apply(raw)
                if isinstance(raw, dict) and "general" not in raw:
                    settings._migrate_v1()
            except (OSError, ValueError) as exc:
                log.warning("Could not read %s (%s); using defaults", path, exc)
        settings.validate()
        return settings

    def _migrate_v1(self) -> None:
        """1.0 saved its English-only defaults explicitly; let 1.1's language defaults apply."""
        if self.voice.piper_voice == "en_US-lessac-medium":
            self.voice.piper_voice = ""
        if self.ai.stt_model == "base.en":
            self.ai.stt_model = ""
        if self.audio.activation_mode == ACTIVATION_TOGGLE:
            self.audio.activation_mode = ACTIVATION_CONVERSATION
        if self.audio.silence_timeout_ms == 900:
            self.audio.silence_timeout_ms = 1000
        if self.ai.execute_threshold == 0.40:
            self.ai.execute_threshold = 0.30
        if self.ai.refuse_threshold == 0.15:
            self.ai.refuse_threshold = 0.10
        log.info("migrated settings from 1.0")

    def save(self) -> None:
        path = self._path or self.default_path()
        with self._lock:
            data = self.to_dict()
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(path)
        log.info("Settings saved to %s", path)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in dataclasses.fields(self):
            if f.name.startswith("_"):
                continue
            out[f.name] = dataclasses.asdict(getattr(self, f.name))
        return out

    def _apply(self, raw: dict[str, Any]) -> None:
        if not isinstance(raw, dict):
            return
        for f in dataclasses.fields(self):
            if f.name.startswith("_") or not isinstance(raw.get(f.name), dict):
                continue
            section = getattr(self, f.name)
            for sub in dataclasses.fields(section):
                if sub.name not in raw[f.name]:
                    continue
                value = raw[f.name][sub.name]
                default = getattr(section, sub.name)
                if isinstance(default, bool):
                    ok = isinstance(value, bool)
                elif isinstance(default, int):
                    ok = isinstance(value, int) and not isinstance(value, bool)
                elif isinstance(default, float):
                    ok = isinstance(value, (int, float)) and not isinstance(value, bool)
                    value = float(value) if ok else value
                elif isinstance(default, list):
                    ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
                else:
                    ok = isinstance(value, str)
                if ok:
                    setattr(section, sub.name, value)
                else:
                    log.warning("Ignoring invalid setting %s.%s=%r", f.name, sub.name, value)

    # ------------------------------------------------------------ validate
    def validate(self) -> None:
        def clamp(v: float, lo: float, hi: float) -> float:
            return max(lo, min(hi, v))

        a = self.audio
        a.sensitivity = clamp(a.sensitivity, 0.25, 4.0)
        if a.sample_rate not in (16000, 22050, 44100, 48000):
            a.sample_rate = 16000
        if a.activation_mode not in (ACTIVATION_CONVERSATION, ACTIVATION_TOGGLE, ACTIVATION_HOLD):
            a.activation_mode = ACTIVATION_CONVERSATION
        if self.general.language not in LANGUAGES:
            self.general.language = "auto"
        if self.ai.search_engine not in ("google", "bing", "duckduckgo"):
            self.ai.search_engine = "google"
        from app.ai.writer import DEFAULT_WRITER, WRITER_MODELS

        if self.ai.writer_model not in WRITER_MODELS:
            self.ai.writer_model = DEFAULT_WRITER
        self.ai.writer_threads = int(clamp(self.ai.writer_threads, 0, 64))
        self.ai.writer_idle_minutes = int(clamp(self.ai.writer_idle_minutes, 0, 240))
        a.silence_timeout_ms = int(clamp(a.silence_timeout_ms, 300, 4000))
        a.max_record_seconds = int(clamp(a.max_record_seconds, 3, 60))

        v = self.voice
        v.speed = clamp(v.speed, 0.5, 2.0)
        v.volume = clamp(v.volume, 0.0, 1.0)
        if v.engine not in ("piper", "sapi"):
            v.engine = "piper"

        ai = self.ai
        ai.execute_threshold = clamp(ai.execute_threshold, 0.0, 1.0)
        ai.refuse_threshold = clamp(ai.refuse_threshold, 0.0, ai.execute_threshold)
        ai.max_steps = int(clamp(ai.max_steps, 1, 8))

        self.files.index_max_depth = int(clamp(self.files.index_max_depth, 2, 12))
        if self.debug.log_level.upper() not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            self.debug.log_level = "INFO"

    # ------------------------------------------------------------- helpers
    def lang(self) -> str:
        """Resolved language: 'pt' or 'en'."""
        if self.general.language in ("pt", "en"):
            return self.general.language
        from app.i18n import system_language

        return system_language()

    def piper_voice_name(self) -> str:
        return self.voice.piper_voice or DEFAULT_VOICES[self.lang()]

    def stt_model_name(self) -> str:
        return self.ai.stt_model or DEFAULT_STT[self.lang()]

    def stt_language(self) -> str:
        return self.lang()

    def output_folder(self, kind: str) -> Path:
        """Where NEXUS saves what it writes: ``Documents\\NEXUS\\Projetos`` / ``...\\Textos``."""
        from app.i18n import tr

        root = Path(self.ai.projects_folder) if self.ai.projects_folder else \
            (paths.user_folder("documents") or Path.home() / "Documents") / "NEXUS"
        return root / (tr("Projects") if kind == "code" else tr("Texts"))

    def tool_index_file(self) -> Path:
        if self.ai.tool_index_path:
            return Path(self.ai.tool_index_path)
        return paths.cache_dir() / "needle_tools.idx"
