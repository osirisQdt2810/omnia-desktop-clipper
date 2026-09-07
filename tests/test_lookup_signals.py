"""The one boundary the pure-logic tests structurally cannot see: the Qt signal slots.

Everything else about regeneration is tested against plain objects, which is why a real note
id never reaches a real signal anywhere else in this suite. An Anki note id is the note's
creation time in MILLISECONDS — thirteen digits — and a bare ``int`` in a ``pyqtSignal``
signature is a 32-bit C++ ``int``. Emitting one through the wrong slot raises on the worker
thread, so the answer never arrives and the field spins over a note Anki has already changed.
The fixtures elsewhere use ids like 42 and 123, which fit.

Skipped where PyQt6 is absent; CI installs it from requirements.txt.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from omnia_desktop_clipper.lookup.generate import GenerateOutcome
from omnia_desktop_clipper.lookup.service import LookupService

#: A real Anki note id: epoch milliseconds, and about 700 times the 32-bit ceiling.
_REAL_NOTE_ID = 1725638400123


class _NullClient:
    """Enough of a LookupClient for the service to construct; nothing is called."""

    def lookup(self, *_args, **_kwargs):  # pragma: no cover - never reached
        raise AssertionError("no request should be made")


class TestTheNoteIdSurvivesTheSignal:
    def test_a_completed_regeneration_carries_a_real_note_id(self) -> None:
        service = LookupService(_NullClient())
        seen: list[tuple[int, object]] = []
        service.generated.connect(lambda nid, outcome: seen.append((nid, outcome)))
        outcome = GenerateOutcome(note_id=_REAL_NOTE_ID)

        service.generated.emit(_REAL_NOTE_ID, outcome)

        assert seen == [(_REAL_NOTE_ID, outcome)]

    def test_a_failed_regeneration_carries_a_real_note_id(self) -> None:
        service = LookupService(_NullClient())
        seen: list[tuple[int, str, object]] = []
        service.generate_failed.connect(
            lambda nid, msg, names: seen.append((nid, msg, names))
        )

        service.generate_failed.emit(_REAL_NOTE_ID, "Anki is not running.", ("Audio",))

        assert seen == [(_REAL_NOTE_ID, "Anki is not running.", ("Audio",))]
