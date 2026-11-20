"""'Start with Windows' via the per-user Run key (no admin rights, easy to undo)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from app import paths

log = logging.getLogger("nexus.autostart")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "NEXUS"


def launch_command(minimized: bool) -> str:
    if paths.is_frozen():
        cmd = f'"{sys.executable}"'
    else:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        exe = pythonw if pythonw.exists() else Path(sys.executable)
        cmd = f'"{exe}" "{paths.install_root() / "app" / "main.py"}"'
    return cmd + (" --minimized" if minimized else "")


def is_enabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool, minimized: bool = False) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, launch_command(minimized))
            log.info("start with Windows enabled")
        else:
            try:
                winreg.DeleteValue(key, VALUE)
                log.info("start with Windows disabled")
            except FileNotFoundError:
                pass
