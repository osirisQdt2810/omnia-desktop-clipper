"""Run lookups off the Qt main thread and deliver the result back on it.

A lookup is a blocking HTTP round-trip to Anki. Doing it inline would freeze the overlay and the
panel, so every request runs on a throwaway daemon thread and comes back through a queued Qt
signal (the same pattern the hotkeys already use).

Three behaviours matter beyond "don't block":

* **Generation guard.** Selecting a second word while the first request is in flight must never
  render the first word's card. Each request carries a generation number and stale replies are
  dropped (see :class:`~omnia_desktop_clipper.lookup.guard.GenerationGuard`).
* **Probe vs full lookup.** The overlay asks for a cheap "does this exist, and how many?" before
  the user clicks; the panel asks for the whole thing. Both share one client and one guard.
* **Regeneration.** The same service, a different route: slow, authenticated, and answered
  field by field. It gets its own guard, because several fields regenerating at once are all
  still wanted — what makes them stale is a lookup for ANOTHER word, not each other.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from .client import LookupClient, LookupUnavailableError, LookupView
from .generate import GenerateClient, GenerateError
from .guard import GenerationGuard


class LookupService(QObject):
    """Threaded front end to :class:`~omnia_desktop_clipper.lookup.client.LookupClient`."""

    # (word, view) — a completed lookup, on the Qt main thread.
    finished = pyqtSignal(str, object)
    # (word, message) — the lookup could not run.
    failed = pyqtSignal(str, str)
    # (word, count) — a cheap existence probe for the overlay hint; count -1 means "unknown".
    probed = pyqtSignal(str, int)
    # (note_id, GenerateOutcome) — a completed regeneration, on the Qt main thread.
    #
    # ``qint64``, NOT a bare ``int``. PyQt maps a bare ``int`` to a 32-bit C++ ``int``, and an
    # Anki note id is the note's creation time in MILLISECONDS — 13 digits, far past
    # 2147483647. Emitting one through an ``int`` slot raises OverflowError on the worker
    # thread, outside ``work()``'s try, so the thread dies, the answer never arrives, and the
    # field spins forever over a note Anki has in fact already regenerated. The tests cannot
    # see it: their fixtures use ids like 42. This is the same trap `_on_select_gesture`
    # documents for a float in an int slot.
    generated = pyqtSignal("qint64", object)
    # (note_id, message, requested field names) — the regeneration could not run at all.
    # The names travel with it so a failure clears only the fields THIS request asked for.
    generate_failed = pyqtSignal("qint64", str, object)

    def __init__(
        self, client: LookupClient, generator: Optional[GenerateClient] = None
    ) -> None:
        """Wrap ``client`` (and, when regeneration is available, ``generator``) in threads.

        Args:
            client: The read-only lookup/media client.
            generator: The authenticated ``/generate`` client. ``None`` disables regeneration.
        """
        super().__init__()
        self._client = client
        self._generator = generator
        self._lookups = GenerationGuard()
        self._regenerations = GenerationGuard()

    def _next_generation(self) -> int:
        """Start a new LOOKUP, superseding the previous one AND any regeneration in flight.

        A regeneration is only wanted while its note is the one the user is looking at, and a
        new lookup is precisely the moment they stop looking at it. ``probe`` deliberately does
        NOT come through here — see its docstring.
        """
        self._regenerations.invalidate()
        return self._lookups.invalidate()

    def _is_current(self, generation: int) -> bool:
        return self._lookups.is_current(generation)

    def probe(self, word: str) -> None:
        """Ask (in the background) how many notes match ``word``; emits :attr:`probed`.

        Supersedes the previous PROBE only. A probe is the cheap existence check behind the
        "+" pill and it changes nothing the panel shows, so it must not invalidate a
        regeneration: selecting any other word while a field is generating would otherwise
        drop the answer on arrival, leaving the row disabled and the spinner ticking forever
        over a note Anki had already rewritten and been paid for.
        """
        word = word.strip()
        if not word:
            return
        generation = self._lookups.invalidate()

        def work() -> None:
            try:
                view = self._client.lookup(word)
                count = len(view.cards)
            except LookupUnavailableError:
                count = -1  # unknown: leave the overlay's neutral appearance
            except Exception:
                count = -1
            if self._is_current(generation):
                self.probed.emit(word, count)

        self._spawn(work)

    def lookup(self, word: str) -> None:
        """Run a full lookup for ``word``; emits :attr:`finished` or :attr:`failed`."""
        word = word.strip()
        if not word:
            return
        generation = self._next_generation()

        def work() -> None:
            try:
                view: Optional[LookupView] = self._client.lookup(word)
            except LookupUnavailableError as exc:
                if self._is_current(generation):
                    self.failed.emit(word, str(exc))
                return
            except Exception:
                if self._is_current(generation):
                    self.failed.emit(word, "The lookup failed unexpectedly.")
                return
            if self._is_current(generation) and view is not None:
                self.finished.emit(word, view)

        self._spawn(work)

    def generate(self, note_id: int, fields: Optional[Sequence[str]] = None) -> None:
        """Regenerate ``fields`` of ``note_id`` (``None`` = the whole note), in the background.

        Emits :attr:`generated` with the per-field outcome, or :attr:`generate_failed` when the
        request could not run. Both carry the note id so the panel applies the answer to the
        note it was asked for, even if the user has stepped to another one meanwhile.

        Unlike a lookup this does NOT supersede its predecessors: two fields regenerating at
        once are both still wanted. Only a new lookup invalidates them.
        """
        if self._generator is None:
            self.generate_failed.emit(
                note_id,
                "Regenerating from the clipper is not available in this build.",
                tuple(fields or ()),
            )
            return
        generation = self._regenerations.current()
        requested = None if fields is None else list(fields)
        generator = self._generator

        def work() -> None:
            try:
                outcome = generator.generate(note_id, requested)
            except GenerateError as exc:
                if self._regenerations.is_current(generation):
                    self.generate_failed.emit(note_id, str(exc), tuple(requested or ()))
                return
            except Exception:
                if self._regenerations.is_current(generation):
                    self.generate_failed.emit(
                        note_id,
                        "The regeneration failed unexpectedly.",
                        tuple(requested or ()),
                    )
                return
            if self._regenerations.is_current(generation):
                self.generated.emit(note_id, outcome)

        self._spawn(work, name="omnia-generate")

    def media(self, filename: str) -> bytes | None:
        """Return a collection-media file's bytes from omnia, or ``None``.

        Synchronous on purpose: the only caller already runs on its own worker thread, because
        the bytes become a QPixmap and that is main-thread-only. Wrapping it in another thread
        would buy nothing and cost a second hop back.
        """
        return self._client.media(filename)

    @staticmethod
    def _spawn(work, name: str = "omnia-lookup") -> None:
        threading.Thread(target=work, name=name, daemon=True).start()
