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
* **Readable AND fixable.** Every field carries a generate button, and the note carries a
  "Generate all", so the answer to an empty or stale field is here rather than "open Anki and
  find the note". A field omnia cannot generate keeps its button: clicking it fetches the
  reason, which is the thing worth knowing.

Rendering only — every decision about *what* to show, and about what may be generated, is made
by omnia's word-lookup plugin and arrives display-ready (see
:mod:`omnia_desktop_clipper.lookup.client`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, Qt, QTimer
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

from ..lookup.client import LookupCardView, LookupFieldView, LookupView
from ..lookup.generate import GenerateOutcome
from ..lookup.regeneration import SPIN_FRAMES, RegenerationState
from . import theme
from .audio import play_bytes
from .flow_layout import flow_widget
from .macos_window import promote_over_all_apps

_WIDTH = 420
_MAX_HEIGHT = 560
_CURSOR_OFFSET = 16
_SCREEN_MARGIN = 12
# Thumbnails are bounded: a full-size card image would push the panel past the screen edge.
_FADE_MS = 130
_FADE_HEIGHT = 26  # px of gradient at the bottom of a scrolling field list
_CAPTION_GAP = 7  # px between a field's caption and its value
_IMAGE_MAX_WIDTH = 360
_IMAGE_MAX_HEIGHT = 260

# The spinner's pace and the generate button's width. What those controls SAY, and whether
# they can be pressed, is decided in lookup/regeneration.py — this file only draws it.
_SPIN_MS = 220
_GENERATE_WIDTH = 28  # px; a fixed width keeps the glyph and the spinner frames aligned


@dataclass
class _FieldRow:
    """One field's row: the widget in the list, and the button that regenerates it.

    Held on to because a generate answer arrives tens of seconds later, into a panel the user
    is still reading. Replacing THAT row leaves the scroll position, the other rows, and any
    field still spinning exactly as they were — which rebuilding the panel would not.
    """

    widget: QWidget
    button: Optional[QPushButton] = None


def _state_pill(state: str, colors: theme.Palette) -> QLabel:
    """A small coloured pill naming the card's scheduling state."""
    label = QLabel(state.replace("relearning", "re-learning").title())
    color = theme.state_color(state)
    label.setStyleSheet(
        f"QLabel {{ background: {color}; color: white; border-radius: 8px;"
        f" padding: 2px 9px; font-size: 11px; font-weight: 600; }}"
    )
    return label


