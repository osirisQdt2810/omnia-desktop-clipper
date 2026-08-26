"""Tests for the Windows UI Automation context backend.

Headless, on every platform: the COM boundary is exactly three small methods (`_text_of`,
`_children_of`, `_ancestors_of`), so the tests substitute those and exercise everything above
them. That is deliberate rather than convenient — the logic worth pinning is which node's text
is chosen and how it is trimmed, and none of that is COM.

The tree fakes model what was measured on a real desktop, not an idealised one: the focused
element carries no text at all (VS Code's `Pane`), and the text sits several levels down (a
`Document` of class `RichEditD2DPT` in Notepad).
"""

from __future__ import annotations

import pytest

from omnia_desktop_clipper.capture.context import ContextProvider
from omnia_desktop_clipper.capture.windows_context import WindowsUIAContextProvider


class _Node:
    """A UIA element stand-in: some text, some children."""

    def __init__(self, text: str = "", children: list | None = None) -> None:
        self.text = text
        self.children = children or []
        self.parent: _Node | None = None
        for child in self.children:
            child.parent = self


def _wire(provider: WindowsUIAContextProvider, *, focus=None, at_point=None) -> None:
    """Replace the three COM methods with tree walks over `_Node`s."""
    provider._text_of = lambda node: node.text  # type: ignore[method-assign]
    provider._children_of = lambda node: list(node.children)  # type: ignore[method-assign]

    def ancestors(node):
        chain, current = [node], node
        for _ in range(4):
            current = current.parent
            if current is None:
                break
            chain.append(current)
        return chain

    provider._ancestors_of = ancestors  # type: ignore[method-assign]
    provider._context_at_position = (  # type: ignore[method-assign]
        (
            lambda selection, position: provider._sentence_in(
                ancestors(at_point), selection
            )
        )
        if at_point is not None
        else (lambda selection, position: "")
    )
    provider._context_from_focus = (  # type: ignore[method-assign]
        (lambda selection: provider._sentence_in(ancestors(focus), selection))
        if focus is not None
        else (lambda selection: "")
    )


class TestItIsAContextProvider:
    def test_it_implements_the_seam(self) -> None:
        assert isinstance(WindowsUIAContextProvider(), ContextProvider)


class TestResolve:
    """The behaviour a card depends on: the sentence, not the word, not the whole document."""

    def test_it_returns_the_sentence_around_the_selection(self) -> None:
        provider = WindowsUIAContextProvider()
        document = _Node(
            "Intro sentence here. The quick brown fox jumps over it. And then more."
        )
        _wire(provider, focus=document)

        assert provider.resolve("fox") == "The quick brown fox jumps over it."

    def test_it_finds_text_several_levels_below_the_focused_element(self) -> None:
        """Measured shape: the focused element has no text and the document is four down.

        Trusting the focused element is what a naive port would do, and on this tree it returns
        nothing at all.
        """
        provider = WindowsUIAContextProvider()
        document = _Node("A sentence with the word inside it. Another one after.")
        focused = _Node("", [_Node("", [_Node("", [document])])])
        _wire(provider, focus=focused)

        assert provider.resolve("word") == "A sentence with the word inside it."

    def test_the_gesture_point_wins_over_focus(self) -> None:
        """The reason position is tried first: it disambiguates a repeated word.

        Both subtrees contain "fox". The one the user gestured at is the one that must be read,
        or a word appearing five times on a page resolves to whichever occurrence happens to be
        found first.
        """
        provider = WindowsUIAContextProvider()
        focused = _Node("The fox in the focused element. Ignore this.")
        pointed = _Node("The fox in the paragraph the user clicked. Read this.")
        _wire(provider, focus=focused, at_point=pointed)

        assert provider.resolve("fox", position=(10, 20)) == (
            "The fox in the paragraph the user clicked."
        )

    def test_it_falls_back_to_focus_when_the_point_yields_nothing(self) -> None:
        """A point can land on a wrapper, a scrollbar, or a window that has since closed."""
        provider = WindowsUIAContextProvider()
        focused = _Node("The fox is only in the focused element here.")
        _wire(provider, focus=focused, at_point=_Node(""))

        assert provider.resolve("fox", position=(10, 20)) == (
            "The fox is only in the focused element here."
        )

    def test_an_ancestor_is_searched_when_the_hit_carries_no_text(self) -> None:
        """A point lands on a cell inside a row; the sentence lives on the container."""
        provider = WindowsUIAContextProvider()
        cell = _Node("")
        _Node("The sentence lives on the container. Not on the cell.", [cell])
        _wire(provider, at_point=cell)

        assert provider.resolve("container", position=(1, 2)) == (
            "The sentence lives on the container."
        )


