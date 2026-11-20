"""NEXUS entry point.

    python -m app.main              development
    NEXUS.exe                       packaged build
    NEXUS.exe --minimized           start minimized (used by "Start with Windows")
    NEXUS.exe --selftest            headless check of every subsystem, exit code 0 = OK
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    # Allow ``python app/main.py`` (used by the Start with Windows entry in dev mode).
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _dispatch_needle_worker() -> bool:
    """Needle runs tuned ``.cact`` weights in a child process started as
    ``[sys.executable, <needle/_worker.py>, "--child"]``. In a frozen build
    sys.executable is NEXUS.exe, so route that invocation to the worker."""
    if len(sys.argv) >= 3 and sys.argv[-1] == "--child":
        script = Path(sys.argv[1])
        if script.parent.name == "needle" and script.name.startswith("_worker"):
            from needle import _worker

            raise SystemExit(_worker._child())
    return False


def _ensure_std_streams() -> None:
    # Windowed (no console) builds have no stdout/stderr; libraries that print
    # or show progress bars would crash writing to None.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def main() -> int:
    _dispatch_needle_worker()
    _ensure_std_streams()
    if "--selftest" in sys.argv:
        from app.selftest import run_selftest

        return run_selftest()

    from app.config.settings import Settings
    from app.logging_setup import setup_logging

    settings = Settings.load()
    log_file = setup_logging(settings.debug.enable_logs, settings.debug.log_level)
    import logging

    log = logging.getLogger("nexus")
    from app import __version__

    log.info("NEXUS %s starting (frozen=%s, log=%s)", __version__, getattr(sys, "frozen", False), log_file)

    from app.ai.needle_engine import configure_environment

    configure_environment(settings)

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    from app.ui.nexus_core import configure_default_format

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    configure_default_format()
    app = QApplication(sys.argv)
    app.setApplicationName("NEXUS")
    app.setOrganizationName("NEXUS")

    from app.audio.audio_manager import AudioManager
    from app.core.assistant import Assistant
    from app.core.state import StateModel
    from app.ui import theme
    from app.ui.main_window import MainWindow

    theme.load_fonts()
    app.setFont(theme.font(10))
    state = StateModel()
    audio = AudioManager(settings)
    assistant = Assistant(settings, state, audio)
    window = MainWindow(settings, assistant)
    if settings.system.start_minimized or "--minimized" in sys.argv:
        window.showMinimized()
    else:
        window.show()
    assistant.start()
    code = app.exec()
    log.info("NEXUS exited (%s)", code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
