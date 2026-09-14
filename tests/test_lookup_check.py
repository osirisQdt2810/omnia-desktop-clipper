"""The ``/check`` client and the correction panel's rules, with no Qt and no network.

What is checked here is the part a screenshot cannot: that the runs shown are always exactly the
sentence that arrived, that "nothing to change" is a different answer from "nothing came back",
that an answer for a request the user has moved on from is dropped, and that each HTTP failure
comes out as a sentence someone can act on — including the one nobody would think to test, an
Anki too old to have the endpoint at all.
"""

from __future__ import annotations

import http.server
import json
import socket
import threading

import pytest

from omnia_desktop_clipper.lookup.check import (
    SPOKEN,
    WRITTEN,
    CheckClient,
    CheckError,
    error_message,
    to_correction,
)
from omnia_desktop_clipper.lookup.correction_state import (
    CorrectionState,
    mode_label,
    rich_rewrite,
)

_ANSWER = {
    "original": "I have went to the shop for buy milk.",
    "rewritten": "I went to the shop to buy milk.",
    "mode": "written",
    "already_good": False,
    "changed": True,
    "fixes": [
        {
            "before": "have went",
            "after": "went",
            "why": "A finished action at a stated time takes the simple past.",
            "kind": "grammar",
            "is_deletion": False,
        },
        {
            "before": "for buy",
            "after": "to buy",
            "why": "Purpose takes “to”.",
            "kind": "word choice",
        },
    ],
    "highlight": [
        ["I ", False],
        ["went", True],
        [" to the shop ", False],
        ["to buy", True],
        [" milk.", False],
    ],
}


def _client(answer=None, seen=None):
    """A client whose transport records the call and replies with ``answer``."""

    def transport(url, body, headers):
        if seen is not None:
            seen.append({"url": url, "body": body, "headers": headers})
        return {} if answer is None else answer

    return CheckClient("http://127.0.0.1:8766", transport=transport)


class TestTheRequest:
    def test_it_posts_the_documented_body(self):
        seen: list[dict] = []
        _client(_ANSWER, seen).check("I have went.", SPOKEN, True)

        assert seen[0]["url"] == "http://127.0.0.1:8766/check"
        assert seen[0]["body"] == {
            "text": "I have went.",
            "mode": "spoken",
            "refresh": True,
        }

    def test_no_register_is_sent_empty_rather_than_guessed(self):
        """The configured default lives in omnia; guessing here would override a setting."""
        seen: list[dict] = []
        _client(_ANSWER, seen).check("I have went.")

        assert seen[0]["body"]["mode"] == ""

    def test_a_register_nobody_recognises_is_sent_empty_too(self):
        seen: list[dict] = []
        _client(_ANSWER, seen).check("I have went.", "shouted")

        assert seen[0]["body"]["mode"] == ""

    def test_it_asks_for_json_and_nothing_else(self):
        # No credential of any kind: the socket is loopback and omnia does not authenticate.
        seen: list[dict] = []
        _client(_ANSWER, seen).check("x")

        assert seen[0]["headers"] == {"Content-Type": "application/json"}

    def test_an_empty_selection_never_leaves_the_app(self):
        asked: list[str] = []
        client = CheckClient(
            "http://h:1",
            transport=lambda url, body, headers: asked.append(url) or _ANSWER,
        )

        with pytest.raises(CheckError, match="nothing selected"):
            client.check("   ")
        assert asked == []

    def test_the_base_url_trailing_slash_is_normalised(self):
        seen: list[dict] = []
        CheckClient(
            "http://h:1/",
            transport=lambda url, body, headers: seen.append({"url": url}) or _ANSWER,
        ).check("x")

        assert seen[0]["url"] == "http://h:1/check"


