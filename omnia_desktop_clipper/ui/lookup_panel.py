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
from PyQt6.QtGui import QGuiApplication, QKeySequence, QPixmap, QShortcut
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
from .audio import play_bytes
from .flow_layout import flow_widget
from .macos_window import promote_over_all_apps

_WIDTH = 420
_MAX_HEIGHT = 560
_CURSOR_OFFSET = 16
_SCREEN_MARGIN = 12
# Thumbnails are bounded: a full-size card image would push the panel past the screen edge.
_IMAGE_MAX_WIDTH = 360
_IMAGE_MAX_HEIGHT = 260


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
        request_media: Optional[Callable[[str, Callable[[object], None]], None]] = None,
    ) -> None:
        """Build the (reusable, singleton) panel.

        Args:
            on_add: Called when the user chooses to add the word from the "not found" state.
            on_open_in_anki: Called with a note id to reveal it in Anki's browser.
            request_media: ``(filename, on_ready)`` fetching a media file OFF the UI thread and
                calling ``on_ready(bytes | None)`` back on it. ``None`` disables image viewing.
        """
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self._on_add = on_add
        self._on_open_in_anki = on_open_in_anki
        self._request_media = request_media
        self._word = ""
        # The whole result plus which of its notes is on screen, so the switcher can re-render
        # a different note without asking omnia again.
        self._view: Optional[LookupView] = None
        self._index = 0
        self._position = (0, 0)
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

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt's API
        """Dismiss when focus leaves: a popover the user clicked away from must not linger.

        Without this the panel is a singleton that only Esc could close, so a stale answer (in
        particular an old error) stayed on screen and looked like it reappeared on every new
        selection.
        """
        self.hide()
        super().focusOutEvent(event)

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
        self._view = view
        self._index = 0
        self._position = position
        if not view.found:
            self._render_not_found(view.word)
        else:
            self._render_cards(view, 0)
        self._present(position)

    def _switch_to(self, index: int) -> None:
        """Show another matched note without re-querying (the result is already in hand)."""
        if self._view is None or not (0 <= index < len(self._view.cards)):
            return
        self._index = index
        self._render_cards(self._view, index)
        self._present(self._position)

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

    def _render_cards(self, view: LookupView, index: int = 0) -> None:
        colors = self._clear()
        card = view.cards[index]

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

        self._body.addWidget(self._meta_row(card))
        if len(view.cards) > 1:
            # More than one note matched: let the user step between them instead of only ever
            # seeing the top hit (the right note is not always the highest-ranked one).
            self._body.addWidget(self._switcher(view, index, colors))
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

    def _switcher(self, view: LookupView, index: int, colors: theme.Palette) -> QWidget:
        """One small button per matched note; the current one is highlighted."""
        holder, row = flow_widget(spacing=6)
        for position, card in enumerate(view.cards):
            label = card.title or card.note_type or f"note {card.note_id}"
            button = QPushButton(label if len(label) <= 22 else label[:21] + "…")
            button.setObjectName("lookupAction")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(f"{card.note_type or 'note'} — {card.deck or 'no deck'}")
            if position == index:
                button.setStyleSheet(
                    f"QPushButton {{ background: {colors.accent}; color: white;"
                    f" border: 1px solid {colors.accent}; border-radius: 7px;"
                    f" padding: 4px 10px; font-size: 12px; }}"
                )
            else:
                button.clicked.connect(
                    lambda _checked=False, target=position: self._switch_to(target)
                )
            row.addWidget(button)
        return holder

    def _meta_row(self, card: LookupCardView) -> QWidget:
        """Deck / interval / reps / lapses as compact chips that WRAP rather than clip.

        These carry user data (deck names, counts) whose combined width is unpredictable; a
        plain row silently truncated them mid-word ("Unit 06-1", "1d interva").
        """
        holder, row = flow_widget(spacing=6)
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
        # No "+N more" chip: the switcher below already names every other match, and saying it
        # twice just costs a line.
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

    def _field_block(self, field) -> QWidget:
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
            # Media-only field: say what it holds instead of rendering raw markup...
            bits = []
            if field.audio and not self._can_play_audio():
                bits.append(f"🔊 {len(field.audio)} audio")
            if field.images and not self._can_show_images():
                bits.append(f"🖼 {len(field.images)} image")
            if bits:
                badge = QLabel("  ".join(bits))
                badge.setObjectName("lookupSubtitle")
                layout.addWidget(badge)
            elif not field.images and not field.audio:
                badge = QLabel("—")
                badge.setObjectName("lookupSubtitle")
                layout.addWidget(badge)
        # ...but an image is worth seeing, so offer to load it (fetching is a round-trip to
        # Anki, so it happens on demand rather than for every field of every result).
        if field.audio and self._can_play_audio():
            layout.addWidget(self._audio_block(field.audio))
        if field.images and self._can_show_images():
            layout.addWidget(self._image_block(field.images))
        return holder

    def _can_play_audio(self) -> bool:
        """Audio needs the same media fetcher images do (the clip lives in Anki's media folder)."""
        return self._request_media is not None

    def _audio_block(self, filenames: tuple[str, ...]) -> QWidget:
        """A Play button per clip — a pronunciation you cannot hear is just a dead badge."""
        holder, row = flow_widget(spacing=6)
        for name in filenames:
            button = QPushButton("▶ Play" if len(filenames) == 1 else f"▶ {name[:18]}")
            button.setObjectName("lookupAction")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(name)
            button.clicked.connect(
                lambda _checked=False, target=name, btn=button: self._play(target, btn)
            )
            row.addWidget(button)
        return holder

    def _play(self, filename: str, button: QPushButton) -> None:
        """Fetch the clip (off the UI thread) and hand it to the OS player."""
        original = button.text()
        button.setEnabled(False)
        button.setText("…")

        def ready(data: object) -> None:
            ok = isinstance(data, (bytes, bytearray)) and play_bytes(bytes(data), filename)
            button.setText(original if ok else "unavailable")
            button.setEnabled(True)

        self._request_media(filename, ready)

    def _can_show_images(self) -> bool:
        """Whether a media fetcher was supplied (no fetcher = images stay as a badge)."""
        return self._request_media is not None

    def _image_block(self, filenames: tuple[str, ...]) -> QWidget:
        """A 'Show image' button that loads the picture in place when clicked."""
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        button = QPushButton(
            f"🖼 Show image{'' if len(filenames) == 1 else f's ({len(filenames)})'}"
        )
        button.setObjectName("lookupAction")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)

        def load() -> None:
            button.setEnabled(False)
            button.setText("Loading…")
            remaining = {"count": len(filenames)}

            def done() -> None:
                remaining["count"] -= 1
                if remaining["count"] <= 0:
                    button.hide()

            for name in filenames:
                self._request_media(
                    name, lambda data, target=layout, cb=done: (
                        self._place_image(target, data),
                        cb(),
                    )
                )

        button.clicked.connect(load)
        return holder

    @staticmethod
    def _place_image(layout: QVBoxLayout, data: object) -> None:
        """Render fetched bytes as a bounded thumbnail (or say the image is unavailable)."""
        label = QLabel()
        pixmap = QPixmap()
        if isinstance(data, (bytes, bytearray)) and pixmap.loadFromData(bytes(data)):
            # Bound it: a full-size card image would blow the panel past the screen.
            label.setPixmap(
                pixmap.scaled(
                    _IMAGE_MAX_WIDTH,
                    _IMAGE_MAX_HEIGHT,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            label.setText("Image unavailable")
            label.setObjectName("lookupSubtitle")
        layout.addWidget(label)

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
