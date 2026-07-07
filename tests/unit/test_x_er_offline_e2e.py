"""Mode X-ER offline e2e — the XER6 backbone against the frozen lane interface (doc08 §8 ①).

Drives the full offline chain the ``x_er_bridge`` node runs per cycle
(docs/mode-x-er/08-x-er-bridge-node-spec.md §4-§6):

    build_x_er_runtime(cfg)                        # composition startup, fail-closed (§4)
      -> GeminiErAdapter(offline_payload=...)      # fixture replay, NOT via the factory (§8 ①)
      -> run_x_er_cycle(...)                       # ER -> plugin validate -> L3 -> gen -> dispatch (§5)
      -> WarehouseTools(nav2_forwarder=None)       # validate + book-keep only, 0 actuation (§5 step6)

and proves the red -> blue ordered demo (doc08 §5 step7: t1 completes via the caller loop, the
next cycle readies t2) plus the R-26 plugin-reject invariant (§5 step3: 0 dispatch, store
untouched).

IMPORT NOTE (Integrate phase): this module imports the lane-A/B modules
``warehouse_llm_bridge.x_er_composition`` / ``warehouse_llm_bridge.x_er_cycle`` at module level.
Until those lanes land, collecting THIS FILE fails with a clear ImportError — deliberately NOT a
skip, so the missing backbone can never silently pass CI. The fixture self-checks re-imported at
the bottom run standalone pre-Integrate via ``pytest tests/unit/x_er_fixtures.py``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_interfaces.schemas import Command, CommandAction
from warehouse_interfaces.stores import FileGenStore, FileIdempotencyStore, FileStateStore
from warehouse_llm_bridge.executor import DispatchToolExecutor
from warehouse_llm_bridge.robotics.adapters.enums import Transport
from warehouse_llm_bridge.robotics.adapters.gemini_er import GeminiErAdapter
from warehouse_llm_bridge.robotics.composition.plugin_results import StructuredPluginRuleResult
from warehouse_llm_bridge.robotics.composition.plugins import hookimpl
from warehouse_llm_bridge.robotics.er_task import ErTaskRequest
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import (
    INNER_PLAN,
    direct_envelope,
)
from warehouse_llm_bridge.robotics_planning_core.task_graph_executor import (
    TaskGraphExecutor,
    TaskStatus,
)
from warehouse_llm_bridge.robotics_planning_core.validator import PlanningContext
from warehouse_llm_bridge.robotics_planning_core.validator.report import DispatchEffect
from warehouse_llm_bridge.robotics_planning_core.validator.seams import InMemoryTaskGraphStore

# XER6 lane-A/B modules (the FROZEN inter-lane interface, doc08 §4-§5). A clear ImportError
# here until they land is intentional — do not convert to pytest.importorskip.
from warehouse_llm_bridge.x_er_composition import build_x_er_runtime
from warehouse_llm_bridge.x_er_cycle import run_x_er_cycle
from warehouse_mcp_server.audit import CommandAuditLog
from warehouse_mcp_server.gen_check import GenChecker
from warehouse_mcp_server.policy_gate import PolicyGate
from warehouse_mcp_server.tools import WarehouseTools

# DO NOT TRIM this re-import block: x_er_fixtures.py does not match pytest's python_files
# pattern, so its 12 self-check tests (including the R-26 pin on the checked-in
# config/dev/calibration/dev-sim-v1.yaml artifact) are collected ONLY through these imports —
# removing a test_* name here silently drops that test from CI with zero failing signal.
# Follow-up (flagged residual): move the self-checks into a collected test_x_er_fixtures.py.
from tests.unit.x_er_fixtures import (  # noqa: F401 — test_* names collected into this suite
    CALIBRATION_ID,
    X_ER_PLUGIN_ID,
    X_ER_PLUGIN_REASON_CODE,
    X_ER_RUN_ID,
    test_cfg_locations_match_base_yaml,
    test_cfg_mode_x_er_block_matches_doc08_frozen_keys,
    test_checked_in_dev_calibration_artifact_matches_fixture_geometry,
    test_geometry_red_blue_snap_via_dev_calibration,
    test_governed_calibration_resolves_for_fixture_bundle,
    test_site_profile_bundle_verifies_against_approved_record,
    test_tampered_bundle_fails_closed,
    test_uncertified_bundle_calibration_is_rejected_fail_closed,
    test_write_x_er_cfg_tree_materializes_loadable_artifacts,
    test_x_er_plugin_manifest_reconciles_with_run_manifest,
    test_x_er_run_manifest_fixture_is_valid_run_manifest_v1,
    test_x_er_run_manifest_plugin_less_variant_is_valid,
    write_x_er_cfg_tree,
)

PLAN_ID: str = INNER_PLAN["plan_id"]  # "plan_demo_red_blue" (red_blue_sequence.py:25)

# The accepted dispatch_task payload when NO forwarder is wired — validate + book-keep only
# (tools.py:217-227; forwarding fields structurally absent = the 0-actuation witness).
_DISPATCH_OK_KEYS = frozenset(
    {"status", "task_id", "robot", "action", "dropoff", "via", "priority", "duration"}
)


class BenignZonePlugin:
    """Manifest-declared hookimpl with no findings (happy-path composition witness)."""

    @hookimpl
    def validate_plan(
        self, plan: Mapping[str, Any], context: PlanningContext
    ) -> list[StructuredPluginRuleResult]:
        return []


class RejectingZonePlugin:
    """Manifest-declared hookimpl that BLOCKs every plan under its declared reason code."""

    @hookimpl
    def validate_plan(
        self, plan: Mapping[str, Any], context: PlanningContext
    ) -> list[StructuredPluginRuleResult]:
        return [
            StructuredPluginRuleResult.from_parts(
                plugin_id=X_ER_PLUGIN_ID,
                reason_code=X_ER_PLUGIN_REASON_CODE,
                message_for_operator="e2e fixture: target outside the allowed zone",
                dispatch_effect=DispatchEffect.BLOCK,
            )
        ]


def _tools(tmp_path: Path, gen_store: FileGenStore) -> WarehouseTools:
    """Real WarehouseTools on tmp file stores, SHARING the cycle's gen store (llm_bridge.py:152-166
    wiring shape) and with ``nav2_forwarder=None`` (offline layer ①, doc08 §5 step6 / §8)."""
    state = FileStateStore(tmp_path / "state.json")
    state.write(
        {
            "timestamp": datetime.now().isoformat(),
            "robots": {"bot1": {"battery": 90}, "bot2": {"battery": 90}},
        }
    )
    return WarehouseTools(
        gen_checker=GenChecker(gen_store, FileIdempotencyStore(tmp_path / "idempotency_store")),
        policy_gate=PolicyGate(state),
        audit=CommandAuditLog(tmp_path / "audit.jsonl"),
        state_store=state,
        nav2_forwarder=None,
    )


def _request() -> ErTaskRequest:
    return ErTaskRequest(
        request_id="req-x-er-offline-e2e",
        transcript=INNER_PLAN["transcript"],
        calibration_id=CALIBRATION_ID,
        known_robots=["bot1", "bot2"],
        known_locations=sorted(KNOWN_LOCATIONS),
    )


def _cycle(runtime, adapter, executor, gen_store, tool_executor):
    return asyncio.run(
        run_x_er_cycle(
            request=_request(),
            adapter=adapter,
            runtime=runtime,
            executor=executor,
            gen_store=gen_store,
            tool_executor=tool_executor,
        )
    )


def _complete(executor: TaskGraphExecutor, task_id: str) -> None:
    """Caller-loop progression between cycles (doc08 §5 step7 — the node owns
    mark_running -> mark_succeeded; tolerate a cycle that already committed RUNNING)."""
    state = executor.load_state(PLAN_ID)
    if state.runtime.status_of(task_id) is not TaskStatus.RUNNING:
        executor.mark_running(PLAN_ID, task_id, state)
    executor.mark_succeeded(PLAN_ID, task_id, state)


@pytest.mark.safety
def test_offline_e2e_red_then_blue_ordered_zero_actuation(tmp_path: Path) -> None:
    """The XER6 acceptance demo, offline: red (t1) dispatches in cycle 1, blue (t2) only after
    t1 completes (doc02:171-173 ``after`` gate held by the long-lived executor), destinations
    stay inside the frozen vocabulary, gen increments across cycles, and the run writes its
    effective-composition witness (doc08 §4 step7)."""
    cfg = write_x_er_cfg_tree(tmp_path)
    out_root = tmp_path / "out_runs"
    runtime = build_x_er_runtime(
        cfg,
        plugin_factories={X_ER_PLUGIN_ID: BenignZonePlugin},
        write_artifacts=True,
        out_root=out_root,
    )
    adapter = GeminiErAdapter(transport=Transport.DIRECT, offline_payload=direct_envelope())
    store = InMemoryTaskGraphStore()
    executor = TaskGraphExecutor(store)  # long-lived across cycles (doc08 §5 step4)
    gen_store = FileGenStore(tmp_path / "gen_store")
    tools = _tools(tmp_path, gen_store)
    tool_executor = DispatchToolExecutor(tools.dispatch)

    # cycle 1: only t1 (bot1 -> red_box -> shelf_1) is ready; t2 is `after t1.completed`.
    outcome1 = _cycle(runtime, adapter, executor, gen_store, tool_executor)
    assert outcome1.skipped_reason is None
    assert isinstance(outcome1.command, Command)
    assert [(i.bot, i.action, i.destination) for i in outcome1.command.commands] == [
        ("bot1", CommandAction.NAVIGATE, "shelf_1")
    ]
    assert outcome1.plugin_report is not None
    assert outcome1.plugin_report.permits_dispatch is True
    assert len(outcome1.dispatched) == 1
    gen_after_cycle1 = gen_store.get()
    assert gen_after_cycle1 >= 1  # the node minted the generation (doc08 §5 step5, B-3)

    # caller-loop progression (doc08 §5 step7): t1 succeeds -> t2 becomes ready next cycle.
    _complete(executor, "t1")

    # cycle 2: same fixture envelope replay; the shared executor state orders red -> blue.
    outcome2 = _cycle(runtime, adapter, executor, gen_store, tool_executor)
    assert outcome2.skipped_reason is None
    assert [(i.bot, i.action, i.destination) for i in outcome2.command.commands] == [
        ("bot2", CommandAction.NAVIGATE, "shelf_2")
    ]
    assert len(outcome2.dispatched) == 1
    assert gen_store.get() > gen_after_cycle1  # gen increments across cycles (01:184-197)

    # ordered dispatch red -> blue; every destination in the frozen KNOWN_LOCATIONS.
    dispatched = [*outcome1.dispatched, *outcome2.dispatched]
    assert [(r["robot"], r["dropoff"]) for r in dispatched] == [
        ("bot1", "shelf_1"),
        ("bot2", "shelf_2"),
    ]
    for result in dispatched:
        assert result["status"] == "ok"
        # forwarder=None: accepted call = the validate-only bookkeeping payload, nothing more
        # (tools.py:217-227) — the structural 0-actuation witness for the offline layer.
        assert set(result) == _DISPATCH_OK_KEYS
        assert result["dropoff"] in KNOWN_LOCATIONS

    # effective-composition witness under the injected out_root (doc08 §4 step7, record.py:242).
    assert runtime.out_dir is not None
    assert out_root in runtime.out_dir.parents
    assert (runtime.out_dir / "manifest.yaml").is_file()
    effective_path = runtime.out_dir / "effective_composition.json"
    assert effective_path.is_file()
    effective = json.loads(effective_path.read_text(encoding="utf-8"))
    assert effective["schema_version"] == "effective_composition.v1"
    assert effective["run_id"] == X_ER_RUN_ID
    assert X_ER_PLUGIN_ID in effective["preflight"]["registered_plugin_ids"]


@pytest.mark.safety
def test_plugin_reject_zero_dispatch_and_store_untouched(tmp_path: Path) -> None:
    """R-26 e2e (doc08 §5 step3 / §6): a manifest-declared plugin BLOCK on an otherwise-valid
    plan => that cycle dispatches NOTHING and the task-graph store is never touched (no load,
    no persist) — a rejected plan can neither read nor dirty cross-cycle state."""
    cfg = write_x_er_cfg_tree(tmp_path)
    runtime = build_x_er_runtime(
        cfg,
        plugin_factories={X_ER_PLUGIN_ID: RejectingZonePlugin},
        write_artifacts=False,
    )
    assert runtime.out_dir is None  # write_artifacts=False => no run directory is created
    adapter = GeminiErAdapter(transport=Transport.DIRECT, offline_payload=direct_envelope())
    store = InMemoryTaskGraphStore()
    executor = TaskGraphExecutor(store)
    gen_store = FileGenStore(tmp_path / "gen_store")
    tools = _tools(tmp_path, gen_store)
    tool_executor = DispatchToolExecutor(tools.dispatch)

    outcome = _cycle(runtime, adapter, executor, gen_store, tool_executor)

    assert outcome.skipped_reason == "plugin_rejected"
    assert outcome.dispatched == ()
    assert outcome.command.commands == []
    assert outcome.plugin_report is not None
    assert outcome.plugin_report.permits_dispatch is False
    assert [f.reason_code for f in outcome.plugin_report.plugin_errors] == [X_ER_PLUGIN_REASON_CODE]
    # Store untouched — independent oracle at the seam itself: a plan that was never persisted
    # reads back as None (validator/seams.py InMemoryTaskGraphStore), so the guard's "store
    # 無接触" claim is falsifiable, not tautological.
    assert store.get(PLAN_ID) is None
