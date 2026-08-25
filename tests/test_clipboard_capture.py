"""Tests for the injectable core of ``ClipboardCapture`` (no PyQt6/pynput needed).

Fakes model the real behaviour: emitting the copy shortcut writes the focused
app's selection into the clipboard. We assert copy is synthesised, the selection
is returned, and the original clipboard is restored.
"""

from __future__ import annotations

import pytest

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
        self._mime = (
            None  # writing text replaces any non-text content (like a real clipboard)
        )
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
        assert clipboard.snapshot() == (
            "",
            "IMAGE-DATA",
        )  # original non-text content restored


class _WindowsLikeClipboard(ClipboardAccessor):
    """A clipboard with WINDOWS visibility semantics: an external copy lands only after a pump.

    This is the whole bug in a fake. On Windows, Qt does not read the system clipboard on
    access — it is told about another application's copy by a native window message, so the new
    value becomes visible to ``get_text()`` only once the app processes events. ``_pending`` is
    what the source app has copied; ``pump()`` is the event processing that reveals it.

    macOS has no such gap (``NSPasteboard`` is polled on access), which is exactly why the
    blocking-sleep version passed every test and every manual check on a Mac.
    """

    def __init__(self, text: str = "") -> None:
        self._visible = text
        self._pending: str | None = None
        self.pumps = 0

    def get_text(self) -> str:
        return self._visible

    def set_text(self, text: str) -> None:
        # Our OWN writes are immediate; only another app's copy needs the message pump.
        self._visible = text
        self._pending = None

    def copy_from_focused_app(self, selection: str) -> None:
        self._pending = selection

    def pump(self) -> None:
        self.pumps += 1
        if self._pending is not None:
            self._visible = self._pending
            self._pending = None

    def snapshot(self) -> object:
        return self._visible

    def restore(self, snapshot: object) -> None:
        self._visible = snapshot  # type: ignore[assignment]


class _WindowsCopyEmitter(CopyEmitter):
    """Ctrl+C on Windows: the source app copies, but Qt cannot see it yet."""

    def __init__(self, clipboard: _WindowsLikeClipboard, selection: str) -> None:
        self._clipboard = clipboard
        self._selection = selection

    def emit(self) -> None:
        self._clipboard.copy_from_focused_app(self._selection)


class TestTheSettleMustPumpNotBlock:
    """The Windows regression: a blocking settle makes every capture come back empty.

    Symptom in the shipped app: double-click showed no "+" and the capture hotkey added
    nothing, on Windows only, with no error anywhere — ``capture()`` returned ``None`` and
    every caller treats that as "nothing was selected".
    """

    def test_a_blocking_settle_loses_the_selection(self) -> None:
        """The bug, pinned. If this ever passes, the fix has been undone."""
        clipboard = _WindowsLikeClipboard("ORIGINAL")
        capture = ClipboardCapture(
            clipboard,
            _WindowsCopyEmitter(clipboard, "selected text"),
            sleep=_no_sleep,  # a plain time.sleep pumps nothing
        )

        assert capture.capture() is None
        assert clipboard.pumps == 0

    def test_a_pumping_settle_gets_it(self) -> None:
        clipboard = _WindowsLikeClipboard("ORIGINAL")
        capture = ClipboardCapture(
            clipboard,
            _WindowsCopyEmitter(clipboard, "selected text"),
            sleep=lambda _seconds: clipboard.pump(),
        )

        assert capture.capture() == "selected text"
        assert clipboard.pumps == 1

    def test_the_original_clipboard_is_still_restored(self) -> None:
        """Pumping must not cost the non-destructiveness the capture already guaranteed."""
        clipboard = _WindowsLikeClipboard("ORIGINAL")
        capture = ClipboardCapture(
            clipboard,
            _WindowsCopyEmitter(clipboard, "selected text"),
            sleep=lambda _seconds: clipboard.pump(),
        )

        capture.capture()

        assert clipboard.get_text() == "ORIGINAL"


