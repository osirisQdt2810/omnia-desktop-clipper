"""Global hotkey listener (a thin ``pynput`` wrapper; imported only at runtime).

``pynput`` is imported lazily inside :meth:`GlobalHotkey.start` so this module
stays import-safe without the dependency installed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class GlobalHotkey:
    """Registers a single global hotkey via pynput and invokes a callback.

    Note: the callback runs on pynput's listener thread, not the Qt main thread;
    the caller is responsible for marshalling any UI work back to the GUI thread.
    """

    def __init__(self, hotkey: str, callback: Callable[[], None]) -> None:
        """Initialise the hotkey.

        Args:
            hotkey: A ``pynput``-style hotkey string, e.g. ``<cmd>+<shift>+a``.
            callback: The zero-argument callable to invoke when the hotkey fires.
        """
        self._hotkey = hotkey
        self._callback = callback
        # pynput's GlobalHotKeys listener; typed loosely as the dep is lazy.
        self._listener: Any = None

    def start(self) -> None:
        """Start listening for the hotkey (idempotent)."""
        from pynput import keyboard

        if self._listener is not None:
            return
        self._listener = keyboard.GlobalHotKeys({self._hotkey: self._callback})
        self._listener.start()

    def stop(self) -> None:
        """Stop listening and release the OS hook (idempotent)."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
