"""tool6 escalation_response + tool7 start_negotiation dispatch tests (doc15 §ツール定義).

These two gen-checked meta tools had ZERO direct dispatch coverage before this file
— they appeared only as no-forward names (``test_nav2_forward.py:117``) and as audit
fixtures (``test_wo_kpi.py``). Pinned here, all offline (no ROS, no network, no MCP
wire — doc16 §11):

* the rejection ORDER — a stale generation (B-3) is refused BEFORE the
  action/starter shape check, so a superseded cycle's escalation/negotiation never
  books anything (the ``gen_checker.check`` guard, tools.py:351 / tools.py:408);
* the shape rejects — ``action`` ∉ {reassign, cancel, retry} (doc15:177) and
  ``starter`` ∉ {bot1, bot2} (doc15:186);
* the success audits — a seeded escalation and a valid negotiation each emit an
  ``"executed"`` row, and tool7 mints a deterministic ``nego_{seq:03d}`` id;
* the already-resolved gap (doc15:337-338) — an escalation answered once must be
  re-rejected, not acted on twice (the ``escalation_id not in self._escalations``
  check at tools.py:361 previously gated on id existence alone, never resolved state).
"""

import asyncio
import json
from pathlib import Path

import pytest
from warehouse_interfaces.stores import FileGenStore, FileStateStore
from warehouse_mcp_server.audit import CommandAuditLog
from warehouse_mcp_server.gen_check import GenChecker
from warehouse_mcp_server.tools import WarehouseTools


def _tools(tmp_path: Path, *, cur_gen: int = 5) -> WarehouseTools:
    """A real WarehouseTools with a tmp-scoped gen store + audit log.

    ``escalation_response`` / ``start_negotiation`` never touch the Policy Gate or
    state store, so those default (over a tmp-scoped state) and are never invoked.
    """
    gen = FileGenStore(tmp_path / "gen_store")
    gen.set(cur_gen)
    return WarehouseTools(
        gen_checker=GenChecker(gen),
        audit=CommandAuditLog(tmp_path / "audit.jsonl"),
        state_store=FileStateStore(tmp_path / "state.json"),
    )


def _audit(tmp_path: Path) -> list[dict]:
    """Parse the audit JSON-Lines log written by ``_tools`` (empty if none)."""
    path = tmp_path / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ── tool 6: escalation_response (doc15:173-180) ──────────────────────────────


@pytest.mark.safety
@pytest.mark.unit
def test_escalation_stale_gen_rejected_before_action_check(tmp_path: Path) -> None:
    # cur_gen=5, gen_id=4 (stale) AND a bogus action: the gen_checker.check stale guard
    # (tools.py:351) must fire BEFORE the action check (tools.py:357), so the reason is
    # stale_generation — never unknown_action. A superseded cycle books nothing.
    tools = _tools(tmp_path, cur_gen=5)
    res = asyncio.run(
        tools.escalation_response(4, escalation_id="esc_1", action="not_a_real_action")
    )
    assert res["status"] == "rejected"
    assert res["reason"] == "stale_generation"
    assert res["received_gen"] == 4
    assert not any(e["result"] == "executed" for e in _audit(tmp_path))


@pytest.mark.unit
def test_escalation_unknown_action_rejected(tmp_path: Path) -> None:
    # A non-stale call with action ∉ {reassign,cancel,retry} (doc15:177) is refused
    # with the action echoed back. Action is shape-checked (tools.py:357) before the
    # id existence check, so even a seeded id would not save a bogus action.
    tools = _tools(tmp_path)
    tools._escalations["esc_1"] = {}
    res = asyncio.run(tools.escalation_response(5, escalation_id="esc_1", action="frobnicate"))
    assert res["status"] == "rejected"
    assert res["reason"] == "unknown_action"
    assert res["action"] == "frobnicate"


@pytest.mark.unit
def test_escalation_unknown_id_rejected(tmp_path: Path) -> None:
    # A valid action but an id absent from the (in-memory) registry is rejected
    # (tools.py:361). Nothing seeded → unknown_escalation_id.
    tools = _tools(tmp_path)
    res = asyncio.run(tools.escalation_response(5, escalation_id="ghost", action="reassign"))
    assert res["status"] == "rejected"
    assert res["reason"] == "unknown_escalation_id"


