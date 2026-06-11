"""Mode C (open-rmf) NO-ACTUATION contract — the LLM Bridge never drives a robot.

Frozen topology (docs-first source of truth):

* ``/bot{n}/goal_pose`` is published by the Fleet Adapter (Mode C) / the Warehouse
  MCP Server (Mode A/B) — NEVER by the LLM Bridge Node
  (docs/architecture/03-software-architecture.md:97 /
  docs/architecture/08-llm-bridge-common.md:462).
* In Mode C the commander's decisions reach Nav2 over the Open-RMF Task API, so the
  Bridge wires NO Nav2 REST forwarder — ``forwarder=None``
  (docs/architecture/08-llm-bridge-common.md:169 /
  docs/architecture/15-mcp-platform.md:211-219 /
  docs/mode-c/08c-llm-bridge-mode-c.md:264).

The existing ``tests/unit/test_nav2_forward.py`` covers the Mode A/B side (a forwarder
IS injected and an accepted motion tool POSTs exactly once). This file pins the
*missing* Mode C invariant from three independent angles — all host-runnable with no
ROS / network / Gazebo (doc16 §11, .github/workflows/ci.yml installs no rclpy):

1. ``forwarder=None`` ⇒ an ACCEPTED motion tool still validates + book-keeps but
   actuates NOTHING: the single forward seam (``_maybe_forward``, tools.py:153)
   early-returns before it even *plans* a request — proven with a sentinel that fails
   the test if the seam is ever entered.
2. The wiring constant ``NAV2_BRIDGE_MODES`` (llm_bridge.py:75) selects a forwarder
   ONLY for {none, simple}; ``open-rmf`` is excluded, so llm_bridge.py:135's ternary
   takes the ``None`` branch for Mode C.
3. The Bridge node creates publishers for ``/llm/reasoning`` + ``/llm/command`` ONLY —
   it never publishes ``/bot{n}/goal_pose`` (doc03:97 / doc08:462).

This is an R-26 safety contract: a Mode C commander that bypassed Open-RMF and drove a
robot directly would defeat the entire traffic-management layer. Lane A's coordinate-
goal injector (#223) routes through the Nav2 Bridge and must honour the same boundary.
"""

import ast
import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from warehouse_interfaces.schemas import Command
from warehouse_interfaces.stores import FileGenStore, FileIdempotencyStore, FileStateStore
from warehouse_llm_bridge.action_map import command_to_tool_calls
from warehouse_llm_bridge.executor import DispatchToolExecutor
from warehouse_mcp_server.audit import CommandAuditLog
from warehouse_mcp_server.gen_check import GenChecker
from warehouse_mcp_server.policy_gate import PolicyGate
from warehouse_mcp_server.tools import WarehouseTools

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LLM_BRIDGE_PY = _REPO_ROOT / "ws/src/warehouse_llm_bridge/warehouse_llm_bridge/llm_bridge.py"


def _tools_without_forwarder(tmp_path: Path, gen: int, *, battery: int = 90) -> WarehouseTools:
    """Real ``WarehouseTools`` wired exactly as Mode C wires them: ``nav2_forwarder=None``.

    Mirrors ``test_nav2_forward.py::_tools`` but injects NO forwarder. That helper
    always falls back to a ``RecordingNav2Forwarder`` (test_nav2_forward.py:149), so it
    structurally cannot express the Mode C wiring — hence this dedicated builder.
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
        nav2_forwarder=None,  # Mode C / Open-RMF: no direct Nav2 forwarder (doc15:211-219)
    )


def _navigate_call(bot: str, destination: str, gen: int):
    cmd = Command.model_validate(
        {
            "reasoning": "r",
            "commands": [{"bot": bot, "action": "navigate", "destination": destination}],
        }
    )
    [tool_call] = command_to_tool_calls(cmd, gen)
    return tool_call


def _forbid_forward_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``plan_nav2_request`` fail loudly if the forward seam is ever entered.

    ``_maybe_forward`` only reaches ``plan_nav2_request`` (tools.py:155) AFTER the
    ``self._nav2_forwarder is None`` gate (tools.py:153). With forwarder=None the gate
    must short-circuit, so this sentinel must never fire — if it does, an accepted Mode
    C decision tried to actuate, which is the exact regression this contract forbids.
    """

    def _sentinel(*_args, **_kwargs):  # pragma: no cover - asserts it is never called
        raise AssertionError(
            "plan_nav2_request was called with forwarder=None: the Mode C no-actuation "
            "gate (tools.py:153) failed — an accepted decision tried to actuate a robot."
        )

    monkeypatch.setattr("warehouse_mcp_server.tools.plan_nav2_request", _sentinel)


# ── 1. forwarder=None ⇒ an accepted tool validates + book-keeps but actuates nothing ──