class TestItNeverMakesThingsWorse:
    """Every failure returns the selection. A capture without context is a worse card; a
    capture that raises is a lost one."""

    def test_no_text_anywhere_returns_the_selection(self) -> None:
        provider = WindowsUIAContextProvider()
        _wire(provider, focus=_Node("", [_Node(""), _Node("")]))

        assert provider.resolve("fox") == "fox"

    def test_text_that_does_not_contain_the_selection_returns_the_selection(
        self,
    ) -> None:
        provider = WindowsUIAContextProvider()
        _wire(provider, focus=_Node("Nothing relevant in this element at all."))

        assert provider.resolve("fox") == "fox"

    @pytest.mark.parametrize("selection", ["", "   ", "\n"])
    def test_an_empty_selection_is_returned_untouched(self, selection: str) -> None:
        provider = WindowsUIAContextProvider()
        _wire(provider, focus=_Node("Some text."))

        assert provider.resolve(selection) == selection.strip()

    def test_a_raising_com_layer_degrades_to_the_selection(self) -> None:
        """UIA is cross-process COM against apps that may be busy or closing."""
        provider = WindowsUIAContextProvider()

        def boom(*_args, **_kwargs):
            raise OSError("the RPC server is unavailable")

        provider._uia = boom  # type: ignore[method-assign]

        assert provider.resolve("fox", position=(1, 2)) == "fox"
        assert provider.resolve("fox") == "fox"


class TestTheSearchIsTheSharedOne:
    def test_it_reuses_find_text_containing_rather_than_its_own_walk(self) -> None:
        """The traversal, its node budget and its breadth-first guarantee are already written
        and already tested; a second copy here could drift from the macOS backend in how far it
        looks or which node it prefers."""
        import ast
        from pathlib import Path

        import omnia_desktop_clipper.capture.windows_context as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "find_text_containing" in called
        assert "sentence_around" in called

    def test_the_shallowest_node_holding_the_selection_wins(self) -> None:
        """Breadth-first, so a deep fragment does not beat the container that holds the word.

        The previous version asserted only that the answer contained "fox", which every
        possible answer does -- it could not have failed.
        """
        provider = WindowsUIAContextProvider()
        deep = _Node("fox")  # a fragment: the word alone, no sentence around it
        shallow = _Node("The container sentence has a fox in it. Trailing.", [deep])
        root = _Node("", [shallow])
        _wire(provider, focus=root)

        assert provider.resolve("fox") == "The container sentence has a fox in it."


class _FakeWalker:
    """The three tree-walk calls UIA's ControlViewWalker offers."""

    @staticmethod
    def GetFirstChildElement(node):  # noqa: N802 - UIA's own spelling
        return node.children[0] if node.children else None

    @staticmethod
    def GetNextSiblingElement(node):  # noqa: N802 - UIA's own spelling
        siblings = node.parent.children if node.parent else []
        index = siblings.index(node)
        return siblings[index + 1] if index + 1 < len(siblings) else None

    @staticmethod
    def GetParentElement(node):  # noqa: N802 - UIA's own spelling
        return node.parent


