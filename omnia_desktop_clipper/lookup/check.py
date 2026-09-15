"""Ask omnia what is wrong with a phrase, and what a fluent speaker would write.

The lookup panel answers *"is this word in my collection?"*. This answers the question that
comes just before it: the user is reading or writing something and is not sure the sentence is
right. Select a phrase, press the wand, and omnia sends back a **list** of small fixes — each
with its own reason — then the whole phrase rewritten with the changed words marked.

That shape is the feature, and it is why this is not "ask a model to fix my sentence". "Your
sentence should be X" teaches nothing, and one paragraph explaining six unrelated problems is
read by nobody.

Two registers, because the same sentence is wrong in different ways depending on whether it is
being said or written — *"I ain't got none"* is a mistake in an essay and ordinary in
conversation — and a corrector with one standard is wrong half the time with total confidence.

Like :mod:`omnia_desktop_clipper.lookup.generate`, and for the same reasons:

* **It spends money.** One LLM call per phrase, which is why it sits behind an explicit user
  action rather than a probe. Nothing authenticates it — the service is loopback-only, so being
  able to reach it is the permission.
* **It is slow enough to matter.** Not `/generate`-slow (that is several calls filling a whole
  note); this is one call on one phrase, with the user watching. Its budget is sized for that:
  long enough for a slow model, short enough that the panel stops claiming to be busy.
* **The highlight runs come from omnia**, computed there and sent. Two implementations of
  "which words changed" is two answers to a question with one right one, and the desktop and web
  panels would slowly disagree.

Pure of PyQt6 and of any UI import, with an injectable transport, so it unit-tests headless.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

from .generate import Transport, error_body

#: How many fixes omnia says the panel should list. Every fix still arrives — the saved card is
#: built from the same answer, and the ones nobody had room for are the ones worth coming back
#: to — so this is a display limit and nothing is dropped on the way in.
DEFAULT_FIXES_SHOWN = 5

#: The registers a phrase can be judged in. Sent as-is; omnia decides when it is empty.
WRITTEN = "written"
SPOKEN = "spoken"
MODES = (WRITTEN, SPOKEN)

_CHECK_PATH = "/check"
_SAVE_PATH = "/check/save"

# One model call on a phrase the user has selected and is watching a spinner for. Much shorter
# than /generate's five minutes on purpose: that is several calls filling a whole note and may
# reasonably be left running, where this is past the point of anything coming back. Waiting
# longer only leaves the panel lying about being busy.
_TIMEOUT_SECONDS = 90.0

# What each HTTP failure MEANS, in words that name the next action. Used when the service does
# not send an ``error`` of its own.
#
# The two that matter are told apart deliberately. 503 is "Phrase Check is switched off",
# answered by a toggle; 502 is "it ran and failed", and omnia puts the provider's own words in
# the body, which is the difference between "check your key" and "you are out of credit".
# Flattening those sends the user hunting through settings for a problem that was never there.
_HTTP_HINTS = {
    400: "Anki did not understand the request.",
    403: "Anki refused the request.",
    # An omnia that predates Phrase Check has no such endpoint and sends no body to explain it.
    # Not a setting — an update. The clipper ships separately from the add-on, so this is a
    # routine combination rather than an exotic one.
    404: (
        # Also what an omnia with Phrase Check but no SAVE route answers. One sentence covers
        # both, because the remedy is the same and the user cannot tell them apart anyway.
        "The Omnia add-on in Anki does not have this yet.\n"
        "Update it (Tools → Add-ons → Check for Updates), then try again."
    ),
    502: "Omnia could not check that phrase.",
    503: (
        "Phrase Check is switched off.\n"
        "Turn it on in Anki (Tools → Omnia), then try again."
    ),
}


class CheckError(Exception):
    """A phrase could not be checked. The message is meant to be shown as-is."""


def error_message(status_code: int, service_error: str = "") -> str:
    """Turn an HTTP failure into something the user can act on.

    The service's own ``{"error": …}`` wins when it sends one — it knows more than a status code
    does, and on a 502 it is carrying the provider's sentence, which names the thing only the
    user can fix.
    """
    message = service_error.strip()
    if message:
        return message
    return _HTTP_HINTS.get(status_code, f"Anki answered {status_code}.")


@dataclass(frozen=True)
class Fix:
    """One change, and why it was made."""

    before: str
    after: str
    why: str = ""
    kind: str = ""
    is_deletion: bool = False


@dataclass(frozen=True)
class Correction:
    """What omnia says about one phrase."""

    original: str
    rewritten: str
    mode: str = WRITTEN
    already_good: bool = False
    changed: bool = False
    fixes: tuple[Fix, ...] = ()
    #: ``((text, is_new), …)`` — joined in order it is exactly :attr:`rewritten`, so a panel
    #: rendering the runs cannot show a sentence nobody wrote.
    highlight: tuple[tuple[str, bool], ...] = field(default_factory=tuple)
    #: How many of :attr:`fixes` the panel should list, most important first. Not a cap on what
    #: arrived: a saved card keeps every one.
    shown: int = DEFAULT_FIXES_SHOWN

    @property
    def visible_fixes(self) -> tuple[Fix, ...]:
        """The fixes the panel lists — the first :attr:`shown`, in the order omnia ranked them."""
        limit = max(1, int(self.shown or DEFAULT_FIXES_SHOWN))
        return self.fixes[:limit]

    @property
    def hidden_fixes(self) -> int:
        """How many are being held back. Zero when everything is on screen."""
        return max(0, len(self.fixes) - len(self.visible_fixes))

    @property
    def has_changes(self) -> bool:
        """Whether there is anything to show as a fix."""
        return bool(self.fixes)


def _as_fix(raw: Any) -> Optional[Fix]:
    """One fix from the wire, or ``None`` when the entry is not one."""
    if not isinstance(raw, dict):
        return None
    return Fix(
        before=str(raw.get("before") or ""),
        after=str(raw.get("after") or ""),
        why=str(raw.get("why") or "").strip(),
        kind=str(raw.get("kind") or "").strip(),
        is_deletion=bool(raw.get("is_deletion")),
    )


def _as_runs(raw: Any, rewritten: str) -> tuple[tuple[str, bool], ...]:
    """The highlight runs from the wire, checked against the sentence they claim to be.

    A payload without them falls back to the plain sentence rather than to a guess: an older
    omnia, or one that could not diff, is better served by an unmarked rewrite than by this side
    inventing which words moved.

    A payload whose runs do NOT join back to ``rewritten`` falls back the same way. The panel
    renders the runs and the Copy button hands over ``rewritten``, so runs that disagree would
    put something on the clipboard other than the sentence on screen — and a user who pastes a
    correction they did not read is exactly who this feature is for.
    """
    runs: list[tuple[str, bool]] = []
    for entry in raw or ():
        if isinstance(entry, (list, tuple)) and entry:
            runs.append((str(entry[0]), bool(entry[1]) if len(entry) > 1 else False))
    if not runs or "".join(text for text, _new in runs) != rewritten:
        return ((rewritten, False),)
    return tuple(runs)


def to_correction(payload: dict[str, Any]) -> Correction:
    """Convert the service payload into dataclasses, tolerating missing keys.

    Tolerant on purpose: a panel that raised on an unexpected shape would turn a partial answer
    into no answer, and the parts that did arrive are still worth showing.
    """
    fixes = tuple(
        fix
        for fix in (_as_fix(raw) for raw in payload.get("fixes") or ())
        if fix is not None
    )
    rewritten = str(payload.get("rewritten") or "")
    return Correction(
        original=str(payload.get("original") or ""),
        rewritten=rewritten,
        mode=str(payload.get("mode") or WRITTEN),
        already_good=bool(payload.get("already_good")),
        changed=bool(payload.get("changed")),
        fixes=fixes,
        highlight=_as_runs(payload.get("highlight"), rewritten),
        shown=_as_shown(payload.get("shown")),
    )


def _as_shown(raw: Any) -> int:
    """How many fixes to list, from a payload that may not say.

    An older omnia sends no limit at all, and showing everything beats showing nothing — so the
    fallback is the default rather than zero.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_FIXES_SHOWN
    return value if value > 0 else DEFAULT_FIXES_SHOWN


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
        raise CheckError(error_message(exc.code, error_body(exc))) from exc
    except urllib.error.URLError as exc:
        host = url.rsplit(_CHECK_PATH, 1)[0]
        raise CheckError(
            f"Can't reach Anki at {host}.\n"
            "• Is Anki running?\n"
            "• Is Omnia → Word Lookup switched on? (it serves this)"
        ) from exc
    except (TimeoutError, OSError) as exc:
        raise CheckError(
            "Anki did not answer in time. The model may be slow or unreachable — "
            "try again, or pick a shorter phrase."
        ) from exc
    except json.JSONDecodeError as exc:
        raise CheckError("Anki returned an unreadable response.") from exc
    if not isinstance(payload, dict):
        raise CheckError("Anki returned an unexpected response.")
    return payload


