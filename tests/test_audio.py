"""Tests for clip playback.

The reported bug: clicking a clip in the lookup panel **opened** an audio file instead of
playing it. Windows handed the temp file to the shell, which launched whatever application owns
`.mp3` and put a media player on screen over the panel the user was reading. Nothing was broken
-- `start` did exactly what it says -- but "open the file" is not "play the sound".

macOS and Linux were never affected: `afplay`, `paplay` and `ffplay -nodisp` are windowless
already. So these tests are mostly about the Windows path, and about the platforms it must not
disturb.
"""

from __future__ import annotations

import pathlib

import pytest

from omnia_desktop_clipper.ui import audio


class _Mci:
    """Records the MCI command strings, answering each with a scripted return code."""

    def __init__(self, codes: dict[str, int] | None = None) -> None:
        self.commands: list[str] = []
        self._codes = codes or {}

    def __call__(self, command: str) -> int:
        self.commands.append(command)
        for prefix, code in self._codes.items():
            if command.startswith(prefix):
                return code
        return 0

    def verbs(self) -> list[str]:
        return [c.split()[0] for c in self.commands]


class TestWindowsPlaysRatherThanOpens:
    def test_no_process_is_spawned_at_all(self, tmp_path, monkeypatch) -> None:
        """The regression. Spawning anything on Windows is what put a window on screen."""
        monkeypatch.setattr(audio.sys, "platform", "win32")
        monkeypatch.setattr(audio, "_temp_copy", lambda data, suffix: tmp_path / "clip.mp3")
        monkeypatch.setattr(audio, "_play_windows", lambda path: True)

        def forbidden(argv):
            raise AssertionError(f"a process was spawned on Windows: {argv}")

        monkeypatch.setattr(audio, "_spawn", forbidden)

        assert audio.play_bytes(b"ID3 fake", "clip.mp3") is True

    def test_the_shell_open_verb_is_gone(self) -> None:
        """`start` launches the file's associated application; that IS the bug."""
        source = pathlib.Path(audio.__file__).read_text(encoding="utf-8")

        assert '"start"' not in source, "the shell's open verb is still used somewhere"
        assert "win32" not in audio._PLAYERS, (
            "Windows is back in the spawn table, which can only mean a window again"
        )

    def test_it_opens_then_plays(self, tmp_path) -> None:
        mci = _Mci()

        assert audio._play_windows(tmp_path / "clip.mp3", send=mci) is True
        assert mci.verbs() == ["close", "open", "play"]

    def test_the_path_is_quoted(self, tmp_path) -> None:
        """Anki media names carry spaces; unquoted, MCI reads the first word as the whole path."""
        mci = _Mci()
        clip = tmp_path / "a spoken word.mp3"

        audio._play_windows(clip, send=mci)

        opened = next(c for c in mci.commands if c.startswith("open"))
        assert f'"{clip}"' in opened

    def test_a_previous_clip_is_closed_first(self, tmp_path) -> None:
        """One alias is reused, so opening without closing leaks an MCI device per click."""
        mci = _Mci()

        audio._play_windows(tmp_path / "clip.mp3", send=mci)

        assert mci.verbs()[0] == "close"
        assert mci.commands[0].endswith(audio._MCI_ALIAS)


class TestWindowsFailureIsReportedNotPapered:
    def test_an_undecodable_format_reports_false(self, tmp_path) -> None:
        """The caller shows a reason; it must NOT fall back to opening a window."""
        mci = _Mci({"open": 1})

        assert audio._play_windows(tmp_path / "clip.ogg", send=mci) is False
        assert "play" not in mci.verbs()

    def test_a_failed_play_does_not_leak_the_device(self, tmp_path) -> None:
        mci = _Mci({"play": 1})

        assert audio._play_windows(tmp_path / "clip.mp3", send=mci) is False
        assert mci.verbs().count("close") == 2, "the opened device was left open"

    def test_a_missing_winmm_is_false_not_a_crash(self, tmp_path) -> None:
        """This runs on the Qt main thread; an escaping OSError would kill the click."""

        def explode(_command):
            raise OSError("winmm is not available")

        assert audio._play_windows(tmp_path / "clip.mp3", send=explode) is False

    def test_no_data_never_touches_the_disk(self, tmp_path, monkeypatch) -> None:
        def forbidden(data, suffix):
            raise AssertionError("a temp file was written for an empty clip")

        monkeypatch.setattr(audio, "_temp_copy", forbidden)

        assert audio.play_bytes(b"", "clip.mp3") is False


class TestTheOtherPlatformsStillSpawn:
    @pytest.mark.parametrize(
        ("platform", "expected"), [("darwin", "afplay"), ("linux", "paplay")]
    )
    def test_a_windowless_cli_is_used(self, platform, expected, tmp_path, monkeypatch) -> None:
        """These were never the bug; the fix must not have moved them."""
        monkeypatch.setattr(audio.sys, "platform", platform)
        monkeypatch.setattr(audio, "_temp_copy", lambda data, suffix: tmp_path / "clip.mp3")
        spawned: list[list[str]] = []
        monkeypatch.setattr(audio, "_spawn", lambda argv: spawned.append(argv) or True)

        assert audio.play_bytes(b"ID3 fake", "clip.mp3") is True
        assert spawned[0][0] == expected

    def test_linux_falls_through_to_the_next_player(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(audio.sys, "platform", "linux")
        monkeypatch.setattr(audio, "_temp_copy", lambda data, suffix: tmp_path / "clip.mp3")
        tried: list[str] = []

        def only_ffplay(argv):
            tried.append(argv[0])
            return argv[0] == "ffplay"

        monkeypatch.setattr(audio, "_spawn", only_ffplay)

        assert audio.play_bytes(b"ID3 fake", "clip.mp3") is True
        assert tried == ["paplay", "aplay", "ffplay"]