class _FakeAutomation:
    """The slice of IUIAutomation the provider calls, including both entry points.

    `ElementFromPoint` and `GetFocusedElement` are here because without them nothing tested
    them: a wrong method name or a bad POINT type is swallowed by the `except Exception` that
    makes this backend degrade gracefully, so it would silently return selection-only -- the
    exact bug this backend fixes, with a green suite.
    """

    ControlViewWalker = _FakeWalker()

    def __init__(self, *, at_point=None, focused=None) -> None:
        self._at_point = at_point
        self._focused = focused
        self.points: list = []

    def ElementFromPoint(self, point):  # noqa: N802 - UIA's own spelling
        self.points.append((point.x, point.y))
        return self._at_point

    def GetFocusedElement(self):  # noqa: N802 - UIA's own spelling
        return self._focused


class _FakeModule:
    UIA_TextPatternId = 1
    UIA_ValuePatternId = 2
    IUIAutomationTextPattern = "text"
    IUIAutomationValuePattern = "value"


class TestTheComAdjacentWalksThemselves:
    """These fake at the COM BOUNDARY (`_uia`) so the walks run for real.

    The tests above replace `_ancestors_of` and `_children_of` wholesale, which is right for
    exercising the logic above them but means a regression INSIDE those two methods cannot fail
    anything — verified by mutation: deleting the ancestor walk left the whole suite green. A
    fake one layer too high is a test that cannot see the code it is named after.
    """

    @staticmethod
    def _provider_over(tree):
        provider = WindowsUIAContextProvider()
        provider._uia = lambda: (_FakeAutomation(), _FakeModule())  # type: ignore[method-assign]
        provider._text_of = lambda node: node.text  # type: ignore[method-assign]
        return provider

    def test_ancestors_climbs_and_stops_at_the_root(self) -> None:
        leaf = _Node("leaf")
        middle = _Node("middle", [leaf])
        root = _Node("root", [middle])
        provider = self._provider_over(root)

        chain = provider._ancestors_of(leaf)

        assert [n.text for n in chain] == ["leaf", "middle", "root"]

    def test_ancestors_is_bounded(self) -> None:
        """A deep tree must not be climbed to the desktop; the sentence is never up there."""
        node = _Node("0")
        for depth in range(1, 12):
            node = _Node(str(depth), [node])
        deepest = node
        while deepest.children:
            deepest = deepest.children[0]
        provider = self._provider_over(node)

        assert len(provider._ancestors_of(deepest)) <= 5

    def test_children_enumerates_every_sibling(self) -> None:
        root = _Node("root", [_Node("a"), _Node("b"), _Node("c")])
        provider = self._provider_over(root)

        assert [n.text for n in provider._children_of(root)] == ["a", "b", "c"]

    def test_a_leaf_has_no_children(self) -> None:
        leaf = _Node("leaf")
        assert self._provider_over(leaf)._children_of(leaf) == []

    def test_the_real_ancestor_walk_is_what_finds_the_sentence(self) -> None:
        """End to end through the REAL walks: a point lands on a text-less cell.

        This is the test that mutation showed was missing — remove the ancestor climb and it
        fails, because the sentence lives on the container the cell sits in.
        """
        cell = _Node("")
        _Node("The sentence lives on the container. Not on the cell.", [cell])
        provider = self._provider_over(cell)

        assert provider._sentence_in(provider._ancestors_of(cell), "container") == (
            "The sentence lives on the container."
        )


