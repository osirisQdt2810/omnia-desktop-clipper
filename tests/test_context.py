"""Tests for the pure ``capture.context`` module (sentence extraction + providers)."""

from __future__ import annotations

from omnia_desktop_clipper.capture.context import (
    AccessibilityWarmer,
    ContextProvider,
    MacAXContextProvider,
    SelectionContextProvider,
    build_context_provider,
    find_text_containing,
    sentence_around,
)


class TestSentenceAround:
    def test_extracts_enclosing_sentence(self) -> None:
        text = "First one. The quick brown fox jumps. Third one."
        got = sentence_around(text, text.index("fox"), len("fox"))
        assert got == "The quick brown fox jumps."

    def test_sentence_crosses_line_breaks(self) -> None:
        # The point of the change: a sentence wrapped over lines is ONE sentence, not "this line".
        text = "Intro. The quick brown\nfox jumps over it. Third one."
        got = sentence_around(text, text.index("fox"), len("fox"))
        assert got == "The quick brown fox jumps over it."

    def test_stops_at_a_paragraph_break(self) -> None:
        text = "First para about the fox\n\nSecond para entirely unrelated"
        got = sentence_around(text, text.index("fox"), len("fox"))
        assert got == "First para about the fox"

    def test_falls_back_to_the_line_when_there_is_no_full_stop(self) -> None:
        # Code / lists / headings have no sentence enders; returning the whole buffer would be
        # useless, so the selection's own line is used instead.
        line = "def compute_target_value(self) -> int:"
        text = ("x" * 500) + "\n" + line + "\n" + ("y" * 500)
        got = sentence_around(text, text.index("target"), len("target"))
        assert got == line

    def test_collapses_internal_whitespace(self) -> None:
        text = "The  fox\n   jumps."
        assert sentence_around(text, text.index("fox"), 3) == "The fox jumps."

    def test_first_sentence(self) -> None:
        text = "Alpha beta gamma. Next sentence."
        assert sentence_around(text, 0, len("Alpha")) == "Alpha beta gamma."

    def test_out_of_range_returns_empty(self) -> None:
        assert sentence_around("short", 100, 3) == ""
        assert sentence_around("short", -1, 3) == ""


class TestProviders:
    def test_selection_provider_returns_selection(self) -> None:
        assert SelectionContextProvider().resolve("hello world") == "hello world"

    def test_build_is_fallback_off_macos(self) -> None:
        assert isinstance(build_context_provider("linux"), SelectionContextProvider)
        assert isinstance(build_context_provider("win32"), SelectionContextProvider)

    def test_build_is_ax_on_macos(self) -> None:
        assert isinstance(build_context_provider("darwin"), MacAXContextProvider)

    def test_both_are_context_providers(self) -> None:
        assert isinstance(SelectionContextProvider(), ContextProvider)
        assert isinstance(MacAXContextProvider(), ContextProvider)


class TestMacAXResolve:
    """resolve() uses the focused text when available and always degrades to the selection."""

    def test_extracts_sentence_when_surrounding_text_available(self, monkeypatch) -> None:
        prov = MacAXContextProvider()
        monkeypatch.setattr(
            prov, "_surrounding_text", lambda selection: "Intro. The word is here. End."
        )
        assert prov.resolve("word") == "The word is here."

    def test_falls_back_when_ax_unavailable(self, monkeypatch) -> None:
        prov = MacAXContextProvider()

        def boom(selection: str) -> str:
            raise RuntimeError("no Accessibility permission")

        monkeypatch.setattr(prov, "_surrounding_text", boom)
        assert prov.resolve("word") == "word"

    def test_falls_back_when_selection_not_in_surrounding_text(self, monkeypatch) -> None:
        prov = MacAXContextProvider()
        monkeypatch.setattr(
            prov, "_surrounding_text", lambda selection: "completely different text"
        )
        assert prov.resolve("word") == "word"

    def test_falls_back_when_no_container_found(self, monkeypatch) -> None:
        # The AX search found nothing (PDF image, unsupported app) -> the selection stands.
        prov = MacAXContextProvider()
        monkeypatch.setattr(prov, "_surrounding_text", lambda selection: "")
        assert prov.resolve("word") == "word"

    def test_empty_selection_returns_empty(self) -> None:
        assert MacAXContextProvider().resolve("   ") == ""


