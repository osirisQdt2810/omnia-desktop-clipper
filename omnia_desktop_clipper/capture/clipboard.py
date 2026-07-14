"""Clipboard-based selection capture.

The core algorithm (:class:`ClipboardCapture`) is dependency-injected: it takes a
:class:`ClipboardAccessor` and a :class:`CopyEmitter`, so it unit-tests headless
with fakes. The concrete Qt/pynput backends live at the bottom and import their
heavy deps lazily (inside methods), so importing this module never requires
PyQt6/pynput.
"""

from __future__ import annotations

import abc
import time
from collections.abc import Callable

from .base import SelectionCapture

# How long to wait after synthesising copy before reading the clipboard back.
_DEFAULT_SETTLE_SECONDS = 0.15


class ClipboardAccessor(abc.ABC):
    """Reads and writes the system clipboard's plain text, and snapshots its full content."""

    @abc.abstractmethod
    def get_text(self) -> str:
        """Return the clipboard's current plain text (``""`` if empty)."""

    @abc.abstractmethod
    def set_text(self, text: str) -> None:
        """Replace the clipboard's plain text with ``text``."""

    @abc.abstractmethod
    def snapshot(self) -> object:
        """Return an opaque snapshot of the FULL clipboard (text, image, files, …).

        Paired with :meth:`restore` so a capture can preserve non-text content (an image or
        copied files) that a plain-text save/restore would silently wipe.
        """

    @abc.abstractmethod
    def restore(self, snapshot: object) -> None:
        """Restore a snapshot previously returned by :meth:`snapshot`."""


class CopyEmitter(abc.ABC):
    """Synthesises the platform copy shortcut (Cmd+C on macOS, else Ctrl+C)."""

    @abc.abstractmethod
    def emit(self) -> None:
        """Send the copy keystroke to the focused application."""


class ClipboardCapture(SelectionCapture):
    """Capture the selection by synthesising copy and diffing the clipboard.

    It snapshots the current clipboard (its FULL content — text, image, or files), clears the
    text, synthesises the copy shortcut, reads whatever the focused app copied, then restores the
    snapshot so the capture is non-destructive even when the clipboard held non-text content.
    Clearing first lets it distinguish "the app copied the selection" from "nothing was selected"
    (clipboard stays empty).
    """

    def __init__(
        self,
        clipboard: ClipboardAccessor,
        copy_emitter: CopyEmitter,
        *,
        settle_seconds: float = _DEFAULT_SETTLE_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Initialise the capture.

        Args:
            clipboard: The clipboard accessor to read/write.
            copy_emitter: The copy-keystroke emitter.
            settle_seconds: How long to wait for the app to write the clipboard.
            sleep: The sleep function (inject a no-op in tests).
        """
        self._clipboard = clipboard
        self._copy_emitter = copy_emitter
        self._settle_seconds = settle_seconds
        self._sleep = sleep

    def capture(self) -> str | None:
        """Return the selected text (stripped), or ``None`` if nothing captured."""
        # Snapshot the FULL clipboard (not just its text) so restoring can't wipe a copied image
        # or file list — a plain get_text()/set_text() round-trip would replace those with "".
        snapshot = self._clipboard.snapshot()
        try:
            self._clipboard.set_text("")
            self._copy_emitter.emit()
            self._sleep(self._settle_seconds)
            captured = self._clipboard.get_text()
        finally:
            self._clipboard.restore(snapshot)
        captured = captured.strip()
        return captured or None


class QtClipboard(ClipboardAccessor):
    """Qt-backed clipboard accessor (requires a running ``QApplication``)."""

    def __init__(self) -> None:
        from PyQt6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard is None:  # pragma: no cover - needs a live QApplication
            raise RuntimeError("No QApplication clipboard is available.")
        self._clipboard = clipboard

    def get_text(self) -> str:
        return self._clipboard.text()

    def set_text(self, text: str) -> None:
        self._clipboard.setText(text)

    def snapshot(self) -> object:  # pragma: no cover - needs a live QApplication
        # Deep-copy every format of the current clipboard so a later clear/copy can't mutate our
        # saved copy; this preserves non-text content (images, files) across the capture.
        from PyQt6.QtCore import QMimeData

        source = self._clipboard.mimeData()
        data = QMimeData()
        if source is not None:
            for fmt in source.formats():
                data.setData(fmt, source.data(fmt))
        return data

    def restore(self, snapshot: object) -> None:  # pragma: no cover - needs a live QApplication
        from PyQt6.QtCore import QMimeData

        if isinstance(snapshot, QMimeData):
            self._clipboard.setMimeData(snapshot)


class PynputCopyEmitter(CopyEmitter):
    """pynput-backed emitter of the Cmd+C (macOS) / Ctrl+C copy shortcut."""

    def __init__(self, use_command_key: bool) -> None:
        from pynput.keyboard import Controller, Key

        self._controller = Controller()
        self._modifier = Key.cmd if use_command_key else Key.ctrl

    def emit(self) -> None:
        with self._controller.pressed(self._modifier):
            self._controller.press("c")
            self._controller.release("c")


def build_clipboard_capture(*, use_command_key: bool) -> ClipboardCapture:
    """Wire the concrete Qt clipboard + pynput emitter (runtime only).

    Args:
        use_command_key: Use Cmd (macOS) instead of Ctrl for the copy shortcut.

    Returns:
        A ready-to-use :class:`ClipboardCapture`.
    """
    return ClipboardCapture(QtClipboard(), PynputCopyEmitter(use_command_key))
