"""Word lookup: ask omnia whether a word is already in the user's Anki collection.

``client`` is the pure transport/dataclass layer; ``service`` adds the Qt threading so a lookup
never blocks the UI. All the *decisions* (searchable note types, field triage, ranking) live in
the omnia add-on's ``word_lookup`` plugin — this package only asks and renders.
"""

from __future__ import annotations

from .client import (
    LookupCardView,
    LookupClient,
    LookupFieldView,
    LookupUnavailableError,
    LookupView,
)

__all__ = [
    "LookupCardView",
    "LookupClient",
    "LookupFieldView",
    "LookupUnavailableError",
    "LookupView",
]
