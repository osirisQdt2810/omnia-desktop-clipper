"""The correction panel: what the wand opens — what is wrong with the phrase, and why.

The lookup panel answers *"is this word in my collection?"*. This answers the question that
comes just before it: the user is reading or writing something and is not sure the sentence is
right.

Design notes (the parts that are decisions, not taste):

* **One card per fix, and the reason behind a button.** "Your sentence should be X" teaches
  nothing, and one paragraph explaining six unrelated problems is read by nobody. The reason
  arrives with the answer and is merely hidden, so opening it is a redraw, never a request.
* **Two registers, and switching re-asks.** The same sentence is wrong in different ways
  depending on whether it is being said or written, so spoken and written are different
  questions with different right answers. omnia remembers each, so switching back costs nothing
  after the first of each.
* **Green, where the lookup panel is blue.** They open from the same pill over the same
  selection and answer different questions; a glance has to be enough to tell them apart.
* **The rewrite is shown marked, and copied plain.** What the user wants on the clipboard is a
  sentence they can paste; markers pasted into an email are worse than no button at all.

Every decision about *what* to show is omnia's (see :mod:`omnia_desktop_clipper.lookup.check`),
and every decision about *when* — which answer is still wanted, which explanation is open — is
made in :mod:`omnia_desktop_clipper.lookup.correction_state`, which has no Qt in it and is
tested without one. This module renders.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Optional

from PyQt6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, Qt, QTimer
from PyQt6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..lookup.check import MODES, Correction
from ..lookup.correction_state import (
    MODE_TOOLTIPS,
    CorrectionState,
    mode_label,
    rich_rewrite,
)
from . import theme
from .macos_window import promote_over_all_apps

_WIDTH = 440
_MAX_HEIGHT = 560
_CURSOR_OFFSET = 16
_SCREEN_MARGIN = 12
_FADE_MS = 130
#: How long the Copy button says it worked before going back to offering to do it again.
_COPIED_MS = 1400


class CorrectionPanel(QWidget):
    """A frameless, focusable panel showing what omnia says about a phrase."""

    def __init__(
        self,
        on_check: Optional[Callable[[str, str, bool], None]] = None,
        on_save: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """Build the (reusable, singleton) panel.

        Args:
            on_check: ``(phrase, mode, refresh)`` asking omnia to correct the phrase, OFF the
                UI thread. The answer comes back through :meth:`apply_correction` /
                :meth:`report_failure` rather than a callback, because a request started here
                (the register toggle) and one started outside it land the same way.
            on_save: ``(phrase, mode)`` asking omnia to keep this correction as a note, OFF the
                UI thread. ``None`` hides the button. The answer comes back through
                :meth:`report_saved`.
        """
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self._on_check = on_check
        self._on_save = on_save
        self._state = CorrectionState()
        self._position = (0, 0)
        self._copy_button: Optional[QPushButton] = None
        self._save_button: Optional[QPushButton] = None
        self.setFixedWidth(_WIDTH)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._root = QFrame(self)
        self._root.setObjectName("lookupRoot")
        outer.addWidget(self._root)

        # The root holds exactly ONE child: the current state's content widget. Each render
        # swaps in a brand-new widget rather than emptying a long-lived layout — a reused
        # QVBoxLayout keeps reporting the OLD size hint after its items are taken out, which
        # collapses the panel to its margins on every state change after the first. (The lookup
        # panel documents the same trap; this is the same Qt behaviour, not a copied habit.)
        self._shell = QVBoxLayout(self._root)
        self._shell.setContentsMargins(18, 16, 18, 16)
        self._shell.setSpacing(0)
        self._content: Optional[QWidget] = None
        self._body: QVBoxLayout = QVBoxLayout()

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.hide)

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(_FADE_MS)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

    def event(self, event) -> bool:
        """Dismiss the panel when its window stops being the active one."""
        if event.type() == QEvent.Type.WindowDeactivate and self.isVisible():
            self.hide()
        return super().event(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt's own name
        """Abandon whatever request was in flight when the panel went away.

        Dismissing the panel is the user saying they are done with that answer, and
        :meth:`_present` shows, raises and ACTIVATES unconditionally — so an answer accepted
        after a dismissal does not merely draw into a hidden widget, it pops the panel back onto
        the screen at the old position and takes keyboard focus from whatever the user has since
        started typing in.

        Bumping the ticket here rather than checking ``isVisible()`` in each entry point,
        because ``hideEvent`` is the one place every dismissal route passes through: Escape, the
        WindowDeactivate auto-hide, and an explicit ``hide()`` from a new selection.
        """
        self._state.ticket += 1
        super().hideEvent(event)

    # -- states --------------------------------------------------------------------------

    def start(self, phrase: str, position: tuple[int, int], mode: str = "") -> int:
        """Show the panel immediately in a waiting state, and return the request's ticket.

        The caller passes the ticket back with the answer, so an answer to a request the user
        has moved on from can be dropped rather than drawn over the one they are reading.
        """
        ticket = self._state.start(phrase, mode)
        self._position = position
        self._render()
        self._present(position)
        return ticket

    def apply_correction(self, ticket: int, correction: Correction) -> None:
        """Show an answer, if it is still the one being waited for."""
        if not self._state.accept(ticket, correction):
            return
        self._render()
        self._present(self._position, fresh=False)

    def report_failure(self, ticket: int, message: str) -> None:
        """Show that the check could not run, if it is still the one being waited for."""
        if not self._state.fail(ticket, message):
            return
        self._render()
        self._present(self._position, fresh=False)

    def ticket(self) -> int:
        """The request currently being waited for."""
        return self._state.ticket

    # -- rendering -----------------------------------------------------------------------

    def _clear(self) -> None:
        """Install a fresh content widget and re-apply the appearance."""
        # The old content owns these buttons; drop the handles with them, or a later "Copied"
        # would be written onto a widget Qt has already deleted.
        self._copy_button = None
        self._save_button = None
        if self._content is not None:
            self._shell.removeWidget(self._content)
            self._content.setParent(None)
            self._content.deleteLater()
        self._content = QWidget(self._root)
        self._body = QVBoxLayout(self._content)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(10)
        self._shell.addWidget(self._content)
        self.setStyleSheet(theme.stylesheet(theme.palette()))

    def _render(self) -> None:
        """Draw whichever state the panel is in."""
        self._clear()
        self._body.addWidget(self._header())
        if self._state.error:
            self._body.addWidget(self._message(self._state.error))
            return
        correction = self._state.correction
        if correction is None:
            self._body.addWidget(self._message("Checking…"))
            return
        # The APPROVAL first. It is the success path of the feature — "your sentence is fine" —
        # and it was being swallowed by the empty-payload guard below, so a correct sentence was
        # reported as though the backend had misbehaved.
        if correction.already_good and not correction.has_changes:
            good = QLabel(
                f"This reads correctly as {mode_label(self._state.mode).lower()}. "
                "Nothing to change."
            )
            good.setObjectName("correctGood")
            good.setWordWrap(True)
            self._body.addWidget(good)
            # Only when there is one. An approval whose rewrite is the original echoed back has
            # a sentence to show; one without is still an approval, just a quieter one.
            if correction.rewritten:
                self._body.addWidget(self._final(correction))
            return
        if not correction.rewritten and not correction.has_changes:
            # Neither a correction nor an approval: omnia answered with a shape that carries
            # nothing to show. Saying so beats an empty scroll area under a "CORRECTED" heading
            # with nothing under it, which reads as the panel being broken.
            self._body.addWidget(
                self._message("Omnia did not return a correction for that phrase.")
            )
            return
        self._body.addWidget(self._fix_list(correction))
        self._body.addWidget(self._final(correction))

    def _header(self) -> QWidget:
        """The title and the register toggle."""
        band = QFrame()
        band.setObjectName("correctBand")
        row = QHBoxLayout(band)
        row.setContentsMargins(11, 9, 11, 9)
        row.setSpacing(8)
        title = QLabel("Correction")
        title.setObjectName("lookupTitle")
        row.addWidget(title, 1)
        for mode in MODES:
            button = QPushButton(mode_label(mode))
            active = mode == self._state.mode
            button.setObjectName("correctActive" if active else "correctAction")
            button.setToolTip(MODE_TOOLTIPS.get(mode, ""))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, m=mode: self._switch(m))
            row.addWidget(button)
        return band

    @staticmethod
    def _message(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("lookupSubtitle")
        label.setWordWrap(True)
        return label

    def _fix_list(self, correction: Correction) -> QWidget:
        """One card per fix, scrolling when there are more than fit."""
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        for index, fix in enumerate(correction.visible_fixes):
            column.addWidget(self._fix_card(index, fix))
        # Said out loud rather than silently cut: a list that stops without explanation reads as
        # omnia having found that many, and the rest are on the card.
        if correction.hidden_fixes:
            count = correction.hidden_fixes
            more = QLabel(
                f"{count} more {'fix' if count == 1 else 'fixes'} — "
                "all of them are kept if you save this."
            )
            more.setObjectName("lookupSubtitle")
            more.setWordWrap(True)
            column.addWidget(more)
        column.addStretch(1)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(holder)
        # Bounded so a phrase with a dozen fixes scrolls INSIDE the panel rather than pushing
        # the rewrite — the thing the user most wants to see — off the bottom of the screen.
        area.setMaximumHeight(300)
        return area

    def _fix_card(self, index: int, fix) -> QWidget:
        """One change, its kind, and its reason behind a button."""
        card = QFrame()
        card.setObjectName("fixCard")
        column = QVBoxLayout(card)
        column.setContentsMargins(11, 9, 11, 10)
        column.setSpacing(6)

        change = QHBoxLayout()
        change.setSpacing(6)
        before = QLabel(fix.before)
        before.setObjectName("fixBefore")
        before.setWordWrap(True)
        # Struck through, so "this was wrong" is visible without reading the colour — which a
        # colour-blind reader cannot do, and which is the only other thing marking it.
        font = before.font()
        font.setStrikeOut(True)
        before.setFont(font)
        change.addWidget(before)
        arrow = QLabel("→")
        arrow.setObjectName("fixArrow")
        change.addWidget(arrow)
        after = QLabel("(removed)" if fix.is_deletion else fix.after)
        after.setObjectName("fixAfter")
        after.setWordWrap(True)
        change.addWidget(after, 1)
        column.addLayout(change)

        meta = QHBoxLayout()
        meta.setSpacing(8)
        if fix.kind:
            kind = QLabel(fix.kind)
            kind.setObjectName("fixKind")
            meta.addWidget(kind)
        meta.addStretch(1)
        if fix.why:
            is_open = index in self._state.open_explanations
            button = QPushButton("Hide" if is_open else "Why?")
            button.setObjectName("correctAction")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, i=index: self._toggle_explanation(i)
            )
            meta.addWidget(button)
        column.addLayout(meta)

        if fix.why and index in self._state.open_explanations:
            why = QLabel(fix.why)
            why.setObjectName("fixWhy")
            why.setWordWrap(True)
            column.addWidget(why)
        return card

    def _final(self, correction: Correction) -> QWidget:
        """The rewritten phrase, with the changed words marked, and a Copy button."""
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        label = QLabel("CORRECTED")
        label.setObjectName("fixKind")
        head.addWidget(label, 1)
        # Only when there is something to put on the clipboard. A button that does nothing and
        # says nothing is worse than no button — and a payload without a rewrite is reachable
        # from an older or a broken omnia even though the current one always sends it (its
        # parser refuses an answer with no rewritten sentence).
        if correction.rewritten:
            # Drawn from STATE, not relabelled after the click: this panel redraws for its own
            # reasons and a label written onto a widget is wiped by the next one.
            if self._on_save is not None:
                kept = self._state.is_saved
                saving = self._state.saving and not kept
                # All three labels come from STATE. "Saved" always did; "Saving…" did not, and
                # that was the hole — `_clear()` destroys this button on every redraw, so
                # opening an explanation mid-save rebuilt it as an enabled "Save to Anki" and
                # the next press wrote a second note.
                save = QPushButton(
                    "Saved" if kept else "Saving…" if saving else "Save to Anki"
                )
                save.setObjectName("correctActive" if kept else "correctAction")
                save.setEnabled(not kept and not saving)
                save.setCursor(Qt.CursorShape.PointingHandCursor)
                save.clicked.connect(self._save)
                head.addWidget(save)
                self._save_button = save
            copy = QPushButton("Copy")
            copy.setObjectName("correctAction")
            copy.setCursor(Qt.CursorShape.PointingHandCursor)
            copy.clicked.connect(self._copy)
            head.addWidget(copy)
            self._copy_button = copy
        column.addLayout(head)

        text = QLabel(rich_rewrite(correction))
        text.setObjectName("correctedText")
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(text)
        # Beside the correction, never instead of it. A failed save leaves a perfectly good
        # correction on screen — routing it through `_state.error` made `_render` return before
        # drawing the fixes, the rewrite, Copy, or the button to try again with.
        if self._state.save_error:
            said = QLabel(self._state.save_error)
            said.setObjectName("correctWarn")
            said.setWordWrap(True)
            column.addWidget(said)
        elif self._state.saved:
            said = QLabel(self._state.saved)
            said.setObjectName("correctGood")
            said.setWordWrap(True)
            column.addWidget(said)
        return holder

    # -- actions -------------------------------------------------------------------------

    def _switch(self, mode: str) -> None:
        """Check the same phrase as the other register.

        A fresh request, not a re-render: the two are different questions, and omnia keys its
        cache on the register, so switching back and forth costs one request each and then
        nothing.
        """
        if not self._state.wants(mode) or self._on_check is None:
            return
        ticket = self._state.start(self._state.phrase, mode)
        self._render()
        self._present(self._position, fresh=False)
        _ = ticket  # the panel already holds it; the service answers through apply_correction
        self._on_check(self._state.phrase, mode, False)

    def _toggle_explanation(self, index: int) -> None:
        self._state.toggle_explanation(index)
        self._render()
        self._present(self._position, fresh=False)

    def _save(self) -> None:
        """Ask omnia to keep this correction as a note.

        The phrase is sent, not the correction: omnia looks it up again (a cache hit) and builds
        the note itself, which keeps note content out of this process's hands entirely.
        """
        # `save_pending`, not `is_saved`: `is_saved` only becomes true when the ANSWER lands,
        # and the window before that is exactly when a redraw used to re-arm the button.
        if (
            self._on_save is None
            or self._state.correction is None
            or self._state.save_pending
        ):
            return
        self._state.saving_now()
        self._render()
        self._present(self._position, fresh=False)
        self._on_save(self._state.phrase, self._state.mode)

    def report_saved(self, ticket: int, summary: str) -> None:
        """Show that the correction was kept, if it is still the one on screen.

        Guarded by the ticket like every other answer: the note is written either way, and this
        only decides whether anyone is told. Drawing into a panel the user has moved on from
        would put "Saved" over somebody else's sentence.
        """
        if ticket != self._state.ticket:
            return
        self._state.keep(summary)
        self._render()
        self._present(self._position, fresh=False)

    def report_save_failed(self, ticket: int, message: str) -> None:
        """Show why a save did not happen, beside the correction, and let it be tried again.

        NOT through ``_state.error``: that channel means "there is no correction", and ``_render``
        returns on it before drawing anything else. A save failing is not a correction failing —
        the fixes, the rewrite and the Save button all have to survive it, or "tried again" is
        not something the user can do.
        """
        if ticket != self._state.ticket:
            return
        self._state.save_failed(message)
        self._render()
        self._present(self._position, fresh=False)

    def _copy(self) -> None:
        """Put the corrected phrase on the clipboard, plain.

        The rewrite, not the marked-up version: what the user wants is a sentence they can
        paste, and markers pasted into an email are worse than no button at all.
        """
        correction = self._state.correction
        if correction is None or not correction.rewritten:
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setText(correction.rewritten)
        button = self._copy_button
        if button is None:
            return
        # Said ON the button rather than in a toast: it is the answer to pressing THAT control,
        # and a toast for it would cover the sentence it just copied.
        button.setText("Copied")
        QTimer.singleShot(_COPIED_MS, lambda: self._restore_copy(button))

    def _restore_copy(self, button: QPushButton) -> None:
        """Put the Copy button back, if it is still the one on screen."""
        # A redraw between the click and the timer destroys the button. `self._copy_button` is
        # cleared by `_clear`, so comparing against it is what stops this touching a deleted
        # widget — which Qt reports as a RuntimeError from a lambda nobody can trace.
        if button is self._copy_button:
            button.setText("Copy")

    # -- placement -----------------------------------------------------------------------

    def _present(self, position: tuple[int, int], fresh: bool = True) -> None:
        """Size the panel to its content, anchor it near ``position``, and show it."""
        fresh = fresh and not self.isVisible()
        # Show the freshly built content BEFORE measuring: widgets added to an already-visible
        # parent are not shown automatically, and QLayout ignores hidden children when computing
        # a size hint — which collapses every state after the first to its margins alone.
        if self._content is not None:
            for child in self._content.findChildren(QWidget):
                child.show()
            self._content.show()
        self.show()
        self.setMinimumHeight(0)
        self.setMaximumHeight(_MAX_HEIGHT)
        layout = self.layout()
        if layout is not None:
            layout.activate()
        self.adjustSize()
        height = min(self.sizeHint().height(), _MAX_HEIGHT)
        self.setFixedHeight(height)
        self.move(*self._anchor(position, height))
        self.raise_()
        self._play_fade(fresh)
        promote_over_all_apps(self, activate=True)
        self.activateWindow()

    def _play_fade(self, fresh: bool) -> None:
        """Fade in on a fresh appearance; stay opaque on a re-render."""
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
            y = max(
                area.top() + _SCREEN_MARGIN, area.bottom() - height - _SCREEN_MARGIN
            )
        return x, y
