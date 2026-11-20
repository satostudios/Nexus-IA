"""Local text-to-speech.

The application talks to :class:`TextToSpeech` only. Speech is synthesised by a
:class:`SpeechSynthesizer` engine (Piper neural voices by default, the built-in
Windows SAPI voices as an offline fallback) and played through our own output
stream so every block that reaches the speaker is also fed to the analyzer:
the NEXUS Core moves with the voice, in sync.

Nothing leaves the machine. Speech is asynchronous and interruptible:
``stop()`` silences playback within one audio block.
"""

from __future__ import annotations

import collections
import logging
import queue
import re
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

log = logging.getLogger("nexus.tts")


class TTSError(RuntimeError):
    pass


# ================================================================ engines
class SpeechSynthesizer(ABC):
    name = "synthesizer"

    @abstractmethod
    def load(self) -> None: ...

    @abstractmethod
    def voices(self) -> list[str]: ...

    @abstractmethod
    def synthesize(self, text: str, speed: float) -> Iterator[tuple[np.ndarray, int]]:
        """Yield (mono float32 samples, samplerate) chunks, one per sentence or so."""


class PiperSynthesizer(SpeechSynthesizer):
    name = "piper"

    def __init__(self, voice_dir: Path, voice: str) -> None:
        self.voice_dir = voice_dir
        self.voice_name = voice
        self._voice = None

    def voices(self) -> list[str]:
        return sorted(p.name[:-5] for p in self.voice_dir.glob("*.onnx") if (p.parent / (p.name + ".json")).exists())

    def load(self) -> None:
        from piper import PiperVoice

        model = self.voice_dir / f"{self.voice_name}.onnx"
        if not model.is_file():
            available = self.voices()
            if not available:
                raise TTSError(f"Piper voice {self.voice_name} is not installed")
            log.warning("voice %s missing; using %s", self.voice_name, available[0])
            self.voice_name = available[0]
            model = self.voice_dir / f"{self.voice_name}.onnx"
        self._voice = PiperVoice.load(str(model))
        log.info("Piper voice loaded: %s (%d Hz)", self.voice_name, self._voice.config.sample_rate)

    def synthesize(self, text: str, speed: float) -> Iterator[tuple[np.ndarray, int]]:
        from piper import SynthesisConfig

        if self._voice is None:
            self.load()
        config = SynthesisConfig(length_scale=1.0 / max(0.5, min(2.0, speed)), normalize_audio=True, volume=1.0)
        for chunk in self._voice.synthesize(text, syn_config=config):  # type: ignore[union-attr]
            yield chunk.audio_float_array.astype(np.float32), chunk.sample_rate


class SapiSynthesizer(SpeechSynthesizer):
    """Windows' built-in SAPI 5 voices, rendered to memory (no internet, no install)."""

    name = "sapi"
    _RATE = 22050
    _FORMAT_22K_16BIT_MONO = 22

    def __init__(self, voice: str = "") -> None:
        self.voice_name = voice

    def load(self) -> None:
        if not self.voices():
            raise TTSError("No Windows voices are installed")

    def voices(self) -> list[str]:
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            tokens = speaker.GetVoices()
            return [tokens.Item(i).GetDescription() for i in range(tokens.Count)]
        except Exception:
            log.warning("SAPI unavailable", exc_info=True)
            return []

    def synthesize(self, text: str, speed: float) -> Iterator[tuple[np.ndarray, int]]:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        if self.voice_name:
            tokens = speaker.GetVoices()
            for i in range(tokens.Count):
                if tokens.Item(i).GetDescription() == self.voice_name:
                    speaker.Voice = tokens.Item(i)
                    break
        stream = win32com.client.Dispatch("SAPI.SpMemoryStream")
        stream.Format.Type = self._FORMAT_22K_16BIT_MONO
        speaker.AudioOutputStream = stream
        speaker.Rate = int(max(-10, min(10, round((speed - 1.0) * 10))))
        speaker.Speak(text)
        data = bytes(stream.GetData())
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        yield samples, self._RATE


