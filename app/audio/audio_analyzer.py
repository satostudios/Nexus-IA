"""Real-time audio features for the NEXUS Core and for voice endpointing.

Every block of samples (microphone or TTS playback) is analysed with an FFT:

* ``volume``  RMS level, mapped from dBFS to 0..1
* ``bass``    60-250 Hz energy     (drives orbital motion)
* ``mid``     250-2000 Hz energy   (drives particles)
* ``treble``  2-8 kHz energy       (drives chromatic aberration)
* ``energy``  overall weighted energy (drives glow)
* ``speech``  voice activity 0..1  (drives surface distortion)

Values are smoothed with separate attack/release rates so the visual reacts
immediately to onsets but does not flicker. The analyzer is thread-safe: audio
callbacks write, the UI thread reads.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass

import numpy as np

_FFT_SIZE = 1024
_BANDS = {"bass": (60.0, 250.0), "mid": (250.0, 2000.0), "treble": (2000.0, 8000.0)}


@dataclass
class AudioLevels:
    volume: float = 0.0
    bass: float = 0.0
    mid: float = 0.0
    treble: float = 0.0
    energy: float = 0.0
    speech: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {"volume": self.volume, "bass": self.bass, "mid": self.mid, "treble": self.treble,
                "energy": self.energy, "speech": self.speech}


def _db(x: float) -> float:
    return 20.0 * math.log10(max(x, 1e-9))


def _norm(db: float, floor: float, ceil: float) -> float:
    return float(min(1.0, max(0.0, (db - floor) / (ceil - floor))))


class AudioAnalyzer:
    def __init__(self, samplerate: int, sensitivity: float = 1.0) -> None:
        self.samplerate = samplerate
        self.sensitivity = sensitivity
        self._lock = threading.Lock()
        self._ring = np.zeros(_FFT_SIZE, dtype=np.float32)
        self._window = np.hanning(_FFT_SIZE).astype(np.float32)
        freqs = np.fft.rfftfreq(_FFT_SIZE, 1.0 / samplerate)
        nyquist = samplerate / 2.0
        self._band_bins = {name: (freqs >= lo) & (freqs < min(hi, nyquist)) for name, (lo, hi) in _BANDS.items()}
        self._levels = AudioLevels()
        self._noise_db = -60.0
        self._calibration: list[float] = []
        self.last_is_speech = False
        self.last_rms_db = -120.0

    def reset(self) -> None:
        """Clear state; the noise floor is re-measured from the next ~160 ms."""
        with self._lock:
            self._ring[:] = 0
            self._levels = AudioLevels()
            self._calibration = []
            self._noise_db = -60.0
            self.last_is_speech = False

    def set_samplerate(self, samplerate: int) -> None:
        if samplerate != self.samplerate:
            self.__init__(samplerate, self.sensitivity)  # type: ignore[misc]

    def process(self, samples: np.ndarray) -> AudioLevels:
        """Analyse one block (mono float32 in -1..1). Returns the smoothed levels."""
        block = np.asarray(samples, dtype=np.float32).reshape(-1)
        if block.size == 0:
            return self.levels()
        gain = self.sensitivity
        n = min(block.size, _FFT_SIZE)
        with self._lock:
            self._ring = np.roll(self._ring, -n)
            self._ring[-n:] = block[-n:]
            frame = self._ring * self._window

        rms = float(np.sqrt(np.mean(np.square(block)))) * gain
        rms_db = _db(rms)
        spectrum = np.abs(np.fft.rfft(frame)) * gain / (_FFT_SIZE / 4)
        bands = {}
        for name, mask in self._band_bins.items():
            if mask.any():
                bands[name] = _norm(_db(float(np.sqrt(np.mean(np.square(spectrum[mask]))))), -75.0, -20.0)
            else:
                bands[name] = 0.0

        # Voice activity: level clearly above the tracked noise floor, with most
        # energy in the speech range (not a low hum or hiss).
        if len(self._calibration) < 8:
            self._calibration.append(rms_db)
            self._noise_db = min(self._calibration)
        elif rms_db < self._noise_db:
            self._noise_db = 0.7 * self._noise_db + 0.3 * rms_db
        else:
            self._noise_db = min(self._noise_db + 0.04, rms_db)
        self._noise_db = max(self._noise_db, -90.0)
        speech_band = bands["mid"] + 0.5 * bands["bass"]
        is_speech = rms_db > self._noise_db + 9.0 and rms_db > -52.0 and speech_band > 0.25
        self.last_is_speech = is_speech
        self.last_rms_db = rms_db

        target = AudioLevels(
            volume=_norm(rms_db, -58.0, -12.0),
            bass=bands["bass"], mid=bands["mid"], treble=bands["treble"],
            energy=min(1.0, 0.45 * bands["bass"] + 0.35 * bands["mid"] + 0.2 * bands["treble"]
                       + 0.3 * _norm(rms_db, -58.0, -12.0)),
            speech=1.0 if is_speech else 0.0,
        )
        with self._lock:
            cur = self._levels
            for field in ("volume", "bass", "mid", "treble", "energy", "speech"):
                old = getattr(cur, field)
                new = getattr(target, field)
                rate = 0.6 if new > old else (0.08 if field == "speech" else 0.18)
                setattr(cur, field, old + (new - old) * rate)
            return AudioLevels(**cur.as_dict())

    def levels(self) -> AudioLevels:
        with self._lock:
            return AudioLevels(**self._levels.as_dict())

    def decay(self, factor: float = 0.85) -> None:
        """Let levels fall toward zero when no audio is flowing."""
        with self._lock:
            for field in ("volume", "bass", "mid", "treble", "energy", "speech"):
                setattr(self._levels, field, getattr(self._levels, field) * factor)
