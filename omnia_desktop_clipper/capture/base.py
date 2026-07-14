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
