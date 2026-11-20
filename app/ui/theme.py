"""Visual theme: black, white, greys; accents only where they carry meaning."""

from __future__ import annotations

import logging

from PySide6.QtGui import QColor, QFont, QFontDatabase

from app import paths

log = logging.getLogger("nexus.theme")

BACKGROUND = "#000000"
TEXT = "#E8E8EA"
TEXT_DIM = "#8A8A90"
TEXT_FAINT = "#55555B"
LINE = "rgba(255,255,255,0.12)"
LINE_FOCUS = "rgba(255,255,255,0.32)"
ACCENT = "#9FD8FF"        # subtle cyan (listening)
ACCENT_VIOLET = "#B7A8FF"  # processing
WARN = "#E7C9A8"          # muted amber, never alarm red

SANS = "Inter"
MONO = "IBM Plex Mono"


def load_fonts() -> None:
    """Register bundled fonts; fall back to Segoe UI / Consolas if missing."""
    global SANS, MONO
    families: set[str] = set()
    for name in ("Inter-Variable.ttf", "IBMPlexMono-Regular.ttf", "IBMPlexMono-Medium.ttf"):
        path = paths.asset("fonts", name)
        if path.exists():
            fid = QFontDatabase.addApplicationFont(str(path))
            families.update(QFontDatabase.applicationFontFamilies(fid))
    if "Inter" not in families:
        SANS = "Segoe UI Variable Text" if "Segoe UI Variable Text" in QFontDatabase.families() else "Segoe UI"
        log.info("Inter not available; using %s", SANS)
    if "IBM Plex Mono" not in families:
        MONO = "Consolas"


def font(size: float, weight: QFont.Weight = QFont.Weight.Normal, mono: bool = False,
         spacing: float = 0.0) -> QFont:
    f = QFont(MONO if mono else SANS)
    f.setPointSizeF(size)
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


def qcolor(value: str, alpha: float = 1.0) -> QColor:
    c = QColor(value)
    c.setAlphaF(alpha)
    return c


def stylesheet() -> str:
    return f"""
    QWidget {{ color: {TEXT}; font-family: "{SANS}"; }}
    QToolTip {{ background: #111114; color: {TEXT}; border: 1px solid rgba(255,255,255,0.15); padding: 4px 6px; }}
    QScrollBar:vertical {{ background: transparent; width: 6px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.18); border-radius: 3px; min-height: 24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    """