@pytest.mark.safety
@pytest.mark.unit
def test_mode_c_forwarder_none_actuates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fully ACCEPTED navigate (gen matches, battery healthy, known location) returns
    # status ok — yet with forwarder=None it must reach NO actuation. The sentinel proves
    # the forward seam early-returns before even planning an endpoint (R-26).
    tools = _tools_without_forwarder(tmp_path, gen=1)
    _forbid_forward_seam(monkeypatch)

    result = asyncio.run(
        DispatchToolExecutor(tools.dispatch).execute(_navigate_call("bot1", "berth_A", 1))
    )

    assert result["status"] == "ok"  # the tool DID validate + book-keep (not skipped)
    assert result.get("robot") == "bot1"
    assert tools._nav2_forwarder is None  # the Mode C precondition this contract pins
    # _sentinel never raised ⇒ the forward seam was never entered ⇒ zero actuation.


@pytest.mark.safety
@pytest.mark.unit
def test_mode_c_forwarder_none_charge_actuates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same gate for a second accepted motion tool (charge -> send_to_charging): the
    # forwarder=None branch is tool-agnostic, so an accepted charge also actuates nothing.
    tools = _tools_without_forwarder(tmp_path, gen=1, battery=15)  # <=80 so charging is allowed
    _forbid_forward_seam(monkeypatch)
    cmd = Command.model_validate(
        {"reasoning": "low", "commands": [{"bot": "bot1", "action": "charge"}]}
    )
    [tool_call] = command_to_tool_calls(cmd, gen_id=1)

    result = asyncio.run(DispatchToolExecutor(tools.dispatch).execute(tool_call))

    assert result["status"] == "ok"
    assert tools._nav2_forwarder is None


# ── 2. Mode C selects no forwarder (the wiring constant llm_bridge.py:75) ──────────────


def _nav2_bridge_modes_from_source() -> frozenset:
    """Read the REAL ``NAV2_BRIDGE_MODES`` (llm_bridge.py:75) from source, no import.

    ``llm_bridge.py`` is a thin rclpy adapter: it does ``import rclpy`` at module top
    (llm_bridge.py:47) and declares ``class LlmBridge(Node)``, so it cannot be imported
    in the no-ROS CI gate (.github/workflows/ci.yml installs no rclpy). We AST-parse the
    module-level ``NAV2_BRIDGE_MODES = frozenset({...})`` assignment and evaluate the set
    literal — pinning the ACTUAL source-of-truth constant (so a hand-copied duplicate
    cannot silently drift) with ZERO import side effects. Same technique as
    ``test_mode_c_bridge_publishes_only_llm_topics`` below; no ``sys.modules`` surgery.
    """
    tree = ast.parse(_LLM_BRIDGE_PY.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "NAV2_BRIDGE_MODES"
            for target in node.targets
        ):
            value = node.value  # NAV2_BRIDGE_MODES = frozenset({...}) -> Call(frozenset, [Set])
            assert isinstance(value, ast.Call) and getattr(value.func, "id", None) == "frozenset"
            return frozenset(ast.literal_eval(value.args[0]))
    raise AssertionError("NAV2_BRIDGE_MODES assignment not found in llm_bridge.py")


@pytest.mark.safety
@pytest.mark.unit
def test_mode_c_wiring_selects_no_forwarder() -> None:
    # ``open-rmf`` is NOT a Nav2-Bridge mode ⇒ llm_bridge.py:135's ternary
    # (``Nav2RestForwarder(...) if mode in NAV2_BRIDGE_MODES else None``) takes the None
    # branch for Mode C — which test 1 proves actuates nothing. {none, simple} keep their
    # forwarder (Mode A/B, doc15:211-219). The exact-set assert fails if anyone adds a mode.
    modes = _nav2_bridge_modes_from_source()
    assert "open-rmf" not in modes  # Mode C: routes via Open-RMF, no direct forwarder
    assert "none" in modes  # Mode A
    assert "simple" in modes  # Mode B
    assert modes == frozenset({"none", "simple"})


# ── 3. the Bridge publishes only /llm/* — never /bot{n}/goal_pose ─────────────────────


@pytest.mark.safety
@pytest.mark.unit
def test_mode_c_bridge_publishes_only_llm_topics() -> None:
    # doc03:97 / doc08:462: /bot{n}/goal_pose is published by the Fleet Adapter (Mode C) /
    # Warehouse MCP Server (Mode A/B), NEVER by the LLM Bridge. Pinned structurally (AST
    # over the source — no ROS import): the set of string-literal topics passed to
    # create_publisher must be exactly the two /llm/* topics, so a regression adding a
    # goal_pose / cmd_vel publisher to the bridge would fail here.
    tree = ast.parse(_LLM_BRIDGE_PY.read_text())
    published_topics: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_publisher"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
        ):
            published_topics.add(node.args[1].value)
    assert published_topics == {"/llm/reasoning", "/llm/command"}
    assert not any("goal_pose" in topic for topic in published_topics)  # never drives a robot
