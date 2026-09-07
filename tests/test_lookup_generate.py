"""Tests for regenerating a note's fields through omnia (injected transport — no HTTP, no Anki).

The interesting behaviour here is not "it posts JSON". It is that **partial success is the
normal outcome**: a note's fields do not all generate, and the reason each one gives is the
feature — a field that came back ``blocked`` with "needs Definition" has told the user exactly
what to do next. So most of these tests are about a message surviving intact from the service
to the row it belongs on.
"""

from __future__ import annotations

import json

import pytest

from omnia_desktop_clipper.lookup.client import LookupCardView, LookupFieldView
from omnia_desktop_clipper.lookup.generate import (
    FieldGeneration,
    GenerateClient,
    GenerateError,
    GenerateOutcome,
    describe_status,
    error_message,
)

_ANSWER = {
    "note_id": 123,
    "results": [
        {
            "field": "Definition",
            "status": "generated",
            "message": "",
            "text": "to move down quickly",
            "audio": [],
            "images": [],
        },
        {"field": "Audio", "status": "blocked", "message": "needs Definition"},
    ],
}


def _client(answer=None, seen=None, token="tok"):
    """A client whose transport records the call and replies with ``answer``."""

    def transport(url, body, headers):
        if seen is not None:
            seen.append({"url": url, "body": body, "headers": headers})
        return {} if answer is None else answer

    return GenerateClient(
        "http://127.0.0.1:8766", token_provider=lambda: token, transport=transport
    )


class TestTheRequest:
    def test_it_posts_the_documented_body(self) -> None:
        seen: list[dict] = []
        _client(_ANSWER, seen).generate(123, ["Definition"])

        assert seen[0]["url"] == "http://127.0.0.1:8766/generate"
        assert seen[0]["body"] == {
            "client": "desktop_clipper",
            "note_id": 123,
            "fields": ["Definition"],
        }

    def test_no_fields_means_the_whole_note(self) -> None:
        """Explicitly null, not omitted: "every field" is a decision, not a forgotten key."""
        seen: list[dict] = []
        _client(_ANSWER, seen).generate(123)

        assert seen[0]["body"]["fields"] is None

    def test_the_token_travels_in_its_own_header(self) -> None:
        seen: list[dict] = []
        _client(_ANSWER, seen, token="s3cret").generate(123)

        assert seen[0]["headers"]["X-Omnia-Token"] == "s3cret"
        assert seen[0]["headers"]["Content-Type"] == "application/json"

    def test_the_token_is_read_per_request(self) -> None:
        """The add-on is often installed AFTER the clipper started; a token cached at startup
        would then be empty for the rest of the session and every generate would 401."""
        tokens = iter(["", "written-later"])
        seen: list[dict] = []

        client = GenerateClient(
            "http://h:1",
            token_provider=lambda: next(tokens),
            transport=lambda url, body, headers: seen.append(headers) or _ANSWER,
        )
        with pytest.raises(GenerateError):
            client.generate(123)  # nothing written yet

        client.generate(123)

        assert seen[0]["X-Omnia-Token"] == "written-later"

    def test_without_a_token_it_does_not_even_ask(self) -> None:
        """A pointless 401 teaches the user nothing; naming the missing file does."""
        asked: list[str] = []
        client = GenerateClient(
            "http://h:1",
            token_provider=lambda: "",
            transport=lambda url, body, headers: asked.append(url) or _ANSWER,
        )

        with pytest.raises(GenerateError, match="token"):
            client.generate(123)
        assert asked == []

    def test_the_base_url_trailing_slash_is_normalised(self) -> None:
        seen: list[dict] = []
        GenerateClient(
            "http://h:1/",
            token_provider=lambda: "t",
            transport=lambda url, body, headers: seen.append({"url": url}) or _ANSWER,
        ).generate(1)

        assert seen[0]["url"] == "http://h:1/generate"


