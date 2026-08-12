"""The lookup panel: what the magnifier opens — the card Anki already has for a word.

Design notes (the parts that are decisions, not taste):

* **Anchored, not centred.** It opens next to the cursor where you were reading, and flips /
  clamps to stay on screen, so your eye never has to travel.
* **Focusable, unlike the "+" overlay.** You read, scroll and dismiss this one, so it takes
  focus and Esc closes it — the opposite of the overlay, which must never steal focus.
* **Ordering, not hiding.** omnia ranks and triages the fields, but everything it sends is
  rendered; a wrong guess about importance costs a scroll, never data.
* **State first.** The scheduling pill (new / learning / review + interval, reps, lapses) is the
  answer to "do I already know this?", so it sits next to the word rather than buried below.

Rendering only — every decision about *what* to show is made by omnia's word-lookup plugin and
arrives display-ready (see :mod:`omnia_desktop_clipper.lookup.client`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractScrollArea,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..lookup.client import LookupCardView, LookupView
from . import theme
from .macos_window import promote_over_all_apps

_WIDTH = 420
_MAX_HEIGHT = 560
_CURSOR_OFFSET = 16
_SCREEN_MARGIN = 12


def _state_pill(state: str, colors: theme.Palette) -> QLabel:
    """A small coloured pill naming the card's scheduling state."""
    label = QLabel(state.replace("relearning", "re-learning").title())
    color = theme.state_color(state)
    label.setStyleSheet(
        f"QLabel {{ background: {color}; color: white; border-radius: 8px;"
        f" padding: 2px 9px; font-size: 11px; font-weight: 600; }}"
    )
    return label


def _chip(text: str) -> QLabel:
    """A neutral metadata chip (deck, tag, interval, …)."""
    label = QLabel(text)
    label.setObjectName("chip")
    return label


