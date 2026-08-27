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


class TestFetchingMediaFromOmnia:
    """Images used to be fetched through AnkiConnect, a SEPARATE add-on.

    That is the whole bug: on a machine without it, every image in the lookup panel read
    "Image unavailable" while the panel around it worked perfectly -- because the panel comes
    from omnia's own service on 8766 and the image came from something on 8765 that was not
    installed. Measured on the reporting machine: 8766 listening, 8765 answered by nobody.
    Anything the lookup can answer, the media can.
    """

    @staticmethod
    def _serving(handler):
        """A real loopback HTTP server, because this path is raw bytes, not the JSON transport."""
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # stdlib spells it this way
                handler(self)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def test_the_bytes_come_back_verbatim(self) -> None:
        payload = bytes.fromhex("89504e470d0a1a0a") + b"pretend png"

        def respond(request):
            request.send_response(200)
            request.send_header("Content-Type", "image/png")
            request.send_header("Content-Length", str(len(payload)))
            request.end_headers()
            request.wfile.write(payload)

        server = self._serving(respond)
        try:
            client = LookupClient(f"http://127.0.0.1:{server.server_port}")
            assert client.media("picture.png") == payload
        finally:
            server.shutdown()

    def test_the_filename_is_escaped_not_pasted(self) -> None:
        """Anki filenames carry spaces, '&' and non-ASCII; raw they would corrupt the query."""
        seen = {}

        def respond(request):
            seen["path"] = request.path
            request.send_response(200)
            request.send_header("Content-Length", "1")
            request.end_headers()
            request.wfile.write(b"x")

        server = self._serving(respond)
        try:
            client = LookupClient(f"http://127.0.0.1:{server.server_port}")
            client.media("a b&c=d é.png")
        finally:
            server.shutdown()

        assert " " not in seen["path"]
        assert seen["path"].count("&") == 0, f"a bare '&' split the query: {seen['path']}"
        assert seen["path"].startswith("/media?file=")

    def test_a_404_is_none_not_an_exception(self) -> None:
        """A missing file must fall through to the fallback, not kill the worker thread."""

        def respond(request):
            request.send_response(404)
            request.send_header("Content-Length", "0")
            request.end_headers()

        server = self._serving(respond)
        try:
            client = LookupClient(f"http://127.0.0.1:{server.server_port}")
            assert client.media("absent.png") is None
        finally:
            server.shutdown()

    def test_a_dead_service_is_none_not_an_exception(self) -> None:
        """Anki closed. The panel shows a reason; it does not crash the clipper."""
        import socket

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead = probe.getsockname()[1]

        assert LookupClient(f"http://127.0.0.1:{dead}").media("picture.png") is None

    def test_no_filename_asks_nothing(self) -> None:
        """A note with an empty image field must not produce a request at all."""
        asked = {"n": 0}

        def respond(request):
            asked["n"] += 1
            request.send_response(200)
            request.send_header("Content-Length", "0")
            request.end_headers()

        server = self._serving(respond)
        try:
            client = LookupClient(f"http://127.0.0.1:{server.server_port}")
            assert client.media("") is None
        finally:
            server.shutdown()

        assert asked["n"] == 0
