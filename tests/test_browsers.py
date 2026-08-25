"""Tests for browser recognition (which app the desktop '+' stands aside for)."""

from __future__ import annotations

import pytest

from omnia_desktop_clipper.browsers import is_browser


@pytest.mark.parametrize(
    "bundle_id",
    [
        "com.google.Chrome",
        "com.apple.Safari",
        "org.mozilla.firefox",
        "com.microsoft.edgemac",
        "com.brave.Browser",
        "company.thebrowser.Browser",
    ],
)
def test_known_browsers_are_recognised(bundle_id: str) -> None:
    assert is_browser(bundle_id) is True


def test_matching_ignores_case_and_padding() -> None:
    assert is_browser("  COM.GOOGLE.CHROME  ") is True


@pytest.mark.parametrize(
    "bundle_id",
    [
        "com.microsoft.VSCode",
        "com.apple.TextEdit",
        "com.tinyspeck.slackmacgap",
        "net.kovidgoyal.kitty",
    ],
)
def test_other_apps_are_not_browsers(bundle_id: str) -> None:
    assert is_browser(bundle_id) is False


def test_unknown_app_is_not_a_browser() -> None:
    # An app we cannot identify must keep working normally, not be silently skipped.
    assert is_browser("") is False


class TestWindowsProcessNames:
    """Windows has no bundle ids, so the frontmost app is named by its process image.

    Until this was added, ``frontmost_app_id`` returned ``""`` on Windows and every app looked
    unrecognised. The desktop "+" therefore never stood aside for the web clipper there, and a
    double-click in Chrome raised two "+" buttons — the exact collision the split exists to
    prevent.
    """

    @pytest.mark.parametrize(
        "process_name",
        [
            "chrome.exe",
            "msedge.exe",
            "firefox.exe",
            "brave.exe",
            "opera.exe",
            "vivaldi.exe",
        ],
    )
    def test_a_browser_process_is_recognised(self, process_name: str) -> None:
        assert is_browser(process_name) is True

    def test_matching_ignores_case_and_padding(self) -> None:
        """``QueryFullProcessImageNameW`` returns the on-disk casing, which varies."""
        assert is_browser("  CHROME.EXE  ") is True

    @pytest.mark.parametrize(
        "process_name",
        ["notepad.exe", "code.exe", "explorer.exe", "anki.exe", "chrome"],
    )
    def test_a_non_browser_process_is_not(self, process_name: str) -> None:
        """``chrome`` without the extension is included deliberately: the Windows path always
        carries ``.exe``, so a bare name means the value came from somewhere unexpected and
        should not silently disable the "+"."""
        assert is_browser(process_name) is False

    def test_both_naming_schemes_work_in_one_process(self) -> None:
        """One predicate serves both platforms; neither set may shadow the other."""
        assert is_browser("com.google.chrome") is True
        assert is_browser("chrome.exe") is True


class TestWindowsFrontmostProcessName:
    """The half that was actually broken, driven with a fake ``ctypes.windll``.

    ``is_browser``'s string set was never the bug -- ``frontmost_bundle_id`` returning ``""``
    on every non-macOS platform was. These cover that function's own failure paths, which no
    amount of testing the string set would reach.
    """

    @staticmethod
    def _fake_windll(
        monkeypatch, *, hwnd=1234, pid=99, handle=7, image=None, query_ok=True
    ):
        import ctypes

        from omnia_desktop_clipper import platform as platform_module

        class _Fn:
            def __init__(self, result, side_effect=None):
                self.restype = None
                self.argtypes = None
                self._result = result
                self._side_effect = side_effect

            def __call__(self, *args):
                if self._side_effect is not None:
                    self._side_effect(*args)
                return self._result

        def _write_image(_handle, _flags, buffer, _size):
            if image is not None:
                buffer.value = image

        closed = []

        class _User32:
            GetForegroundWindow = _Fn(hwnd)
            GetWindowThreadProcessId = _Fn(
                1, lambda _h, out: setattr(out._obj, "value", pid)
            )

        class _Kernel32:
            OpenProcess = _Fn(handle)
            QueryFullProcessImageNameW = _Fn(1 if query_ok else 0, _write_image)
            CloseHandle = _Fn(1, lambda h: closed.append(h))

        class _Windll:
            user32 = _User32()
            kernel32 = _Kernel32()

        monkeypatch.setattr(ctypes, "windll", _Windll(), raising=False)
        monkeypatch.setattr(platform_module.sys, "platform", "win32")
        return closed

    def test_it_returns_the_lowercased_basename_of_the_full_path(self, monkeypatch):
        from omnia_desktop_clipper.platform import frontmost_app_id

        self._fake_windll(
            monkeypatch, image=r"C:\Program Files\Google\Chrome\Application\CHROME.EXE"
        )

        assert frontmost_app_id() == "chrome.exe"

    def test_no_foreground_window_is_not_a_browser(self, monkeypatch):
        """A locked screen or a desktop switch has no foreground window; the "+" must keep working."""
        from omnia_desktop_clipper.platform import frontmost_app_id

        self._fake_windll(monkeypatch, hwnd=0)

        assert frontmost_app_id() == ""

    def test_a_refused_open_process_is_not_a_browser(self, monkeypatch):
        """An ELEVATED foreground app refuses OpenProcess to an ordinary one.

        Returning "" there is deliberate: it means "not a browser", which keeps the desktop "+"
        working over an admin window rather than silently disabling it.
        """
        from omnia_desktop_clipper.platform import frontmost_app_id

        self._fake_windll(monkeypatch, handle=0)

        assert frontmost_app_id() == ""

    def test_a_failed_query_is_not_a_browser(self, monkeypatch):
        from omnia_desktop_clipper.platform import frontmost_app_id

        self._fake_windll(monkeypatch, query_ok=False, image="chrome.exe")

        assert frontmost_app_id() == ""

    def test_the_process_handle_is_always_closed(self, monkeypatch):
        """A handle leaked once per double-click adds up over a session."""
        from omnia_desktop_clipper.platform import frontmost_app_id

        closed = self._fake_windll(monkeypatch, image="notepad.exe")

        frontmost_app_id()

        assert closed == [7]

    def test_the_buffer_is_bigger_than_max_path(self, monkeypatch):
        """A browser under a long install path must not come back empty.

        260 chars fails with ERROR_INSUFFICIENT_BUFFER, which reads as "not a browser" and
        brings back the two-"+" collision for the users with the longest paths.
        """
        import ctypes

        from omnia_desktop_clipper.platform import frontmost_app_id

        sizes: list[int] = []
        self._fake_windll(monkeypatch, image="chrome.exe")
        original = ctypes.create_unicode_buffer

        def spy(size):
            sizes.append(size)
            return original(size)

        # platform.py imports ctypes INSIDE the function, so patching the module itself is
        # what the call actually sees.
        monkeypatch.setattr(ctypes, "create_unicode_buffer", spy)
        frontmost_app_id()

        assert sizes and max(sizes) > 260
