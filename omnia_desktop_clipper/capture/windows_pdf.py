"""Which PDF is open on Windows, and on which page.

A PDF viewer is a wall for the normal route on both platforms, and the wall is the same shape:
measured here, Foxit PDF Reader exposes **no text at all** through UI Automation — not one node
in its window contains a word from the document. macOS says the same about Preview
(``AXSelectedText`` and nothing else). So on either platform the only way to get the sentence
around a word in a PDF is to read the document itself.

macOS is handed both answers: the focused window's ``AXDocument`` says which file, and its title
says which page. Windows exposes neither. UIA gives the file NAME (``sample.pdf``) and never the
path, which was measured across the whole window tree of a real viewer.

So the path comes from the process instead. A viewer opened by double-clicking a document was
launched with that document as an argument, and WMI will report the command line of the process
owning the foreground window. That is a narrower answer than macOS's, and deliberately so — see
:func:`pdf_path_from_command_line` for exactly which cases it does and does not cover, stated
rather than glossed, because a wrong document would produce a plausible sentence the reader
never saw.
"""

from __future__ import annotations

import contextlib
import re
from collections import OrderedDict
from pathlib import PureWindowsPath
from typing import Optional

#: Matches a ``.pdf`` argument in a command line, quoted or not. Quoted first: a path with
#: spaces is the normal case on Windows and an unquoted match would stop at the first space.
_PDF_ARG_RE = re.compile(r'"([^"]+\.pdf)"|(\S+\.pdf)', re.IGNORECASE)


def pdf_path_from_command_line(command_line: str, *, expected_name: str = "") -> str:
    """Return the ``.pdf`` path in ``command_line``, or ``""``.

    WHAT THIS DOES NOT COVER, because a reader deserves to know when the answer can be wrong:

    * a document opened from inside the viewer (File → Open, or the recent-files list) is not on
      the command line at all, so this returns ``""`` and the capture keeps its selection-only
      context — a miss, never a wrong answer;
    * a viewer with several tabs open reports only the document it was LAUNCHED with, which is
      why ``expected_name`` exists: the window title says which document is on screen, and a
      command-line path that does not match it is rejected rather than used.

    Args:
        command_line: The process command line, as WMI reports it.
        expected_name: The file name the window title claims is on screen. When given, a path
            whose name differs is refused.

    Returns:
        The path, or ``""`` when there is no usable one.
    """
    if not command_line:
        return ""
    wanted = expected_name.strip().lower()
    for quoted, bare in _PDF_ARG_RE.findall(command_line):
        candidate = quoted or bare
        if not candidate:
            continue
        # The executable itself can end in .pdf only in a contrived case, but the first match
        # being the program is the shape to guard against; requiring a name match when we have
        # one does that for free.
        if wanted and PureWindowsPath(candidate).name.lower() != wanted:
            # BASENAME, not endswith. A suffix test accepts a different document whose name
            # merely ends the same way: "2026-annual-report.pdf".endswith("report.pdf") is
            # True, so opening report.pdf in a new tab would have been answered with the
            # annual report -- a sentence from a file the reader never had on screen, which is
            # precisely what this check exists to refuse. Prefix-decorated names
            # (draft-notes.pdf, v2-thesis.pdf) make that an ordinary case, not a contrived one.
            continue
        return candidate
    return ""


def pdf_name_from_title(title: str) -> str:
    """Return the ``.pdf`` file name a window title advertises, or ``""``.

    Viewers title their windows ``"<file>.pdf - <Viewer>"`` (measured: Foxit) or with the page
    appended. Only the name is taken — the title never carries a path — and it is used to check
    the command line's answer rather than to find the file.
    """
    match = re.search(r"([^\\/:*?\"<>|]+\.pdf)", title or "", re.IGNORECASE)
    return match.group(1).strip() if match else ""


#: A process's command line never changes, so it is asked for once per process rather than once
#: per capture. Bounded so a long-lived session cannot grow it without limit.
_COMMAND_LINE_CACHE: OrderedDict[int, str] = OrderedDict()
_COMMAND_LINE_CACHE_SIZE = 32

