"""Global mouse hook that fires when the user selects text (a thin ``pynput`` wrapper).

``pynput`` is imported lazily inside :meth:`GlobalMouseWatcher.start` so this module stays
import-safe without the dependency installed. Left-button press/release events are stamped with a
monotonic clock and fed to the pure :class:`~omnia_desktop_clipper.capture.gesture.SelectionGestureDetector`;
when a double-click or drag-select is recognised, the callback is invoked with the cursor position.

Like :class:`~omnia_desktop_clipper.hotkey.GlobalHotkey`, the callback runs on pynput's listener
thread — the caller marshals any UI work (showing the "+") back to the Qt main thread.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .capture.gesture import MouseGesture, SelectionGestureDetector


class GlobalMouseWatcher:
    """Watches global left-button gestures and calls back on a text-selection gesture."""

    def __init__(
        self,
        on_select: Callable[[int, int], None],
        *,
        detector: SelectionGestureDetector | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialise the watcher.

        Args:
            on_select: Called with the ``(x, y)`` screen position when a select gesture fires.
            detector: The gesture state machine (a default one is created if omitted).
            clock: Monotonic time source in seconds (injected for tests).
        """
        self._on_select = on_select
        self._detector = detector or SelectionGestureDetector()
        self._clock = clock
        self._listener: Any = None

    def start(self) -> None:
        """Start listening for global mouse gestures (idempotent)."""
        from pynput import mouse

        if self._listener is not None:
            return
        self._listener = mouse.Listener(on_click=self._on_click)
        self._listener.start()

    def stop(self) -> None:
        """Stop listening and release the OS hook (idempotent)."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def _on_click(self, x: int, y: int, button: Any, pressed: bool) -> None:
        """pynput callback (listener thread): feed the detector; call back on a select gesture."""
        from pynput import mouse

        if button is not mouse.Button.left:
            return
        now = self._clock()
        if pressed:
            self._detector.on_press(x, y, now)
            return
        if self._detector.on_release(x, y, now) is not MouseGesture.NONE:
            self._on_select(x, y)
