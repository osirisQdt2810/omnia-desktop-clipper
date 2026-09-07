"""Tests for finding the token that authorises ``/generate`` (no Anki, no HTTP).

The token lives inside omnia's add-on folder, and the FOLDER's name is not fixed: AnkiWeb
installs the add-on as ``726991726``, a development install is ``omnia``. Hard-coding either
would leave half the users unable to regenerate anything, with a 401 as the only explanation —
so discovery globs, and these tests pin that it finds both.
"""

from __future__ import annotations

import os
import sys

import pytest

from omnia_desktop_clipper.lookup.token import (
    anki_data_dir,
    read_token,
    resolve_token,
    token_file,
)


def _write_token(data_dir, addon_folder: str, token: str):
    """Create ``<data_dir>/addons21/<addon_folder>/user_files/clippers/lookup-token.txt``."""
    path = data_dir / "addons21" / addon_folder / "user_files" / "clippers"
    path.mkdir(parents=True, exist_ok=True)
    file = path / "lookup-token.txt"
    file.write_text(token, encoding="utf-8")
    return file


class TestAnkiDataDir:
    """Anki's per-OS data folder — the parent of ``addons21``."""

    def test_macos(self, tmp_path) -> None:
        assert anki_data_dir("darwin", {}, home=tmp_path) == (
            tmp_path / "Library" / "Application Support" / "Anki2"
        )

    def test_windows_uses_appdata(self, tmp_path) -> None:
        appdata = tmp_path / "AppData" / "Roaming"
        result = anki_data_dir("win32", {"APPDATA": str(appdata)}, home=tmp_path)
        assert result == appdata / "Anki2"

    def test_windows_without_appdata_falls_back(self, tmp_path) -> None:
        result = anki_data_dir("win32", {}, home=tmp_path)
        assert result == tmp_path / "AppData" / "Roaming" / "Anki2"

    def test_linux_defaults_to_local_share(self, tmp_path) -> None:
        assert anki_data_dir("linux", {}, home=tmp_path) == (
            tmp_path / ".local" / "share" / "Anki2"
        )

    def test_linux_honours_xdg_data_home(self, tmp_path) -> None:
        xdg = tmp_path / "xdg-data"
        result = anki_data_dir("linux", {"XDG_DATA_HOME": str(xdg)}, home=tmp_path)
        assert result == xdg / "Anki2"

    def test_anki_base_overrides_every_platform(self, tmp_path) -> None:
        """A user who moved their collection expects us to look where Anki looks."""
        moved = tmp_path / "elsewhere"
        for platform_name in ("darwin", "win32", "linux"):
            result = anki_data_dir(
                platform_name, {"ANKI_BASE": str(moved)}, home=tmp_path
            )
            assert result == moved


class TestDiscoveringTheToken:
    def test_finds_the_published_addon_folder(self, tmp_path) -> None:
        """726991726 is the AnkiWeb id — what almost every real install looks like."""
        _write_token(tmp_path, "726991726", "s3cret")

        assert read_token(tmp_path) == "s3cret"

    def test_finds_a_development_install(self, tmp_path) -> None:
        """A dev install is a plain "omnia" folder; the glob must cover it too."""
        _write_token(tmp_path, "omnia", "dev-token")

        assert read_token(tmp_path) == "dev-token"

    def test_absent_token_is_empty_not_an_error(self, tmp_path) -> None:
        """Omnia not installed (yet). The caller says so in words; nothing raises."""
        assert token_file(tmp_path) is None
        assert read_token(tmp_path) == ""

    def test_a_missing_data_directory_is_empty_not_an_error(self, tmp_path) -> None:
        assert read_token(tmp_path / "no-such-anki") == ""

    def test_surrounding_whitespace_is_stripped(self, tmp_path) -> None:
        """The file ends with a newline; sending that in a header would fail the compare."""
        _write_token(tmp_path, "726991726", "  padded-token\n")

        assert read_token(tmp_path) == "padded-token"

    def test_an_empty_file_counts_as_no_token(self, tmp_path) -> None:
        """A truncated write must not authenticate with "" — it must read as "not found"."""
        _write_token(tmp_path, "726991726", "\n")

        assert token_file(tmp_path) is None
        assert read_token(tmp_path) == ""

    def test_an_empty_file_does_not_hide_a_real_one(self, tmp_path) -> None:
        """Two add-on folders, one of them empty: the readable token still wins."""
        _write_token(tmp_path, "000000000", "")
        _write_token(tmp_path, "726991726", "real")

        assert read_token(tmp_path) == "real"

    def test_a_token_written_after_startup_is_found(self, tmp_path) -> None:
        """Nothing is cached: the clipper usually starts before/without the add-on."""
        assert read_token(tmp_path) == ""

        _write_token(tmp_path, "omnia", "arrived-later")

        assert read_token(tmp_path) == "arrived-later"

    @pytest.mark.skipif(
        sys.platform.startswith("win"), reason="POSIX permission bits only"
    )
    def test_an_unreadable_file_is_skipped(self, tmp_path) -> None:
        """The token is owner-only; a file we cannot read is "no token", never a crash."""
        blocked = _write_token(tmp_path, "000000000", "unreadable")
        blocked.chmod(0o000)
        _write_token(tmp_path, "726991726", "readable")
        try:
            assert read_token(tmp_path) == "readable"
        finally:
            blocked.chmod(0o600)


class TestResolvingTheToken:
    """Config first, discovery second."""

    def test_the_configured_token_wins_over_the_discovered_one(self, tmp_path) -> None:
        _write_token(tmp_path, "726991726", "discovered")

        assert resolve_token("typed-by-hand", tmp_path) == "typed-by-hand"

    def test_an_empty_setting_falls_through_to_discovery(self, tmp_path) -> None:
        _write_token(tmp_path, "726991726", "discovered")

        assert resolve_token("", tmp_path) == "discovered"

    def test_a_whitespace_setting_is_not_a_setting(self, tmp_path) -> None:
        """Otherwise a stray space in the box silently disables discovery."""
        _write_token(tmp_path, "726991726", "discovered")

        assert resolve_token("   ", tmp_path) == "discovered"

    def test_nothing_configured_and_nothing_installed_is_empty(self, tmp_path) -> None:
        assert resolve_token("", tmp_path) == ""


class TestWhichInstallWins:
    """A machine can carry two Omnia installs; only one of them is running."""

    def _seed(self, root, folder: str, token: str, mtime: float):
        path = root / "addons21" / folder / "user_files" / "clippers" / "lookup-token.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
        os.utime(path, (mtime, mtime))
        return path

    def test_the_most_recently_written_token_wins(self, tmp_path):
        # Alphabetical order always picks 726991726, which is wrong whenever the development
        # install is the one running — and the symptom is a 401 that restarting never fixes.
        # Omnia rewrites this file on every enable, so mtime names the live install.
        self._seed(tmp_path, "726991726", "published", mtime=1_000_000)
        newer = self._seed(tmp_path, "omnia", "development", mtime=2_000_000)

        assert token_file(tmp_path) == newer
        assert read_token(tmp_path) == "development"

    def test_the_published_install_wins_when_it_is_the_newer_one(self, tmp_path):
        newer = self._seed(tmp_path, "726991726", "published", mtime=2_000_000)
        self._seed(tmp_path, "omnia", "development", mtime=1_000_000)

        assert token_file(tmp_path) == newer
