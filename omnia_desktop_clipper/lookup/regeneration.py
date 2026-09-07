"""What the lookup panel knows about regenerating a note — the part with no Qt in it.

The panel is a renderer: omnia decides what a note contains and whether it may be regenerated,
and the widget code should only draw the answer. So the *decisions* that answer has to make —
may this button be pressed, what does it say when it may not, which fields are running, what
did omnia give as the reason for the ones that did not generate — live here, in a class that
owns that state and can be exercised without a QApplication.

That is not a technicality. The two behaviours most likely to break silently are "the controls
are dead because omnia has regeneration switched off" and "a field is left spinning because
nothing ever answered for it", and neither can be tested through a widget in a headless suite.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .client import LookupFieldView
from .generate import GenerateOutcome, describe_status

# The idle affordance, and the frames that replace it while a field is running. Geometric
# Shapes rather than Braille — the Braille spinners render as tofu in several Windows UI fonts,
# and a spinner nobody can read is worse than no spinner.
GENERATE_GLYPH = "↻"
SPIN_FRAMES = ("◐", "◓", "◑", "◒")

REGENERATE_ALL = f"{GENERATE_GLYPH} Generate all"
RUNNING = "Generating…"

REGEN_OFF_HINT = (
    "Regenerating is switched off in Anki.\n"
    "Turn on Smart Notes → “Regenerate from clippers” to allow it "
    "(and update Omnia if you don't see that option)."
)
NO_ANSWER = "Omnia did not answer for this field."

_ALL_HINT = (
    "Regenerate every field of this note with Omnia Smart Notes.\n"
    "Fields that cannot be generated say why; the rest still run."
)


@dataclass(frozen=True)
class ControlState:
    """How to draw one generate control: whether it can be pressed, and what it says."""

    enabled: bool
    label: str
    tooltip: str


class RegenerationState:
    """The regeneration half of a lookup result: what is allowed, running, and reported.

    Keyed by NOTE rather than by "the visible one" throughout: a lookup can match several
    notes, the user can step between them with the switcher while one is generating, and an
    answer that arrives then belongs to the note it named — not to whatever is on screen.
    """

    def __init__(self, allowed: bool = False) -> None:
        """Start with regeneration disallowed, which is what an answerless panel shows."""
        self._allowed = allowed
        # note id -> {field name: was it named EXPLICITLY by the user}. The flag decides
        # whether "omnia never mentioned this field" is worth saying: see `finish`.
        self._running: dict[int, dict[str, bool]] = {}
        self._messages: dict[int, dict[str, str]] = {}

    @property
    def allowed(self) -> bool:
        """Whether omnia will accept a regeneration for the result on screen."""
        return self._allowed

    def reset(self, allowed: bool) -> None:
        """Adopt a new lookup result: new notes, and nothing of the old one's worth keeping."""
        self._allowed = allowed
        self._running.clear()
        self._messages.clear()

    # -- lifecycle -----------------------------------------------------------------------

    def start(
        self, note_id: int, names: Iterable[str], *, explicit: bool = True
    ) -> list[str]:
        """Mark ``names`` as running for ``note_id``; return them, in order, to be redrawn.

        Their previous reasons go: the question is being asked again, and leaving "needs
        Definition" under a spinner claims an answer that no longer applies.

        Args:
            note_id: The note being generated.
            names: The fields to put a spinner on.
            explicit: Whether the user named these fields. "Generate all" spins every VISIBLE
                field while asking omnia for the whole note, and omnia answers about the fields
                it can generate — so a Source or Notes field with no rule is never mentioned.
                That is not a failure and must not be reported as one; only a field the user
                pointed at earns "omnia did not answer for this field".
        """
        wanted = list(names)
        if not wanted:
            return []
        running = self._running.setdefault(note_id, {})
        for name in wanted:
            running[name] = running.get(name, False) or explicit
        messages = self._messages.setdefault(note_id, {})
        for name in wanted:
            messages.pop(name, None)
        return wanted

    def finish(self, note_id: int, outcome: GenerateOutcome) -> set[str]:
        """Record an answer; return every field name whose row now needs redrawing.

        Settles only the fields THIS request asked for. Several may be generating on one note
        at once — the design allows it and the buttons permit it — so clearing the whole
        running set would make the first answer back declare the others unanswered, stop the
        spinner over a field still being generated, and re-enable a button whose second press
        would pay for the same generation twice.
        """
        answered = set(outcome.fields)
        reasons = outcome.messages()
        messages = self._messages.setdefault(note_id, {})
        for name in answered:
            if name in reasons:
                messages[name] = reasons[name]
            else:
                messages.pop(name, None)  # it generated: the new content IS the message
        # A whole-note request names no fields on the wire, and it genuinely did ask for all of
        # them, so it settles everything still running for the note — that is what stops the
        # third field of a "Generate all" spinning when omnia answered about only two. A
        # single-field request settles only itself. (A whole-note answer arriving while a
        # single-field request is out settles that field too, which is harmless: a whole-note
        # answer speaks about it anyway.)
        running = self._running.get(note_id, {})
        requested = set(outcome.requested) or set(running)
        for name in requested - answered:
            # Asked for and never mentioned. Worth saying only when the user pointed at this
            # field; for "Generate all" it just means omnia has no rule for it, which the
            # field's own state already says.
            if running.get(name, False):
                messages[name] = NO_ANSWER
        return self._settle(note_id, requested | answered)

    def fail(self, note_id: int, message: str, names: Iterable[str] = ()) -> set[str]:
        """Record that a request never ran; return the rows that were waiting on it.

        ``names`` are the fields that request asked for; empty means the whole note, which
        settles everything still running for it. Same reason as :meth:`finish`: one request
        failing must not stop the spinner on a field a different request is still generating.
        """
        running = self._running.get(note_id, {})
        waiting = (set(names) & set(running)) if names else set(running)
        messages = self._messages.setdefault(note_id, {})
        for name in waiting:
            messages[name] = message
        return self._settle(note_id, waiting)

    def _settle(self, note_id: int, names: set[str]) -> set[str]:
        """Drop ``names`` from what is running for ``note_id``; return them."""
        remaining = {
            name: explicit
            for name, explicit in self._running.get(note_id, {}).items()
            if name not in names
        }
        if remaining:
            self._running[note_id] = remaining
        else:
            self._running.pop(note_id, None)
        return names

    # -- what the renderer asks -----------------------------------------------------------

    def running(self, note_id: int) -> set[str]:
        """The field names currently generating for ``note_id`` (empty when none are)."""
        return set(self._running.get(note_id, {}))

    def busy(self, note_id: int) -> bool:
        """Whether anything is generating for ``note_id``."""
        return bool(self.running(note_id))

    def anything_running(self) -> bool:
        """Whether ANY note is generating — what decides if the spinner timer should run."""
        return any(self._running.values())

    def status(self, note_id: int, name: str) -> str:
        """The line under a field: "Generating…", the reason it did not generate, or "" ."""
        if name in self.running(note_id):
            return RUNNING
        return self._messages.get(note_id, {}).get(name, "")

    def field_control(
        self, note_id: int, field: LookupFieldView, spinner: str = SPIN_FRAMES[0]
    ) -> ControlState:
        """How to draw the button at the head of one field row.

        It is offered for EVERY field, including the ones omnia already says it cannot
        generate: the reason ("needs Definition") is worth more than the button, only omnia
        knows it, and a row that silently has no button teaches nothing at all. What does
        disable it is the master switch — there is no point sending a request omnia will
        refuse — and then the tooltip names the setting to turn on.
        """
        if not self._allowed:
            return ControlState(False, GENERATE_GLYPH, REGEN_OFF_HINT)
        if field.name in self.running(note_id):
            return ControlState(False, spinner, f"Generating {field.name}…")
        verb = "Generate" if field.is_empty else "Regenerate"
        hint = "" if field.state == "ready" else f"\n{describe_status(field.state)}"
        return ControlState(
            True, GENERATE_GLYPH, f"{verb} {field.name} with Omnia Smart Notes.{hint}"
        )

    def note_control(self, note_id: int, spinner: str = SPIN_FRAMES[0]) -> ControlState:
        """How to draw "Generate all" for the note on screen."""
        if not self._allowed:
            return ControlState(False, REGENERATE_ALL, REGEN_OFF_HINT)
        if self.busy(note_id):
            return ControlState(False, f"{spinner} {RUNNING}", "Generating this note…")
        return ControlState(True, REGENERATE_ALL, _ALL_HINT)