def _leaded(text: str) -> str:
    """Wrap ``text`` so the label renders with real line spacing.

    Qt style sheets have no ``line-height`` for a ``QLabel``, and the default leading makes
    wrapped prose look cramped and crude. A rich-text wrapper is the only way to set it, so the
    text is escaped and put inside a div that carries the leading.
    """
    from html import escape

    # Newlines are meaningful: omnia keeps the author's <br> structure (a "Phrasal Verb" field
    # is one entry per line), and rich text would otherwise swallow them.
    body = escape(text).replace("\n", "<br>")
    return f'<div style="line-height:148%">{body}</div>'


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
        on_generate: Optional[Callable[[int, Optional[list[str]]], None]] = None,
    ) -> None:
        """Build the (reusable, singleton) panel.

        Args:
            on_add: Called when the user chooses to add the word from the "not found" state.
            on_open_in_anki: Called with a note id to reveal it in Anki's browser.
            request_media: ``(filename, on_ready)`` fetching a media file OFF the UI thread and
                calling ``on_ready(bytes | None)`` back on it. ``None`` disables image viewing.
            on_generate: ``(note_id, field_names | None)`` asking omnia to regenerate those
                fields (``None`` = the whole note), OFF the UI thread. The answer comes back
                through :meth:`apply_generation` / :meth:`report_generation_failure`, not
                through a callback, because one request can answer about many fields at once.
                ``None`` hides the generate controls entirely.
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
        self._on_generate = on_generate
        self._word = ""
        # What may be regenerated, what is running, and what omnia said about the rest. Kept in
        # a Qt-free object so those rules are testable without a QApplication.
        self._regen = RegenerationState()
        # Handles into the rendered field list, so one arriving field can be redrawn alone.
        self._field_rows: dict[str, _FieldRow] = {}
        self._fields_layout: Optional[QVBoxLayout] = None
        self._generate_all: Optional[QPushButton] = None
        # ONE timer drives every spinner on screen. A timer per row would have to be started,
        # stopped and destroyed with each row — which is exactly where such things get left
        # running after the widget they were animating is gone.
        self._spin_frame = 0
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(_SPIN_MS)
        self._spin_timer.timeout.connect(self._tick_spinner)
        # The whole result plus which of its notes is on screen, so the switcher can re-render
        # a different note without asking omnia again.
        # Set when a scrolling field list is built; repositions its bottom gradient.
        self._reposition_fade: Optional[Callable[[], None]] = None
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

        # A short fade so the panel arrives instead of snapping into place. Kept on the WINDOW
        # opacity (not a graphics effect) because effects on a translucent frameless window
        # render badly on macOS.
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(_FADE_MS)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

    def event(self, event) -> bool:
        """Dismiss the panel when its window stops being the active one.

        A popover must vanish when you click away from it. ``focusOutEvent`` is not enough for a
        frameless tool window — focus can move without the widget being told — but
        ``WindowDeactivate`` fires whenever the window loses activation, including a click on
        another application or on the clipper's own tray icon, which is exactly the case that
        left a stale panel on screen.
        """
        if event.type() == QEvent.Type.WindowDeactivate and self.isVisible():
            self.hide()
        return super().event(event)

    # -- states --------------------------------------------------------------------------

    def show_loading(self, word: str, position: tuple[int, int]) -> None:
        """Show the panel immediately in a loading state, so the click feels instant."""
        self._word = word
        # A new lookup discards any regeneration still in flight (the service drops its answer),
        # so let the spinner stop here rather than tick on for a note that has left the screen.
        self._regen.reset(allowed=False)
        self._sync_spinner()
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
        # A new lookup is a new set of notes; the service has already dropped any regeneration
        # still in flight for the old ones, so their spinners and reasons go with them.
        self._regen.reset(view.can_regenerate)
        self._sync_spinner()
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
        self._present(self._position, fresh=False)

    # -- rendering -----------------------------------------------------------------------

    def _clear(self) -> theme.Palette:
        """Install a fresh content widget, re-apply the appearance, and return its palette."""
        # The old content owns the previous fade; drop the handle with it.
        self._reposition_fade = None
        # Same for the field rows and the footer button: they are about to be destroyed, and a
        # handle to a deleted widget is how a late generate answer becomes a crash.
        self._field_rows = {}
        self._fields_layout = None
        self._generate_all = None
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

        self._body.addWidget(self._header_band(card, view, colors))
        if len(view.cards) > 1:
            # More than one note matched: let the user step between them instead of only ever
            # seeing the top hit (the right note is not always the highest-ranked one).
            self._body.addWidget(self._switcher(view, index, colors))
        self._body.addWidget(self._fields_area(card), 1)

        actions = self._action_row(card)
        if actions is not None:
            self._body.addWidget(actions)

    def _action_row(self, card: LookupCardView) -> Optional[QWidget]:
        """The footer: regenerate the whole note on the left, open it in Anki on the right."""
        if self._on_generate is None and self._on_open_in_anki is None:
            return None
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if self._on_generate is not None:
            self._generate_all = self._generate_all_button(card)
            self._sync_generate_all(card)
            row.addWidget(self._generate_all)
        row.addStretch(1)
        if self._on_open_in_anki is not None:
            button = QPushButton("Open in Anki")
            button.setObjectName("lookupAction")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda: self._handle_open(card.note_id))
            row.addWidget(button)
        holder = QWidget()
        holder.setLayout(row)
        return holder

    def _generate_all_button(self, card: LookupCardView) -> QPushButton:
        """The footer's "Generate all": every field of the shown note, whatever its state.

        It runs to completion — a field that cannot be generated reports its reason and the
        rest still run — because the request names the note, not a field, and omnia answers
        about each field separately. :meth:`_sync_generate_all` fills in the label, tooltip and
        enabled state, here and on every later change.
        """
        button = QPushButton()
        button.setObjectName("lookupAction")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda: self._start_generation(card.note_id, None))
        return button

    def _header_band(
        self, card: LookupCardView, view: LookupView, colors: theme.Palette
    ) -> QWidget:
        """The word, its state, and its scheduling as ONE band.

        Deck/interval/reps/lapses used to be a row of chips, which made the header and the note
        switcher two near-identical pill stripes with no hierarchy between them. They are now a
        single quiet line under the word, so the eye reads: word -> state -> details.
        """
        band = QFrame()
        band.setObjectName("headerBand")
        outer = QVBoxLayout(band)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(4)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        title = QLabel(card.title or view.word)
        title.setObjectName("lookupTitle")
        title.setWordWrap(True)
        top.addWidget(title, 1)
        top.addWidget(_state_pill(card.state, colors), 0, Qt.AlignmentFlag.AlignTop)
        holder = QWidget()
        holder.setLayout(top)
        outer.addWidget(holder)

        bits = []
        if card.interval_days:
            bits.append(f"{card.interval_days}d interval")
        if card.reps:
            bits.append(f"{card.reps} reviews")
        if card.lapses:
            bits.append(f"{card.lapses} lapses")
        if card.deck:
            bits.append(card.deck.split("::")[-1])
        if bits:
            meta = QLabel("  ·  ".join(bits))
            meta.setObjectName("metaLine")
            meta.setWordWrap(True)
            if card.deck:
                meta.setToolTip(card.deck)
            outer.addWidget(meta)
        return band

    def _switcher(self, view: LookupView, index: int, colors: theme.Palette) -> QWidget:
        """One small button per matched note; the current one is highlighted."""
        holder, row = flow_widget(spacing=4)
        for position, card in enumerate(view.cards):
            label = card.title or card.note_type or f"note {card.note_id}"
            button = QPushButton(label if len(label) <= 22 else label[:21] + "…")
            # Segmented control, not chips: the active one is filled, the rest are outlined, so
            # this band cannot be mistaken for the header's information line.
            button.setObjectName("segmentActive" if position == index else "segment")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(f"{card.note_type or 'note'} — {card.deck or 'no deck'}")
            if position != index:
                button.clicked.connect(
                    lambda _checked=False, target=position: self._switch_to(target)
                )
            row.addWidget(button)
        _ = colors
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
        """The scrollable field list — everything omnia sent, in its chosen order.

        "Everything" now includes the fields that are EMPTY. They used to be left out, which
        hid precisely the rows worth acting on: a field nobody ever filled is the first thing
        the user wants to generate, and it cannot be asked for if it is not on screen.
        """
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        self._fields_layout = layout
        self._field_rows = {}
        for field in card.fields:
            row = self._field_block(card, field)
            self._field_rows[field.name] = row
            layout.addWidget(row.widget)

        area = QScrollArea()
        area.setWidget(inner)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Size to the fields, so a short card is a short panel instead of a tall one with dead
        # space; the panel's own max height is what turns a long card into a scrolling one.
        area.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        self._install_bottom_fade(area)
        return area

    def _install_bottom_fade(self, area: QScrollArea) -> None:
        """Fade the last visible field into the background when the list scrolls.

        A long note is clipped mid-card at the scroll boundary, which reads as broken rather
        than as "there is more". A gradient to the panel's own background says it softly, and
        it hides itself when everything already fits.
        """
        colors = theme.palette()
        fade = QFrame(area.viewport())
        fade.setObjectName("scrollFade")
        fade.setFixedHeight(_FADE_HEIGHT)
        fade.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        fade.setStyleSheet(
            "QFrame#scrollFade { border: none; background: qlineargradient("
            "x1:0, y1:0, x2:0, y2:1,"
            f" stop:0 {theme.rgba(colors.bg, 0.0)}, stop:1 {theme.rgba(colors.bg, 1.0)}); }}"
            .replace("}}", "}")
        )

        def reposition() -> None:
            viewport = area.viewport()
            fade.setGeometry(
                0, viewport.height() - _FADE_HEIGHT, viewport.width(), _FADE_HEIGHT
            )
            bar = area.verticalScrollBar()
            # Nothing to scroll, or already at the end -> no "more below" to hint at.
            fade.setVisible(bar.maximum() > 0 and bar.value() < bar.maximum() - 2)
            fade.raise_()

        area.verticalScrollBar().valueChanged.connect(reposition)
        area.verticalScrollBar().rangeChanged.connect(reposition)
        self._reposition_fade = reposition

    def _field_block(self, card: LookupCardView, field: LookupFieldView) -> _FieldRow:
        """One field: its name and generate button, the value (or a media badge), any status."""
        holder = QFrame()
        holder.setObjectName("fieldCard")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(10, 8, 10, 9)
        # The caption and its value need visible air between them; sitting nearly flush made the
        # pair read as one block.
        layout.setSpacing(_CAPTION_GAP)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        name = QLabel(field.name)
        name.setObjectName("fieldName")
        head.addWidget(name, 1)
        button = self._field_generate_button(card, field)
        if button is not None:
            head.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)
        if field.text:
            value = QLabel(_leaded(field.text))
            value.setObjectName("fieldText")
            value.setWordWrap(True)
            value.setTextFormat(Qt.TextFormat.RichText)
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
                # A field with nothing in it. This used to read "—", which was written for a
                # case that could not happen (omnia omitted empty fields) and now happens
                # constantly — and a dash next to a generate button reads like a fault rather
                # than like an invitation.
                badge = QLabel("Empty")
                badge.setObjectName("lookupSubtitle")
                layout.addWidget(badge)
        # ...but an image is worth seeing, so offer to load it (fetching is a round-trip to
        # Anki, so it happens on demand rather than for every field of every result).
        if field.audio and self._can_play_audio():
            layout.addWidget(self._audio_block(field.audio))
        if field.images and self._can_show_images():
            layout.addWidget(self._image_block(field.images))
        status = self._regen.status(card.note_id, field.name)
        if status:
            line = QLabel(status)
            line.setObjectName("fieldStatus")
            line.setWordWrap(True)
            layout.addWidget(line)
        return _FieldRow(widget=holder, button=button)

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
            fetched = isinstance(data, (bytes, bytearray)) and bool(data)
            ok = fetched and play_bytes(bytes(data), filename)
            button.setText(original if ok else "unavailable")
            if not ok:
                # A button is too narrow for the reason, so it goes where there is room.
                button.setToolTip(
                    "The audio could not be played on this system."
                    if fetched
                    else f"Anki did not return {filename}."
                )
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
        """Render fetched bytes as a bounded thumbnail, or say WHY there is no thumbnail.

        The two failures need telling apart. No bytes means the fetch itself failed -- the file
        is missing from the collection, or the media route is not answering -- and the user can
        do something about that. Bytes that will not decode mean the file arrived but Qt has no
        plugin for its format, which is a different problem with a different fix. A single
        "Image unavailable" for both is what sent this bug to me as "it just says unavailable".
        """
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
        elif isinstance(data, (bytes, bytearray)) and data:
            label.setText("Image format not supported")
            label.setObjectName("lookupSubtitle")
        else:
            label.setText("Image not found in Anki")
            label.setObjectName("lookupSubtitle")
        layout.addWidget(label)

    # -- regeneration --------------------------------------------------------------------

    def _field_generate_button(
        self, card: LookupCardView, field: LookupFieldView
    ) -> Optional[QPushButton]:
        """The small button at the head of a field row, or ``None`` when the seam is absent.

        What it says, and whether it can be pressed, is decided by
        :class:`~omnia_desktop_clipper.lookup.regeneration.RegenerationState`; this only
        draws it.
        """
        if self._on_generate is None:
            return None
        state = self._regen.field_control(card.note_id, field, self._frame())
        button = QPushButton(state.label)
        button.setObjectName("fieldGenerate")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedWidth(_GENERATE_WIDTH)
        button.setToolTip(state.tooltip)
        button.setEnabled(state.enabled)
        if state.enabled:
            button.clicked.connect(
                lambda _checked=False, name=field.name: self._start_generation(
                    card.note_id, [name]
                )
            )
        return button

    def _start_generation(self, note_id: int, fields: Optional[list[str]]) -> None:
        """Mark the fields as running, redraw them, and ask omnia; the answer arrives later."""
        if self._on_generate is None or not self._regen.allowed:
            return
        card = self._card(note_id)
        if card is None:
            return
        # "Generate all" spins every VISIBLE field but asks omnia for the whole note, and omnia
        # answers only about fields it can generate — a Source or Notes field with no rule is
        # never mentioned. Flagging the difference here is what stops those rows reporting a
        # failure that did not happen.
        started = self._regen.start(
            note_id,
            [f.name for f in card.fields] if fields is None else fields,
            explicit=fields is not None,
        )
        if not started:
            return
        self._refresh(note_id, started)
        self._on_generate(note_id, fields)

    def apply_generation(self, note_id: int, outcome: object) -> None:
        """Fold a ``/generate`` answer into the panel that is open — no re-query, no reopening.

        Applied to whichever matched note it names, even when the switcher has since moved to
        another one: the data is right either way, and the rows are only redrawn while that
        note is the one on screen. An answer for a note this panel never showed is dropped.

        Args:
            note_id: The note the answer is about.
            outcome: A :class:`~omnia_desktop_clipper.lookup.generate.GenerateOutcome` (typed
                ``object`` because it crosses a Qt signal).
        """
        if self._view is None or not isinstance(outcome, GenerateOutcome):
            return
        index = self._index_of(note_id)
        if index is None:
            return
        self._view.cards[index] = outcome.applied_to(self._view.cards[index])
        self._refresh(note_id, self._regen.finish(note_id, outcome))

    def report_generation_failure(
        self, note_id: int, message: str, names: Iterable[str] = ()
    ) -> None:
        """Show why a request could not run, on the fields IT asked for.

        ``names`` empty means it asked for the whole note, so everything still waiting on that
        note is settled. Naming them matters when two requests are out at once: a failure of
        one must not stop the spinner on a field the other is still generating.
        """
        self._refresh(note_id, self._regen.fail(note_id, message, names))

    def _refresh(self, note_id: int, names: Iterable[str]) -> None:
        """Rebuild the named rows (and the footer) from the panel's current state."""
        self._sync_spinner()
        wanted = list(names)
        card = self._shown_card()
        if card is None or card.note_id != note_id or self._fields_layout is None:
            return  # the state is kept; the rows belong to a note that is not on screen
        by_name = {field.name: field for field in card.fields}
        for name in wanted:
            row = self._field_rows.get(name)
            field = by_name.get(name)
            if row is None or field is None:
                continue
            replacement = self._field_block(card, field)
            self._fields_layout.replaceWidget(row.widget, replacement.widget)
            row.widget.setParent(None)
            row.widget.deleteLater()
            replacement.widget.show()
            self._field_rows[name] = replacement
        self._sync_generate_all(card)
        if wanted and self.isVisible() and self.isActiveWindow():
            # Re-measure: a generated field is usually taller than the "Empty" it replaced.
            #
            # Only while this panel is the ACTIVE window. _present takes focus, and this runs
            # when an answer lands — up to minutes after the click. On the platforms where
            # WindowDeactivate does not reliably hide the panel, re-presenting would yank focus
            # out of whatever the user has since started typing in. Skipping it costs nothing
            # worse than a taller field having to be scrolled to.
            self._present(self._position, fresh=False)

    def _sync_generate_all(self, card: LookupCardView) -> None:
        """Keep the footer button in step: disabled, and spinning, while the note is running."""
        button = self._generate_all
        if button is None:
            return
        state = self._regen.note_control(card.note_id, self._frame())
        button.setText(state.label)
        button.setToolTip(state.tooltip)
        button.setEnabled(state.enabled)

    def _sync_spinner(self) -> None:
        """Run the shared spinner timer only while something is actually generating."""
        busy = self._regen.anything_running()
        if busy and not self._spin_timer.isActive():
            self._spin_timer.start()
        elif not busy and self._spin_timer.isActive():
            self._spin_timer.stop()

    def _frame(self) -> str:
        """The spinner frame every running control on screen currently shares."""
        return SPIN_FRAMES[self._spin_frame]

    def _tick_spinner(self) -> None:
        """Advance that one frame."""
        self._spin_frame = (self._spin_frame + 1) % len(SPIN_FRAMES)
        card = self._shown_card()
        if card is None:
            return
        for name in self._regen.running(card.note_id):
            row = self._field_rows.get(name)
            if row is not None and row.button is not None:
                row.button.setText(self._frame())
        self._sync_generate_all(card)

    def _shown_card(self) -> Optional[LookupCardView]:
        """The matched note currently rendered, or ``None`` in any other state."""
        if self._view is None or not 0 <= self._index < len(self._view.cards):
            return None
        return self._view.cards[self._index]

    def _index_of(self, note_id: int) -> Optional[int]:
        """Where ``note_id`` sits among the matched notes, or ``None`` if it is not one."""
        if self._view is None:
            return None
        for position, card in enumerate(self._view.cards):
            if card.note_id == note_id:
                return position
        return None

    def _card(self, note_id: int) -> Optional[LookupCardView]:
        """The matched note with ``note_id``, or ``None``."""
        index = self._index_of(note_id)
        if index is None or self._view is None:
            return None
        return self._view.cards[index]

    # -- presentation --------------------------------------------------------------------

    def _present(self, position: tuple[int, int], *, fresh: bool = True) -> None:
        """Size to content, keep the panel fully on screen, show it and take focus.

        Args:
            position: Where the gesture happened; the panel anchors beside it.
            fresh: Whether this is a new appearance (fade in) rather than a re-render of an
                already-visible panel (no fade — re-fading on every note switch would flicker).
        """
        fresh = fresh and not self.isVisible()
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
        self._play_fade(fresh)
        if self._reposition_fade is not None:
            self._reposition_fade()
        # Unlike the overlay, this panel IS meant to take focus (scroll + Esc).
        promote_over_all_apps(self, activate=True)
        self.activateWindow()

    def _play_fade(self, fresh: bool) -> None:
        """Fade the panel in on a fresh appearance; leave it opaque on a re-render."""
        if not fresh:
            self.setWindowOpacity(1.0)
            return
        self._fade.stop()
        self.setWindowOpacity(0.0)
        self._fade.start()

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
