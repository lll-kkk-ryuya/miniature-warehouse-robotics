"""Commander-side negotiation proposal ingestion tests (doc14:62-63,142, Slice 2).

The bridge node feeds a /negotiation/proposal into the scheduler via
``set_negotiation_proposal``; the NEXT cycle attaches it to the situation the commander sees
(``negotiation_proposal`` key) IFF its gen_id is within +/-2 of the current generation, then
consumes it (one-shot). No frozen-contract change: the proposal is added to the serialized
situation dict (extra="ignore", schemas.py:25), not to the frozen Situation model.
"""

import asyncio
from pathlib import Path

import pytest
from warehouse_interfaces.schemas import AgreedAction, CommandAction, Proposal, TranscriptLine
from warehouse_interfaces.stores import FileGenStore
from warehouse_llm_bridge.executor import RecordingToolExecutor
from warehouse_llm_bridge.scheduler import BridgeScheduler


class CapturingLLM:
    """Records each situation the scheduler posts; returns an empty command."""

    def __init__(self) -> None:
        self.situations: list[dict] = []

    async def decide(self, situation: dict) -> dict:
        self.situations.append(dict(situation))
        return {"reasoning": "ok", "commands": []}


class StubSituation:
    """Minimal situation builder: a canned non-empty dict every cycle."""

    def build(self, *, turn, gen_id, history=None, pending_tasks=None, current_tasks=None) -> dict:
        return {"turn": turn, "gen_id": gen_id, "robots": {}}


def _proposal(*, gen_id: int, negotiation_id: str = "nego_001") -> Proposal:
    return Proposal(
        negotiation_id=negotiation_id,
        gen_id=gen_id,
        agreed_action=AgreedAction(action=CommandAction.YIELD, by="bot1", to="退避地点B"),
        transcript=[TranscriptLine(speaker="bot1", text="退避します")],
        reached_at=1717000000.0,
    )


def _scheduler(tmp_path: Path, llm: CapturingLLM) -> BridgeScheduler:
    return BridgeScheduler(
        llm_client=llm,
        situation_builder=StubSituation(),
        executor=RecordingToolExecutor(),
        gen_store=FileGenStore(tmp_path / "gen_store"),
    )


@pytest.mark.unit
def test_proposal_within_window_injected_then_consumed(tmp_path: Path) -> None:
    llm = CapturingLLM()
    sched = _scheduler(tmp_path, llm)
    sched.set_negotiation_proposal(_proposal(gen_id=1))  # cycle 1 runs at gen=1 -> drift 0

    asyncio.run(sched.run_cycle())
    injected = llm.situations[0].get("negotiation_proposal")
    assert injected is not None
    assert injected["negotiation_id"] == "nego_001"
    assert injected["agreed_action"]["action"] == "yield"  # StrEnum -> wire-safe str

    # one-shot: the next cycle (no new proposal) no longer carries it
    asyncio.run(sched.run_cycle())
    assert "negotiation_proposal" not in llm.situations[1]


@pytest.mark.unit
def test_proposal_gen_drift_discarded_and_cleared(tmp_path: Path) -> None:
    llm = CapturingLLM()
    sched = _scheduler(tmp_path, llm)
    sched.set_negotiation_proposal(_proposal(gen_id=100))  # |1 - 100| > 2 -> discard (doc14:142)

    asyncio.run(sched.run_cycle())  # gen=1
    assert "negotiation_proposal" not in llm.situations[0]

    # discarded proposals are also consumed; a fresh in-window proposal still attaches
    sched.set_negotiation_proposal(_proposal(gen_id=2))  # |2 - 2| = 0 at gen=2
    asyncio.run(sched.run_cycle())  # gen=2
    assert llm.situations[1].get("negotiation_proposal") is not None


@pytest.mark.unit
def test_no_proposal_leaves_situation_untouched(tmp_path: Path) -> None:
    llm = CapturingLLM()
    sched = _scheduler(tmp_path, llm)
    asyncio.run(sched.run_cycle())
    assert "negotiation_proposal" not in llm.situations[0]
