"""Local, structured logging with secret redaction.

Logs go to ``logs/nexus.log`` (rotating). Nothing is sent anywhere. A filter
masks anything that looks like a password, token or API key before a record is
written.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
import threading
from pathlib import Path

from app import paths

_REDACTIONS = [
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|authorization)\b(\s*[:=]\s*|\s+is\s+)(\S+)"),
     r"\1\2[REDACTED]"),
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"), "[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[REDACTED]"),
    (re.compile(r"\bneedle_ft_[A-Za-z0-9_-]{8,}\b"), "[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{12,}"), "Bearer [REDACTED]"),
]


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = None
        return True


_configured = False
_log_path: Path | None = None


def setup_logging(enabled: bool = True, level: str = "INFO") -> Path:
    """Configure root logging once; later calls just adjust the level."""
    global _configured, _log_path
    root = logging.getLogger()
    effective = getattr(logging, level.upper(), logging.INFO) if enabled else logging.WARNING
    if _configured:
        root.setLevel(effective)
        return _log_path  # type: ignore[return-value]

    log_dir = paths.logs_dir()
    _log_path = log_dir / "nexus.log"
    handler = logging.handlers.RotatingFileHandler(
        _log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)

    if sys.stderr is not None and not paths.is_frozen():
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
        console.addFilter(RedactingFilter())
        console.setLevel(logging.WARNING)
        root.addHandler(console)

    root.setLevel(effective)
    for noisy in ("httpx", "httpcore", "huggingface_hub", "urllib3", "faster_whisper", "piper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    def _excepthook(exc_type, exc, tb):
        logging.getLogger("nexus.crash").critical("Unhandled exception", exc_info=(exc_type, exc, tb))

    def _thread_excepthook(args):
        logging.getLogger("nexus.crash").critical(
            "Unhandled exception in thread %s", getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    _configured = True
    return _log_path


def log_path() -> Path | None:
    return _log_path