class TestTheAnswer:
    def test_it_reads_both_a_result_and_a_reason(self) -> None:
        outcome = _client(_ANSWER).generate(123)

        assert outcome.note_id == 123
        assert outcome.fields == ("Definition", "Audio")
        assert outcome.results[0].generated is True
        assert outcome.results[0].text == "to move down quickly"
        assert outcome.results[1].generated is False

    def test_every_non_generated_status_carries_a_usable_message(self) -> None:
        """omnia's own message wins — it is the specific one ("needs Definition")."""
        statuses = [
            "skipped",
            "blocked",
            "error",
            "no_rule",
            "rule_off",
            "not_generatable",
        ]
        answer = {
            "note_id": 7,
            "results": [
                {"field": name, "status": name, "message": f"because {name}"}
                for name in statuses
            ],
        }

        messages = _client(answer).generate(7).messages()

        assert messages == {name: f"because {name}" for name in statuses}

    def test_a_status_with_no_message_still_says_something(self) -> None:
        """A bare status name on screen is a bug report waiting to happen."""
        answer = {
            "results": [
                {"field": name, "status": name}
                for name in (
                    "skipped",
                    "blocked",
                    "error",
                    "no_rule",
                    "rule_off",
                    "not_generatable",
                )
            ]
        }

        messages = _client(answer).generate(7).messages()

        assert set(messages) == {
            "skipped",
            "blocked",
            "error",
            "no_rule",
            "rule_off",
            "not_generatable",
        }
        for field, message in messages.items():
            assert message and message != field
            assert message[0].isupper() and message.endswith(".")

    def test_a_generated_field_contributes_no_message(self) -> None:
        """Its new content IS the message; a line saying "Generated." would be noise."""
        assert _client(_ANSWER).generate(123).messages() == {
            "Audio": "needs Definition"
        }

    def test_an_unknown_status_is_shown_rather_than_swallowed(self) -> None:
        answer = {"results": [{"field": "F", "status": "quantum"}]}

        assert _client(answer).generate(1).messages() == {"F": "quantum"}

    def test_missing_keys_are_tolerated(self) -> None:
        outcome = _client({"results": [{"field": "F"}]}).generate(9)

        assert (
            outcome.note_id == 9
        )  # absent note_id falls back to the one we asked about
        assert outcome.results[0].status == "error"
        assert outcome.results[0].audio == ()

    def test_a_result_naming_no_field_is_dropped(self) -> None:
        """There is nowhere to show it: every message belongs on a row."""
        outcome = _client(
            {"results": [{"status": "error"}, {"field": "F", "status": "skipped"}]}
        ).generate(1)

        assert outcome.fields == ("F",)

    def test_a_non_dict_payload_is_an_error(self) -> None:
        client = GenerateClient(
            "http://h:1", token_provider=lambda: "t", transport=lambda *a: ["nope"]
        )
        with pytest.raises(GenerateError):
            client.generate(1)


class TestApplyingTheAnswerToTheCard:
    """A ``/generate`` result updates ONLY the fields it names."""

    @staticmethod
    def _card() -> LookupCardView:
        return LookupCardView(
            note_id=123,
            title="plunge",
            fields=(
                LookupFieldView(name="Word", text="plunge"),
                LookupFieldView(name="Definition", text="", empty=True, state="ready"),
                LookupFieldView(name="Audio", text="", empty=True, state="blocked"),
            ),
        )

    def test_only_the_named_generated_field_changes(self) -> None:
        card = self._card()

        updated = _client(_ANSWER).generate(123).applied_to(card)

        assert updated.fields[1].text == "to move down quickly"
        assert updated.fields[0] == card.fields[0]  # untouched, same object
        assert updated.fields[2] == card.fields[2]  # blocked: keeps what it had

    def test_a_field_that_did_not_generate_keeps_its_content(self) -> None:
        """A "blocked" result carries no text; writing it would ERASE the field."""
        card = self._card()
        answer = {"results": [{"field": "Word", "status": "blocked", "text": ""}]}

        updated = _client(answer).generate(123).applied_to(card)

        assert updated.fields[0].text == "plunge"

    def test_a_generated_field_stops_reading_as_empty(self) -> None:
        card = self._card()

        updated = _client(_ANSWER).generate(123).applied_to(card)

        assert updated.fields[1].is_empty is False
        assert updated.fields[1].state == "ready"

    def test_generated_media_replaces_the_old_media(self) -> None:
        card = self._card()
        answer = {
            "results": [
                {"field": "Audio", "status": "generated", "audio": ["plunge.mp3"]}
            ]
        }

        updated = _client(answer).generate(123).applied_to(card)

        assert updated.fields[2].audio == ("plunge.mp3",)
        assert updated.fields[2].is_empty is False

    def test_a_field_the_card_does_not_have_is_ignored(self) -> None:
        """The panel renders omnia's field list; a row conjured from an answer has no place."""
        card = self._card()
        answer = {"results": [{"field": "Ghost", "status": "generated", "text": "boo"}]}

        updated = _client(answer).generate(123).applied_to(card)

        assert [f.name for f in updated.fields] == ["Word", "Definition", "Audio"]

    def test_an_answer_with_nothing_generated_leaves_the_card_alone(self) -> None:
        card = self._card()
        answer = {"results": [{"field": "Definition", "status": "no_rule"}]}

        assert _client(answer).generate(123).applied_to(card) is card

    def test_the_original_card_is_not_mutated(self) -> None:
        """The panel keeps the whole result in hand; a switcher hop must not see half an edit."""
        card = self._card()

        _client(_ANSWER).generate(123).applied_to(card)

        assert card.fields[1].text == ""


