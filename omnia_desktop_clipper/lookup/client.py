"""Thin client for omnia's word-lookup service (the clipper renders, omnia decides).

The Anki-side plugin owns which note types are searchable, which of a big note type's fields are
worth showing, and how hits rank; this module just asks it and hands back plain dataclasses. That
keeps the clipper a renderer and means improving the triage never needs a clipper rebuild.

Pure of PyQt6 and of any UI import, with an injectable transport, so it unit-tests headless.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

# Short: the lookup is an interactive gesture. A slow/absent service must fail fast enough that
# the panel can say so instead of spinning.
_TIMEOUT_SECONDS = 4.0

# What a transport returns: the parsed JSON body.
Transport = Callable[[str], "dict[str, Any]"]


class LookupUnavailableError(Exception):
    """The lookup service could not be reached or answered an error (message is user-facing)."""


@dataclass(frozen=True)
class LookupFieldView:
    """One field of a matched note, already cleaned and classified by omnia."""

    name: str
    text: str
    kind: str = "text"
    audio: tuple[str, ...] = ()
    images: tuple[str, ...] = ()


@dataclass(frozen=True)
class LookupCardView:
    """A matched note, already display-ready."""

    note_id: int
    title: str
    note_type: str = ""
    deck: str = ""
    tags: tuple[str, ...] = ()
    state: str = "new"
    interval_days: int = 0
    reps: int = 0
    lapses: int = 0
    fields: tuple[LookupFieldView, ...] = ()


@dataclass
class LookupView:
    """The whole answer for one word."""

    word: str
    cards: list[LookupCardView] = field(default_factory=list)
    truncated: bool = False

    @property
    def found(self) -> bool:
        return bool(self.cards)


def _urllib_transport(url: str) -> dict[str, Any]:
    """Default transport: GET ``url`` and parse the JSON body."""
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # The service answers errors as JSON; surface its message when it has one.
        try:
            body = json.loads(exc.read().decode("utf-8"))
            message = str(body.get("error") or exc.reason)
        except Exception:
            message = str(exc.reason)
        raise LookupUnavailableError(message) from exc
    except urllib.error.URLError as exc:
        host = url.split("/lookup", 1)[0]
        raise LookupUnavailableError(
            f"Can't reach Anki's lookup service at {host}.\n"
            "• Is Anki running?\n"
            "• Is Omnia → Word Lookup switched on? (it starts the service)\n"
            "Enabling it in Anki takes effect immediately — just try again."
        ) from exc
    except (TimeoutError, OSError) as exc:
        raise LookupUnavailableError("The lookup timed out.") from exc
    except json.JSONDecodeError as exc:
        raise LookupUnavailableError("The lookup service returned an unreadable response.") from exc


class LookupClient:
    """Asks omnia's loopback service about a word."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8766",
        transport: Transport | None = None,
    ) -> None:
        """Initialise the client.

        Args:
            base_url: Where omnia's lookup service listens.
            transport: The HTTP call (injected in tests). Defaults to a stdlib ``urllib`` GET.
        """
        self._base_url = base_url.rstrip("/")
        self._transport = transport if transport is not None else _urllib_transport

    def lookup(self, word: str) -> LookupView:
        """Look ``word`` up; raise :class:`LookupUnavailableError` if the service can't answer.

        A word that is simply not in the collection is NOT an error — it returns a
        :class:`LookupView` with no cards, which the UI shows as its "not found" state.
        """
        word = word.strip()
        if not word:
            return LookupView(word="")
        url = f"{self._base_url}/lookup?{urlencode({'word': word})}"
        payload = self._transport(url)
        if not isinstance(payload, dict):
            raise LookupUnavailableError("The lookup service returned an unexpected response.")
        return self._to_view(word, payload)

    @staticmethod
    def _to_view(word: str, payload: dict[str, Any]) -> LookupView:
        """Convert the service payload into dataclasses, tolerating missing keys."""
        cards = []
        for raw in payload.get("cards") or []:
            if not isinstance(raw, dict):
                continue
            cards.append(
                LookupCardView(
                    note_id=int(raw.get("note_id") or 0),
                    title=str(raw.get("title") or ""),
                    note_type=str(raw.get("note_type") or ""),
                    deck=str(raw.get("deck") or ""),
                    tags=tuple(str(t) for t in (raw.get("tags") or [])),
                    state=str(raw.get("state") or "new"),
                    interval_days=int(raw.get("interval_days") or 0),
                    reps=int(raw.get("reps") or 0),
                    lapses=int(raw.get("lapses") or 0),
                    fields=tuple(
                        LookupFieldView(
                            name=str(f.get("name") or ""),
                            text=str(f.get("text") or ""),
                            kind=str(f.get("kind") or "text"),
                            audio=tuple(str(a) for a in (f.get("audio") or [])),
                            images=tuple(str(i) for i in (f.get("images") or [])),
                        )
                        for f in (raw.get("fields") or [])
                        if isinstance(f, dict)
                    ),
                )
            )
        return LookupView(
            word=str(payload.get("word") or word),
            cards=cards,
            truncated=bool(payload.get("truncated")),
        )