class _Node:
    """A fake AX node: a text value plus children (mirrors the real AXValue/AXChildren pair)."""

    def __init__(self, value=None, children=None):
        self.value = value
        self.children = children or []


def _value_of(node):
    return node.value


def _children_of(node):
    return node.children


class TestFindTextContaining:
    """The BFS that locates the node actually holding the selection (see module docstring)."""

    def test_finds_value_on_the_focused_node_itself(self):
        # TextEdit shape: the focused text area carries the whole text.
        focused = _Node("The boy plunged in anyway. He loved it.")
        assert find_text_containing(
            [focused], "plunged", _value_of, _children_of
        ) == "The boy plunged in anyway. He loved it."

    def test_finds_text_in_a_descendant_when_focused_value_is_empty(self):
        # Chrome shape: focused AXWebArea has an EMPTY value; the text is a static-text child.
        para = _Node("The water was cold, but the boy plunged in anyway.")
        web_area = _Node("", [_Node(""), _Node("", [para])])
        assert find_text_containing([web_area], "plunged", _value_of, _children_of) == para.value

    def test_prefers_the_shallowest_container(self):
        # Breadth-first: the tightest enclosing block wins over a deeper fragment.
        shallow = _Node("a plunged b")
        deep = _Node("", [_Node("", [_Node("xx plunged yy zz")])])
        root = _Node("", [shallow, deep])
        assert find_text_containing([root], "plunged", _value_of, _children_of) == "a plunged b"

    def test_ignores_a_node_holding_only_the_selection(self):
        # A node whose value IS the selection adds no context; keep searching.
        only = _Node("plunged")
        real = _Node("the boy plunged in")
        root = _Node("", [only, real])
        assert find_text_containing([root], "plunged", _value_of, _children_of) == "the boy plunged in"

    def test_falls_through_roots_in_order(self):
        empty = _Node("", [])
        window = _Node("", [_Node("he plunged in")])
        assert find_text_containing(
            [empty, window], "plunged", _value_of, _children_of
        ) == "he plunged in"

    def test_returns_empty_when_nothing_matches(self):
        assert find_text_containing([_Node("nothing here")], "plunged", _value_of, _children_of) == ""

    def test_empty_selection_returns_empty(self):
        assert find_text_containing([_Node("x")], "", _value_of, _children_of) == ""

    def test_node_budget_is_shared_across_roots_and_bounded(self):
        # A pathological tree must not be walked forever: the budget caps total visits.
        visits = []

        def counting_value(node):
            visits.append(node)
            return node.value

        deep = _Node("")
        cursor = deep
        for _ in range(50):
            child = _Node("")
            cursor.children = [child]
            cursor = child
        cursor.value = "the boy plunged in"  # beyond the depth limit
        assert find_text_containing([deep], "plunged", counting_value, _children_of, max_nodes=10) == ""
        assert len(visits) <= 10

    def test_depth_limit_stops_descent(self):
        deep = _Node("", [_Node("", [_Node("the boy plunged in")])])
        assert find_text_containing([deep], "plunged", _value_of, _children_of, max_depth=1) == ""


class TestAccessibilityWarmer:
    """Warms each app once, off the caller's thread."""

    def _warmer(self, warmed):
        return AccessibilityWarmer(warm=warmed.append, spawn=lambda work: work())

    def test_warms_a_new_pid_once(self):
        warmed: list[int] = []
        warmer = self._warmer(warmed)
        assert warmer.ensure(1234) is True
        assert warmed == [1234]

    def test_second_call_for_same_pid_is_a_noop(self):
        warmed: list[int] = []
        warmer = self._warmer(warmed)
        warmer.ensure(1234)
        assert warmer.ensure(1234) is False
        assert warmed == [1234]  # not warmed again

    def test_tracks_each_app_separately(self):
        warmed: list[int] = []
        warmer = self._warmer(warmed)
        warmer.ensure(1)
        warmer.ensure(2)
        warmer.ensure(1)
        assert warmed == [1, 2]

    def test_none_pid_is_ignored(self):
        warmed: list[int] = []
        assert self._warmer(warmed).ensure(None) is False
        assert warmed == []

    def test_warm_runs_off_the_callers_thread(self):
        # The spawn seam is what keeps a slow/unresponsive app from blocking the Qt main thread.
        spawned: list = []
        warmer = AccessibilityWarmer(warm=lambda pid: None, spawn=spawned.append)
        warmer.ensure(99)
        assert len(spawned) == 1 and callable(spawned[0])
