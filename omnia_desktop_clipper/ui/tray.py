"""System tray icon + menu (Capture now / Settings… / Quit)."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon


class ClipperTray:
    """Wraps a ``QSystemTrayIcon`` with the clipper's actions and notifications."""

    def __init__(
        self,
        *,
        icon: QIcon,
        on_capture: Callable[[], None],
        on_ocr: Callable[[], None],
        on_settings: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        """Build the tray icon and its context menu.

        Args:
            icon: The tray icon to show.
            on_capture: Handler for "Capture now" (clipboard selection).
            on_ocr: Handler for "Capture text from screen (OCR)…".
            on_settings: Handler for "Settings…".
            on_quit: Handler for "Quit".
        """
        self._tray = QSystemTrayIcon(icon)
        self._tray.setToolTip("Omnia Desktop Clipper")

        menu = QMenu()
        self._add_action(menu, "Capture now", on_capture)
        self._add_action(menu, "Capture text from screen (OCR)…", on_ocr)
        self._add_action(menu, "Settings…", on_settings)
        menu.addSeparator()
        self._add_action(menu, "Quit", on_quit)
        self._menu = menu
        self._tray.setContextMenu(menu)

    @staticmethod
    def _add_action(menu: QMenu, text: str, handler: Callable[[], None]) -> QAction:
        """Add a menu action wired to ``handler`` (ignoring the checked arg)."""
        action = QAction(text, menu)
        action.triggered.connect(lambda _checked=False: handler())
        menu.addAction(action)
        return action

    def show(self) -> None:
        """Make the tray icon visible."""
        self._tray.show()

    def show_message(self, title: str, message: str) -> None:
        """Show a transient system notification (a toast)."""
        self._tray.showMessage(title, message)
