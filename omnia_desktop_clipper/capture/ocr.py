"""OCR capture: read text from a screen region that can't be selected/copied.

Use OCR when there is no selectable text layer — scanned/image PDFs, screenshots and
images, and apps that expose neither copy nor accessibility. The flow: the user drags a
rectangle over the screen, that region is grabbed as a PNG, and an :class:`OcrEngine` turns
it into text (then the normal capture popup opens).

The engine + the screen grab are injected into :class:`RegionOcrCapture`, so the capture
logic unit-tests with a fake engine + fake grabber (no real screen, no OCR model download).
:class:`RapidOcrEngine` is the concrete CPU engine — RapidOCR (ONNX Runtime): cross-platform
pip wheels, no PyTorch, self-contained models — lazy-imported so importing this module is free.
"""

from __future__ import annotations

import abc
from collections.abc import Callable, Sequence
from typing import Any

# A grabber takes a screen rectangle (x, y, width, height) and returns PNG bytes.
RegionGrabber = Callable[["tuple[int, int, int, int]"], bytes]


def boxes_to_reading_order(result: Sequence[Any] | None) -> str:
    """Turn RapidOCR's ``[[box, text, score], ...]`` into text in reading order.

    RapidOCR returns one entry per detected text box; ``box`` is four ``[x, y]`` corner
    points. It does not guarantee reading order (a spaced-out line can come back as
    separate word-boxes in an odd order). This groups boxes into lines (by vertical
    proximity — within 60% of a box's height), orders each line left-to-right, joins a
    line's boxes with spaces, and joins lines with newlines. Pure + unit-testable.

    Args:
        result: RapidOCR's result list (or ``None``/empty).

    Returns:
        The recognised text in reading order (``""`` when nothing was detected).
    """
    entries: list[tuple[float, float, float, str]] = []
    for item in result or []:
        if len(item) < 2:
            continue
        box, text = item[0], str(item[1]).strip()
        if not text:
            continue
        ys = [float(point[1]) for point in box]
        xs = [float(point[0]) for point in box]
        entries.append((min(ys), min(xs), max(ys) - min(ys), text))
    if not entries:
        return ""
    entries.sort(key=lambda entry: (entry[0], entry[1]))
    lines: list[list[tuple[float, float, float, str]]] = [[entries[0]]]
    for entry in entries[1:]:
        tolerance = max(lines[-1][0][2], entry[2]) * 0.6
        if abs(entry[0] - lines[-1][0][0]) <= tolerance:
            lines[-1].append(entry)
        else:
            lines.append([entry])
    ordered_lines = []
    for line in lines:
        line.sort(key=lambda entry: entry[1])
        ordered_lines.append(" ".join(entry[3] for entry in line))
    return "\n".join(ordered_lines)


class OcrEngine(abc.ABC):
    """Turns image bytes into recognised text."""

    @abc.abstractmethod
    def image_to_text(self, png_bytes: bytes) -> str:
        """Return the recognised text for a PNG image (``""`` if none)."""


class RapidOcrEngine(OcrEngine):
    """CPU OCR via RapidOCR (ONNX Runtime). Lazy-loads the model on first use."""

    def __init__(self) -> None:
        self._engine: object | None = None

    def _ensure_engine(self) -> object:
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR()
        return self._engine

    def image_to_text(self, png_bytes: bytes) -> str:
        """Recognise text in ``png_bytes`` and return the lines joined by newlines."""
        import io

        import numpy as np
        from PIL import Image

        image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        engine = self._ensure_engine()
        result, _elapsed = engine(np.asarray(image))  # type: ignore[operator]
        # RapidOCR returns [[box, text, score], ...] without a guaranteed order; sort into
        # reading order (lines top-to-bottom, words left-to-right) before returning.
        return boxes_to_reading_order(result)


class RegionOcrCapture:
    """Capture text from a screen rectangle via a grabber + an :class:`OcrEngine`."""

    def __init__(self, engine: OcrEngine, grab_region: RegionGrabber) -> None:
        """Initialise the capture.

        Args:
            engine: The OCR engine to run on the grabbed image.
            grab_region: Callable taking ``(x, y, w, h)`` and returning PNG bytes.
        """
        self._engine = engine
        self._grab_region = grab_region

    def capture(self, region: tuple[int, int, int, int]) -> str | None:
        """Grab ``region`` (x, y, w, h), OCR it, and return the text (or ``None``)."""
        _x, _y, width, height = region
        if width <= 0 or height <= 0:
            return None
        png_bytes = self._grab_region(region)
        if not png_bytes:
            return None
        text = self._engine.image_to_text(png_bytes).strip()
        return text or None
