"""Resolve the sentence/phrase surrounding a captured selection ("context").

The web clipper reads the DOM to grab the sentence around a word so the generated card
has accurate wording. The desktop equivalent uses the OS accessibility layer. This module
keeps that split cleanly:

* :func:`sentence_around` — a PURE, unit-testable helper that extracts the sentence
  containing a span from a larger text.
* :class:`ContextProvider` — the ABC; :class:`SelectionContextProvider` is the universal
  fallback (context = the selection itself), and :class:`MacAXContextProvider` reads the
  focused element's text via macOS Accessibility (lazy-imported pyobjc) and extracts the
  enclosing sentence — degrading to the fallback on ANY failure (no permission, unsupported
  app, selection not found).

:func:`build_context_provider` picks the accessibility backend on macOS, else the fallback,
so callers get auto-context where possible and the selection everywhere else.
"""

from __future__ import annotations

import abc
import sys

# Characters that terminate a sentence when scanning outward from the selection.
_SENTENCE_BOUNDARIES = ".!?\n\r"


def sentence_around(text: str, start: int, length: int) -> str:
    """Return the sentence in ``text`` that contains the span ``[start, start+length)``.

    Scans left/right from the span to the nearest sentence boundary (``.!?`` or newline)
    and returns the trimmed sentence. Returns ``""`` for an out-of-range span.

    Args:
        text: The full surrounding text (e.g. the focused field's value).
        start: The span's start index within ``text``.
        length: The span's length.

    Returns:
        The enclosing sentence (stripped), or ``""`` if the span is out of range.
    """
    if start < 0 or length < 0 or start + length > len(text):
        return ""
    left = start
    while left > 0 and text[left - 1] not in _SENTENCE_BOUNDARIES:
        left -= 1
    right = start + length
    while right < len(text) and text[right] not in _SENTENCE_BOUNDARIES:
        right += 1
    # Keep the terminating punctuation (. ! ?) as part of the sentence, but not a newline.
    if right < len(text) and text[right] in ".!?":
        right += 1
    return text[left:right].strip()


class ContextProvider(abc.ABC):
    """Resolves the context (surrounding sentence) for a captured selection."""

    @abc.abstractmethod
    def resolve(self, selection: str) -> str:
        """Return the context for ``selection`` (falls back to ``selection`` itself)."""


class SelectionContextProvider(ContextProvider):
    """Universal fallback: the context IS the selection (no OS support needed)."""

    def resolve(self, selection: str) -> str:
        return selection


class MacAXContextProvider(ContextProvider):
    """macOS Accessibility backend: the enclosing sentence of the selection.

    Reads the currently-focused UI element's text via ``AXUIElement`` (lazy-imported
    pyobjc), locates the selection inside it, and returns the enclosing sentence. Any
    failure — Accessibility permission not granted, the app exposing no AX text, or the
    selection not found in the focused text (e.g. a PDF image) — degrades to returning the
    selection unchanged, so context capture is always best-effort and never raises.
    """

    def resolve(self, selection: str) -> str:
        selection = selection.strip()
        if not selection:
            return selection
        try:
            text = self._focused_text()
        except (
            Exception
        ):  # pragma: no cover - needs a live macOS AX session + permission
            return selection
        if not text:
            return selection
        index = text.find(selection)
        if index < 0:
            return selection
        return sentence_around(text, index, len(selection)) or selection

    @staticmethod
    def _focused_text() -> (
        str
    ):  # pragma: no cover - macOS + Accessibility permission only
        """Return the plain text of the frontmost app's focused UI element (or ``""``).

        Queries the FRONTMOST application's AX element (via its pid) rather than the system-wide
        element: ``AXUIElementCreateSystemWide()`` + ``kAXFocusedUIElementAttribute`` returns
        ``kAXErrorCannotComplete`` (-25204) here, whereas the app-scoped element reliably yields
        the focused text field's full value (which is what makes the enclosing sentence available).
        """
        from AppKit import NSWorkspace
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
            AXUIElementSetMessagingTimeout,
            kAXFocusedUIElementAttribute,
            kAXValueAttribute,
        )

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return ""
        ax_app = AXUIElementCreateApplication(app.processIdentifier())
        AXUIElementSetMessagingTimeout(ax_app, 2.0)  # avoid a hang on an unresponsive app
        err, focused = AXUIElementCopyAttributeValue(
            ax_app, kAXFocusedUIElementAttribute, None
        )
        if err != 0 or focused is None:
            return ""
        err, value = AXUIElementCopyAttributeValue(focused, kAXValueAttribute, None)
        if err != 0 or value is None:
            return ""
        return str(value)


def build_context_provider(platform_name: str | None = None) -> ContextProvider:
    """Return the best context provider for the platform.

    macOS gets :class:`MacAXContextProvider` (auto sentence via Accessibility, falling back
    to the selection on any failure); every other platform gets
    :class:`SelectionContextProvider`.

    Args:
        platform_name: A ``sys.platform`` override (for tests). Defaults to the running OS.
    """
    platform_name = sys.platform if platform_name is None else platform_name
    if platform_name == "darwin":
        return MacAXContextProvider()
    return SelectionContextProvider()
