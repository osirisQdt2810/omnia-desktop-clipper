"""The Qt boundary for phrase checking: the service's guard and its signals.

The state machine is tested against plain objects in ``test_lookup_check.py``; what only shows
up here is which answers the SERVICE lets through. Its guard and the panel's ticket are two
different mechanisms solving the same problem at two different layers, and a test of one says
nothing about the other.

A ``QCoreApplication``, not a ``QApplication``: these tests need an event loop to deliver a
signal emitted on a worker thread, and nothing else. ``QtWidgets`` pulls in ``libEGL``, which a
headless Linux runner does not have, so asking for it here would skip the suite on a platform
that can perfectly well run it.

Skipped where PyQt6 is absent; CI installs it from requirements.txt.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PyQt6.QtCore")

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
        self.saves: list[tuple[str, str]] = []
        self.released = threading.Event()
        self.started = threading.Event()
        self.answer = {"rewritten": "I went."}
        self.raises: Exception | None = None
        self.save_raises: Exception | None = None
        self.save_result = None
        self.block = False

    def save(self, text, mode=""):
        self.saves.append((text, mode))
        self.started.set()
        if self.block:
            assert self.released.wait(5), "the save was never released"
        if self.save_raises is not None:
            raise self.save_raises
        from omnia_desktop_clipper.lookup.check import to_save_result

        return self.save_result or to_save_result({"summary": "Saved."})

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
    from PyQt6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


class TestTheCheckSignal:
    def test_an_answer_comes_back_on_the_checked_signal(self, qapp):
        checker = _Checker()
        service = LookupService(_NullClient(), checker=checker)
        seen: list[tuple] = []
        service.checked.connect(lambda *args: seen.append(args))

        service.check("I have went.", "spoken", True)

        assert _drain(qapp, lambda: seen)
        assert seen[0][0] == "I have went."
        assert seen[0][1].rewritten == "I went."
        assert checker.calls == [("I have went.", "spoken", True)]

    def test_the_caller_s_ticket_comes_back_untouched(self, qapp):
        # The panel starts requests of its own (the register toggle), so it is the only thing
        # that knows which answer it is waiting for. A ticket the app looked up when the answer
        # ARRIVED would always be the latest one, and the panel's guard could never fire.
        checker = _Checker()
        service = LookupService(_NullClient(), checker=checker)
        seen: list[tuple] = []
        service.checked.connect(lambda *args: seen.append(args))

        service.check("I have went.", "", False, 41)

        assert _drain(qapp, lambda: seen)
        assert seen[0][2] == 41

    def test_the_ticket_comes_back_on_the_failure_path_too(self, qapp):
        checker = _Checker()
        checker.raises = CheckError("nope")
        service = LookupService(_NullClient(), checker=checker)
        seen: list[tuple] = []
        service.check_failed.connect(lambda *args: seen.append(args))

        service.check("x", "", False, 7)

        assert _drain(qapp, lambda: seen)
        assert seen[0][2] == 7

    def test_a_build_without_a_checker_still_returns_the_ticket(self, qapp):
        # Otherwise the panel never learns this request is over, and spins for ever.
        service = LookupService(_NullClient())
        seen: list[tuple] = []
        service.check_failed.connect(lambda *args: seen.append(args))

        service.check("x", "", False, 9)

        assert _drain(qapp, lambda: seen)
        assert seen[0][2] == 9

    def test_a_failure_comes_back_with_its_own_sentence(self, qapp):
        checker = _Checker()
        checker.raises = CheckError("Phrase Check is switched off.")
        service = LookupService(_NullClient(), checker=checker)
        seen: list[tuple] = []
        service.check_failed.connect(lambda *args: seen.append(args))

        service.check("x")

        assert _drain(qapp, lambda: seen)
        assert seen[0][1] == "Phrase Check is switched off."

    def test_an_unexpected_error_is_reported_rather_than_swallowed(self, qapp):
        checker = _Checker()
        checker.raises = ValueError("an internal detail")
        service = LookupService(_NullClient(), checker=checker)
        seen: list[tuple] = []
        service.check_failed.connect(lambda *args: seen.append(args))

        service.check("x")

        assert _drain(qapp, lambda: seen)
        assert "unexpectedly" in seen[0][1]
        assert (
            "internal detail" not in seen[0][1]
        ), "an internal message reached the panel"

    def test_no_checker_says_so_instead_of_hanging(self, qapp):
        service = LookupService(_NullClient())
        seen: list[tuple] = []
        service.check_failed.connect(lambda *args: seen.append(args))

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
            lambda _p, correction, _t: seen.append(correction.rewritten)
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
        service.checked.connect(lambda phrase, _c, _t: seen.append(phrase))

        service.check("I have went.")
        assert checker.started.wait(5)
        service.lookup("another")  # a new lookup invalidates everything before it
        checker.released.set()

        assert not _drain(
            qapp, lambda: seen, timeout=0.6
        ), "a correction for the previous selection arrived anyway"

    def test_cancelling_abandons_a_check_without_starting_one(self, qapp):
        # The route a new SELECTION takes. It fires only `probe`, which deliberately invalidates
        # lookups alone, so nothing else on that path supersedes a check — and a correction that
        # lands afterwards does not merely draw into a hidden panel, it shows, raises and
        # ACTIVATES it, taking focus from whatever the user is now typing in.
        checker = _Checker()
        checker.block = True
        service = LookupService(_NullClient(), checker=checker)
        seen: list[str] = []
        service.checked.connect(lambda phrase, _c, _t: seen.append(phrase))

        service.check("I have went.")
        assert checker.started.wait(5)
        service.cancel_check()
        checker.released.set()

        assert not _drain(
            qapp, lambda: seen, timeout=0.6
        ), "the abandoned correction arrived and would have reopened the panel"
        assert checker.calls == [
            ("I have went.", "", False)
        ], "cancelling started a request"


class TestSavingIsNotACheck:
    """A save is a finished act, not a view of something — so it obeys different rules."""

    def test_the_summary_comes_back_with_the_ticket(self, qapp):
        checker = _Checker()
        checker.save_result = type(
            "R", (), {"summary": "Saved to Omnia::Phrase Check."}
        )()
        service = LookupService(_NullClient(), checker=checker)
        seen: list[tuple] = []
        service.saved.connect(lambda *args: seen.append(args))

        service.save("I have went.", "spoken", 7)

        assert _drain(qapp, lambda: seen)
        assert seen[0][1] == "Saved to Omnia::Phrase Check."
        assert seen[0][2] == 7
        assert checker.saves == [("I have went.", "spoken")]

    def test_a_second_save_does_not_supersede_the_first(self, qapp):
        # The opposite of a check, and deliberately. Two saves in flight are two notes the user
        # asked for; dropping the first because the second started would silently lose one.
        slow = _Checker()
        slow.block = True
        service = LookupService(_NullClient(), checker=slow)
        seen: list[str] = []
        service.saved.connect(lambda phrase, _s, _t: seen.append(phrase))

        service.save("first", "", 1)
        assert slow.started.wait(5)
        slow.block = False
        service.save("second", "", 2)
        slow.released.set()

        assert _drain(qapp, lambda: len(seen) == 2), seen
        assert sorted(seen) == ["first", "second"]

    def test_a_new_selection_does_not_cancel_a_save(self, qapp):
        # `cancel_check` abandons a correction because it is about text that is no longer
        # selected. A save is already happening to the collection; abandoning it would mean the
        # user pressed Save, the note was written, and nothing ever said so.
        checker = _Checker()
        checker.block = True
        service = LookupService(_NullClient(), checker=checker)
        seen: list[str] = []
        service.saved.connect(lambda phrase, _s, _t: seen.append(phrase))

        service.save("I have went.", "", 1)
        assert checker.started.wait(5)
        service.cancel_check()
        service.lookup("something else")
        checker.released.set()

        assert _drain(qapp, lambda: seen), "the save was dropped along with the check"

    def test_a_failure_says_why_and_carries_the_ticket(self, qapp):
        checker = _Checker()
        checker.save_raises = CheckError(
            "Anki was busy — nothing was saved. Try again."
        )
        service = LookupService(_NullClient(), checker=checker)
        seen: list[tuple] = []
        service.save_failed.connect(lambda *args: seen.append(args))

        service.save("x", "", 3)

        assert _drain(qapp, lambda: seen)
        assert "nothing was saved" in seen[0][1]
        assert seen[0][2] == 3

    def test_an_unexpected_error_does_not_leak(self, qapp):
        checker = _Checker()
        checker.save_raises = ValueError("an internal detail")
        service = LookupService(_NullClient(), checker=checker)
        seen: list[tuple] = []
        service.save_failed.connect(lambda *args: seen.append(args))

        service.save("x")

        assert _drain(qapp, lambda: seen)
        assert "internal detail" not in seen[0][1]

    def test_an_empty_phrase_is_not_a_save(self, qapp):
        checker = _Checker()
        service = LookupService(_NullClient(), checker=checker)

        service.save("   ")

        assert checker.saves == []

    def test_a_build_without_a_checker_says_so(self, qapp):
        service = LookupService(_NullClient())
        seen: list[tuple] = []
        service.save_failed.connect(lambda *args: seen.append(args))

        service.save("x", "", 5)

        assert _drain(qapp, lambda: seen)
        assert seen[0][2] == 5
