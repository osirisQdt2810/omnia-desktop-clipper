"""Tests for the pure ``anki`` module using a mock transport.

The fake transport records every request and returns canned results keyed by
action, so we can assert the exact createDeck/addNote payloads without a network.
"""

from __future__ import annotations

from typing import Any

import pytest

from omnia_desktop_clipper.anki import AnkiConnectClient, AnkiConnectProtocolError


class _Error:
    """Marker: make the fake transport return an AnkiConnect ``error`` response."""

    def __init__(self, message: str) -> None:
        self.message = message


class _RecordingTransport:
    """Records requests and returns canned results keyed by AnkiConnect action."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((url, request))
        action = request["action"]
        if action not in self._responses:
            raise AssertionError(f"unexpected action: {action}")
        result = self._responses[action]
        if isinstance(result, _Error):
            return {"result": None, "error": result.message}
        return {"result": result, "error": None}

    def actions(self) -> list[str]:
        return [request["action"] for _url, request in self.calls]

    def request_for(self, action: str) -> dict[str, Any]:
        for _url, request in self.calls:
            if request["action"] == action:
                return request
        raise AssertionError(f"no call recorded for action: {action}")


class TestAddNote:
    """The add flow issues createDeck then addNote with the correct payload."""

    def _client(
        self, responses: dict[str, Any]
    ) -> tuple[AnkiConnectClient, _RecordingTransport]:
        transport = _RecordingTransport(responses)
        return (
            AnkiConnectClient("http://127.0.0.1:8765", transport=transport),
            transport,
        )

    def test_createdeck_then_addnote_payload(self) -> None:
        client, transport = self._client(
            {
                "modelFieldNames": ["Front", "Back"],
                "createDeck": 1,
                "addNote": 12345,
            }
        )
        note_id = client.add_note(
            "ephemeral",
            "It was an ephemeral moment.",
            deck="Omnia Capture",
            model="Basic",
            field_map={"word": "Front", "context": "Back"},
            tags=["omnia-web-clipper", "omnia-autogen"],
        )

        assert note_id == 12345
        assert transport.actions() == ["modelFieldNames", "createDeck", "addNote"]

        create = transport.request_for("createDeck")
        assert create["version"] == 6
        assert create["params"] == {"deck": "Omnia Capture"}

        note = transport.request_for("addNote")["params"]["note"]
        assert note["deckName"] == "Omnia Capture"
        assert note["modelName"] == "Basic"
        assert note["fields"] == {
            "Front": "ephemeral",
            "Back": "It was an ephemeral moment.",
        }
        assert note["options"] == {"allowDuplicate": False}

    def test_tags_include_source_and_autogen(self) -> None:
        client, transport = self._client(
            {"modelFieldNames": ["Front"], "createDeck": 1, "addNote": 1}
        )
        client.add_note(
            "word",
            "context",
            deck="D",
            model="Basic",
            field_map={"word": "Front"},
            tags=["omnia-web-clipper", "omnia-autogen"],
        )
        note = transport.request_for("addNote")["params"]["note"]
        assert note["tags"] == ["omnia-web-clipper", "omnia-autogen"]

    def test_first_field_autofilled_when_mapping_leaves_it_empty(self) -> None:
        # "Note ID" is the first field but is never mapped; it must be auto-filled
        # with the word so Anki does not reject the note as empty.
        client, transport = self._client(
            {
                "modelFieldNames": ["Note ID", "Word", "Context"],
                "createDeck": 1,
                "addNote": 7,
            }
        )
        client.add_note(
            "serendipity",
            "a serendipity of events",
            deck="D",
            model="AnkiVocabulary",
            field_map={"word": "Word", "context": "Context"},
            tags=["omnia-web-clipper"],
        )
        fields = transport.request_for("addNote")["params"]["note"]["fields"]
        assert fields["Note ID"] == "serendipity"
        assert fields["Word"] == "serendipity"
        assert fields["Context"] == "a serendipity of events"

    def test_api_key_included_when_set(self) -> None:
        transport = _RecordingTransport(
            {"modelFieldNames": ["Front"], "createDeck": 1, "addNote": 1}
        )
        client = AnkiConnectClient("http://x", api_key="KEY", transport=transport)
        client.add_note(
            "w", "c", deck="D", model="M", field_map={"word": "Front"}, tags=["t"]
        )
        assert transport.request_for("addNote")["key"] == "KEY"

    def test_api_key_omitted_when_empty(self) -> None:
        client, transport = self._client(
            {"modelFieldNames": ["Front"], "createDeck": 1, "addNote": 1}
        )
        client.add_note(
            "w", "c", deck="D", model="M", field_map={"word": "Front"}, tags=["t"]
        )
        assert "key" not in transport.request_for("addNote")

    def test_protocol_error_raised_on_ankiconnect_error(self) -> None:
        client, _transport = self._client(
            {
                "modelFieldNames": ["Front"],
                "createDeck": 1,
                "addNote": _Error("cannot create note because it is a duplicate"),
            }
        )
        with pytest.raises(AnkiConnectProtocolError, match="duplicate"):
            client.add_note(
                "w", "c", deck="D", model="M", field_map={"word": "Front"}, tags=["t"]
            )
