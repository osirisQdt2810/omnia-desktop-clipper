"""Play a note's audio clip through facilities the operating system already ships.

Why not ``PyQt6.QtMultimedia``: it imports fine in a dev venv but is **not** pulled into the
PyInstaller bundle unless explicitly collected, and even then it needs Qt's multimedia backend
plugins at runtime — the classic "works from source, silently does nothing in the .app" trap.
Everything used here is part of the OS itself, so there is no packaging risk at all: ``afplay``
on macOS, ALSA/PulseAudio clients on Linux, and ``winmm.dll`` on Windows.

**The clip must PLAY, not OPEN.** Every route here is silent and windowless. That is easy to get
wrong on Windows: handing the file to the shell (``start``) launches whatever application owns
``.mp3`` and puts a media player on screen over the panel the user was reading. Windows therefore
uses MCI — ``winmm``'s string interface — which decodes and plays the file inside this process,
showing nothing. ``afplay`` and ``paplay`` are already windowless CLIs, so those platforms were
never affected.

The clip arrives as bytes (fetched from Anki's media folder), so it is written to a temp file
first — every one of these players takes a path, not a stream.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

# Players tried in order, per platform. The first one present on the machine wins.
# Windows is absent on purpose: it does not spawn anything (see _play_windows).
_PLAYERS: dict[str, tuple[list[str], ...]] = {
    "darwin": (["afplay"],),  # always present on macOS
    "linux": (["paplay"], ["aplay"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]),
}

# One alias reused for every clip: opening a second without closing the first leaks an MCI
# device, and a user clicking through a lookup panel plays a great many clips.
_MCI_ALIAS = "omnia_clip"


def _temp_copy(data: bytes, suffix: str) -> Path:
    """Write ``data`` to a temp file the player can open, and return its path.

    The file deliberately OUTLIVES this call: the player is a separate process that opens it
    after we return, so it must not be auto-deleted. It lands in the OS temp directory and is
    cleaned up with the rest of it.
    """
    descriptor, name = tempfile.mkstemp(suffix=suffix or ".mp3")
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    return Path(name)


def play_bytes(data: bytes, filename: str = "") -> bool:
    """Play an audio clip, returning whether a player could be started.

    Never raises and never blocks: playback is fire-and-forget, so a long clip cannot freeze the
    lookup panel and a missing player just reports ``False`` for the caller to surface.

    Args:
        data: The clip's bytes (as fetched from Anki's collection media).
        filename: The original name, used only for its extension so the player picks the right
            decoder.
    """
    if not data:
        return False
    suffix = Path(filename).suffix if filename else ""
    try:
        path = _temp_copy(data, suffix)
    except OSError:
        return False

    if sys.platform.startswith("win"):
        return _play_windows(path)
    for player in _PLAYERS.get(sys.platform, _PLAYERS["linux"]):
        if _spawn([*player, str(path)]):
            return True
    return False


def _spawn(argv: list[str]) -> bool:
    """Start ``argv`` detached, returning whether it launched (a missing binary is not an error)."""
    try:
        subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,  # never write to the app's stderr
        )
    except (OSError, ValueError):
        return False
    return True


def _mci_send(command: str) -> int:
    """Send one MCI command string, returning its error code (0 is success).

    ``winmm`` ships with Windows, so this adds nothing to the bundle -- the same reason ``afplay``
    is used on macOS. argtypes are declared because the defaults would truncate the 64-bit
    pointers on the last two parameters.
    """
    import ctypes

    winmm = ctypes.WinDLL("winmm")
    winmm.mciSendStringW.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_void_p,
    )
    winmm.mciSendStringW.restype = ctypes.c_uint
    return int(winmm.mciSendStringW(command, None, 0, None))


def _play_windows(path: Path, send: Callable[[str], int] | None = None) -> bool:
    """Play ``path`` inside this process, showing no window.

    The shell route this replaces (``start``) does not play a file, it OPENS it: Windows launches
    whichever application claims ``.mp3`` and drops a media player on top of the lookup panel the
    user was reading. MCI decodes in-process and shows nothing.

    ``play`` is asynchronous -- it returns as soon as playback starts -- which is what keeps this
    fire-and-forget and the panel responsive.

    Args:
        path: The temp file holding the clip.
        send: The MCI entry point (injected by tests).

    Returns:
        Whether playback started. A format MCI cannot decode reports ``False``, and the caller
        surfaces that rather than falling back to opening a window, which is the thing the user
        did not want.
    """
    mci = send or _mci_send
    try:
        mci(f"close {_MCI_ALIAS}")  # the previous clip, if one is still open
        if mci(f'open "{path}" alias {_MCI_ALIAS}') != 0:
            return False
        if mci(f"play {_MCI_ALIAS}") != 0:
            mci(f"close {_MCI_ALIAS}")
            return False
    except OSError:
        return False
    return True
