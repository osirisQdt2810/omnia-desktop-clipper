"""Resolve the sentence/phrase surrounding a captured selection ("context").

The web clipper reads the DOM to grab the sentence around a word so the generated card has
accurate wording. The desktop equivalent uses the OS accessibility (AX) layer.

Why this is more than "read the focused element's value"
-------------------------------------------------------
Measured on macOS 15 (see the table below), the focused element alone only carries the text in
*native* text views. The apps people actually read in expose it elsewhere:

===============  ==================  ======================  ==============================
App              focused AXRole      focused ``AXValue``     where the text really is
===============  ==================  ======================  ==============================
TextEdit         ``AXTextArea``      the full text           the focused element itself
Chrome/Safari    ``AXWebArea``       **empty**               a descendant ``AXStaticText``
VSCode/Electron  ``AXTextArea``      the current line        the focused element — but ONLY
                                                             after AX is switched on (below)
===============  ==================  ======================  ==============================

So this module does two things beyond reading one attribute:

1. **Wake the app's AX tree** (:func:`warm_accessibility`). Chromium builds its accessibility
   tree lazily — a cold Chrome answers ``AXFocusedUIElement`` with an error and only becomes
   readable ~2s after something asks. Electron apps (VSCode, Slack, …) stay dark until a client
   sets ``AXManualAccessibility``. Both are exactly what an assistive app does; warming is done
   per-pid, off the capture path, so by the time a gesture happens the tree is ready.
2. **Search the AX subtree** (:func:`find_text_containing`) for the node whose value CONTAINS the
   selection, instead of trusting the focused node. Breadth-first from the focused element, then
   outward through a few ancestors, then the focused window. Measured cost once warm: 1 node in
   TextEdit/VSCode, 3 nodes in Chrome — 0 ms, so it is safe on the capture path.

:func:`sentence_around` then trims the found text down to the enclosing sentence.

Every step degrades to returning the selection unchanged (no permission, unsupported app, PDF
image, text not found), so context capture is always best-effort and never raises.
"""

from __future__ import annotations

import abc
import sys
from collections.abc import Callable, Iterable
from typing import Any, Optional

# Characters that end a sentence. A newline is deliberately NOT one: a sentence in a document,
# an editor or a wrapped web page routinely spans lines, and stopping at the line break was
# giving "context = this line" instead of the actual sentence.
_SENTENCE_ENDERS = ".!?"
# Hard boundaries that always stop the scan: a blank line separates paragraphs, so the sentence
# can never run past one even when nobody wrote a full stop.
_PARAGRAPH_BREAK = "\n\n"
# Cap on how far the scan may run without meeting a sentence ender (code, lists, headings often
# have none). Beyond this we fall back to the selection's own line, which is always sane.
_MAX_SENTENCE_CHARS = 400

# Search bounds. Kept small deliberately: every real hit measured so far was within 3 nodes, and
# an unbounded walk of a big app's AX tree would stall the "+" overlay.
_MAX_NODES = 600
_MAX_DEPTH = 12
_MAX_CHILDREN = 60
_MAX_ANCESTOR_HOPS = 3
# A stuck app must not freeze the capture path (the "+" is shown right after this returns).
_AX_TIMEOUT_SECONDS = 1.0


def sentence_around(text: str, start: int, length: int) -> str:
    """Return the sentence in ``text`` that contains the span ``[start, start+length)``.

    Scans outward to the nearest sentence ender (``.``/``!``/``?``), **crossing line breaks** —
    a sentence in prose, an editor or a wrapped page regularly spans lines, and treating a
    newline as an ender was what made the captured context read as "just this line".

    Two guards keep that from over-reaching:

    * a blank line (paragraph break) always stops the scan;
    * if no ender is met within :data:`_MAX_SENTENCE_CHARS`, the span falls back to the
      selection's own line — text without full stops (code, lists, headings) must not return a
      whole document.

    Returns ``""`` for an out-of-range span.

    Args:
        text: The full surrounding text (e.g. the focused field's value).
        start: The span's start index within ``text``.
        length: The span's length.

    Returns:
        The enclosing sentence with internal line breaks collapsed to spaces, or ``""``.
    """
    if start < 0 or length < 0 or start + length > len(text):
        return ""
    end = start + length
    left = _scan_left(text, start)
    right = _scan_right(text, end)
    if right - left > _MAX_SENTENCE_CHARS:
        left, right = _line_bounds(text, start, end)
    return " ".join(text[left:right].split())


