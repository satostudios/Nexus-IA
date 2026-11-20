"""Microphone capture with sounddevice (PortAudio).

The stream is open only while NEXUS is listening. Audio is delivered as mono
float32 blocks at 16 kHz (what the recogniser wants); if the device cannot
capture at 16 kHz directly, it is opened at its native rate and resampled.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

import numpy as np

from app.i18n import tr

log = logging.getLogger("nexus.mic")

TARGET_RATE = 16000
BLOCK_SECONDS = 0.02


class MicrophoneError(RuntimeError):
    """``str()`` is a short user-facing message."""


@dataclass
class InputDevice:
    index: int
    name: str
    samplerate: float
    is_default: bool


def _sd():
    import sounddevice as sd
    return sd


def list_input_devices() -> list[InputDevice]:
    """Input devices of the default host API (MME on Windows, which resamples)."""
    try:
        sd = _sd()
        default_in = sd.default.device[0]
        default_api = sd.query_devices(default_in)["hostapi"] if default_in is not None and default_in >= 0 else 0
        out: list[InputDevice] = []
        seen: set[str] = set()
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] <= 0 or d["hostapi"] != default_api:
                continue
            name = str(d["name"]).strip()
            low = name.lower()
            if low in seen or "mapeador" in low or "sound mapper" in low:
                continue
            seen.add(low)
            out.append(InputDevice(i, name, float(d["default_samplerate"]), i == default_in))
        return out
    except Exception:
        log.exception("could not enumerate input devices")
        return []


def list_output_devices() -> list[str]:
    try:
        sd = _sd()
        default_out = sd.default.device[1]
        api = sd.query_devices(default_out)["hostapi"] if default_out is not None and default_out >= 0 else 0
        names = []
        for d in sd.query_devices():
            name = str(d["name"]).strip()
            if d["max_output_channels"] > 0 and d["hostapi"] == api and name not in names \
                    and "mapeador" not in name.lower() and "sound mapper" not in name.lower():
                names.append(name)
        return names
    except Exception:
        log.exception("could not enumerate output devices")
        return []


def find_device(name: str, kind: str = "input") -> int | None:
    if not name:
        return None
    sd = _sd()
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    for i, d in enumerate(sd.query_devices()):
        if d[key] > 0 and str(d["name"]).strip() == name:
            return i
    for i, d in enumerate(sd.query_devices()):
        if d[key] > 0 and str(d["name"]).strip().lower().startswith(name.lower()[:25]):
            return i
    return None


class Microphone:
    def __init__(self, on_audio: Callable[[np.ndarray], None], on_error: Callable[[str], None]) -> None:
        self._on_audio = on_audio
        self._on_error = on_error
        self._stream = None
        self._lock = threading.Lock()
        self._rate = TARGET_RATE
        self._carry = np.zeros(0, dtype=np.float32)
        self.device_name = ""

    @property
    def running(self) -> bool:
        return self._stream is not None

    def start(self, device_name: str = "", samplerate: int = TARGET_RATE) -> None:
        with self._lock:
            if self._stream is not None:
                return
            sd = _sd()
            index = find_device(device_name, "input")
            if device_name and index is None:
                log.warning("microphone %r not found; using the default input", device_name)
            try:
                info = sd.query_devices(index if index is not None else sd.default.device[0])
            except Exception as exc:
                raise MicrophoneError(tr("No microphone was found.")) from exc
            if info["max_input_channels"] <= 0:
                raise MicrophoneError(tr("No microphone was found."))
            self.device_name = str(info["name"]).strip()
            attempts = [samplerate, int(info["default_samplerate"])]
            last_exc: Exception | None = None
            for rate in dict.fromkeys(attempts):
                try:
                    stream = sd.InputStream(device=index, channels=1, samplerate=rate, dtype="float32",
                                            blocksize=int(rate * BLOCK_SECONDS), callback=self._callback,
                                            finished_callback=self._finished)
                    stream.start()
                except Exception as exc:
                    last_exc = exc
                    log.info("microphone at %d Hz failed: %s", rate, exc)
                    continue
                self._rate = rate
                self._carry = np.zeros(0, dtype=np.float32)
                self._stream = stream
                log.info("microphone started: %s @ %d Hz", self.device_name, rate)
                return
            raise MicrophoneError(tr("The microphone couldn't be opened. It may be in use or disabled.")) from last_exc

    def stop(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.debug("error closing microphone", exc_info=True)
            log.info("microphone stopped")

    # PortAudio thread -------------------------------------------------------
    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            log.debug("mic status: %s", status)
        block = indata[:, 0].astype(np.float32, copy=True)
        if self._rate != TARGET_RATE:
            block = self._resample(block)
        if block.size:
            try:
                self._on_audio(block)
            except Exception:
                log.exception("audio consumer failed")

    def _resample(self, block: np.ndarray) -> np.ndarray:
        data = np.concatenate([self._carry, block])
        ratio = TARGET_RATE / self._rate
        n_out = int(data.size * ratio)
        if n_out <= 1:
            self._carry = data
            return np.zeros(0, dtype=np.float32)
        positions = np.arange(n_out) / ratio
        out = np.interp(positions, np.arange(data.size), data).astype(np.float32)
        used = int(np.floor(positions[-1])) + 1
        self._carry = data[used:]
        return out

    def _finished(self) -> None:
        if self._stream is not None:
            # Finished while we still expected audio: the device went away.
            log.warning("microphone stream ended unexpectedly")
            self._stream = None
            self._on_error(tr("The microphone was disconnected."))