@pytest.mark.unit
def test_escalation_seeded_id_executes_and_audits(tmp_path: Path) -> None:
    # A seeded escalation answered with a valid action returns ok and writes one
    # "executed" audit row carrying the response shape (doc15:173-180).
    tools = _tools(tmp_path)
    tools._escalations["esc_1"] = {}
    res = asyncio.run(
        tools.escalation_response(
            5, escalation_id="esc_1", action="reassign", new_robot="bot2", reason="bot1 stuck"
        )
    )
    assert res["status"] == "ok"
    assert res["escalation_id"] == "esc_1"
    assert res["action"] == "reassign"
    assert res["new_robot"] == "bot2"
    executed = [e for e in _audit(tmp_path) if e["result"] == "executed"]
    assert len(executed) == 1
    assert executed[0]["tool"] == "escalation_response"
    assert executed[0]["robot"] == "bot2"


# ── tool 6 already-resolved gap (doc15:337-338) ──────────────────────────────


@pytest.mark.unit
def test_escalation_already_resolved_rejected_on_second_response(tmp_path: Path) -> None:
    # doc15:337-338: an escalation resolved by a prior response must be re-rejected —
    # not acted on twice. The first valid response resolves it; the second (same id,
    # same non-stale gen) is refused with already_resolved. This closes the gap where
    # the id-existence check (tools.py:361) gated on existence alone, never resolved.
    tools = _tools(tmp_path)
    tools._escalations["esc_1"] = {}
    first = asyncio.run(tools.escalation_response(5, escalation_id="esc_1", action="reassign"))
    assert first["status"] == "ok"
    second = asyncio.run(tools.escalation_response(5, escalation_id="esc_1", action="retry"))
    assert second["status"] == "rejected"
    assert second["reason"] == "already_resolved"
    assert second["escalation_id"] == "esc_1"
    # exactly one executed row (the first) and one rejected row (the second)
    audit = _audit(tmp_path)
    assert sum(e["result"] == "executed" for e in audit) == 1
    assert sum(e["result"] == "rejected" for e in audit) == 1


@pytest.mark.unit
def test_escalation_resolved_then_bad_action_keeps_action_precedence(tmp_path: Path) -> None:
    # The action shape check (tools.py:357) runs BEFORE the resolved branch
    # (tools.py:371): on a SECOND response to a resolved escalation a *valid* action is
    # refused already_resolved, but a *bogus* action is refused unknown_action — action
    # precedence wins. Pins the "a bogus action above still wins" comment so a reorder
    # regression (resolved-before-action, matching doc15:334-341's pseudocode order)
    # cannot slip through green.
    tools = _tools(tmp_path)
    tools._escalations["esc_1"] = {}
    first = asyncio.run(tools.escalation_response(5, escalation_id="esc_1", action="reassign"))
    assert first["status"] == "ok"
    resolved = asyncio.run(tools.escalation_response(5, escalation_id="esc_1", action="retry"))
    assert resolved["reason"] == "already_resolved"
    bad = asyncio.run(tools.escalation_response(5, escalation_id="esc_1", action="frobnicate"))
    assert bad["reason"] == "unknown_action"
    assert bad["action"] == "frobnicate"


# ── tool 7: start_negotiation (doc15:182-188) ────────────────────────────────


@pytest.mark.safety
@pytest.mark.unit
def test_negotiation_stale_gen_rejected_before_starter_check(tmp_path: Path) -> None:
    # cur_gen=5, gen_id=4 (stale) AND a bogus starter: the gen_checker.check stale guard
    # (tools.py:408) fires BEFORE the starter check (tools.py:414) → stale_generation,
    # never unknown_starter. No id is minted for a superseded cycle.
    tools = _tools(tmp_path, cur_gen=5)
    res = asyncio.run(
        tools.start_negotiation(4, deadlock_or_escalation_id="dl_1", starter="nobody")
    )
    assert res["status"] == "rejected"
    assert res["reason"] == "stale_generation"
    assert res["received_gen"] == 4
    assert tools._negotiation_seq == 0  # nothing minted
    assert not any(e["result"] == "executed" for e in _audit(tmp_path))


