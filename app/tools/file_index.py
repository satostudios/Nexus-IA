"""Bounded, cached file-name index for the file tools.

Only the user's own folders (Desktop, Documents, Downloads, Pictures, Music,
Videos, OneDrive, saved places and configured extra roots) are indexed, to a
limited depth, skipping dependency/cache folders. The index is built in a
background thread and refreshed when it goes stale; a search never walks the
whole drive.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

log = logging.getLogger("nexus.files")

SKIP_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv", "env", ".env", ".tox",
    ".cache", "appdata", "$recycle.bin", "system volume information", ".idea", ".vscode",
    "site-packages", "dist-packages", ".gradle", ".m2", ".npm", ".nuget", ".cargo", ".rustup",
    ".android", "temp", "tmp", ".pytest_cache", ".mypy_cache", "bower_components",
}
KNOWN_EXTENSIONS = {
    "pdf", "doc", "docx", "txt", "md", "rtf", "odt", "xls", "xlsx", "csv", "ppt", "pptx", "jpg", "jpeg",
    "png", "gif", "bmp", "webp", "svg", "psd", "ai", "mp3", "wav", "flac", "m4a", "ogg", "mp4", "mkv",
    "mov", "avi", "webm", "zip", "rar", "7z", "exe", "msi", "py", "js", "ts", "html", "css", "json",
    "xml", "yaml", "yml", "java", "c", "cpp", "cs", "go", "rs", "php", "sql", "prproj", "aep", "blend",
}
_WORDS = re.compile(r"[a-z0-9]+")


@dataclass
class FileMatch:
    path: Path
    is_dir: bool
    score: float

    @property
    def mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0


def _tokens(text: str) -> set[str]:
    return set(_WORDS.findall(text.lower()))


def score_name(name: str, query: str) -> float:
    """How well a file/folder name matches the spoken query (0..1)."""
    n = name.lower()
    q = query.lower().strip().strip('"\'')
    if not q:
        return 0.0
    stem = n.rsplit(".", 1)[0] if "." in n else n
    q_stem = q.rsplit(".", 1)[0] if "." in q and len(q.rsplit(".", 1)[1]) <= 5 else q
    if n == q:
        return 1.0
    if stem == q_stem or stem == q:
        return 0.95
    if n.startswith(q) or stem.startswith(q_stem):
        return 0.85
    if q_stem in n:
        return 0.75
    qt = _tokens(q_stem)
    if qt and qt <= _tokens(n):
        return 0.68
    ratio = difflib.SequenceMatcher(None, q_stem, stem).ratio()
    return ratio * 0.8 if ratio >= 0.8 else 0.0


class FileIndex:
    def __init__(self, roots: Callable[[], Iterable[Path]], max_depth: int = 7,
                 max_entries: int = 250_000, ttl_seconds: float = 600.0) -> None:
        self._roots = roots
        self.max_depth = max_depth
        self.max_entries = max_entries
        self.ttl = ttl_seconds
        self._entries: list[tuple[str, str, bool]] = []   # (lower name, path, is_dir)
        self._built_at = 0.0
        self._lock = threading.Lock()
        self._building = threading.Event()

    # ------------------------------------------------------------ build
    def roots(self) -> list[Path]:
        unique: list[Path] = []
        for root in self._roots():
            try:
                root = root.resolve()
            except OSError:
                continue
            if not root.is_dir():
                continue
            if any(root == u or root.is_relative_to(u) for u in unique):
                continue
            unique = [u for u in unique if not u.is_relative_to(root)] + [root]
        return unique

    def build(self) -> int:
        started = time.perf_counter()
        entries: list[tuple[str, str, bool]] = []
        for root in self.roots():
            self._walk(root, entries)
            if len(entries) >= self.max_entries:
                log.warning("file index capped at %d entries", self.max_entries)
                break
        with self._lock:
            self._entries = entries
            self._built_at = time.time()
        log.info("file index: %d entries in %.1fs", len(entries), time.perf_counter() - started)
        return len(entries)

    def _walk(self, root: Path, out: list[tuple[str, str, bool]]) -> None:
        stack: list[tuple[str, int]] = [(str(root), 0)]
        while stack and len(out) < self.max_entries:
            folder, depth = stack.pop()
            try:
                with os.scandir(folder) as it:
                    for entry in it:
                        name = entry.name
                        if name.startswith((".", "$", "~$")):
                            continue
                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                        except OSError:
                            continue
                        out.append((name.lower(), entry.path, is_dir))
                        if is_dir and depth < self.max_depth and name.lower() not in SKIP_DIRS:
                            stack.append((entry.path, depth + 1))
            except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
                continue

    def build_async(self) -> None:
        if self._building.is_set():
            return
        self._building.set()

        def run() -> None:
            try:
                self.build()
            except Exception:
                log.exception("file index build failed")
            finally:
                self._building.clear()

        threading.Thread(target=run, name="file-index", daemon=True).start()

    def ensure_fresh(self) -> None:
        if time.time() - self._built_at > self.ttl:
            self.build_async()

    @property
    def ready(self) -> bool:
        return self._built_at > 0

    @property
    def size(self) -> int:
        return len(self._entries)

    # ----------------------------------------------------------- search
    def search(self, query: str, extension: str | None = None, folder: Path | None = None,
               want_dir: bool | None = None, limit: int = 20, min_score: float = 0.6) -> list[FileMatch]:
        """Search the index (or ``folder`` live, which is always fresh)."""
        if folder is not None:
            return self.live_search(folder, query, extension, want_dir, limit=limit, min_score=min_score)
        if not self.ready:
            # First launch and the background build has not finished: search the
            # roots directly with a smaller depth rather than failing.
            results: list[FileMatch] = []
            for root in self.roots():
                results.extend(self.live_search(root, query, extension, want_dir, depth=4, limit=limit,
                                                min_score=min_score, time_budget=1.5))
            return _rank(results, limit)
        self.ensure_fresh()
        with self._lock:
            entries = self._entries
        ext = _clean_ext(extension)
        results = []
        for name, path, is_dir in entries:
            if want_dir is not None and is_dir != want_dir:
                continue
            if ext and (is_dir or not name.endswith("." + ext)):
                continue
            s = score_name(name, query) if query else 0.7
            if s >= min_score:
                results.append(FileMatch(Path(path), is_dir, s))
        return _rank(results, limit)

    def live_search(self, root: Path, query: str, extension: str | None = None, want_dir: bool | None = None,
                    depth: int = 5, limit: int = 20, min_score: float = 0.6,
                    time_budget: float = 3.0) -> list[FileMatch]:
        ext = _clean_ext(extension)
        deadline = time.perf_counter() + time_budget
        results: list[FileMatch] = []
        stack: list[tuple[str, int]] = [(str(root), 0)]
        while stack and time.perf_counter() < deadline:
            folder, d = stack.pop()
            try:
                with os.scandir(folder) as it:
                    for entry in it:
                        name = entry.name
                        if name.startswith((".", "$", "~$")):
                            continue
                        try:
                            is_dir = entry.is_dir(follow_symlinks=False)
                        except OSError:
                            continue
                        if is_dir and d < depth and name.lower() not in SKIP_DIRS:
                            stack.append((entry.path, d + 1))
                        if want_dir is not None and is_dir != want_dir:
                            continue
                        if ext and (is_dir or not name.lower().endswith("." + ext)):
                            continue
                        s = score_name(name, query) if query else 0.7
                        if s >= min_score:
                            results.append(FileMatch(Path(entry.path), is_dir, s))
            except OSError:
                continue
        return _rank(results, limit)


def _clean_ext(extension: str | None) -> str | None:
    if not extension:
        return None
    ext = extension.lower().strip().lstrip("*").lstrip(".")
    ext = re.sub(r"\s*files?$", "", ext).strip()
    return ext or None


def _rank(results: list[FileMatch], limit: int) -> list[FileMatch]:
    # Score first (cheap), then break ties by recency on a short list only,
    # because mtime costs a stat() per file.
    results.sort(key=lambda m: m.score, reverse=True)
    results = results[:max(limit * 10, 100)]
    results.sort(key=lambda m: (m.score, m.mtime), reverse=True)
    seen: set[str] = set()
    unique = []
    for m in results:
        key = str(m.path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(m)
    return unique[:limit]
