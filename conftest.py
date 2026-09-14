"""Make ``omnia_desktop_clipper`` importable when pytest runs from the repo root, and give the
widget tests one way to sit out a headless Linux leg.

The package's parent directory (this folder) is not on ``sys.path`` by default
when the suite is invoked from the repository root, so add it here.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_PACKAGE_ROOT = Path(__file__).resolve().parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))


def requires_qt_widgets() -> None:
    """Make a ``QApplication`` safe to build here, or skip the calling module.

    Two separate hazards, and only one of them is an exception.

    **The import.** Not ``pytest.importorskip``: that skips on **ModuleNotFoundError** and lets
    every other ``ImportError`` through, and what a bare Linux runner raises is not a missing
    module — PyQt6 is installed and its extension fails to load for want of ``libEGL.so.1``. So
    ``importorskip("PyQt6.QtWidgets")`` raises at COLLECTION time and fails the whole suite,
    which is exactly what it looks like it prevents.

    **The platform plugin.** Constructing a ``QApplication`` where ``QtWidgets`` imports but
    there is no display — a dev box with mesa, or this runner the day someone installs
    ``libegl1`` — calls ``qFatal``, which is ``SIGABRT``. Python cannot catch that: the whole
    pytest process dies and takes every other test with it. No try/except can guard it, so the
    only fix is to never ask for a display: ``offscreen`` is set here, before anything imports
    Qt, and it is what these tests want anyway. Nothing on screen is being looked at.

    Note the rule this sits beside rather than replaces: the geometry that actually matters
    (:mod:`omnia_desktop_clipper.ui.pill_geometry`) is pure and is checked on every platform on
    every run. What is skipped where Qt will not load is the thin widget layer over it.
    """
    # setdefault, so a run that deliberately asks for a real platform still gets one.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        import PyQt6.QtWidgets  # noqa: F401
    except ImportError as exc:  # pragma: no cover - platform-dependent
        pytest.skip(f"PyQt6.QtWidgets is unusable here: {exc}", allow_module_level=True)
