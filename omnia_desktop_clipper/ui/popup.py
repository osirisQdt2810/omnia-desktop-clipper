"""The capture popup: review/edit the word + context before adding to Anki."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QWidget,
)


class CapturePopup(QDialog):
    """A small always-on-top dialog to confirm the captured note.

    Shows an editable ``word`` and ``context`` plus a read-only deck/note-type,
    with Add/Cancel. Positioned near the cursor when a position is given.
    """

    def __init__(
        self,
        *,
        word: str,
        context: str,
        deck: str,
        model: str,
        position: tuple[int, int] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """Build the popup.

        Args:
            word: The prefilled word (Omnia base value).
            context: The prefilled context/sentence.
            deck: The target deck name (read-only display).
            model: The note type name (read-only display).
            position: Optional ``(x, y)`` screen position for the popup.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("Add to Anki (Omnia)")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        self._word_edit = QLineEdit(word)
        self._context_edit = QPlainTextEdit(context)

        form = QFormLayout(self)
        form.addRow("Word", self._word_edit)
        form.addRow("Context", self._context_edit)
        form.addRow("Deck", self._readonly_label(deck))
        form.addRow("Note type", self._readonly_label(model))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setText("Add")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        if position is not None:
            self.move(*position)
        self._word_edit.setFocus()

    @staticmethod
    def _readonly_label(text: str) -> QLabel:
        """Return a selectable, read-only label displaying ``text``."""
        label = QLabel(text)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def word(self) -> str:
        """Return the (stripped) edited word."""
        return self._word_edit.text().strip()

    def context(self) -> str:
        """Return the (stripped) edited context."""
        return self._context_edit.toPlainText().strip()
