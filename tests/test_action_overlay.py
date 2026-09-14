"""The action pill's geometry, which is the part that silently breaks when a button is added.

The web clipper shipped exactly this bug: a right-edge clamp tuned for two buttons, a third one
added, and 14 of its 22 pixels ended up past the screen edge — where a frameless always-on-top
window is clipped rather than scrollable-to, and the right-hand column is a common place to be
selecting text. The width here is derived rather than written down, and this is what holds it
to that.

Skipped where PyQt6 is absent; CI installs it from requirements.txt.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from omnia_desktop_clipper.ui.action_overlay import _BUTTON, _GAP, _PAD, ActionOverlay


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _expected(buttons: int) -> int:
    """The width a pill of ``buttons`` buttons must be, from the constants it is drawn with."""
    return _PAD * 2 + buttons * _BUTTON + _GAP * max(0, buttons - 1)


class TestThePillFitsItsButtons:
    def test_three_buttons_get_three_buttons_worth_of_pill(self, qapp):
        overlay = ActionOverlay(
            lambda: None, on_lookup=lambda: None, on_check=lambda: None
        )

        assert overlay.button_count() == 3
        assert overlay.width() == _expected(3)

    def test_the_plus_alone_is_still_the_original_single_button_pill(self, qapp):
        overlay = ActionOverlay(lambda: None)

        assert overlay.button_count() == 1
        assert overlay.width() == _expected(1)

    def test_switching_word_lookup_off_takes_both_of_its_buttons_with_it(self, qapp):
        # One switch for both, because they are one service: the wand POSTs to the same loopback
        # port the magnifier reads from, so "Word Lookup off" leaves neither anything to talk to.
        overlay = ActionOverlay(
            lambda: None, on_lookup=lambda: None, on_check=lambda: None
        )

        overlay.set_lookup_enabled(False)

        assert overlay.button_count() == 1
        assert overlay.width() == _expected(1)

    def test_switching_it_back_on_restores_the_full_pill(self, qapp):
        overlay = ActionOverlay(
            lambda: None, on_lookup=lambda: None, on_check=lambda: None
        )
        overlay.set_lookup_enabled(False)

        overlay.set_lookup_enabled(True)

        assert overlay.button_count() == 3
        assert overlay.width() == _expected(3)

    def test_a_build_without_a_check_callback_shows_two(self, qapp):
        overlay = ActionOverlay(lambda: None, on_lookup=lambda: None)

        assert overlay.button_count() == 2
        assert overlay.width() == _expected(2)

    def test_the_pill_is_never_wider_than_the_buttons_it_shows(self, qapp):
        # The assertion that would have caught the web clipper's bug: not "is it 74px", which is
        # just restating the constant, but that the pill and its contents agree for EVERY shape.
        for lookup, check in (
            (None, None),
            (lambda: None, None),
            (lambda: None, lambda: None),
        ):
            overlay = ActionOverlay(lambda: None, on_lookup=lookup, on_check=check)
            assert overlay.width() == _expected(
                overlay.button_count()
            ), f"{overlay.button_count()} buttons in a {overlay.width()}px pill"


class TestThePillActsOnTheRightButton:
    def test_the_wand_calls_the_check_callback_and_dismisses(self, qapp):
        pressed: list[str] = []
        overlay = ActionOverlay(
            lambda: pressed.append("add"),
            on_lookup=lambda: pressed.append("lookup"),
            on_check=lambda: pressed.append("check"),
        )

        overlay._handle_check()

        assert pressed == ["check"]
        assert not overlay.isVisible()

    def test_a_wand_with_no_callback_is_a_no_op_rather_than_a_crash(self, qapp):
        overlay = ActionOverlay(lambda: None, on_lookup=lambda: None)

        overlay._handle_check()  # the button is hidden, but the slot must still be safe
