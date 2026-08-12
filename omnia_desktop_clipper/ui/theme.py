"""Colour tokens + stylesheet for the lookup UI, in light and dark macOS appearance.

Kept as plain data (a :class:`Palette` of tokens) plus one :func:`stylesheet` builder, so the
widgets never hardcode a colour and the whole look changes from one place. The appearance is
detected from Qt's own palette rather than a macOS API, which keeps this file import-safe and
testable, and automatically follows the system setting Qt already resolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# The brand blue shared with the "+" overlay and the web clipper.
ACCENT = "#2f81f7"


@dataclass(frozen=True)
class Palette:
    """The colour tokens the lookup UI paints with."""

    bg: str
    surface: str
    border: str
    text: str
    muted: str
    accent: str
    chip_bg: str
    shadow: str
    # The surface colour as a plain hex, so translucent variants can be mixed from it.
    surface_rgb: str


LIGHT = Palette(
    bg="#ffffff",
    surface="#f6f7f9",
    border="#dfe3e8",
    text="#16191d",
    muted="#6b727c",
    accent=ACCENT,
    chip_bg="#eef2f7",
    shadow="rgba(0, 0, 0, 60)",
    surface_rgb="#f6f7f9",
)

DARK = Palette(
    bg="#1e2127",
    surface="#262a31",
    border="#363b44",
    text="#e8eaed",
    muted="#9aa1ac",
    accent="#5aa0ff",
    chip_bg="#2e333c",
    shadow="rgba(0, 0, 0, 140)",
    surface_rgb="#262a31",
)

# Card-state accent colours (new / learning / review / relearning), used for the state pill.
STATE_COLORS = {
    "new": "#3b82f6",
    "learning": "#f59e0b",
    "relearning": "#ef4444",
    "review": "#22a06b",
}


def is_dark(app: Optional[Any] = None) -> bool:
    """Whether the app is currently rendering in a dark appearance.

    Derived from Qt's resolved window colour (lightness < 50%) rather than a platform API, so it
    works on every OS and needs no pyobjc.

    Args:
        app: A ``QApplication`` (defaults to the running instance). ``None``/no instance -> light.
    """
    try:
        from PyQt6.QtWidgets import QApplication

        instance = app or QApplication.instance()
        if instance is None:
            return False
        return instance.palette().window().color().lightness() < 128
    except Exception:
        return False


def palette(app: Optional[Any] = None) -> Palette:
    """Return the palette matching the current appearance."""
    return DARK if is_dark(app) else LIGHT


def state_color(state: str) -> str:
    """The accent colour for a card state name (unknown states fall back to review green)."""
    return STATE_COLORS.get(state, STATE_COLORS["review"])


def rgba(hex_color: str, alpha: float) -> str:
    """Public alias of :func:`_rgba`, for widgets that build a gradient inline."""
    return _rgba(hex_color, alpha)


def _rgba(hex_color: str, alpha: float) -> str:
    """``#rrggbb`` -> ``rgba(r, g, b, a)`` for translucent QSS fills."""
    value = hex_color.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha:.2f})"


def stylesheet(colors: Palette) -> str:
    """Build the lookup panel's QSS from ``colors``.

    One sheet for the whole panel: object names (``#lookupRoot``, ``#lookupTitle``, …) keep the
    widget code free of inline styling.

    The three bands are styled to look DIFFERENT on purpose. They previously shared the same
    pill-row shape, so the panel read as two identical stripes and the eye had no hierarchy:

    * the header is a soft gradient wash carrying the word and its state;
    * the switcher is a segmented control — connected, filled-when-active;
    * each field is a translucent card with a thin accent spine, lifting on hover.
    """
    accent_soft = _rgba(colors.accent, 0.10)
    accent_edge = _rgba(colors.accent, 0.22)
    field_bg = _rgba(colors.surface_rgb, 0.55)
    field_bg_hover = _rgba(colors.surface_rgb, 0.95)
    return f"""
    #headerBand {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
            stop:0 {accent_soft}, stop:1 rgba(0, 0, 0, 0));
        border: 1px solid {accent_edge};
        border-radius: 10px;
    }}
    #fieldCard {{
        background: {field_bg};
        border: 1px solid {colors.border};
        border-left: 3px solid {accent_edge};
        border-radius: 8px;
    }}
    #fieldCard:hover {{
        background: {field_bg_hover};
        border-left: 3px solid {colors.accent};
    }}
    #segment {{
        background: transparent;
        color: {colors.muted};
        border: 1px solid {colors.border};
        border-radius: 8px;
        padding: 4px 12px;
        font-size: 12px;
    }}
    #segment:hover {{ color: {colors.text}; border-color: {colors.accent}; }}
    #segmentActive {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {colors.accent}, stop:1 {_rgba(colors.accent, 0.82)});
        color: white;
        border: 1px solid {colors.accent};
        border-radius: 8px;
        padding: 4px 12px;
        font-size: 12px;
        font-weight: 600;
    }}
    #metaLine {{ font-size: 11px; color: {colors.muted}; }}
    #lookupRoot {{
        background: {colors.bg};
        border: 1px solid {colors.border};
        border-radius: 12px;
    }}
    QLabel {{ color: {colors.text}; }}
    #lookupTitle {{
        font-size: 20px;
        font-weight: 600;
        color: {colors.text};
    }}
    #lookupSubtitle, #fieldName, #lookupHint {{
        font-size: 11px;
        color: {colors.muted};
    }}
    #fieldName {{
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.4px;
    }}
    #fieldText {{ font-size: 13px; color: {colors.text}; }}
    #chip {{
        background: {colors.chip_bg};
        color: {colors.muted};
        border-radius: 9px;
        padding: 2px 8px;
        font-size: 11px;
    }}
    #separator {{ background: {colors.border}; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{
        background: transparent; width: 8px; margin: 4px 2px 4px 0;
    }}
    QScrollBar::handle:vertical {{
        background: {colors.border}; border-radius: 4px; min-height: 24px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QPushButton#lookupAction {{
        background: {colors.surface};
        color: {colors.text};
        border: 1px solid {colors.border};
        border-radius: 7px;
        padding: 5px 12px;
        font-size: 12px;
    }}
    QPushButton#lookupAction:hover {{ border-color: {colors.accent}; color: {colors.accent}; }}
    QLineEdit#lookupSearch {{
        background: {colors.surface};
        color: {colors.text};
        border: 1px solid {colors.border};
        border-radius: 8px;
        padding: 6px 10px;
        font-size: 13px;
    }}
    QLineEdit#lookupSearch:focus {{ border-color: {colors.accent}; }}
    """
