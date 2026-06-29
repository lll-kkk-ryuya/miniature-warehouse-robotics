"""Golden fixtures for the Operator Feedback Box (XER-OF1)."""

from __future__ import annotations

from .decision_events import (
    GATE_REJECT_EVENTS,
    NON_SPEAKABLE_EVENTS,
    UNKNOWN_CODE_EVENTS,
)
from .golden_ja import GOLDEN_FALLBACK_JA, GOLDEN_JA

__all__ = [
    "GATE_REJECT_EVENTS",
    "GOLDEN_FALLBACK_JA",
    "GOLDEN_JA",
    "NON_SPEAKABLE_EVENTS",
    "UNKNOWN_CODE_EVENTS",
]
