"""Render assets/icon/nexus.ico (multi-resolution) from the procedural icon."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from app.ui.app_icon import write_ico  # noqa: E402


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    out = ROOT / "assets" / "icon" / "nexus.ico"
    write_ico(out)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0 if app else 1


if __name__ == "__main__":
    raise SystemExit(main())
