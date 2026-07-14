"""Pure detection of a text-selection mouse gesture (double-click or drag-select).

No PyQt6/pynput imports — a small state machine over raw mouse press/release events, so it
unit-tests headless. The runtime mouse hook (``mouse_watcher.GlobalMouseWatcher``) stamps each
event with a monotonic time and feeds it here; when a gesture that *likely* selected text is
recognised, the app shows the floating "+" near the cursor.

Two gestures count as "the user probably selected text":

* **Double-click** — two clicks (press≈release, no drag) close together in time and position.
* **Drag-select** — a press then a release far enough apart (the mouse moved while held).

Timestamps and thresholds are injected/parameterised (never read here), keeping the detector
clock-agnostic and deterministic in tests.
"""

from __future__ import annotations

import math
from enum import Enum, auto
from typing import Optional


class MouseGesture(Enum):
    """The gesture recognised from a completed press→release (``NONE`` = not a selection)."""

    NONE = auto()
    DOUBLE_CLICK = auto()
    DRAG = auto()


class SelectionGestureDetector:
    """Recognises a double-click or drag-select from a stream of press/release events.

    Feed every left-button press to :meth:`on_press` and every release to :meth:`on_release`;
    the release call returns the recognised :class:`MouseGesture` (or ``NONE``). Coordinates are
    screen pixels; ``t`` is a monotonic timestamp in seconds (injected by the caller).
    """

    def __init__(
        self,
        *,
        double_click_seconds: float = 0.4,
        double_click_radius: float = 6.0,
        drag_min_distance: float = 8.0,
    ) -> None:
        """Initialise the detector.

        Args:
            double_click_seconds: Max gap between the two clicks of a double-click.
            double_click_radius: Max pixel distance between the two clicks' positions.
            drag_min_distance: Min press→release pixel distance to count as a drag-select.
        """
        self._double_click_seconds = double_click_seconds
        self._double_click_radius = double_click_radius
        self._drag_min_distance = drag_min_distance
        self._press: Optional[tuple[float, float, float]] = None
        self._last_click: Optional[tuple[float, float, float]] = None

    def on_press(self, x: float, y: float, t: float) -> None:
        """Record a button press at ``(x, y)`` stamped ``t``."""
        self._press = (x, y, t)

    def on_release(self, x: float, y: float, t: float) -> MouseGesture:
        """Complete a press→release; return the recognised gesture (or ``NONE``)."""
        press = self._press
        self._press = None
        if press is None:
            return MouseGesture.NONE
        px, py, _pt = press

        # Moved while held → a drag-select. (Resets any pending double-click.)
        if math.hypot(x - px, y - py) >= self._drag_min_distance:
            self._last_click = None
            return MouseGesture.DRAG

        # A stationary click. Is it the second of a double-click?
        prev = self._last_click
        self._last_click = (x, y, t)
        if prev is not None:
            ox, oy, ot = prev
            within_time = (t - ot) <= self._double_click_seconds
            within_pos = math.hypot(x - ox, y - oy) <= self._double_click_radius
            if within_time and within_pos:
                self._last_click = None  # consume, so a triple-click doesn't double-fire
                return MouseGesture.DOUBLE_CLICK
        return MouseGesture.NONE
