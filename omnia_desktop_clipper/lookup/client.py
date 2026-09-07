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

# Who is asking. omnia answers a known client with more than it answers an anonymous one — the
# per-field generation state, and whether regenerating is allowed at all — because only a
# client that can ACT on those has any use for them.
CLIENT_NAME = "desktop_clipper"

# What a transport returns: the parsed JSON body.
Transport = Callable[[str], "dict[str, Any]"]


class LookupUnavailableError(Exception):
    """The lookup service could not be reached or answered an error (message is user-facing)."""


@dataclass(frozen=True)
class LookupFieldView:
    """One field of a matched note, already cleaned and classified by omnia.

    ``empty`` and ``state`` are what makes a field actionable rather than merely readable:
    a never-filled field is the one the user most wants to regenerate, and ``state`` says
    whether omnia *could* — ``ready``, or one of ``no_rule`` / ``rule_off`` /
    ``not_generatable`` / ``blocked`` / ``unavailable``. Both default to the permissive
    reading so an older omnia (which sends neither) still offers the button and lets the
    service itself give the reason.
    """

    name: str
    text: str
    kind: str = "text"
    audio: tuple[str, ...] = ()
    images: tuple[str, ...] = ()
    empty: bool = False
    state: str = "ready"

    @property
    def is_empty(self) -> bool:
        """Whether there is nothing in this field (omnia says so, or nothing arrived)."""
        return self.empty or not (self.text or self.audio or self.images)


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
    # Whether omnia will accept a regeneration request at all (Smart Notes' "Regenerate from
    # clippers" option). Defaults to False: an omnia too old to answer the question has no
    # /generate route either, so offering an enabled button would only produce a failure.
    can_regenerate: bool = False
    # WHY it will not, when it will not: ``"off"`` (the checkbox), ``"unavailable"`` (Smart
    # Notes itself is disabled, so that checkbox is not on screen to be ticked), or ``""``.
    # The two need different sentences — one of them names a control the user cannot reach —
    # and an omnia too old to say which leaves this empty.
    regenerate_reason: str = ""

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

    def media(self, filename: str) -> bytes | None:
        """Return a collection-media file's bytes from omnia, or ``None``.

        Omnia's own lookup service serves this. It used to come from AnkiConnect, which is a
        SEPARATE add-on: on a machine without it every image in this panel read "Image
        unavailable" while the panel itself worked, because the panel is this service and the
        image was not. Anything the lookup can answer, the media can.

        Raw bytes, so this does not go through the JSON transport. Never raises -- a failure
        means the caller falls back, and then shows a badge.
        """
        import urllib.error
        import urllib.parse
        import urllib.request

        if not filename:
            return None
        url = f"{self._base_url}/media?file={urllib.parse.quote(filename)}"
        try:
            with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as response:
                if response.status != 200:
                    return None
                return bytes(response.read())
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def lookup(self, word: str) -> LookupView:
        """Look ``word`` up; raise :class:`LookupUnavailableError` if the service can't answer.

        A word that is simply not in the collection is NOT an error — it returns a
        :class:`LookupView` with no cards, which the UI shows as its "not found" state.
        """
        word = word.strip()
        if not word:
            return LookupView(word="")
        url = f"{self._base_url}/lookup?{urlencode({'word': word, 'client': CLIENT_NAME})}"
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
                            empty=bool(f.get("empty")),
                            state=str(f.get("state") or "ready"),
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
            can_regenerate=bool(payload.get("can_regenerate")),
            regenerate_reason=str(payload.get("regenerate_reason") or ""),
        )
