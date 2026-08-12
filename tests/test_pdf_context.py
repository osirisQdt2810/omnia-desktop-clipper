"""Tests for the PDF context helpers (pure parts; PDFKit itself needs macOS)."""

from __future__ import annotations

import pytest

from omnia_desktop_clipper.capture.pdf_context import (
    is_pdf,
    pages_to_search,
    parse_page_number,
    path_from_document_url,
    unique_occurrence,
)


class TestParsePageNumber:
    @pytest.mark.parametrize(
        "title,expected",
        [
            ("report.pdf - Page 57 of 90", 57),
            ("report.pdf — Page 1 of 3", 1),
            ("thesis.pdf 12 / 200", 12),
            ("no page here.pdf", None),
            ("", None),
        ],
    )
    def test_reads_the_page_the_reader_is_on(self, title, expected):
        assert parse_page_number(title) == expected

    def test_page_zero_is_rejected(self):
        assert parse_page_number("x 0 of 9") is None


class TestPathFromDocumentUrl:
    def test_file_url_becomes_a_path(self):
        assert path_from_document_url("file:///Users/me/a.pdf") == "/Users/me/a.pdf"

    def test_percent_escapes_are_decoded(self):
        assert path_from_document_url("file:///Users/me/my%20file.pdf") == "/Users/me/my file.pdf"

    def test_remote_documents_are_refused(self):
        # Not ours to fetch, and reading one would be a surprise network call.
        assert path_from_document_url("https://example.com/a.pdf") == ""

    def test_blank(self):
        assert path_from_document_url("") == ""
        assert path_from_document_url("   ") == ""


class TestIsPdf:
    def test_detects_pdfs_case_insensitively(self):
        assert is_pdf("/a/b.pdf") and is_pdf("/a/B.PDF")

    def test_other_documents_are_not_pdfs(self):
        assert not is_pdf("/a/b.txt")
        assert not is_pdf("")


class TestPagesToSearch:
    def test_only_the_page_on_screen_is_searched(self):
        # Neighbours are deliberately NOT searched: with the uniqueness gate that silently
        # returned a sentence from a page the reader was not looking at.
        assert pages_to_search(57, 90) == [56]

    def test_first_and_last_pages(self):
        assert pages_to_search(1, 90) == [0]
        assert pages_to_search(90, 90) == [89]

    def test_out_of_range_page_yields_nothing(self):
        assert pages_to_search(200, 90) == []

    def test_unknown_page_falls_back_to_every_page(self):
        assert pages_to_search(None, 3) == [0, 1, 2]

    def test_empty_document(self):
        assert pages_to_search(1, 0) == []


class TestUniqueOccurrence:
    """The honesty gate: act only when there is no guess to make."""

    def test_a_single_occurrence_is_located(self):
        assert unique_occurrence("the cat sat", "cat") == 4

    def test_a_repeated_word_is_refused(self):
        # Accessibility cannot say WHICH one the reader highlighted, so a confidently-wrong
        # sentence must not be produced.
        assert unique_occurrence("the cat saw a cat", "cat") == -1

    def test_matching_ignores_case(self):
        assert unique_occurrence("The Cat sat", "cat") == 4

    def test_missing_word(self):
        assert unique_occurrence("the dog sat", "cat") == -1

    def test_blank_inputs(self):
        assert unique_occurrence("", "cat") == -1
        assert unique_occurrence("the cat", "") == -1
