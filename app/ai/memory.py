"""Small local memory: saved places, app nicknames, preferences, recent commands.

Stored as JSON in ``%APPDATA%\\NEXUS\\memory.json``. Nothing leaves the machine.
Only what the user explicitly asks NEXUS to remember is stored, plus the last
few successful commands (tool name and arguments, no audio).

Lookups are lexical first. When a Needle embedder is attached, saved names are
also matched semantically (``my programming stuff`` finds ``programming
project``). Needle 3 embeddings are strongly anisotropic (every pair of short
phrases scores 0.90-0.99 raw cosine, measured), so vectors are centred on the
mean of a fixed background vocabulary before comparison; with centring, true
paraphrases scored 0.34-0.94 and unrelated names up to 0.32, hence the 0.33
floor plus a margin over the runner-up, and never for file names.
"""

from __future__ import annotations

import difflib
import json
import logging
import math
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from app import paths

log = logging.getLogger("nexus.memory")

_FILLER = re.compile(r"^(?:my|the|our|a|an)\s+|\s+(?:folder|directory|dir|location|place)$")
_RECENT_LIMIT = 50
_HAS_EXTENSION = re.compile(r"\.[a-z0-9]{1,5}$", re.IGNORECASE)
_BACKGROUND = ("chrome", "notepad", "downloads", "desktop", "report.pdf", "notes.txt", "music", "photos", "work",
               "school", "games", "video editing", "invoices", "budget", "family pictures", "project", "website",
               "documents", "backup", "screenshots", "vacation", "resume", "code", "design", "recipes", "movies",
               "homework", "finance", "clients", "archive")
SEMANTIC_FLOOR = 0.33
SEMANTIC_MARGIN = 0.07
SEMANTIC_ONLY = 0.6   # score reported for a match found only by embeddings


def normalise_name(text: str) -> str:
    text = re.sub(r"[^\w\s.-]", " ", text.lower()).strip()
    text = re.sub(r"\s+", " ", text)
    previous = None
    while previous != text:
        previous = text
        text = _FILLER.sub("", text).strip()
    return text


