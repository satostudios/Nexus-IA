"""Map what the user calls an application onto something Windows can launch.

Sources, in order of authority:

1. user aliases from local memory ("editor" -> "Visual Studio Code");
2. the built-in alias table ``assets/config/app_aliases.json`` (locale
   independent: works on a Portuguese Windows where Calculator is
   "Calculadora");
3. the Start menu (``Get-StartApps``), which lists classic and Store apps with
   their AppIDs and is launched through ``shell:AppsFolder``;
4. App Paths registration and ``PATH``.

The Start menu index is cached on disk and refreshed in the background, so a
lookup never waits for PowerShell.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app import paths

log = logging.getLogger("nexus.apps")

CREATE_NO_WINDOW = 0x08000000
_VERSION = re.compile(r"(\d{2,4}(?:\.\d+)*)")
_SKIP_NAME = re.compile(
    r"\b(uninstall|readme|read me|documentation|website|help|faq|release notes|samples?|"
    r"support center|localization reference|skin format|license|manual)\b", re.IGNORECASE)
_SKIP_TARGET = re.compile(r"\.(url|pdf|html?|txt|chm|zip|rar|7z|md|rtf|ini|log)$", re.IGNORECASE)
_FILLER = re.compile(r"\b(the|app|application|program|software|my|please|o|a|os|as|meu|minha|aplicativo|programa|"
                     r"por favor)\b")
_BROWSER_WORDS = {"browser", "web browser", "default browser", "navegador", "navegador de internet",
                  "navegador padrao", "navegador padrão", "internet"}


def normalise(text: str) -> str:
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[®™©]", "", text)
    text = re.sub(r"[^\w\s.+#-]", " ", text)
    text = _FILLER.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip(" .")


@dataclass
class AppEntry:
    name: str                   # display name
    target: str                 # AppID, path or URI
    kind: str                   # "appsfolder" | "path" | "uri"
    processes: set[str] = field(default_factory=set)
    window_class: str | None = None
    source: str = ""

    def launch(self) -> None:
        if self.kind == "appsfolder":
            try:
                os.startfile(f"shell:AppsFolder\\{self.target}")  # type: ignore[attr-defined]
            except OSError:
                subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{self.target}"],
                                 creationflags=CREATE_NO_WINDOW)
        elif self.kind == "uri":
            os.startfile(self.target)  # type: ignore[attr-defined]
        else:
            target = Path(self.target)
            if target.suffix.lower() in (".exe", ".com") and target.exists():
                subprocess.Popen([str(target)], cwd=str(target.parent),
                                 creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                                 close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            else:
                os.startfile(self.target)  # type: ignore[attr-defined]


@dataclass
class Resolution:
    entry: AppEntry | None = None
    candidates: list[str] = field(default_factory=list)   # when ambiguous
    known_but_missing: str | None = None                  # alias known, app not installed
    score: float = 0.0


@dataclass
class _Builtin:
    canonical: str
    aliases: list[str]
    appids: list[str]
    names: list[str]
    launch: list[str]
    processes: list[str]
    window_class: str | None


class AppResolver:
    def __init__(self, cache_file: Path | None = None,
                 user_alias: Callable[[str], str | None] | None = None) -> None:
        self._cache_file = cache_file or (paths.cache_dir() / "start_apps.json")
        self._user_alias = user_alias
        self._lock = threading.Lock()
        self._start: list[tuple[str, str]] = []   # (name, appid)
        self._start_by_id: dict[str, str] = {}
        self._built_at = 0.0
        self._refreshing = False
        self._builtins = self._load_builtins()
        self._alias_index: dict[str, _Builtin] = {}
        for b in self._builtins:
            self._alias_index[normalise(b.canonical)] = b
            for a in b.aliases:
                self._alias_index[normalise(a)] = b
        self._load_cache()

    # ------------------------------------------------------------- data
    @staticmethod
    def _load_builtins() -> list[_Builtin]:
        path = paths.asset("config", "app_aliases.json")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.error("could not read %s", path, exc_info=True)
            return []
        out = []
        for canonical, spec in raw.items():
            if canonical.startswith("_") or not isinstance(spec, dict):
                continue
            out.append(_Builtin(canonical, list(spec.get("aliases", [])), list(spec.get("appids", [])),
                                list(spec.get("names", [])), list(spec.get("launch", [])),
                                list(spec.get("process", [])), spec.get("window_class")))
        return out

    def _load_cache(self) -> None:
        try:
            raw = json.loads(self._cache_file.read_text(encoding="utf-8"))
            self._set_start([(e["name"], e["appid"]) for e in raw.get("entries", [])], raw.get("built", 0.0))
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _set_start(self, entries: list[tuple[str, str]], built: float) -> None:
        clean = []
        for name, appid in entries:
            if not name or not appid or _SKIP_NAME.search(name):
                continue
            low = appid.lower()
            if low.startswith(("http://", "https://")) or _SKIP_TARGET.search(appid):
                continue
            clean.append((name, appid))
        with self._lock:
            self._start = clean
            self._start_by_id = {appid.lower(): name for name, appid in clean}
            self._built_at = built

    def refresh(self) -> int:
        """Rebuild the Start menu index (about one second). Returns entry count."""
        script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
                  "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress")
        try:
            proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                  capture_output=True, timeout=30, stdin=subprocess.DEVNULL,
                                  creationflags=CREATE_NO_WINDOW)
            raw = json.loads(proc.stdout.decode("utf-8", "replace") or "[]")
        except (OSError, subprocess.TimeoutExpired, ValueError):
            log.warning("Get-StartApps failed; keeping cached app index", exc_info=True)
            return len(self._start)
        if isinstance(raw, dict):
            raw = [raw]
        entries = [(str(e.get("Name") or ""), str(e.get("AppID") or "")) for e in raw if isinstance(e, dict)]
        built = time.time()
        self._set_start(entries, built)
        try:
            self._cache_file.write_text(json.dumps(
                {"built": built, "entries": [{"name": n, "appid": a} for n, a in entries]}, ensure_ascii=False),
                encoding="utf-8")
        except OSError:
            log.warning("could not write app cache", exc_info=True)
        log.info("Start menu index: %d apps", len(self._start))
        return len(self._start)

    def refresh_async(self) -> None:
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True

        def run() -> None:
            try:
                self.refresh()
            finally:
                with self._lock:
                    self._refreshing = False

        threading.Thread(target=run, name="app-index", daemon=True).start()

    @property
    def size(self) -> int:
        return len(self._start)

    # -------------------------------------------------------- resolution
    def resolve(self, query: str, _depth: int = 0) -> Resolution:
        q = normalise(query)
        if not q:
            return Resolution()
        if self._user_alias and _depth == 0:
            target = self._user_alias(query) or self._user_alias(q)
            if target:
                res = self.resolve(target, _depth=1)
                if res.entry or res.candidates:
                    return res
        if q in _BROWSER_WORDS or query.strip().lower() in _BROWSER_WORDS:
            browser = self.default_browser()
            if browser is not None:
                return Resolution(entry=browser, score=1.0)
        builtin = self._alias_index.get(q) or self._near_alias(q)
        if builtin:
            entry = self._from_builtin(builtin)
            if entry:
                return Resolution(entry=entry, score=1.0)
            # Known application that does not seem to be installed; still look
            # at the Start menu in case it is registered under another name.
            res = self._from_start(q)
            if res.entry and res.score >= 0.93:
                return res
            return Resolution(known_but_missing=builtin.canonical)
        res = self._from_start(q)
        if res.entry or res.candidates:
            return res
        exe = self._which(q)
        if exe:
            return Resolution(entry=AppEntry(Path(exe).stem, exe, "path", {Path(exe).name.lower()},
                                             source="path"), score=0.8)
        if len(self._start) == 0 or time.time() - self._built_at > 60:
            # Index empty or stale: refresh once synchronously and retry.
            self.refresh()
            res = self._from_start(q)
            if res.entry or res.candidates:
                return res
        return Resolution()

    def _near_alias(self, q: str) -> _Builtin | None:
        """A single clear near-miss of a known alias ("crome" -> chrome), as speech
        recognition produces for app names; ambiguous near-misses are ignored."""
        if len(q) < 4:
            return None
        scored = sorted(((difflib.SequenceMatcher(None, q, alias).ratio(), alias)
                         for alias in self._alias_index if abs(len(alias) - len(q)) <= 2), reverse=True)
        if scored and scored[0][0] >= 0.85 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.08):
            log.info("app name %r treated as %r", q, scored[0][1])
            return self._alias_index[scored[0][1]]
        return None

    def is_alias_of(self, query: str, name: str) -> bool:
        """Is ``query`` a built-in alias of the application called ``name``?"""
        builtin = self._alias_index.get(normalise(query))
        return builtin is not None and normalise(builtin.canonical) == normalise(name)

    def default_browser(self) -> AppEntry | None:
        """The user's default web browser (from the https URL association)."""
        if sys.platform != "win32":
            return None
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\Shell\Associations"
                                r"\UrlAssociations\https\UserChoice") as key:
                progid = winreg.QueryValueEx(key, "ProgId")[0]
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{progid}\shell\open\command") as key:
                command = winreg.QueryValueEx(key, "")[0]
        except OSError:
            return None
        m = re.match(r'\s*"([^"]+)"|\s*(\S+)', command)
        exe = (m.group(1) or m.group(2)) if m else ""
        if not exe or not os.path.exists(exe):
            return None
        proc = Path(exe).name.lower()
        name = next((b.canonical for b in self._builtins if proc in {p.lower() for p in b.processes}),
                    Path(exe).stem.capitalize())
        return AppEntry(name, exe, "path", {proc}, None, "default-browser")

    def _from_builtin(self, b: _Builtin) -> AppEntry | None:
        procs = {p.lower() for p in b.processes}
        with self._lock:
            start = list(self._start)
            by_id = dict(self._start_by_id)
        for appid in b.appids:
            if appid.lower() in by_id:
                return AppEntry(b.canonical, appid, "appsfolder", procs, b.window_class, "builtin+start")
        for prefix in b.names:
            p = normalise(prefix)
            matches = [(n, a) for n, a in start if normalise(n) == p or normalise(n).startswith(p + " ")]
            if matches:
                name, appid = _newest(matches)
                procs |= _process_from_appid(appid)
                return AppEntry(name, appid, "appsfolder", procs, b.window_class, "builtin+start")
        for cmd in b.launch:
            if cmd.endswith(":"):
                return AppEntry(b.canonical, cmd, "uri", procs, b.window_class, "builtin-uri")
            expanded = os.path.expandvars(cmd)
            if os.path.isabs(expanded):
                if os.path.exists(expanded):
                    return AppEntry(b.canonical, expanded, "path", procs, b.window_class, "builtin-path")
                continue
            found = self._which(expanded)
            if found:
                return AppEntry(b.canonical, found, "path", procs, b.window_class, "builtin-path")
        return None

    def _from_start(self, q: str) -> Resolution:
        with self._lock:
            start = list(self._start)
        if not start:
            return Resolution()
        q_tokens = q.split()
        scored: list[tuple[float, str, str]] = []
        for name, appid in start:
            n = normalise(name)
            if not n:
                continue
            base = _VERSION.sub("", n).strip()
            if n == q or base == q:
                score = 1.0
            elif n.startswith(q + " ") or base.startswith(q + " "):
                score = 0.93
            elif all(t in n.split() for t in q_tokens):
                score = 0.88
            elif len(q) >= 5 and any(w.startswith(q) and len(q) >= 0.6 * len(w) for w in n.split()):
                # a clear prefix of a word ("photo" / "photoshop" is not enough: "cone"
                # must not open "Conexão de Área de Trabalho Remota")
                score = 0.8
            else:
                score = difflib.SequenceMatcher(None, q, base).ratio()
                if score < 0.84:
                    continue
            scored.append((score, name, appid))
        if not scored:
            return Resolution()
        scored.sort(key=lambda s: s[0], reverse=True)
        best = scored[0][0]
        top = [(n, a) for s, n, a in scored if best - s < 0.02]
        if len(top) > 1:
            families = {_VERSION.sub("", normalise(n)).strip() for n, _ in top}
            if len(families) == 1:
                top = [_newest(top)]
            else:
                return Resolution(candidates=[n for n, _ in top[:4]], score=best)
        name, appid = top[0]
        kind = "appsfolder"
        if re.match(r"^[a-z][a-z0-9+.-]*://", appid, re.IGNORECASE):
            kind = "uri"
        elif re.match(r"^[a-z]:\\", appid, re.IGNORECASE):
            kind = "path"
        procs = _process_from_appid(appid)
        builtin = self._alias_index.get(normalise(name))
        if builtin:
            procs |= {p.lower() for p in builtin.processes}
        return Resolution(entry=AppEntry(name, appid, kind, procs, None, "start"), score=best)

    @staticmethod
    def _which(name: str) -> str | None:
        candidates = [name] if name.lower().endswith(".exe") else [name + ".exe", name]
        for c in candidates:
            if " " in c:
                continue
            found = shutil.which(c)
            if found and found.lower().endswith((".exe", ".com")):
                return found
            reg = _app_paths(c)
            if reg:
                return reg
        return None


def _newest(items: list[tuple[str, str]]) -> tuple[str, str]:
    def version(name: str) -> tuple[int, ...]:
        m = _VERSION.findall(name)
        if not m:
            return (0,)
        return tuple(int(x) for x in m[-1].split(".") if x.isdigit())
    return max(items, key=lambda item: version(item[0]))


def _process_from_appid(appid: str) -> set[str]:
    base = appid.replace("/", "\\").rsplit("\\", 1)[-1]
    return {base.lower()} if base.lower().endswith(".exe") else set()


def _app_paths(exe: str) -> str | None:
    if sys.platform != "win32":
        return None
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as key:
                value, _ = winreg.QueryValueEx(key, "")
                value = os.path.expandvars(str(value).strip('"'))
                if value and os.path.exists(value):
                    return value
        except OSError:
            continue
    return None
