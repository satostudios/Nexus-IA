"""Resolve what the user said into a concrete path, URL or application.

Needle extracts the words ("README.md", "my coding project", "this folder");
this module decides which real thing they refer to. The open-family tools
share it, so if Needle picks ``open_file`` for "Open Chrome" the target still
resolves to the Chrome application instead of failing.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from app import paths
from app.ai.memory import SEMANTIC_ONLY
from app.i18n import join_options, tr
from app.tools.context import require_memory, require_settings, services
from app.tools.file_index import FileMatch

_TLDS = {
    "com", "org", "net", "io", "dev", "app", "ai", "co", "br", "uk", "de", "edu", "gov", "tv", "me",
    "info", "gg", "so", "ly", "xyz", "site", "online", "tech", "us", "ca", "fr", "es", "it", "pt", "jp",
    "in", "au", "nl", "ru", "ch", "se", "no", "pl", "eu", "cloud", "page", "blog", "news", "store", "shop",
}
_URL = re.compile(r"^(?:(https?)://)?((?:[\w-]+\.)+([a-z]{2,24}))(?::\d+)?(/\S*)?$", re.IGNORECASE)
SITES = {
    "youtube": "https://www.youtube.com", "google": "https://www.google.com", "gmail": "https://mail.google.com",
    "github": "https://github.com", "netflix": "https://www.netflix.com", "twitter": "https://x.com",
    "x": "https://x.com", "facebook": "https://www.facebook.com", "instagram": "https://www.instagram.com",
    "reddit": "https://www.reddit.com", "wikipedia": "https://www.wikipedia.org", "amazon": "https://www.amazon.com",
    "linkedin": "https://www.linkedin.com", "chatgpt": "https://chatgpt.com", "claude": "https://claude.ai",
    "whatsapp web": "https://web.whatsapp.com", "google drive": "https://drive.google.com",
    "google maps": "https://maps.google.com", "maps": "https://maps.google.com", "twitch": "https://www.twitch.tv",
    "outlook web": "https://outlook.live.com", "stack overflow": "https://stackoverflow.com",
}
_IN_FOLDER = re.compile(r"^(?P<name>.+?)\s+(?:in|on|from|inside|at|na|no|em|dentro d[aeo]|da|do)\s+"
                        r"(?:the\s+|my\s+|a\s+|o\s+|minha\s+|meu\s+|pasta\s+)?(?P<folder>.+?)(?:\s+folder)?$",
                        re.IGNORECASE)
_STRIP = re.compile(r"^(?:the|my|a|an|o|os|as|um|uma|meu|minha|meus|minhas)\s+|\s+(?:file|folder|directory)$",
                    re.IGNORECASE)


# ------------------------------------------------------------------ URLs
def as_url(text: str, allow_bare_name: bool = False) -> str | None:
    """Return a browsable URL when ``text`` is (or names) a website."""
    t = text.strip().strip("<>\"'").rstrip(".")
    low = t.lower()
    if low.startswith(("http://", "https://")):
        return t
    if low in SITES:
        return SITES[low]
    m = _URL.match(t)
    if m and m.group(3).lower() in _TLDS:
        return "https://" + t
    if allow_bare_name and re.fullmatch(r"[a-z0-9-]{2,40}", low):
        return f"https://www.{low}.com"
    return None


# ----------------------------------------------------------------- paths
def known_folder(text: str) -> Path | None:
    t = text.strip().lower()
    for _ in range(3):
        t = _STRIP.sub("", t).strip()
    t = re.sub(r"^(?:pasta|folder)\s+(?:de\s+|das?\s+|dos?\s+)?|\s+folder$|\bfolder\b", "", t).strip()
    t = re.sub(r"^(?:de|da|do|das|dos)\s+", "", t).strip()
    mapping = {
        "desktop": "desktop", "documents": "documents", "my documents": "documents", "docs": "documents",
        "document": "documents", "downloads": "downloads", "download": "downloads",
        "pictures": "pictures", "images": "pictures", "photos": "pictures", "music": "music",
        "videos": "videos", "video": "videos", "movies": "videos",
        "área de trabalho": "desktop", "area de trabalho": "desktop", "documentos": "documents",
        "meus documentos": "documents", "transferências": "downloads", "transferencias": "downloads",
        "imagens": "pictures", "fotos": "pictures", "músicas": "music", "musicas": "music", "música": "music",
        "vídeos": "videos", "filmes": "videos",
    }
    if t in mapping:
        return paths.user_folder(mapping[t])
    if t in ("home", "user folder", "user", "profile", "home folder", "pasta pessoal", "usuário", "usuario"):
        return Path.home()
    if t in ("screenshots", "screenshot folder", "nexus screenshots", "my screenshots", "capturas de tela",
             "capturas", "prints"):
        return screenshots_folder()
    return None


def screenshots_folder() -> Path:
    pictures = paths.user_folder("pictures") or Path.home() / "Pictures"
    folder = pictures / "NEXUS" / "Screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def explicit_path(text: str) -> Path | None:
    t = os.path.expandvars(text.strip().strip("\"'"))
    if t.startswith("~"):
        t = os.path.expanduser(t)
    if re.match(r"^[a-zA-Z]:[\\/]", t) or t.startswith("\\\\"):
        return Path(t)
    if re.match(r"^[a-zA-Z]:$", t):
        return Path(t + "\\")
    return None


def describe(path: Path) -> str:
    """Short spoken description: ``notes.txt in Desktop``."""
    parent = path.parent.name or str(path.parent)
    return tr("{name} in {folder}", name=path.name, folder=parent) if parent else path.name


@dataclass
class PathResolution:
    path: Path | None = None
    candidates: list[Path] = field(default_factory=list)
    error: str | None = None          # FILE_NOT_FOUND | AMBIGUOUS
    via: str = ""


def protected(path: Path) -> bool:
    """Paths destructive tools must never touch."""
    try:
        p = path.resolve()
    except OSError:
        p = path
    if p.parent == p:  # drive root
        return True
    system_roots = [os.environ.get(v) for v in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)",
                                                "ProgramData", "ProgramW6432")]
    for root in filter(None, system_roots):
        r = Path(root)
        if p == r or p.is_relative_to(r):
            return True
    home = Path.home()
    if p == home or p == home.parent:
        return True
    appdata = home / "AppData"
    if p == appdata or p.is_relative_to(appdata):
        return True
    for name in ("desktop", "documents", "downloads", "pictures", "music", "videos"):
        folder = paths.user_folder(name)
        if folder is not None and p == folder:
            return True
    if p == paths.install_root() or p.is_relative_to(paths.install_root()):
        return True
    return False


def resolve_folder(text: str) -> PathResolution:
    return resolve_path(text, want_dir=True)


def resolve_path(text: str, want_dir: bool | None = None, strict: bool = False) -> PathResolution:
    """Resolve spoken text to one existing path, several candidates, or nothing.

    ``strict`` requires a close name match (used by destructive tools, so a
    vague word never selects a random file to delete).
    """
    raw = text.strip().strip("\"'").strip()
    if not raw:
        return PathResolution(error="FILE_NOT_FOUND")
    session = services.session

    ref = session.resolve_reference(raw, want_dir)
    if ref is not None:
        return PathResolution(path=ref, via="context") if ref.exists() else PathResolution(error="FILE_NOT_FOUND")

    exp = explicit_path(raw)
    if exp is not None:
        if exp.exists() and (want_dir is None or exp.is_dir() == want_dir):
            return PathResolution(path=exp, via="explicit")
        return PathResolution(error="FILE_NOT_FOUND")

    if want_dir is not False:
        kf = known_folder(raw)
        if kf is not None:
            return PathResolution(path=kf, via="known") if kf.exists() else PathResolution(error="FILE_NOT_FOUND")

    saved = require_memory().find_locations(raw)
    if len(saved) == 1:
        p = Path(saved[0][1])
        if saved[0][2] <= SEMANTIC_ONLY:
            # Found only by meaning, not by name: a file actually called that wins.
            named = _search(_STRIP.sub("", raw).strip() or raw, want_dir, strict=True)
            if named.path is not None:
                return named
        if p.exists() and (want_dir is None or p.is_dir() == want_dir or want_dir is True and p.is_file()):
            return PathResolution(path=p, via="memory")
    elif len(saved) > 1:
        existing = [Path(s[1]) for s in saved if Path(s[1]).exists()]
        if len(existing) > 1:
            return PathResolution(candidates=existing, error="AMBIGUOUS", via="memory")
        if existing:
            return PathResolution(path=existing[0], via="memory")

    # "report.pdf in downloads" / "notes on the desktop"
    m = _IN_FOLDER.match(raw)
    if m:
        folder_res = resolve_path(m.group("folder"), want_dir=True)
        if folder_res.path is not None and folder_res.path.is_dir():
            return _search(m.group("name"), want_dir, strict, folder=folder_res.path)

    name = _STRIP.sub("", raw).strip() or raw
    session_folder = session.last_folder
    if session_folder is not None and session_folder.is_dir():
        direct = session_folder / name
        if direct.exists() and (want_dir is None or direct.is_dir() == want_dir):
            return PathResolution(path=direct, via="context-folder")
        local = _search(name, want_dir, strict, folder=session_folder, depth=3)
        if local.path is not None:
            return local
    return _search(name, want_dir, strict)


def _search(name: str, want_dir: bool | None, strict: bool, folder: Path | None = None,
            depth: int = 5) -> PathResolution:
    index = services.files
    if index is None:
        return PathResolution(error="FILE_NOT_FOUND")
    min_score = 0.85 if strict else 0.6
    if folder is not None:
        matches = index.live_search(folder, name, None, want_dir, depth=depth, limit=10, min_score=min_score)
    else:
        matches = index.search(name, None, None, want_dir, limit=10, min_score=min_score)
    return _pick(matches, strict)


def _pick(matches: list[FileMatch], strict: bool) -> PathResolution:
    if not matches:
        return PathResolution(error="FILE_NOT_FOUND")
    top = matches[0]
    close = [m for m in matches if top.score - m.score < 0.03]
    if len(close) == 1 or (top.score >= 0.95 and len([m for m in close if m.score >= 0.95]) == 1):
        return PathResolution(path=top.path, via="search")
    # Several equally good matches: the most recently modified wins only when
    # the names are identical and we are not about to do something destructive.
    if not strict and len({m.path.name.lower() for m in close}) == 1 and top.score >= 0.95:
        return PathResolution(path=top.path, via="search-recent")
    return PathResolution(candidates=[m.path for m in close[:4]], error="AMBIGUOUS", via="search")


def which_question(kind: str, candidates: list[Path]) -> tuple[str, list[str]]:
    """Clarifying question and option labels for ambiguous paths."""
    services.session.set_results(candidates)
    labels = [describe(c) for c in candidates[:4]]
    return tr("Which {kind} do you mean: {options}?", kind=tr(kind), options=join_options(labels)), labels


def ambiguous_paths(kind: str, candidates: list[Path]) -> dict:
    """Tool result asking the user to pick one of several paths."""
    from app.tools.registry import ambiguous

    question, labels = which_question(kind, candidates)
    return ambiguous(question, labels, [str(c) for c in candidates[:4]])


def default_create_folder() -> Path:
    setting = require_settings().files.default_create_folder
    return known_folder(setting) or explicit_path(setting) or paths.user_folder("desktop") or Path.home()
