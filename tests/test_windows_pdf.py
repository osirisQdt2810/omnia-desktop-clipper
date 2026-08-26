"""Tests for finding which PDF a Windows viewer has open.

Pure string work plus one COM call, so everything except that call tests headless on every
platform. The parsing is where the risk is: a wrong path here would produce a *plausible*
sentence out of a document the reader never opened, which is worse than no context at all.
"""

from __future__ import annotations

import pytest

from omnia_desktop_clipper.capture.windows_pdf import (
    open_pdf_for,
    pdf_name_from_title,
    pdf_path_from_command_line,
)

# Measured on this machine, launched by double-clicking the file.
_FOXIT = (
    '"C:\\Program Files (x86)\\Foxit Software\\Foxit PDF Reader\\FoxitPDFReader.exe" '
    '"C:\\Users\\PC\\AppData\\Local\\Temp\\omnia-diag\\sample.pdf"'
)


class TestPdfNameFromTitle:
    """Viewers put the file name in the title. They never put the path there."""

    @pytest.mark.parametrize(
        "title,expected",
        [
            ("sample.pdf - Foxit PDF Reader", "sample.pdf"),
            ("thesis.pdf — Page 57 of 90", "thesis.pdf"),
            ("Annual Report 2026.pdf - Adobe Acrobat", "Annual Report 2026.pdf"),
            ("REPORT.PDF - Viewer", "REPORT.PDF"),
        ],
    )
    def test_it_reads_the_name(self, title: str, expected: str) -> None:
        assert pdf_name_from_title(title) == expected

    @pytest.mark.parametrize(
        "title", ["Untitled - Notepad", "", "no document here", "notes.txt - Editor"]
    )
    def test_a_title_with_no_pdf_yields_nothing(self, title: str) -> None:
        assert pdf_name_from_title(title) == ""


class TestPdfPathFromCommandLine:
    """The path comes from the process, because UIA never exposes it."""

    def test_it_finds_a_quoted_path(self) -> None:
        assert pdf_path_from_command_line(_FOXIT) == (
            "C:\\Users\\PC\\AppData\\Local\\Temp\\omnia-diag\\sample.pdf"
        )

    def test_a_path_with_spaces_survives(self) -> None:
        """Quoted alternatives are matched first; an unquoted match stops at the first space."""
        line = '"C:\\Viewer\\v.exe" "C:\\My Documents\\Annual Report.pdf"'

        assert pdf_path_from_command_line(line) == "C:\\My Documents\\Annual Report.pdf"

    def test_an_unquoted_path_still_works(self) -> None:
        assert (
            pdf_path_from_command_line("viewer.exe C:\\docs\\a.pdf")
            == "C:\\docs\\a.pdf"
        )

    def test_no_pdf_argument_yields_nothing(self) -> None:
        """A document opened from inside the viewer is not on the command line at all.

        Returning "" there is a MISS, and a miss keeps the selection as context. The thing
        being avoided is a wrong answer.
        """
        assert pdf_path_from_command_line('"C:\\Viewer\\v.exe"') == ""

    def test_the_title_name_must_match(self) -> None:
        """A viewer with tabs reports only the document it was LAUNCHED with.

        The window title says which one is on screen; if the command line names a different
        file, the reader is looking at something else and the answer would be a sentence from a
        document they are not reading.
        """
        line = '"v.exe" "C:\\docs\\first.pdf"'

        assert pdf_path_from_command_line(line, expected_name="second.pdf") == ""
        assert pdf_path_from_command_line(line, expected_name="first.pdf") == (
            "C:\\docs\\first.pdf"
        )

    def test_a_name_that_merely_ends_the_same_is_refused(self) -> None:
        """The failure a suffix test allows, and the reason this compares basenames.

        The viewer was launched with the annual report and the reader then opened report.pdf in
        a new tab. "2026-annual-report.pdf".endswith("report.pdf") is True, so a suffix test
        answers with the annual report -- a document that was never on screen.
        """
        line = '"v.exe" "C:\\Users\\me\\Downloads\\2026-annual-report.pdf"'

        assert pdf_path_from_command_line(line, expected_name="report.pdf") == ""

    def test_a_directory_that_ends_the_same_is_refused_too(self) -> None:
        line = '"v.exe" "C:\\report.pdf\\other.pdf"'

        assert pdf_path_from_command_line(line, expected_name="report.pdf") == ""

    def test_matching_the_name_ignores_case(self) -> None:
        line = '"v.exe" "C:\\docs\\Report.PDF"'
        assert pdf_path_from_command_line(line, expected_name="report.pdf") == (
            "C:\\docs\\Report.PDF"
        )

    def test_an_empty_command_line_yields_nothing(self) -> None:
        """WMI returns an empty CommandLine for processes it will not describe."""
        assert pdf_path_from_command_line("") == ""


class TestOpenPdfFor:
    """The three steps composed, so the caller has one thing to call and one way to fail."""

    @staticmethod
    def _with_command_line(monkeypatch, command_line: str) -> None:
        import omnia_desktop_clipper.capture.windows_pdf as module

        monkeypatch.setattr(
            module,
            "foreground_command_line",
            lambda _pid, _timeout=None: command_line,
        )

    def test_it_returns_the_path_when_title_and_command_line_agree(
        self, monkeypatch
    ) -> None:
        self._with_command_line(monkeypatch, _FOXIT)

        assert open_pdf_for(32372, "sample.pdf - Foxit PDF Reader") == (
            "C:\\Users\\PC\\AppData\\Local\\Temp\\omnia-diag\\sample.pdf"
        )

    def test_a_title_without_a_pdf_short_circuits(self, monkeypatch) -> None:
        """No document on screen means no query at all -- WMI costs ~66 ms."""
        called = {"n": 0}

        import omnia_desktop_clipper.capture.windows_pdf as module

        def counting(_pid, _timeout=None):
            called["n"] += 1
            return _FOXIT

        monkeypatch.setattr(module, "foreground_command_line", counting)

        assert open_pdf_for(32372, "Untitled - Notepad") is None
        assert called["n"] == 0

    def test_a_disagreement_returns_none(self, monkeypatch) -> None:
        self._with_command_line(monkeypatch, '"v.exe" "C:\\docs\\other.pdf"')

        assert open_pdf_for(1, "sample.pdf - Foxit PDF Reader") is None

    def test_no_pid_returns_none(self, monkeypatch) -> None:
        self._with_command_line(monkeypatch, "")

        assert open_pdf_for(0, "sample.pdf - Foxit PDF Reader") is None
