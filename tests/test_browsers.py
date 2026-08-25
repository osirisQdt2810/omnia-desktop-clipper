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
