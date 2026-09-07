"""Ask omnia to (re)generate a note's fields with Smart Notes.

The lookup panel shows what Anki already has for a word. Half the time what it shows is a
field that is empty, or wrong, or was written before the note type grew a new field — and the
answer to that is not "open Anki and find the note", it is "generate it, here". This module is
the client half of that: one authenticated ``POST {lookup_url}/generate``.

Three things separate it from :mod:`omnia_desktop_clipper.lookup.client`:

* **It mutates.** The request rewrites notes and spends the user's LLM/TTS credits, so it is
  authenticated with the shared secret omnia writes into its add-on data (see
  :mod:`omnia_desktop_clipper.lookup.token`).
* **It is slow.** Generation calls a provider; tens of seconds is normal. The lookup's 4 s
  deadline would time out every real request, so this has its own generous one — and its
  caller must be off the Qt main thread (see :class:`~omnia_desktop_clipper.lookup.service.LookupService`).
* **Partial success is the normal outcome.** A note's fields do not all generate: one has no
  rule, another is waiting on a field that has not been generated yet. Every field comes back
  with its own status and its own message, and showing that message is the point of the
  feature — not an error path.

Pure of PyQt6 and of any UI import, with an injectable transport, so it unit-tests headless.
"""

from __future__ import annotations

import dataclasses
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from .client import CLIENT_NAME, LookupCardView, LookupFieldView
from .token import resolve_token

# Generous on purpose: an LLM field plus a TTS clip regularly takes half a minute, and a whole
# note asks for several of them in one request. The cost of being too patient is a spinner; the
# cost of being too eager is a request that ALWAYS fails while omnia happily finishes the work.
_TIMEOUT_SECONDS = 300.0

_GENERATE_PATH = "/generate"

# What a transport returns: the parsed JSON body. Takes (url, body, headers).
Transport = Callable[[str, "dict[str, Any]", "dict[str, str]"], "dict[str, Any]"]

# A field regenerated successfully. Everything else is a reason, not a result.
STATUS_GENERATED = "generated"

# Plain-English renderings of the status/state names omnia sends, used ONLY when it sends no
# message of its own. Its message is the good one ("needs Definition"); these keep a bare
# status from reaching the user as a bare status.
_STATUS_HINTS = {
    "generated": "Generated.",
    "ready": "Ready to generate.",
    "skipped": "Skipped — nothing to do for this field.",
    "blocked": "Waiting on another field.",
    "error": "Generation failed.",
    "no_rule": "No generation rule is set for this field.",
    "rule_off": "This field's generation rule is switched off.",
    "not_generatable": "Omnia cannot generate this field.",
    "unavailable": "Not available right now.",
}

# What each HTTP failure MEANS, in words that name the next action. Used when the service does
# not send an ``error`` of its own.
_HTTP_HINTS = {
    400: "Anki did not understand the request.",
    401: (
        "Anki rejected this app's token.\n"
        "Restart Anki so Omnia writes a fresh one, or paste it into "
        "Settings → Lookup token."
    ),
    403: "Anki refused the request.",
    409: "Switch on Smart Notes → “Regenerate from clippers” in Anki to allow this.",
    503: "Smart Notes is not available right now — is it enabled, and is Anki idle?",
}

_NO_TOKEN = (
    "Couldn't find Omnia's clipper token.\n"
    "• Is the Omnia add-on installed, and has Anki been started since?\n"
    "• Otherwise paste the token into Settings → Lookup token."
)


class GenerateError(Exception):
    """The generation could not run at all (message is user-facing).

    A field that merely came back ``blocked`` or ``no_rule`` is NOT this — that is a normal
    result carrying a reason. This is "the request never happened": no token, Anki closed, the
    option switched off, a malformed answer.
    """


def describe_status(status: str) -> str:
    """Return a readable sentence for a field status/state name.

    Unknown names are returned as-is rather than swallowed: a status this build has never
    heard of is still more useful on screen than silence.
    """
    return _STATUS_HINTS.get(status, status)


def error_message(status_code: int, service_error: str = "") -> str:
    """Turn an HTTP failure into something the user can act on.

    The service's own ``{"error": …}`` wins when it sends one — it knows more than a status
    code does. The fallbacks exist because the two interesting failures (409 "the option is
    off", 401 "bad token") are only actionable if somebody says what to switch on.
    """
    message = service_error.strip()
    if message:
        return message
    return _HTTP_HINTS.get(status_code, f"Anki answered {status_code}.")


@dataclass(frozen=True)
class FieldGeneration:
    """What happened to ONE field, as omnia reports it."""

    field: str
    status: str
    message: str = ""
    text: str = ""
    audio: tuple[str, ...] = ()
    images: tuple[str, ...] = ()

    @property
    def generated(self) -> bool:
        """Whether this field actually changed."""
        return self.status == STATUS_GENERATED

    @property
    def display_message(self) -> str:
        """The line to show on the field: omnia's message, else a rendering of the status."""
        return self.message.strip() or describe_status(self.status)


