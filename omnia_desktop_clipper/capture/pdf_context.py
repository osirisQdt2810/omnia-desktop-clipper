"""Read the sentence around a selection out of the PDF itself, when accessibility cannot.

Preview.app is a wall for the normal route: measured on macOS 15, its focused element exposes
``AXSelectedText`` and **nothing else** — no ``AXValue``, no ``AXSelectedTextRange``, no
``AXStringForRange``, no text markers. There is simply no accessibility API that returns the
text *around* a PDF selection, so the generic provider can only ever hand back the selected word.

But Preview does tell us which file is open (the focused window's ``AXDocument``) and which page
is showing (its title), and macOS ships PDFKit. So for a PDF we skip accessibility and read the
document: open it, take the current page's text, and cut the sentence out of that.

Restricting to the current page is the part that matters. A word like "inference" occurs 154
times in a real dissertation, and a whole-document search happily returns the title page — a
plausible-looking sentence the reader never saw. Page-scoped, the same lookup returns the
sentence actually on screen, in about 4 ms.
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import unquote, urlparse

# Preview titles a window "<file> — Page 57 of 90"; other viewers use "57 / 90". Both give the
# page the reader is on, which is the only reliable way to pick the right occurrence.
_PAGE_IN_TITLE_RE = re.compile(r"(\d+)\s*(?:of|/)\s*(\d+)")
# Only the page on screen is searched. Looking at neighbours as well sounded harmless — a
# sentence can straddle a page break — but combined with the uniqueness gate it silently jumped
# to a DIFFERENT page whenever the word repeated on the current one, handing back a sentence the
# reader was not looking at. That is precisely the guess this module refuses to make.


def parse_page_number(title: str) -> Optional[int]:
    """Return the 1-based page number from a PDF window title, or ``None``.

    Args:
        title: The window title, e.g. ``"report.pdf - Page 57 of 90"``.
    """
    match = _PAGE_IN_TITLE_RE.search(title or "")
    if not match:
        return None
    page = int(match.group(1))
    return page if page > 0 else None


def path_from_document_url(document_url: str) -> str:
    """Return a local filesystem path from an ``AXDocument`` URL, or ``""``.

    Only local ``file://`` documents are usable — a remote one is not ours to fetch.
    """
    url = (document_url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme != "file":
        return ""
    return unquote(parsed.path) if parsed.scheme == "file" else url


def is_pdf(path: str) -> bool:
    """Whether ``path`` names a PDF (the only document type this module can read)."""
    return path.lower().endswith(".pdf")


def pages_to_search(page_number: Optional[int], page_count: int) -> list[int]:
    """Return the 0-based page indices to search.

    A known page number gives exactly that page — the one the reader is looking at. Without one
    every page is searched, which is still guess-free because the caller then requires the word
    to be unique across the WHOLE document: unique everywhere means it can only be the one that
    was selected.

    Args:
        page_number: The 1-based page on screen, or ``None`` if it could not be parsed.
        page_count: How many pages the document has.
    """
    if page_count <= 0:
        return []
    if page_number is None:
        return list(range(page_count))
    current = page_number - 1
    return [current] if 0 <= current < page_count else []


def unique_occurrence(text: str, needle: str) -> int:
    """Index of ``needle`` in ``text`` when it appears EXACTLY once, else ``-1``.

    This is the honesty gate for the PDF route. A page holds ~3000 characters and accessibility
    cannot say WHICH occurrence the reader highlighted — Preview exposes the whole page as one
    static-text block, and there is no selection range or text marker to locate within it. So a
    repeated word leaves us guessing, and a confidently-wrong sentence the reader never looked at
    is worse than no context at all: it would be copied into a card as if it were the source.

    One occurrence means no guess is involved, which is the only case we act on.

    Args:
        text: The page text to search.
        needle: The captured selection.
    """
    if not text or not needle:
        return -1
    lowered, target = text.lower(), needle.lower()
    first = lowered.find(target)
    if first < 0 or lowered.find(target, first + 1) >= 0:
        return -1
    return first


class PdfTextReader:
    """Opens PDFs with PDFKit and caches them by path + modification time.

    Re-opening a 90-page document on every capture would be wasteful; keying the cache on the
    file's mtime means an edited document is still re-read.
    """

    def __init__(self) -> None:
        self._path: str = ""
        self._stamp: float = -1.0
        self._document: Any = None

    def document(self, path: str) -> Any:
        """Return the opened ``PDFDocument`` for ``path``, or ``None`` if it cannot be read."""
        try:
            import os

            stamp = os.path.getmtime(path)
        except OSError:
            return None
        if self._document is not None and self._path == path and self._stamp == stamp:
            return self._document
        try:  # pragma: no cover - needs PDFKit (macOS)
            from Foundation import NSURL
            from Quartz import PDFDocument

            document = PDFDocument.alloc().initWithURL_(NSURL.fileURLWithPath_(path))
        except Exception:
            return None
        if document is None:
            return None
        self._path, self._stamp, self._document = path, stamp, document
        return document

    def page_texts(self, path: str, page_number: Optional[int]) -> list[str]:
        """Return the text of the pages worth searching, nearest the reader first."""
        document = self.document(path)
        if document is None:
            return []
        try:  # pragma: no cover - needs PDFKit (macOS)
            count = int(document.pageCount())
            texts = []
            for index in pages_to_search(page_number, count):
                page = document.pageAtIndex_(index)
                texts.append(str(page.string() or "") if page is not None else "")
            return texts
        except Exception:
            return []
