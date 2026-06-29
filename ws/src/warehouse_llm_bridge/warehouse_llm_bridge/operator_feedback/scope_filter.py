"""``ScopeFilter`` — XER-OF2.5 attribution / lifecycle / suppression (doc05 §5.3).

Decides whether a decision_event should be SPOKEN or SUPPRESSED so the box does NOT
"鳴り続ける" (doc05:14,198). v0 = reject-class only; milestones (arrived/completed) are
out of scope (doc05:376). The rule mirrors the doc05:205-208 formula:

    speak ⟺ event.gen_id ∈ {live operator commands}
          ∧ event.decision ∈ {rejected, needs_clarification, emergency_stop}
          ∧ event is a lifecycle transition (not a high-freq sample/tick)

Suppression precedence (deterministic, checked in order):
  1. non_speakable_decision — decision ∉ speakable (accepted/warning/milestone) (doc05:206)
  2. uncorrelated_autonomous — gen_id missing or not in the live set: an autonomous stop
     / unrelated reject not tied to an operator command (doc05:200,224, §5.3)
  3. duplicate_suppressed — same (run_id, gen_id, robot, box, reason_code) already spoken
     this session: collapses high-freq tick / repeated reject ("同一 reason の連投を間引く",
     doc05:100). The key MUST include ``robot`` (and ``run_id``): one commander cycle shares
     a single ``gen_id`` across bot1+bot2 (doc08:183 — "同一 gen_id の tool call が複数
     正当に発火"・"世代単位のキーは正当な2台分を誤って弾く"), so a gen-only key would wrongly
     drop the 2nd robot's distinct reject. Full correlation tuple = gen_id/run_id/robot
     (doc05:202).

Suppressed events are NOT dropped silently — they are returned with a reason so the box
can keep them for audit ("喋らないが audit には残す", doc05:227).
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import SPEAKABLE_DECISIONS, DecisionEvent

# Outcome kinds.
SPEAK = "speak"
SUPPRESS = "suppress"

# Suppression reasons (this box's own audit vocabulary — internal, not a frozen contract).
REASON_NON_SPEAKABLE = "non_speakable_decision"
REASON_UNCORRELATED = "uncorrelated_autonomous"
REASON_DUPLICATE = "duplicate_suppressed"


@dataclass(frozen=True)
class ScopeOutcome:
    """Result of :meth:`ScopeFilter.classify`. ``speak`` is the only path that talks."""

    outcome: str  # SPEAK | SUPPRESS
    reason: str  # "" for SPEAK, else one of the REASON_* constants

    @property
    def speak(self) -> bool:
        return self.outcome == SPEAK


class ScopeFilter:
    """Stateful attribution + lifecycle filter for one operator session.

    ``live_command_gen_ids`` is the attribution context = gen_ids of operator commands
    currently live (compiled by L3, dispatched by action_map with gen_id=N, doc05:202,218).
    A reject only speaks if it is correlated to one of those commands.
    """

    def __init__(self, live_command_gen_ids: set[int] | None = None) -> None:
        self._live: set[int] = set(live_command_gen_ids or set())
        # Dedup key = full correlation tuple (run_id, gen_id, robot, box, reason_code);
        # gen_id is non-None on the SPEAK path (classify guards it). doc05:202 / doc08:184.
        self._spoken: set[tuple[str, int, str, str, str]] = set()

    # -- attribution context management -------------------------------------------------
    def add_live_command(self, gen_id: int | None) -> None:
        """Register a freshly dispatched operator command (gen_id) as live; None ignored."""
        if gen_id is None:
            return
        self._live.add(int(gen_id))

    def retire_command(self, gen_id: int | None) -> None:
        """Drop a command from the live set (e.g. terminal/cancelled); None ignored."""
        if gen_id is None:
            return
        self._live.discard(int(gen_id))

    @property
    def live_command_gen_ids(self) -> frozenset[int]:
        return frozenset(self._live)

    # -- classification -----------------------------------------------------------------
    def classify(self, event: DecisionEvent) -> ScopeOutcome:
        """Return SPEAK / SUPPRESS(+reason) for a decoded decision_event.

        No side effects on SUPPRESS; on SPEAK the full correlation key
        ``(run_id, gen_id, robot, box, reason_code)`` is remembered for dedup. ``robot`` and
        ``run_id`` are part of the key because one commander cycle shares a single ``gen_id``
        across bot1+bot2 (doc08:183) — a gen-only key would wrongly drop the 2nd robot's
        distinct reject. The correlation tuple is gen_id/run_id/robot (doc05:202).
        """
        if event.decision not in SPEAKABLE_DECISIONS:
            return ScopeOutcome(SUPPRESS, REASON_NON_SPEAKABLE)

        if event.gen_id is None or event.gen_id not in self._live:
            return ScopeOutcome(SUPPRESS, REASON_UNCORRELATED)

        key = (event.run_id, event.gen_id, event.robot, event.box, event.reason_code)
        if key in self._spoken:
            return ScopeOutcome(SUPPRESS, REASON_DUPLICATE)

        self._spoken.add(key)
        return ScopeOutcome(SPEAK, "")
