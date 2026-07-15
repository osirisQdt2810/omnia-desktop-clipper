"""The settings dialog: AnkiConnect-backed deck / note-type / field pickers.

Mirrors the web clipper's options: deck and note-type are dropdowns populated live from
AnkiConnect (``deckNames`` / ``modelNames``), and the word/context field pickers list the
selected note-type's fields (``modelFieldNames``), refreshing when the note-type changes.
Every combo is *editable* and preserves the saved value, so if AnkiConnect is unreachable the
dialog degrades to free text with the current settings intact.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QWidget,
)

from ..anki import AnkiConnectClient
from ..config import Config

_NONE_LABEL = "(none)"
_T = TypeVar("_T")


class SettingsDialog(QDialog):
    """Edits a :class:`Config`; :meth:`result_config` returns the new values.

    Args:
        config: The config to edit.
        client: An AnkiConnect client used to populate the deck/note-type/field pickers.
            When ``None`` or unreachable, the pickers fall back to the saved values.
        parent: Optional parent widget.
    """

    def __init__(
        self,
        config: Config,
        client: AnkiConnectClient | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Omnia Desktop Clipper — Settings")
        self._config = config
        self._client = client

        self._url_edit = QLineEdit(config.ankiconnect_url)
        self._key_edit = QLineEdit(config.api_key)
        self._key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._deck_combo = self._editable_combo()
        self._model_combo = self._editable_combo()
        self._word_field_combo = self._editable_combo()
        self._context_field_combo = self._editable_combo()
        self._source_tag_edit = QLineEdit(config.source_tag)
        self._hotkey_edit = QLineEdit(config.hotkey)
        self._ocr_hotkey_edit = QLineEdit(config.ocr_hotkey)
        self._autogen_check = QCheckBox(
            "Tag notes 'omnia-autogen' so Omnia auto-generates the card"
        )
        self._autogen_check.setChecked(config.autogen)
        self._plus_overlay_check = QCheckBox(
            "Show a floating + on double-click / drag-select (needs Input Monitoring on macOS)"
        )
        self._plus_overlay_check.setChecked(config.plus_overlay)
        self._enabled_check = QCheckBox(
            "Enable the clipper (master switch: hotkeys + floating +). Off = capture nothing."
        )
        self._enabled_check.setChecked(config.enabled)

        self._populate_decks_and_models()
        self._populate_fields(self._model_combo.currentText())
        # Refetch the field pickers whenever the note-type changes.
        self._model_combo.currentTextChanged.connect(self._populate_fields)

        form = QFormLayout(self)
        form.addRow(self._enabled_check)  # master switch, on top
        form.addRow("AnkiConnect URL", self._url_edit)
        form.addRow("API key", self._key_edit)
        form.addRow("Deck", self._deck_combo)
        form.addRow("Note type", self._model_combo)
        form.addRow("Word → field", self._word_field_combo)
        form.addRow("Context → field", self._context_field_combo)
        form.addRow("Source tag", self._source_tag_edit)
        form.addRow("Capture hotkey", self._hotkey_edit)
        form.addRow("OCR hotkey", self._ocr_hotkey_edit)
        form.addRow(self._autogen_check)
        form.addRow(self._plus_overlay_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    @staticmethod
    def _editable_combo() -> QComboBox:
        """A combo the user can also type into (so a value survives AnkiConnect being down)."""
        combo = QComboBox()
        combo.setEditable(True)
        return combo

    def _fetch(self, fetch: Callable[[], _T], default: _T) -> _T:
        """Return ``fetch()`` or ``default`` if there is no client / it fails."""
        if self._client is None:
            return default
        try:
            return fetch()
        except Exception:
            return default

    @staticmethod
    def _fill(combo: QComboBox, items: Sequence[str], current: str) -> None:
        """Populate ``combo`` with ``items``, preserving/selecting ``current``."""
        combo.blockSignals(True)
        combo.clear()
        seen: list[str] = []
        for item in items:
            if item and item not in seen:
                combo.addItem(item)
                seen.append(item)
        if current and current not in seen:
            combo.insertItem(
                0, current
            )  # keep the saved value even if AnkiConnect lacks it
        combo.setCurrentText(current or (seen[0] if seen else ""))
        combo.blockSignals(False)

    def _populate_decks_and_models(self) -> None:
        decks = self._fetch(
            lambda: self._client.deck_names(), []  # type: ignore[union-attr]
        )
        models = self._fetch(
            lambda: self._client.model_names(), []  # type: ignore[union-attr]
        )
        self._fill(self._deck_combo, decks, self._config.deck_name)
        self._fill(self._model_combo, models, self._config.model_name)

    def _populate_fields(self, model: str) -> None:
        """Populate the word/context field pickers with ``model``'s fields + ``(none)``."""
        fields = self._fetch(
            lambda: self._client.model_field_names(model), []  # type: ignore[union-attr]
        )
        choices = [_NONE_LABEL, *fields]
        self._fill(
            self._word_field_combo, choices, self._config.field_map.get("word", "")
        )
        self._fill(
            self._context_field_combo,
            choices,
            self._config.field_map.get("context", ""),
        )

    def result_config(self) -> Config:
        """Return a new :class:`Config` built from the current field values."""

        def field_value(combo: QComboBox) -> str:
            text = combo.currentText().strip()
            return "" if text == _NONE_LABEL else text

        return Config(
            ankiconnect_url=self._url_edit.text().strip(),
            api_key=self._key_edit.text(),
            deck_name=self._deck_combo.currentText().strip(),
            model_name=self._model_combo.currentText().strip(),
            field_map={
                "word": field_value(self._word_field_combo),
                "context": field_value(self._context_field_combo),
            },
            source_tag=self._source_tag_edit.text().strip(),
            autogen=self._autogen_check.isChecked(),
            hotkey=self._hotkey_edit.text().strip(),
            ocr_hotkey=self._ocr_hotkey_edit.text().strip(),
            plus_overlay=self._plus_overlay_check.isChecked(),
            enabled=self._enabled_check.isChecked(),
        )