# ================================================================ player
class _Player:
    """One output stream per utterance; the callback feeds the analyzer."""

    def __init__(self, device: int | None, on_block: Callable[[np.ndarray], None]) -> None:
        self.device = device
        self.on_block = on_block
        self._buffers: collections.deque[np.ndarray] = collections.deque()
        self._lock = threading.Lock()
        self._done_feeding = threading.Event()
        self._finished = threading.Event()
        self._stop = threading.Event()
        self._stream = None
        self.samplerate = 0

    def open(self, samplerate: int) -> None:
        import sounddevice as sd

        self.samplerate = samplerate
        self._stream = sd.OutputStream(samplerate=samplerate, channels=1, dtype="float32", device=self.device,
                                       blocksize=int(samplerate * 0.02), callback=self._callback,
                                       finished_callback=self._finished.set)
        self._stream.start()

    def feed(self, samples: np.ndarray) -> None:
        with self._lock:
            self._buffers.append(samples)

    def end(self) -> None:
        self._done_feeding.set()

    def stop(self) -> None:
        self._stop.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._finished.wait(timeout)

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass

    def _callback(self, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        import sounddevice as sd

        if self._stop.is_set():
            outdata[:] = 0
            raise sd.CallbackStop
        out = np.zeros(frames, dtype=np.float32)
        filled = 0
        with self._lock:
            while filled < frames and self._buffers:
                head = self._buffers[0]
                take = min(frames - filled, head.size)
                out[filled:filled + take] = head[:take]
                filled += take
                if take == head.size:
                    self._buffers.popleft()
                else:
                    self._buffers[0] = head[take:]
            empty = not self._buffers
        outdata[:, 0] = out
        self.on_block(out[:filled] if filled else out)
        if empty and filled < frames and self._done_feeding.is_set():
            raise sd.CallbackStop


# ============================================================ facade
class TextToSpeech(ABC):
    """What the rest of NEXUS uses. ``speak`` never blocks."""

    @abstractmethod
    def speak(self, text: str) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @property
    @abstractmethod
    def speaking(self) -> bool: ...

    @abstractmethod
    def configure(self, speed: float | None = None, volume: float | None = None,
                  output_device: str | None = None) -> None: ...

    @abstractmethod
    def voices(self) -> list[str]: ...


class LocalTTS(TextToSpeech):
    def __init__(self, synthesizer: SpeechSynthesizer, on_block: Callable[[np.ndarray, int], None],
                 on_start: Callable[[], None], on_finish: Callable[[bool], None],
                 on_error: Callable[[str], None], speed: float = 1.0, volume: float = 0.9,
                 output_device: str = "") -> None:
        self.synth = synthesizer
        self._on_block = on_block
        self._on_start = on_start
        self._on_finish = on_finish
        self._on_error = on_error
        self.speed = speed
        self.volume = volume
        self.output_device = output_device
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._current: _Player | None = None
        self._cancel = threading.Event()
        self._speaking = threading.Event()
        self._working = threading.Event()
        self._thread = threading.Thread(target=self._run, name="tts", daemon=True)
        self._thread.start()

    # --------------------------------------------------------------- API
    @property
    def speaking(self) -> bool:
        return self._speaking.is_set()

    @property
    def busy(self) -> bool:
        """Speaking, synthesising, or holding queued text."""
        return self._speaking.is_set() or self._working.is_set() or not self._queue.empty()

    def speak(self, text: str) -> None:
        text = _speakable(text)
        if text:
            self._cancel.clear()
            self._queue.put(text)

    def stop(self) -> None:
        self._cancel.set()
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        player = self._current
        if player is not None:
            player.stop()

    def configure(self, speed: float | None = None, volume: float | None = None,
                  output_device: str | None = None) -> None:
        if speed is not None:
            self.speed = speed
        if volume is not None:
            self.volume = volume
        if output_device is not None:
            self.output_device = output_device

    def voices(self) -> list[str]:
        return self.synth.voices()

    def shutdown(self) -> None:
        self.stop()
        self._queue.put(None)

    # ------------------------------------------------------------ worker
    def _run(self) -> None:
        while True:
            text = self._queue.get()
            if text is None:
                return
            if self._cancel.is_set():
                continue
            self._working.set()
            try:
                interrupted = self._say(text)
            except Exception as exc:
                log.exception("speech failed")
                self._speaking.clear()
                self._working.clear()
                self._on_error(str(exc) or "Speech failed.")
                self._on_finish(True)
                continue
            self._working.clear()
            self._on_finish(interrupted)

    def _say(self, text: str) -> bool:
        from app.audio.microphone import find_device

        player: _Player | None = None
        device = find_device(self.output_device, "output") if self.output_device else None
        rate_holder: list[int] = []

        def on_block(block: np.ndarray) -> None:
            self._on_block(block, rate_holder[0] if rate_holder else 22050)

        try:
            for samples, rate in self.synth.synthesize(text, self.speed):
                if self._cancel.is_set():
                    break
                samples = np.clip(samples * self.volume, -1.0, 1.0).astype(np.float32)
                if player is None:
                    rate_holder.append(rate)
                    player = _Player(device, on_block)
                    self._current = player
                    player.open(rate)
                    self._speaking.set()
                    self._on_start()
                player.feed(samples)
                # small gap between sentences sounds more natural
                player.feed(np.zeros(int(rate * 0.12), dtype=np.float32))
            if player is not None:
                player.end()
                while not player.wait(0.05):
                    if self._cancel.is_set():
                        player.stop()
                        player.wait(0.5)
                        break
        finally:
            if player is not None:
                player.close()
            self._current = None
            self._speaking.clear()
        return self._cancel.is_set()


_MARKUP = re.compile(r"[`*_#>\[\]{}]")


def _speakable(text: str) -> str:
    """Only concise human text is spoken: no JSON, paths are shortened."""
    text = _MARKUP.sub("", text or "").strip()
    text = re.sub(r"[A-Za-z]:\\[^\s,;]+", lambda m: Path(m.group(0)).name, text)
    return text[:300]