def _scan_left(text: str, start: int) -> int:
    """Index just after the sentence ender / paragraph break preceding ``start``."""
    limit = max(0, start - _MAX_SENTENCE_CHARS)
    index = start
    while index > limit:
        if text[index - 1] in _SENTENCE_ENDERS:
            return index
        if text[index - 1] == "\n" and text[max(0, index - 2) : index] == _PARAGRAPH_BREAK:
            return index
        index -= 1
    return index


def _scan_right(text: str, end: int) -> int:
    """Index just after the sentence ender / before the paragraph break following ``end``."""
    limit = min(len(text), end + _MAX_SENTENCE_CHARS)
    index = end
    while index < limit:
        if text[index] in _SENTENCE_ENDERS:
            return index + 1  # keep the full stop as part of the sentence
        if text[index : index + 2] == _PARAGRAPH_BREAK:
            return index
        index += 1
    return index


def _line_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """The bounds of the line(s) holding ``[start, end)`` — the no-full-stop fallback."""
    left = text.rfind("\n", 0, start) + 1
    right = text.find("\n", end)
    return left, len(text) if right < 0 else right


def find_text_containing(
    roots: Iterable[Any],
    selection: str,
    value_of: Callable[[Any], Optional[str]],
    children_of: Callable[[Any], list],
    *,
    max_nodes: int = _MAX_NODES,
    max_depth: int = _MAX_DEPTH,
    max_children: int = _MAX_CHILDREN,
) -> str:
    """Breadth-first search ``roots`` for a node whose text CONTAINS ``selection``.

    Pure graph search over injected accessors, so it unit-tests without any AX session. Roots are
    tried in order (focused element first, then its ancestors, then the window) and the node
    budget is shared across all of them, bounding the total work regardless of how many roots the
    caller supplies.

    Breadth-first matters: it returns the SHALLOWEST — i.e. tightest — container holding the
    selection, rather than a deep fragment or the whole document.

    Args:
        roots: Candidate subtree roots, most-likely first.
        selection: The captured text that the wanted node must contain.
        value_of: Returns a node's text value (or ``None``).
        children_of: Returns a node's children.
        max_nodes: Total node budget across all roots.
        max_depth: Deepest level to descend from each root.
        max_children: Per-node cap on children enqueued (guards pathological containers).

    Returns:
        The containing node's text, or ``""`` if nothing matched within the budget.
    """
    if not selection:
        return ""
    visited = 0
    for root in roots:
        if root is None or visited >= max_nodes:
            continue
        queue: list[tuple[Any, int]] = [(root, 0)]
        while queue and visited < max_nodes:
            node, depth = queue.pop(0)
            visited += 1
            value = value_of(node)
            # Strictly longer: a node holding ONLY the selection adds no context.
            if isinstance(value, str) and len(value) > len(selection) and selection in value:
                return value
            if depth < max_depth:
                children = children_of(node) or []
                queue.extend((child, depth + 1) for child in children[:max_children])
    return ""


class ContextProvider(abc.ABC):
    """Resolves the context (surrounding sentence) for a captured selection."""

    @abc.abstractmethod
    def resolve(self, selection: str) -> str:
        """Return the context for ``selection`` (falls back to ``selection`` itself)."""


class SelectionContextProvider(ContextProvider):
    """Universal fallback: the context IS the selection (no OS support needed)."""

    def resolve(self, selection: str) -> str:
        return selection


def warm_accessibility(pid: int) -> bool:
    """Switch on (and pre-build) an app's accessibility tree. Safe to call repeatedly.

    Chromium builds its AX tree lazily and Electron apps need ``AXManualAccessibility`` set
    before they expose one at all. Both take a moment, so callers run this OFF the capture path
    (e.g. when the frontmost app changes) — by gesture time the tree is ready and the lookup is
    instant. Returns ``True`` if the app already answers AX queries.

    Args:
        pid: The target application's process id.
    """
    try:  # pragma: no cover - macOS + Accessibility permission only
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
            AXUIElementSetAttributeValue,
            AXUIElementSetMessagingTimeout,
        )

        ax_app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(ax_app, _AX_TIMEOUT_SECONDS)
        # Electron/Chromium opt-in. Unsupported elsewhere (a plain error we ignore).
        AXUIElementSetAttributeValue(ax_app, "AXManualAccessibility", True)
        # The query itself is what makes Chromium start building the tree.
        err, focused = AXUIElementCopyAttributeValue(ax_app, "AXFocusedUIElement", None)
        return err == 0 and focused is not None
    except Exception:
        return False


