"""Floating "+" button shown near the cursor after a text-selection gesture.

A small frameless, always-on-top widget (like the web clipper's in-page "+", but a real OS
window so it works over any app). It never steals focus (``WA_ShowWithoutActivating``) so the
selection in the underlying app stays intact for the capture that runs when "+" is clicked, and
it auto-hides after a short delay if ignored.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QPushButton, QVBoxLayout, QWidget

_AUTO_HIDE_MS = 2500
_CURSOR_OFFSET = 14  # px down-right of the cursor, so the "+" isn't under the pointer


class PlusOverlay(QWidget):
    """A small always-on-top "+" that appears near the cursor; clicking it runs the capture."""

    def __init__(self, on_click: Callable[[], None]) -> None:
        """Build the overlay.

        Args:
            on_click: Called (on the Qt main thread) when the user clicks the "+".
        """
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,  # no taskbar/dock entry
        )
        self._on_click = on_click
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Show without activating so the focused app keeps its selection for the capture.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._button = QPushButton("+", self)
        self._button.setFixedSize(28, 28)
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setToolTip("Add the selected text to Anki (Omnia)")
        self._button.setStyleSheet(
            "QPushButton { background:#2f81f7; color:white; border:none; border-radius:14px;"
            " font-size:18px; font-weight:bold; }"
            "QPushButton:hover { background:#1f6fe5; }"
        )
        self._button.clicked.connect(self._handle_click)
        layout.addWidget(self._button)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_at(self, x: int, y: int) -> None:
        """Show the "+" just down-right of screen ``(x, y)``, auto-hiding after a short delay."""
        self.move(x + _CURSOR_OFFSET, y + _CURSOR_OFFSET)
        self.show()
        self.raise_()
        self._hide_timer.start(_AUTO_HIDE_MS)

    def _handle_click(self) -> None:
        """Hide immediately and run the capture callback."""
        self._hide_timer.stop()
        self.hide()
        self._on_click()
