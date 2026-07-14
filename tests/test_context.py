"""Tests for the pure ``capture.context`` module (sentence extraction + providers)."""

from __future__ import annotations

from omnia_desktop_clipper.capture.context import (
    ContextProvider,
    MacAXContextProvider,
    SelectionContextProvider,
    build_context_provider,
    sentence_around,
)


class TestSentenceAround:
    def test_extracts_enclosing_sentence(self) -> None:
        text = "First one. The quick brown fox jumps. Third one."
        got = sentence_around(text, text.index("fox"), len("fox"))
        assert got == "The quick brown fox jumps."

    def test_handles_newline_boundaries(self) -> None:
        text = "line one\nthe target word here\nline three"
        got = sentence_around(text, text.index("target"), len("target"))
        assert got == "the target word here"

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

    def test_extracts_sentence_when_focused_text_available(self, monkeypatch) -> None:
        prov = MacAXContextProvider()
        monkeypatch.setattr(
            prov, "_focused_text", lambda: "Intro. The word is here. End."
        )
        assert prov.resolve("word") == "The word is here."

    def test_falls_back_when_ax_unavailable(self, monkeypatch) -> None:
        prov = MacAXContextProvider()

        def boom() -> str:
            raise RuntimeError("no Accessibility permission")

        monkeypatch.setattr(prov, "_focused_text", boom)
        assert prov.resolve("word") == "word"

    def test_falls_back_when_selection_not_in_focused_text(self, monkeypatch) -> None:
        prov = MacAXContextProvider()
        monkeypatch.setattr(prov, "_focused_text", lambda: "completely different text")
        assert prov.resolve("word") == "word"

    def test_empty_selection_returns_empty(self) -> None:
        assert MacAXContextProvider().resolve("   ") == ""
