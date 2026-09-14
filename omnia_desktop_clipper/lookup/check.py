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

#: The registers a phrase can be judged in. Sent as-is; omnia decides when it is empty.
WRITTEN = "written"
SPOKEN = "spoken"
MODES = (WRITTEN, SPOKEN)

_CHECK_PATH = "/check"

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
        "The Omnia add-on in Anki does not have Phrase Check.\n"
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

    @property
    def label(self) -> str:
        """The change on one line, for a card's heading."""
        return f"{self.before} → {'(removed)' if self.is_deletion else self.after}"


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
    """The highlight runs from the wire.

    A payload without them falls back to the plain sentence rather than to a guess: an older
    omnia, or one that could not diff, is better served by an unmarked rewrite than by this side
    inventing which words moved.
    """
    runs: list[tuple[str, bool]] = []
    for entry in raw or ():
        if isinstance(entry, (list, tuple)) and entry:
            runs.append((str(entry[0]), bool(entry[1]) if len(entry) > 1 else False))
    return tuple(runs) if runs else ((rewritten, False),)


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


class CheckClient:
    """Asks omnia to correct a phrase."""

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