class TestTheTextCascade:
    """`_text_of` tries three routes because no single one covers the apps people read in."""

    class _Element:
        def __init__(self, *, text=None, value=None, name=""):
            self._patterns = {}
            if text is not None:
                self._patterns[1] = _FakePattern(text)
            if value is not None:
                self._patterns[2] = _FakePattern(value)
            self.CurrentName = name

        def GetCurrentPattern(self, pattern_id):  # noqa: N802 - UIA's own spelling
            return self._patterns.get(pattern_id)

    @staticmethod
    def _provider():
        provider = WindowsUIAContextProvider()
        provider._uia = lambda: (_FakeAutomation(), _FakeModule())  # type: ignore[method-assign]
        return provider

    def test_the_text_pattern_wins(self) -> None:
        """Notepad answers this one with the WHOLE document; Name is just the control label."""
        element = self._Element(text="the document", value="v", name="Text editor")
        assert self._provider()._text_of(element) == "the document"

    def test_the_value_pattern_is_next(self) -> None:
        element = self._Element(value="the field value", name="Address")
        assert self._provider()._text_of(element) == "the field value"

    def test_name_is_the_last_resort(self) -> None:
        """Chromium's accessible leaves carry their text here with both patterns empty."""
        element = self._Element(name="the accessible name")
        assert self._provider()._text_of(element) == "the accessible name"

    def test_an_empty_pattern_falls_through_rather_than_winning(self) -> None:
        """An empty TextPattern must not shadow a Name that has the text.

        Measured: Chromium leaves expose a TextPattern that answers "" — returning the first
        NON-NULL route rather than the first non-empty one loses their text entirely.
        """
        element = self._Element(text="", value="", name="the real text")
        assert self._provider()._text_of(element) == "the real text"

    def test_everything_failing_is_an_empty_string(self) -> None:
        class _Hostile:
            def GetCurrentPattern(self, _id):  # noqa: N802 - UIA's own spelling
                raise OSError("RPC server unavailable")

            @property
            def CurrentName(self):  # noqa: N802 - UIA's own spelling
                raise OSError("RPC server unavailable")

        assert self._provider()._text_of(_Hostile()) == ""


class _FakePattern:
    def __init__(self, text):
        self._text = text

    def QueryInterface(self, _iface):  # noqa: N802 - COM's own spelling
        return self

    @property
    def DocumentRange(self):  # noqa: N802 - UIA's own spelling
        return self

    def GetText(self, _max):  # noqa: N802 - UIA's own spelling
        return self._text

    @property
    def CurrentValue(self):  # noqa: N802 - UIA's own spelling
        return self._text


