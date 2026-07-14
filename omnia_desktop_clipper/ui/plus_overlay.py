"""Floating "+" button shown near the cursor after a text-selection gesture.

A small frameless, always-on-top widget (like the web clipper's in-page "+", but a real OS
window so it works over any app). The hard part on macOS: the clipper is a *background* tray app
when you select text in another app, and a plain Qt window (especially a ``Qt.Tool`` one) is
hidden while the app is not frontmost. So after showing, we promote the underlying ``NSWindow``
to a high window level with an all-spaces collection behaviour (via pyobjc), so the "+" appears
over whatever app is in front — without stealing its focus (``WA_ShowWithoutActivating``), keeping
the selection intact for the capture that runs when "+" is clicked. It auto-hides if ignored.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QPushButton, QVBoxLayout, QWidget

_AUTO_HIDE_MS = 2500
_CURSOR_OFFSET = 12  # px down-right of the cursor, so the "+" isn't under the pointer
_SIZE = 22  # the overlay is exactly the button — a small dot, never a stray default-sized window


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
            | Qt.WindowType.Tool,  # an NSPanel on macOS -> can be non-activating
        )
        self._on_click = on_click
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Show without activating so the focused app keeps its selection for the capture.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(_SIZE, _SIZE)  # pin the size; never a stray 640x480 window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._button = QPushButton("+", self)
        self._button.setFixedSize(_SIZE, _SIZE)
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setToolTip("Add the selected text to Anki (Omnia)")
        self._button.setStyleSheet(
            "QPushButton { background:#2f81f7; color:white; border:none; border-radius:11px;"
            " font-size:14px; font-weight:bold; padding:0; }"
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
        self._promote_over_all_apps()  # macOS: float above whatever app is frontmost
        self._hide_timer.start(_AUTO_HIDE_MS)

    def _handle_click(self) -> None:
        """Hide immediately and run the capture callback."""
        self._hide_timer.stop()
        self.hide()
        self._on_click()

    def _promote_over_all_apps(self) -> None:
        """macOS: raise the NSWindow to a status-level, all-spaces, non-activating panel.

        Without this a background app's window is hidden behind the frontmost app — so the "+"
        never appears while you're selecting text in another app. Best-effort; a no-op elsewhere
        or if pyobjc is unavailable.
        """
        if sys.platform != "darwin":
            return
        try:
            import objc
            from AppKit import (
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
                NSWindowCollectionBehaviorStationary,
            )

            ns_view = objc.objc_object(c_void_p=int(self.winId()))
            ns_window = ns_view.window()
            if ns_window is None:
                return
            ns_window.setLevel_(25)  # NSStatusWindowLevel — above normal + floating windows
            ns_window.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorStationary
                | NSWindowCollectionBehaviorFullScreenAuxiliary
            )
            ns_window.orderFrontRegardless()  # show over the frontmost app without activating us
        except Exception:
            pass
