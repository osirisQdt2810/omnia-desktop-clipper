"""Tests for the injectable core of ``ClipboardCapture`` (no PyQt6/pynput needed).

Fakes model the real behaviour: emitting the copy shortcut writes the focused
app's selection into the clipboard. We assert copy is synthesised, the selection
is returned, and the original clipboard is restored.
"""

from __future__ import annotations

from omnia_desktop_clipper.capture.clipboard import (
    ClipboardAccessor,
    ClipboardCapture,
    CopyEmitter,
)


def _no_sleep(_seconds: float) -> None:
    """A no-op sleep so tests don't actually wait."""


class _FakeClipboard(ClipboardAccessor):
    """An in-memory clipboard modelling text AND opaque non-text content (image/files).

    ``mime`` stands in for non-text content: when it is set, ``get_text()`` returns ``""`` (as a
    real clipboard holding an image would), and writing text clears it. ``snapshot``/``restore``
    round-trip the FULL state so a capture can be verified non-destructive for non-text content.
    """

    def __init__(self, text: str = "", *, mime: object | None = None) -> None:
        self._text = text
        self._mime = mime
        self.history: list[str] = [text]

    def get_text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text
        self._mime = None  # writing text replaces any non-text content (like a real clipboard)
        self.history.append(text)

    def snapshot(self) -> object:
        return (self._text, self._mime)

    def restore(self, snapshot: object) -> None:
        self._text, self._mime = snapshot  # type: ignore[misc]


class _FakeCopyEmitter(CopyEmitter):
    """Simulates Cmd/Ctrl+C by writing a canned selection into the clipboard."""

    def __init__(self, clipboard: _FakeClipboard, selection: str) -> None:
        self._clipboard = clipboard
        self._selection = selection
        self.emit_count = 0

    def emit(self) -> None:
        self.emit_count += 1
        self._clipboard.set_text(self._selection)


class _NoopEmitter(CopyEmitter):
    """Simulates copy with nothing selected (the clipboard is left cleared)."""

    def __init__(self) -> None:
        self.emit_count = 0

    def emit(self) -> None:
        self.emit_count += 1


class TestClipboardCapture:
    """The clipboard round-trip capture is non-destructive and detects empties."""

    def test_returns_selection_and_restores_clipboard(self) -> None:
        clipboard = _FakeClipboard("ORIGINAL")
        emitter = _FakeCopyEmitter(clipboard, "selected text")
        capture = ClipboardCapture(clipboard, emitter, sleep=_no_sleep)

        result = capture.capture()

        assert result == "selected text"
        assert emitter.emit_count == 1
        assert clipboard.get_text() == "ORIGINAL"

    def test_returns_none_when_nothing_selected(self) -> None:
        clipboard = _FakeClipboard("ORIGINAL")
        emitter = _NoopEmitter()
        capture = ClipboardCapture(clipboard, emitter, sleep=_no_sleep)

        result = capture.capture()

        assert result is None
        assert emitter.emit_count == 1
        assert clipboard.get_text() == "ORIGINAL"

    def test_strips_surrounding_whitespace(self) -> None:
        clipboard = _FakeClipboard("ORIGINAL")
        emitter = _FakeCopyEmitter(clipboard, "  spaced  \n")
        capture = ClipboardCapture(clipboard, emitter, sleep=_no_sleep)

        assert capture.capture() == "spaced"
        assert clipboard.get_text() == "ORIGINAL"

    def test_preserves_non_text_clipboard_content(self) -> None:
        # The clipboard holds an image (no text). Capturing a selection must restore the image,
        # not wipe it — the snapshot/restore round-trip carries non-text content through.
        clipboard = _FakeClipboard("", mime="IMAGE-DATA")
        emitter = _FakeCopyEmitter(clipboard, "selected text")
        capture = ClipboardCapture(clipboard, emitter, sleep=_no_sleep)

        assert capture.capture() == "selected text"
        assert clipboard.snapshot() == ("", "IMAGE-DATA")  # original non-text content restored
