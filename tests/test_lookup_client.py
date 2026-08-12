"""Tests for the lookup client (injected transport — no HTTP, no Anki)."""

from __future__ import annotations

import pytest

from omnia_desktop_clipper.lookup.client import LookupClient, LookupUnavailableError

_PAYLOAD = {
    "word": "plunge",
    "found": True,
    "truncated": False,
    "cards": [
        {
            "note_id": 42,
            "title": "plunge",
            "note_type": "AnkiVocabulary",
            "deck": "Vocab::Unit 6",
            "tags": ["omnia-desktop-clipper"],
            "state": "relearning",
            "interval_days": 1,
            "reps": 11,
            "lapses": 3,
            "fields": [
                {"name": "Definition", "text": "to move down quickly", "kind": "text"},
                {"name": "Word (audio)", "text": "", "kind": "audio", "audio": ["w.mp3"]},
            ],
        }
    ],
}


class TestLookup:
    def test_parses_a_full_payload(self):
        client = LookupClient(transport=lambda url: _PAYLOAD)
        view = client.lookup("plunge")
        assert view.found is True and len(view.cards) == 1
        card = view.cards[0]
        assert card.note_id == 42 and card.title == "plunge"
        assert card.state == "relearning" and card.interval_days == 1 and card.lapses == 3
        assert card.deck == "Vocab::Unit 6" and card.tags == ("omnia-desktop-clipper",)
        assert [f.name for f in card.fields] == ["Definition", "Word (audio)"]
        assert card.fields[1].kind == "audio" and card.fields[1].audio == ("w.mp3",)

    def test_url_carries_the_encoded_word(self):
        seen: list[str] = []

        def transport(url):
            seen.append(url)
            return {"cards": []}

        LookupClient(base_url="http://127.0.0.1:9999", transport=transport).lookup("lao xuống")
        assert seen[0].startswith("http://127.0.0.1:9999/lookup?word=")
        assert "lao+xu%E1%BB%91ng" in seen[0] or "lao%20xu%E1%BB%91ng" in seen[0]

    def test_a_miss_is_not_an_error(self):
        # "not in the collection" is a successful lookup with no cards, NOT an exception.
        view = LookupClient(transport=lambda url: {"word": "x", "cards": []}).lookup("x")
        assert view.found is False and view.cards == []

    def test_blank_word_short_circuits_without_calling_the_service(self):
        called: list[str] = []
        view = LookupClient(transport=lambda url: called.append(url) or {}).lookup("   ")
        assert view.found is False and called == []

    def test_missing_keys_are_tolerated(self):
        view = LookupClient(transport=lambda url: {"cards": [{"title": "t"}]}).lookup("t")
        card = view.cards[0]
        assert card.note_id == 0 and card.state == "new" and card.fields == ()

    def test_non_dict_payload_is_an_error(self):
        with pytest.raises(LookupUnavailableError):
            LookupClient(transport=lambda url: ["nope"]).lookup("x")

    def test_transport_failure_propagates_as_lookup_unavailable(self):
        def boom(url):
            raise LookupUnavailableError("Anki is not running")

        with pytest.raises(LookupUnavailableError, match="not running"):
            LookupClient(transport=boom).lookup("x")

    def test_truncated_flag_is_carried(self):
        view = LookupClient(
            transport=lambda url: {"cards": [{"title": "a"}], "truncated": True}
        ).lookup("a")
        assert view.truncated is True

    def test_base_url_trailing_slash_is_normalised(self):
        seen: list[str] = []
        LookupClient(
            base_url="http://h:1/", transport=lambda url: seen.append(url) or {"cards": []}
        ).lookup("w")
        assert seen[0].startswith("http://h:1/lookup?")