#: How long one WMI lookup may take before it is abandoned. The measured healthy cost is ~86 ms;
#: this is the ceiling for the unhealthy case, not a target.
_WMI_TIMEOUT_SECONDS = 0.5


def _query_command_line(pid: int) -> str:
    """The WMI call itself. Runs on a worker thread, never on the caller's."""
    import comtypes

    # A worker thread has no COM apartment of its own until it asks for one.
    comtypes.CoInitializeEx()
    try:
        import comtypes.client

        service = comtypes.client.CoGetObject("winmgmts:")
        rows = service.ExecQuery(
            f"SELECT CommandLine FROM Win32_Process WHERE ProcessId={int(pid)}"
        )
        for row in rows:
            value = row.Properties_("CommandLine").Value
            if value:
                return str(value)
    except Exception:
        return ""
    finally:
        with contextlib.suppress(Exception):
            comtypes.CoUninitialize()
    return ""


def foreground_command_line(pid: int) -> str:
    """Return the command line of process ``pid``, or ``""``.

    WMI over COM rather than spawning PowerShell: measured at ~20 ms to connect and ~66 ms for a
    targeted query on this machine, against several hundred for a PowerShell round trip. This
    runs on the Qt main thread before the "+" appears, and only when UI Automation has already
    come back empty -- which is the PDF case and almost nothing else.

    TWO BOUNDS, because neither ``CoGetObject`` nor ``ExecQuery`` has a timeout and a deadline
    checked around a call cannot interrupt one already in flight. On a machine where ``winmgmt``
    is contended -- an inventory agent enumerating ``Win32_Process``, or the service restarting
    after a repository check -- that query blocks for seconds with the Qt main thread stopped:
    no "+", frozen tray, on every capture while the condition lasts. That is a hang, not the
    "worse card" this module's failure contract promises.

    So the call runs on a worker with a hard timeout, and the answer is cached per pid: a
    process's command line never changes, so a viewer is asked once rather than once per
    double-click. A hung WMI then costs one miss instead of a freeze.

    Never raises: WMI can be disabled, throttled, or refuse a process owned by another user, and
    all of those mean "no context", not "no capture".
    """
    if not pid:
        return ""
    cached = _COMMAND_LINE_CACHE.get(pid)
    if cached is not None:
        _COMMAND_LINE_CACHE.move_to_end(pid)
        return cached
    try:
        from concurrent.futures import ThreadPoolExecutor
        from concurrent.futures import TimeoutError as FutureTimeout

        # NOT a `with` block. ThreadPoolExecutor's __exit__ joins its workers, so a timeout
        # inside one would be undone by the wait on the way out and the caller would still
        # sit for the full hang -- which is what this timeout exists to prevent. The pool is
        # shut down without waiting instead, and an abandoned worker finishes in its own time.
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(_query_command_line, pid)
            try:
                answer = future.result(timeout=_WMI_TIMEOUT_SECONDS)
            except FutureTimeout:
                # Deliberately NOT cached: the service may be healthy again next time, and a
                # cached "" would make one bad moment permanent for this process.
                return ""
        finally:
            pool.shutdown(wait=False)
    except Exception:
        return ""
    _COMMAND_LINE_CACHE[pid] = answer
    while len(_COMMAND_LINE_CACHE) > _COMMAND_LINE_CACHE_SIZE:
        _COMMAND_LINE_CACHE.popitem(last=False)
    return answer


def open_pdf_for(pid: int, title: str) -> Optional[str]:
    """The path of the PDF the foreground viewer has on screen, or ``None``.

    Composes the three steps so the caller has one thing to call and one thing to fail: the
    title says which document is showing, the command line says where it lives, and the two must
    agree.
    """
    name = pdf_name_from_title(title)
    if not name:
        return None
    path = pdf_path_from_command_line(foreground_command_line(pid), expected_name=name)
    return path or None