class TestTheAnswer:
    def test_every_fix_arrives_in_order(self):
        correction = to_correction(_ANSWER)

        assert [fix.before for fix in correction.fixes] == ["have went", "for buy"]
        assert correction.fixes[0].why.startswith("A finished action")
        assert correction.fixes[0].kind == "grammar"

    def test_the_runs_join_back_to_exactly_the_rewrite(self):
        # The whole reason omnia computes them rather than each clipper diffing: two answers to
        # a question with one right one would have the two panels slowly disagree.
        correction = to_correction(_ANSWER)

        assert (
            "".join(text for text, _new in correction.highlight) == correction.rewritten
        )

    def test_a_payload_with_no_runs_falls_back_to_the_plain_sentence(self):
        correction = to_correction({"rewritten": "I went.", "fixes": []})

        assert correction.highlight == (("I went.", False),)

    def test_an_entry_that_is_not_a_run_is_dropped_rather_than_crashing(self):
        correction = to_correction(
            {
                "rewritten": "I went.",
                "highlight": [["I ", False], "nope", ["went.", True]],
            }
        )

        assert correction.highlight == (("I ", False), ("went.", True))

    def test_a_fix_that_is_not_an_object_is_dropped(self):
        correction = to_correction(
            {"rewritten": "x", "fixes": [None, 3, {"before": "a"}]}
        )

        assert [fix.before for fix in correction.fixes] == ["a"]

    def test_a_deletion_says_so_on_its_label(self):
        correction = to_correction(
            {
                "rewritten": "x",
                "fixes": [{"before": "very", "after": "", "is_deletion": True}],
            }
        )

        assert "(removed)" in correction.fixes[0].label

    def test_an_empty_payload_is_an_answer_rather_than_an_exception(self):
        # A panel that raised on an unexpected shape turns a partial answer into no answer.
        correction = to_correction({})

        assert correction.rewritten == ""
        assert correction.fixes == ()
        assert correction.has_changes is False


class TestHttpFailures:
    """Every refusal has to name the next action."""

    def test_the_services_own_error_wins(self):
        assert (
            error_message(502, "HTTP 401: invalid api key")
            == "HTTP 401: invalid api key"
        )

    def test_an_anki_without_phrase_check_is_an_update_not_a_setting(self):
        # The one failure nobody would think to test and everybody will hit: the clipper is
        # updated separately from the add-on, so an omnia with no /check at all is routine.
        message = error_message(404)

        assert "Update it" in message
        assert "switched off" not in message, "it sent them hunting for a toggle"

    def test_503_names_the_switch_that_fixes_it(self):
        message = error_message(503)

        assert "switched off" in message
        assert "Tools → Omnia" in message

    def test_502_and_503_are_not_the_same_sentence(self):
        # "It is off" and "it ran and failed" are answered by different actions; one message for
        # both sends the user hunting through settings for a problem that was never there.
        assert error_message(502) != error_message(503)

    def test_an_unknown_code_still_says_what_happened(self):
        assert "418" in error_message(418)


class TestTheCorrectionState:
    """The panel's rules, which is where the interesting failures live."""

    def test_a_fresh_request_shows_the_register_it_asked_for(self):
        state = CorrectionState()
        state.start("I have went.", SPOKEN)

        assert state.mode == SPOKEN
        assert state.waiting is True

    def test_an_empty_request_waits_in_the_default_register(self):
        state = CorrectionState()
        state.start("I have went.", "")

        assert state.mode == WRITTEN

    def test_the_answer_s_own_register_is_adopted(self):
        # Invisible until one press later: the toggle compares against what the panel THINKS it
        # is in, so a stale value makes the lit button do nothing and the unlit one re-ask for
        # what is already on screen.
        state = CorrectionState()
        ticket = state.start("I have went.", "")  # omnia decides

        state.accept(ticket, to_correction({**_ANSWER, "mode": "spoken"}))

        assert state.mode == SPOKEN
        assert state.wants(WRITTEN) is True
        assert state.wants(SPOKEN) is False

    def test_an_answer_for_an_abandoned_request_is_dropped(self):
        # The register toggle re-asks with the SAME phrase, so the phrase cannot tell them
        # apart. Without the ticket the slow first answer lands twenty seconds late, reverts the
        # panel, and flips the toggle back under the reader.
        state = CorrectionState()
        slow = state.start("I have went.", "")
        state.start("I have went.", SPOKEN)
        state.accept(
            state.ticket, to_correction({**_ANSWER, "rewritten": "THE NEW ONE"})
        )

        landed = state.accept(
            slow, to_correction({**_ANSWER, "rewritten": "THE OLD ONE"})
        )

        assert landed is False
        assert state.correction is not None
        assert state.correction.rewritten == "THE NEW ONE"

    def test_a_late_failure_cannot_wipe_the_answer_on_screen_either(self):
        state = CorrectionState()
        slow = state.start("I have went.", "")
        current = state.start("I have went.", SPOKEN)
        state.accept(current, to_correction(_ANSWER))

        landed = state.fail(slow, "Anki did not answer in time.")

        assert landed is False
        assert state.error == ""
        assert state.correction is not None

    def test_pressing_the_register_already_showing_does_nothing(self):
        state = CorrectionState()
        ticket = state.start("x", "")
        state.accept(ticket, to_correction({**_ANSWER, "mode": "written"}))

        assert state.wants(WRITTEN) is False

    def test_an_explanation_opens_and_closes_on_its_own(self):
        state = CorrectionState()
        state.toggle_explanation(1)

        assert state.open_explanations == {1}

        state.toggle_explanation(1)

        assert state.open_explanations == set()

    def test_a_new_answer_closes_every_explanation(self):
        # They are indexes into the PREVIOUS list of fixes; left open across an answer they
        # would expand whichever fix happens to sit at that position now.
        state = CorrectionState()
        ticket = state.start("x", "")
        state.accept(ticket, to_correction(_ANSWER))
        state.toggle_explanation(0)

        second = state.start("x", SPOKEN)
        state.accept(second, to_correction(_ANSWER))

        assert state.open_explanations == set()


