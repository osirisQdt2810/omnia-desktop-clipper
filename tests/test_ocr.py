"""Tests for the pure ``capture.ocr`` module (RegionOcrCapture with a fake engine)."""

from __future__ import annotations

import pytest

from omnia_desktop_clipper.capture.ocr import (
    OcrEngine,
    RegionOcrCapture,
    boxes_to_reading_order,
)


def _box(x0: int, y0: int, x1: int, y1: int) -> list[list[int]]:
    """A RapidOCR-style 4-corner box for the rectangle (x0,y0)-(x1,y1)."""
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


class TestReadingOrder:
    def test_orders_words_left_to_right_on_one_line(self) -> None:
        # RapidOCR returns the word boxes out of order; they share a line -> space-joined L-R.
        result = [
            [_box(300, 10, 360, 40), "brown", 0.9],
            [_box(10, 10, 70, 40), "The", 0.9],
            [_box(150, 10, 260, 40), "quick", 0.9],
        ]
        assert boxes_to_reading_order(result) == "The quick brown"

    def test_orders_multiple_lines_top_to_bottom(self) -> None:
        result = [
            [_box(10, 100, 90, 130), "second", 0.9],
            [_box(10, 10, 70, 40), "first", 0.9],
        ]
        assert boxes_to_reading_order(result) == "first\nsecond"

    def test_empty_or_none(self) -> None:
        assert boxes_to_reading_order(None) == ""
        assert boxes_to_reading_order([]) == ""


class _FakeEngine(OcrEngine):
    def __init__(self, text: str) -> None:
        self._text = text
        self.seen: list[bytes] = []

    def image_to_text(self, png_bytes: bytes) -> str:
        self.seen.append(png_bytes)
        return self._text


class TestRegionOcrCapture:
    def test_grabs_region_then_ocrs_it(self) -> None:
        engine = _FakeEngine("recognised text")
        grabbed: list[tuple[int, int, int, int]] = []

        def grab(region: tuple[int, int, int, int]) -> bytes:
            grabbed.append(region)
            return b"PNGDATA"

        out = RegionOcrCapture(engine, grab).capture((10, 20, 100, 50))
        assert out == "recognised text"
        assert grabbed == [(10, 20, 100, 50)]
        assert engine.seen == [b"PNGDATA"]

    def test_zero_area_region_returns_none(self) -> None:
        cap = RegionOcrCapture(_FakeEngine("x"), lambda _r: b"P")
        assert cap.capture((0, 0, 0, 10)) is None
        assert cap.capture((0, 0, 10, 0)) is None

    def test_empty_grab_returns_none(self) -> None:
        cap = RegionOcrCapture(_FakeEngine("x"), lambda _r: b"")
        assert cap.capture((0, 0, 10, 10)) is None

    def test_blank_ocr_result_returns_none(self) -> None:
        cap = RegionOcrCapture(_FakeEngine("   \n  "), lambda _r: b"P")
        assert cap.capture((0, 0, 10, 10)) is None

    def test_ocr_engine_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            OcrEngine()  # type: ignore[abstract]
