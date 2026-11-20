"""Turns NEXUS state + live audio levels into smooth shader parameters.

Pure Python (no Qt, no GL) so it can be unit-tested. Mapping (spec):

    volume  -> core scale          bass   -> orbital motion
    mid     -> particle movement   treble -> chromatic aberration
    energy  -> glow                speech -> surface distortion

Every parameter eases toward its target with a frame-rate independent
exponential, and rotation/flow are *integrated* speeds, so a state change never
makes the orb jump.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.core.state import NexusState


@dataclass(frozen=True)
class Profile:
    rot: float          # orb rotation speed (rad/s)
    ring: float         # ring precession speed
    flow: float         # internal noise evolution speed
    distort: float      # base surface distortion
    glow: float         # base glow
    particle: float     # base particle motion
    activity: float     # internal domain-warp strength
    ca: float           # base chromatic aberration
    glitch: float
    grain: float
    scale: float
    warn: float
    tint: tuple[float, float, float]
    audio: float        # how strongly live audio drives the core (0..1)


PROFILES: dict[NexusState, Profile] = {
    NexusState.BOOTING: Profile(0.25, 0.10, 0.6, 0.20, 0.25, 0.30, 0.55, 0.25, 0.10, 0.055, 0.92, 0.0,
                                (0.65, 0.72, 0.95), 0.0),
    NexusState.IDLE: Profile(0.07, 0.05, 0.10, 0.12, 0.28, 0.12, 0.10, 0.18, 0.03, 0.045, 1.0, 0.0,
                             (0.62, 0.70, 0.92), 0.0),
    NexusState.LISTENING: Profile(0.14, 0.12, 0.25, 0.20, 0.45, 0.35, 0.20, 0.25, 0.04, 0.05, 1.04, 0.0,
                                  (0.55, 0.86, 1.00), 1.0),
    NexusState.PROCESSING: Profile(0.45, 0.30, 1.10, 0.30, 0.55, 0.55, 0.90, 0.38, 0.12, 0.055, 1.0, 0.0,
                                   (0.76, 0.66, 1.00), 0.0),
    NexusState.SPEAKING: Profile(0.18, 0.16, 0.30, 0.16, 0.42, 0.35, 0.25, 0.25, 0.04, 0.05, 1.0, 0.0,
                                 (0.70, 0.80, 1.00), 1.0),
    NexusState.SUCCESS: Profile(0.12, 0.08, 0.20, 0.14, 0.40, 0.20, 0.15, 0.22, 0.03, 0.045, 1.02, 0.0,
                                (0.75, 0.90, 1.00), 0.0),
    NexusState.ERROR: Profile(0.10, 0.06, 0.35, 0.34, 0.30, 0.25, 0.35, 0.40, 0.55, 0.06, 0.98, 1.0,
                              (1.00, 0.80, 0.62), 0.0),
    NexusState.CONFIRMATION: Profile(0.04, 0.03, 0.06, 0.08, 0.34, 0.10, 0.05, 0.16, 0.02, 0.04, 0.98, 0.0,
                                     (1.00, 0.88, 0.72), 0.0),
}


def _ease(current: float, target: float, dt: float, rate: float) -> float:
    return current + (target - current) * (1.0 - math.exp(-dt * rate))


@dataclass
class CoreDynamics:
    state: NexusState = NexusState.BOOTING
    time: float = 0.0
    rot: float = 0.0
    ring: float = 0.0
    flow: float = 0.0
    pulse: float = 0.0
    boot: float = 0.0
    values: dict[str, float] = field(default_factory=lambda: {
        "scale": 0.92, "distort": 0.2, "glow": 0.25, "particle": 0.3, "bass": 0.0, "activity": 0.5,
        "ca": 0.25, "glitch": 0.1, "grain": 0.05, "warn": 0.0, "rot_speed": 0.25, "ring_speed": 0.1,
        "flow_speed": 0.6, "audio": 0.0})
    tint: list[float] = field(default_factory=lambda: [0.65, 0.72, 0.95])

    def set_state(self, state: NexusState) -> None:
        if state is NexusState.SUCCESS and self.state is not NexusState.SUCCESS:
            self.trigger_pulse()
        self.state = state

    def trigger_pulse(self, strength: float = 1.0) -> None:
        self.pulse = max(self.pulse, strength)

    def update(self, dt: float, levels: dict[str, float | bool]) -> dict[str, object]:
        """Advance by ``dt`` seconds with the current audio levels; return uniforms."""
        dt = max(0.0, min(dt, 0.1))
        self.time += dt
        prof = PROFILES[self.state]
        v = self.values
        vol = float(levels.get("volume", 0.0))
        bass = float(levels.get("bass", 0.0))
        mid = float(levels.get("mid", 0.0))
        treble = float(levels.get("treble", 0.0))
        energy = float(levels.get("energy", 0.0))
        speech = float(levels.get("speech", 0.0))

        v["audio"] = _ease(v["audio"], prof.audio, dt, 4.0)
        a = v["audio"]
        targets = {
            "scale": prof.scale + a * (0.16 * vol),
            "distort": prof.distort + a * (0.45 * speech + 0.2 * vol),
            "glow": prof.glow + a * 0.6 * energy,
            "particle": prof.particle + a * 0.9 * mid,
            "bass": a * bass,
            "activity": prof.activity,
            "ca": prof.ca + a * 0.9 * treble,
            "glitch": prof.glitch,
            "grain": prof.grain,
            "warn": prof.warn,
            "rot_speed": prof.rot + a * 0.25 * vol,
            "ring_speed": prof.ring * (1.0 + a * 3.0 * bass),
            "flow_speed": prof.flow + a * 0.6 * energy,
        }
        audio_keys = {"scale", "distort", "glow", "particle", "bass", "ca"}
        for key, target in targets.items():
            rate = 9.0 if key in audio_keys and a > 0.2 else 2.6
            v[key] = _ease(v[key], target, dt, rate)
        for i in range(3):
            self.tint[i] = _ease(self.tint[i], prof.tint[i], dt, 2.0)

        self.rot += v["rot_speed"] * dt
        self.ring += v["ring_speed"] * dt
        self.flow += v["flow_speed"] * dt
        self.pulse *= math.exp(-dt * 2.8)
        self.boot = _ease(self.boot, 0.55 if self.state is NexusState.BOOTING else 1.0, dt, 1.5)

        return {
            "uTime": self.time, "uRot": self.rot, "uRing": self.ring, "uFlow": self.flow,
            "uScale": v["scale"] + 0.06 * self.pulse, "uDistort": v["distort"], "uGlow": v["glow"],
            "uParticle": v["particle"], "uBass": v["bass"], "uActivity": v["activity"],
            "uPulse": self.pulse, "uWarn": v["warn"], "uBoot": self.boot, "uTint": tuple(self.tint),
            # post pass
            "uCA": v["ca"] + 0.3 * self.pulse, "uBloom": 0.32 + 0.38 * v["glow"] + 0.4 * self.pulse,
            "uGlitch": v["glitch"], "uGrain": v["grain"], "uScan": 0.035,
        }
