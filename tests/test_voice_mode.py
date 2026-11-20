"""Conversation mode with the real microphone, Needle, Whisper and TTS:
listen -> (a transcript arrives) -> answer out loud -> listen again -> "parar" ends it."""

import time

import pytest

pytestmark = [pytest.mark.needle, pytest.mark.models, pytest.mark.hardware]


def _wait(app, condition, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_conversation_loop(qapp, sandbox, monkeypatch):
    from app.audio.audio_manager import AudioManager
    from app.config.settings import Settings
    from app.core.assistant import Assistant
    from app.core.state import NexusState, StateModel

    settings = Settings.load(sandbox / "config.json")
    settings.audio.input_device = Settings.load().audio.input_device   # the microphone this machine uses
    settings.general.language = "pt"
    settings.voice.volume = 0.0          # exercise playback without making noise
    settings.audio.chime = False
    audio = AudioManager(settings)
    state = StateModel()
    assistant = Assistant(settings, state, audio)
    replies: list[str] = []
    assistant.reply.connect(lambda m, s, e: replies.append(m))
    assistant.start()
    assert _wait(qapp, lambda: assistant.ready and assistant.voice_ready, 240), "startup"
    try:
        assistant.set_voice_mode(True)
        assert _wait(qapp, lambda: audio.listening, 5) and audio.continuous
        # Stand in for the recogniser: this is exactly what it emits after an utterance.
        audio.stop_listening(submit=False)
        audio.transcript_ready.emit("que horas são")
        assert _wait(qapp, lambda: any(r.startswith("Agora são") for r in replies), 20), replies
        # After answering (and speaking), NEXUS listens again by itself.
        assert _wait(qapp, lambda: audio.listening and state.state is NexusState.LISTENING, 20)
        audio.stop_listening(submit=False)
        audio.transcript_ready.emit("parar")
        assert _wait(qapp, lambda: not assistant.voice_mode, 5)
        assert _wait(qapp, lambda: not audio.listening, 5)
        assert "Até mais." in replies
    finally:
        assistant.shutdown()
