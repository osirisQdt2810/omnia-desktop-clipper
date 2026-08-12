"""Play a note's audio clip through the operating system's own player.

Why not ``PyQt6.QtMultimedia``: it imports fine in a dev venv but is **not** pulled into the
PyInstaller bundle unless explicitly collected, and even then it needs Qt's multimedia backend
plugins at runtime — the classic "works from source, silently does nothing in the .app" trap.
Handing the bytes to a player the OS already ships has no packaging risk at all: on macOS
``afplay`` is part of the system, and the Windows/Linux fallbacks degrade to the file's default
handler rather than failing loudly.

The clip arrives as bytes (fetched from Anki's media folder), so it is written to a temp file
first — every one of these players takes a path, not a stream.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Players tried in order, per platform. The first one present on the machine wins.
_PLAYERS: dict[str, tuple[list[str], ...]] = {
    "darwin": (["afplay"],),  # always present on macOS
    "win32": (["powershell", "-NoProfile", "-c"],),  # handled specially below
    "linux": (["paplay"], ["aplay"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]),
}


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
        # No universal CLI player on Windows; the shell's file association is the reliable route.
        return _spawn(["cmd", "/c", "start", "", str(path)])
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
