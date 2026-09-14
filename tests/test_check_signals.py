"""The Qt boundary for phrase checking: the service's guard and its signals.

The state machine is tested against plain objects in ``test_lookup_check.py``; what only shows
up here is which answers the SERVICE lets through. Its guard and the panel's ticket are two
different mechanisms solving the same problem at two different layers, and a test of one says
nothing about the other.

Skipped where PyQt6 is absent; CI installs it from requirements.txt.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PyQt6")

from omnia_desktop_clipper.lookup.check import CheckError
from omnia_desktop_clipper.lookup.service import LookupService


class _NullClient:
    """Enough of a LookupClient for the service to construct; nothing is called."""

    def lookup(self, *_args, **_kwargs):  # pragma: no cover - never reached
        raise AssertionError("no lookup should be made")


class _Checker:
    """A CheckClient stand-in whose answers can be released one at a time."""

    def __init__(self):
        self.calls: list[tuple[str, str, bool]] = []
        self.released = threading.Event()
        self.started = threading.Event()
        self.answer = {"rewritten": "I went."}
        self.raises: Exception | None = None
        self.block = False

    def check(self, text, mode="", refresh=False):
        self.calls.append((text, mode, refresh))
        self.started.set()
        if self.block:
            assert self.released.wait(5), "the answer was never released"
        if self.raises is not None:
            raise self.raises
        from omnia_desktop_clipper.lookup.check import to_correction

        return to_correction(self.answer)


def _drain(qapp, predicate, timeout=5.0):
    """Pump the Qt event loop until ``predicate`` or the timeout."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


class TestTheCheckSignal:
    def test_an_answer_comes_back_on_the_checked_signal(self, qapp):
        checker = _Checker()
        service = LookupService(_NullClient(), checker=checker)
        seen: list[tuple] = []
        service.checked.connect(
            lambda phrase, correction: seen.append((phrase, correction))
        )

        service.check("I have went.", "spoken", True)

        assert _drain(qapp, lambda: seen)
        assert seen[0][0] == "I have went."
        assert seen[0][1].rewritten == "I went."
        assert checker.calls == [("I have went.", "spoken", True)]

    def test_a_failure_comes_back_with_its_own_sentence(self, qapp):
        checker = _Checker()
        checker.raises = CheckError("Phrase Check is switched off.")
        service = LookupService(_NullClient(), checker=checker)
        seen: list[tuple] = []
        service.check_failed.connect(
            lambda phrase, message: seen.append((phrase, message))
        )

        service.check("x")

        assert _drain(qapp, lambda: seen)
        assert seen[0][1] == "Phrase Check is switched off."

    def test_an_unexpected_error_is_reported_rather_than_swallowed(self, qapp):
        checker = _Checker()
        checker.raises = ValueError("an internal detail")
        service = LookupService(_NullClient(), checker=checker)
        seen: list[tuple] = []
        service.check_failed.connect(
            lambda phrase, message: seen.append((phrase, message))
        )

        service.check("x")

        assert _drain(qapp, lambda: seen)
        assert "unexpectedly" in seen[0][1]
        assert (
            "internal detail" not in seen[0][1]
        ), "an internal message reached the panel"

    def test_no_checker_says_so_instead_of_hanging(self, qapp):
        service = LookupService(_NullClient())
        seen: list[tuple] = []
        service.check_failed.connect(
            lambda phrase, message: seen.append((phrase, message))
        )

        service.check("x")

        assert _drain(
            qapp, lambda: seen
        ), "a build without a checker left the panel spinning"

    def test_an_empty_phrase_is_not_a_request(self, qapp):
        checker = _Checker()
        service = LookupService(_NullClient(), checker=checker)

        service.check("   ")

        assert checker.calls == []


class TestWhichAnswersSurvive:
    def test_a_second_check_supersedes_the_first(self, qapp):
        # The register toggle re-asks for the SAME phrase, so two answers for one selection can
        # be in flight at once and only the latest is ever wanted.
        slow = _Checker()
        slow.block = True
        service = LookupService(_NullClient(), checker=slow)
        seen: list[str] = []
        service.checked.connect(
            lambda phrase, correction: seen.append(correction.rewritten)
        )

        service.check("I have went.", "")
        assert slow.started.wait(5), "the first check never started"
        slow.answer = {"rewritten": "THE SECOND"}
        slow.block = False
        service.check("I have went.", "spoken")
        assert _drain(qapp, lambda: seen)

        slow.released.set()  # let the first one finish, long after
        assert not _drain(
            qapp, lambda: len(seen) > 1, timeout=0.6
        ), "the abandoned answer landed over the one on screen"
        assert seen == ["THE SECOND"]

    def test_a_new_lookup_abandons_a_check_in_flight(self, qapp):
        # A check is an answer about the text that WAS selected. Left to land over a new
        # selection it is not stale decoration but a wrong answer to the question on screen.
        checker = _Checker()
        checker.block = True
        service = LookupService(_NullClient(), checker=checker)
        seen: list[str] = []
        service.checked.connect(lambda phrase, correction: seen.append(phrase))

        service.check("I have went.")
        assert checker.started.wait(5)
        service.lookup("another")  # a new lookup invalidates everything before it
        checker.released.set()

        assert not _drain(
            qapp, lambda: seen, timeout=0.6
        ), "a correction for the previous selection arrived anyway"