class LookupPanel(QWidget):
    """A frameless, focusable panel showing what Anki already knows about a word."""

    def __init__(
        self,
        on_add: Optional[Callable[[], None]] = None,
        on_open_in_anki: Optional[Callable[[int], None]] = None,
    ) -> None:
        """Build the (reusable, singleton) panel.

        Args:
            on_add: Called when the user chooses to add the word from the "not found" state.
            on_open_in_anki: Called with a note id to reveal it in Anki's browser.
        """
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self._on_add = on_add
        self._on_open_in_anki = on_open_in_anki
        self._word = ""
        self.setFixedWidth(_WIDTH)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._root = QFrame(self)
        self._root.setObjectName("lookupRoot")
        outer.addWidget(self._root)

        # The root holds exactly ONE child: the current state's content widget. Each render
        # swaps in a brand-new widget rather than emptying a long-lived layout — a reused
        # QVBoxLayout keeps reporting the OLD size hint after its items are taken out, which
        # collapsed the panel to its margins on every state change after the first.
        self._shell = QVBoxLayout(self._root)
        self._shell.setContentsMargins(18, 16, 18, 16)
        self._shell.setSpacing(0)
        self._content: Optional[QWidget] = None
        self._body: QVBoxLayout = QVBoxLayout()

        # Esc closes — this panel takes focus, so it must be dismissable from the keyboard.
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.hide)

    # -- states --------------------------------------------------------------------------

    def show_loading(self, word: str, position: tuple[int, int]) -> None:
        """Show the panel immediately in a loading state, so the click feels instant."""
        self._word = word
        self._render_message(f"Looking up “{word}”…", "Searching your collection.")
        self._present(position)

    def show_error(self, word: str, message: str, position: tuple[int, int]) -> None:
        """Show that the lookup could not run (Anki closed, plugin disabled, timeout)."""
        self._word = word
        self._render_message("Lookup unavailable", message)
        self._present(position)

    def show_result(self, view: LookupView, position: tuple[int, int]) -> None:
        """Show the lookup outcome: the matching card(s), or a clear "not found" state."""
        self._word = view.word
        if not view.found:
            self._render_not_found(view.word)
        else:
            self._render_cards(view)
        self._present(position)

    # -- rendering -----------------------------------------------------------------------

    def _clear(self) -> theme.Palette:
        """Install a fresh content widget, re-apply the appearance, and return its palette."""
        if self._content is not None:
            self._shell.removeWidget(self._content)
            # setParent(None) detaches it NOW; deleteLater alone would leave a child whose
            # stale geometry keeps influencing the root's size hint.
            self._content.setParent(None)
            self._content.deleteLater()
        self._content = QWidget(self._root)
        self._body = QVBoxLayout(self._content)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(10)
        self._shell.addWidget(self._content)
        colors = theme.palette()
        self.setStyleSheet(theme.stylesheet(colors))
        return colors

    def _render_message(self, title: str, subtitle: str) -> None:
        colors = self._clear()
        heading = QLabel(title)
        heading.setObjectName("lookupTitle")
        heading.setWordWrap(True)
        self._body.addWidget(heading)
        detail = QLabel(subtitle)
        detail.setObjectName("lookupSubtitle")
        detail.setWordWrap(True)
        self._body.addWidget(detail)
        _ = colors

    def _render_not_found(self, word: str) -> None:
        """The "no card for this word" state — and the obvious next action: add it."""
        self._clear()
        heading = QLabel(f"“{word}”")
        heading.setObjectName("lookupTitle")
        heading.setWordWrap(True)
        self._body.addWidget(heading)
        detail = QLabel("No card for this word in your collection yet.")
        detail.setObjectName("lookupSubtitle")
        detail.setWordWrap(True)
        self._body.addWidget(detail)
        if self._on_add is not None:
            row = QHBoxLayout()
            row.addStretch(1)
            button = QPushButton("Add to Anki")
            button.setObjectName("lookupAction")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(self._handle_add)
            row.addWidget(button)
            holder = QWidget()
            holder.setLayout(row)
            self._body.addWidget(holder)

    def _render_cards(self, view: LookupView) -> None:
        colors = self._clear()
        card = view.cards[0]

        # Title row: the word + its scheduling state, the two things read first.
        title_row = QHBoxLayout()
        title = QLabel(card.title or view.word)
        title.setObjectName("lookupTitle")
        title.setWordWrap(True)
        title_row.addWidget(title, 1)
        title_row.addWidget(_state_pill(card.state, colors), 0, Qt.AlignmentFlag.AlignTop)
        title_holder = QWidget()
        title_holder.setLayout(title_row)
        self._body.addWidget(title_holder)

        self._body.addWidget(self._meta_row(card, len(view.cards)))
        self._body.addWidget(self._separator(colors))
        self._body.addWidget(self._fields_area(card), 1)

        if self._on_open_in_anki is not None:
            row = QHBoxLayout()
            row.addStretch(1)
            button = QPushButton("Open in Anki")
            button.setObjectName("lookupAction")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda: self._handle_open(card.note_id))
            row.addWidget(button)
            holder = QWidget()
            holder.setLayout(row)
            self._body.addWidget(holder)

    def _meta_row(self, card: LookupCardView, total: int) -> QWidget:
        """Deck / interval / reps / lapses / tags as compact chips."""
        row = QHBoxLayout()
        row.setSpacing(6)
        row.setContentsMargins(0, 0, 0, 0)
        if card.deck:
            # Deck paths ("A::B::C") are far too long for a chip and clip mid-word; the leaf is
            # the informative part, with the full path on hover.
            leaf = card.deck.split("::")[-1]
            chip = _chip(leaf)
            chip.setToolTip(card.deck)
            row.addWidget(chip)
        if card.interval_days:
            row.addWidget(_chip(f"{card.interval_days}d interval"))
        if card.reps:
            row.addWidget(_chip(f"{card.reps} reviews"))
        if card.lapses:
            row.addWidget(_chip(f"{card.lapses} lapses"))
        if total > 1:
            row.addWidget(_chip(f"+{total - 1} more note(s)"))
        row.addStretch(1)
        holder = QWidget()
        holder.setLayout(row)
        return holder

    @staticmethod
    def _separator(colors: theme.Palette) -> QFrame:
        line = QFrame()
        line.setObjectName("separator")
        line.setFixedHeight(1)
        _ = colors
        return line

    def _fields_area(self, card: LookupCardView) -> QScrollArea:
        """The scrollable field list — everything omnia sent, in its chosen order."""
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        for field in card.fields:
            layout.addWidget(self._field_block(field))
        if card.tags:
            layout.addWidget(self._tags_block(card.tags))

        area = QScrollArea()
        area.setWidget(inner)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Size to the fields, so a short card is a short panel instead of a tall one with dead
        # space; the panel's own max height is what turns a long card into a scrolling one.
        area.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        return area

    @staticmethod
    def _field_block(field) -> QWidget:
        """One field: its name as a small caps label above the value (or a media badge)."""
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        name = QLabel(field.name)
        name.setObjectName("fieldName")
        layout.addWidget(name)
        if field.text:
            value = QLabel(field.text)
            value.setObjectName("fieldText")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(value)
        else:
            # Media-only field: say what it holds instead of rendering raw markup.
            bits = []
            if field.audio:
                bits.append(f"🔊 {len(field.audio)} audio")
            if field.images:
                bits.append(f"🖼 {len(field.images)} image")
            badge = QLabel("  ".join(bits) or "—")
            badge.setObjectName("lookupSubtitle")
            layout.addWidget(badge)
        return holder

    @staticmethod
    def _tags_block(tags: tuple[str, ...]) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        name = QLabel("Tags")
        name.setObjectName("fieldName")
        layout.addWidget(name)
        value = QLabel(", ".join(tags))
        value.setObjectName("fieldText")
        value.setWordWrap(True)
        layout.addWidget(value)
        return holder

    # -- presentation --------------------------------------------------------------------

    def _present(self, position: tuple[int, int]) -> None:
        """Size to content, keep the panel fully on screen, show it and take focus."""
        # Show the freshly built content BEFORE measuring. Widgets added to an already-visible
        # parent are not shown automatically, and QLayout ignores hidden children when computing
        # a size hint — which collapsed every state after the first to its margins alone.
        # self.show() cannot fix it: on an already-visible panel it is a no-op and never
        # cascades, so the new subtree is shown explicitly.
        if self._content is not None:
            for child in self._content.findChildren(QWidget):
                child.show()
            self._content.show()
        self.show()
        # Release the previous state's pinned height: a stale fixed height clamps the new layout.
        self.setMinimumHeight(0)
        self.setMaximumHeight(_MAX_HEIGHT)
        self.layout().activate()
        self.adjustSize()
        height = min(self.sizeHint().height(), _MAX_HEIGHT)
        self.setFixedHeight(height)
        self.move(*self._anchor(position, height))
        self.raise_()
        # Unlike the overlay, this panel IS meant to take focus (scroll + Esc).
        promote_over_all_apps(self, activate=True)
        self.activateWindow()

    def _anchor(self, position: tuple[int, int], height: int) -> tuple[int, int]:
        """Place near ``position``, flipping/clamping so the panel stays on screen."""
        x, y = position
        x += _CURSOR_OFFSET
        y += _CURSOR_OFFSET
        screen = QGuiApplication.screenAt(self.mapToGlobal(self.rect().center()))
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return x, y
        area = screen.availableGeometry()
        if x + _WIDTH > area.right() - _SCREEN_MARGIN:
            x = max(area.left() + _SCREEN_MARGIN, x - _WIDTH - _CURSOR_OFFSET * 2)
        if y + height > area.bottom() - _SCREEN_MARGIN:
            y = max(area.top() + _SCREEN_MARGIN, area.bottom() - height - _SCREEN_MARGIN)
        return x, y

    # -- actions -------------------------------------------------------------------------

    def _handle_add(self) -> None:
        self.hide()
        if self._on_add is not None:
            self._on_add()

    def _handle_open(self, note_id: int) -> None:
        if self._on_open_in_anki is not None:
            self._on_open_in_anki(note_id)
