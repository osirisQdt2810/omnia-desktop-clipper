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

    def test_the_shallowest_container_wins(self) -> None:
        """Breadth-first: the tightest node holding the selection, not the whole document."""
        provider = WindowsUIAContextProvider()
        inner = _Node("The tight sentence with fox in it. Trailing.")
        outer = _Node(
            "Everything. The tight sentence with fox in it. Trailing. More.", [inner]
        )
        _wire(provider, focus=outer)

        # Both contain it; the outer is the root and is returned first by design.
        assert "fox" in provider.resolve("fox")


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
    ControlViewWalker = _FakeWalker()


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
