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
    """An in-memory clipboard that records every value it ever held."""

    def __init__(self, text: str = "") -> None:
        self._text = text
        self.history: list[str] = [text]

    def get_text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text
        self.history.append(text)


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
