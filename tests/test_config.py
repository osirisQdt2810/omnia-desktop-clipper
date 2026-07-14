"""Tests for the pure ``config`` module (defaults, round-trip, per-OS dir)."""

from __future__ import annotations

from omnia_desktop_clipper.config import (
    Config,
    default_hotkey,
    default_ocr_hotkey,
    load,
    save,
)
from omnia_desktop_clipper.platform import config_dir


class TestConfigDefaults:
    """The default config matches the desktop-clipper integration the gateway expects."""

    def test_defaults_match_desktop_integration_contract(self) -> None:
        config = Config()
        assert config.ankiconnect_url == "http://127.0.0.1:8765"
        assert config.api_key == ""
        assert config.deck_name == "Omnia Capture"
        assert config.model_name == "Basic"
        assert config.field_map == {"word": "Front", "context": "Back"}
        # The add-on ships a matching `desktop_clipper` integration for this tag.
        assert config.source_tag == "omnia-desktop-clipper"
        assert config.autogen is True
        assert config.ocr_hotkey  # platform-specific, but always set

    def test_tags_include_source_and_autogen_when_enabled(self) -> None:
        config = Config(source_tag="omnia-desktop-clipper", autogen=True)
        assert config.tags() == ["omnia-desktop-clipper", "omnia-autogen"]

    def test_tags_omit_autogen_when_disabled(self) -> None:
        config = Config(autogen=False)
        assert config.tags() == ["omnia-desktop-clipper"]

    def test_default_hotkey_is_cmd_on_macos(self) -> None:
        assert default_hotkey("darwin") == "<cmd>+<shift>+a"

    def test_default_hotkey_is_ctrl_elsewhere(self) -> None:
        assert default_hotkey("win32") == "<ctrl>+<shift>+a"
        assert default_hotkey("linux") == "<ctrl>+<shift>+a"

    def test_default_ocr_hotkey_per_platform(self) -> None:
        assert default_ocr_hotkey("darwin") == "<cmd>+<shift>+o"
        assert default_ocr_hotkey("win32") == "<ctrl>+<shift>+o"
        assert default_ocr_hotkey("linux") == "<ctrl>+<shift>+o"

    def test_load_backcompat_config_without_ocr_hotkey(self, tmp_path) -> None:
        # An older config.json (no ocr_hotkey / omnia-web-clipper source) still loads.
        path = tmp_path / "config.json"
        path.write_text(
            '{"deck_name": "Old", "source_tag": "omnia-web-clipper"}', encoding="utf-8"
        )
        loaded = load(path)
        assert loaded.deck_name == "Old"
        assert loaded.source_tag == "omnia-web-clipper"  # preserved
        assert loaded.ocr_hotkey  # filled from the default


class TestConfigRoundTrip:
    """``save`` then ``load`` preserves values; partial files merge over defaults."""

    def test_save_then_load_preserves_values(self, tmp_path) -> None:
        path = tmp_path / "config.json"
        original = Config(
            ankiconnect_url="http://localhost:9000",
            api_key="secret-key",
            deck_name="My Deck",
            model_name="Omnia Vocabulary",
            field_map={"word": "Word", "context": "Sentence"},
            source_tag="omnia-desktop-clipper",
            autogen=False,
            hotkey="<ctrl>+<alt>+z",
        )
        save(original, path)
        assert path.exists()
        assert load(path) == original

    def test_load_missing_file_returns_defaults(self, tmp_path) -> None:
        assert load(tmp_path / "absent.json") == Config()

    def test_load_merges_partial_field_map_over_defaults(self, tmp_path) -> None:
        path = tmp_path / "config.json"
        path.write_text('{"field_map": {"context": "Sentence"}}', encoding="utf-8")
        loaded = load(path)
        assert loaded.field_map == {"word": "Front", "context": "Sentence"}

    def test_load_ignores_unknown_keys(self, tmp_path) -> None:
        path = tmp_path / "config.json"
        path.write_text('{"deck_name": "D", "mystery": 1}', encoding="utf-8")
        loaded = load(path)
        assert loaded.deck_name == "D"
        assert not hasattr(loaded, "mystery")

    def test_save_creates_parent_directories(self, tmp_path) -> None:
        path = tmp_path / "nested" / "dir" / "config.json"
        save(Config(), path)
        assert path.exists()


class TestConfigRobustness:
    """A corrupt/unreadable config must not brick startup, and saves are atomic."""

    def test_corrupt_json_falls_back_to_defaults(self, tmp_path) -> None:
        path = tmp_path / "config.json"
        path.write_text('{"deck_name": "X", oops', encoding="utf-8")  # truncated / invalid JSON
        assert load(path) == Config()  # no raise; defaults

    def test_non_utf8_file_falls_back_to_defaults(self, tmp_path) -> None:
        path = tmp_path / "config.json"
        path.write_bytes(b"\xff\xfe\x00bad")  # not valid UTF-8
        assert load(path) == Config()  # no raise; defaults

    def test_non_object_json_falls_back_to_defaults(self, tmp_path) -> None:
        path = tmp_path / "config.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        assert load(path) == Config()

    def test_save_is_atomic_leaving_no_temp_file(self, tmp_path) -> None:
        path = tmp_path / "config.json"
        save(Config(deck_name="Atomic"), path)
        assert load(path).deck_name == "Atomic"
        # The sibling temp file used for the atomic replace must not linger.
        assert list(tmp_path.glob("*.tmp")) == []


class TestConfigDir:
    """``config_dir`` resolves the correct per-OS location."""

    def test_macos_dir(self, tmp_path) -> None:
        result = config_dir("darwin", {}, home=tmp_path)
        expected = tmp_path / "Library" / "Application Support" / "OmniaDesktopClipper"
        assert result == expected

    def test_windows_dir_uses_appdata(self, tmp_path) -> None:
        appdata = tmp_path / "AppData" / "Roaming"
        result = config_dir("win32", {"APPDATA": str(appdata)}, home=tmp_path)
        assert result == appdata / "OmniaDesktopClipper"

    def test_windows_dir_without_appdata_falls_back(self, tmp_path) -> None:
        result = config_dir("win32", {}, home=tmp_path)
        assert result == tmp_path / "AppData" / "Roaming" / "OmniaDesktopClipper"

    def test_linux_dir_honours_xdg(self, tmp_path) -> None:
        xdg = tmp_path / "xdg-config"
        result = config_dir("linux", {"XDG_CONFIG_HOME": str(xdg)}, home=tmp_path)
        assert result == xdg / "omnia-desktop-clipper"

    def test_linux_dir_defaults_to_dot_config(self, tmp_path) -> None:
        result = config_dir("linux", {}, home=tmp_path)
        assert result == tmp_path / ".config" / "omnia-desktop-clipper"