def _thread_spawn(work: Callable[[], Any]) -> None:
    """Run ``work`` on a throwaway daemon thread (never blocks the Qt main thread)."""
    import threading

    threading.Thread(target=work, daemon=True).start()


class AccessibilityWarmer:
    """Warms each app's AX tree once, the first time that app comes to the front.

    Chromium needs ~2s from the first query before its tree is readable, so warming lazily at
    gesture time would still miss the very capture that triggered it. Polling the frontmost app
    instead means the tree is ready long before the user selects anything.

    Warming is dispatched to a background thread because an unresponsive app can hold the AX
    call for up to the messaging timeout; the caller (a Qt timer) must not block on that. Only
    the *pid* crosses the thread boundary — resolving the frontmost app is AppKit, which the
    caller does on the main thread.
    """

    def __init__(
        self,
        warm: Callable[[int], bool] = warm_accessibility,
        spawn: Callable[[Callable[[], Any]], None] = _thread_spawn,
    ) -> None:
        """Initialise the warmer.

        Args:
            warm: The per-pid warm-up call (injected for tests).
            spawn: Runs a callable off the caller's thread (injected for tests).
        """
        self._warm = warm
        self._spawn = spawn
        self._seen: set[int] = set()

    def ensure(self, pid: Optional[int]) -> bool:
        """Warm ``pid`` unless it was warmed already. Returns whether a warm was dispatched."""
        if pid is None or pid in self._seen:
            return False
        self._seen.add(pid)
        self._spawn(lambda: self._warm(pid))
        return True


class MacAXContextProvider(ContextProvider):
    """macOS Accessibility backend: the enclosing sentence of the selection.

    Finds the AX node that actually contains the selection (see the module docstring for why the
    focused element is not enough), then trims it to the enclosing sentence. Any failure —
    permission not granted, an app exposing no AX text, a PDF image, the selection not found —
    degrades to returning the selection unchanged.
    """

    def resolve(self, selection: str) -> str:
        selection = selection.strip()
        if not selection:
            return selection
        try:
            text = self._surrounding_text(selection)
        except Exception:  # pragma: no cover - needs a live macOS AX session
            return selection
        if not text:
            return selection
        index = text.find(selection)
        if index < 0:
            return selection
        return sentence_around(text, index, len(selection)) or selection

    @staticmethod
    def _surrounding_text(
        selection: str,
    ) -> str:  # pragma: no cover - macOS + Accessibility permission only
        """Return the text of the AX node containing ``selection`` (or ``""``)."""
        from AppKit import NSWorkspace
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
            AXUIElementSetMessagingTimeout,
        )

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return ""
        ax_app = AXUIElementCreateApplication(app.processIdentifier())
        AXUIElementSetMessagingTimeout(ax_app, _AX_TIMEOUT_SECONDS)

        def attribute(element: Any, name: str) -> Any:
            err, value = AXUIElementCopyAttributeValue(element, name, None)
            return value if err == 0 else None

        def value_of(element: Any) -> Optional[str]:
            value = attribute(element, "AXValue")
            return value if isinstance(value, str) else None

        def children_of(element: Any) -> list:
            return list(attribute(element, "AXChildren") or [])

        focused = attribute(ax_app, "AXFocusedUIElement")
        # Roots, most-likely first: the focused element, a few ancestors (the text may live in a
        # sibling subtree), then the focused window as a last resort.
        roots: list[Any] = []
        node = focused
        for _ in range(_MAX_ANCESTOR_HOPS + 1):
            if node is None:
                break
            roots.append(node)
            node = attribute(node, "AXParent")
        roots.append(attribute(ax_app, "AXFocusedWindow"))
        return find_text_containing(roots, selection, value_of, children_of)


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
