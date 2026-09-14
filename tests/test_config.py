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
        assert config.plus_overlay is True  # the floating "+" is on by default
        assert config.enabled is True  # the clipper is on by default

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
        assert loaded.plus_overlay is True  # new field defaults on for old configs
        assert loaded.enabled is True  # master switch defaults on for old configs


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
            plus_overlay=False,
            enabled=False,
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


class TestLookupSettings:
    """The lookup toggle + service URL survive a save/load round-trip."""

    def test_defaults(self) -> None:
        config = Config()
        assert config.lookup_enabled is True
        assert config.lookup_url == "http://127.0.0.1:8766"

    def test_from_dict_reads_both(self) -> None:
        config = Config.from_dict(
            {"lookup_enabled": False, "lookup_url": "http://127.0.0.1:9000"}
        )
        assert config.lookup_enabled is False
        assert config.lookup_url == "http://127.0.0.1:9000"

    def test_missing_keys_fall_back_to_defaults(self) -> None:
        # An older config.json written before the lookup existed must still load.
        config = Config.from_dict({"deck_name": "D"})
        assert config.lookup_enabled is True
        assert config.lookup_url == "http://127.0.0.1:8766"

    def test_round_trips_through_to_dict(self) -> None:
        original = Config(lookup_enabled=False, lookup_url="http://h:1")
        assert Config.from_dict(original.to_dict()) == original


class TestBrowserSkip:
    def test_defaults_to_standing_aside_in_browsers(self) -> None:
        assert Config().skip_in_browsers is True

    def test_round_trips(self) -> None:
        original = Config(skip_in_browsers=False)
        assert Config.from_dict(original.to_dict()) == original

    def test_older_config_without_the_key_still_loads(self) -> None:
        assert Config.from_dict({"deck_name": "D"}).skip_in_browsers is True


class TestAutoAdd:
    """Adding straight away, without the confirm popup.

    Default ON: the capture gesture — a hotkey, or clicking the "+" — is already a deliberate
    act, so a dialog whose only content is what you just selected asks you to confirm a decision
    you already made. Kept as a setting because there are real reasons to want the popup back:
    tuning the field map, or a source whose accessibility text needs an edit first.
    """

    def test_it_defaults_to_on(self):
        assert Config().auto_add is True

    def test_it_round_trips(self):
        restored = Config.from_dict(Config(auto_add=False).to_dict())

        assert restored.auto_add is False

    def test_a_config_saved_before_this_option_existed_gets_the_new_default(self):
        """An older config file has no such key, so it must inherit the default rather than
        silently keep the old behaviour — otherwise the setting appears on, while the popup
        still shows, and nothing explains why."""
        stored = Config().to_dict()
        del stored["auto_add"]

        assert Config.from_dict(stored).auto_add is True
