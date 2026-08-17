"""G5 committed-artifact CI oracle — the deploy/dev/xer6/ replay kit drives red->blue for free.

docs/dev/08-xer6-live-sim-x-lite-runbook.md 追補 2 commits the G5 execution artifacts
(``deploy/dev/xer6/``: ErTaskRequest fixture, recorded ER envelope, plugin-less run manifest,
APPROVED site-profile bundle, dev overlay example) and promises: "CI 側の裏取り:
``tests/unit/test_xer6_g5_replay_artifacts.py`` が commit 済 artifacts そのもので node cycle 相当
（red→blue・goal_result 駆動）を ``WAREHOUSE_LIVE_ER`` 無しで完走することを固定する".

This suite IS that oracle. It builds the runtime the way the RUNNING node does —
``build_x_er_runtime(cfg, plugin_factories=production_plugin_factories())`` (the real, empty
production registry) + ``build_er_adapter(cfg)`` (the real factory, replay branch via
``mode_x_er.er_offline_payload``, doc mode-x-er/08 §3 G5 freeze) + ``load_request_fixture`` on
the committed request — from the COMMITTED overlay example (container ``/ws`` paths rewritten to
the repo root), then drives the autonomous red->blue progression with a ``goal_result`` payload
(test_x_er_autonomous_e2e pattern). If any committed artifact rots (hash-tampered bundle,
malformed manifest, drifted envelope), this goes red.

Free by construction: no env key is read (``env={}`` where injectable), ``WAREHOUSE_LIVE_ER`` is
explicitly absent, and the replay adapter carries no sender (provider call structurally
impossible — test_er_adapter_factory.py pins that).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_interfaces.schemas import CommandAction
from warehouse_interfaces.stores import FileGenStore
from warehouse_llm_bridge.executor import DispatchToolExecutor
from warehouse_llm_bridge.robotics.adapter_factory import (
    build_er_adapter,
    resolve_er_offline_payload_path,
)
from warehouse_llm_bridge.robotics.composition.factory_registry import (
    production_plugin_factories,
)
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import (
    INNER_PLAN,
    direct_envelope,
)
from warehouse_llm_bridge.robotics_planning_core.task_graph_executor import TaskGraphExecutor
from warehouse_llm_bridge.robotics_planning_core.validator.seams import InMemoryTaskGraphStore
from warehouse_llm_bridge.x_er_bridge import (
    load_request_fixture,
    resolve_nav2_forwarder,
    resolve_request_fixture_path,
)
from warehouse_llm_bridge.x_er_completion import apply_goal_result, fold_inflight, parse_goal_result
from warehouse_llm_bridge.x_er_composition import build_x_er_runtime
from warehouse_llm_bridge.x_er_cycle import run_x_er_cycle

from tests.unit.test_x_er_autonomous_e2e import _goal_result_json, _tools  # shared shape

_REPO_ROOT = Path(__file__).resolve().parents[2]
_XER6_DIR = _REPO_ROOT / "deploy" / "dev" / "xer6"
_OVERLAY_EXAMPLE = _XER6_DIR / "warehouse.dev-overlay.example.yaml"

PLAN_ID: str = INNER_PLAN["plan_id"]


def _committed_cfg() -> dict[str, Any]:
    """The cfg a G5 operator run sees: the COMMITTED overlay example (``/ws`` -> repo root)
    deep-merged over the base ``locations`` block (config/warehouse.base.yaml, bringup-owned)."""
    overlay = yaml.safe_load(_OVERLAY_EXAMPLE.read_text(encoding="utf-8"))
    mode_x_er = overlay["mode_x_er"]

    def _localize(value: Any) -> Any:
        if isinstance(value, str) and value.startswith("/ws/"):
            return str(_REPO_ROOT / value.removeprefix("/ws/"))
        if isinstance(value, dict):
            return {key: _localize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_localize(item) for item in value]
        return value

    base = yaml.safe_load((_REPO_ROOT / "config" / "warehouse.base.yaml").read_text("utf-8"))
    return {"locations": base["locations"], "mode_x_er": _localize(mode_x_er)}


class _Fixture:
    """The exact object set XErBridge composes, built from the committed artifacts only."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WAREHOUSE_LIVE_ER", raising=False)  # free by construction
        cfg = _committed_cfg()
        # §4 startup exactly as the node: real (empty) production registry + committed
        # plugin-less manifest + committed APPROVED bundle (fail-closed gates all run).
        self.runtime = build_x_er_runtime(
            cfg, plugin_factories=production_plugin_factories(), write_artifacts=False
        )
        # §4 step8 via the REAL factory: the committed overlay names er_offline_payload, so
        # this is the replay branch — no sender, no env key (env={} is the injectable seam).
        assert (
            resolve_er_offline_payload_path(cfg) is not None
        )  # node logs er_source=offline_replay
        self.adapter = build_er_adapter(cfg, env={})
        # v0 request source: the committed ErTaskRequest fixture, via the node's own loaders.
        fixture_path = resolve_request_fixture_path(cfg)
        assert fixture_path is not None
        self.request = load_request_fixture(fixture_path)
        # The committed overlay ships dispatch.forward_to_nav2: false => forwarder None
        # (0 actuation — the dry-run first stage of dev/08 追補 2 step 5).
        assert resolve_nav2_forwarder(cfg) is None
        self.executor = TaskGraphExecutor(InMemoryTaskGraphStore())
        self.gen_store = FileGenStore(tmp_path / "gen_store")
        self.tools = _tools(tmp_path, self.gen_store)
        self.tool_executor = DispatchToolExecutor(self.tools.dispatch)
        self.inflight: dict[str, str] = {}

    def cycle(self):
        outcome = asyncio.run(
            run_x_er_cycle(
                request=self.request,
                adapter=self.adapter,
                runtime=self.runtime,
                executor=self.executor,
                gen_store=self.gen_store,
                tool_executor=self.tool_executor,
            )
        )
        assert fold_inflight(self.inflight, outcome.committed) == []
        return outcome

    def complete(self, robot: str, result: str = "succeeded"):
        goal_result = parse_goal_result(_goal_result_json(robot, result))
        assert goal_result is not None
        return apply_goal_result(
            goal_result, plan_id=PLAN_ID, inflight=self.inflight, executor=self.executor
        )