class _RangeElement:
    """An element whose TextPattern answers RangeFromPoint, the way a text control does."""

    def __init__(self, paragraphs, *, y_step: int = 20, top: int = 100) -> None:
        self._paragraphs = paragraphs
        self._y_step = y_step
        self._top = top
        self.CurrentName = "Text editor"

    def GetCurrentPattern(self, pattern_id):  # noqa: N802 - UIA's own spelling
        if pattern_id != 1:
            return None
        return _RangePattern(self)

    def paragraph_at(self, y: int) -> str:
        index = max(0, (y - self._top) // self._y_step)
        return self._paragraphs[min(index, len(self._paragraphs) - 1)]


class _RangePattern:
    def __init__(self, element) -> None:
        self._element = element
        self._text = ""

    def QueryInterface(self, _iface):  # noqa: N802 - COM's own spelling
        return self

    @property
    def DocumentRange(self):  # noqa: N802 - UIA's own spelling
        return self

    def RangeFromPoint(self, point):  # noqa: N802 - UIA's own spelling
        clone = _RangePattern(self._element)
        clone._text = self._element.paragraph_at(point.y)
        return clone

    def ExpandToEnclosingUnit(self, _unit):  # noqa: N802 - UIA's own spelling
        return None

    def GetText(self, _max):  # noqa: N802 - UIA's own spelling
        return self._text


class TestTheEntryPoints:
    """`_context_at_position` and `_context_from_focus` are the whole path in, and had no test."""

    @staticmethod
    def _provider(*, at_point=None, focused=None):
        provider = WindowsUIAContextProvider()
        automation = _FakeAutomation(at_point=at_point, focused=focused)
        provider._uia = lambda: (automation, _FakeModule())  # type: ignore[method-assign]
        return provider, automation

    def test_the_point_route_reaches_the_element_under_the_cursor(self) -> None:
        node = _Node("The sentence at the point. Another one.")
        provider, automation = self._provider(at_point=node)
        provider._text_of = lambda n: n.text  # type: ignore[method-assign]

        assert provider._context_at_position("point", (17, 42)) == (
            "The sentence at the point."
        )
        assert automation.points == [
            (17, 42)
        ], "the gesture point was not passed through"

    def test_the_focus_route_reaches_the_focused_element(self) -> None:
        node = _Node("The sentence in focus. Another one.")
        provider, _automation = self._provider(focused=node)
        provider._text_of = lambda n: n.text  # type: ignore[method-assign]

        assert provider._context_from_focus("focus") == "The sentence in focus."

    def test_no_element_under_the_point_is_not_an_error(self) -> None:
        provider, _automation = self._provider(at_point=None)
        assert provider._context_at_position("anything", (1, 2)) == ""

    def test_no_focused_element_is_not_an_error(self) -> None:
        provider, _automation = self._provider(focused=None)
        assert provider._context_from_focus("anything") == ""


class TestRangeFromPoint:
    """The route that makes the gesture point mean something.

    Without it the point bought nothing for a native text control: `GetText(-1)` returns the
    WHOLE document and the search then takes the FIRST occurrence, so clicking the second
    paragraph returned the first paragraph's sentence. Verified live on a real Notepad document
    where the same word resolves to two different sentences depending on where it was clicked.
    """

    @staticmethod
    def _provider(paragraphs):
        provider = WindowsUIAContextProvider()
        automation = _FakeAutomation(at_point=_RangeElement(paragraphs))
        provider._uia = lambda: (automation, _FakeModule())  # type: ignore[method-assign]
        return provider

    def test_a_repeated_word_resolves_to_the_paragraph_clicked(self) -> None:
        provider = self._provider(
            [
                "The quick brown fox jumps over the dog.",
                "A second sentence mentions the fox again, elsewhere.",
            ]
        )

        assert provider.resolve("fox", position=(5, 100)) == (
            "The quick brown fox jumps over the dog."
        )
        assert provider.resolve("fox", position=(5, 120)) == (
            "A second sentence mentions the fox again, elsewhere."
        )

    def test_a_paragraph_without_the_word_yields_nothing_rather_than_something_wrong(
        self,
    ) -> None:
        """The point landed on a margin or a different column. Silence beats a wrong sentence."""
        provider = self._provider(["No match here at all.", "Nor here."])

        assert provider._paragraph_at_point("fox", (5, 100)) == ""

    def test_an_element_without_a_text_pattern_falls_through(self) -> None:
        """Most of Chromium. The caller then searches the subtree instead."""
        provider = WindowsUIAContextProvider()
        automation = _FakeAutomation(at_point=_Node("no text pattern here"))
        provider._uia = lambda: (automation, _FakeModule())  # type: ignore[method-assign]

        assert provider._paragraph_at_point("fox", (1, 2)) == ""


class TestItDoesNotSitOnTheQtThread:
    """ "Never block the Qt event loop": the "+" is shown right after resolve() returns."""

    def test_the_sibling_walk_stops_at_the_cap(self) -> None:
        """Each GetNextSiblingElement is a cross-process round trip.

        A 5,000-row list walked to completion is 5,000 of them before the "+" appears, and the
        search's own cap then discards all but 60 -- after they have been paid for.
        """
        from omnia_desktop_clipper.capture.context import _MAX_CHILDREN

        root = _Node("root", [_Node(str(i)) for i in range(5000)])
        provider = WindowsUIAContextProvider()
        provider._uia = lambda: (_FakeAutomation(), _FakeModule())  # type: ignore[method-assign]

        assert len(provider._children_of(root)) == _MAX_CHILDREN

    def test_a_spent_budget_stops_the_walk(self) -> None:
        """The deadline bounds the PAIR of routes, which max_nodes cannot: it is per search."""
        import time

        root = _Node("root", [_Node(str(i)) for i in range(50)])
        provider = WindowsUIAContextProvider()
        provider._uia = lambda: (_FakeAutomation(), _FakeModule())  # type: ignore[method-assign]
        provider._deadline = time.monotonic() - 1  # already spent

        assert len(provider._children_of(root)) == 1

    def test_resolve_sets_and_clears_the_deadline(self) -> None:
        provider = WindowsUIAContextProvider()
        seen = {}

        def spy(selection):
            seen["deadline"] = provider._deadline
            return ""

        provider._context_from_focus = spy  # type: ignore[method-assign]

        provider.resolve("word")

        assert seen["deadline"] is not None, "no budget was set for the capture"
        assert provider._deadline is None, "the budget outlived the capture"
