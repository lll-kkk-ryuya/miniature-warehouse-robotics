"""Notice sinks — the INJECTED output IF for the box (XER-OF2 fail-open).

The box never calls a TTS provider directly; it is handed a ``NoticeSink`` (or a plain
callable) and emits the rendered text through it. This keeps the offline core provider-free
(real TTS / Hermes Voice transport is XER-OF3, DEFERRED — doc05:259,281) and lets the box
fail open: if a sink raises, the box falls back to another sink (web/overlay-equivalent)
and the run continues (doc05:109,270 L4OF-G2).

``RecordingSink`` is the in-memory stand-in used as the fallback (web/overlay) sink and in
tests — it records notices and never raises. ``LoggingNoticeSink`` is the runtime stand-in
"speaker" used by the subscriber node until the real TTS sink lands (XER-OF3). A sink emits
ONLY text (``OperatorNotice``); it has no motion channel, preserving the box's 0-actuation
guarantee (R-26).
"""

from __future__ import annotations

from collections.abc import Callable
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


class LoggingNoticeSink:
    """Sink that writes the rendered notice through an INJECTED log callable.

    The runtime stand-in "speaker" for the subscriber node (``notice_node.py``) until the real
    TTS sink is wired (XER-OF3, doc05:259,281): it emits ONLY the deterministic ``text`` and
    the derived ``severity`` through the injected callable (a ROS logger's ``.info`` at
    runtime, a list ``append`` in tests). No audio provider, no motion channel — 0 actuation
    (R-26 / L4OF-G1, doc05:269). The logger is injected (never imported) so this stays
    host-testable without ROS, matching the injection discipline of this module.
    """

    def __init__(self, log: Callable[[str], None]) -> None:
        self._log = log

    def speak(self, notice: OperatorNotice) -> None:
        self._log(f"[operator_notice] {notice.severity}: {notice.text}")


class RecordingSink:
    """In-memory sink that records spoken notices and never raises (fail-open fallback)."""

    def __init__(self) -> None:
        self.spoken: list[OperatorNotice] = []

    def speak(self, notice: OperatorNotice) -> None:
        self.spoken.append(notice)

    @property
    def texts(self) -> list[str]:
        return [n.text for n in self.spoken]
