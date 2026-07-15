"""Configuration model + JSON load/save.

Pure module: no PyQt6/pynput imports, so it unit-tests headless. The on-disk
format is a small JSON object in the per-OS config directory (see
:func:`platform.config_dir`). Loading is defensive — unknown keys are ignored,
missing keys fall back to defaults, and ``field_map`` is deep-merged over the
defaults (mirroring the web clipper's partial-map behaviour).
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .platform import config_dir

# The tag the Omnia add-on's IntegrationGateway keys on to auto-generate a note.
_AUTOGEN_TAG = "omnia-autogen"
_CONFIG_FILENAME = "config.json"


def default_hotkey(platform_name: str | None = None) -> str:
    """Return the default global-hotkey string for the given platform.

    Args:
        platform_name: A ``sys.platform`` override (for tests). Defaults to the
            running platform.

    Returns:
        A ``pynput``-style hotkey string (``<cmd>+<shift>+a`` on macOS,
        ``<ctrl>+<shift>+a`` elsewhere).
    """
    platform_name = sys.platform if platform_name is None else platform_name
    if platform_name == "darwin":
        return "<cmd>+<shift>+a"
    return "<ctrl>+<shift>+a"


def default_ocr_hotkey(platform_name: str | None = None) -> str:
    """Return the default global-hotkey string for the OCR (screen-region) capture.

    Args:
        platform_name: A ``sys.platform`` override (for tests). Defaults to the
            running platform.

    Returns:
        A ``pynput``-style hotkey (``<cmd>+<shift>+o`` on macOS, else ``<ctrl>+<shift>+o``).
    """
    platform_name = sys.platform if platform_name is None else platform_name
    if platform_name == "darwin":
        return "<cmd>+<shift>+o"
    return "<ctrl>+<shift>+o"


def _default_field_map() -> dict[str, str]:
    """Return the default capture-key -> Anki-field-name mapping."""
    return {"word": "Front", "context": "Back"}


def _as_str(value: object, default: str) -> str:
    """Return ``value`` if it is a string, else ``default``."""
    return value if isinstance(value, str) else default


def _as_bool(value: object, default: bool) -> bool:
    """Return ``value`` if it is a bool, else ``default``."""
    return value if isinstance(value, bool) else default


@dataclass
class Config:
    """User-editable settings for the desktop clipper.

    ``field_map`` maps a capture key (``"word"`` / ``"context"``) to the Anki
    note-field name it should fill. The default ``source_tag`` is
    ``omnia-desktop-clipper`` — the Omnia add-on ships a matching integration so
    it recognises the note and auto-generates it (like the web clipper). ``hotkey``
    triggers the clipboard capture; ``ocr_hotkey`` triggers the screen-region OCR
    capture.
    """

    ankiconnect_url: str = "http://127.0.0.1:8765"
    api_key: str = ""
    deck_name: str = "Omnia Capture"
    model_name: str = "Basic"
    field_map: dict[str, str] = field(default_factory=_default_field_map)
    source_tag: str = "omnia-desktop-clipper"
    autogen: bool = True
    hotkey: str = field(default_factory=default_hotkey)
    ocr_hotkey: str = field(default_factory=default_ocr_hotkey)
    # Show a floating "+" near the cursor on a double-click / drag-select in any app; clicking it
    # runs the same capture as the hotkey. A global mouse hook (needs Input Monitoring on macOS).
    plus_overlay: bool = True
    # Master on/off switch. When off, NOTHING captures — the hotkeys and the "+" mouse hook are
    # stopped and the tray/capture actions no-op. Toggle it in Settings or the tray menu.
    enabled: bool = True

    def tags(self) -> list[str]:
        """Return the note tags: the source tag + ``omnia-autogen`` if enabled."""
        tags = [self.source_tag]
        if self.autogen and _AUTOGEN_TAG not in tags:
            tags.append(_AUTOGEN_TAG)
        return tags

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable dict of this config."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Config:
        """Build a config from a (possibly partial) mapping, merged over defaults.

        Args:
            data: A mapping parsed from JSON. Unknown keys are ignored; missing
                keys use the dataclass defaults; ``field_map`` is deep-merged.

        Returns:
            A fully-populated :class:`Config`.
        """
        base = cls()
        merged_field_map = dict(base.field_map)
        raw_field_map = data.get("field_map")
        if isinstance(raw_field_map, Mapping):
            for key, value in raw_field_map.items():
                if isinstance(key, str) and isinstance(value, str):
                    merged_field_map[key] = value
        return cls(
            ankiconnect_url=_as_str(data.get("ankiconnect_url"), base.ankiconnect_url),
            api_key=_as_str(data.get("api_key"), base.api_key),
            deck_name=_as_str(data.get("deck_name"), base.deck_name),
            model_name=_as_str(data.get("model_name"), base.model_name),
            field_map=merged_field_map,
            source_tag=_as_str(data.get("source_tag"), base.source_tag),
            autogen=_as_bool(data.get("autogen"), base.autogen),
            hotkey=_as_str(data.get("hotkey"), base.hotkey),
            ocr_hotkey=_as_str(data.get("ocr_hotkey"), base.ocr_hotkey),
            plus_overlay=_as_bool(data.get("plus_overlay"), base.plus_overlay),
            enabled=_as_bool(data.get("enabled"), base.enabled),
        )


def config_file(directory: Path | None = None) -> Path:
    """Return the full path to ``config.json`` (in ``directory`` or the OS dir)."""
    directory = config_dir() if directory is None else directory
    return directory / _CONFIG_FILENAME


def load(path: Path | None = None) -> Config:
    """Load the config from ``path`` (or the OS config file), or return defaults.

    A missing file yields a default :class:`Config`. A present file is parsed as
    JSON and merged over the defaults.

    Args:
        path: The file to read. Defaults to the per-OS config file.

    Returns:
        The loaded (or default) :class:`Config`.
    """
    path = config_file() if path is None else path
    if not path.exists():
        return Config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        # A corrupt/truncated/unreadable config must NOT brick startup — a mid-write crash or a
        # hand-edit typo falls back to defaults rather than raising through ClipperApp.__init__.
        return Config()
    return Config.from_dict(raw if isinstance(raw, Mapping) else {})


def save(config: Config, path: Path | None = None) -> None:
    """Write ``config`` as pretty JSON to ``path`` (or the OS config file).

    Parent directories are created as needed.

    Args:
        config: The config to persist.
        path: The file to write. Defaults to the per-OS config file.
    """
    path = config_file() if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config.to_dict(), indent=2, ensure_ascii=False)
    # Atomic write: a crash/power-loss mid-write must not truncate config.json to a corrupt state
    # (which load() would then have to fall back from). Write a sibling temp file, then os.replace
    # — atomic on macOS/Windows/Linux — so the live file is only ever the previous or the new one.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload + "\n", encoding="utf-8")
    os.replace(tmp, path)
