"""The action pill's geometry, which is the part that silently breaks when a button is added.

The web clipper shipped exactly this bug: a right-edge clamp tuned for two buttons, a third one
added, and 14 of its 22 pixels ended up past the screen edge — where a frameless always-on-top
window is clipped rather than scrollable-to, and the right-hand column is a common place to be
selecting text. The width here is derived rather than written down, and this is what holds it
to that.

Skipped where PyQt6 is absent, and where ``QtWidgets`` cannot load — a headless Linux runner has
no ``libEGL``, and asking for a widget there is an ImportError at COLLECTION time, which fails
the whole suite rather than skipping one file. ``importorskip`` on the submodule is what turns
that into a skip. CI installs PyQt6 from requirements.txt; macOS and Windows run this for real.
"""

from __future__ import annotations

import pytest

from conftest import requires_qt_widgets

# BEFORE the imports below, which is the whole point: they pull in QtWidgets, so a module that
# imported them first would raise at collection rather than skip. Hence the E402s.
requires_qt_widgets()

from omnia_desktop_clipper.ui.action_overlay import (  # noqa: E402
    _BUTTON,
    _GAP,
    _PAD,
    ActionOverlay,
)


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


class TestThePillLandsOnScreen:
    """That the widget actually USES the clamp, which the width tests structurally cannot see.

    ``pill_geometry`` has the arithmetic and is tested on every platform; this is the wiring.
    Removing the clamp from ``show_at`` leaves every width assertion passing, because none of
    them ever place the pill — which is precisely how the missing clamp got here.
    """

    @staticmethod
    def _area():
        from PyQt6.QtGui import QGuiApplication

        return QGuiApplication.primaryScreen().availableGeometry()

    def test_a_gesture_at_the_right_edge_keeps_the_whole_pill_visible(self, qapp):
        # The failure: the pill grew from 54px to 80px with the third button, so a selection
        # near the right edge put the wand — the new one — entirely past it, where a frameless
        # always-on-top window is clipped rather than scrollable-to.
        area = self._area()
        overlay = ActionOverlay(
            lambda: None, on_lookup=lambda: None, on_check=lambda: None
        )

        overlay.show_at(area.right() - 30, 100)

        assert (
            overlay.x() + overlay.width() <= area.right() + 1
        ), f"the pill runs {overlay.x() + overlay.width() - area.right()}px off the right edge"
        assert overlay.x() >= area.left()

    def test_a_gesture_at_the_bottom_edge_does_the_same(self, qapp):
        area = self._area()
        overlay = ActionOverlay(
            lambda: None, on_lookup=lambda: None, on_check=lambda: None
        )

        overlay.show_at(400, area.bottom() - 5)

        assert overlay.y() + overlay.height() <= area.bottom() + 1
        assert overlay.y() >= area.top()

    def test_a_gesture_in_the_middle_is_left_where_it_was_put(self, qapp):
        # The clamp must not move a pill that was never in trouble: it would drift away from the
        # pointer for no reason, and the offset is what keeps it out from under the cursor.
        from omnia_desktop_clipper.ui.pill_geometry import CURSOR_OFFSET

        overlay = ActionOverlay(
            lambda: None, on_lookup=lambda: None, on_check=lambda: None
        )

        overlay.show_at(300, 200)

        assert (overlay.x(), overlay.y()) == (300 + CURSOR_OFFSET, 200 + CURSOR_OFFSET)

    def test_it_holds_for_every_pill_shape(self, qapp):
        area = self._area()
        for lookup, check in (
            (None, None),
            (lambda: None, None),
            (lambda: None, lambda: None),
        ):
            overlay = ActionOverlay(lambda: None, on_lookup=lookup, on_check=check)
            overlay.show_at(area.right() - 5, area.bottom() - 5)
            assert (
                overlay.x() + overlay.width() <= area.right() + 1
            ), overlay.button_count()
            assert (
                overlay.y() + overlay.height() <= area.bottom() + 1
            ), overlay.button_count()


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