class TestQtEventLoopSettle:
    """The concrete settle, exercised without a real Qt (the suite installs no PyQt6)."""

    @staticmethod
    def _with_fake_qt(monkeypatch):
        """Install a fake ``PyQt6.QtWidgets.QApplication`` that counts ``processEvents``."""
        import sys
        import types

        calls = {"n": 0}

        class _FakeQApplication:
            @staticmethod
            def processEvents(flags=None) -> None:  # noqa: N802 - Qt's own spelling
                calls["n"] += 1
                calls.setdefault("flags", []).append(flags)

        class _ProcessEventsFlag:
            ExcludeUserInputEvents = "exclude-user-input"

        class _FakeQEventLoop:
            ProcessEventsFlag = _ProcessEventsFlag

        widgets = types.ModuleType("PyQt6.QtWidgets")
        widgets.QApplication = _FakeQApplication  # type: ignore[attr-defined]
        core = types.ModuleType("PyQt6.QtCore")
        core.QEventLoop = _FakeQEventLoop  # type: ignore[attr-defined]
        package = types.ModuleType("PyQt6")
        package.QtWidgets = widgets  # type: ignore[attr-defined]
        package.QtCore = core  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "PyQt6", package)
        monkeypatch.setitem(sys.modules, "PyQt6.QtWidgets", widgets)
        monkeypatch.setitem(sys.modules, "PyQt6.QtCore", core)
        return calls

    def test_it_pumps_at_least_once_even_for_a_zero_wait(self, monkeypatch) -> None:
        """A zero settle must still process events — otherwise the copy is never seen.

        The loop pumps BEFORE checking the clock for exactly this reason; a
        check-first loop would return without pumping and reintroduce the bug for any
        caller that shortens the settle.
        """
        from omnia_desktop_clipper.capture.clipboard import QtEventLoopSettle

        calls = self._with_fake_qt(monkeypatch)
        QtEventLoopSettle()(0.0)

        assert calls["n"] >= 1

    def test_it_pumps_repeatedly_across_the_wait(self, monkeypatch) -> None:
        from omnia_desktop_clipper.capture.clipboard import QtEventLoopSettle

        calls = self._with_fake_qt(monkeypatch)
        QtEventLoopSettle(slice_seconds=0.001)(0.05)

        assert calls["n"] > 1

    def test_it_waits_about_as_long_as_asked(self, monkeypatch) -> None:
        """Pumping replaces the blocking wait; it must not shorten it.

        The settle exists to give the focused app time to write the clipboard. A pump loop
        that returned early would trade one empty-capture bug for another.
        """
        import time

        from omnia_desktop_clipper.capture.clipboard import QtEventLoopSettle

        self._with_fake_qt(monkeypatch)
        started = time.monotonic()
        QtEventLoopSettle(slice_seconds=0.005)(0.08)

        assert time.monotonic() - started >= 0.07

    def test_it_excludes_user_input_from_the_pump(self, monkeypatch) -> None:
        """Kept because nothing here needs input delivered, not as a re-entrancy defence.

        It is NOT one: every gesture and hotkey arrives as a queued cross-thread signal, which
        ``processEvents`` delivers whatever this flag says. Nesting is refused by
        :class:`TestCaptureRefusesToNest` instead.
        """
        from omnia_desktop_clipper.capture.clipboard import QtEventLoopSettle

        calls = self._with_fake_qt(monkeypatch)
        QtEventLoopSettle(slice_seconds=0.001)(0.02)

        assert calls["flags"], "processEvents was called with no flag at all"
        assert all(flag == "exclude-user-input" for flag in calls["flags"])


