"""System tools: time, system information, lock, shutdown, restart."""

from __future__ import annotations

import ctypes
import datetime
import logging
import platform
import subprocess
import sys
import time

from app.i18n import clock, tr
from app.tools.registry import Safety, fail, nexus_tool, ok

log = logging.getLogger("nexus.tools.system")

_NO = r"^(?![\s\S]*\b(don'?t|do not|never|cancel)\b)"
_MACHINE = r"(\s+(the|my|this))?\s*(computer|pc|laptop|windows|system|machine|device|notebook)?\s*(now|please)?\s*[.!?]*$"
CREATE_NO_WINDOW = 0x08000000


def _run_power_command(args: list[str]) -> None:
    """Run ``shutdown.exe``. Isolated so tests can intercept it."""
    subprocess.run(["shutdown.exe", *args], check=True, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)


def _lock_workstation() -> bool:
    return bool(ctypes.windll.user32.LockWorkStation())  # type: ignore[attr-defined]


@nexus_tool(
    safety=Safety.SAFE,
    confirm="Tell the time?",
    triggers=[r"^(?![\s\S]*\b(ram|memory|cpu|processor|battery|disk|storage|uptime|timer|weather|forecast)\b)"
              r"[\s\S]*\b(time|date|day|clock|today|what's the hour)\b"],
)
def get_system_time():
    """Tell the current time and date."""
    now = datetime.datetime.now()
    time_text, day = clock(now)
    return ok(tr("It's {clock}, {day}.", clock=time_text, day=day), iso=now.isoformat(timespec="seconds"))


def _windows_name() -> str:
    release = platform.release()
    try:
        build = sys.getwindowsversion().build  # type: ignore[attr-defined]
        if release == "10" and build >= 22000:
            release = "11"
    except AttributeError:
        pass
    return f"Windows {release}"


def _cpu_name() -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            return " ".join(str(name).split())
    except OSError:
        return platform.processor() or "unknown processor"


@nexus_tool(
    safety=Safety.SAFE,
    confirm="Show system information?",
    triggers=[r"\b(system info(rmation)?|specs?|specifications|ram|memory|cpu|processor|battery|disk space|"
              r"storage|windows version|uptime|about (this|my) (pc|computer))\b"],
)
def get_system_information():
    """Report system information: Windows version, processor, memory, battery and disk."""
    import psutil

    vm = psutil.virtual_memory()
    total_gb = round(vm.total / 1024 ** 3)
    cpu = _cpu_name().replace("(R)", "").replace("(TM)", "").replace("12th Gen ", "").replace("13th Gen ", "")
    parts = [_windows_name(), " ".join(cpu.split()), tr("{gb} GB memory, {pct}% used", gb=total_gb, pct=f"{vm.percent:.0f}")]
    battery = psutil.sensors_battery()
    if battery is not None:
        parts.append(tr("battery {pct}%", pct=f"{battery.percent:.0f}"))
    try:
        disk = psutil.disk_usage("C:\\")
    except OSError:
        disk = None
    uptime_h = (time.time() - psutil.boot_time()) / 3600
    data = {
        "os": _windows_name(), "version": platform.version(), "cpu": _cpu_name(),
        "cores": psutil.cpu_count(logical=True), "memory_gb": total_gb, "memory_used_percent": vm.percent,
        "battery_percent": battery.percent if battery else None,
        "disk_free_gb": round(disk.free / 1024 ** 3, 1) if disk else None, "uptime_hours": round(uptime_h, 1),
        "machine": platform.node(),
    }
    return ok(", ".join(parts) + ".", **data)


@nexus_tool(
    safety=Safety.SAFE,
    confirm="Show help?",
    triggers=[r"\b(what can you do|what do you do|help|what are your commands|how do i use you)\b"],
)
def show_help():
    """Explain what NEXUS can do, with a few example commands."""
    return ok(tr("I can write texts and programs for you, open and close programs, search the web, type into "
                 "Notepad, control volume and music, handle files and folders, take screenshots and tell the time. "
                 "Try: open Antigravity and write a Python expense tracker."))


@nexus_tool(
    safety=Safety.SAFE,
    confirm="Lock the computer?",
    triggers=[r"^(?![\s\S]*\b(don'?t|do not|unlock)\b)[\s\S]*\block\b"],
)
def lock_windows():
    """Lock the computer screen."""
    if not _lock_workstation():
        return fail("LOCK_FAILED", tr("Windows didn't lock."))
    return ok(tr("Locked."))


@nexus_tool(
    safety=Safety.CONFIRMATION_REQUIRED,
    destructive=True,
    confirm="Shut down the computer?",
    triggers=[_NO + r"[\s\S]*\b(shut ?down|turn off|power off|switch off|power down)\b" + _MACHINE],
)
def shutdown_windows():
    """Shut down (turn off) the computer."""
    _run_power_command(["/s", "/t", "5"])
    return ok(tr("Shutting down in five seconds."))


@nexus_tool(
    safety=Safety.CONFIRMATION_REQUIRED,
    destructive=True,
    confirm="Restart the computer?",
    triggers=[_NO + r"[\s\S]*\b(restart|reboot)\b" + _MACHINE],
)
def restart_windows():
    """Restart (reboot) the computer."""
    _run_power_command(["/r", "/t", "5"])
    return ok(tr("Restarting in five seconds."))
