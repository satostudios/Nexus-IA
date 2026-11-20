"""Shared services and short-term session context for tools.

Tools are plain functions (so Needle can build schemas from their signatures);
they reach configuration, memory and indexes through this module instead of
through arguments the model would have to fill.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ai.memory import LocalMemory
    from app.ai.writer import LocalWriter
    from app.config.settings import Settings
    from app.core.jobs import BackgroundJobs
    from app.tools.app_resolver import AppResolver
    from app.tools.file_index import FileIndex

# Words that point at "the thing we just talked about".
_DEICTIC = re.compile(
    r"^(?:this|that|it|the|the same|the last|the latest|the previous|isso|isto|aquilo|o|a|esse|essa|este|esta"
    r"|aquele|aquela|o mesmo|a mesma|o [uú]ltimo|a [uú]ltima)?\s*"
    r"(?:one|file|folder|directory|item|document|thing|arquivo|pasta|diret[oó]rio|documento|item|coisa)?$",
    re.IGNORECASE)
_ORDINAL = re.compile(r"\b(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th|last|primeir[oa]|segund[oa]|"
                      r"terceir[oa]|quart[oa]|quint[oa]|[uú]ltim[oa])\b(?:\s+one)?", re.IGNORECASE)
_ORDINALS = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2,
             "fourth": 3, "4th": 3, "fifth": 4, "5th": 4, "last": -1,
             "primeiro": 0, "primeira": 0, "segundo": 1, "segunda": 1, "terceiro": 2, "terceira": 2,
             "quarto": 3, "quarta": 3, "quinto": 4, "quinta": 4, "último": -1, "última": -1,
             "ultimo": -1, "ultima": -1}


@dataclass
class SessionContext:
    """What NEXUS touched recently, used to resolve 'this folder', 'the first one'."""
    last_path: Path | None = None
    last_folder: Path | None = None
    last_results: list[Path] = field(default_factory=list)
    request: str = ""   # the user's words for the command being executed (argument repair)
    normalized: object = None          # app.ai.language.Normalized for the current request
    launched: tuple[object, float] | None = None   # (AppEntry, time) of the last app NEXUS opened
    typing_target: tuple[int, float] | None = None  # (hwnd, time) chosen by the last input preflight
    last_program: Path | None = None   # the last program write_code produced (run_program / edit_code)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def touch(self, path: Path) -> None:
        with self.lock:
            self.last_path = path
            if path.is_dir():
                self.last_folder = path
            elif path.parent.exists():
                self.last_folder = path.parent

    def set_results(self, results: list[Path]) -> None:
        with self.lock:
            self.last_results = list(results)

    def resolve_reference(self, text: str, want_dir: bool | None = None) -> Path | None:
        """Map deictic/ordinal phrases onto a recently used path."""
        phrase = text.strip().strip(".").strip()
        with self.lock:
            ordinal = _ORDINAL.search(phrase)
            if ordinal and self.last_results and len(phrase.split()) <= 4:
                index = _ORDINALS[ordinal.group(1).lower()]
                try:
                    return self.last_results[index]
                except IndexError:
                    return None
            if not phrase or _DEICTIC.match(phrase):
                if want_dir is True:
                    return self.last_folder or (self.last_path if self.last_path and self.last_path.is_dir() else None)
                if want_dir is False and self.last_path and self.last_path.is_file():
                    return self.last_path
                return self.last_path
        return None


class ToolServices:
    """Late-bound service locator, filled in by the application at startup."""

    settings: "Settings | None" = None
    memory: "LocalMemory | None" = None
    apps: "AppResolver | None" = None
    files: "FileIndex | None" = None
    writer: "LocalWriter | None" = None
    jobs: "BackgroundJobs | None" = None
    session: SessionContext = SessionContext()


services = ToolServices()


def require_jobs() -> "BackgroundJobs":
    if services.jobs is None:
        from app.core.jobs import BackgroundJobs
        services.jobs = BackgroundJobs()
    return services.jobs


def require_writer() -> "LocalWriter":
    if services.writer is None:
        from app.ai.writer import LocalWriter
        services.writer = LocalWriter(require_settings())
    return services.writer


def require_settings() -> "Settings":
    if services.settings is None:
        from app.config.settings import Settings
        services.settings = Settings()
    return services.settings


def require_memory() -> "LocalMemory":
    if services.memory is None:
        from app.ai.memory import LocalMemory
        services.memory = LocalMemory()
    return services.memory
