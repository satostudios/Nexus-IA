"""Local command history (JSON lines in %APPDATA%\\NEXUS\\history.jsonl)."""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
from pathlib import Path

from app import paths
from app.core.events import HistoryEntry

log = logging.getLogger("nexus.history")
_KEEP = 200


class CommandHistory:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (paths.config_dir() / "history.jsonl")
        self._lock = threading.Lock()
        self._entries: list[HistoryEntry] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines()[-_KEEP:]:
                try:
                    raw = json.loads(line)
                    self._entries.append(HistoryEntry(**{k: raw[k] for k in raw
                                                         if k in HistoryEntry.__dataclass_fields__}))
                except (ValueError, TypeError):
                    continue
        except OSError:
            log.warning("history unreadable", exc_info=True)

    def add(self, entry: HistoryEntry) -> None:
        with self._lock:
            self._entries.append(entry)
            del self._entries[:-_KEEP]
            try:
                if self.path.exists() and self.path.stat().st_size > 512_000:
                    self.path.write_text("\n".join(json.dumps(dataclasses.asdict(e)) for e in self._entries) + "\n",
                                         encoding="utf-8")
                else:
                    with self.path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(dataclasses.asdict(entry)) + "\n")
            except OSError:
                log.warning("could not write history", exc_info=True)

    def entries(self) -> list[HistoryEntry]:
        with self._lock:
            return list(self._entries)

    def user_commands(self) -> list[str]:
        return [e.user for e in self.entries()]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