@pytest.mark.unit
def test_negotiation_unknown_starter_rejected(tmp_path: Path) -> None:
    # starter ∉ {bot1,bot2} (doc15:186) is refused with the starter echoed and no id
    # minted (the seq counter stays at 0).
    tools = _tools(tmp_path)
    res = asyncio.run(tools.start_negotiation(5, deadlock_or_escalation_id="dl_1", starter="bot9"))
    assert res["status"] == "rejected"
    assert res["reason"] == "unknown_starter"
    assert res["starter"] == "bot9"
    assert tools._negotiation_seq == 0


@pytest.mark.unit
def test_negotiation_mints_deterministic_sequential_ids_and_audits(tmp_path: Path) -> None:
    # A valid starter mints nego_{seq:03d} deterministically (001, 002, ...) and writes
    # an "executed" audit row each time (doc15:182-188). With no negotiation_starter wired
    # (the default here), the /negotiation/start publish is a no-op — id + audit only.
    tools = _tools(tmp_path)
    first = asyncio.run(
        tools.start_negotiation(
            5, deadlock_or_escalation_id="dl_1", starter="bot1", context="head-on"
        )
    )
    second = asyncio.run(
        tools.start_negotiation(5, deadlock_or_escalation_id="dl_2", starter="bot2")
    )
    assert first["status"] == "ok"
    assert first["negotiation_id"] == "nego_001"
    assert first["starter"] == "bot1"
    assert first["context"] == "head-on"
    assert second["negotiation_id"] == "nego_002"
    executed = [e for e in _audit(tmp_path) if e["result"] == "executed"]
    assert len(executed) == 2
    assert all(e["tool"] == "start_negotiation" for e in executed)


# ── tool 7: /negotiation/start publish seam (Slice 2, doc14:59,205) ───────────


def _tools_with_starter(tmp_path: Path, starter_cb, *, cur_gen: int = 5) -> WarehouseTools:
    gen = FileGenStore(tmp_path / "gen_store")
    gen.set(cur_gen)
    return WarehouseTools(
        gen_checker=GenChecker(gen),
        audit=CommandAuditLog(tmp_path / "audit.jsonl"),
        state_store=FileStateStore(tmp_path / "state.json"),
        negotiation_starter=starter_cb,
    )


@pytest.mark.unit
def test_negotiation_publishes_start_envelope_when_wired(tmp_path: Path) -> None:
    # When a negotiation_starter is injected (Slice 2), an accepted start_negotiation emits the
    # /negotiation/start envelope with the minted id + the commander gen_id (doc14:59,70).
    published: list[dict] = []
    tools = _tools_with_starter(tmp_path, published.append)
    res = asyncio.run(
        tools.start_negotiation(
            5, deadlock_or_escalation_id="dl_9", starter="bot1", context="aisle"
        )
    )
    assert res["status"] == "ok"
    assert len(published) == 1
    env = published[0]
    assert env == {
        "negotiation_id": "nego_001",
        "gen_id": 5,
        "starter": "bot1",
        "deadlock_or_escalation_id": "dl_9",
        "context": "aisle",
    }


@pytest.mark.unit
def test_negotiation_rejected_does_not_publish(tmp_path: Path) -> None:
    # A stale gen / unknown starter is refused BEFORE publishing — the character LLMs must not
    # be summoned for a rejected trigger (the /negotiation/start publish is post-acceptance).
    published: list[dict] = []
    tools = _tools_with_starter(tmp_path, published.append)
    stale = asyncio.run(tools.start_negotiation(2, deadlock_or_escalation_id="d", starter="bot1"))
    bad = asyncio.run(tools.start_negotiation(5, deadlock_or_escalation_id="d", starter="bot9"))
    assert stale["status"] == "rejected" and bad["status"] == "rejected"
    assert published == []


@pytest.mark.unit
def test_negotiation_starter_failure_is_fail_open(tmp_path: Path) -> None:
    # A publisher fault must NOT crash the tool (fail-open seam, doc14:38): the tool still
    # returns ok + audits executed so the commander cycle survives a bad ROS publish.
    def boom(_env: dict) -> None:
        raise RuntimeError("ros publish failed")

    tools = _tools_with_starter(tmp_path, boom)
    res = asyncio.run(tools.start_negotiation(5, deadlock_or_escalation_id="d", starter="bot2"))
    assert res["status"] == "ok"
    assert [e for e in _audit(tmp_path) if e["result"] == "executed"]
