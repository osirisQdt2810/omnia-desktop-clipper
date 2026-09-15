"""The correction panel's Qt behaviour: what it refuses to do once it has been dismissed.

Everything the panel DECIDES is tested against `CorrectionState` in ``test_lookup_check.py``,
with no widget involved. What only shows up here is what the widget does with an answer, and the
specific trap is that `_present` shows, raises and **activates** unconditionally: an answer
accepted after a dismissal does not merely draw into a hidden widget, it pops the panel back
onto the screen at the old position and takes keyboard focus from whatever the user has since
started typing in.

Skipped where ``QtWidgets`` cannot load — a headless Linux runner has no ``libEGL``, and asking
for a widget there is an ImportError at COLLECTION time, which fails the whole suite rather than
skipping one file. macOS and Windows run this for real.
"""

from __future__ import annotations

import pytest

from conftest import requires_qt_widgets

# BEFORE the imports below, which is the whole point: they pull in QtWidgets, so a module that
# imported them first would raise at collection rather than skip. Hence the E402s.
requires_qt_widgets()

from omnia_desktop_clipper.lookup.check import to_correction  # noqa: E402
from omnia_desktop_clipper.ui.correct_panel import CorrectionPanel  # noqa: E402

_ANSWER = {
    "rewritten": "I went to the shop.",
    "mode": "written",
    "changed": True,
    "fixes": [
        {
            "before": "have went",
            "after": "went",
            "why": "Simple past.",
            "kind": "grammar",
        }
    ],
    "highlight": [["I ", False], ["went", True], [" to the shop.", False]],
}


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def panel(qapp):
    asked: list[tuple] = []
    saves: list[tuple] = []
    widget = CorrectionPanel(
        on_check=lambda *args: asked.append(args),
        on_save=lambda *args: saves.append(args),
    )
    widget.asked = asked  # type: ignore[attr-defined]
    widget.asked_saves = saves  # type: ignore[attr-defined]
    yield widget
    widget.hide()
    widget.deleteLater()


class TestADismissedPanelStaysDismissed:
    def test_an_answer_after_a_dismissal_does_not_reopen_it(self, panel):
        # The failure this was written for: select a phrase, press the wand, then click back
        # into your editor. WindowDeactivate hides the panel; ninety seconds later the answer
        # lands, and the panel reappears over what you are typing and takes the keyboard.
        ticket = panel.start("I have went to the shop.", (100, 100))
        panel.hide()

        panel.apply_correction(ticket, to_correction(_ANSWER))

        assert not panel.isVisible(), "a dismissed panel put itself back on screen"

    def test_a_failure_after_a_dismissal_does_not_reopen_it_either(self, panel):
        ticket = panel.start("I have went.", (100, 100))
        panel.hide()

        panel.report_failure(ticket, "Anki did not answer in time.")

        assert not panel.isVisible()

    def test_the_answer_is_not_merely_hidden_but_refused(self, panel):
        # Guarding on visibility alone would leave the answer accepted and the panel holding a
        # correction for a phrase the user abandoned — which the next `start` would have to
        # clear, and which `_render` would draw if anything else showed the widget.
        ticket = panel.start("I have went.", (100, 100))
        panel.hide()

        panel.apply_correction(ticket, to_correction(_ANSWER))

        assert panel._state.correction is None

    def test_a_fresh_request_after_a_dismissal_works_normally(self, panel):
        # The guard must not be a one-way door: dismissing and asking again is ordinary.
        panel.start("I have went.", (100, 100))
        panel.hide()

        ticket = panel.start("I have went.", (100, 100))
        panel.apply_correction(ticket, to_correction(_ANSWER))

        assert panel.isVisible()
        assert panel._state.correction is not None
        assert panel._state.correction.rewritten == "I went to the shop."


def _said(panel) -> str:
    """Everything the panel currently has on screen, as text.

    Asserting on what it SAYS rather than on which widget exists is the difference between the
    two versions of the approval test below: the old one checked `_copy_button is None`, which is
    trivially true when the block holding it was never built at all, and so passed while the
    panel reported a correct sentence as a backend failure.
    """
    from PyQt6.QtWidgets import QLabel

    return " ".join(label.text() for label in panel.findChildren(QLabel))


def _buttons(panel) -> list:
    """Every button label currently on the panel.

    Separate from :func:`_said`, which reads QLabels: a control's label is not something the
    panel SAYS, and conflating them let one assertion pass because the summary sentence happened
    to contain the word the button was supposed to show.
    """
    from PyQt6.QtWidgets import QPushButton

    return [button.text() for button in panel.findChildren(QPushButton)]


