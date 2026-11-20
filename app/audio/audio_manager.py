"""Coordinates microphone, analysis, speech recognition and speech output.

Exposes Qt signals to the assistant and a normalised level snapshot to the UI::

    {"volume": 0.42, "bass": 0.63, "mid": 0.35, "treble": 0.21, "energy": .., "speech": ..,
     "listening": True, "speaking": False}

Threads: PortAudio calls the microphone and playback callbacks on its own
threads; transcription runs on a dedicated STT thread; speech synthesis on the
TTS thread. Nothing here blocks the UI thread.
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from app.audio.audio_analyzer import AudioAnalyzer, AudioLevels
from app.audio.microphone import TARGET_RATE, Microphone, MicrophoneError
from app.audio.stt import SpeechRecognizer
from app.audio.tts import LocalTTS, PiperSynthesizer, SapiSynthesizer, SpeechSynthesizer, TextToSpeech
from app.config.settings import ACTIVATION_HOLD, Settings
from app.i18n import tr

log = logging.getLogger("nexus.audio")

_SPEECH_ONSET_BLOCKS = 3        # 60 ms of speech before we believe it
_NO_SPEECH_TIMEOUT_S = 7.0
# A dead/virtual input carries at most dither: +-1 LSB of 16-bit audio (measured:
# peak 3.05e-5 = 1/32768, 3 distinct values). A real microphone in a silent room
# measured peak 1.5e-4 (5 LSB). Anything up to 2 LSB is treated as "no signal".
_DEAD_PEAK = 2.2 / 32768
_LIVE_PEAK = 3.0 / 32768
_PREROLL_BLOCKS = 25            # 0.5 s kept before speech while waiting in conversation mode


class AudioManager(QObject):
    listening_changed = Signal(bool)
    speaking_changed = Signal(bool)
    transcribing = Signal()
    transcript_ready = Signal(str)
    no_speech = Signal()
    mic_error = Signal(str)
    tts_error = Signal(str)
    device_switched = Signal(str)
    _auto_stop = Signal()
    _probe_done = Signal(object)
    _speech_started = Signal()
    _speech_finished = Signal(bool)
    _mic_failed = Signal(str)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.mic_analyzer = AudioAnalyzer(TARGET_RATE, settings.audio.sensitivity)
        self.tts_analyzer = AudioAnalyzer(22050, 1.0)
        self.microphone = Microphone(self._on_mic_audio, self._mic_failed.emit)
        self.recognizer: SpeechRecognizer | None = None
        self.tts: TextToSpeech | None = None
        self._stt_pool = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="stt")
        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._listening = False
        self._speech_blocks = 0
        self._heard_speech = False
        self._last_speech = 0.0
        self._started_at = 0.0
        self._last_block = 0.0
        self._peak = 0.0
        self._probing = False
        self._continuous = False
        self._speech_onset = 0.0
        self._ignore_until = 0.0
        self._probe_done.connect(self._on_probe_done)
        self._auto_stop.connect(lambda: self.stop_listening(submit=True))
        self._speech_started.connect(lambda: self.speaking_changed.emit(True))
        self._speech_finished.connect(lambda _i: self.speaking_changed.emit(False))
        self._mic_failed.connect(self._handle_mic_failure)
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(500)
        self._watchdog.timeout.connect(self._check_mic)

    # ============================================================ setup
    def set_recognizer(self, recognizer: SpeechRecognizer) -> None:
        self.recognizer = recognizer

    def build_tts(self, voice_dir) -> str:  # noqa: ANN001 - Path
        """Create the TTS engine from settings. Returns the engine name in use."""
        v = self.settings.voice
        synth: SpeechSynthesizer
        engine = v.engine
        if engine == "piper":
            synth = PiperSynthesizer(voice_dir, self.settings.piper_voice_name())
            try:
                synth.load()
            except Exception as exc:
                log.warning("Piper unavailable (%s); falling back to Windows voices", exc)
                engine = "sapi"
        if engine == "sapi":
            synth = SapiSynthesizer(v.sapi_voice)
            synth.load()
        old = self.tts
        self.tts = LocalTTS(synth, self._on_tts_audio, self._speech_started.emit, self._speech_finished.emit,
                            self.tts_error.emit, speed=v.speed, volume=v.volume,
                            output_device=self.settings.audio.output_device)
        if isinstance(old, LocalTTS):
            old.shutdown()
        return engine

    def probe_input_devices(self, seconds: float = 0.35) -> str | None:
        """Find a microphone that actually hears something.

        Some machines default to a virtual input that only delivers digital
        silence (seen in testing: a "HitPaw Virtual Audio" default device at
        -97 dBFS while the built-in microphone worked). Returns the name of the
        best real device when the default one is silent, else ``None``.
        Runs off the UI thread; each device is opened for ``seconds``.
        """
        from app.audio.microphone import list_input_devices

        # Peak, not loudness, tells a dead input (dither only) from a real
        # microphone in a quiet room; see _DEAD_PEAK.
        peaks: dict[str, float] = {}
        levels: dict[str, float] = {}
        default_name = ""
        for dev in list_input_devices():
            blocks: list[np.ndarray] = []
            mic = Microphone(blocks.append, lambda _m: None)
            try:
                mic.start(dev.name, TARGET_RATE)
                time.sleep(seconds)
            except Exception:
                continue
            finally:
                mic.stop()
            if blocks:
                x = np.concatenate(blocks)
                peaks[dev.name] = float(np.max(np.abs(x)))
                levels[dev.name] = 20 * float(np.log10(np.sqrt(np.mean(np.square(x))) + 1e-9))
            if dev.is_default:
                default_name = dev.name
        log.info("microphone probe: %s", {k: (round(levels[k], 1), round(peaks[k], 5)) for k in levels})
        if default_name and peaks.get(default_name, 0.0) > _DEAD_PEAK:
            return None  # the default input is alive: keep following Windows
        live = {k: v for k, v in levels.items() if peaks.get(k, 0.0) >= _LIVE_PEAK}
        return max(live, key=live.get) if live else None

    def apply_settings(self) -> None:
        self.mic_analyzer.sensitivity = self.settings.audio.sensitivity
        if self.tts is not None:
            self.tts.configure(speed=self.settings.voice.speed, volume=self.settings.voice.volume,
                               output_device=self.settings.audio.output_device)

    # ======================================================== listening
    @property
    def listening(self) -> bool:
        return self._listening

    @property
    def speaking(self) -> bool:
        return bool(self.tts and self.tts.speaking)

    @property
    def speech_pending(self) -> bool:
        """Speaking, or about to (text queued / being synthesised)."""
        return bool(self.tts and getattr(self.tts, "busy", self.tts.speaking))

    @property
    def continuous(self) -> bool:
        return self._continuous

    def start_listening(self, continuous: bool = False, chime: bool = False) -> bool:
        """Open the microphone. ``continuous``: wait as long as it takes for speech
        (conversation mode) instead of giving up after a few silent seconds."""
        if self._listening:
            return True
        if self.tts is not None and self.tts.speaking:
            self.tts.stop()  # the user is talking over NEXUS: stop speaking immediately
        if self._probing:
            return False
        now = time.monotonic()
        with self._lock:
            self._chunks = []
            self._speech_blocks = 0
            self._heard_speech = False
            self._speech_onset = 0.0
            self._peak = 0.0
            self._started_at = self._last_block = self._last_speech = now
            self._continuous = continuous
            # Ignore the first moments: the chime (or the tail of NEXUS's own voice)
            # must neither calibrate the noise floor nor count as speech.
            self._ignore_until = now + (0.35 if chime else 0.12)
        if chime:
            self._play_chime()
        self.mic_analyzer.reset()
        try:
            self.microphone.start(self.settings.audio.input_device, self.settings.audio.sample_rate)
        except MicrophoneError as exc:
            self.mic_error.emit(str(exc))
            return False
        self._listening = True
        self._watchdog.start()
        self.listening_changed.emit(True)
        return True

    def stop_listening(self, submit: bool = True) -> None:
        if not self._listening:
            return
        self._listening = False
        self._watchdog.stop()
        self.microphone.stop()
        self.listening_changed.emit(False)
        with self._lock:
            audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.float32)
            heard = self._heard_speech
            self._chunks = []
        if not submit:
            return
        if not heard or audio.size < int(0.3 * TARGET_RATE):
            self.no_speech.emit()
            return
        self.transcribing.emit()
        self._stt_pool.submit(self._transcribe, audio)

    def cancel_listening(self) -> None:
        self.stop_listening(submit=False)

    def _transcribe(self, audio: np.ndarray) -> None:
        if self.recognizer is None:
            self.mic_error.emit(tr("Speech recognition is still loading."))
            return
        started = time.perf_counter()
        try:
            text = self.recognizer.transcribe(audio)
        except Exception:
            log.exception("transcription failed")
            self.mic_error.emit(tr("I couldn't understand the audio."))
            return
        log.info("transcribed %.1fs of audio in %.0f ms: %r", audio.size / TARGET_RATE,
                 (time.perf_counter() - started) * 1000, text)
        if text:
            self.transcript_ready.emit(text)
        else:
            self.no_speech.emit()

    # PortAudio thread ------------------------------------------------------
    def _on_mic_audio(self, block: np.ndarray) -> None:
        if not self._listening:
            return
        now = time.monotonic()
        if now < self._ignore_until:
            self._last_block = now
            return
        self.mic_analyzer.process(block)
        with self._lock:
            self._chunks.append(block)
            self._last_block = now
            self._peak = max(self._peak, float(np.max(np.abs(block))) if block.size else 0.0)
            if self.mic_analyzer.last_is_speech:
                self._speech_blocks += 1
                if self._speech_blocks >= _SPEECH_ONSET_BLOCKS and not self._heard_speech:
                    self._heard_speech = True
                    self._speech_onset = now
                self._last_speech = now
            else:
                self._speech_blocks = 0
            heard, last_speech, started = self._heard_speech, self._last_speech, self._started_at
            if self._continuous and not heard and len(self._chunks) > _PREROLL_BLOCKS:
                # Waiting in conversation mode: keep only a short pre-roll so the
                # start of the next sentence is not clipped and memory stays flat.
                del self._chunks[:-_PREROLL_BLOCKS]
        audio_cfg = self.settings.audio
        if heard and now - self._speech_onset > audio_cfg.max_record_seconds:
            self._auto_stop.emit()
        elif not heard and not self._continuous and now - started > audio_cfg.max_record_seconds:
            self._auto_stop.emit()
        elif audio_cfg.activation_mode != ACTIVATION_HOLD or self._continuous:
            if heard and now - last_speech > audio_cfg.silence_timeout_ms / 1000.0:
                self._auto_stop.emit()
            elif not heard and not self._continuous and now - started > _NO_SPEECH_TIMEOUT_S:
                self._auto_stop.emit()

    def _play_chime(self) -> None:
        """A soft two-note rise: 'I'm listening'."""
        try:
            import sounddevice as sd

            rate = 22050
            notes = []
            for freq in (660.0, 990.0):
                t = np.arange(int(rate * 0.075)) / rate
                env = np.minimum(1.0, np.minimum(t, t[::-1]) / 0.012)
                notes.append(0.12 * self.settings.voice.volume * env * np.sin(2 * np.pi * freq * t))
            from app.audio.microphone import find_device

            device = find_device(self.settings.audio.output_device, "output") if self.settings.audio.output_device else None
            sd.play(np.concatenate(notes).astype(np.float32), rate, device=device)
        except Exception:
            log.debug("chime failed", exc_info=True)

    def _check_mic(self) -> None:
        if not self._listening:
            return
        now = time.monotonic()
        if now - self._last_block > 1.5:
            self._handle_mic_failure(tr("The microphone stopped responding."))
            return
        # The Windows default input delivers pure digital silence (a disabled or
        # virtual device): find the microphone that actually hears the room.
        if now - self._started_at > 0.8 and self._peak <= _DEAD_PEAK and not self.settings.audio.input_device:
            self._listening = False
            self._watchdog.stop()
            self.microphone.stop()
            self._probing = True
            log.warning("default microphone %r is silent; probing inputs", self.microphone.device_name)
            threading.Thread(target=lambda: self._probe_done.emit(self.probe_input_devices()),
                             name="mic-probe", daemon=True).start()

    def _on_probe_done(self, choice: str | None) -> None:
        self._probing = False
        if not choice:
            self.listening_changed.emit(False)
            self.mic_error.emit(tr("The microphone isn't picking up any sound. Choose one in Settings."))
            return
        self.settings.audio.input_device = choice
        self.settings.save()
        log.info("switched microphone to %s", choice)
        self.device_switched.emit(choice)
        self.start_listening()

    def _handle_mic_failure(self, message: str) -> None:
        if self._listening:
            self._listening = False
            self._watchdog.stop()
            self.microphone.stop()
            self.listening_changed.emit(False)
        self.mic_error.emit(message)

    # ========================================================== speaking
    def speak(self, text: str) -> None:
        if self.tts is not None and self.settings.voice.enabled and text:
            self.tts.speak(text)

    def stop_speaking(self) -> None:
        if self.tts is not None:
            self.tts.stop()

    def _on_tts_audio(self, block: np.ndarray, samplerate: int) -> None:
        self.tts_analyzer.set_samplerate(samplerate)
        self.tts_analyzer.process(block)

    # ============================================================ levels
    def levels(self) -> dict[str, float | bool]:
        speaking = self.speaking
        if self._listening:
            lv = self.mic_analyzer.levels()
            self.tts_analyzer.decay()
        elif speaking:
            lv = self.tts_analyzer.levels()
            self.mic_analyzer.decay()
        else:
            self.mic_analyzer.decay()
            self.tts_analyzer.decay()
            lv = AudioLevels()
        out: dict[str, float | bool] = dict(lv.as_dict())
        out["listening"] = self._listening
        out["speaking"] = speaking
        return out

    def shutdown(self) -> None:
        self.cancel_listening()
        if isinstance(self.tts, LocalTTS):
            self.tts.shutdown()
        self._stt_pool.shutdown(wait=False, cancel_futures=True)
