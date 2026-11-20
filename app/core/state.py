"""The single source of truth for what NEXUS is doing right now."""

from __future__ import annotations

import logging
from enum import Enum

from PySide6.QtCore import QObject, QTimer, Signal

log = logging.getLogger("nexus.state")


class NexusState(Enum):
    BOOTING = "booting"
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    SUCCESS = "success"
    ERROR = "error"
    CONFIRMATION = "confirmation"

    @property
    def label(self) -> str:
        return {
            NexusState.BOOTING: "STARTING",
            NexusState.IDLE: "READY",
            NexusState.LISTENING: "LISTENING",
            NexusState.PROCESSING: "PROCESSING",
            NexusState.SPEAKING: "SPEAKING",
            NexusState.SUCCESS: "DONE",
            NexusState.ERROR: "ERROR",
            NexusState.CONFIRMATION: "CONFIRM",
        }[self]


# Transitions that make sense. Anything else is logged and still applied, so a
# missed edge case can never wedge the UI, but it shows up in the log.
_ALLOWED: dict[NexusState, set[NexusState]] = {
    NexusState.BOOTING: {NexusState.IDLE, NexusState.ERROR},
    NexusState.IDLE: {NexusState.LISTENING, NexusState.PROCESSING, NexusState.SPEAKING, NexusState.ERROR},
    NexusState.LISTENING: {NexusState.PROCESSING, NexusState.IDLE, NexusState.ERROR},
    NexusState.PROCESSING: {NexusState.SUCCESS, NexusState.ERROR, NexusState.CONFIRMATION,
                            NexusState.SPEAKING, NexusState.IDLE},
    NexusState.SPEAKING: {NexusState.IDLE, NexusState.LISTENING, NexusState.PROCESSING, NexusState.CONFIRMATION},
    NexusState.SUCCESS: {NexusState.SPEAKING, NexusState.IDLE, NexusState.LISTENING, NexusState.PROCESSING},
    NexusState.ERROR: {NexusState.IDLE, NexusState.SPEAKING, NexusState.LISTENING, NexusState.PROCESSING},
    NexusState.CONFIRMATION: {NexusState.PROCESSING, NexusState.IDLE, NexusState.LISTENING,
                              NexusState.SPEAKING, NexusState.ERROR},
}

# States that fall back to IDLE by themselves after a short moment.
_TRANSIENT_MS = {NexusState.SUCCESS: 1100, NexusState.ERROR: 2200}


class StateModel(QObject):
    """Holds the current :class:`NexusState` and notifies the UI of changes."""

    changed = Signal(object, object)  # (new_state, previous_state)

    def __init__(self) -> None:
        super().__init__()
        self._state = NexusState.BOOTING
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._settle)

    @property
    def state(self) -> NexusState:
        return self._state

    def set(self, new: NexusState) -> None:
        old = self._state
        if new is old:
            return
        if new not in _ALLOWED.get(old, set()):
            log.debug("Unusual state transition %s -> %s", old.name, new.name)
        self._state = new
        self._timer.stop()
        if new in _TRANSIENT_MS:
            self._timer.start(_TRANSIENT_MS[new])
        log.debug("state %s -> %s", old.name, new.name)
        self.changed.emit(new, old)

    def _settle(self) -> None:
        if self._state in _TRANSIENT_MS:
            self.set(NexusState.IDLE)