@pytest.mark.safety
def test_committed_artifacts_drive_red_then_blue_without_live_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The G5 promise: the committed kit alone runs the autonomous red->blue node cycle —
    zero provider calls, zero env keys, WAREHOUSE_LIVE_ER absent, 0 actuation."""
    fx = _Fixture(tmp_path, monkeypatch)

    out1 = fx.cycle()
    assert out1.skipped_reason is None
    assert [(i.bot, i.action, i.destination) for i in out1.command.commands] == [
        ("bot1", CommandAction.NAVIGATE, "shelf_1")
    ]

    completion = fx.complete("bot1")
    assert completion.applied is True and completion.retrigger is True

    out2 = fx.cycle()
    assert out2.skipped_reason is None
    assert [(i.bot, i.action, i.destination) for i in out2.command.commands] == [
        ("bot2", CommandAction.NAVIGATE, "shelf_2")
    ]

    dispatched = [*out1.dispatched, *out2.dispatched]
    assert [(r["robot"], r["dropoff"]) for r in dispatched] == [
        ("bot1", "shelf_1"),
        ("bot2", "shelf_2"),
    ]
    for record in dispatched:
        assert record["dropoff"] in KNOWN_LOCATIONS


# --- committed-artifact integrity oracles (each file individually pinned) --------------------


def test_committed_envelope_is_the_red_blue_direct_recording() -> None:
    # dev/08 追補 2: "direct_envelope() と同一形" — literal equality, an anti-drift oracle.
    payload = json.loads((_XER6_DIR / "er_offline_payload.direct.json").read_text("utf-8"))
    assert payload == direct_envelope()


def test_committed_request_fixture_is_a_valid_er_task_request() -> None:
    request = load_request_fixture(_XER6_DIR / "er_request.red_blue.json")
    assert request.transcript == INNER_PLAN["transcript"]
    assert request.known_robots == ["bot1", "bot2"]
    assert set(request.known_locations) == KNOWN_LOCATIONS


def test_committed_overlay_example_carries_exactly_the_doc08_keys() -> None:
    # doc08 §3 frozen keys + the Slice-B keys + the G5 freeze key — nothing invented.
    overlay = yaml.safe_load(_OVERLAY_EXAMPLE.read_text(encoding="utf-8"))
    assert set(overlay) == {"mode_x_er"}
    assert set(overlay["mode_x_er"]) == {
        "enabled",
        "execution_profile",
        "calibration_id",
        "visual",
        "run_manifest",
        "plugin_manifests",
        "site_profile",
        "dispatch",
        "request_fixture",
        "er_offline_payload",
    }
    assert overlay["mode_x_er"]["dispatch"] == {"forward_to_nav2": False}  # ships safe-OFF
    assert overlay["mode_x_er"]["plugin_manifests"] == []  # zero-plugin baseline


@pytest.mark.safety
def test_base_yaml_ships_er_offline_payload_empty_safe_default() -> None:
    # The base.yaml additive key must ship disabled ("" = unchanged live path, doc08 §3).
    base = yaml.safe_load((_REPO_ROOT / "config" / "warehouse.base.yaml").read_text("utf-8"))
    assert base["mode_x_er"]["er_offline_payload"] == ""


@pytest.mark.safety
def test_tampering_the_committed_bundle_refuses_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-26: the committed APPROVED.yaml is not decorative — a post-approval safety.yaml edit
    (copied aside; the repo tree is never mutated) must refuse the composed startup."""
    import shutil

    from warehouse_llm_bridge.robotics.composition.profile import SiteProfileError

    monkeypatch.delenv("WAREHOUSE_LIVE_ER", raising=False)
    cfg = _committed_cfg()
    tampered_base = tmp_path / "site_profiles"
    shutil.copytree(_XER6_DIR / "site_profiles", tampered_base)
    safety = tampered_base / "customer_a" / "site_01" / "safety.yaml"
    safety.write_text(safety.read_text(encoding="utf-8").replace("3.0", "9.0", 1), "utf-8")
    cfg["mode_x_er"]["site_profile"]["base_dir"] = str(tampered_base)
    with pytest.raises(SiteProfileError):
        build_x_er_runtime(
            cfg, plugin_factories=production_plugin_factories(), write_artifacts=False
        )
