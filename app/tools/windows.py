"""Window discovery, focus and synthetic keyboard input (Win32).

Used by close_application, type_text and press_keys. Typing is only ever sent
after the target window is verified to be in the foreground, never into
NEXUS itself, and never into terminals or the Run dialog (typed text there
would become a command).
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

log = logging.getLogger("nexus.windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
for _fn in ("IsWindow", "IsWindowVisible", "IsIconic", "BringWindowToTop", "SetForegroundWindow"):
    getattr(user32, _fn).argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.EnumChildWindows.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.LPARAM]
user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.c_void_p]
user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                       wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageTimeoutW.restype = ctypes.c_size_t

# Windows itself: never targets for typing / closing.
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "TaskListThumbnailWnd",
                 "MultitaskingViewFrame", "ForegroundStaging", "XamlExplorerHostIslandWindow",
                 "Windows.UI.Core.CoreWindow", "NotifyIconOverflowWindow", "TopLevelWindowForOverflowXamlIsland"}
TERMINAL_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "mintty", "VirtualConsoleClass"}
TERMINAL_PROCESSES = {"cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe", "conhost.exe",
                      "mintty.exe", "bash.exe", "wsl.exe", "openconsole.exe", "powershell_ise.exe"}


# ------------------------------------------------------------------ info
def title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def pid_of(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def process_name(pid: int) -> str:
    try:
        import psutil
        return psutil.Process(pid).name().lower()
    except Exception:
        return ""


def exists(hwnd: int | None) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd)) and bool(user32.IsWindowVisible(hwnd))


def is_terminal(hwnd: int) -> bool:
    return class_name(hwnd) in TERMINAL_CLASSES or process_name(pid_of(hwnd)) in TERMINAL_PROCESSES


def is_run_dialog(hwnd: int) -> bool:
    return class_name(hwnd) == "#32770" and title(hwnd).lower() in ("executar", "run")


def unsafe_target(hwnd: int) -> bool:
    return is_terminal(hwnd) or is_run_dialog(hwnd) or pid_of(hwnd) == os.getpid()


# ---------------------------------------------------- foreground tracker
@dataclass
class Foreground:
    hwnd: int
    title: str
    seen: float


class ForegroundTracker:
    """Remembers the last real application window the user was in (not NEXUS)."""

    def __init__(self) -> None:
        self.last: Foreground | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="foreground", daemon=True)

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        own = os.getpid()
        while not self._stop.wait(0.4):
            try:
                hwnd = user32.GetForegroundWindow()
                if not hwnd or pid_of(hwnd) == own or class_name(hwnd) in SHELL_CLASSES:
                    continue
                text = title(hwnd)
                if text:
                    self.last = Foreground(hwnd, text, time.time())
            except Exception:
                continue


tracker = ForegroundTracker()


# -------------------------------------------------------------- windows
def top_level_windows() -> list[int]:
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd: int, _l: int) -> bool:
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4) and title(hwnd):  # GW_OWNER = 4
            ex = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
            if not ex & 0x00000080:  # WS_EX_TOOLWINDOW
                found.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return found


def real_process(hwnd: int) -> str:
    """Process behind a window, looking through Store-app frame hosts."""
    pid = pid_of(hwnd)
    name = process_name(pid)
    if name != "applicationframehost.exe":
        return name
    found: list[str] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def child(ch: int, _l: int) -> bool:
        cpid = pid_of(ch)
        if cpid != pid:
            found.append(process_name(cpid))
            return False
        return True

    user32.EnumChildWindows(hwnd, child, 0)
    return found[0] if found else name


# ------------------------------------------------------------------ focus
def focus(hwnd: int, timeout: float = 1.5) -> bool:
    """Bring ``hwnd`` to the foreground and verify it got there."""
    if not exists(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    if user32.GetForegroundWindow() == hwnd:
        return True
    current = kernel32.GetCurrentThreadId()
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_thread) and fg_thread != current and user32.AttachThreadInput(current, fg_thread, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(current, fg_thread, False)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.05)
    # Last resort for the foreground lock: a synthetic ALT tap, then retry.
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.2)
    return user32.GetForegroundWindow() == hwnd


# ------------------------------------------------------------------ input
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_EXTENDEDKEY = 0x0001
ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send(events: list[INPUT]) -> None:
    if not events:
        return
    arr = (INPUT * len(events))(*events)
    sent = user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))
    if sent != len(events):
        raise OSError(f"SendInput delivered {sent}/{len(events)} events")


def _key(vk: int, up: bool = False, extended: bool = False) -> INPUT:
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    scan = user32.MapVirtualKeyW(vk, 0)   # MAPVK_VK_TO_VSC: XAML apps read scan codes too
    return INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)))


def _unicode(unit: int, up: bool) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(type=INPUT_KEYBOARD, u=_U(ki=KEYBDINPUT(wVk=0, wScan=unit, dwFlags=flags, time=0, dwExtraInfo=0)))


CHAR_DELAY = 0.012


def type_text(text: str) -> None:
    """Type ``text`` into the foreground window (any Unicode, accents included).

    One character per SendInput with a short pause: measured on Windows 11
    Notepad, bursts of injected characters get garbled ("ABC abc NEXUS 123."
    arrived as "ABC abc !!!!!!!!!!!!!!!"), one-at-a-time input arrives intact.
    """
    for ch in text.replace("\r\n", "\n"):
        if ch == "\n":
            _send([_key(0x0D), _key(0x0D, True)])
        elif ch == "\t":
            _send([_key(0x09), _key(0x09, True)])
        else:
            data = ch.encode("utf-16-le")
            units = [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]
            _send([_unicode(u, False) for u in units] + [_unicode(u, True) for u in units])
        time.sleep(CHAR_DELAY)


VK = {
    "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "space": 0x20, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23, "pageup": 0x21,
    "pagedown": 0x22, "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    **{chr(c): c for c in range(ord("A"), ord("Z") + 1)}, **{str(d): 0x30 + d for d in range(10)},
}
EXTENDED = {0x2E, 0x2D, 0x26, 0x28, 0x25, 0x27, 0x24, 0x23, 0x21, 0x22, 0x5B}


KEY_DELAY = 0.04


def press(combo: list[str]) -> None:
    """Press a chord such as ['ctrl', 'S'] (modifiers first).

    One key per SendInput with a pause: sent as one burst, Windows 11 Notepad
    saw the S before the Ctrl and typed an "s" instead of saving.
    """
    codes = [VK[k] for k in combo]
    for c in codes:
        _send([_key(c, extended=c in EXTENDED)])
        time.sleep(KEY_DELAY)
    for c in reversed(codes):
        _send([_key(c, True, extended=c in EXTENDED)])
        time.sleep(KEY_DELAY)


# ------------------------------------------------------- text controls
EDIT_CLASSES = {"Edit", "RichEdit20W", "RichEdit20A", "RICHEDIT50W", "RichEditD2DPT"}
WM_GETTEXT, WM_GETTEXTLENGTH, EM_SETSEL, EM_REPLACESEL = 0x000D, 0x000E, 0x00B1, 0x00C2


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]


def text_control(hwnd: int) -> int | None:
    """The standard (Rich)Edit control that holds ``hwnd``'s text, if it has one.

    Windows 11 Notepad keeps its document in a ``RichEditD2DPT``; classic
    Notepad, WordPad and most Win32 editors use Edit / RichEdit. Text can be
    put straight into such a control (:func:`insert_text`) instead of being
    typed as keystrokes.
    """
    if class_name(hwnd) in EDIT_CLASSES:
        return hwnd
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    thread = user32.GetWindowThreadProcessId(hwnd, None)
    if thread and user32.GetGUIThreadInfo(thread, ctypes.byref(info)) and info.hwndFocus and \
            class_name(info.hwndFocus) in EDIT_CLASSES:
        return info.hwndFocus
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(child: int, _l: int) -> bool:
        if class_name(child) in EDIT_CLASSES and user32.IsWindowVisible(child):
            found.append(child)
        return True

    user32.EnumChildWindows(hwnd, visit, 0)
    return found[0] if len(found) == 1 else None


def _message(hwnd: int, msg: int, wparam: int, lparam: int, timeout_ms: int = 3000) -> int | None:
    result = ctypes.c_size_t()
    ok = user32.SendMessageTimeoutW(hwnd, msg, wparam, lparam, 0x0002, timeout_ms, ctypes.byref(result))  # ABORTIFHUNG
    return result.value if ok else None


def insert_text(control: int, text: str) -> bool:
    """Insert ``text`` at the caret of a (Rich)Edit control, undoable, exactly as given.

    Unlike keystrokes this cannot be garbled by a busy application and never
    lands in another window if the user switches away.
    """
    buf = ctypes.create_unicode_buffer(text.replace("\r\n", "\n").replace("\n", "\r\n"))
    return _message(control, EM_REPLACESEL, 1, ctypes.cast(buf, ctypes.c_void_p).value or 0) is not None


def control_text(control: int) -> str:
    n = _message(control, WM_GETTEXTLENGTH, 0, 0) or 0
    buf = ctypes.create_unicode_buffer(n + 1)
    _message(control, WM_GETTEXT, n + 1, ctypes.cast(buf, ctypes.c_void_p).value or 0)
    return buf.value


def caret_to_end(control: int) -> None:
    _message(control, EM_SETSEL, 0x7FFFFFFF, 0x7FFFFFFF)


def media_key(vk: int, times: int = 1) -> None:
    for _ in range(times):
        _send([_key(vk, extended=True), _key(vk, True, extended=True)])
        time.sleep(0.02)
