"""What to do with a capture before it reaches Anki — the decision, without the Qt.

The choice between "add it straight away" and "ask first" used to live inside the tray app,
tangled with the dialog it opens. That made it the one part of the auto-add feature no test
could reach: `app.py` imports PyQt6, the suite deliberately does not, and so the rule that
actually decides whether a user sees a popup went unguarded while the config default that
feeds it had three tests.

Splitting it out is the house rule ("pure logic separated from Qt glue"), applied to the
place that was breaking it.
"""

from __future__ import annotations

from enum import Enum


class CaptureAction(Enum):
    """The three outcomes a fresh capture can have.

    :meth:`decide` is the whole rule; the tray app does nothing but obey it.
    """

    #: Nothing worth sending — discard without telling Anki or the user.
    DROP = "drop"
    #: Send it now, no dialog.
    ADD = "add"
    #: Show the confirm popup and let the user edit or cancel.
    CONFIRM = "confirm"

    @classmethod
    def decide(cls, *, auto_add: bool, word: str, context: str) -> CaptureAction:
        """Return what should happen to a capture of ``word`` / ``context``.

        With ``auto_add`` off, EVERY capture confirms — including an empty one. That is not
        an oversight: the popup is where a user sees what was grabbed, and one that grabbed
        nothing is exactly the case worth showing rather than silently dropping. Cancel is
        right there.

        With ``auto_add`` on there is no Cancel to catch anything, so an empty capture is
        dropped here instead. Adding a blank note is worse than adding nothing, and the user
        asked not to be interrupted — a popup that only appears on failure would be the
        interruption they switched off.

        Whitespace-only counts as empty. A capture is stripped before it arrives, but the
        OCR path can hand over a block that strips to nothing, and `" "` is not a note.

        Args:
            auto_add: The user's ``auto_add`` setting.
            word: The captured term.
            context: The sentence or block around it.

        Returns:
            The action the caller must take.
        """
        if not auto_add:
            return cls.CONFIRM
        if not word.strip() and not context.strip():
            return cls.DROP
        return cls.ADD
