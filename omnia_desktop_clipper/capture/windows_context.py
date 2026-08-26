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

1. **Prefer the point the user gestured at.** ``ElementFromPoint`` resolves to the smallest
   element at a screen coordinate, which is the paragraph they were reading — that is what makes
   a repeated word resolvable rather than guessed.
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

from typing import Any, Optional

from .base import SelectionCapture  # noqa: F401  (documents the sibling seam)
from .context import ContextProvider, find_text_containing, sentence_around

#: ``CUIAutomation``'s class id. Hard-coded because there is nothing to look it up from: the
#: automation client is the entry point to everything else here.
_CUIAUTOMATION_CLSID = "{ff48dba4-60ef-4201-aa87-54103eef594e}"

#: How many ancestors above the hit element to try when the hit itself carries no text. A point
#: can land on a wrapper (a cell inside a row inside a table); the text is usually one or two
#: levels up, and going further starts returning the whole window.
_ANCESTOR_LEVELS = 4


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
            self._automation = comtypes.client.CreateObject(
                _CUIAUTOMATION_CLSID, interface=module.IUIAutomation
            )
            self._module = module
        return self._automation, self._module

    def _text_of(self, element: Any) -> str:
        """The element's text, by the first route that yields any.

        Three routes because no single one covers the apps people read in, measured on this
        machine: Notepad answers ``TextPattern`` (the whole document), edit controls answer
        ``ValuePattern``, and Chromium's accessible leaves carry their text in ``Name`` alone
        with both patterns empty. Trying them in this order returns the most text available
        rather than the first thing that happens to be non-null.
        """
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
        children = []
        try:
            walker = automation.ControlViewWalker
            child = walker.GetFirstChildElement(element)
            while child:
                children.append(child)
                child = walker.GetNextSiblingElement(child)
        except Exception:
            return children
        return children

    def _ancestors_of(self, element: Any) -> list:
        """``element`` followed by a few of its ancestors, nearest first."""
        automation, _module = self._uia()
        chain = [element]
        try:
            walker = automation.ControlViewWalker
            node = element
            for _ in range(_ANCESTOR_LEVELS):
                node = walker.GetParentElement(node)
                if not node:
                    break
                chain.append(node)
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
        # The gesture POINT first, for the same reason macOS tries it first: the element under
        # it is the paragraph the user was actually reading, so a word that appears five times
        # on the page resolves to the occurrence they meant.
        if position is not None:
            located = self._context_at_position(selection, position)
            if located:
                return located
        located = self._context_from_focus(selection)
        return located or selection

    def _context_at_position(
        self, selection: str, position: tuple[int, int]
    ) -> str:  # pragma: no cover - needs a live Windows desktop
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

    def _context_from_focus(
        self, selection: str
    ) -> str:  # pragma: no cover - needs a live Windows desktop
        """The sentence around ``selection`` in the focused element's subtree, or ``""``."""
        try:
            automation, _module = self._uia()
            element = automation.GetFocusedElement()
            if not element:
                return ""
            return self._sentence_in(self._ancestors_of(element), selection)
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
