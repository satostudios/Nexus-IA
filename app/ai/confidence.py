"""Confidence gating: may a call Needle proposed run now?

Needle decides *which* action the user wants; this policy decides whether it is
allowed to execute. It never lets the model bypass confirmation: tools marked
CONFIRMATION_REQUIRED always ask, whatever the confidence.

Thresholds are configurable (Settings -> AI). The defaults come from measuring
Needle 3 on NEXUS's own command set (see README, "Confidence calibration"):
correct, trigger-routed calls scored 0.42-1.0 in English and 0.13-1.0 in
Portuguese (most >= 0.33), so safe actions run at >= 0.30, anything between
0.10 (the engine's own withholding floor) and 0.30 is shown to the user first,
and below 0.10 nothing runs. The command-surface gate, not confidence, is what
keeps off-topic requests out (they scored 0.70-1.0).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.tools.registry import Safety


class Route(Enum):
    EXECUTE = "execute"
    CONFIRM = "confirm"
    REFUSE = "refuse"
    BLOCK = "block"


@dataclass(frozen=True)
class GateDecision:
    route: Route
    reason: str


@dataclass(frozen=True)
class ConfidencePolicy:
    execute_threshold: float = 0.30
    refuse_threshold: float = 0.10

    def decide(self, safety: Safety, confidence: float | None, suppressed: bool = False,
               escalation: Safety | None = None) -> GateDecision:
        effective = escalation or safety
        if effective is Safety.BLOCKED:
            return GateDecision(Route.BLOCK, "blocked tool or protected target")
        if confidence is None:
            # Weights without a calibration head (e.g. a local LoRA build).
            return GateDecision(Route.CONFIRM, "confidence unavailable")
        if suppressed:
            if confidence < self.refuse_threshold:
                return GateDecision(Route.REFUSE, f"withheld by engine, confidence {confidence:.2f}")
            return GateDecision(Route.CONFIRM, "withheld by engine grounding gate")
        if confidence < self.refuse_threshold:
            return GateDecision(Route.REFUSE, f"low confidence {confidence:.2f}")
        if effective is Safety.CONFIRMATION_REQUIRED:
            return GateDecision(Route.CONFIRM, "tool requires confirmation")
        if confidence >= self.execute_threshold:
            return GateDecision(Route.EXECUTE, f"confidence {confidence:.2f}")
        return GateDecision(Route.CONFIRM, f"medium confidence {confidence:.2f}")
