"""The rule that decides whether a user sees the confirm popup.

Until now the auto-add feature was tested only at the config layer: three tests pinned the
default and its round-trip, and none of them would have noticed the branch that consumes it
being inverted, reordered or bypassed. The setting can be perfectly stored and perfectly
ignored, and the user's complaint is about the popup, not about the JSON.

The structural tests at the bottom exist because `app.py` cannot be imported here (it pulls
in PyQt6, which the suite deliberately does not install). They read the source with `ast`
instead, which is enough to catch the regression that actually threatens this feature: a new
capture path that opens the popup without asking the rule first.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from omnia_desktop_clipper.capture_flow import CaptureAction

_APP = Path(__file__).resolve().parents[1] / "omnia_desktop_clipper" / "app.py"


class TestDecide:
    """`CaptureAction.decide` — the whole rule."""

    def test_auto_add_on_sends_a_real_capture_straight_through(self):
        """The feature itself: something was captured, so add it without asking."""
        assert (
            CaptureAction.decide(auto_add=True, word="cat", context="a cat sat")
            is CaptureAction.ADD
        )

    def test_auto_add_off_always_asks(self):
        """The old behaviour, unchanged for anyone who switches the option back off."""
        assert (
            CaptureAction.decide(auto_add=False, word="cat", context="a cat sat")
            is CaptureAction.CONFIRM
        )

    @pytest.mark.parametrize(
        "word,context",
        [("", ""), (" ", ""), ("", "\n"), ("  ", " \t ")],
        ids=["both-empty", "space-word", "newline-context", "all-whitespace"],
    )
    def test_auto_add_on_drops_a_capture_with_nothing_in_it(self, word, context):
        """No popup means no Cancel, so the empty capture has to die here.

        Whitespace counts as empty. The OCR path can hand over a block that strips to
        nothing, and `" "` is not a note.
        """
        assert (
            CaptureAction.decide(auto_add=True, word=word, context=context)
            is CaptureAction.DROP
        )

    def test_auto_add_on_keeps_a_capture_that_has_only_context(self):
        """Context without a word is still worth adding — an OCR block often looks like this."""
        assert (
            CaptureAction.decide(auto_add=True, word="", context="a cat sat")
            is CaptureAction.ADD
        )

    def test_auto_add_on_keeps_a_capture_that_has_only_a_word(self):
        assert (
            CaptureAction.decide(auto_add=True, word="cat", context="")
            is CaptureAction.ADD
        )

    @pytest.mark.parametrize(
        "word,context",
        [("", ""), ("cat", "a cat sat")],
        ids=["empty", "populated"],
    )
    def test_auto_add_off_asks_even_about_an_empty_capture(self, word, context):
        """Deliberate asymmetry: with the popup available, an empty grab is worth SHOWING.

        It is the case a user most wants to see — the capture missed — and Cancel is right
        there. Dropping it silently would look like the hotkey did nothing.
        """
        assert (
            CaptureAction.decide(auto_add=False, word=word, context=context)
            is CaptureAction.CONFIRM
        )

    def test_the_three_outcomes_are_the_only_outcomes(self):
        """A fourth member would need a fourth branch in the tray app, which has three."""
        assert {member.name for member in CaptureAction} == {"DROP", "ADD", "CONFIRM"}


class TestTheTrayAppObeysTheRule:
    """Structural guards, because `app.py` imports PyQt6 and this suite must stay headless."""

    @staticmethod
    def _module() -> ast.Module:
        return ast.parse(_APP.read_text(encoding="utf-8"))

    @staticmethod
    def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"{name} is gone from app.py — this guard needs rewriting")

    def test_the_popup_is_constructed_in_exactly_one_place(self):
        """A second construction site would be a capture path that never consults the rule.

        This is the regression that would reintroduce the reported bug for some paths while
        leaving it fixed for others — the hardest kind to notice, because the feature would
        still appear to work.
        """
        tree = self._module()
        sites = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "CapturePopup"
        ]
        assert len(sites) == 1, f"CapturePopup built in {len(sites)} places, expected 1"

    def test_that_one_place_is_inside_the_gated_method(self):
        gated = self._function(self._module(), "_confirm_and_add")
        assert any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "CapturePopup"
            for node in ast.walk(gated)
        ), "the popup escaped _confirm_and_add, so auto_add no longer governs it"

    def test_the_gated_method_asks_the_rule(self):
        """`_confirm_and_add` must consult `CaptureAction.decide`, not re-implement it."""
        gated = self._function(self._module(), "_confirm_and_add")
        assert any(
            isinstance(node, ast.Attribute)
            and node.attr == "decide"
            and isinstance(node.value, ast.Name)
            and node.value.id == "CaptureAction"
            for node in ast.walk(gated)
        ), "_confirm_and_add stopped calling CaptureAction.decide"

    def test_no_capture_path_bypasses_the_gate(self):
        """Every caller of `_add_note` is either the gate itself or downstream of it.

        `_add_note` talks to AnkiConnect. A capture path that called it directly would add
        notes while skipping both the popup and the empty-capture drop.
        """
        tree = self._module()
        callers = {
            fn.name
            for fn in ast.walk(tree)
            if isinstance(fn, ast.FunctionDef)
            and any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_add_note"
                for node in ast.walk(fn)
            )
        }
        assert callers == {
            "_confirm_and_add"
        }, f"_add_note is called from {sorted(callers)}; only _confirm_and_add may call it"
