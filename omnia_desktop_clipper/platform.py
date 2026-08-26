"""Per-OS helpers: the config directory and the mouse-cursor position.

Import-safe: PyQt6 is imported lazily inside :func:`cursor_pos`, so the pure
modules that only need :func:`config_dir` (e.g. :mod:`config`) can import this
module headless (no PyQt6 required).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath

# The app-data folder name differs per platform to match each OS's conventions.
_MAC_APP_DIR = "OmniaDesktopClipper"
_WIN_APP_DIR = "OmniaDesktopClipper"
_LINUX_APP_DIR = "omnia-desktop-clipper"

# Win32: the least privilege that still lets QueryFullProcessImageNameW name another
# ordinary user process. Asking for more would fail against processes we are entitled to read.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def config_dir(
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the per-OS configuration directory for the clipper.

    Args:
        platform_name: A ``sys.platform`` override (for tests). Defaults to the
            running platform.
        environ: An environment mapping override (for tests). Defaults to
            ``os.environ``.
        home: A home-directory override (for tests). Defaults to ``Path.home()``.

    Returns:
        The directory (not created) where ``config.json`` should live.
    """
    platform_name = sys.platform if platform_name is None else platform_name
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else home

    if platform_name == "darwin":
        return home / "Library" / "Application Support" / _MAC_APP_DIR
    if platform_name.startswith("win"):
        appdata = environ.get("APPDATA")
        root = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return root / _WIN_APP_DIR
    # Linux / other POSIX: honour XDG_CONFIG_HOME, else ~/.config.
    xdg = environ.get("XDG_CONFIG_HOME")
    root = Path(xdg) if xdg else home / ".config"
    return root / _LINUX_APP_DIR


def frontmost_pid() -> int | None:
    """Return the frontmost application's process id, or ``None`` if unavailable.

    macOS reads AppKit's ``NSWorkspace`` (and must be called on the main thread); Windows asks
    which process owns the foreground window. Linux has no equivalent the clipper needs and
    gets ``None``.
    """
    if sys.platform.startswith("win"):
        return _windows_foreground_pid() or None
    if sys.platform != "darwin":
        return None
    try:
        from AppKit import NSWorkspace

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return None if app is None else int(app.processIdentifier())
    except Exception:
        return None


def frontmost_app_id() -> str:
    """Return an identifier for the frontmost app, or ``""`` when it cannot be determined.

    The two platforms have no common way to name a running application, so this returns
    whichever its OS can give and :func:`~omnia_desktop_clipper.browsers.is_browser` accepts
    both: a **bundle id** on macOS (``com.google.chrome``) and a **process image name** on
    Windows (``chrome.exe``).

    Windows returned ``""`` unconditionally until this was written, which silently disabled the
    whole browser hand-off there: every app looked unrecognised, the desktop "+" never stood
    aside, and a double-click in Chrome raised two "+" buttons once the capture worked at all.

    Linux still returns ``""``. Identifying the focused window means talking to X11 or a
    compositor-specific Wayland protocol, which is a different job from this one; the honest
    consequence is that the hand-off does not happen there, so both clippers may offer to
    capture in a Linux browser.

    Must be called on the main thread (the macOS path touches AppKit).
    """
    if sys.platform == "darwin":
        try:
            from AppKit import NSWorkspace

            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            return "" if app is None else str(app.bundleIdentifier() or "")
        except Exception:
            return ""
    if sys.platform.startswith("win"):
        return _windows_frontmost_process_name()
    return ""


def _windows_foreground_pid() -> int:
    """The pid owning the foreground window, or 0.

    Split out because two callers need it: the process NAME (to tell a browser apart from
    everything else) and the PDF lookup (which asks WMI for that process's command line). One
    definition means they can never disagree about which window "frontmost" refers to.
    """
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetForegroundWindow.argtypes = []
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return 0
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)
    except Exception:  # identifying the app is a convenience, never a failure
        return 0


def _windows_frontmost_process_name() -> str:
    """The image name of the process owning the foreground window (``""`` on any failure).

    Uses ``ctypes`` rather than a dependency: the clipper vendors nothing on Windows for this,
    and ``QueryFullProcessImageNameW`` needs only ``PROCESS_QUERY_LIMITED_INFORMATION``, which
    an ordinary user process is granted for other ordinary user processes. An elevated
    foreground app therefore comes back ``""`` — treated as "not a browser", which keeps the
    "+" working there rather than silently disabling it.
    """
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        # Declare the signatures. Without them ctypes assumes ``c_int``, which TRUNCATES a
        # 64-bit HANDLE and sign-extends it back; Windows keeps these values 32-bit-significant
        # so it happens to work, and that is exactly the kind of thing that stops happening to
        # work on someone else's machine.
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        pid = _windows_foreground_pid()
        if not pid:
            return ""
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            # 32768, not MAX_PATH. A browser installed under a long path overflows a 260-char
            # buffer, QueryFullProcessImageNameW fails with ERROR_INSUFFICIENT_BUFFER, and this
            # returns "" -- which is read as "not a browser", bringing back the two-"+"
            # collision for exactly the users with the longest install paths.
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            ):
                return ""
            # PureWindowsPath, not Path: this is a WINDOWS path string, and Path applies the
            # RUNNING host's rules -- on POSIX a backslash is an ordinary character, so the
            # whole thing comes back as one component. The distinction is invisible in
            # production (this branch only runs on Windows) and immediately visible to a test
            # suite that runs on macOS too.
            return PureWindowsPath(buffer.value).name.lower()
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # identifying the app is a convenience, never a failure
        return ""


def cursor_pos() -> tuple[int, int]:
    """Return the global mouse-cursor position as ``(x, y)`` in screen pixels."""
    from PyQt6.QtGui import QCursor

    point = QCursor.pos()
    return point.x(), point.y()