@dataclass(frozen=True)
class GenerateOutcome:
    """The whole answer for one ``/generate`` request."""

    note_id: int
    results: tuple[FieldGeneration, ...] = ()
    #: The field names THIS request asked for; empty means it asked for the whole note.
    #: Carried because several fields of one note may be generating at once — a whole-note
    #: request and a single-field one, or two single-field ones — and an answer must settle
    #: only its own, or the first one back declares the others unanswered while they are still
    #: running.
    requested: tuple[str, ...] = ()

    @property
    def fields(self) -> tuple[str, ...]:
        """The field names the answer speaks about (in the order omnia listed them)."""
        return tuple(result.field for result in self.results)

    def messages(self) -> dict[str, str]:
        """Field name -> the reason to show, for every field that did NOT generate.

        A generated field says nothing: its new content IS the message.
        """
        return {
            result.field: result.display_message
            for result in self.results
            if not result.generated
        }

    def applied_to(self, card: LookupCardView) -> LookupCardView:
        """Return ``card`` with the generated fields replaced — and nothing else touched.

        Only fields this answer NAMES and reports as ``generated`` change. A field omnia
        skipped keeps what it had (its reason is shown separately), and a name the card does
        not have is ignored rather than invented: the panel renders omnia's field list, and a
        row that appeared out of a generate answer would have no place in the note it came from.
        """
        updates = {result.field: result for result in self.results if result.generated}
        if not updates:
            return card
        fields = tuple(
            (
                _updated_field(existing, updates[existing.name])
                if existing.name in updates
                else existing
            )
            for existing in card.fields
        )
        return dataclasses.replace(card, fields=fields)


def _updated_field(
    existing: LookupFieldView, result: FieldGeneration
) -> LookupFieldView:
    """Fold one generated result into the field view the panel is rendering."""
    return dataclasses.replace(
        existing,
        text=result.text,
        audio=result.audio,
        images=result.images,
        # It has content now, and it plainly can be generated — say so, or the row would keep
        # offering the reason it could not be filled while showing that it has been.
        empty=not (result.text or result.audio or result.images),
        state="ready",
    )


def _urllib_transport(
    url: str, body: dict[str, Any], headers: dict[str, str]
) -> dict[str, Any]:
    """Default transport: POST ``body`` as JSON and parse the reply."""
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise GenerateError(error_message(exc.code, _error_body(exc))) from exc
    except urllib.error.URLError as exc:
        host = url.rsplit(_GENERATE_PATH, 1)[0]
        raise GenerateError(
            f"Can't reach Anki at {host}.\n"
            "• Is Anki running?\n"
            "• Is Omnia → Word Lookup switched on? (it serves this)"
        ) from exc
    except (TimeoutError, OSError) as exc:
        raise GenerateError(
            "Anki did not answer in time. Generation can take a while — "
            "check Anki, then try again."
        ) from exc
    except json.JSONDecodeError as exc:
        raise GenerateError("Anki returned an unreadable response.") from exc
    if not isinstance(payload, dict):
        raise GenerateError("Anki returned an unexpected response.")
    return payload


def _error_body(exc: urllib.error.HTTPError) -> str:
    """Read an error response's ``{"error": …}``, or ``""`` when there is none."""
    try:
        body = json.loads(exc.read().decode("utf-8"))
    except Exception:
        return ""
    return str(body.get("error") or "") if isinstance(body, dict) else ""


class GenerateClient:
    """Asks omnia to regenerate a note's fields."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8766",
        token_provider: Optional[Callable[[], str]] = None,
        transport: Optional[Transport] = None,
    ) -> None:
        """Initialise the client.

        Args:
            base_url: Where omnia's lookup service listens (the same URL the lookups use).
            token_provider: Returns the shared secret, called PER REQUEST so a token written
                after the clipper started is still found. Defaults to discovering it in
                Anki's add-on data.
            transport: The HTTP call (injected in tests). Defaults to a stdlib POST.
        """
        self._base_url = base_url.rstrip("/")
        self._token_provider = (
            token_provider if token_provider is not None else resolve_token
        )
        self._transport = transport if transport is not None else _urllib_transport

    def generate(
        self, note_id: int, fields: Optional[Sequence[str]] = None
    ) -> GenerateOutcome:
        """Regenerate ``fields`` of note ``note_id`` (``None`` = the whole note).

        Blocking, and slow by nature — call it from a worker thread.

        Raises:
            GenerateError: If there is no token, the service cannot be reached, or it refuses
                the request (the message is meant to be shown as-is).
        """
        token = (self._token_provider() or "").strip()
        if not token:
            raise GenerateError(_NO_TOKEN)
        body: dict[str, Any] = {
            "client": CLIENT_NAME,
            "note_id": int(note_id),
            # Explicitly null rather than omitted: "every field" is a decision, and a body
            # that states it cannot be confused with one that forgot the key.
            "fields": None if fields is None else [str(name) for name in fields],
        }
        payload = self._transport(
            f"{self._base_url}{_GENERATE_PATH}",
            body,
            {"Content-Type": "application/json", "X-Omnia-Token": token},
        )
        if not isinstance(payload, dict):
            raise GenerateError("Anki returned an unexpected response.")
        return self._to_outcome(note_id, payload, fields)

    @staticmethod
    def _to_outcome(
        note_id: int,
        payload: dict[str, Any],
        fields: Optional[Sequence[str]] = None,
    ) -> GenerateOutcome:
        """Convert the service payload into dataclasses, tolerating missing keys.

        ``fields`` is what the request asked for; it is carried on the outcome so the panel can
        settle only those, even when another request for the same note is still out.
        """
        results = []
        for raw in payload.get("results") or []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("field") or "")
            if not name:
                continue  # a result that names no field cannot be shown anywhere
            results.append(
                FieldGeneration(
                    field=name,
                    status=str(raw.get("status") or "error"),
                    message=str(raw.get("message") or ""),
                    text=str(raw.get("text") or ""),
                    audio=tuple(str(a) for a in (raw.get("audio") or [])),
                    images=tuple(str(i) for i in (raw.get("images") or [])),
                )
            )
        return GenerateOutcome(
            note_id=int(payload.get("note_id") or note_id),
            results=tuple(results),
            requested=tuple(fields or ()),
        )
