"""Windows context: the sentence around the selection, via UI Automation.

The macOS backend reads the Accessibility tree; this is its Windows counterpart, and it exists
because until now Windows had none. ``build_context_provider`` handed every non-macOS platform
:class:`~omnia_desktop_clipper.capture.context.SelectionContextProvider`, whose "context" IS the
selection — so a Windows user's card carried the word twice and never the sentence it came from,
while a macOS user's carried the real sentence. Same app, same gesture, quietly different cards.

Why the focused element is not enough (the same lesson, restated for UIA)
------------------------------------------------------------------------
Measured on this machine: the focused element in VS Code is a ``Pane`` of class
``Chrome_WidgetWin_1`` whose Name is empty and which exposes no TextPattern — the text lives in
descendants. In Notepad the focused window's text is four levels down, in a ``Document`` of class
``RichEditD2DPT``. So this does the same two things the macOS backend does:

1. **Read the text AT the point the user gestured at.** ``RangeFromPoint`` answers with a
   range at the cursor, which expanded to its paragraph is the text they were actually looking
   at. This is the only route that disambiguates a repeated word: a native text control answers
   ``GetText(-1)`` with the WHOLE document, so searching it returns the FIRST occurrence
   wherever it happens to be. Verified on a two-paragraph document where "fox" appears twice —
   clicking each paragraph returns its own sentence. ``ElementFromPoint`` alone is the weaker
   second try, for controls with no TextPattern.
2. **Search the subtree** for the node whose text CONTAINS the selection, rather than trusting
   whichever element has focus.

The search itself is not written here. It is
:func:`~omnia_desktop_clipper.capture.context.find_text_containing`, already pure and already
tested, which takes the accessors as arguments — so this module supplies UIA-shaped
``value_of``/``children_of`` and inherits the traversal, its node budget and its breadth-first
guarantee unchanged.

Everything is best-effort. UI Automation is a cross-process COM protocol against applications
that may be busy, closing, or hostile to introspection; any failure degrades to returning the
selection, exactly as the macOS backend does. A capture that loses its context is a worse card,
not a crash.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from .context import (
    _AX_TIMEOUT_SECONDS,
    _MAX_ANCESTOR_HOPS,
    _MAX_CHILDREN,
    ContextProvider,
    find_text_containing,
    sentence_around,
)
from .pdf_context import PdfTextReader, is_pdf, parse_page_number, unique_occurrence

#: ``CUIAutomation``'s class id. Hard-coded because there is nothing to look it up from: the
#: automation client is the entry point to everything else here.
_CUIAUTOMATION_CLSID = "{ff48dba4-60ef-4201-aa87-54103eef594e}"

#: ``CUIAutomation8``'s class id, which yields ``IUIAutomation2`` and its timeouts. Tried first;
#: a machine that only offers the older client still works, just without them.
_CUIAUTOMATION8_CLSID = "{e22ad333-b25f-460c-83d0-0581107395c9}"

#: Total wall clock one resolve may spend inside UIA, across BOTH routes.
#:
#: The per-call timeouts above are not enough on their own. The expensive case is the one this
#: module documents as cheap: in a PDF viewer no node exposes text, so the point route spends
#: its whole node budget finding nothing, and then the focus route starts a FRESH budget --
#: ``max_nodes`` is per search, not per capture. A deadline is what bounds the pair, and it is
#: what keeps the "+" appearing promptly rather than after the slowest app on the machine.
_UIA_BUDGET_SECONDS = 1.0

#: What joins page texts when the page number is unknown. A blank line, so a sentence
#: cannot be read across a page boundary that was never on screen together.
PAGE_SEPARATOR = chr(10) + chr(10)

#: UIA's TextUnit_Paragraph. Used with ``RangeFromPoint`` to read the text AT the cursor.
_TEXT_UNIT_PARAGRAPH = 4


class WindowsUIAContextProvider(ContextProvider):
    """UI Automation backend: the enclosing sentence of the selection.

    Mirrors :class:`~omnia_desktop_clipper.capture.context.MacAXContextProvider`, including its
    failure contract: any problem at any step returns the selection unchanged.
    """

    def __init__(self) -> None:
        # The automation client is created once and reused. Building it per capture costs a COM
        # activation on the Qt main thread for no benefit -- the object is stateless.
        self._automation: Any = None
        self._module: Any = None
        # Set for the duration of one resolve(); see _UIA_BUDGET_SECONDS.
        self._deadline: Optional[float] = None
        # Caches the opened PDF between captures; re-opening a 90-page document each time would
        # be wasteful, and the reader keys on mtime so an edited file is still re-read.
        self._pdf_reader = PdfTextReader()

    # -- COM plumbing -------------------------------------------------------------------------

    def _uia(self) -> tuple[Any, Any]:
        """Return ``(automation, module)``, creating them on first use.

        ``comtypes.client.GetModule`` generates (or loads) the typed wrapper for
        ``UIAutomationCore.dll``. It is done lazily so importing this module costs nothing on a
        machine that never captures, and so a machine where UIA is unavailable fails at the one
        call site that can degrade rather than at import time.
        """
        if self._automation is None:
            import comtypes.client

            module = comtypes.client.GetModule("UIAutomationCore.dll")
            self._automation = self._create_client(comtypes.client, module)
            self._module = module
        return self._automation, self._module

    @staticmethod
    def _create_client(client: Any, module: Any) -> Any:
        """The automation client, with per-call timeouts when this Windows offers them.

        ``CUIAutomation8`` yields ``IUIAutomation2``, which has ``ConnectionTimeout`` and
        ``TransactionTimeout``. Without them a call into a hung application blocks on COM's own
        RPC timeout, which is far longer than anything acceptable here -- the "+" is shown right
        after this returns, so a stuck app would stall the gesture. macOS sets the same kind of
        bound with ``AXUIElementSetMessagingTimeout``.

        Falls back to the plain client rather than failing: an older Windows still gets context,
        bounded by the wall-clock deadline instead.
        """
        milliseconds = int(_AX_TIMEOUT_SECONDS * 1000)
        try:
            automation = client.CreateObject(
                _CUIAUTOMATION8_CLSID, interface=module.IUIAutomation2
            )
            automation.ConnectionTimeout = milliseconds
            automation.TransactionTimeout = milliseconds
            return automation
        except Exception:
            return client.CreateObject(
                _CUIAUTOMATION_CLSID, interface=module.IUIAutomation
            )

    def _out_of_time(self) -> bool:
        """Whether this capture has spent its UIA budget."""
        return self._deadline is not None and time.monotonic() > self._deadline

    def _text_of(self, element: Any) -> str:
        """The element's text, by the first route that yields any.

        Three routes because no single one covers the apps people read in, measured on this
        machine: Notepad answers ``TextPattern`` (the whole document), edit controls answer
        ``ValuePattern``, and Chromium's accessible leaves carry their text in ``Name`` alone
        with both patterns empty. Trying them in this order returns the most text available
        rather than the first thing that happens to be non-null.
        """
        if self._out_of_time():
            # _text_of is the bulk of the cost -- up to three cross-process round trips per
            # node -- so a budget that only guarded the sibling walk was not the bound its
            # own docstring claimed.
            return ""
        _automation, module = self._uia()
        try:
            pattern = element.GetCurrentPattern(module.UIA_TextPatternId)
            if pattern:
                text = pattern.QueryInterface(
                    module.IUIAutomationTextPattern
                ).DocumentRange.GetText(-1)
                if text:
                    return str(text)
        except Exception:
            pass
        try:
            pattern = element.GetCurrentPattern(module.UIA_ValuePatternId)
            if pattern:
                text = pattern.QueryInterface(
                    module.IUIAutomationValuePattern
                ).CurrentValue
                if text:
                    return str(text)
        except Exception:
            pass
        try:
            return str(element.CurrentName or "")
        except Exception:
            return ""

    def _children_of(self, element: Any) -> list:
        """The element's children in the CONTROL view.

        The control view rather than the raw view: the raw tree includes layout nodes that carry
        no text and multiply the node count for nothing, and the search has a fixed budget to
        spend.
        """
        automation, _module = self._uia()
        children: list = []
        try:
            walker = automation.ControlViewWalker
            child = walker.GetFirstChildElement(element)
            # STOP AT THE CAP, not after. Each GetNextSiblingElement is a cross-process COM
            # round trip, so walking a 5,000-row Explorer list or a day-old chat transcript to
            # completion costs 5,000 of them -- on the Qt main thread, before the "+" appears.
            # The search's own max_children applies only to what it is HANDED, so it would
            # discard 4,940 results that had already been paid for. macOS never had this
            # problem: AXChildren returns the whole array in one marshalled call.
            while child and len(children) < _MAX_CHILDREN:
                children.append(child)
                if self._out_of_time():
                    break
                child = walker.GetNextSiblingElement(child)
        except Exception:
            return children
        return children

    def _ancestors_of(self, element: Any) -> list:
        """``element`` followed by a few of its ancestors, STOPPING AT THE DESKTOP.

        The stop is the whole point. In UIA the parent of a top-level window is the desktop
        root, whose children are every top-level window of every running application — so an
        unbounded climb hands the search a root from which it can descend into other people's
        apps. The first node whose text contains the word then wins, and since ``_text_of``
        falls back to ``CurrentName``, a background browser window titled
        "policy - Google Search" is eligible. The card would be stamped with a sentence from an
        application the user was never reading, and it passes every downstream check because
        the word really is in it.

        macOS cannot do this and that is why the bug is Windows-only: its climb starts from
        ``AXUIElementCreateApplication(pid)``, so ``AXParent`` tops out at that application.
        This restores the same ceiling.

        The last element of the chain is therefore the top-level window, which is also the
        equivalent of the ``AXFocusedWindow`` root macOS appends — deliberately, not by
        accident.
        """
        automation, _module = self._uia()
        chain = [element]
        try:
            walker = automation.ControlViewWalker
            root = automation.GetRootElement()
            node = element
            for _ in range(_MAX_ANCESTOR_HOPS):
                parent = walker.GetParentElement(node)
                if not parent or automation.CompareElements(parent, root):
                    break
                chain.append(parent)
                node = parent
        except Exception:
            pass
        return chain

    # -- the contract -------------------------------------------------------------------------

    def resolve(
        self, selection: str, position: Optional[tuple[int, int]] = None
    ) -> str:
        selection = selection.strip()
        if not selection:
            return selection
        self._deadline = time.monotonic() + _UIA_BUDGET_SECONDS
        try:
            if position is not None:
                # The text AT the point, when the control can give it. This is the only route
                # that genuinely disambiguates a repeated word -- see _paragraph_at_point.
                located = self._paragraph_at_point(selection, position)
                if located:
                    return located
                located = self._context_at_position(selection, position)
                if located:
                    return located
            if not self._out_of_time():
                located = self._context_from_focus(selection)
                if located:
                    return located
            # UI Automation gave nothing. In a PDF that is not a gap to work around but a hard
            # wall -- measured, Foxit exposes no text for any node in its window -- so read the
            # document itself, exactly as the macOS backend does with Preview.
            return self._pdf_context(selection) or selection
        finally:
            self._deadline = None

    def _paragraph_at_point(self, selection: str, position: tuple[int, int]) -> str:
        """The sentence around the point, read from the range UIA reports AT that point.

        This exists because searching an element's whole text cannot honour the gesture. A
        native text control answers ``GetText(-1)`` with the ENTIRE document, and the search
        then takes ``text.find(selection)`` -- the FIRST occurrence, wherever it is. On the
        two-paragraph document used to verify this backend, "fox" appears twice and the first
        occurrence was returned no matter which one the user double-clicked.

        ``RangeFromPoint`` answers with a degenerate range at the cursor, and expanding it to
        its enclosing paragraph gives the text the reader is actually looking at. That is what
        the point was always supposed to buy.

        Returns ``""`` for a control with no TextPattern (most of Chromium), where the caller
        falls back to searching the subtree.
        """
        try:
            from ctypes.wintypes import POINT

            automation, module = self._uia()
            element = automation.ElementFromPoint(
                POINT(int(position[0]), int(position[1]))
            )
            if not element:
                return ""
            raw = element.GetCurrentPattern(module.UIA_TextPatternId)
            if not raw:
                return ""
            pattern = raw.QueryInterface(module.IUIAutomationTextPattern)
            text_range = pattern.RangeFromPoint(
                POINT(int(position[0]), int(position[1]))
            )
            if not text_range:
                return ""
            text_range.ExpandToEnclosingUnit(_TEXT_UNIT_PARAGRAPH)
            paragraph = str(text_range.GetText(-1) or "")
        except Exception:
            return ""
        index = paragraph.find(selection)
        if index < 0:
            # The paragraph under the cursor does not contain the word -- the point landed on a
            # margin, or the selection spans a break. Say nothing rather than something wrong.
            return ""
        return sentence_around(paragraph, index, len(selection))

    def _context_at_position(self, selection: str, position: tuple[int, int]) -> str:
        """The sentence around ``selection`` under ``position``, or ``""``."""
        try:
            from ctypes.wintypes import POINT

            automation, _module = self._uia()
            element = automation.ElementFromPoint(
                POINT(int(position[0]), int(position[1]))
            )
            if not element:
                return ""
            return self._sentence_in(self._ancestors_of(element), selection)
        except Exception:
            return ""

    def _context_from_focus(self, selection: str) -> str:
        """The sentence around ``selection`` in the focused element's subtree, or ``""``."""
        try:
            automation, _module = self._uia()
            element = automation.GetFocusedElement()
            if not element:
                return ""
            return self._sentence_in(self._ancestors_of(element), selection)
        except Exception:
            return ""

    def _pdf_context(self, selection: str) -> str:
        """The enclosing sentence read from the open PDF, or ``""``.

        The page is what makes this safe. A word like "inference" occurs 154 times in a real
        dissertation, and a whole-document search happily returns the title page -- a plausible
        sentence the reader never saw. Restricted to the page on screen, the same lookup returns
        what they were looking at. macOS gets that page from the window title and so does this;
        the difference is only where the FILE comes from (see :mod:`windows_pdf`).
        """
        from ..platform import frontmost_pid
        from .windows_pdf import open_pdf_for

        try:
            title = self._foreground_title()
            pid = frontmost_pid()
            if not title or not pid:
                return ""
            path = open_pdf_for(pid, title)
            if not path or not is_pdf(path):
                return ""
            page = parse_page_number(title)
            texts = self._pdf_reader.page_texts(path, page)
            if not texts:
                return ""
            # With a known page there is exactly one text (that page). Without one, every page
            # is joined so uniqueness is judged across the WHOLE document -- either way the word
            # must be unambiguous before its sentence is used.
            haystack = texts[0] if page is not None else PAGE_SEPARATOR.join(texts)
            index = unique_occurrence(haystack, selection)
            if index < 0:
                return ""
            return sentence_around(haystack, index, len(selection))
        except Exception:
            return ""

    def _foreground_title(self) -> str:
        """The foreground window's title, or ``""``.

        Read through UIA rather than ``GetWindowText`` because the client is already built and
        the title is a property of the element we would otherwise have to fetch a second way.
        """
        try:
            automation, _module = self._uia()
            element = automation.GetFocusedElement()
            if not element:
                return ""
            # Climb to the top-level window: the focused element is usually a control inside it,
            # and only the window carries the document title.
            chain = self._ancestors_of(element)
            for node in reversed(chain):
                name = str(node.CurrentName or "")
                if name:
                    return name
            return ""
        except Exception:
            return ""

    def _sentence_in(self, roots: list, selection: str) -> str:
        """Search ``roots`` for the node containing ``selection`` and trim to its sentence.

        The search is the shared pure one, so this cannot drift from the macOS backend in how
        far it looks or which node it prefers -- only in what the accessors read.
        """
        text = find_text_containing(roots, selection, self._text_of, self._children_of)
        if not text:
            return ""
        index = text.find(selection)
        if index < 0:
            return ""
        return sentence_around(text, index, len(selection))
