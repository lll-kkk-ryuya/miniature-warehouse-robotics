"""XErBridge ROS 2 node — Mode X-ER visual-task commander cycle (docs/mode-x-er/08).

The XER6 backbone: one resident rclpy node (the Mode A ``llm_bridge.py`` shape) that
closes the Mode X-ER connectivity hops ⓪③④⑤ (docs/mode-x-er/08 §1):

* STARTUP (once, fail-closed — docs/mode-x-er/08 §4/§6): ``build_x_er_runtime(cfg,
  plugin_factories=production_plugin_factories())`` runs the composition sequence (run
  manifest -> plugin registry -> dispatch policy -> composition -> preflight -> site-profile
  gate -> governed calibration -> effective composition witness). Plugin factories come from
  the explicit production registry (``robotics/composition/factory_registry.py``, empty
  today — doc09 §稼働 node の plugin factory seam; a declared plugin with no registered
  factory keeps refusing startup, fail-closed). ANY raise aborts construction and
  ``main()`` exits nonzero — zero cycles, zero dispatch (the node never partially starts). ``build_er_adapter(cfg)``
  (robotics/adapter_factory.py:130) constructs the ER adapter (config-driven transport,
  shipped default DIRECT fail-safe, ADR-0002:43).
* CYCLE (docs/mode-x-er/08 §5): ``propose_plan`` is async while L3/composition are sync,
  so the cycle runs on a background thread with a dedicated asyncio event loop — the
  llm_bridge.py:254-297 pattern. The per-cycle logic (plugin-composed validate, L3 chain,
  gen minting, dispatch) lives in ``x_er_cycle.run_x_er_cycle`` (ROS-free).
* ACTUATION: none from this node BY DEFAULT. ``WarehouseTools`` is wired with a config-driven
  forwarder (``resolve_nav2_forwarder``): safe-OFF default ``nav2_forwarder=None`` (tools.py:
  92-115 — accept + book-keep only, 0 actuation). ``mode_x_er.dispatch.forward_to_nav2: true``
  (doc08 §3, #421) flips it to ``Nav2RestForwarder`` on the existing ``nav2_bridge.base_url`` —
  a CONFIG-only sim/real motion enable (no code change). The node itself still originates no
  motion; L1/L0 safety layers are untouched.
* MUTUAL EXCLUSION: bringup composes this node IFF ``mode_x_er.enabled`` and then does
  NOT launch the Mode A commander (a single gen-minting commander per run — B-3 unique
  owner, docs/mode-x-er/08 §2 / mode-x-er/01:184-197).

* COMPLETION (Slice B, docs/mode-x-er/08 §5 step7): the node subscribes to
  ``/nav2_bridge/goal_result`` (std_msgs/String ``{robot, task_id, result}``, doc03:110 /
  doc12a:384-392). The spin-thread callback only ENQUEUES a parsed completion and wakes the
  loop; the loop DRAINS the queue BETWEEN cycles (``apply_pending_completions``), converting
  each into a guarded ``mark_succeeded`` / ``mark_failed`` on the long-lived executor and
  re-driving so the after-gated successor readies next cycle (red -> blue runs node-alone, no
  manual step). Draining between cycles (never during one) keeps a SINGLE live executor handle
  per plan (executor.py:157-163) — a completion applied mid-cycle would race the cycle's handle
  and silently lose a transition. Correlation is BY ROBOT (the nav2 task id does not
  round-trip; x_er_completion module docstring). All decision logic is ROS-free
  (``x_er_completion``); this node only subscribes, enqueues, drains, and folds.

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
import time
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

# ROS-free (httpx is lazily imported inside Nav2RestForwarder.forward; emergency_sync is
# pure L2 logic), so this stays importable under plain pytest — the forwarder resolver and
# the emergency-mirror state source below are pure, testable helpers.
from warehouse_mcp_server.emergency_sync import EmergencyLevelMirror, clear_after_from_config
from warehouse_mcp_server.nav2_client import Nav2Forwarder, Nav2RestForwarder

from warehouse_llm_bridge.robotics.composition.factory_registry import (
    production_plugin_factories,
)
from warehouse_llm_bridge.robotics.er_task import ErTaskRequest
from warehouse_llm_bridge.robotics_planning_core.validator import RuntimeSafetyState

try:
    # Runtime node deps: rclpy exists only in the ROS runtime env; x_er_composition /
    # x_er_cycle are the ROS-free wiring modules this node codes against (frozen
    # inter-module IF). Guarded together so the pure helpers stay collectable.
    import rclpy
    from geometry_msgs.msg import Twist
    from rclpy.logging import get_logger
    from rclpy.node import Node
    from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
    from std_msgs.msg import String
    from warehouse_interfaces.config import load_config
    from warehouse_interfaces.stores import FileGenStore, FileIdempotencyStore, FileStateStore
    from warehouse_mcp_server.gen_check import GenChecker
    from warehouse_mcp_server.tools import WarehouseTools

    from warehouse_llm_bridge.executor import DispatchToolExecutor
    from warehouse_llm_bridge.robotics.adapter_factory import (
        build_er_adapter,
        resolve_er_offline_payload_path,
    )
    from warehouse_llm_bridge.robotics_planning_core.task_graph_executor import TaskGraphExecutor
    from warehouse_llm_bridge.x_er_completion import (
        apply_pending_completions,
        fold_inflight,
        parse_goal_result,
    )
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
# Slice B sim-flip key (doc08 §3, #421): mode_x_er.dispatch.forward_to_nav2 selects whether an
# accepted motion tool is forwarded to the Nav2 Bridge REST API. Endpoint reuses the existing
# nav2_bridge.base_url (no new key invented, mirroring llm_bridge.py:160).
_DISPATCH_KEY = "dispatch"
_FORWARD_TO_NAV2_KEY = "forward_to_nav2"
_NAV2_BRIDGE_KEY = "nav2_bridge"
_BASE_URL_KEY = "base_url"
# Guardian estop mirror wiring (doc08 §11 / doc12 【2026-09-07 追補】). Fleet namespaces are
# the doc03 topic-contract tuple (same as llm_bridge.py _BOTS); the topic is the Guardian's
# level-held estop output (producer: warehouse_safety emergency_guardian, twist_mux prio100
# input — this node is an added consumer, contract unchanged).
_BOTS = ("bot1", "bot2")
_EMERGENCY_TOPIC = "/{bot}/cmd_vel/emergency"
# Sweep cadence (llm_bridge.py EMERGENCY_SWEEP_PERIOD_S same value): worst-case clear
# latency = emergency_clear_after_s + this period, still after the 0.5s twist_mux expiry.
_EMERGENCY_SWEEP_PERIOD_S = 0.1


def resolve_nav2_forwarder(cfg: Mapping[str, Any]) -> Nav2Forwarder | None:
    """Resolve the Nav2 forwarder from ``mode_x_er.dispatch.forward_to_nav2`` (doc08 §3, #421).

    SAFE-OFF by default: returns ``None`` (offline accept + book-keep only, 0 actuation —
    tools.py:92-115) unless the key is EXACTLY the YAML boolean ``true``. Any other value
    (absent, ``false``, a truthy string/typo) stays OFF — a motion-enabling flag must never be
    turned on by an ambiguous value. When enabled, the endpoint reuses the existing
    ``nav2_bridge.base_url`` (no new key); a missing/blank ``base_url`` with forwarding requested
    is a fail-closed startup refusal (you asked to actuate but named no endpoint), not a silent
    fall back to ``None``.

    Pure / offline: constructs but does not call ``Nav2RestForwarder`` (its httpx POST is lazy),
    so this is unit-testable without ROS or a network.
    """
    mode_x_er = cfg.get(_MODE_X_ER_KEY)
    dispatch = mode_x_er.get(_DISPATCH_KEY) if isinstance(mode_x_er, Mapping) else None
    forward = dispatch.get(_FORWARD_TO_NAV2_KEY) if isinstance(dispatch, Mapping) else None
    if forward is not True:
        return None
    nav2 = cfg.get(_NAV2_BRIDGE_KEY)
    base_url = nav2.get(_BASE_URL_KEY) if isinstance(nav2, Mapping) else None
    if not isinstance(base_url, str) or not base_url:
        raise ValueError(
            "mode_x_er.dispatch.forward_to_nav2 is true but nav2_bridge.base_url is missing/blank "
            "— refusing to start a motion-forwarding node with no endpoint (fail-closed)"
        )
    return Nav2RestForwarder(base_url)


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


class _SupportsHeldBots(Protocol):
    """The mirror surface this node's L3 feed reads (emergency_sync.py held_bots)."""

    def held_bots(self) -> frozenset[str]: ...


class EmergencyMirrorStateSource:
    """L4 adapter: Guardian estop mirror -> L3 ``RuntimeSafetyState`` (doc08 §11.2).

    Implements the L3 ``RuntimeStateSource`` protocol (context.py:41-51) over the SAME
    ``EmergencyLevelMirror`` instance that feeds the L2 Policy Gate, closing the L3
    ``EMERGENCY_ACTIVE`` never-fire (validator.py:121 — ``emergency_active`` had no
    production producer). Semantics are FLEET-ANY: while ANY bot's estop level is held,
    no new plan is assembled; per-robot precision stays L2's dispatch-time job
    (productization/11 L3=plan-time / L2=dispatch-time pair).

    ``state_age_s`` stays ``None`` (the freshness gate is disabled in the reference
    policy — feeding it is a separate slice, doc08 §11.3 残①).

    Fail-closed read: ``held_bots()`` builds a frozenset from a dict mutated on the rclpy
    spin thread (``on_stop_signal`` / ``sweep``) while this reader runs on the cycle-loop
    thread; a concurrent-resize ``RuntimeError`` is treated as emergency-active for THIS
    cycle (the mutation itself evidences an estop transition; one conservative skipped
    cycle, self-healing on the next).
    """

    def __init__(self, mirror: _SupportsHeldBots) -> None:
        self._mirror = mirror

    def current_state(self) -> RuntimeSafetyState:
        """Snapshot the mirror into the per-cycle L3 safety state (doc08 §11.2)."""
        try:
            held = self._mirror.held_bots()
        except RuntimeError:
            return RuntimeSafetyState(emergency_active=True)
        return RuntimeSafetyState(emergency_active=bool(held))


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
            # gate -> effective-composition witness (out/runs/<run_id>/). Plugin factories
            # come from the explicit production registry (factory_registry.py — empty today;
            # doc09 §稼働 node の plugin factory seam): a run-declared plugin missing from it
            # keeps refusing startup (XErCompositionError, x_er_composition.py:174-182).
            self._runtime: XErRuntime = build_x_er_runtime(
                cfg, plugin_factories=production_plugin_factories()
            )
            # §4 step8: config -> transport -> constructed ER adapter (DIRECT fail-safe,
            # adapter_factory.py:130). Construction only — a live send stays behind the
            # WAREHOUSE_LIVE_ER operator/cost gate, which this node NEVER sets.
            self._adapter = build_er_adapter(cfg)
            # §5 step5: gen minting is node-owned via the shared FileGenStore
            # (llm_bridge.py:152 same shape); ER-derived values are never used as gen
            # (mode-x-er/01:184-197).
            self._gen_store = FileGenStore()
            # §5 step4: ONE long-lived TaskGraphExecutor for the node's lifetime; every
            # cycle re-injects the SAME instance (STALE-HANDLE contract, doc02:361).
            self._task_executor = TaskGraphExecutor()
            # §5 step6 (Slice B): the forwarder is config-driven via
            # mode_x_er.dispatch.forward_to_nav2 (doc08 §3, #421) — safe-OFF default = None
            # (accept + book-keep only, 0 actuation, tools.py:92-115); true = Nav2RestForwarder
            # on the existing nav2_bridge.base_url (sim/real motion). Store wiring mirrors
            # llm_bridge.py:160-166 (shared gen_store => B-3 works end-to-end).
            self._tools = WarehouseTools(
                gen_checker=GenChecker(self._gen_store, FileIdempotencyStore()),
                state_store=FileStateStore(),
                nav2_forwarder=resolve_nav2_forwarder(cfg),
                # Config-driven Policy Gate freshness windows (cfg["policy_gate"],
                # base defaults 0.5/2.0; doc12 §stale 判定). Absent block => defaults.
                config=cfg,
            )
            self._tool_executor = DispatchToolExecutor(self._tools.dispatch)
            # Guardian estop -> L2/L3 emergency feed (doc08 §11 / doc12 【2026-09-07 追補】,
            # llm_bridge.py #592 same-shape wiring). ONE mirror, TWO consumers: (L2) each
            # level signal drives tools.policy_gate.set_emergency so check_emergency fires
            # at dispatch time; (L3) held_bots() feeds plan-time EMERGENCY_ACTIVE through
            # the runtime-state source injected into run_x_er_cycle. A malformed clear
            # window (clear_after_from_config, tighten-only floor) raises here = startup
            # refusal (§6 起動時 family). QoS mirrors the Guardian's RELIABLE publisher.
            self._emergency_mirror = EmergencyLevelMirror(
                self._tools.policy_gate.set_emergency, clear_after_from_config(cfg)
            )
            self._runtime_state_source = EmergencyMirrorStateSource(self._emergency_mirror)
            estop_qos = QoSProfile(
                reliability=ReliabilityPolicy.RELIABLE, history=HistoryPolicy.KEEP_LAST, depth=10
            )
            for bot in _BOTS:
                # b=bot binds the loop variable per-callback (late-binding closure pitfall).
                self.create_subscription(
                    Twist,
                    _EMERGENCY_TOPIC.format(bot=bot),
                    lambda _msg, b=bot: self._emergency_mirror.on_stop_signal(b, time.monotonic()),
                    estop_qos,
                )
            self.create_timer(_EMERGENCY_SWEEP_PERIOD_S, self._sweep_emergency_mirror)
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
            # the single active plan's store key; ``_inflight`` maps ``robot -> dispatched node
            # id`` for by-robot completion correlation (x_er_completion). Both are mutated ONLY
            # on the cycle-loop thread, BETWEEN cycles — never while run_x_er_cycle holds a live
            # executor handle (single-live-handle contract, executor.py:157-163).
            self._plan_id: str | None = None
            self._inflight: dict[str, str] = {}
            # Completions arrive on the rclpy spin thread; the callback only ENQUEUES here
            # (deque.append is atomic) and wakes the loop. The loop drains + applies them at the
            # top of each iteration, so a completion never races an in-flight cycle's handle.
            self._pending_completions: deque = deque()
            # Completion signal subscription (doc08 §5 step7): parse -> enqueue -> (loop) drain
            # -> guarded mark_succeeded/failed -> re-trigger. 0 actuation.
            self.create_subscription(String, _GOAL_RESULT_TOPIC, self._on_goal_result_msg, 10)
            request_src = "fixture" if self._request is not None else "none"
            # er_source surfaces which plan source the factory built (docs/dev/08 追補 2 step 3:
            # the operator checks `er_source=offline_replay` before a free G5 run; `live` means
            # the overlay was not picked up). resolve_er_offline_payload_path already ran inside
            # build_er_adapter above — this re-resolution is pure and cannot diverge from it.
            er_source = (
                "offline_replay" if resolve_er_offline_payload_path(cfg) is not None else "live"
            )
            self.get_logger().info(
                f"x_er_bridge ready (composition preflight passed, er_source={er_source}, "
                f"request_source={request_src}, out_dir={self._runtime.out_dir})"
            )

        def _sweep_emergency_mirror(self) -> None:
            """Clear mirrored estops whose level signal has gone silent (timer cb)."""
            self._emergency_mirror.sweep(time.monotonic())

        # ── cycle loop (background thread + dedicated event loop, llm_bridge.py:254-297) ──

        def _run_loop(self) -> None:
            asyncio.set_event_loop(self._loop)
            with contextlib.suppress(asyncio.CancelledError):
                self._loop.run_until_complete(self._run_cycles())

        async def _run_cycles(self) -> None:
            """Drive ``run_x_er_cycle`` while a request is pending (docs/mode-x-er/08 §5).

            Per iteration: (1) DRAIN queued completions (apply mark_succeeded/failed BETWEEN
            cycles, single handle) -> (2) run one cycle -> (3) fold its committed pairs -> (4)
            park on the trigger. ``compile_raw_output`` is one-shot ready-tasks-only, so t2
            becomes ready only after t1's completion is applied in step (1) of the next
            iteration (the completion callback sets ``_cycle_trigger`` to wake the park). The
            loop parks instead of busy-re-offering the same ready set (docs/mode-x-er/08 §5
            step7).
            """
            if self._request is None:
                self.get_logger().info(
                    "x_er_bridge idle: mode_x_er.request_fixture unset (v0 request source)"
                )
                return
            while not self._stop_requested.is_set():
                # (1) Apply completions queued since the last cycle, on THIS loop thread, before
                # any cycle handle is live — this is the race fix (executor.py:157-163).
                self._drain_completions()
                try:
                    outcome = await run_x_er_cycle(
                        request=self._request,
                        adapter=self._adapter,
                        runtime=self._runtime,
                        executor=self._task_executor,
                        gen_store=self._gen_store,
                        tool_executor=self._tool_executor,
                        runtime_state_source=self._runtime_state_source,
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
                    # map; refusals = an unsupported same-robot-concurrent plan shape (logged
                    # loudly, correlation kept for the earlier task — never silently mis-marked).
                    for robot, node_id in fold_inflight(self._inflight, outcome.committed):
                        self.get_logger().error(
                            f"x_er_bridge: robot {robot!r} already has in-flight task "
                            f"{self._inflight[robot]!r}; by-robot correlation cannot track a "
                            f"second concurrent same-robot task {node_id!r} (unsupported plan "
                            "shape) — its completion will be ignored"
                        )
                    self.get_logger().info(
                        f"x_er_bridge cycle dispatched {len(outcome.dispatched)} tool call(s)"
                    )
                await self._wait_for_cycle_trigger()

        def _drain_completions(self) -> None:
            """Apply all queued completions on the loop thread (BETWEEN cycles). Race fix:
            single live handle at a time (x_er_completion.apply_pending_completions)."""
            for outcome in apply_pending_completions(
                self._pending_completions,
                plan_id=self._plan_id,
                inflight=self._inflight,
                executor=self._task_executor,
            ):
                self.get_logger().info(f"x_er_bridge completion applied: {outcome.reason}")

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

            Parses the ``std_msgs/String`` JSON, ENQUEUES a valid completion (``deque.append``
            is atomic across threads), and wakes the cycle loop. It does NOT touch the executor
            here — the store transition is applied by ``_drain_completions`` on the loop thread
            BETWEEN cycles, so it never opens a second live handle racing an in-flight cycle
            (executor.py:157-163). A malformed payload is dropped (fail-closed, doc08 §6).
            """
            goal_result = parse_goal_result(msg.data)
            if goal_result is None:
                self.get_logger().warning(
                    "x_er_bridge: ignoring malformed /nav2_bridge/goal_result payload"
                )
                return
            self._pending_completions.append(goal_result)
            with contextlib.suppress(RuntimeError):  # loop already stopped (idle node)
                self._loop.call_soon_threadsafe(self._cycle_trigger.set)

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
