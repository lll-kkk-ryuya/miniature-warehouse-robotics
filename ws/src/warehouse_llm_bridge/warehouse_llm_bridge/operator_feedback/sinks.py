"""Notice sinks — the INJECTED output IF for the box (XER-OF2 fail-open).

The box never calls a TTS provider directly; it is handed a ``NoticeSink`` (or a plain
callable) and emits the rendered text through it. This keeps the offline core provider-free
(real TTS / Hermes Voice transport is XER-OF3, DEFERRED — doc05:259,281) and lets the box
fail open: if a sink raises, the box falls back to another sink (web/overlay-equivalent)
and the run continues (doc05:109,270 L4OF-G2).

``RecordingSink`` is the in-memory stand-in used as the fallback (web/overlay) sink and in
tests — it records notices and never raises. A sink emits ONLY text (``OperatorNotice``);
it has no motion channel, preserving the box's 0-actuation guarantee (R-26).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import OperatorNotice


@runtime_checkable
class NoticeSink(Protocol):
    """Structural type for a notice sink. ``speak`` may raise; the box catches it."""

    def speak(self, notice: OperatorNotice) -> None: ...


def invoke_sink(sink: object, notice: OperatorNotice) -> None:
    """Deliver ``notice`` to ``sink``.

    Accepts either a ``NoticeSink`` (has ``.speak``) or a plain ``callable(notice)``.
    Propagates any exception the sink raises (the box's fail-open logic handles it).
    """
    speak = getattr(sink, "speak", None)
    if callable(speak):
        speak(notice)
    elif callable(sink):
        sink(notice)
    else:  # pragma: no cover - misuse guard
        raise TypeError(f"sink is neither a NoticeSink nor callable: {sink!r}")


class RecordingSink:
    """In-memory sink that records spoken notices and never raises (fail-open fallback)."""

    def __init__(self) -> None:
        self.spoken: list[OperatorNotice] = []

    def speak(self, notice: OperatorNotice) -> None:
        self.spoken.append(notice)

    @property
    def texts(self) -> list[str]:
        return [n.text for n in self.spoken]
