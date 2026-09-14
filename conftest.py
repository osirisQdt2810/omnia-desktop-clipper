"""Make ``omnia_desktop_clipper`` importable when pytest runs from the repo root, and give the
widget tests one way to sit out a headless Linux leg.

The package's parent directory (this folder) is not on ``sys.path`` by default
when the suite is invoked from the repository root, so add it here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PACKAGE_ROOT = Path(__file__).resolve().parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))


def requires_qt_widgets() -> None:
    """Skip the calling module when ``QtWidgets`` cannot be imported.

    Not ``pytest.importorskip``. That skips on **ModuleNotFoundError** and lets every other
    ``ImportError`` through, and the one we get here is not a missing module: PyQt6 is installed,
    and its extension fails to load because the Linux runner has no ``libEGL.so.1``. So
    ``importorskip("PyQt6.QtWidgets")`` raises at COLLECTION time, which fails the whole suite
    rather than skipping one file — exactly what it looks like it prevents.

    macOS and Windows run these for real; only the Linux leg sits them out. Installing ``libegl1``
    on that runner and setting ``QT_QPA_PLATFORM=offscreen`` would restore the coverage, but that
    is a workflow change and does not belong to whichever PR happens to add a widget test.

    Written here rather than copied into each test module, because the reason is the whole value
    and two copies of it will drift.
    """
    try:
        import PyQt6.QtWidgets  # noqa: F401
    except ImportError as exc:  # pragma: no cover - platform-dependent
        pytest.skip(f"PyQt6.QtWidgets is unusable here: {exc}", allow_module_level=True)
