"""A generation guard: deliver only the answers the user is still waiting for.

Every request this package makes is answered on a worker thread — tens of milliseconds later
for a lookup, tens of SECONDS later for a regeneration (it calls an LLM/TTS provider). By then
the user may well have selected another word, and rendering the old answer would put the wrong
card on screen with nothing to explain it. Each request captures the generation that was
current when it started, and its answer is delivered only while that generation still is.

Pure module (``threading`` only, no PyQt6), so it unit-tests headless.
"""

from __future__ import annotations

import threading


class GenerationGuard:
    """A monotonic counter that says whether a request's answer is still wanted.

    Two usage shapes, both needed here:

    * ``invalidate()`` per request — the lookups. Starting a new lookup supersedes the one
      before it, so only the newest answer is ever rendered.
    * ``current()`` per request — the regenerations. Several fields can be regenerating at
      once and every one of them is still wanted; what supersedes them is the CONTEXT
      changing, i.e. a lookup for another word calling ``invalidate()``.
    """

    def __init__(self) -> None:
        self._generation = 0
        self._lock = threading.Lock()

    def current(self) -> int:
        """Return the generation a request starting now belongs to."""
        with self._lock:
            return self._generation

    def invalidate(self) -> int:
        """Supersede every in-flight request; return the new current generation."""
        with self._lock:
            self._generation += 1
            return self._generation

    def is_current(self, generation: int) -> bool:
        """Whether an answer from ``generation`` should still be delivered."""
        with self._lock:
            return generation == self._generation
