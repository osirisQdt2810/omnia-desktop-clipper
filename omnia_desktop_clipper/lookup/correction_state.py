"""What the correction panel is showing, with no Qt in it.

The same split :mod:`omnia_desktop_clipper.lookup.regeneration` makes, and for the same reason:
the panel's *rules* — which explanation is open, whether an answer still belongs to the phrase
on screen, how a rewrite renders with its changes marked — are exactly the parts worth testing,
and testing them through a QApplication tests the wrong thing slowly.

The staleness rule is the sharp one. The register toggle re-asks for the **same phrase**, so two
answers for one selection can be in flight at once, and the slow first one may land long after
the user switched — silently reverting the panel and flipping the toggle back under them.
Neither the phrase nor the register can tell those apart (the answer's own register is adopted
when it arrives), so each request carries a ticket and only the latest is ever wanted.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Optional

from .check import MODES, SPOKEN, WRITTEN, Correction

#: What the register buttons say. Short because it is a two-item switch, and a sentence on each
#: would be longer than the thing it labels.
MODE_LABELS = {WRITTEN: "Writing", SPOKEN: "Speaking"}
MODE_TOOLTIPS = {
    WRITTEN: "Judge it as writing — essays, email, anything read",
    SPOKEN: "Judge it as speech — conversation, where contractions are correct",
}


def mode_label(mode: str) -> str:
    """The label for ``mode``, defaulting rather than guessing."""
    return MODE_LABELS.get(mode, MODE_LABELS[WRITTEN])


def rich_rewrite(correction: Optional[Correction]) -> str:
    """The rewrite as rich text, with the changed words marked.

    Bold **and** a background: bold alone disappears in a sentence that already has some, and
    this has to survive being skim-read. Escaped throughout — the phrase is whatever the user
    selected in some other application, and the rewrite is a model's prose.
    """
    if correction is None:
        return ""
    parts = []
    for text, is_new in correction.highlight or ():
        escaped = html.escape(text).replace("\n", "<br>")
        parts.append(
            f'<b style="background: rgba(31,157,99,0.20);">{escaped}</b>'
            if is_new
            else escaped
        )
    return "".join(parts)


@dataclass
class CorrectionState:
    """Everything the correction panel needs to draw itself.

    Held by the panel, mutated by it, and read by its renderer — so a test can drive the whole
    interaction (ask, switch register, open an explanation, receive a late answer) without ever
    building a widget.
    """

    #: The phrase being corrected.
    phrase: str = ""
    #: The register the panel is showing. Set from what was ASKED for while waiting, then from
    #: what came back — the two can differ, because an empty request lets omnia decide.
    mode: str = WRITTEN
    #: The answer, once there is one.
    correction: Optional[Correction] = None
    #: The message, when the check could not run.
    error: str = ""
    #: Which fixes have their explanation showing, by index.
    open_explanations: set[int] = field(default_factory=set)
    #: Which request the panel is waiting for. Only its answer is ever accepted.
    ticket: int = 0
    #: omnia's sentence about where a saved card went, or "" when this one has not been saved.
    #: Kept in the STATE rather than written onto the button: the panel redraws for its own
    #: reasons (an explanation opening, a register switching), and a label poked into a widget
    #: is wiped by the next redraw without anybody noticing.
    saved: str = ""
    #: Whether a save is in flight. Here for exactly the reason ``saved`` is: the panel redraws
    #: for its own reasons, ``_clear()`` destroys the button, and a "Saving…" label written onto
    #: that widget comes back as an enabled "Save to Anki" — which is a second note.
    saving: bool = False
    #: Why the last save did not happen. SEPARATE from ``error``, which means "there is no
    #: correction" and makes the renderer draw nothing else. A failed save leaves a perfectly
    #: good correction on screen, so it reports beside it rather than in place of it.
    save_error: str = ""

    @property
    def waiting(self) -> bool:
        """Whether there is nothing to show yet."""
        return self.correction is None and not self.error

    def start(self, phrase: str, mode: str) -> int:
        """Begin a new request and return its ticket.

        The register shown while waiting is the one ASKED for. An empty request means omnia
        decides, and until it answers there is nothing truthful to light up, so the toggle shows
        the default and is corrected by the answer.
        """
        self.ticket += 1
        self.phrase = phrase
        self.mode = mode if mode in MODES else WRITTEN
        self.correction = None
        self.error = ""
        self.open_explanations = set()
        # A new request is a new card to keep. Carrying "Saved" across would put a disabled
        # button over a correction nobody has kept — and carrying either of the other two would
        # show a new correction as mid-save, or explain a failure that was about the last one.
        self.saved = ""
        self.saving = False
        self.save_error = ""
        return self.ticket

    def accept(self, ticket: int, correction: Correction) -> bool:
        """Take an answer if it is still the one being waited for. Returns whether it landed."""
        if ticket != self.ticket:
            return False
        self.correction = correction
        self.error = ""
        # The register the answer was JUDGED in, which is not necessarily the one asked for.
        # Without this the toggle compares against a stale value: the lit button does nothing
        # and the unlit one re-asks for what is already on screen.
        self.mode = correction.mode if correction.mode in MODES else WRITTEN
        self.open_explanations = set()
        return True

    def fail(self, ticket: int, message: str) -> bool:
        """Take a failure if it is still the one being waited for.

        Guarded exactly like :meth:`accept`. An abandoned request that times out would otherwise
        replace a perfectly good correction with a red message about a request nobody is waiting
        for.
        """
        if ticket != self.ticket:
            return False
        self.error = message
        self.correction = None
        return True

    def keep(self, summary: str) -> None:
        """Remember that this correction was saved, and what Anki said about where."""
        self.saved = summary or "Saved to Anki."
        self.saving = False
        self.save_error = ""

    def saving_now(self) -> None:
        """Note that a save has been asked for and not yet answered."""
        self.saving = True
        self.save_error = ""

    def save_failed(self, message: str) -> None:
        """Note that the save did not happen, and why.

        The correction is untouched: it is still correct, and only the save failed.
        """
        self.saving = False
        self.save_error = message or "The save failed."

    @property
    def save_pending(self) -> bool:
        """Whether a save has been asked for and must not be asked for again."""
        return self.saving or self.is_saved

    @property
    def is_saved(self) -> bool:
        """Whether this correction has already been kept."""
        return bool(self.saved)

    def toggle_explanation(self, index: int) -> None:
        """Show, or hide, one fix's reason.

        The reason arrived WITH the answer and is merely hidden, so this is a redraw and never a
        request — a button that had to fetch a string already in memory would be a spinner over
        nothing.
        """
        if index in self.open_explanations:
            self.open_explanations.discard(index)
        else:
            self.open_explanations.add(index)

    def wants(self, mode: str) -> bool:
        """Whether pressing ``mode`` should ask again.

        Pressing the register already on screen must do nothing: it is not a refresh button, and
        re-asking for the answer already being read is a spinner the user did not request.
        """
        return mode in MODES and mode != self.mode
