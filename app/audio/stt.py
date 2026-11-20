"""Speech-to-text abstraction.

Needle 3 is a text-only model (its published config lists
``"modalities": ["text"]`` and the Python API has no audio parameters), so
NEXUS transcribes locally and hands the text to Needle. The rest of the
application only knows :class:`SpeechRecognizer`; the Whisper implementation
can be swapped for any other local engine.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

log = logging.getLogger("nexus.stt")

SAMPLE_RATE = 16000

# Phrases Whisper is known to produce from silence or noise.
_HALLUCINATIONS = {
    "", "you", "thank you", "thank you.", "thanks for watching", "thanks for watching!", "bye", "bye.",
    "thank you for watching", "subtitles by the amara.org community", ".", "...", "okay.", "so",
    "i'm sorry", "hmm", "uh", "um",
    # Portuguese hallucinations seen on silence / noise
    "obrigado.", "obrigado", "obrigada.", "obrigada", "tchau.", "tchau, tchau.", "e aí.", "valeu.",
    "legendas pela comunidade amara.org", "inscreva-se no canal", "até a próxima.", "até mais.",
}


class SpeechRecognizer(ABC):
    """Turns 16 kHz mono float32 audio into text. Implementations run locally."""

    name = "speech recognizer"

    @abstractmethod
    def load(self) -> None:
        """Load the model (may be slow; call off the UI thread)."""

    @property
    @abstractmethod
    def ready(self) -> bool: ...

    @abstractmethod
    def transcribe(self, audio_data: np.ndarray) -> str:
        """Return the spoken text, or ``""`` when nothing intelligible was said."""


class WhisperRecognizer(SpeechRecognizer):
    """faster-whisper (CTranslate2) on the CPU, int8."""

    name = "faster-whisper"

    def __init__(self, model_dir: Path, language: str | None = "en", initial_prompt: str | None = None,
                 beam_size: int = 5, cpu_threads: int | None = None) -> None:
        self.model_dir = model_dir
        self.language = language or None
        self.initial_prompt = initial_prompt
        self.beam_size = beam_size
        # Measured on a 12-thread i5-1235U: 8 threads ~1.15 s per command, 4 threads ~1.4 s.
        self.cpu_threads = cpu_threads or max(2, min(8, (os.cpu_count() or 4) - 2))
        self._model = None
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        from faster_whisper import WhisperModel

        with self._lock:
            if self._model is None:
                model = WhisperModel(str(self.model_dir), device="cpu", compute_type="int8",
                                     cpu_threads=self.cpu_threads)
                # Warm-up so the first real command is not slower than the rest.
                list(model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language=self.language,
                                      beam_size=1, without_timestamps=True)[0])
                self._model = model
                log.info("Whisper loaded from %s (%d threads)", self.model_dir, self.cpu_threads)

    def transcribe(self, audio_data: np.ndarray) -> str:
        if self._model is None:
            self.load()
        audio = np.asarray(audio_data, dtype=np.float32).reshape(-1)
        if audio.size < int(0.3 * SAMPLE_RATE):
            return ""
        peak = float(np.max(np.abs(audio))) or 1.0
        if peak < 0.5:
            audio = audio * (0.5 / peak)  # quiet microphones: normalise, Whisper prefers full-scale speech
        with self._lock:
            segments, info = self._model.transcribe(  # type: ignore[union-attr]
                audio, language=self.language, beam_size=self.beam_size, vad_filter=False,
                condition_on_previous_text=False, without_timestamps=True,
                initial_prompt=self.initial_prompt, temperature=0.0)
            parts = []
            for seg in segments:
                # Measured on real microphone audio: intelligible commands scored
                # avg_logprob -0.17..-0.63, garbled/too-quiet audio -1.18. A guess
                # from bad audio must never become a command.
                if seg.avg_logprob < -1.0 or (seg.no_speech_prob > 0.6 and seg.avg_logprob < -0.7):
                    log.info("dropped low-confidence segment %r (logprob %.2f, no_speech %.2f)",
                             seg.text, seg.avg_logprob, seg.no_speech_prob)
                    continue
                parts.append(seg.text.strip())
        text = re.sub(r"\s+", " ", " ".join(parts)).strip()
        if text.lower().strip() in _HALLUCINATIONS:
            log.info("discarded likely hallucination %r", text)
            return ""
        return text


def create_recognizer(model_dir: Path, language: str) -> SpeechRecognizer:
    # No initial_prompt on purpose. A prompt listing example commands was tested
    # and rejected: on unclear microphone audio Whisper completed it into a
    # plausible *wrong command* ("Open Notepad." heard as "Open Chrome." from the
    # prompt), while without a prompt unclear audio becomes harmless text that
    # NEXUS refuses. Clear audio transcribed identically either way.
    return WhisperRecognizer(model_dir, language=language or None, initial_prompt=None)
