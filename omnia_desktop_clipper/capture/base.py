"""The selection-capture seam (pure ABC).

Backends (clipboard now; accessibility later) implement :class:`SelectionCapture`
so the trigger/UI layer depends only on this interface.
"""

from __future__ import annotations

import abc


class SelectionCapture(abc.ABC):
    """Captures the user's current text selection from the active application."""

    @abc.abstractmethod
    def capture(self) -> str | None:
        """Return the currently selected text, or ``None`` if nothing captured."""

    @property
    def in_flight(self) -> bool:
        """Whether a capture is running right now.

        Part of the seam rather than one backend's detail, because the app READS it to drop
        gestures and hotkeys that would otherwise re-enter a capture — and anything that opens
        a modal underneath a capture's ``finally`` can overwrite the user's clipboard. A
        backend that quietly lacked this would turn all three of those guards into no-ops with
        no error anywhere.

        Defaults to False for a backend whose ``capture`` cannot be re-entered (one that never
        pumps an event loop, so nothing can arrive mid-call).
        """
        return False
