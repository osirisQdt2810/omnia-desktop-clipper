"""AnkiConnect HTTP client.

Pure module: no PyQt6/pynput imports, so it unit-tests headless with an injected
transport. It reproduces the *exact* AnkiConnect v6 contract the Omnia web
clipper uses:

* Request body ``{"action": str, "version": 6, "params": {...}, "key"?: str}``.
* Response ``{"result": ..., "error": ...}``; a non-null ``error`` raises.
* Per add: ``createDeck`` (idempotent) then ``addNote``.
* If the note type's FIRST field is still empty after the field-map is applied,
  auto-fill it with the word (mirrors the web clipper's ``firstFieldName`` guard)
  so Anki never rejects the note with "cannot create note because it is empty".
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from typing import Any

ANKICONNECT_VERSION = 6
_DEFAULT_TIMEOUT_SECONDS = 10.0

# A transport takes (url, request_dict) and returns the parsed response dict.
# Injecting it keeps the client testable with a mock and free of a hard HTTP dep.
Transport = Callable[[str, "dict[str, Any]"], "dict[str, Any]"]


class AnkiConnectError(Exception):
    """Base error for any AnkiConnect interaction failure."""


class AnkiConnectProtocolError(AnkiConnectError):
    """AnkiConnect returned a non-null ``error`` for a request."""


class AnkiConnectTransportError(AnkiConnectError):
    """AnkiConnect could not be reached (connection / HTTP / decode failure)."""


def _urllib_transport(url: str, request: dict[str, Any]) -> dict[str, Any]:
    """Default transport: POST ``request`` as JSON via stdlib ``urllib``.

    Args:
        url: The AnkiConnect endpoint URL.
        request: The request body to serialise as JSON.

    Returns:
        The parsed JSON response dict.

    Raises:
        AnkiConnectTransportError: On an unsupported URL scheme, a connection
            failure, or a non-JSON / unexpected response.
    """
    if not url.startswith(("http://", "https://")):
        raise AnkiConnectTransportError(f"Unsupported AnkiConnect URL scheme: {url!r}")
    payload = json.dumps(request).encode("utf-8")
    http_request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            http_request, timeout=_DEFAULT_TIMEOUT_SECONDS
        ) as response:
            body = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise AnkiConnectTransportError(
            f"Could not reach AnkiConnect at {url}. Make sure Anki is running with "
            f"the AnkiConnect add-on. Underlying error: {exc}"
        ) from exc
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AnkiConnectTransportError(
            "AnkiConnect returned a non-JSON response."
        ) from exc
    if not isinstance(decoded, dict):
        raise AnkiConnectTransportError(
            "AnkiConnect returned an unexpected response shape."
        )
    return decoded


def _first_nonempty(values: Iterable[str]) -> str:
    """Return the first stripped non-empty string in ``values``, or ``""``."""
    for value in values:
        stripped = value.strip()
        if stripped:
            return stripped
    return ""


class AnkiConnectClient:
    """Minimal AnkiConnect v6 client for adding Omnia capture notes."""

    def __init__(
        self,
        url: str,
        api_key: str = "",
        transport: Transport | None = None,
    ) -> None:
        """Initialise the client.

        Args:
            url: The AnkiConnect endpoint (default AnkiConnect is
                ``http://127.0.0.1:8765``).
            api_key: The AnkiConnect ``apiKey`` (empty when none is configured).
            transport: The HTTP transport callable. Defaults to a stdlib
                ``urllib`` transport; inject a fake in tests.
        """
        self._url = url
        self._api_key = api_key
        self._transport = transport if transport is not None else _urllib_transport
        # Cache modelFieldNames per model so we don't re-query on every add.
        self._field_cache: dict[str, list[str]] = {}

    def add_note(
        self,
        word: str,
        context: str,
        *,
        deck: str,
        model: str,
        field_map: Mapping[str, str],
        tags: Iterable[str],
    ) -> int:
        """Ensure the deck exists then add a note; return the new note id.

        Args:
            word: The captured word/base value (Omnia's base field).
            context: The captured surrounding context/sentence.
            deck: The target deck name (created if missing).
            model: The note type (model) name.
            field_map: Capture key (``"word"`` / ``"context"``) -> field name.
            tags: The tags to attach (e.g. source tag + ``omnia-autogen``).

        Returns:
            The new note id reported by AnkiConnect.

        Raises:
            AnkiConnectError: If nothing could be captured into the note, or on
                any AnkiConnect protocol/transport failure.
        """
        fields = self._build_fields(word, context, field_map)
        self._ensure_first_field(fields, word, context, model)
        if not fields:
            raise AnkiConnectError(
                "Nothing was captured to add to Anki. Select some text and retry."
            )
        # createDeck is idempotent: returns the deck id whether or not it existed.
        self._invoke("createDeck", {"deck": deck})
        note = {
            "deckName": deck,
            "modelName": model,
            "fields": fields,
            "tags": list(tags),
            "options": {"allowDuplicate": False},
        }
        result = self._invoke("addNote", {"note": note})
        return int(result) if result is not None else 0

    def deck_names(self) -> list[str]:
        """Return all deck names (AnkiConnect ``deckNames``), for the settings picker."""
        return self._string_list("deckNames", {})

    def model_names(self) -> list[str]:
        """Return all note-type (model) names (AnkiConnect ``modelNames``)."""
        return self._string_list("modelNames", {})

    def model_field_names(self, model: str) -> list[str]:
        """Return ``model``'s field names in order (AnkiConnect ``modelFieldNames``).

        Cached per model (shared with the add-note first-field lookup).
        """
        if model not in self._field_cache:
            names = self._invoke("modelFieldNames", {"modelName": model})
            self._field_cache[model] = (
                [name for name in names if isinstance(name, str)]
                if isinstance(names, list)
                else []
            )
        return list(self._field_cache[model])

    def _string_list(self, action: str, params: dict[str, Any]) -> list[str]:
        """Invoke ``action`` and return its result coerced to a list of strings."""
        result = self._invoke(action, params)
        if not isinstance(result, list):
            return []
        return [item for item in result if isinstance(item, str)]

    def _build_fields(
        self, word: str, context: str, field_map: Mapping[str, str]
    ) -> dict[str, str]:
        """Map non-empty capture values onto their configured field names."""
        values = {"word": word, "context": context}
        fields: dict[str, str] = {}
        for capture_key, target_field in field_map.items():
            target = (target_field or "").strip()
            value = values.get(capture_key, "")
            if target and value:
                fields[target] = value
        return fields

    def _ensure_first_field(
        self, fields: dict[str, str], word: str, context: str, model: str
    ) -> None:
        """Auto-fill the note type's first field if the map left it empty."""
        first = self._first_field_name(model)
        if first and not fields.get(first, "").strip():
            base = word.strip() or context.strip() or _first_nonempty(fields.values())
            if base:
                fields[first] = base

    def _first_field_name(self, model: str) -> str:
        """Return the note type's first field name, or ``""`` if unknown.

        Best-effort: an ``AnkiConnectProtocolError`` (e.g. an unknown model) is
        swallowed and surfaced later via ``addNote``'s real error. Transport
        failures are NOT swallowed — they propagate so the caller fails fast.
        """
        if model not in self._field_cache:
            try:
                names = self._invoke("modelFieldNames", {"modelName": model})
            except AnkiConnectProtocolError:
                return ""
            self._field_cache[model] = (
                [name for name in names if isinstance(name, str)]
                if isinstance(names, list)
                else []
            )
        cached = self._field_cache[model]
        return cached[0] if cached else ""

    def _invoke(self, action: str, params: dict[str, Any]) -> Any:
        """POST one AnkiConnect action and return its ``result``.

        Raises:
            AnkiConnectProtocolError: If the response carries a non-null error.
        """
        request: dict[str, Any] = {
            "action": action,
            "version": ANKICONNECT_VERSION,
            "params": params,
        }
        if self._api_key:
            request["key"] = self._api_key
        response = self._transport(self._url, request)
        if response.get("error") is not None:
            raise AnkiConnectProtocolError(str(response["error"]))
        return response.get("result")
