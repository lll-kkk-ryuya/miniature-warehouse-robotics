"""XErBridge ROS 2 node — Mode X-ER visual-task commander cycle (docs/mode-x-er/08).

The XER6 backbone: one resident rclpy node (the Mode A ``llm_bridge.py`` shape) that
closes the Mode X-ER connectivity hops ⓪③④⑤ (docs/mode-x-er/08 §1):

* STARTUP (once, fail-closed — docs/mode-x-er/08 §4/§6): ``build_x_er_runtime(cfg)``
  runs the composition sequence (run manifest -> plugin registry -> dispatch policy ->
  composition -> preflight -> site-profile gate -> governed calibration -> effective
  composition witness). ANY raise aborts construction and ``main()`` exits nonzero —
  zero cycles, zero dispatch (the node never partially starts). ``build_er_adapter(cfg)``
  (robotics/adapter_factory.py:77) constructs the ER adapter (config-driven transport,
  shipped default DIRECT fail-safe, ADR-0002:43).
* CYCLE (docs/mode-x-er/08 §5): ``propose_plan`` is async while L3/composition are sync,
  so the cycle runs on a background thread with a dedicated asyncio event loop — the
  llm_bridge.py:254-297 pattern. The per-cycle logic (plugin-composed validate, L3 chain,
  gen minting, dispatch) lives in ``x_er_cycle.run_x_er_cycle`` (ROS-free).
* ACTUATION: none from this node. Offline Slice A HARD-FIXES ``WarehouseTools`` with
  ``nav2_forwarder=None`` (tools.py:92-115 — accept + book-keep only, 0 actuation).
  docs/mode-x-er/08 §5 step6 pins the sim flip to ``Nav2RestForwarder`` as config-driven,
  but its §3 frozen key set carries no forwarder key, so wiring that flip (llm_bridge.py:160
  pattern on the existing nav2_bridge config) is a LATER slice — flagged residual; flipping
  today WOULD require a code change.
* MUTUAL EXCLUSION: bringup composes this node IFF ``mode_x_er.enabled`` and then does
  NOT launch the Mode A commander (a single gen-minting commander per run — B-3 unique
  owner, docs/mode-x-er/08 §2 / mode-x-er/01:184-197).

* COMPLETION (Slice B, docs/mode-x-er/08 §5 step7): the node subscribes to
  ``/nav2_bridge/goal_result`` (std_msgs/String ``{robot, task_id, result}``, doc03:110 /
  doc12a:384-392) and converts each completion into a guarded ``mark_succeeded`` / ``mark_failed``
  on the long-lived executor, then re-triggers the cycle so the after-gated successor readies
  next cycle (red -> blue runs node-alone, no manual step). Correlation is BY ROBOT (the nav2
  task id does not round-trip; x_er_completion module docstring). The pure decision logic lives
  in ``x_er_completion.apply_goal_result`` (ROS-free); this node only parses + marshals + wakes.

v0 request source (dev-only, PROVISIONAL — flagged residual): ``mode_x_er.request_fixture`` is
a path to a JSON file of ``ErTaskRequest`` fields, ONLY consumed when set. Real request
producers (mic capture / pre-recorded ref wiring) remain a later slice.

rclpy plus the lane-built ``x_er_composition`` / ``x_er_cycle`` wiring modules are
import-guarded so plain pytest collection can import the pure helpers below without ROS
(doc16 §11 pure-CI discipline — ``tests/unit/test_bringup_launch.py`` importorskips the
launch modules the same way).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from warehouse_llm_bridge.robotics.er_task import ErTaskRequest

try:
    # Runtime node deps: rclpy exists only in the ROS runtime env; x_er_composition /
    # x_er_cycle are the ROS-free wiring modules this node codes against (frozen
    # inter-module IF). Guarded together so the pure helpers stay collectable.
    import rclpy
    from rclpy.logging import get_logger
    from rclpy.node import Node
    from std_msgs.msg import String
    from warehouse_interfaces.config import load_config
    from warehouse_interfaces.stores import FileGenStore, FileIdempotencyStore, FileStateStore
    from warehouse_mcp_server.gen_check import GenChecker
    from warehouse_mcp_server.tools import WarehouseTools

    from warehouse_llm_bridge.executor import DispatchToolExecutor
    from warehouse_llm_bridge.robotics.adapter_factory import build_er_adapter
    from warehouse_llm_bridge.robotics_planning_core.task_graph_executor import TaskGraphExecutor
    from warehouse_llm_bridge.x_er_completion import apply_goal_result, parse_goal_result
    from warehouse_llm_bridge.x_er_composition import XErRuntime, build_x_er_runtime
    from warehouse_llm_bridge.x_er_cycle import run_x_er_cycle
except ImportError as exc:  # pragma: no cover — only in a rclpy-less (pure pytest) env
    _NODE_IMPORT_ERROR: ImportError | None = exc
else:
    _NODE_IMPORT_ERROR = None

# Config sub-tree (docs/mode-x-er/08 §3 frozen key shape; base.yaml ships safe-OFF/empty).
_MODE_X_ER_KEY = "mode_x_er"
# v0 dev-only request source (PROVISIONAL, not part of the doc08 §3 frozen shape — see
# module docstring / CLAUDE.md residual). Only consumed when set to a non-blank string.
_REQUEST_FIXTURE_KEY = "request_fixture"
# Completion signal the node consumes to advance the task graph (Slice B, doc08 §5 step7).
# std_msgs/String JSON {robot, task_id, result} (doc03:110 / doc12a:384-392 /
# warehouse_nav2_bridge/nav2_bridge.py:42). Named locally — no cross-package import.
_GOAL_RESULT_TOPIC = "/nav2_bridge/goal_result"


def resolve_request_fixture_path(cfg: Mapping[str, Any]) -> Path | None:
    """Return ``mode_x_er.request_fixture`` as a ``Path``, or ``None`` when unset.

    Absent block / absent key / blank string -> ``None`` (the node starts and idles —
    no request source configured). A PRESENT but non-string value is malformed config
    and raises (fail-closed at startup) rather than being silently ignored.
    """
    mode_x_er = cfg.get(_MODE_X_ER_KEY)
    if not isinstance(mode_x_er, Mapping):
        return None
    raw = mode_x_er.get(_REQUEST_FIXTURE_KEY)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(
            f"mode_x_er.{_REQUEST_FIXTURE_KEY} must be a string path, got {type(raw).__name__}"
        )
    if not raw.strip():
        return None
    return Path(raw)


def load_request_fixture(path: Path | str) -> ErTaskRequest:
    """Parse a JSON file of ``ErTaskRequest`` fields into a validated request.

    The fixture rides the SAME L4 input hygiene as any request (er_task.py:31 —
    ``known_locations ⊆ KNOWN_LOCATIONS`` etc.), so a typo'd fixture is a startup
    refusal (raise), never a request the ER model sees. Raises ``ValueError``
    (``json.JSONDecodeError`` included) on unreadable/non-object JSON and pydantic
    ``ValidationError`` on contract-violating fields — all fail-closed at startup.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(
            f"request fixture must be a JSON object of ErTaskRequest fields, "
            f"got {type(payload).__name__}"
        )
    return ErTaskRequest.model_validate(payload)


