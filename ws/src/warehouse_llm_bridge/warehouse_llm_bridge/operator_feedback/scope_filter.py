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
  3. duplicate_suppressed — same (gen_id, box, reason_code) already spoken this session:
     collapses high-freq tick / repeated reject ("同一 reason の連投を間引く", doc05:100)

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
        self._spoken: set[tuple[int, str, str]] = set()

    # -- attribution context management -------------------------------------------------
    def add_live_command(self, gen_id: int) -> None:
        """Register a freshly dispatched operator command (gen_id) as live."""
        self._live.add(int(gen_id))

    def retire_command(self, gen_id: int) -> None:
        """Drop a command from the live set (e.g. terminal/cancelled)."""
        self._live.discard(int(gen_id))

    @property
    def live_command_gen_ids(self) -> frozenset[int]:
        return frozenset(self._live)

    # -- classification -----------------------------------------------------------------
    def classify(self, event: DecisionEvent) -> ScopeOutcome:
        """Return SPEAK / SUPPRESS(+reason) for a decoded decision_event (no side effects
        on SUPPRESS; on SPEAK the (gen_id, box, reason_code) key is remembered for dedup).
        """
        if event.decision not in SPEAKABLE_DECISIONS:
            return ScopeOutcome(SUPPRESS, REASON_NON_SPEAKABLE)

        if event.gen_id is None or event.gen_id not in self._live:
            return ScopeOutcome(SUPPRESS, REASON_UNCORRELATED)

        key = (event.gen_id, event.box, event.reason_code)
        if key in self._spoken:
            return ScopeOutcome(SUPPRESS, REASON_DUPLICATE)

        self._spoken.add(key)
        return ScopeOutcome(SPEAK, "")
