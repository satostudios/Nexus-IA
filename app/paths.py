"""Filesystem locations used by NEXUS.

Development runs keep bundled assets in the project folder. A PyInstaller build
unpacks them into ``sys._MEIPASS``. User data never lives next to the code:
configuration goes to ``%APPDATA%\\NEXUS`` and large downloaded models and caches
go to ``%LOCALAPPDATA%\\NEXUS``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app import APP_NAME


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Folder that holds the read-only ``assets`` directory."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def install_root() -> Path:
    """Folder of the executable (frozen) or the project root (development)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def asset(*parts: str) -> Path:
    return resource_root().joinpath("assets", *parts)


def _env_dir(var: str, fallback: Path) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else fallback


def config_dir() -> Path:
    path = _env_dir("APPDATA", Path.home() / "AppData" / "Roaming") / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    path = _env_dir("LOCALAPPDATA", Path.home() / "AppData" / "Local") / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    path = data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _package_family() -> str | None:
    """Package family name when this Python is an MSIX-packaged app (Microsoft Store Python)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    try:
        fn = ctypes.windll.kernel32.GetCurrentPackageFamilyName  # type: ignore[attr-defined]
    except AttributeError:
        return None
    length = wintypes.UINT(0)
    if fn(ctypes.byref(length), None) != 122:   # ERROR_INSUFFICIENT_BUFFER; 15700 = not packaged
        return None
    buf = ctypes.create_unicode_buffer(length.value)
    return buf.value if fn(ctypes.byref(length), buf) == 0 else None


def launchable(path: Path) -> Path:
    """The on-disk path of a program NEXUS downloaded, for starting it as a child process.

    Microsoft Store Python sees ``%LOCALAPPDATA%`` through a per-package overlay.
    A program started from there by its overlay path cannot find its own DLLs
    (0xC0000135), so development runs start it from the overlay's real folder.
    The packaged NEXUS.exe is not affected.
    """
    if not _package_family():
        return path
    real = path.resolve()   # inside the package, resolving follows the overlay to LocalCache
    return real if real.exists() else path


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def logs_dir() -> Path:
    """``logs/`` beside the app when writable, otherwise under LOCALAPPDATA."""
    preferred = install_root() / "logs"
    if _writable(preferred):
        return preferred
    fallback = data_dir() / "logs"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def user_folder(name: str) -> Path | None:
    """Resolve a Windows known folder (Desktop, Documents, ...) for this user.

    Uses the shell API so redirected folders (for example OneDrive Desktop)
    resolve correctly, falling back to the conventional profile path.
    """
    known = {
        "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
        "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
        "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
        "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
        "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
        "videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
    }
    key = name.lower()
    guid = known.get(key)
    if guid and sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            import uuid

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                            ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

            raw = uuid.UUID(guid).bytes_le
            fid = GUID.from_buffer_copy(raw)
            out = ctypes.c_wchar_p()
            shell32 = ctypes.windll.shell32
            ole32 = ctypes.windll.ole32
            if shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(out)) == 0:
                value = out.value
                ole32.CoTaskMemFree(out)
                if value:
                    return Path(value)
        except Exception:
            pass
    fallback = Path.home() / name.capitalize()
    return fallback if fallback.exists() else None
