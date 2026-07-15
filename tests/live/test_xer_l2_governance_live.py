"""LIVE ER → L3 → L2 Governance dispatch (0 actuation) — opt-in, PAID.

Extends the ER→L3 forerunner (``test_xer_full_chain_live.py``) by one leg: it takes the frozen
``Command`` from the live ER full chain and runs it through **L2 Governance** — the ``action_map``
seam (Bridge mints ``gen_id`` / ``idempotency_key``) → the MCP ``dispatch`` tool → Policy Gate →
the **accepted-motion gate** — proving that with ``nav2_forwarder=None`` an ACCEPTED live dispatch is
validated and book-kept (audit ledger) but **actuates NOTHING** (X-lite / Mode C boundary, R-26).

Gated + operator-run: module-skips unless ``WAREHOUSE_LIVE_ER=1`` and a Gemini key is in env
(``GEMINI_API_KEY`` / ``GOOGLE_API_KEY``). The live ER call BILLS the provider, so the OPERATOR runs
it via ``deploy/dev/run-live-er-chain.sh`` (``docs/dev/07-mode-x-er-live-e2e-runbook.md`` §3/§4.5) —
an agent never sets ``WAREHOUSE_LIVE_ER=1``. Offline (no gate) the module skips cleanly (no ER call).

Asserts INVARIANTS, not acceptance — two live realities (``spike/xer6-live-matrix/REPORT.md``:82-90):
  * a text-only live ER invents pixels that may not snap to a known location → an EMPTY ``Command``
    is a valid fail-closed outcome (0 dispatch = trivially 0 actuation);
  * Policy Gate ``UNAVAILABLE_AFTER_S=2.0`` (``policy_gate.py``:50) < live ER latency (4-6 s), so the
    state snapshot is written AFTER the ER call (see ``_tools_without_forwarder``).

The 0-actuation wiring (``forwarder=None`` + sentinel + audit) is lifted from the offline contract
``tests/unit/test_modec_noactuation.py``:54-128. Complements — does NOT duplicate — the
``spike/xer6-live-matrix`` harness (which owns the Hermes-gateway transport, full ``run_x_er_cycle``
backbone, goal_result/cycle2 replay and timing matrix); this is a clean, CI-collectable ``tests/live``
tripwire for the ``compile_raw_output → action_map → tools.dispatch`` L2 seam via ``direct`` transport.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime

import pytest

if os.getenv("WAREHOUSE_LIVE_ER") != "1":
    pytest.skip(
        "set WAREHOUSE_LIVE_ER=1 + GEMINI_API_KEY for the live ER->L3->L2 dispatch test "
        "(PAID provider call); see docs/dev/07-mode-x-er-live-e2e-runbook.md",
        allow_module_level=True,
    )

from warehouse_interfaces.locations import KNOWN_LOCATIONS  # noqa: E402
from warehouse_interfaces.schemas import Command, CommandAction  # noqa: E402
from warehouse_interfaces.stores import (  # noqa: E402
    FileGenStore,
    FileIdempotencyStore,
    FileStateStore,
)
from warehouse_llm_bridge.action_map import command_to_tool_calls  # noqa: E402
from warehouse_llm_bridge.executor import DispatchToolExecutor  # noqa: E402
from warehouse_llm_bridge.robotics_planning_core import RawModelOutput  # noqa: E402
from warehouse_llm_bridge.robotics_planning_core.pipeline import compile_raw_output  # noqa: E402
from warehouse_llm_bridge.robotics_planning_core.validator.seams import Calibration  # noqa: E402
from warehouse_llm_bridge.robotics_planning_core.visual_resolver import VisualPolicy  # noqa: E402
from warehouse_mcp_server.audit import CommandAuditLog  # noqa: E402
from warehouse_mcp_server.gen_check import GenChecker  # noqa: E402
from warehouse_mcp_server.policy_gate import PolicyGate  # noqa: E402
from warehouse_mcp_server.tools import WarehouseTools  # noqa: E402

from tests.live._er_live_client import DEFAULT_MODEL, api_key, call_er_direct  # noqa: E402

# --- injected fixtures (bridge-local; lifted verbatim from test_xer_full_chain_live.py:59-88) -----
LOCATION_COORDS: dict[str, tuple[float, float]] = {
    "shelf_1": (0.2, 0.3),
    "shelf_2": (0.7, 0.3),
    "shelf_3": (1.2, 0.3),
}
_A = 0.5 / 390.0
_C = 0.2 - 420 * _A
_E = (0.30 - 0.28) / (310 - 280)
_F = 0.30 - 310 * _E
HOMOGRAPHY = [[_A, 0.0, _C], [0.0, _E, _F], [0.0, 0.0, 1.0]]
VALID_POLYGON = [[-0.5, -0.5], [2.0, -0.5], [2.0, 1.5], [-0.5, 1.5]]

GEN = 1  # the Bridge role mints/holds gen_id; L3 / the LLM never does (action_map.py:5-9)


def _calibration() -> Calibration:
    return Calibration(
        camera_id="cam0",
        map_frame="map",
        homography=HOMOGRAPHY,
        reprojection_error=1.0,
        valid_polygon=VALID_POLYGON,
    )


def _policy() -> VisualPolicy:
    return VisualPolicy(location_coords=LOCATION_COORDS, snap_radius_m=0.25)


def _tools_without_forwarder(tmp_path, gen: int, *, battery: int = 90) -> WarehouseTools:
    """Real ``WarehouseTools`` wired as X-lite / Mode C wires them: ``nav2_forwarder=None``.

    Verbatim shape of ``tests/unit/test_modec_noactuation.py``:54-76. The state snapshot is written
    HERE, so this MUST be constructed AFTER the live ER call: Policy Gate's ``UNAVAILABLE_AFTER_S=2.0``
    (``policy_gate.py``:50) is shorter than live ER latency (4-6 s), so a snapshot written before the
    call would be stale at dispatch → ``robot_unavailable`` (``spike/xer6-live-matrix/REPORT.md``:86-90).
    """
    gen_store = FileGenStore(tmp_path / "gen_store")
    gen_store.set(gen)
    state = FileStateStore(tmp_path / "state.json")
    state.write(
        {
            "timestamp": datetime.now().isoformat(),
            "robots": {"bot1": {"battery": battery}, "bot2": {"battery": battery}},
        }
    )
    return WarehouseTools(
        gen_checker=GenChecker(gen_store, FileIdempotencyStore(tmp_path / "idempotency_store")),
        policy_gate=PolicyGate(state),
        audit=CommandAuditLog(tmp_path / "audit.jsonl"),
        state_store=state,
        nav2_forwarder=None,  # X-lite / Mode C: no direct Nav2 forwarder → 0 actuation
    )


def _forbid_forward_seam(monkeypatch) -> None:
    """Make ``plan_nav2_request`` fail loudly if the accepted-motion forward seam is ever entered.

    ``_maybe_forward`` reaches ``plan_nav2_request`` only AFTER the ``forwarder is None`` gate
    (``tools.py``:173); with ``forwarder=None`` that gate must short-circuit, so this sentinel must
    never fire — if it does, an accepted live dispatch tried to actuate a robot (the R-26 regression).
    """

    def _sentinel(*_args, **_kwargs):
        raise AssertionError(
            "plan_nav2_request was called with forwarder=None: the accepted-motion gate "
            "(tools.py:173) failed — an accepted live dispatch tried to actuate a robot."
        )

    monkeypatch.setattr("warehouse_mcp_server.tools.plan_nav2_request", _sentinel)


def test_live_er_l3_l2_dispatch_actuates_nothing(tmp_path, monkeypatch, capsys):
    """live ER → compile_raw_output → Command → L2 Governance → book-kept, 0 actuation (invariant)."""
    if not api_key():
        pytest.skip("GEMINI_API_KEY / GOOGLE_API_KEY not set")

    # 1) REAL direct ER call → L3 chain → frozen Command (same forerunner as the full-chain test).
    response = call_er_direct()
    raw = RawModelOutput(
        transport="direct", provider="er", source_model=DEFAULT_MODEL, payload=response
    )
    cmd = compile_raw_output(raw, calibration=_calibration(), resolver_policy=_policy())
    assert isinstance(cmd, Command)
    for item in cmd.commands:  # 0-dispatch invariant: dispatchable items target a frozen location
        assert item.destination in KNOWN_LOCATIONS
        assert item.action == CommandAction.NAVIGATE

    # 2) L2 Governance — build tools AFTER the ER call so the state snapshot is fresh (< 2 s).
    tools = _tools_without_forwarder(tmp_path, GEN)
    _forbid_forward_seam(monkeypatch)
    executor = DispatchToolExecutor(tools.dispatch)

    # 2a) the LIVE-ER-derived plan through L2. Text-only live ER is fail-closed (empty Command is the
    #     norm — spike/xer6-live-matrix/REPORT.md:82-85), so this may be a 0-dispatch; that still
    #     proves the ER→L3→L2 seam runs live and holds the 0-actuation invariant.
    live_results = [
        asyncio.run(executor.execute(tc)) for tc in command_to_tool_calls(cmd, gen_id=GEN)
    ]

    # 2b) WITNESS the L2 dispatch path deterministically through the SAME live-wired tools: a canned
    #     navigate to a frozen KNOWN_LOCATION must be ACCEPTED (gen ok + healthy battery + known dest)
    #     yet actuate NOTHING (forwarder=None). This exercises the L2 leg even when the live ER plan is
    #     empty. berth_A is proven navigable (test_modec_noactuation.py:123).
    canned = Command.model_validate(
        {
            "reasoning": "e2e-witness",
            "commands": [{"bot": "bot1", "action": "navigate", "destination": "berth_A"}],
        }
    )
    [witness_call] = command_to_tool_calls(canned, gen_id=GEN)
    witness = asyncio.run(executor.execute(witness_call))
    assert (
        witness["status"] == "ok"
    )  # accepted by L2 governance (Policy Gate + accepted-motion gate)
    assert witness.get("robot") == "bot1"

    # 3) INVARIANT — zero actuation for every accepted dispatch (live plan + witness, R-26 boundary).
    #    The sentinel never raising proves the forward seam (tools.py:173) was never entered.
    assert (
        tools._nav2_forwarder is None
    )  # X-lite / Mode C precondition (test_modec_noactuation.py:128)
    for result in [*live_results, witness]:  # an accepted dispatch is book-kept, never actuated
        if result.get("status") == "ok":
            assert result.get("robot") in {"bot1", "bot2"}

    # 4) BOOK-KEPT — the audit ledger records each dispatch, tagged with the issuing gen. The witness
    #    guarantees >= 1 accepted record even when the live ER plan is empty.
    audit_lines = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert audit_lines, "the witness dispatch must be book-kept in the audit log"
    for rec in audit_lines:
        assert rec["result"] in {"executed", "rejected", "error"}  # audit.py:38
        assert rec.get("detail", {}).get("gen_id") == GEN  # gen_id merged into detail (audit.py:48)

    # Summary only (no secrets / no key); run with -s to see it.
    with capsys.disabled():
        accepted = sum(1 for r in [*live_results, witness] if r.get("status") == "ok")
        print(
            f"\n[live ER->L3->L2] live_items={len(cmd.commands)} "
            f"live_tool_calls={len(live_results)} witness=ok total_accepted={accepted} "
            f"actuations=0 (forwarder=None) audit_records={len(audit_lines)}"
        )
