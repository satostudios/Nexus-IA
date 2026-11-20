"""Procedural NEXUS icon (window icon and the .ico used for the executable)."""

from __future__ import annotations

import random
import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap, QRadialGradient

SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QPointF(size / 2, size / 2)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0))
    p.drawRoundedRect(0, 0, size, size, size * 0.22, size * 0.22)
    r = size * 0.27
    g = QRadialGradient(QPointF(c.x() - r * 0.35, c.y() - r * 0.35), r * 1.3)
    g.setColorAt(0.0, QColor(255, 255, 255))
    g.setColorAt(0.65, QColor(170, 175, 190))
    g.setColorAt(1.0, QColor(40, 42, 50))
    p.setBrush(g)
    p.drawEllipse(c, r, r)
    if size >= 32:
        rnd = random.Random(3)
        for _ in range(size * 3):
            x, y = rnd.uniform(-r, r), rnd.uniform(-r, r)
            if x * x + y * y < r * r * 0.9:
                shade = rnd.randint(40, 120)
                p.setPen(QColor(0, 0, 0, shade))
                p.drawPoint(QPointF(c.x() + x, c.y() + y))
    p.setBrush(Qt.BrushStyle.NoBrush)
    pen = QPen(QColor(215, 228, 255, 230), max(1.0, size / 64))
    p.setPen(pen)
    p.save()
    p.translate(c)
    p.rotate(-28)
    p.drawEllipse(QPointF(0, 0), size * 0.43, size * 0.12)
    p.restore()
    p.end()
    return img


def window_icon() -> QIcon:
    icon = QIcon()
    for s in SIZES:
        icon.addPixmap(QPixmap.fromImage(render(s)))
    return icon


def write_ico(path: Path) -> None:
    """Write a multi-resolution .ico with PNG-compressed entries."""
    blobs = []
    for s in SIZES:
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        render(s).save(buf, "PNG")
        buf.close()
        blobs.append((s, bytes(data)))
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries, payload = b"", b""
    for s, blob in blobs:
        dim = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset + len(payload))
        payload += blob
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + payload)