class TestHttpFailures:
    """Every refusal has to name the switch that fixes it."""

    def test_the_services_own_error_wins(self) -> None:
        assert error_message(409, "Regeneration is off for this profile") == (
            "Regeneration is off for this profile"
        )

    def test_409_explains_which_option_to_switch_on(self) -> None:
        assert "Regenerate from clippers" in error_message(409)

    def test_401_points_at_the_token(self) -> None:
        assert "token" in error_message(401).lower()

    def test_503_points_at_smart_notes(self) -> None:
        assert "Smart Notes" in error_message(503)

    def test_an_unknown_code_still_says_what_happened(self) -> None:
        assert "500" in error_message(500)

    def test_a_blank_service_error_falls_back(self) -> None:
        assert error_message(409, "   ") == error_message(409)


class TestAgainstARealServer:
    """One end-to-end pass over real HTTP: headers and body are a contract with the add-on."""

    @staticmethod
    def _serving(handler):
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # stdlib spells it this way
                handler(self)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def test_a_real_post_carries_the_token_and_the_body(self) -> None:
        seen = {}

        def respond(request):
            length = int(request.headers.get("Content-Length", 0))
            seen["body"] = json.loads(request.rfile.read(length).decode("utf-8"))
            seen["token"] = request.headers.get("X-Omnia-Token")
            seen["type"] = request.headers.get("Content-Type")
            payload = json.dumps(_ANSWER).encode("utf-8")
            request.send_response(200)
            request.send_header("Content-Type", "application/json")
            request.send_header("Content-Length", str(len(payload)))
            request.end_headers()
            request.wfile.write(payload)

        server = self._serving(respond)
        try:
            client = GenerateClient(
                f"http://127.0.0.1:{server.server_port}", token_provider=lambda: "abc"
            )
            outcome = client.generate(123, ["Definition"])
        finally:
            server.shutdown()

        assert seen["token"] == "abc"
        assert seen["type"] == "application/json"
        assert seen["body"] == {
            "client": "desktop_clipper",
            "note_id": 123,
            "fields": ["Definition"],
        }
        assert outcome.results[0].text == "to move down quickly"

    def test_a_409_reaches_the_user_as_the_switch_to_flip(self) -> None:
        """The option being off is the single likeliest failure, so it must not read as a 409."""

        def respond(request):
            payload = json.dumps({"error": ""}).encode("utf-8")
            request.send_response(409)
            request.send_header("Content-Type", "application/json")
            request.send_header("Content-Length", str(len(payload)))
            request.end_headers()
            request.wfile.write(payload)

        server = self._serving(respond)
        try:
            client = GenerateClient(
                f"http://127.0.0.1:{server.server_port}", token_provider=lambda: "abc"
            )
            with pytest.raises(GenerateError, match="Regenerate from clippers"):
                client.generate(123)
        finally:
            server.shutdown()

    def test_a_dead_service_says_anki_is_not_running(self) -> None:
        import socket

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead = probe.getsockname()[1]

        client = GenerateClient(
            f"http://127.0.0.1:{dead}", token_provider=lambda: "abc"
        )
        with pytest.raises(GenerateError, match="Is Anki running"):
            client.generate(123)


class TestValueObjects:
    def test_describe_status_covers_the_field_states_too(self) -> None:
        """The panel reuses it for a field's state, so both vocabularies must be in the map."""
        for state in (
            "ready",
            "no_rule",
            "rule_off",
            "not_generatable",
            "blocked",
            "unavailable",
        ):
            assert describe_status(state) != state

    def test_display_message_prefers_omnias_words(self) -> None:
        result = FieldGeneration(
            field="F", status="blocked", message="needs Definition"
        )

        assert result.display_message == "needs Definition"

    def test_an_empty_outcome_has_nothing_to_say(self) -> None:
        outcome = GenerateOutcome(note_id=1)

        assert outcome.fields == () and outcome.messages() == {}