class LocalMemory:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (paths.config_dir() / "memory.json")
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {"locations": {}, "app_aliases": {}, "preferences": {},
                                      "recent_commands": []}
        self._embedder: Callable[[str], list[float] | None] | None = None
        self._vectors: dict[str, list[float]] = {}
        self._mean: list[float] | None = None
        self._load()

    # ---------------------------------------------------------------- io
    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("memory file unreadable (%s); starting empty", exc)
            return
        for key in self._data:
            if isinstance(raw.get(key), type(self._data[key])):
                self._data[key] = raw[key]

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)

    # ------------------------------------------------------- embeddings
    def attach_embedder(self, embed: Callable[[str], list[float] | None] | None) -> None:
        """Enable semantic lookup. ``embed`` may return None when it cannot run now."""
        with self._lock:
            self._embedder = embed
            self._vectors.clear()
            self._mean = None

    def prime(self) -> None:
        """Embed the background vocabulary and saved names (call on the embedder's thread)."""
        self._background_mean()
        for key in self.locations():
            self._vector(key)

    def _background_mean(self) -> list[float] | None:
        if self._mean is None and self._embedder is not None:
            vectors = [v for v in (self._vector(t) for t in _BACKGROUND) if v is not None]
            if len(vectors) == len(_BACKGROUND):
                dim = len(vectors[0])
                self._mean = [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]
        return self._mean

    def _vector(self, text: str) -> list[float] | None:
        if self._embedder is None:
            return None
        if text not in self._vectors:
            try:
                vector = self._embedder(text)
            except Exception:
                log.warning("embedding failed; semantic lookup disabled", exc_info=True)
                self._embedder = None
                return None
            if vector is None:
                return None
            self._vectors[text] = vector
        return self._vectors[text]

    def _centred_cosine(self, a: list[float], b: list[float]) -> float | None:
        mean = self._background_mean()
        if mean is None:
            return None
        ac = [x - m for x, m in zip(a, mean)]
        bc = [y - m for y, m in zip(b, mean)]
        dot = sum(x * y for x, y in zip(ac, bc))
        na = math.sqrt(sum(x * x for x in ac))
        nb = math.sqrt(sum(y * y for y in bc))
        return dot / (na * nb) if na and nb else 0.0

    # --------------------------------------------------------- locations
    def remember_location(self, name: str, path: str) -> str:
        key = normalise_name(name)
        with self._lock:
            self._data["locations"][key] = {"path": path, "saved": time.time()}
            self._save()
        log.info("remembered location %r -> %s", key, path)
        return key

    def forget_location(self, name: str) -> list[str]:
        with self._lock:
            matches = self.find_locations(name)
            if len(matches) != 1:
                return [m[0] for m in matches]
            key = matches[0][0]
            self._data["locations"].pop(key, None)
            self._vectors.pop(key, None)
            self._save()
            return [key]

    def locations(self) -> dict[str, str]:
        with self._lock:
            return {k: v["path"] for k, v in self._data["locations"].items()}

    def find_locations(self, query: str, semantic: bool = True) -> list[tuple[str, str, float]]:
        """Saved places matching ``query``: [(name, path, score)], best first.

        A single clear winner is returned alone; several comparable matches are
        all returned so the caller can ask which one. Lexical matches score
        >= 0.7; a semantic-only match scores exactly 0.6 so callers can tell it
        apart (see :data:`SEMANTIC_ONLY`).
        """
        q = normalise_name(query)
        if not q:
            return []
        with self._lock:
            items = self.locations()
            scored: list[tuple[str, str, float]] = []
            q_tokens = set(q.split())
            for key, path in items.items():
                if key == q:
                    return [(key, path, 1.0)]
                k_tokens = set(key.split())
                score = difflib.SequenceMatcher(None, q, key).ratio()
                score = score if score >= 0.8 else 0.0
                if q_tokens and k_tokens and (q_tokens <= k_tokens or k_tokens <= q_tokens):
                    score = max(score, 0.85)
                elif k_tokens and len(q_tokens & k_tokens) / len(k_tokens) >= 0.66:
                    score = max(score, 0.7)
                if score:
                    scored.append((key, path, score))
            if not scored and semantic and not _HAS_EXTENSION.search(q) and len(q) >= 3:
                scored = self._semantic(q, items)
        scored.sort(key=lambda s: s[2], reverse=True)
        if not scored:
            return []
        best = scored[0][2]
        close = [s for s in scored if best - s[2] < 0.05]
        return close if len(close) > 1 else [scored[0]]

    def _semantic(self, q: str, items: dict[str, str]) -> list[tuple[str, str, float]]:
        qv = self._vector(q)
        if qv is None or not items:
            return []
        ranked = []
        for key, path in items.items():
            kv = self._vector(key)
            if kv is None:
                return []
            sim = self._centred_cosine(qv, kv)
            if sim is None:
                return []
            ranked.append((sim, key, path))
        ranked.sort(reverse=True)
        top = ranked[0]
        runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
        if top[0] >= SEMANTIC_FLOOR and top[0] - runner_up >= SEMANTIC_MARGIN:
            log.info("semantic memory match %r -> %r (%.2f)", q, top[1], top[0])
            return [(top[1], top[2], SEMANTIC_ONLY)]
        return []

    # -------------------------------------------------------- app aliases
    def remember_alias(self, alias: str, application: str) -> None:
        with self._lock:
            self._data["app_aliases"][normalise_name(alias)] = application.strip()
            self._save()

    def alias_for(self, name: str) -> str | None:
        with self._lock:
            return self._data["app_aliases"].get(normalise_name(name))

    def aliases(self) -> dict[str, str]:
        with self._lock:
            return dict(self._data["app_aliases"])

    # -------------------------------------------------------- preferences
    def preference(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data["preferences"].get(key, default)

    def set_preference(self, key: str, value: Any) -> None:
        with self._lock:
            self._data["preferences"][key] = value
            self._save()

    # ---------------------------------------------------- recent commands
    def record_command(self, text: str, tool: str, arguments: dict[str, Any]) -> None:
        with self._lock:
            recent = self._data["recent_commands"]
            recent.append({"text": text[:200], "tool": tool, "arguments": arguments, "time": time.time()})
            del recent[:-_RECENT_LIMIT]
            self._save()

    def recent_commands(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._data["recent_commands"])
