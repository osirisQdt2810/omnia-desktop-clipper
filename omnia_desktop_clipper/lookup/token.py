"""Find the token that authorises regenerating a note through omnia.

``POST /generate`` rewrites the user's notes and spends their LLM/TTS credits, so omnia's
lookup service authenticates it. Omnia writes a shared secret, owner-readable, into its own
add-on data:

    <Anki data dir>/addons21/<addon folder>/user_files/clippers/lookup-token.txt

Two things make that path awkward, and both are handled here:

* **The add-on folder is not a fixed name.** AnkiWeb installs the add-on as its numeric id
  (``726991726``); a development install is a plain ``omnia``. So the folder is *globbed*
  rather than named, and any future id keeps working.
* **The file may not exist yet.** The clipper is frequently running before omnia is installed
  or updated. A token resolved once at startup would be wrong for the rest of the session, so
  nothing is cached: every request re-reads.

Pure module (``os``/``pathlib`` only), so it unit-tests headless.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

# Anki's per-OS data folder, the parent of ``addons21``. ``ANKI_BASE`` overrides it — Anki
# itself honours that variable, and a user who moved their collection expects the clipper to
# follow rather than to look in an empty default.
_ANKI_BASE_ENV = "ANKI_BASE"
_ANKI_DIR = "Anki2"

# The token's location under the data folder. ``*`` is the add-on folder (see the module
# docstring): a numeric AnkiWeb id, or "omnia" for a dev install.
_TOKEN_GLOB = "addons21/*/user_files/clippers/lookup-token.txt"


def anki_data_dir(
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return Anki's per-OS data directory (the folder that holds ``addons21``).

    Args:
        platform_name: A ``sys.platform`` override (for tests). Defaults to the running
            platform.
        environ: An environment mapping override (for tests). Defaults to ``os.environ``.
        home: A home-directory override (for tests). Defaults to ``Path.home()``.

    Returns:
        The directory (not created, not checked for existence).
    """
    platform_name = sys.platform if platform_name is None else platform_name
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else home

    base = environ.get(_ANKI_BASE_ENV)
    if base:
        return Path(base)
    if platform_name == "darwin":
        return home / "Library" / "Application Support" / _ANKI_DIR
    if platform_name.startswith("win"):
        appdata = environ.get("APPDATA")
        root = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return root / _ANKI_DIR
    # Linux / other POSIX: Anki honours XDG_DATA_HOME, else ~/.local/share.
    xdg = environ.get("XDG_DATA_HOME")
    root = Path(xdg) if xdg else home / ".local" / "share"
    return root / _ANKI_DIR


def token_file(data_dir: Path | None = None) -> Path | None:
    """Return the first readable token file under ``data_dir``, or ``None``.

    Args:
        data_dir: Anki's data directory. Defaults to :func:`anki_data_dir`.

    Returns:
        The path of the first token file that exists and can be read, or ``None`` when there
        is none. Candidates are sorted so the answer is stable across runs when a machine
        carries both a published and a development install.
    """
    data_dir = anki_data_dir() if data_dir is None else data_dir
    try:
        candidates = sorted(data_dir.glob(_TOKEN_GLOB))
    except OSError:  # an unreadable / vanished data dir is "no token", never a crash
        return None
    for candidate in candidates:
        if _read(candidate):
            return candidate
    return None


def read_token(data_dir: Path | None = None) -> str:
    """Return the discovered token, or ``""`` when omnia has not written one.

    Args:
        data_dir: Anki's data directory. Defaults to :func:`anki_data_dir`.
    """
    path = token_file(data_dir)
    return "" if path is None else _read(path)


def resolve_token(configured: str = "", data_dir: Path | None = None) -> str:
    """Return the token to authenticate with: the configured one, else the discovered one.

    The setting exists as an escape hatch (an unusual Anki layout, a token copied by hand),
    so an explicitly entered value always wins over discovery — otherwise the setting would
    look like it did nothing on exactly the machines that need it.

    Args:
        configured: The value of ``Config.lookup_token`` (usually empty).
        data_dir: Anki's data directory. Defaults to :func:`anki_data_dir`.
    """
    explicit = configured.strip()
    return explicit if explicit else read_token(data_dir)


def _read(path: Path) -> str:
    """Return ``path``'s stripped contents, or ``""`` if it cannot be read."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""
