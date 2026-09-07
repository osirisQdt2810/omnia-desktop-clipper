"""Tests for the lookup panel's regeneration rules (no PyQt6 — this is the Qt-free half).

The panel draws whatever these say, so this is where the two behaviours that would otherwise
only be visible by hand are pinned: the controls going dead when omnia has regeneration
switched off, and a field never being left spinning at an answer that did not mention it.
"""

from __future__ import annotations

from omnia_desktop_clipper.lookup.client import LookupFieldView
from omnia_desktop_clipper.lookup.generate import FieldGeneration, GenerateOutcome
from omnia_desktop_clipper.lookup.regeneration import (
    GENERATE_GLYPH,
    NO_ANSWER,
    SPIN_FRAMES,
    RegenerationState,
)

_NOTE = 123
_OTHER_NOTE = 456


def _outcome(*results, note_id: int = _NOTE) -> GenerateOutcome:
    return GenerateOutcome(note_id=note_id, results=tuple(results))


def _generated(field: str, text: str = "new text") -> FieldGeneration:
    return FieldGeneration(field=field, status="generated", text=text)


def _blocked(field: str, message: str = "needs Definition") -> FieldGeneration:
    return FieldGeneration(field=field, status="blocked", message=message)


class TestTheMasterSwitch:
    """``can_regenerate: false`` — Smart Notes' "Regenerate from clippers" is off."""

    def test_the_controls_start_disabled(self) -> None:
        """Nothing looked up yet: there is no permission to regenerate anything."""
        state = RegenerationState()

        assert state.allowed is False
        assert state.field_control(_NOTE, LookupFieldView("F", "")).enabled is False
        assert state.note_control(_NOTE).enabled is False

    def test_a_disallowing_answer_disables_every_control(self) -> None:
        state = RegenerationState()
        state.reset(allowed=False)

        assert state.field_control(_NOTE, LookupFieldView("F", "")).enabled is False
        assert state.note_control(_NOTE).enabled is False

    def test_a_disabled_control_names_the_setting_to_switch_on(self) -> None:
        """A dead button with no explanation is the bug this feature would be reported as."""
        state = RegenerationState()
        state.reset(allowed=False)

        for control in (
            state.field_control(_NOTE, LookupFieldView("F", "")),
            state.note_control(_NOTE),
        ):
            assert "Regenerate from clippers" in control.tooltip

    def test_a_disabled_control_still_shows_its_glyph(self) -> None:
        """Dimmed, not hidden — otherwise the panel simply looks like it has no such feature."""
        state = RegenerationState()
        state.reset(allowed=False)

        assert (
            state.field_control(_NOTE, LookupFieldView("F", "")).label == GENERATE_GLYPH
        )
        assert GENERATE_GLYPH in state.note_control(_NOTE).label

    def test_allowing_it_enables_them(self) -> None:
        state = RegenerationState()
        state.reset(allowed=True)

        assert state.field_control(_NOTE, LookupFieldView("F", "")).enabled is True
        assert state.note_control(_NOTE).enabled is True


class TestTheFieldButton:
    """It is offered for every field — including the ones omnia says it cannot generate."""

    def _allowed(self) -> RegenerationState:
        state = RegenerationState()
        state.reset(allowed=True)
        return state

    def test_a_field_that_cannot_be_generated_keeps_a_live_button(self) -> None:
        """The reason is only knowable by asking, and the reason is the thing worth having."""
        field = LookupFieldView("Audio", "", empty=True, state="blocked")

        control = self._allowed().field_control(_NOTE, field)

        assert control.enabled is True

    def test_such_a_field_says_up_front_what_is_wrong(self) -> None:
        field = LookupFieldView("Audio", "", empty=True, state="no_rule")

        tooltip = self._allowed().field_control(_NOTE, field).tooltip

        assert "no generation rule" in tooltip.lower()

    def test_an_empty_field_offers_to_generate_a_filled_one_to_regenerate(self) -> None:
        state = self._allowed()

        empty = state.field_control(_NOTE, LookupFieldView("Definition", ""))
        filled = state.field_control(_NOTE, LookupFieldView("Definition", "a word"))

        assert empty.tooltip.startswith("Generate Definition")
        assert filled.tooltip.startswith("Regenerate Definition")

    def test_a_running_field_spins_and_cannot_be_pressed_again(self) -> None:
        state = self._allowed()
        state.start(_NOTE, ["Definition"])

        control = state.field_control(
            _NOTE, LookupFieldView("Definition", ""), SPIN_FRAMES[2]
        )

        assert control.enabled is False
        assert control.label == SPIN_FRAMES[2]

    def test_another_notes_run_does_not_spin_this_ones_field(self) -> None:
        state = self._allowed()
        state.start(_OTHER_NOTE, ["Definition"])

        control = state.field_control(_NOTE, LookupFieldView("Definition", ""))

        assert control.enabled is True
        assert control.label == GENERATE_GLYPH


