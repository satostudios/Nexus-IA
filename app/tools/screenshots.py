"""Screenshot tool. Captures all monitors into Pictures\\NEXUS\\Screenshots."""

from __future__ import annotations

import datetime
from pathlib import Path

from app.tools import targets
from app.tools.context import services
from app.i18n import tr
from app.tools.registry import Safety, nexus_tool


def capture(folder: Path | None = None) -> Path:
    import mss
    import mss.tools

    folder = folder or targets.screenshots_folder()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = folder / f"NEXUS_{stamp}.png"
    n = 2
    while path.exists():
        path = folder / f"NEXUS_{stamp}_{n}.png"
        n += 1
    factory = getattr(mss, "MSS", None) or mss.mss   # mss >= 10 renamed the class
    with factory() as sct:
        shot = sct.grab(sct.monitors[0])  # the virtual screen spanning every monitor
        mss.tools.to_png(shot.rgb, shot.size, output=str(path))
    return path


@nexus_tool(
    safety=Safety.SAFE,
    confirm="Take a screenshot?",
    triggers=[r"\b(screenshot|screen ?shot|screen ?cap(ture)?|capture (the |my )?screen|print ?screen|"
              r"snapshot of (the |my )?screen)\b"],
)
def take_screenshot():
    """Take a screenshot of the screen and save it."""
    path = capture()
    services.session.touch(path)
    return {"success": True, "message": tr("Screenshot saved."), "path": str(path), "data": {"path": str(path)}}
