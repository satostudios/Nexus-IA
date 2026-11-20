"""Plain data objects passed between the pipeline, the assistant and the UI."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OutcomeKind(Enum):
    DONE = "done"                  # tools ran (successfully or with a reported failure)
    CONFIRM = "confirm"            # waiting for the user to approve an action
    CLARIFY = "clarify"            # a tool needs the user to disambiguate
    UNSUPPORTED = "unsupported"    # no tool covers the request
    REFUSED = "refused"            # a call was found but not trusted enough to run
    CANCELLED = "cancelled"
    ERROR = "error"                # engine or internal failure


@dataclass
class ToolCallRecord:
    """Technical metadata about one call, for the debug panel and history."""
    name: str
    arguments: dict[str, Any]
    confidence: float | None
    route: str
    suppressed: bool = False
    result: dict[str, Any] | None = None
    duration_ms: float | None = None


@dataclass
class ConfirmationRequest:
    title: str                     # "Delete folder?"
    detail: str                    # the resolved target, e.g. a full path
    destructive: bool
    spoken: str                    # short sentence for TTS
    tool: str


@dataclass
class TurnOutcome:
    kind: OutcomeKind
    message: str                   # concise, human-readable; the only thing TTS reads
    success: bool = False
    records: list[ToolCallRecord] = field(default_factory=list)
    confirmation: ConfirmationRequest | None = None
    extra_lines: list[str] = field(default_factory=list)  # e.g. search results shown under the reply
    awaiting_followup: bool = False
    timings: dict[str, float] = field(default_factory=dict)
    needle_input: str = ""         # what Needle actually read (Portuguese is normalised)


@dataclass
class HistoryEntry:
    user: str
    reply: str
    source: str                    # "text" or "voice"
    success: bool
    tools: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