class TestGenerateAll:
    def _running(self) -> RegenerationState:
        state = RegenerationState()
        state.reset(allowed=True)
        state.start(_NOTE, ["Definition", "Audio"])
        return state

    def test_it_is_disabled_while_the_note_is_running(self) -> None:
        """Otherwise a second click sends a second whole-note request over the first."""
        assert self._running().note_control(_NOTE).enabled is False

    def test_it_spins_with_the_shared_frame(self) -> None:
        assert (
            self._running()
            .note_control(_NOTE, SPIN_FRAMES[1])
            .label.startswith(SPIN_FRAMES[1])
        )

    def test_it_comes_back_when_the_note_finishes(self) -> None:
        state = self._running()

        state.finish(_NOTE, _outcome(_generated("Definition"), _generated("Audio")))

        assert state.note_control(_NOTE).enabled is True

    def test_another_notes_run_leaves_this_one_pressable(self) -> None:
        assert self._running().note_control(_OTHER_NOTE).enabled is True


class TestStatusLines:
    """Every non-``generated`` status shows its message on that field — that IS the feature."""

    def _started(self, *names: str) -> RegenerationState:
        state = RegenerationState()
        state.reset(allowed=True)
        state.start(_NOTE, list(names))
        return state

    def test_a_running_field_says_so(self) -> None:
        assert self._started("Definition").status(_NOTE, "Definition") == "Generating…"

    def test_a_generated_field_says_nothing(self) -> None:
        """Its new content is the message; "Generated." under every row would be noise."""
        state = self._started("Definition")

        state.finish(_NOTE, _outcome(_generated("Definition")))

        assert state.status(_NOTE, "Definition") == ""

    def test_a_refused_field_shows_omnias_reason(self) -> None:
        state = self._started("Audio")

        state.finish(_NOTE, _outcome(_blocked("Audio", "needs Definition")))

        assert state.status(_NOTE, "Audio") == "needs Definition"

    def test_a_reason_survives_until_the_field_is_asked_again(self) -> None:
        state = self._started("Audio")
        state.finish(_NOTE, _outcome(_blocked("Audio")))

        state.start(_NOTE, ["Audio"])

        # Asking again replaces the old answer with the spinner, not with a stale claim.
        assert state.status(_NOTE, "Audio") == "Generating…"

    def test_a_whole_note_failure_lands_on_every_waiting_field(self) -> None:
        """401/409/Anki closed: the request never ran, so each spinner owes an explanation."""
        state = self._started("Definition", "Audio")

        touched = state.fail(_NOTE, "Anki is not running.")

        assert touched == {"Definition", "Audio"}
        assert state.status(_NOTE, "Definition") == "Anki is not running."
        assert state.status(_NOTE, "Audio") == "Anki is not running."
        assert state.anything_running() is False

    def test_a_field_the_answer_never_mentions_is_not_left_spinning(self) -> None:
        """Generate all, and omnia answers about two of the three fields asked for."""
        state = self._started("Word", "Definition", "Audio")

        state.finish(_NOTE, _outcome(_generated("Definition"), _blocked("Audio")))

        assert state.status(_NOTE, "Word") == NO_ANSWER
        assert state.busy(_NOTE) is False


class TestGenerateAllRunsToCompletion:
    """The refused fields report a reason and the rest still change — in ONE answer."""

    def test_a_mixed_answer_is_applied_field_by_field(self) -> None:
        state = RegenerationState()
        state.reset(allowed=True)
        state.start(_NOTE, ["Definition", "Audio", "Example"])

        touched = state.finish(
            _NOTE,
            _outcome(
                _generated("Definition"),
                _blocked("Audio", "needs Definition"),
                FieldGeneration(field="Example", status="no_rule"),
            ),
        )

        assert touched == {"Definition", "Audio", "Example"}
        assert state.status(_NOTE, "Definition") == ""
        assert state.status(_NOTE, "Audio") == "needs Definition"
        assert state.status(
            _NOTE, "Example"
        )  # a status with no message still says something


class TestWhatDrivesTheSpinnerTimer:
    def test_nothing_runs_when_nothing_was_started(self) -> None:
        assert RegenerationState().anything_running() is False

    def test_any_note_running_keeps_it_going(self) -> None:
        state = RegenerationState()
        state.reset(allowed=True)
        state.start(_OTHER_NOTE, ["Definition"])

        assert state.anything_running() is True
        assert state.busy(_NOTE) is False

    def test_it_stops_when_the_last_field_answers(self) -> None:
        state = RegenerationState()
        state.reset(allowed=True)
        state.start(_NOTE, ["Definition"])
        state.start(_OTHER_NOTE, ["Audio"])

        state.finish(_NOTE, _outcome(_generated("Definition")))
        assert state.anything_running() is True

        state.fail(_OTHER_NOTE, "Anki is not running.")
        assert state.anything_running() is False


class TestStartingARun:
    def test_it_returns_the_fields_to_redraw(self) -> None:
        state = RegenerationState()
        state.reset(allowed=True)

        assert state.start(_NOTE, ["Definition", "Audio"]) == ["Definition", "Audio"]

    def test_starting_nothing_changes_nothing(self) -> None:
        state = RegenerationState()
        state.reset(allowed=True)

        assert state.start(_NOTE, []) == []
        assert state.anything_running() is False

    def test_a_new_lookup_forgets_the_previous_result(self) -> None:
        """New word, new notes: an old spinner or reason would belong to nothing on screen."""
        state = RegenerationState()
        state.reset(allowed=True)
        state.start(_NOTE, ["Definition"])
        state.fail(_NOTE, "Anki is not running.")

        state.reset(allowed=True)

        assert state.status(_NOTE, "Definition") == ""
        assert state.anything_running() is False
