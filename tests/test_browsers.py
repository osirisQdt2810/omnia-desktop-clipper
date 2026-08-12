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
    ["com.microsoft.VSCode", "com.apple.TextEdit", "com.tinyspeck.slackmacgap", "net.kovidgoyal.kitty"],
)
def test_other_apps_are_not_browsers(bundle_id: str) -> None:
    assert is_browser(bundle_id) is False


def test_unknown_app_is_not_a_browser() -> None:
    # An app we cannot identify must keep working normally, not be silently skipped.
    assert is_browser("") is False
