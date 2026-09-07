"""Word lookup: ask omnia whether a word is already in the user's Anki collection.

``client`` is the pure transport/dataclass layer and ``generate`` its authenticated, mutating
counterpart (regenerating a note's fields through Smart Notes); ``token`` finds the secret that
authorises it, ``guard`` decides which late answers are still wanted, ``regeneration`` holds
what the panel knows about a regeneration in progress, and ``service`` adds the Qt threading so
none of it ever blocks the UI. All the *decisions* (searchable note types, field triage,
ranking, what may be generated) live in the omnia add-on — this package only asks and renders.
"""

from __future__ import annotations

from .client import (
    CLIENT_NAME,
    LookupCardView,
    LookupClient,
    LookupFieldView,
    LookupUnavailableError,
    LookupView,
)
from .generate import (
    FieldGeneration,
    GenerateClient,
    GenerateError,
    GenerateOutcome,
)
from .guard import GenerationGuard
from .regeneration import ControlState, RegenerationState
from .token import resolve_token

__all__ = [
    "CLIENT_NAME",
    "ControlState",
    "FieldGeneration",
    "GenerateClient",
    "GenerateError",
    "GenerateOutcome",
    "GenerationGuard",
    "LookupCardView",
    "LookupClient",
    "LookupFieldView",
    "LookupUnavailableError",
    "LookupView",
    "RegenerationState",
    "resolve_token",
]