class TestTheMarkedRewrite:
    def test_only_the_changed_runs_are_marked(self):
        rich = rich_rewrite(to_correction(_ANSWER))

        assert "<b" in rich
        assert rich.count("<b") == 2, "it marked words nobody changed"

    def test_the_marked_text_still_reads_as_the_rewrite(self):
        import re

        correction = to_correction(_ANSWER)

        plain = re.sub(r"<[^>]+>", "", rich_rewrite(correction))

        assert plain == correction.rewritten

    def test_a_phrase_carrying_markup_cannot_reach_the_panel_as_markup(self):
        # The phrase is whatever the user selected in some other application, and the rewrite is
        # a model's prose. Both are interpolated into rich text.
        correction = to_correction(
            {"rewritten": "<b>x</b>", "highlight": [["<img src=x>", True]]}
        )

        rich = rich_rewrite(correction)

        assert "<img" not in rich
        assert "&lt;img" in rich

    def test_nothing_at_all_renders_as_nothing(self):
        assert rich_rewrite(None) == ""

    def test_a_register_nobody_recognises_still_has_a_label(self):
        assert mode_label("shouted") == "Writing"
        assert mode_label(SPOKEN) == "Speaking"


class TestAgainstARealServer:
    """One end-to-end pass over real HTTP, because the transport is not exercised above."""

    @staticmethod
    def _serving(respond):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                # Read the body BEFORE responding. Not politeness: on Windows, answering while
                # the request body is still unread wedges the connection and the client hangs
                # until its timeout — which a green macOS/Linux run does not catch.
                self.body = self.rfile.read(length)
                respond(self)

            def log_message(self, *_args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def test_a_real_post_carries_the_body_and_no_credential(self):
        seen = {}

        def respond(request):
            seen["body"] = json.loads(request.body.decode("utf-8"))
            seen["token"] = request.headers.get("X-Omnia-Token")
            payload = json.dumps(_ANSWER).encode("utf-8")
            request.send_response(200)
            request.send_header("Content-Type", "application/json")
            request.send_header("Content-Length", str(len(payload)))
            request.end_headers()
            request.wfile.write(payload)

        server = self._serving(respond)
        try:
            correction = CheckClient(f"http://127.0.0.1:{server.server_port}").check(
                "I have went to the shop for buy milk.", SPOKEN
            )
        finally:
            server.shutdown()

        assert seen["token"] is None, "a credential reached the wire"
        assert seen["body"]["mode"] == "spoken"
        assert correction.rewritten == "I went to the shop to buy milk."
        assert len(correction.fixes) == 2

    def test_a_503_reaches_the_user_as_the_switch_to_flip(self):
        def respond(request):
            payload = json.dumps(
                {"error": "Phrase Check is switched off in Omnia — turn it on"}
            ).encode("utf-8")
            request.send_response(503)
            request.send_header("Content-Type", "application/json")
            request.send_header("Content-Length", str(len(payload)))
            request.end_headers()
            request.wfile.write(payload)

        server = self._serving(respond)
        try:
            with pytest.raises(CheckError, match="switched off"):
                CheckClient(f"http://127.0.0.1:{server.server_port}").check("x")
        finally:
            server.shutdown()

    def test_a_dead_service_says_anki_is_not_running(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead = probe.getsockname()[1]

        with pytest.raises(CheckError, match="Is Anki running"):
            CheckClient(f"http://127.0.0.1:{dead}").check("x")
