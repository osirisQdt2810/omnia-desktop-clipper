"""Per-OS helpers: the config directory and the mouse-cursor position.

Import-safe: PyQt6 is imported lazily inside :func:`cursor_pos`, so the pure
modules that only need :func:`config_dir` (e.g. :mod:`config`) can import this
module headless (no PyQt6 required).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

# The app-data folder name differs per platform to match each OS's conventions.
_MAC_APP_DIR = "OmniaDesktopClipper"
_WIN_APP_DIR = "OmniaDesktopClipper"
_LINUX_APP_DIR = "omnia-desktop-clipper"


def config_dir(
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the per-OS configuration directory for the clipper.

    Args:
        platform_name: A ``sys.platform`` override (for tests). Defaults to the
            running platform.
        environ: An environment mapping override (for tests). Defaults to
            ``os.environ``.
        home: A home-directory override (for tests). Defaults to ``Path.home()``.

    Returns:
        The directory (not created) where ``config.json`` should live.
    """
    platform_name = sys.platform if platform_name is None else platform_name
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else home

    if platform_name == "darwin":
        return home / "Library" / "Application Support" / _MAC_APP_DIR
    if platform_name.startswith("win"):
        appdata = environ.get("APPDATA")
        root = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return root / _WIN_APP_DIR
    # Linux / other POSIX: honour XDG_CONFIG_HOME, else ~/.config.
    xdg = environ.get("XDG_CONFIG_HOME")
    root = Path(xdg) if xdg else home / ".config"
    return root / _LINUX_APP_DIR


def cursor_pos() -> tuple[int, int]:
    """Return the global mouse-cursor position as ``(x, y)`` in screen pixels."""
    from PyQt6.QtGui import QCursor

    point = QCursor.pos()
    return point.x(), point.y()