if _NODE_IMPORT_ERROR is None:

    class XErBridge(Node):
        """ROS 2 node hosting the Mode X-ER commander cycle (docs/mode-x-er/08 §2)."""

        def __init__(self) -> None:
            """Run the fail-closed composition startup, then prepare the cycle loop thread.

            ANY raise out of here (composition §4 steps, adapter construction, a set-but-
            malformed request fixture) aborts node construction; ``main()`` then refuses
            to spin — 0 cycles, 0 dispatch (docs/mode-x-er/08 §6 起動時).
            """
            super().__init__("x_er_bridge")
            cfg = load_config()
            # §4 composition startup (fail-closed, once): manifest -> plugin registry ->
            # dispatch policy -> composition -> preflight -> site-profile/calibration
            # gate -> effective-composition witness (out/runs/<run_id>/).
            self._runtime: XErRuntime = build_x_er_runtime(cfg)
            # §4 step8: config -> transport -> constructed ER adapter (DIRECT fail-safe,
            # adapter_factory.py:77). Construction only — a live send stays behind the
            # WAREHOUSE_LIVE_ER operator/cost gate, which this node NEVER sets.
            self._adapter = build_er_adapter(cfg)
            # §5 step5: gen minting is node-owned via the shared FileGenStore
            # (llm_bridge.py:152 same shape); ER-derived values are never used as gen
            # (mode-x-er/01:184-197).
            self._gen_store = FileGenStore()
            # §5 step4: ONE long-lived TaskGraphExecutor for the node's lifetime; every
            # cycle re-injects the SAME instance (STALE-HANDLE contract, doc02:361).
            self._task_executor = TaskGraphExecutor()
            # §5 step6: offline Slice A is FIXED nav2_forwarder=None (tools.py:92-115 =
            # accept + book-keep only, 0 actuation). Sim flips to Nav2RestForwarder by
            # CONFIG in a later slice — no code change here. Store wiring mirrors
            # llm_bridge.py:160-166 (shared gen_store => B-3 works end-to-end).
            self._tools = WarehouseTools(
                gen_checker=GenChecker(self._gen_store, FileIdempotencyStore()),
                state_store=FileStateStore(),
                nav2_forwarder=None,
            )
            self._tool_executor = DispatchToolExecutor(self._tools.dispatch)
            # v0 request source (dev-only, provisional): only consumed when set. A
            # set-but-malformed fixture raises here = startup refusal, not a skipped cycle.
            fixture_path = resolve_request_fixture_path(cfg)
            self._request: ErTaskRequest | None = (
                load_request_fixture(fixture_path) if fixture_path is not None else None
            )
            self._loop = asyncio.new_event_loop()
            self._stop_requested = asyncio.Event()
            self._cycle_trigger = asyncio.Event()
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            # §5 step7 progression (Slice B): the caller loop owns lifecycle. ``_plan_id`` is
            # the single active plan's store key; ``_inflight`` maps ``robot -> dispatched
            # node id`` so a /nav2_bridge/goal_result is correlated by robot back to the node
            # it advances (x_er_completion). Both are mutated ONLY on the cycle event loop
            # (the subscription callback marshals via call_soon_threadsafe), so no lock.
            self._plan_id: str | None = None
            self._inflight: dict[str, str] = {}
            # Completion signal subscription (doc08 §5 step7): parse -> marshal onto the cycle
            # loop -> guarded mark_succeeded/failed -> re-trigger. 0 actuation.
            self.create_subscription(String, _GOAL_RESULT_TOPIC, self._on_goal_result_msg, 10)
            request_src = "fixture" if self._request is not None else "none"
            self.get_logger().info(
                f"x_er_bridge ready (composition preflight passed, request_source="
                f"{request_src}, out_dir={self._runtime.out_dir})"
            )

        # ── cycle loop (background thread + dedicated event loop, llm_bridge.py:254-297) ──

        def _run_loop(self) -> None:
            asyncio.set_event_loop(self._loop)
            with contextlib.suppress(asyncio.CancelledError):
                self._loop.run_until_complete(self._run_cycles())

        async def _run_cycles(self) -> None:
            """Drive ``run_x_er_cycle`` while a request is pending (docs/mode-x-er/08 §5).

            One awaited cycle per wake-up: ``compile_raw_output`` is one-shot
            ready-tasks-only, so t2 becomes ready only after t1's completion signal
            marks it ``succeeded`` (``_apply_goal_result_on_loop`` sets ``_cycle_trigger``,
            Slice B). The loop parks on the trigger between cycles instead of re-offering the
            same ready set in a busy loop (docs/mode-x-er/08 §5 step7).
            """
            if self._request is None:
                self.get_logger().info(
                    "x_er_bridge idle: mode_x_er.request_fixture unset (v0 request source)"
                )
                return
            while not self._stop_requested.is_set():
                try:
                    outcome = await run_x_er_cycle(
                        request=self._request,
                        adapter=self._adapter,
                        runtime=self._runtime,
                        executor=self._task_executor,
                        gen_store=self._gen_store,
                        tool_executor=self._tool_executor,
                    )
                except Exception as exc:
                    # §6: never swallow an exception and keep dispatching (fail-open
                    # forbidden). An unexpected cycle raise ends the cycle loop for
                    # good — the node stays up but issues 0 further dispatches.
                    self.get_logger().error(
                        f"x_er_bridge cycle raised ({exc!r}); refusing further cycles "
                        "(fail-closed, docs/mode-x-er/08 §6)"
                    )
                    raise
                if outcome.plan_id is not None:
                    self._plan_id = outcome.plan_id
                if outcome.skipped_reason is not None:
                    # §6 cycle-level fail-closed outcomes (adapter_error /
                    # plugin_rejected / empty_command): 0 dispatch, store untouched;
                    # the node stays alive for the next trigger.
                    self.get_logger().warning(
                        f"x_er_bridge cycle skipped ({outcome.skipped_reason}): 0 dispatch"
                    )
                else:
                    # Fold the committed (robot, node id) pairs into the by-robot correlation
                    # map so a completion for that robot advances the right node (Slice B).
                    for robot, node_id in outcome.committed:
                        self._inflight[robot] = node_id
                    self.get_logger().info(
                        f"x_er_bridge cycle dispatched {len(outcome.dispatched)} tool call(s)"
                    )
                await self._wait_for_cycle_trigger()

        async def _wait_for_cycle_trigger(self) -> None:
            """Park until the completion-signal seam (or shutdown) wakes the loop."""
            waiters = {
                asyncio.ensure_future(self._cycle_trigger.wait()),
                asyncio.ensure_future(self._stop_requested.wait()),
            }
            try:
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for waiter in waiters:
                    waiter.cancel()
            self._cycle_trigger.clear()

        def _on_goal_result_msg(self, msg: String) -> None:
            """ROS callback for ``/nav2_bridge/goal_result`` (runs on the rclpy spin thread).

            Parses the ``std_msgs/String`` JSON and marshals a valid completion onto the cycle
            event loop (``call_soon_threadsafe``) so the store transition happens on the SAME
            single thread the cycle runs on — no lock, no stale handle (executor.py:150-163).
            A malformed payload is dropped (fail-closed, doc08 §6).
            """
            goal_result = parse_goal_result(msg.data)
            if goal_result is None:
                self.get_logger().warning(
                    "x_er_bridge: ignoring malformed /nav2_bridge/goal_result payload"
                )
                return
            with contextlib.suppress(RuntimeError):  # loop already stopped (idle node)
                self._loop.call_soon_threadsafe(self._apply_goal_result_on_loop, goal_result)

        def _apply_goal_result_on_loop(self, goal_result: Any) -> None:
            """Apply one completion on the cycle loop (doc08 §5 step7): mark_succeeded/failed
            then re-trigger the loop so the after-gated successor is compiled next cycle.

            All executor/store access stays on the loop thread; correlation + the fail-closed
            transition live in the pure ``x_er_completion.apply_goal_result``.
            """
            if self._plan_id is None:
                return  # no cycle has dispatched yet — nothing to correlate against.
            outcome = apply_goal_result(
                goal_result,
                plan_id=self._plan_id,
                inflight=self._inflight,
                executor=self._task_executor,
            )
            self.get_logger().info(
                f"x_er_bridge goal_result (robot={goal_result.robot}, "
                f"result={goal_result.result}): {outcome.reason}"
            )
            if outcome.retrigger:
                self._cycle_trigger.set()

        # ── lifecycle (mirrors llm_bridge.py:299-305) ─────────────────────────────────

        def start(self) -> None:
            """Start the commander cycle loop in a background thread."""
            self._thread.start()

        def shutdown(self) -> None:
            """Stop the cycle loop (best-effort; the loop thread is a daemon)."""
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(self._stop_requested.set)


def main() -> None:
    """Run the X-ER Bridge node: spin ROS while the asyncio cycle loop runs.

    Startup refusal (docs/mode-x-er/08 §6): ANY exception during node construction
    (composition §4, adapter, request fixture) is logged and the process exits
    nonzero WITHOUT spinning — the node never partially starts cycles.
    """
    if _NODE_IMPORT_ERROR is not None:
        raise ModuleNotFoundError(
            "x_er_bridge runtime deps unavailable (rclpy / x_er_composition / x_er_cycle)"
        ) from _NODE_IMPORT_ERROR
    rclpy.init()
    try:
        node = XErBridge()
    except Exception as exc:
        get_logger("x_er_bridge").error(
            f"x_er_bridge startup refused (fail-closed, docs/mode-x-er/08 §6): {exc!r}"
        )
        rclpy.shutdown()
        raise SystemExit(1) from exc
    node.start()
    try:
        with contextlib.suppress(KeyboardInterrupt):
            rclpy.spin(node)
    finally:
        node.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
