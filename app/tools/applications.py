"""Application and web tools: open_application, close_application, open_url."""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

from app.i18n import join_options, tr
from app.tools import targets, windows
from app.tools.app_resolver import AppEntry, AppResolver, normalise
from app.tools.context import services
from app.tools.registry import Safety, ambiguous, fail, nexus_tool, ok

log = logging.getLogger("nexus.tools.apps")

# Never send WM_CLOSE to these: they are Windows itself.
_PROTECTED_PROCESSES = {
    "explorer.exe", "dwm.exe", "csrss.exe", "winlogon.exe", "svchost.exe", "lsass.exe", "services.exe",
    "smss.exe", "wininit.exe", "sihost.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe",
    "searchhost.exe", "textinputhost.exe", "lockapp.exe", "applicationframehost.exe", "taskhostw.exe",
}
_NEG = r"\b(don'?t|do not|never)\s+"


def _apps() -> AppResolver:
    if services.apps is None:
        services.apps = AppResolver()
    return services.apps


def _spoken_name(query: str, entry: AppEntry) -> str:
    """Say the app the way the user did ("Bloco de notas", "Chrome") when it is a known alias."""
    q = normalise(query)
    if q and len(q) > 2 and (q in normalise(entry.name) or _apps().is_alias_of(q, entry.name)):
        words = query.strip().split()
        return " ".join([words[0] if words[0].isupper() else words[0].capitalize()] + words[1:])
    return entry.name


def _which_app(names: list[str]) -> dict:
    return ambiguous(tr("Which app do you mean: {options}?", options=join_options(names)), names)


# ------------------------------------------------------------ open anything
def open_path(path: Path) -> dict:
    os.startfile(str(path))  # type: ignore[attr-defined]
    services.session.touch(path)
    message = tr("Folder opened.") if path.is_dir() else tr("{name} opened.", name=path.name)
    return ok(message, path=str(path))


def open_app_entry(query: str, entry: AppEntry) -> dict:
    entry.launch()
    services.session.launched = (entry, time.time())   # type_text / press_keys target this app next
    name = _spoken_name(query, entry)
    log.info("launched %s via %s (%s)", entry.name, entry.kind, entry.target)
    return ok(tr("{name} opened.", name=name), application=entry.name)


def open_web(url: str) -> dict:
    os.startfile(url)  # type: ignore[attr-defined]
    host = url.split("://", 1)[-1].split("/", 1)[0].removeprefix("www.")
    return ok(tr("{name} opened in the browser.", name=host), url=url)


# ------------------------------------------------------------------ tools
@nexus_tool(
    safety=Safety.SAFE,
    confirm="Open {name}?",
    triggers=[r"^(?![\s\S]*(\.(?!exe\b)[a-z0-9]{2,5}\b|\bfiles?\b(?!\s+explorer)|\bfolders?\b|\bdirector(y|ies)\b"
              r"|\bprojects?\b|https?:|\bwebsites?\b|\bsites?\b|\bpage\b|\bscreenshot|\bdelete\b|\bremove\b"
              r"|\bdesktop\b|\bdownloads\b|\bdocuments\b|\bpictures\b|\b[a-z]:[\\/]|" + _NEG + r"(open|launch|start|run)))"
              r"[\s\S]*\b(open|launch|start|run|fire up|bring up|load)\b",
              r"\bfile explorer\b"],
)
def open_application(application: str):
    """Open, launch or start an installed application or program on this computer.

    Args:
        application: the application name exactly as the user said it, e.g. Chrome, Notepad, Visual Studio Code
    """
    res = _apps().resolve(application)
    if res.entry is not None:
        return open_app_entry(application, res.entry)
    if res.candidates:
        return _which_app(res.candidates)
    # "Chrome and Notepad" / "Chrome e Spotify" arrive as one argument: open each part.
    parts = [p.strip() for p in re.split(r",|\band\b|\be\b|&|\bplus\b|\bmais\b", application) if p.strip()]
    if len(parts) > 1:
        entries = [(p, _apps().resolve(p).entry) for p in parts]
        if all(e is not None for _, e in entries):
            names = []
            for part, entry in entries:
                open_app_entry(part, entry)  # type: ignore[arg-type]
                names.append(_spoken_name(part, entry))  # type: ignore[arg-type]
            return ok(tr("{names} and {last} opened.", names=", ".join(names[:-1]), last=names[-1]),
                      applications=names)
    if res.known_but_missing:
        return fail("APPLICATION_NOT_INSTALLED", tr("{name} doesn't seem to be installed.", name=res.known_but_missing))
    # Needle sometimes calls this for websites and files; resolve by target.
    url = targets.as_url(application)
    if url:
        return open_web(url)
    found = targets.resolve_path(application, strict=True)
    if found.path is not None:
        return open_path(found.path)
    return fail("APPLICATION_NOT_FOUND", tr("I couldn't find an app called {name}.", name=application.strip()))


