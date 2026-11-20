"""RMS / band energies / voice activity on synthetic signals."""

import numpy as np

from app.audio.audio_analyzer import AudioAnalyzer

SR = 16000


def _tone(freq: float, seconds: float = 0.5, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _feed(an: AudioAnalyzer, x: np.ndarray):
    lv = None
    for i in range(0, x.size, 320):
        lv = an.process(x[i:i + 320])
    return lv


def test_silence_is_zero():
    an = AudioAnalyzer(SR)
    lv = _feed(an, np.zeros(SR // 2, dtype=np.float32))
    assert lv.volume == 0 and lv.bass < 0.01 and lv.speech < 0.01


def test_bands_separate():
    bass = _feed(AudioAnalyzer(SR), _tone(120))
    mid = _feed(AudioAnalyzer(SR), _tone(1000))
    treble = _feed(AudioAnalyzer(SR), _tone(5000))
    assert bass.bass > 0.5 and bass.bass > bass.treble + 0.3
    assert mid.mid > 0.5 and mid.mid > mid.bass + 0.3
    assert treble.treble > 0.5 and treble.treble > treble.bass + 0.3


def test_volume_tracks_level():
    quiet = _feed(AudioAnalyzer(SR), _tone(500, amp=0.01))
    loud = _feed(AudioAnalyzer(SR), _tone(500, amp=0.5))
    assert loud.volume > quiet.volume + 0.4


def test_voice_activity_after_quiet_calibration():
    an = AudioAnalyzer(SR)
    noise = (np.random.default_rng(1).standard_normal(SR // 2) * 0.001).astype(np.float32)
    _feed(an, noise)
    assert not an.last_is_speech
    # speech-like: several harmonics in the voice range
    t = np.arange(SR // 2) / SR
    voice = sum(0.1 * np.sin(2 * np.pi * f * t) for f in (180, 360, 720, 1100)).astype(np.float32)
    _feed(an, voice)
    assert an.last_is_speech
