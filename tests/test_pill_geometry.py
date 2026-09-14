"""Where the action pill goes and how wide it is. No Qt, so it runs on every platform.

That is the point of the file rather than an incidental property of it. The screen-edge rule has
now broken twice — once in the web clipper (a clamp tuned for two buttons, a third added) and
once here (no clamp at all, and a pill 26px wider) — and both times the widget test could not see
it, either because it only checked the width or because the leg that would have run it was
skipped. Arithmetic this load-bearing does not belong behind a ``QApplication``.
"""

from __future__ import annotations

import pytest

from omnia_desktop_clipper.ui.pill_geometry import (
    BUTTON_PX,
    CURSOR_OFFSET,
    GAP_PX,
    PAD_PX,
    SCREEN_MARGIN,
    Area,
    pill_height,
    pill_width,
    place,
)

#: A 1440x900 display whose origin is not (0, 0) — a second monitor, which is where off-by-one
#: clamping actually bites.
SCREEN = Area(left=1440, top=0, right=2879, bottom=899)
#: The primary one, for the ordinary case.
PRIMARY = Area(left=0, top=0, right=1439, bottom=899)


class TestHowWideItIs:
    def test_each_button_is_paid_for(self):
        assert pill_width(1) == PAD_PX * 2 + BUTTON_PX
        assert pill_width(2) == PAD_PX * 2 + BUTTON_PX * 2 + GAP_PX
        assert pill_width(3) == PAD_PX * 2 + BUTTON_PX * 3 + GAP_PX * 2

    def test_every_extra_button_costs_the_same(self):
        # The old formula was `+ (GAP if buttons > 1 else 0)` — one gap however many buttons —
        # which under-sized a three-button pill by exactly one gap. A uniform step is what makes
        # a fourth button safe.
        steps = {pill_width(n + 1) - pill_width(n) for n in range(1, 6)}

        assert steps == {BUTTON_PX + GAP_PX}

    def test_a_pill_with_no_buttons_is_just_its_padding(self):
        assert pill_width(0) == PAD_PX * 2

    def test_a_nonsense_count_does_not_produce_a_nonsense_width(self):
        assert pill_width(-3) == PAD_PX * 2

    def test_the_height_does_not_depend_on_the_count(self):
        assert pill_height() == PAD_PX * 2 + BUTTON_PX


class TestWhereItGoes:
    def test_it_sits_down_right_of_the_pointer_with_room_to_spare(self):
        assert place(700, 400, pill_width(3), pill_height(), PRIMARY) == (
            700 + CURSOR_OFFSET,
            400 + CURSOR_OFFSET,
        )

    def test_a_selection_at_the_right_edge_keeps_the_whole_pill_on_screen(self):
        # The bug. The pill grew from 54px to 80px with the third button, so a gesture 60px from
        # the edge put the wand — the new one — entirely past it, where a frameless
        # always-on-top window is clipped rather than scrollable-to.
        width = pill_width(3)

        x, _y = place(PRIMARY.right - 60, 400, width, pill_height(), PRIMARY)

        assert (
            x + width <= PRIMARY.right - SCREEN_MARGIN + 1
        ), f"the pill runs {x + width - PRIMARY.right}px off the right edge"

    def test_a_selection_at_the_bottom_edge_does_the_same(self):
        height = pill_height()

        _x, y = place(700, PRIMARY.bottom - 4, pill_width(3), height, PRIMARY)

        assert y + height <= PRIMARY.bottom - SCREEN_MARGIN + 1

    def test_it_stays_inside_a_screen_that_does_not_start_at_zero(self):
        # A second monitor. Clamping against a width rather than against the screen's own right
        # edge is the classic way to get this wrong, and it only shows up here.
        width = pill_width(3)

        x, y = place(
            SCREEN.right - 10, SCREEN.bottom - 10, width, pill_height(), SCREEN
        )

        assert SCREEN.left <= x and x + width <= SCREEN.right
        assert SCREEN.top <= y and y + pill_height() <= SCREEN.bottom

    def test_the_left_edge_wins_on_a_screen_narrower_than_the_pill(self):
        # Degenerate, but the ordering matters: clamping right-then-left keeps the "+" visible,
        # and losing that is no better than losing the wand.
        narrow = Area(left=0, top=0, right=40, bottom=899)

        x, _y = place(30, 10, pill_width(3), pill_height(), narrow)

        assert x >= narrow.left

    @pytest.mark.parametrize("buttons", [1, 2, 3, 4])
    def test_whatever_it_carries_it_lands_on_screen(self, buttons):
        # The property the individual cases are examples of. A fourth button fails this rather
        # than shipping.
        width, height = pill_width(buttons), pill_height()
        for x in (0, 1, 700, PRIMARY.right - 1, PRIMARY.right):
            for y in (0, 1, 400, PRIMARY.bottom - 1, PRIMARY.bottom):
                px, py = place(x, y, width, height, PRIMARY)
                assert (
                    PRIMARY.left <= px and px + width <= PRIMARY.right
                ), f"{buttons} buttons at ({x}, {y}) -> x={px}, width={width}"
                assert (
                    PRIMARY.top <= py and py + height <= PRIMARY.bottom
                ), f"{buttons} buttons at ({x}, {y}) -> y={py}, height={height}"
