"""Real audio I/O: microphone capture, Piper/SAPI synthesis, TTS interruption, Whisper STT."""

import threading
import time

import numpy as np
import pytest

from app import paths
from app.ai.model_assets import ModelAssets


@pytest.fixture(scope="module")
def assets():
    return ModelAssets(paths.models_dir())


@pytest.fixture(scope="module")
def piper(assets):
    from app.audio.tts import PiperSynthesizer

    assets.piper_voice("en_US-lessac-medium")
    synth = PiperSynthesizer(assets.piper_dir(), "en_US-lessac-medium")
    synth.load()
    return synth


def _speech(piper, text: str) -> np.ndarray:
    chunks = list(piper.synthesize(text, 1.0))
    audio = np.concatenate([c[0] for c in chunks])
    rate = chunks[0][1]
    x = np.interp(np.arange(int(audio.size * 16000 / rate)) * rate / 16000, np.arange(audio.size), audio)
    return np.concatenate([np.zeros(4000), x, np.zeros(4000)]).astype(np.float32)


@pytest.mark.hardware
def test_microphone_capture():
    from app.audio.microphone import Microphone, list_input_devices
    from app.config.settings import Settings

    devices = list_input_devices()
    if not devices:
        pytest.skip("no microphone")
    blocks = []
    mic = Microphone(blocks.append, lambda m: None)
    mic.start(Settings.load().audio.input_device, 16000)
    time.sleep(0.5)
    mic.stop()
    audio = np.concatenate(blocks)
    assert 0.35 < audio.size / 16000 < 0.8          # real-time 16 kHz mono stream
    assert audio.dtype == np.float32


@pytest.mark.models
def test_piper_synthesis(piper):
    chunks = list(piper.synthesize("Chrome opened.", 1.0))
    assert chunks and chunks[0][1] == 22050 and sum(c[0].size for c in chunks) > 11025


@pytest.mark.models
def test_sapi_fallback_synthesis():
    from app.audio.tts import SapiSynthesizer

    synth = SapiSynthesizer()
    if not synth.voices():
        pytest.skip("no Windows voices")
    samples, rate = next(synth.synthesize("Done.", 1.0))
    assert rate == 22050 and samples.size > 2000


@pytest.mark.models
@pytest.mark.hardware
def test_tts_speaks_and_can_be_interrupted(piper):
    from app.audio.tts import LocalTTS

    started, finished, blocks = threading.Event(), [], []
    tts = LocalTTS(piper, lambda b, r: blocks.append(b), started.set, finished.append, lambda e: None, volume=0.0)
    tts.speak("This sentence is long enough that it will certainly be interrupted before the end of it.")
    assert started.wait(15)
    time.sleep(0.3)
    t = time.perf_counter()
    tts.stop()
    while not finished and time.perf_counter() - t < 3:
        time.sleep(0.01)
    assert finished == [True]                       # reported as interrupted
    assert time.perf_counter() - t < 1.0            # promptly
    assert blocks                                   # audio went through the analyzer tap
    tts.shutdown()


@pytest.mark.models
def test_whisper_transcribes_speech(assets, piper):
    from app.audio.stt import create_recognizer

    rec = create_recognizer(assets.whisper_model("base.en"), "en")
    rec.load()
    assert "open notepad" in rec.transcribe(_speech(piper, "Open Notepad.")).lower()
    assert "screenshot" in rec.transcribe(_speech(piper, "Take a screenshot.")).lower().replace(" ", "")
    assert rec.transcribe(np.zeros(16000, dtype=np.float32)) == ""   # silence is not a command