class TestTheShippedWiringUsesIt:
    """`build_clipboard_capture` constructs Qt objects, so this reads the source with ast."""

    def test_build_clipboard_capture_injects_the_pumping_settle(self) -> None:
        """The regression is a one-word revert: dropping ``sleep=`` restores the bug silently."""
        import ast
        from pathlib import Path

        import omnia_desktop_clipper.capture.clipboard as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        builder = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "build_clipboard_capture"
        )
        injected = {
            keyword.value.func.id
            for call in ast.walk(builder)
            if isinstance(call, ast.Call)
            for keyword in call.keywords
            if keyword.arg == "sleep"
            and isinstance(keyword.value, ast.Call)
            and isinstance(keyword.value.func, ast.Name)
        }
        assert "QtEventLoopSettle" in injected, (
            "the shipped capture no longer pumps the event loop while settling; "
            "on Windows every capture will come back empty"
        )


class TestCaptureRefusesToNest:
    """Pumping the event loop makes a nested capture REACHABLE; it must still be impossible.

    A blocking sleep delivered nothing, so nesting could not happen. The pump delivers queued
    cross-thread signals -- which is how every gesture and hotkey in this app arrives -- so the
    guard has to be explicit rather than a side effect of blocking.
    """

    @staticmethod
    def _capture_that_reenters(clipboard, emitter, attempts):
        """A capture whose SETTLE calls back into itself, the way a pumped signal would."""
        capture = ClipboardCapture(clipboard, emitter, sleep=lambda _s: None)

        def settle(_seconds: float) -> None:
            attempts.append(capture.capture())

        capture._sleep = settle
        return capture

    def test_a_nested_call_is_refused(self) -> None:
        clipboard = _WindowsLikeClipboard("ORIGINAL")
        attempts: list = []
        capture = self._capture_that_reenters(
            clipboard, _WindowsCopyEmitter(clipboard, "selected text"), attempts
        )

        capture.capture()

        assert attempts == [None], "the nested capture was allowed to run"

    def test_the_outer_capture_still_returns_the_selection(self) -> None:
        """The bug this prevents: the nested call restored an EMPTY snapshot over the real one,
        so the gesture the user actually made came back with nothing and showed no "+".
        """
        clipboard = _WindowsLikeClipboard("ORIGINAL")
        attempts: list = []
        capture = self._capture_that_reenters(
            clipboard, _WindowsCopyEmitter(clipboard, "selected text"), attempts
        )

        def settle(_seconds: float) -> None:
            attempts.append(capture.capture())
            clipboard.pump()

        capture._sleep = settle

        assert capture.capture() == "selected text"

    def test_the_users_clipboard_survives_a_nested_attempt(self) -> None:
        clipboard = _WindowsLikeClipboard("ORIGINAL")
        attempts: list = []
        capture = self._capture_that_reenters(
            clipboard, _WindowsCopyEmitter(clipboard, "selected text"), attempts
        )

        capture.capture()

        assert clipboard.get_text() == "ORIGINAL"

    def test_the_flag_clears_so_the_next_gesture_works(self) -> None:
        """A guard that never resets would silently kill every capture after the first."""
        clipboard = _WindowsLikeClipboard("ORIGINAL")
        capture = ClipboardCapture(
            clipboard,
            _WindowsCopyEmitter(clipboard, "selected text"),
            sleep=lambda _s: clipboard.pump(),
        )

        assert capture.capture() == "selected text"
        assert capture.in_flight is False
        assert capture.capture() == "selected text"

    def test_the_flag_clears_even_when_the_clipboard_raises(self) -> None:
        class _Exploding(_WindowsLikeClipboard):
            def get_text(self) -> str:
                raise RuntimeError("clipboard busy")

        clipboard = _Exploding("ORIGINAL")
        capture = ClipboardCapture(
            clipboard, _WindowsCopyEmitter(clipboard, "x"), sleep=lambda _s: None
        )

        with pytest.raises(RuntimeError):
            capture.capture()
        assert capture.in_flight is False, "one failure would wedge every later capture"
