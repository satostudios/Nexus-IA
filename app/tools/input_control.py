"""Keyboard and media tools: type_text, press_keys, media_control.

Where the text goes:

* the window of the app NEXUS just opened ("abra o bloco de notas e digite
  olá") -> typed directly;
* otherwise the last application window you used -> NEXUS asks first
  ("Digitar em 'Documento1 - Word'?");
* terminals, the Run dialog and NEXUS itself are never typed into.

The text to type is taken verbatim from what you said: Needle decides that
this is a typing request, the words come from your sentence.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Literal

from app.i18n import tr
from app.tools import intents, windows
from app.tools.context import services
from app.tools.registry import Preflight, Safety, ToolError, fail, nexus_tool, ok

log = logging.getLogger("nexus.tools.input")

# Command forms only: "digitado" / "escrito" inside the dictated text are not verbs.
_TYPE_VERB = re.compile(r"\b(digite|digita|digitar|escreva|escreve|escrever|anote|anota|anotar|type|write|"
                        r"dictate|jot down)\b\s*[:,-]?\s*"
                        r"(?:(?:o texto|o seguinte|isso|isto|the text|this|the following)\s*[:,-]?\s*)?",
                        re.IGNORECASE)
_TYPE_TAIL = re.compile(r"\s+(?:no|na|in|into|em)\s+(?:o\s+|a\s+|the\s+)?(bloco de notas|notepad|word|documento|"
                        r"arquivo|janela|campo)\s*[.!]?$", re.IGNORECASE)
_COMPOSE = re.compile(r"^(?:(?:me|pra mim|para mim|for me)\s+)?(?:um|uma|a|an|some)\s+(?:\w+\s+)?"
                      r"(poema|poesia|hist[oó]ria|reda[çc][aã]o|carta|e-?mail|texto|piada|resumo|m[uú]sica|artigo|"
                      r"poem|story|essay|letter|joke|summary|song|article)\b", re.IGNORECASE)


# ------------------------------------------------------------- targeting
def _target(wait: float) -> tuple[int | None, bool]:
    """(hwnd, trusted). Trusted = a window of the app NEXUS just launched."""
    launched = services.session.launched
    if launched is not None and time.time() - launched[1] < 90:
        entry = launched[0]
        deadline = time.time() + wait
        from app.tools.applications import find_windows

        while True:
            wins = [w for w in find_windows(entry, getattr(entry, "name", "")) if not windows.unsafe_target(w[0])]
            if wins:
                return wins[0][0], True
            if time.time() >= deadline:
                break
            time.sleep(0.25)
    last = windows.tracker.last
    if last is not None and windows.exists(last.hwnd):
        return last.hwnd, False
    return None, False


def _input_preflight(what: str) -> Preflight:
    hwnd, trusted = _target(wait=8.0)
    if hwnd is None:
        raise ToolError("NO_TARGET", tr("There's no window to type into."))
    if windows.unsafe_target(hwnd):
        return Preflight(arguments={}, error=fail("BLOCKED", tr("For safety I don't type into terminals.")),
                         safety=Safety.BLOCKED)
    services.session.typing_target = (hwnd, time.time())
    name = windows.title(hwnd)[:60]
    title = tr("Type into {name}?", name=f"“{name}”") if what == "type" else what
    return Preflight(arguments={}, title=title, detail=name, spoken=title,
                     safety=None if trusted else Safety.CONFIRMATION_REQUIRED)


def _focused_target() -> int:
    chosen = services.session.typing_target
    hwnd = chosen[0] if chosen and time.time() - chosen[1] < 120 else _target(wait=4.0)[0]
    if hwnd is None or not windows.exists(hwnd):
        raise ToolError("NO_TARGET", tr("There's no window to type into."))
    if windows.unsafe_target(hwnd):
        raise ToolError("BLOCKED", tr("For safety I don't type into terminals."))
    if not windows.focus(hwnd):
        raise ToolError("FOCUS_FAILED", tr("There's no window to type into."))
    time.sleep(0.15)
    if windows.user32.GetForegroundWindow() != hwnd:   # never type into the wrong window
        raise ToolError("FOCUS_FAILED", tr("There's no window to type into."))
    return hwnd


# ---------------------------------------------------------------- typing
@nexus_tool(
    safety=Safety.SAFE,
    confirm="Type into {name}?",
    # Dictation only: "write a poem" / "create an app" are write_text / write_code.
    triggers=[r"^(?![\s\S]*\b(don'?t|do not|never)\s+(type|write))(?![\s\S]*" + intents.COMPOSE + r")"
              r"[\s\S]*\b(type|write|dictate|jot down)\b"],
)
def type_text(text: str):
    """Type text into the open application, as if it was typed on the keyboard.

    Args:
        text: the exact words to type
    """
    text = text.strip()
    if not text:
        return fail("INVALID_ARGUMENTS", tr("What should I type?"))
    if _COMPOSE.match(text):
        return fail("UNSUPPORTED", tr("That's a text to write, not to dictate: ask me to “write” it."))
    hwnd = _focused_target()
    control = windows.text_control(hwnd)
    # Straight into the app's text control when it has a standard one (exact,
    # even when the app is busy); otherwise as keystrokes.
    if control is None or not windows.insert_text(control, text):
        windows.type_text(text)
    return ok(tr("Typed."), characters=len(text))


def dictated_text(original: str) -> str:
    """What follows the first typing verb: "abra o bloco de notas e digite olá mundo" -> 'olá mundo'.

    The first verb starts the dictation; everything after it is the text, even
    if the text itself contains words like "escreva" or "type".
    """
    m = _TYPE_VERB.search(original)
    return _TYPE_TAIL.sub("", original[m.end():]).strip().strip("\"'“”") if m is not None else ""


@type_text.repair
def _repair_text(args: dict, request) -> dict:  # noqa: ANN001
    original = getattr(request, "original", "") or services.session.request
    spoken = dictated_text(original)
    if spoken:
        args = dict(args, text=spoken)
    if _COMPOSE.match(str(args.get("text", "")).strip()):
        # "write me a poem" asks NEXUS to compose, not to type dictation: unsupported.
        return dict(args, text="")
    return args


@type_text.preflight
def _type_preflight(args: dict) -> Preflight:
    text = str(args.get("text", "")).strip()
    if not text:
        raise ToolError("INVALID_ARGUMENTS", tr("What should I type?"))
    if _COMPOSE.match(text):
        raise ToolError("UNSUPPORTED", tr("That's a text to write, not to dictate: ask me to “write” it."))
    pf = _input_preflight("type")
    pf.arguments = {"text": text}
    return pf


# ------------------------------------------------------------ shortcuts
_SEMANTIC = [  # (pattern on the normalised request, chord)
    (r"\bsave\b", ["ctrl", "S"]), (r"\bundo\b", ["ctrl", "Z"]), (r"\bredo\b", ["ctrl", "Y"]),
    (r"\bpaste\b", ["ctrl", "V"]), (r"\bselect all\b", ["ctrl", "A"]), (r"\bnew tab\b", ["ctrl", "T"]),
    (r"\bclose (the |this )?tab\b", ["ctrl", "W"]), (r"\b(refresh|reload)\b", ["f5"]),
    (r"\bswitch (the )?window\b|\balt ?tab\b", ["alt", "tab"]),
]
_NAMES = {
    "enter": "enter", "return": "enter", "tab": "tab", "esc": "esc", "escape": "esc", "space": "space",
    "espaco": "space", "espaço": "space", "backspace": "backspace", "delete": "delete", "del": "delete",
    "up": "up", "cima": "up", "down": "down", "baixo": "down", "left": "left", "esquerda": "left",
    "right": "right", "direita": "right", "home": "home", "inicio": "home", "início": "home", "end": "end",
    "fim": "end", "pageup": "pageup", "pagedown": "pagedown", "ctrl": "ctrl", "control": "ctrl",
    "controle": "ctrl", "alt": "alt", "shift": "shift", "win": "win", "windows": "win",
}


def parse_keys(text: str) -> list[str] | None:
    """'ctrl + s' / 'aperte enter' / 'save' -> ['ctrl', 'S']."""
    low = text.lower()
    for pattern, chord in _SEMANTIC:
        if re.search(pattern, low):
            return list(chord)
    chord: list[str] = []
    for tok in re.findall(r"[a-zçãéíú]+\d*|\d+", low.replace("+", " ")):
        if tok in _NAMES:
            key = _NAMES[tok]
        elif re.fullmatch(r"f([1-9]|1[0-2])", tok):
            key = tok
        elif len(tok) == 1 and tok.isalnum() and chord and chord[-1] in ("ctrl", "alt", "shift", "win"):
            key = tok.upper()
        else:
            continue
        if key not in chord:
            chord.append(key)
    if not chord or all(k in ("ctrl", "alt", "shift", "win") for k in chord):
        return None
    mods = [k for k in chord if k in ("ctrl", "alt", "shift", "win")]
    rest = [k for k in chord if k not in mods]
    return mods + rest[:1] if mods else rest[:1]


@nexus_tool(
    safety=Safety.SAFE,
    confirm="Press {name} in {window}?",
    triggers=[r"\b(press|hit)\b", r"\b(save|undo|redo|paste|refresh|reload)\b(?![\s\S]*\b(called|as|location|place)\b)",
              r"\bselect all\b", r"\bnew tab\b", r"\bclose (the |this )?tab\b", r"\balt ?tab\b|\bswitch (the )?window\b"],
)
def press_keys(keys: str):
    """Press a key or keyboard shortcut in the open application.

    Args:
        keys: the key or shortcut, e.g. enter, ctrl+s
    """
    chord = parse_keys(keys)
    if chord is None:
        return fail("INVALID_ARGUMENTS", tr("Which key should I press?"))
    _focused_target()
    windows.press(chord)
    return ok(tr("Pressed {name}.", name="+".join(chord)))


@press_keys.repair
def _repair_keys(args: dict, request) -> dict:  # noqa: ANN001
    for source in (getattr(request, "text", ""), getattr(request, "original", ""), str(args.get("keys", ""))):
        chord = parse_keys(source)
        if chord:
            return dict(args, keys="+".join(chord))
    return args


@press_keys.preflight
def _keys_preflight(args: dict) -> Preflight:
    chord = parse_keys(str(args.get("keys", "")))
    if chord is None:
        raise ToolError("INVALID_ARGUMENTS", tr("Which key should I press?"))
    name = "+".join(chord)
    pf = _input_preflight("keys")
    window = pf.detail
    pf.title = pf.spoken = tr("Press {name} in {window}?", name=name, window=f"“{window}”")
    pf.arguments = {"keys": name}
    return pf


# ----------------------------------------------------------------- media
_MEDIA_VK = {"volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD, "play_pause": 0xB3, "next": 0xB0,
             "previous": 0xB1}
_MEDIA_MSG = {"volume_up": "Volume up.", "volume_down": "Volume down.", "mute": "Mute toggled.",
              "play_pause": "Play/pause.", "next": "Next track.", "previous": "Previous track."}


def media_action(text: str) -> str | None:
    low = text.lower()
    if re.search(r"\b(unmute|mute)\b", low):
        return "mute"
    if re.search(r"\b(next|skip)\b", low):
        return "next"
    if re.search(r"\b(previous|back)\b", low):
        return "previous"
    if re.search(r"\b(increase|louder|turn up|raise|up|higher)\b", low) and "volume" in low or "louder" in low:
        return "volume_up"
    if re.search(r"\b(decrease|quieter|turn down|lower|down)\b", low) and "volume" in low or "quieter" in low:
        return "volume_down"
    if re.search(r"\b(pause|play|resume|stop|continue)\b", low):
        return "play_pause"
    return None


@nexus_tool(
    safety=Safety.SAFE,
    confirm="{name}?",
    triggers=[r"\b(volume|mute|unmute|louder|quieter|next track|previous track)\b",
              r"\b(pause|play|resume|skip)\b[\s\S]*\b(music|song|track|video|media|playback)\b",
              r"^\s*(please\s+)?(pause|play|resume)\s*[.!]*$"],
)
def media_control(action: Literal["volume_up", "volume_down", "mute", "play_pause", "next", "previous"]):
    """Control music/video playback and the system volume.

    Args:
        action: what to do with the media or volume
    """
    vk = _MEDIA_VK.get(action)
    if vk is None:
        return fail("INVALID_ARGUMENTS", tr("Which media action?"))
    windows.media_key(vk, times=5 if action in ("volume_up", "volume_down") else 1)
    return ok(tr(_MEDIA_MSG[action]), action=action)


@media_control.repair
def _repair_media(args: dict, request) -> dict:  # noqa: ANN001
    action = media_action(getattr(request, "text", "") or services.session.request)
    return dict(args, action=action) if action else args