@dataclass(frozen=True)
class SaveResult:
    """Where a saved correction went, as omnia reports it."""

    note_id: int = 0
    deck: str = ""
    note_type: str = ""
    renamed: bool = False
    #: omnia's own sentence. Shown as-is: it names the deck, and says when the note type had to
    #: be renamed, which is the one thing about a save nobody can see for themselves.
    summary: str = ""


def _note_id(value: Any) -> int:
    """``value`` as a note id, or 0.

    Tolerant on purpose. A bare ``int()`` here raised ``ValueError`` on anything non-numeric,
    which ``LookupService.save``'s broad except turned into "The save failed unexpectedly." —
    said about a note omnia had ALREADY written, whose natural answer is to press Save again and
    write a second one. Nothing reads this field; it is not worth a duplicate note.
    """
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def to_save_result(payload: dict[str, Any]) -> SaveResult:
    """Convert the save payload into a dataclass, tolerating missing keys."""
    return SaveResult(
        note_id=_note_id(payload.get("note_id")),
        deck=str(payload.get("deck") or ""),
        note_type=str(payload.get("note_type") or ""),
        renamed=bool(payload.get("renamed")),
        summary=str(payload.get("summary") or "").strip() or "Saved to Anki.",
    )


class CheckClient:
    """Asks omnia to correct a phrase, and to keep one as a card."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8766",
        transport: Optional[Transport] = None,
    ) -> None:
        """Initialise the client.

        Args:
            base_url: Where omnia's lookup service listens (the same URL the lookups use).
        """
        self._base_url = base_url.rstrip("/")
        self._transport = transport if transport is not None else _urllib_transport

    def check(self, text: str, mode: str = "", refresh: bool = False) -> Correction:
        """Correct ``text``, judged as ``mode``.

        Blocking, and slow by nature — call it from a worker thread.

        Args:
            text: The selected phrase.
            mode: ``"written"``, ``"spoken"``, or empty for whatever omnia is set to. Empty is
                sent deliberately: the configured default lives in the add-on, and guessing here
                would quietly override a setting.
            refresh: True to ignore omnia's remembered answer and ask again.

        Raises:
            CheckError: If the service cannot be reached, or it refuses the request (the
                message is meant to be shown as-is).
        """
        phrase = (text or "").strip()
        if not phrase:
            raise CheckError("There is nothing selected to check.")
        payload = self._transport(
            f"{self._base_url}{_CHECK_PATH}",
            {
                "text": phrase,
                "mode": mode if mode in MODES else "",
                "refresh": bool(refresh),
            },
            {"Content-Type": "application/json"},
        )
        if not isinstance(payload, dict):
            raise CheckError("Anki returned an unexpected response.")
        return to_correction(payload)

    def save(self, text: str, mode: str = "") -> SaveResult:
        """Keep the correction for ``text`` as a note in Anki.

        The PHRASE is sent, not the correction. omnia looks it up again — almost always a cache
        hit — and builds the note itself; letting this app post note content into somebody's
        collection would be a different feature with a different risk, and this one does not
        need it.

        Blocking. Short, but it waits on Anki's main thread, so call it from a worker.

        Raises:
            CheckError: If the service cannot be reached, or it refuses (the message is meant
                to be shown as-is).
        """
        phrase = (text or "").strip()
        if not phrase:
            raise CheckError("There is nothing selected to save.")
        payload = self._transport(
            f"{self._base_url}{_SAVE_PATH}",
            {"text": phrase, "mode": mode if mode in MODES else ""},
            {"Content-Type": "application/json"},
        )
        if not isinstance(payload, dict):
            raise CheckError("Anki returned an unexpected response.")
        return to_save_result(payload)
