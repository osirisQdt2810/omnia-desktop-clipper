"""The floating action pill shown near the cursor after a text-selection gesture.

Three buttons now: **+** (add to Anki), **⌕** (look the word up in the collection) and the
**wand** (check the phrase for mistakes). The window mechanics are the load-bearing part and are
unchanged from the original single-"+" overlay: frameless, always-on-top, shown WITHOUT
activating so the source app keeps its selection, and promoted to a status-level all-spaces
panel on macOS so it appears over whatever app is in front.

Three things make the extra buttons better than one rather than worse:

* the magnifier reports what a lookup would find BEFORE it is clicked — a count badge when the
  word is already in the collection, a muted glyph and "not in your collection" tooltip when it
  is not. That answers the common question without any click at all;
* the wand is a WAND, not a tick: the button means "make this better", where a tick would read
  as "this is correct" — the opposite of why anyone presses it;
* the auto-hide is longer with more targets, and pauses while the pointer is over the pill, so
  aiming at the third button never races the timer.

The pill's width is DERIVED from the buttons actually on it. A hard-coded width tuned for two
put most of a third one past the right edge of the screen, where a frameless always-on-top
window is clipped rather than scrollable-to.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Optional

from PyQt6.QtCore import QPoint, QSize, Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from . import pill_geometry
from .icon import search_icon, wand_icon
from .macos_window import promote_over_all_apps

# Three targets need more aiming time than the original single "+", and the timer pauses while
# the pointer is over the pill, so the extra button never races it. Unchanged when the third
# button arrived: four seconds was already generous for two, and the pause-on-hover is what
# actually carries the aiming — a longer blind timer would only leave a stale pill on screen.
_AUTO_HIDE_MS = 4000
_AUTO_HIDE_AFTER_LEAVE_MS = 1200
# The geometry lives in pill_geometry, which has no Qt in it and is therefore checked on every
# platform on every run -- including the ones where a QApplication cannot be built. That is not
# tidiness: the screen-edge rule is the thing that has now broken twice.
_CURSOR_OFFSET = pill_geometry.CURSOR_OFFSET
_BUTTON = pill_geometry.BUTTON_PX
_GAP = pill_geometry.GAP_PX
_PAD = pill_geometry.PAD_PX

_ADD_QSS = (
    "QPushButton { background:#2f81f7; color:white; border:none; border-radius:11px;"
    " font-size:14px; font-weight:bold; padding:0; }"
    "QPushButton:hover { background:#1f6fe5; }"
)
_LOOKUP_QSS = (
    "QPushButton { background:#5b6472; border:none; border-radius:11px; padding:0; }"
    "QPushButton:hover { background:#414a57; }"
)
# Muted variant: the word is not in the collection, so a lookup has nothing to show.
_LOOKUP_EMPTY_QSS = (
    "QPushButton { background:#8b93a1; border:none; border-radius:11px; padding:0; }"
    "QPushButton:hover { background:#79808d; }"
)
_BADGE_QSS = (
    "QLabel { background:#22a06b; color:white; border-radius:7px;"
    " font-size:9px; font-weight:bold; padding:0 3px; }"
)
# Green, matching the correction panel it opens — and distinct from the magnifier's grey, so
# which button was pressed is obvious from what appears.
_CHECK_QSS = (
    "QPushButton { background:#1f9d63; border:none; border-radius:11px; padding:0; }"
    "QPushButton:hover { background:#17804f; }"
)


class ActionOverlay(QWidget):
    """A small always-on-top pill with "add" and "look up" buttons, shown near the cursor."""

    def __init__(
        self,
        on_add: Callable[[], None],
        on_lookup: Optional[Callable[[], None]] = None,
        on_check: Optional[Callable[[], None]] = None,
    ) -> None:
        """Build the overlay.

        Args:
            on_add: Called (Qt main thread) when the "+" is clicked.
            on_lookup: Called when the magnifier is clicked. ``None`` hides that button, which
                reduces the pill to exactly the original single-"+" overlay. Pass the callback
                and use :meth:`set_lookup_enabled` when the button must toggle at runtime.
            on_check: Called when the wand is clicked. ``None`` hides that button. Gated on the
                same switch as the magnifier — both are the lookup service on one socket, so if
                that is off there is nothing for either to talk to.
        """
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,  # an NSPanel on macOS -> can be non-activating
        )
        self._on_add = on_add
        self._on_lookup = on_lookup
        self._on_check = on_check
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Show without activating so the focused app keeps its selection for the capture.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(_PAD, _PAD, _PAD, _PAD)
        layout.setSpacing(_GAP)

        self._add_button = QPushButton("+", self)
        self._add_button.setFixedSize(_BUTTON, _BUTTON)
        self._add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_button.setToolTip("Add the selected text to Anki (Omnia)")
        self._add_button.setStyleSheet(_ADD_QSS)
        self._add_button.clicked.connect(self._handle_add)
        layout.addWidget(self._add_button)

        self._lookup_button = QPushButton(self)
        self._lookup_button.setFixedSize(_BUTTON, _BUTTON)
        self._lookup_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lookup_button.setIcon(search_icon())
        self._lookup_button.setIconSize(QSize(14, 14))
        self._lookup_button.setStyleSheet(_LOOKUP_QSS)
        self._lookup_button.setToolTip("Look this word up in your Anki collection")
        self._lookup_button.clicked.connect(self._handle_lookup)
        # Track this explicitly: a child of a not-yet-shown parent reports isVisible() == False,
        # so sizing off isVisible() would build a one-button pill until the first show.
        self._lookup_visible = on_lookup is not None
        self._lookup_button.setVisible(self._lookup_visible)
        layout.addWidget(self._lookup_button)

        self._check_button = QPushButton(self)
        self._check_button.setFixedSize(_BUTTON, _BUTTON)
        self._check_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._check_button.setIcon(wand_icon())
        self._check_button.setIconSize(QSize(14, 14))
        self._check_button.setStyleSheet(_CHECK_QSS)
        self._check_button.setToolTip("Check this phrase for mistakes (Omnia)")
        self._check_button.clicked.connect(self._handle_check)
        self._check_visible = on_check is not None
        self._check_button.setVisible(self._check_visible)
        layout.addWidget(self._check_button)

        # Match count badge, parented to the lookup button so it rides along.
        self._badge = QLabel(self._lookup_button)
        self._badge.setStyleSheet(_BADGE_QSS)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.setFixedHeight(14)
        self._badge.hide()

        self._resize_to_content()

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def set_lookup_enabled(self, enabled: bool) -> None:
        """Show or hide the magnifier AND the wand (Settings toggles both while the app runs).

        One switch for both, because they are one service: the wand POSTs to the same loopback
        port the magnifier reads from, so "Word Lookup off" leaves neither anything to talk to.
        Whether Phrase Check itself is on is omnia's to answer — it says so in a sentence naming
        the toggle, which is more use than a button that quietly is not there.
        """
        self._lookup_visible = enabled and self._on_lookup is not None
        self._lookup_button.setVisible(self._lookup_visible)
        self._check_visible = enabled and self._on_check is not None
        self._check_button.setVisible(self._check_visible)
        self._resize_to_content()

    # -- geometry ------------------------------------------------------------------------

    def button_count(self) -> int:
        """How many buttons the pill is currently showing."""
        return 1 + int(self._lookup_visible) + int(self._check_visible)

    def _resize_to_content(self) -> None:
        """Pin the pill to exactly its buttons (never a stray default-sized window)."""
        self.setFixedSize(
            pill_geometry.pill_width(self.button_count()), pill_geometry.pill_height()
        )

    def show_at(self, x: int, y: int) -> None:
        """Show the pill just down-right of screen ``(x, y)``, auto-hiding after a delay.

        Clamped to the screen, which it was NOT before. The pill grew from 54px to 80px with the
        third button, so a selection near the right edge put the wand — the new one — entirely
        past the edge, where a frameless always-on-top window is clipped rather than
        scrollable-to. A right-hand column is a common place to be selecting text.
        """
        self._resize_to_content()
        self.move(*self._placement(x, y))
        self.show()
        self.raise_()
        promote_over_all_apps(
            self
        )  # float above the frontmost app, without stealing focus
        self._hide_timer.start(_AUTO_HIDE_MS)

    def _placement(self, x: int, y: int) -> tuple[int, int]:
        """Where to put the pill for a gesture at ``(x, y)``, kept on screen.

        The arithmetic is in :mod:`omnia_desktop_clipper.ui.pill_geometry`; this half only asks
        Qt which screen the pointer is on. A pointer on no screen at all (it happens between
        displays) falls back to the primary one, and then to the unclamped position rather than
        to nothing.
        """
        screen = (
            QGuiApplication.screenAt(QPoint(x, y)) or QGuiApplication.primaryScreen()
        )
        if screen is None:  # pragma: no cover - no screens at all
            return x + _CURSOR_OFFSET, y + _CURSOR_OFFSET
        area = screen.availableGeometry()
        return pill_geometry.place(
            x,
            y,
            self.width(),
            self.height(),
            pill_geometry.Area(
                left=area.left(),
                top=area.top(),
                right=area.right(),
                bottom=area.bottom(),
            ),
        )

    # -- lookup hinting ------------------------------------------------------------------

    def set_lookup_hint(self, count: Optional[int], word: str = "") -> None:
        """Show what a lookup would find, before the user clicks.

        Args:
            count: Matching notes; ``0`` mutes the button and says so, ``None`` means "unknown"
                (Anki closed / probe failed) and leaves the neutral appearance.
            word: The looked-up word, for the tooltip.
        """
        quoted = f"“{word}”" if word else "this word"
        if count is None:
            self._lookup_button.setStyleSheet(_LOOKUP_QSS)
            self._lookup_button.setToolTip(f"Look {quoted} up in your Anki collection")
            self._badge.hide()
            return
        if count <= 0:
            self._lookup_button.setStyleSheet(_LOOKUP_EMPTY_QSS)
            self._lookup_button.setToolTip(
                f"No card for {quoted} yet — click to confirm"
            )
            self._badge.hide()
            return
        self._lookup_button.setStyleSheet(_LOOKUP_QSS)
        self._lookup_button.setToolTip(
            f"{count} card(s) match {quoted} — click to see them"
        )
        self._badge.setText("9+" if count > 9 else str(count))
        self._badge.adjustSize()
        self._badge.setFixedHeight(14)
        self._badge.move(_BUTTON - self._badge.width() + 3, -3)
        self._badge.show()
        self._badge.raise_()

    # -- interaction ---------------------------------------------------------------------

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt's API
        """Pause the auto-hide while the pointer is on the pill (aiming must not race it)."""
        self._hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt's API
        """Resume a short auto-hide once the pointer leaves."""
        self._hide_timer.start(_AUTO_HIDE_AFTER_LEAVE_MS)
        super().leaveEvent(event)

    def _handle_add(self) -> None:
        self._dismiss()
        self._on_add()

    def _handle_lookup(self) -> None:
        self._dismiss()
        if self._on_lookup is not None:
            self._on_lookup()

    def _handle_check(self) -> None:
        self._dismiss()
        if self._on_check is not None:
            self._on_check()

    def _dismiss(self) -> None:
        """Hide immediately and clear any hint state for the next gesture."""
        self._hide_timer.stop()
        self.hide()
        self._badge.hide()


# The original name, kept so existing imports/tests keep working.
PlusOverlay = ActionOverlay