class TestWhatItDrawsForEachAnswer:
    def test_an_answer_with_nothing_in_it_says_so(self, panel):
        # `to_correction` tolerates a payload with no rewrite and no fixes by design. Rendering
        # it as an empty scroll area under a "CORRECTED" heading reads as the panel being broken.
        ticket = panel.start("x", (10, 10))

        panel.apply_correction(ticket, to_correction({}))

        assert "did not return a correction" in _said(panel)

    def test_an_approved_sentence_is_reported_as_an_approval(self, panel):
        # The success path of the whole feature — "your sentence is fine" — and it was being
        # swallowed by the empty-payload guard, so a correct sentence came out as "Omnia did not
        # return a correction", as though the backend had misbehaved.
        ticket = panel.start("I went.", (10, 10))

        panel.apply_correction(
            ticket,
            to_correction({"already_good": True, "fixes": [], "mode": "written"}),
        )

        said = _said(panel)
        assert "Nothing to change" in said
        assert (
            "did not return a correction" not in said
        ), "an approval read as a failure"
        assert panel._copy_button is None, "a Copy button with nothing behind it"

    def test_an_approval_that_echoes_the_sentence_shows_it(self, panel):
        # What the current add-on actually sends: it derives `already_good` from the two
        # sentences matching, so an approval carries the original echoed back.
        ticket = panel.start("I went to the shop.", (10, 10))

        panel.apply_correction(
            ticket,
            to_correction(
                {
                    "already_good": True,
                    "fixes": [],
                    "mode": "written",
                    "rewritten": "I went to the shop.",
                    "highlight": [["I went to the shop.", False]],
                }
            ),
        )

        said = _said(panel)
        assert "Nothing to change" in said
        assert "I went to the shop." in said, "it hid the sentence it approved of"
        assert (
            panel._copy_button is not None
        ), "nothing to copy an approved sentence with"

    def test_a_real_correction_offers_one(self, panel):
        ticket = panel.start("I have went to the shop.", (10, 10))

        panel.apply_correction(ticket, to_correction(_ANSWER))

        assert panel._copy_button is not None

    def test_switching_register_asks_again_rather_than_redrawing(self, panel):
        ticket = panel.start("I have went to the shop.", (10, 10))
        panel.apply_correction(ticket, to_correction(_ANSWER))

        panel._switch("spoken")

        assert panel.asked == [("I have went to the shop.", "spoken", False)]
        assert panel.ticket() != ticket, "the new request reused the old ticket"

    def test_pressing_the_register_already_showing_asks_nothing(self, panel):
        ticket = panel.start("I have went to the shop.", (10, 10))
        panel.apply_correction(ticket, to_correction(_ANSWER))

        panel._switch("written")

        assert panel.asked == []


class TestKeepingACorrection:
    """The Save button, and the state that remembers it worked."""

    def _saved_panel(self, panel):
        ticket = panel.start("I have went to the shop.", (10, 10))
        panel.apply_correction(ticket, to_correction(_ANSWER))
        return ticket

    def test_pressing_it_asks_with_the_phrase_and_the_register(self, panel):
        self._saved_panel(panel)

        panel._save()

        assert panel.asked_saves == [("I have went to the shop.", "written")]

    def test_what_anki_said_is_shown(self, panel):
        ticket = self._saved_panel(panel)

        panel.report_saved(ticket, "Saved to Omnia::Phrase Check.")

        assert "Saved to Omnia::Phrase Check." in _said(panel)

    def test_it_survives_a_redraw(self, panel):
        # The panel redraws for its own reasons; a label written onto the button would be wiped
        # by the next one without anybody noticing.
        ticket = self._saved_panel(panel)
        panel.report_saved(ticket, "Saved to Omnia::Phrase Check.")

        panel._toggle_explanation(0)

        assert "Saved to Omnia::Phrase Check." in _said(panel), "the sentence was wiped"
        assert "Saved" in _buttons(panel), "the button forgot it had saved"

    def test_an_answer_for_an_abandoned_request_is_dropped(self, panel):
        stale = self._saved_panel(panel)
        panel.start("something else", (10, 10))

        panel.report_saved(stale, "Saved to the wrong place.")

        assert "wrong place" not in _said(panel)

    def test_a_failure_says_why(self, panel):
        ticket = self._saved_panel(panel)

        panel.report_save_failed(ticket, "Anki was busy — nothing was saved.")

        assert "nothing was saved" in _said(panel)

    def test_a_second_press_does_nothing(self, panel):
        ticket = self._saved_panel(panel)
        panel.report_saved(ticket, "Saved.")

        panel._save()

        assert panel.asked_saves == [], "it saved the same correction twice"

    def test_a_new_answer_offers_to_save_again(self, panel):
        ticket = self._saved_panel(panel)
        panel.report_saved(ticket, "Saved.")

        second = panel.start("I have went to the shop.", (10, 10))
        panel.apply_correction(second, to_correction(_ANSWER))

        assert "Save to Anki" in _buttons(panel)
        assert "Saved." not in _said(panel)

    def test_a_panel_with_no_saver_offers_no_button(self, qapp):
        from omnia_desktop_clipper.ui.correct_panel import CorrectionPanel

        widget = CorrectionPanel(on_check=lambda *a: None)
        try:
            ticket = widget.start("x", (10, 10))
            widget.apply_correction(ticket, to_correction(_ANSWER))

            assert "Save to Anki" not in _buttons(widget)
        finally:
            widget.hide()
            widget.deleteLater()


class TestTheDisplayLimitOnScreen:
    def test_only_the_visible_fixes_are_drawn(self, panel):
        payload = dict(_ANSWER)
        payload["fixes"] = [
            {"before": str(i), "after": "x", "why": "w"} for i in range(6)
        ]
        payload["shown"] = 2
        ticket = panel.start("x", (10, 10))

        panel.apply_correction(ticket, to_correction(payload))

        said = _said(panel)
        assert "4 more fixes" in said, said
        assert "kept if you save" in said
