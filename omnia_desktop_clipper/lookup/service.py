"""Run lookups off the Qt main thread and deliver the result back on it.

A lookup is a blocking HTTP round-trip to Anki. Doing it inline would freeze the overlay and the
panel, so every request runs on a throwaway daemon thread and comes back through a queued Qt
signal (the same pattern the hotkeys already use).

Two behaviours matter beyond "don't block":

* **Generation guard.** Selecting a second word while the first request is in flight must never
  render the first word's card. Each request carries a generation number and stale replies are
  dropped.
* **Probe vs full lookup.** The overlay asks for a cheap "does this exist, and how many?" before
  the user clicks; the panel asks for the whole thing. Both share one client and one guard.
"""

from __future__ import annotations

import threading
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal

from .client import LookupClient, LookupUnavailableError, LookupView


class LookupService(QObject):
    """Threaded front end to :class:`~omnia_desktop_clipper.lookup.client.LookupClient`."""

    # (word, view) — a completed lookup, on the Qt main thread.
    finished = pyqtSignal(str, object)
    # (word, message) — the lookup could not run.
    failed = pyqtSignal(str, str)
    # (word, count) — a cheap existence probe for the overlay hint; count -1 means "unknown".
    probed = pyqtSignal(str, int)

    def __init__(self, client: LookupClient) -> None:
        super().__init__()
        self._client = client
        self._generation = 0
        self._lock = threading.Lock()

    def _next_generation(self) -> int:
        with self._lock:
            self._generation += 1
            return self._generation

    def _is_current(self, generation: int) -> bool:
        with self._lock:
            return generation == self._generation

    def probe(self, word: str) -> None:
        """Ask (in the background) how many notes match ``word``; emits :attr:`probed`."""
        word = word.strip()
        if not word:
            return
        generation = self._next_generation()

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

    @staticmethod
    def _spawn(work) -> None:
        threading.Thread(target=work, name="omnia-lookup", daemon=True).start()
