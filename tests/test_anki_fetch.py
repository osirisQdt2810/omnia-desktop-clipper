"""Tests for the AnkiConnect list-fetch methods that populate the settings pickers."""

from __future__ import annotations

from omnia_desktop_clipper.anki import AnkiConnectClient


def _client(responses: dict[str, object]):
    calls: list[dict] = []

    def transport(url: str, request: dict) -> dict:
        calls.append(request)
        return {"result": responses.get(request["action"]), "error": None}

    return AnkiConnectClient("http://x", transport=transport), calls


class TestListFetch:
    def test_deck_names(self) -> None:
        client, calls = _client({"deckNames": ["A", "B", "Default"]})
        assert client.deck_names() == ["A", "B", "Default"]
        assert calls[0]["action"] == "deckNames"

    def test_model_names(self) -> None:
        client, _ = _client({"modelNames": ["Basic", "Cloze"]})
        assert client.model_names() == ["Basic", "Cloze"]

    def test_model_field_names(self) -> None:
        client, _ = _client({"modelFieldNames": ["Front", "Back"]})
        assert client.model_field_names("Basic") == ["Front", "Back"]

    def test_model_field_names_is_cached(self) -> None:
        client, calls = _client({"modelFieldNames": ["Front", "Back"]})
        client.model_field_names("Basic")
        client.model_field_names("Basic")
        queries = [c for c in calls if c["action"] == "modelFieldNames"]
        assert len(queries) == 1  # second call served from cache

    def test_non_list_result_is_empty(self) -> None:
        client, _ = _client({"deckNames": None})
        assert client.deck_names() == []
