"""Core state machine and the audio -> visual parameter mapping."""

from app.core.state import NexusState, StateModel
from app.ui.core_dynamics import CoreDynamics


def test_state_transitions_and_transient_states(qapp):
    from PySide6.QtCore import QEventLoop, QTimer

    seen = []
    model = StateModel()
    model.changed.connect(lambda new, old: seen.append((old, new)))
    for s in (NexusState.IDLE, NexusState.LISTENING, NexusState.PROCESSING, NexusState.SUCCESS):
        model.set(s)
    assert model.state is NexusState.SUCCESS
    loop = QEventLoop()
    QTimer.singleShot(1500, loop.quit)
    loop.exec()
    assert model.state is NexusState.IDLE  # SUCCESS settles back to READY by itself
    assert seen[0] == (NexusState.BOOTING, NexusState.IDLE)
    assert NexusState.IDLE.label == "READY"


def _run(dyn: CoreDynamics, seconds: float, levels: dict) -> dict:
    u = {}
    for _ in range(int(seconds * 60)):
        u = dyn.update(1 / 60, levels)
    return u


def test_listening_follows_the_microphone():
    dyn = CoreDynamics()
    dyn.set_state(NexusState.LISTENING)
    quiet = _run(dyn, 1.5, {})
    loud = _run(dyn, 0.5, {"volume": 0.9, "bass": 0.8, "mid": 0.8, "treble": 0.8, "energy": 0.9, "speech": 1.0})
    assert loud["uScale"] > quiet["uScale"] + 0.08       # volume -> scale
    assert loud["uDistort"] > quiet["uDistort"] + 0.3    # speech -> distortion
    assert loud["uCA"] > quiet["uCA"] + 0.3              # treble -> chromatic aberration
    assert loud["uParticle"] > quiet["uParticle"] + 0.4  # mid -> particles
    assert loud["uGlow"] > quiet["uGlow"] + 0.3          # energy -> glow


def test_processing_ignores_audio_and_moves_internally():
    dyn = CoreDynamics()
    dyn.set_state(NexusState.PROCESSING)
    u = _run(dyn, 3.0, {"volume": 1.0, "speech": 1.0})
    assert u["uScale"] < 1.02 and u["uActivity"] > 0.8


def test_success_pulse_decays_and_values_are_smooth():
    dyn = CoreDynamics()
    dyn.set_state(NexusState.IDLE)
    _run(dyn, 1.0, {})
    dyn.set_state(NexusState.SUCCESS)
    first = dyn.update(1 / 60, {})
    assert first["uPulse"] > 0.9
    later = _run(dyn, 2.0, {})
    assert later["uPulse"] < 0.01
    prev = dyn.update(1 / 60, {})
    dyn.set_state(NexusState.ERROR)
    nxt = dyn.update(1 / 60, {})
    assert abs(nxt["uWarn"] - prev["uWarn"]) < 0.1   # no jump on state change
    assert nxt["uRot"] >= prev["uRot"]                # rotation is integrated, never resets
