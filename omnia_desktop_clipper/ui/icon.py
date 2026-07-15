"""The Omnia clipper app icon (a blue rounded square with a white "+").

Painted in Qt so the tray icon, the running dock icon, and the packaged ``.app`` icon all match
the web clipper's ``icon.svg`` (same ``#2d6cdf`` mark) — no image asset or SVG renderer needed.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap

_BRAND_BLUE = "#2d6cdf"  # the Omnia clipper mark blue (matches omnia-web-clipper icon.svg)


def plus_pixmap(size: int = 128) -> QPixmap:
    """Render the blue-rounded-square + white-"+" mark at ``size`` px (transparent background)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # Rounded blue square (radius ~20% of the side, matching the web icon's rx=26/128).
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(_BRAND_BLUE)))
    radius = size * 0.205
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    # White "+" with rounded caps (stroke ~11% of the side, spanning the middle ~53%).
    pen = QPen(QColor("#ffffff"))
    pen.setWidthF(size * 0.11)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    lo, hi, mid = size * 0.235, size * 0.765, size / 2
    painter.drawLine(QPointF(mid, lo), QPointF(mid, hi))  # vertical
    painter.drawLine(QPointF(lo, mid), QPointF(hi, mid))  # horizontal
    painter.end()
    return pixmap


def plus_icon() -> QIcon:
    """Return the Omnia clipper app icon (the blue "+" mark)."""
    icon = QIcon()
    for size in (16, 32, 64, 128, 256, 512):
        icon.addPixmap(plus_pixmap(size))
    return icon
