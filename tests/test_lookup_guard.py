"""Tests for the generation guard that decides which late answers are still wanted.

A lookup answers in milliseconds; a regeneration calls an LLM and a TTS voice and answers in
tens of SECONDS. Both land on the Qt thread long after the gesture that asked for them, and by
then the user may be reading a different word entirely. Rendering that answer would put the
wrong card on screen with nothing to explain it — this is the rule that stops it.

Pure ``threading``: no PyQt6 here, so the rule is testable without a QApplication.
"""

from __future__ import annotations

import threading

from omnia_desktop_clipper.lookup.guard import GenerationGuard


class TestSupersedingRequests:
    """``invalidate()`` per request — how the lookups use it: newest answer only."""

    def test_the_newest_request_is_the_current_one(self) -> None:
        guard = GenerationGuard()

        first = guard.invalidate()
        second = guard.invalidate()

        assert guard.is_current(second) is True
        assert guard.is_current(first) is False

    def test_a_stale_answer_is_discarded(self) -> None:
        """Selecting a second word while the first lookup is in flight."""
        guard = GenerationGuard()
        delivered: list[str] = []

        slow = guard.invalidate()  # "plunge" — still in flight
        guard.invalidate()  # the user selects "dive"

        if guard.is_current(slow):
            delivered.append("plunge")

        assert delivered == []

    def test_a_fresh_answer_is_delivered(self) -> None:
        guard = GenerationGuard()
        generation = guard.invalidate()

        assert guard.is_current(generation) is True


class TestSharingAGeneration:
    """``current()`` per request — how the regenerations use it: siblings coexist."""

    def test_two_requests_started_together_are_both_still_wanted(self) -> None:
        """Two fields regenerating at once must not cancel each other."""
        guard = GenerationGuard()

        definition = guard.current()
        audio = guard.current()

        assert guard.is_current(definition) and guard.is_current(audio)

    def test_reading_the_current_generation_supersedes_nothing(self) -> None:
        guard = GenerationGuard()
        started = guard.current()

        guard.current()
        guard.current()

        assert guard.is_current(started) is True

    def test_a_new_lookup_discards_a_regeneration_in_flight(self) -> None:
        """The user navigated away: the answer is about a note they are no longer looking at."""
        guard = GenerationGuard()
        delivered: list[str] = []

        regeneration = guard.current()
        guard.invalidate()  # a lookup for another word

        if guard.is_current(regeneration):
            delivered.append("Definition")

        assert delivered == []


class TestThreadSafety:
    def test_concurrent_invalidations_all_produce_distinct_generations(self) -> None:
        """Answers arrive on worker threads, so the counter is touched from several at once."""
        guard = GenerationGuard()
        seen: list[int] = []
        lock = threading.Lock()

        def work() -> None:
            generation = guard.invalidate()
            with lock:
                seen.append(generation)

        threads = [threading.Thread(target=work) for _ in range(50)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sorted(seen) == list(range(1, 51))
        assert guard.is_current(max(seen)) is True