def _sentence_not_name(text: str) -> bool:
    """An 'application name' that is really a sentence ("dia 25 de novembro às 15 horas com o nome teste")."""
    words = text.split()
    return len(words) > 5 or len(text) > 40 or bool(re.search(r"\d{1,2}\s+(de|of)\s+\w+|\b\d{1,2}\s*h", text))


@open_application.repair
def _app_repair(args: dict, _request) -> dict:  # noqa: ANN001
    name = str(args.get("application") or "")
    return dict(args, application="") if _sentence_not_name(name) and not targets.explicit_path(name) else args


@nexus_tool(
    safety=Safety.SAFE,
    confirm="Close {name}?",
    triggers=[r"^(?![\s\S]*" + _NEG + r"(close|quit|exit))[\s\S]*\b(close|quit|exit|kill|terminate|shut)\b"
              r"(?![\s\S]*\b(computer|pc|laptop|windows|system|machine|tabs?)\b)"],
)
def close_application(application: str):
    """Close, quit or exit a running application.

    Args:
        application: the application name as the user said it, e.g. Chrome, Notepad
    """
    res = _apps().resolve(application)
    if res.candidates and res.entry is None:
        return _which_app(res.candidates)
    entry = res.entry
    display = _spoken_name(application, entry) if entry else application.strip().title()
    wins = find_windows(entry, application)
    if not wins:
        return fail("APPLICATION_NOT_RUNNING", tr("{name} isn't open.", name=display))
    import win32con
    import win32gui

    for hwnd, _title, _proc in wins:
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            log.debug("WM_CLOSE failed for %s", hwnd, exc_info=True)
    deadline = time.time() + 3.0
    remaining = wins
    while time.time() < deadline:
        remaining = [w for w in remaining if windows.exists(w[0])]
        if not remaining:
            break
        time.sleep(0.15)
    if remaining:
        return fail("APPLICATION_STILL_OPEN",
                    tr("{name} didn't close. It may be asking you to save something.", name=display),
                    windows=len(remaining))
    return ok(tr("{name} closed.", name=display), windows=len(wins))


def find_windows(entry: AppEntry | None, query: str) -> list[tuple[int, str, str]]:
    """Top-level windows that belong to the application, front-most first."""
    own_pid = os.getpid()
    procs = {p.lower() for p in (entry.processes if entry else set())}
    wanted_class = entry.window_class if entry else None
    names = {n for n in (normalise(query), normalise(entry.name) if entry else "") if len(n) >= 3}
    matches: list[tuple[int, str, str]] = []
    for hwnd in windows.top_level_windows():
        if windows.pid_of(hwnd) == own_pid or windows.class_name(hwnd) in windows.SHELL_CLASSES:
            continue
        title = windows.title(hwnd)
        if wanted_class:
            if windows.class_name(hwnd) == wanted_class:
                matches.append((hwnd, title, "explorer.exe"))
            continue
        proc = windows.real_process(hwnd)
        if proc in _PROTECTED_PROCESSES:
            # A Store-app frame whose app process could not be identified: title match only.
            if proc == "applicationframehost.exe" and names and any(n in normalise(title) for n in names):
                matches.append((hwnd, title, proc))
            continue
        if procs and proc in procs:
            matches.append((hwnd, title, proc))
        elif not procs and names and any(n in normalise(title) for n in names):
            matches.append((hwnd, title, proc))
    return matches


@nexus_tool(
    safety=Safety.SAFE,
    confirm="Open {name}?",
    triggers=[r"https?://|\bwww\.|\b[a-z0-9-]+\.(com|org|net|io|dev|app|ai|co|br|edu|gov|tv|me|info|gg)\b",
              r"\b(website|site|web page|webpage)\b"],
)
def open_url(url: str):
    """Open a website or web address in the browser.

    Args:
        url: the web address or site name, e.g. youtube.com or https://example.com
    """
    url = re.sub(r"^(?:the\s+|o\s+)?(?:website|site|p[aá]gina|page)\s+", "", url.strip(), flags=re.IGNORECASE)
    direct = targets.as_url(url)
    if direct:
        return open_web(direct)
    res = _apps().resolve(url)
    if res.entry is not None and res.score >= 0.93:
        return open_app_entry(url, res.entry)
    guessed = targets.as_url(url, allow_bare_name=True)
    if guessed:
        return open_web(guessed)
    return fail("INVALID_URL", tr("That doesn't look like a web address."))


_DOMAIN = re.compile(r"https?://\S+|\b(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|dev|app|ai|co|br|edu|"
                     r"gov|tv|me|info|gg)(?:\.[a-z]{2})?(?:/\S*)?", re.IGNORECASE)


@open_url.repair
def _url_repair(args: dict, request) -> dict:  # noqa: ANN001
    """The address exactly as written ("youtube.com"), not Needle's rewrite of it."""
    m = _DOMAIN.search(getattr(request, "original", "") or services.session.request)
    return dict(args, url=m.group(0).rstrip(".,!?")) if m else args
