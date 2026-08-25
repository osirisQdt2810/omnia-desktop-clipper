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
        self._in_flight = False

    def capture(self) -> str | None:
        """Return the selected text (stripped), or ``None`` if nothing captured.

        RE-ENTRANT CALLS ARE REFUSED, and that is a correctness guard rather than an
        optimisation. The settle is spent pumping the host's event loop (see
        :class:`QtEventLoopSettle`), and a pump delivers queued cross-thread signals — which is
        how every gesture and hotkey in this app arrives. Without this flag a second gesture
        during the settle re-enters here, snapshots the ALREADY-CLEARED clipboard, and restores
        that empty snapshot on its way out; the outer call then reads "" and reports nothing
        selected, so the gesture the user actually made produces no "+".

        Refusing is the honest answer: between ``set_text("")`` and ``restore`` the clipboard
        does not hold the user's data, so there is nothing a nested call could truthfully
        return.
        """
        if self._in_flight:
            return None
        self._in_flight = True
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
            self._in_flight = False
        captured = captured.strip()
        return captured or None

    @property
    def in_flight(self) -> bool:
        """Whether a capture is running right now.

        Public because the GUI needs it too: this class can refuse a nested capture, but it
        cannot stop a pumped signal from opening a MODAL dialog underneath the ``finally``
        above — and a modal runs its own event loop, so the user can sit there copying things
        while this call's ``restore`` waits to overwrite the clipboard with what was there
        before their gesture.
        """
        return self._in_flight


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

    def restore(
        self, snapshot: object
    ) -> None:  # pragma: no cover - needs a live QApplication
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


class QtEventLoopSettle:
    """The settle wait, spent PUMPING Qt's event loop instead of blocking it.

    This exists because a plain ``time.sleep`` makes the capture return nothing on Windows, and
    it is not obvious why. Qt learns that another application put something on the clipboard by
    processing a native window message (``WM_CLIPBOARDUPDATE`` and the delayed-rendering
    handshake behind it). :meth:`ClipboardCapture.capture` runs on the Qt main thread, so a
    blocking sleep there means those messages are never processed during the one window that
    matters: ``get_text()`` then reports the value we ourselves wrote a moment earlier — the
    empty string used to tell "nothing was selected" apart from a real copy — and every capture
    comes back ``None``. No "+" ever appears, and the hotkey capture silently adds nothing.

    macOS never showed this. ``NSPasteboard`` is polled on access (Qt compares its
    ``changeCount``), so the answer is correct with no event processing at all, which is why the
    blocking sleep survived review and shipped.

    Pumping is confined to this class so the algorithm in :class:`ClipboardCapture` stays free of
    Qt and keeps unit-testing headless with an injected no-op sleep.
    """

    def __init__(self, *, slice_seconds: float = 0.01) -> None:
        """Initialise the settle.

        Args:
            slice_seconds: How long to sleep between pumps. Small enough that the clipboard
                message is picked up promptly, large enough not to spin a core.
        """
        self._slice = slice_seconds

    def __call__(self, seconds: float) -> None:
        """Wait ``seconds``, processing pending Qt events throughout.

        User input is excluded from the pump because nothing here needs it: the clipboard
        arrives as a system message, so the exclusion costs the capture nothing (measured on
        Windows, not assumed) while keeping stray clicks off our own widgets mid-capture.

        IT IS NOT THE RE-ENTRANCY DEFENCE, and an earlier version of this docstring wrongly
        said it was. ``ExcludeUserInputEvents`` filters Qt's own input queue; every gesture and
        hotkey in this app arrives on a pynput listener thread and crosses to the GUI thread as
        a QUEUED SIGNAL, which ``processEvents`` delivers whatever the flag says. Re-entrancy is
        refused by :meth:`ClipboardCapture.capture` and by the app's own busy check, both of
        which work regardless of how the event got here.
        """
        from PyQt6.QtCore import QEventLoop
        from PyQt6.QtWidgets import QApplication

        end = time.monotonic() + seconds
        while True:
            QApplication.processEvents(
                QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
            )
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(self._slice, remaining))


def build_clipboard_capture(*, use_command_key: bool) -> ClipboardCapture:
    """Wire the concrete Qt clipboard + pynput emitter (runtime only).

    The settle is :class:`QtEventLoopSettle`, not ``time.sleep`` — see that class for why a
    blocking wait returns an empty capture on Windows.

    Args:
        use_command_key: Use Cmd (macOS) instead of Ctrl for the copy shortcut.

    Returns:
        A ready-to-use :class:`ClipboardCapture`.
    """
    return ClipboardCapture(
        QtClipboard(),
        PynputCopyEmitter(use_command_key),
        sleep=QtEventLoopSettle(),
    )
