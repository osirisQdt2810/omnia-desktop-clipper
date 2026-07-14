"""Tests for the pure ``capture.gesture`` selection-gesture detector (no PyQt6/pynput needed)."""

from __future__ import annotations

from omnia_desktop_clipper.capture.gesture import (
    MouseGesture,
    SelectionGestureDetector,
)


class TestDoubleClick:
    def test_two_quick_stationary_clicks_are_a_double_click(self) -> None:
        d = SelectionGestureDetector()
        d.on_press(100, 100, 0.00)
        assert d.on_release(100, 100, 0.05) is MouseGesture.NONE  # first click
        d.on_press(101, 100, 0.20)
        assert d.on_release(101, 100, 0.25) is MouseGesture.DOUBLE_CLICK  # second click

    def test_clicks_too_far_apart_in_time_are_not_a_double_click(self) -> None:
        d = SelectionGestureDetector(double_click_seconds=0.4)
        d.on_press(100, 100, 0.0)
        d.on_release(100, 100, 0.0)
        d.on_press(100, 100, 1.0)  # 1s later → too slow
        assert d.on_release(100, 100, 1.0) is MouseGesture.NONE

    def test_clicks_too_far_apart_in_space_are_not_a_double_click(self) -> None:
        d = SelectionGestureDetector(double_click_radius=6.0)
        d.on_press(100, 100, 0.0)
        d.on_release(100, 100, 0.0)
        d.on_press(400, 400, 0.1)  # far away → separate clicks
        assert d.on_release(400, 400, 0.1) is MouseGesture.NONE

    def test_triple_click_does_not_double_fire(self) -> None:
        d = SelectionGestureDetector()
        d.on_press(10, 10, 0.0)
        d.on_release(10, 10, 0.0)
        d.on_press(10, 10, 0.1)
        assert d.on_release(10, 10, 0.1) is MouseGesture.DOUBLE_CLICK
        d.on_press(10, 10, 0.2)
        # The double-click was consumed, so the third click is just a fresh single click.
        assert d.on_release(10, 10, 0.2) is MouseGesture.NONE


class TestDragSelect:
    def test_press_then_release_far_away_is_a_drag(self) -> None:
        d = SelectionGestureDetector(drag_min_distance=8.0)
        d.on_press(100, 100, 0.0)
        assert d.on_release(200, 140, 0.3) is MouseGesture.DRAG

    def test_tiny_movement_is_not_a_drag(self) -> None:
        d = SelectionGestureDetector(drag_min_distance=8.0)
        d.on_press(100, 100, 0.0)
        assert d.on_release(103, 101, 0.1) is MouseGesture.NONE  # jitter, not a drag

    def test_a_drag_resets_a_pending_double_click(self) -> None:
        d = SelectionGestureDetector()
        d.on_press(10, 10, 0.0)
        d.on_release(10, 10, 0.0)  # first click pending
        d.on_press(10, 10, 0.1)
        assert d.on_release(200, 200, 0.2) is MouseGesture.DRAG  # became a drag
        d.on_press(10, 10, 0.3)
        # The pending double-click was cleared by the drag, so this stationary click is NONE.
        assert d.on_release(10, 10, 0.3) is MouseGesture.NONE


class TestEdges:
    def test_release_without_press_is_none(self) -> None:
        d = SelectionGestureDetector()
        assert d.on_release(5, 5, 0.0) is MouseGesture.NONE

    def test_single_click_is_none(self) -> None:
        d = SelectionGestureDetector()
        d.on_press(1, 1, 0.0)
        assert d.on_release(1, 1, 0.0) is MouseGesture.NONE
