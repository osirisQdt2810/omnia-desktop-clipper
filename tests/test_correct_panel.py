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

import pytest  # noqa: F401 - imported so the module reads as a test module

from conftest import requires_qt_widgets

requires_qt_widgets()

from omnia_desktop_clipper.lookup.check import to_correction
from omnia_desktop_clipper.ui.correct_panel import CorrectionPanel

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
    widget = CorrectionPanel(on_check=lambda *args: asked.append(args))
    widget.asked = asked  # type: ignore[attr-defined]
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


class TestWhatItDrawsForEachAnswer:
    def test_an_answer_with_nothing_in_it_says_so(self, panel):
        # `to_correction` tolerates a payload with no rewrite and no fixes by design. Rendering
        # it as an empty scroll area under a "CORRECTED" heading reads as the panel being broken.
        ticket = panel.start("x", (10, 10))

        panel.apply_correction(ticket, to_correction({}))

        text = " ".join(
            label.text() for label in panel.findChildren(type(panel._message("")))
        )
        assert "did not return a correction" in text

    def test_an_approved_sentence_offers_no_copy_button(self, panel):
        # `already_good` may legitimately omit the rewrite — nothing was rewritten — and a Copy
        # button that does nothing and says nothing is worse than no button.
        ticket = panel.start("I went.", (10, 10))

        panel.apply_correction(
            ticket,
            to_correction({"already_good": True, "fixes": [], "mode": "written"}),
        )

        assert panel._copy_button is None

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
