"""Emergency Guardian — 50ms reflex safety node (doc12:95-151). LLM-independent.

On a 50ms timer it estops on inter-robot proximity / critical battery / stale
localization (#126 + doc23 A-5③ displacement gate) / a LATCHED operator stop
request (``/operator/stop_request`` engage/clear JSON, doc05 §5 / OQ-OP2), and
triggers a (low-harm) recovery event on blocked-timeout. An estop cancels Nav2
goals, publishes a zero ``Twist`` to ``/{bot}/cmd_vel/emergency`` (twist_mux
priority 100 — never ``/{bot}/cmd_vel`` directly, which races Nav2, doc15) and
publishes a structured ``/emergency/event``. The Twist stop is re-asserted every
tick a condition holds (level); the event is edge-triggered (#126, gl.EdgeLatch)
so a sustained condition does not re-spam ``/emergency/event`` at 20Hz.

All decisions live in the rclpy-free ``guard_logic`` module (unit-testable
without ROS, doc16 §11); this node only marshals ROS and performs side effects.

Caveats:
- R-39: ``/{bot}/amcl_pose`` is 5-10 Hz, so the "50ms reflex" is effectively
  100-200 ms stale; the ESP32 Layer 0 (and ``/scan``, a Phase-2 seam not yet
  subscribed here) is the true 2-bot proximity owner.
- R-40: ``gc.disable()`` / ``gc.freeze()`` in ``main()`` is best-effort jitter
  control; the ESP32 Layer 0 is the final physical-stop guarantee.
"""

import gc
import json
import time
from datetime import datetime

import rclpy
from action_msgs.srv import CancelGoal
from geometry_msgs.msg import PoseWithCovarianceStamped, Twist
from nav_msgs.msg import Odometry
from rclpy.client import Client
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.publisher import Publisher
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import BatteryState
from std_msgs.msg import String
from warehouse_interfaces.compat import UTC
from warehouse_interfaces.config import load_config
from warehouse_interfaces.safety import BATTERY_PERCENTAGE_SCALE_DEFAULT, validate_battery_scale

from warehouse_safety import guard_logic as gl

_BOTS: tuple[str, ...] = ("bot1", "bot2")

#: doc05 §4-1 producer window: how far ahead each published ``/bot{n}/stop_state``
#: deadline sits on the shared CLOCK_MONOTONIC (``valid_until = now + this``). The
#: consumer clips any accepted deadline to its OWN ``stop_state_max_validity_s``
#: ceiling (``warehouse_m1_driver.stop_state.DEFAULT_STOP_STATE_MAX_VALIDITY_S`` = 0.5,
#: itself borrowed from the frozen twist_mux ``emergency`` input timeout in
#: ``warehouse_bringup/config/twist_mux.yaml``). Matching that 0.5s here keeps the
#: producer's deadline at (just under) the consumer ceiling in normal operation —
#: producer_now <= consumer_now on the shared clock, so ``min()`` never clips it, and
#: clip detection stays quiet (doc05 §4-1 "壁時計 producer は上限クリップに吸収されて静か
#: に縮退する" — a monotonic producer never trips it). This is a BORROW, not a derivation
#: — # TODO(Phase 1 実測). Do not invent a separate literal.
DEFAULT_STOP_STATE_VALID_WINDOW_S: float = 0.5


class EmergencyGuardian(Node):
    def __init__(self) -> None:
        super().__init__("emergency_guardian")
        cfg = load_config()
        # Tunables read from config (NOT hardcoded). emergency_min_distance is the
        # inter-robot collision distance — a different concept from the speed cap;
        # blocked_timeout is added to config/warehouse.base.yaml by this track.
        dist = cfg["safety"]["emergency_min_distance"]
        blocked = cfg["safety"]["blocked_timeout"]
        # #126 freshness guard: amcl_pose staleness window. Past it the bot is
        # navigating with an unknown position -> precautionary estop (doc12 §freshness).
        freshness = cfg["safety"]["pose_freshness_timeout"]
        self._dist_threshold = self.declare_parameter("emergency_min_distance", dist).value
        self._blocked_timeout = self.declare_parameter("blocked_timeout", blocked).value
        self._freshness_timeout = self.declare_parameter("pose_freshness_timeout", freshness).value
        # doc23 A-5③ displacement gate: AMCL is motion-gated, so a PARKED bot's
        # /amcl_pose silence is normal and used to false-fire pose_stale (OQ-11,
        # doc23:394-399). Independent wheel odom decides whether the bot could be
        # moving. Hard-indexed (not .get) so a missing key fails the node LOUDLY at
        # startup rather than silently reverting the guard's behaviour.
        self._gate_motion_eps = self.declare_parameter(
            "pose_freshness_motion_epsilon", cfg["safety"]["pose_freshness_motion_epsilon"]
        ).value
        self._gate_angular_eps = self.declare_parameter(
            "pose_freshness_angular_epsilon", cfg["safety"]["pose_freshness_angular_epsilon"]
        ).value
        # Odom's own staleness window: past it the gate input is unknown -> fail-closed.
        self._odom_freshness_timeout = self.declare_parameter(
            "odom_freshness_timeout", cfg["safety"]["odom_freshness_timeout"]
        ).value
        # #44: explicit battery driver scale, shared with State Cache via
        # warehouse_interfaces.safety so this reflex and the snapshot never diverge.
        scale = cfg["safety"].get("battery_percentage_scale", BATTERY_PERCENTAGE_SCALE_DEFAULT)
        # #44: validate at startup so a typo'd scale (config or `-p` override) fails fast
        # — the node refuses to start — instead of silently disabling the battery estop
        # (an unknown scale would raise on every reading and _on_battery would suppress it,
        # leaving battery None = unknown = no estop = fail-OPEN).
        self._battery_scale = validate_battery_scale(
            self.declare_parameter("battery_percentage_scale", scale).value
        )

        self._seq = 0
        self._tracker = gl.BlockTracker()
        # doc23 A-5③: accumulates odom travel since the last /amcl_pose arrival.
        self._gate = gl.PoseGateTracker()
        # #126 edge-trigger: latch active (bot, reason) alarms so /emergency/event
        # fires on the rising edge only (the physical stop below stays level).
        self._latch = gl.EdgeLatch()
        self._pose: dict[str, tuple[float, float] | None] = {b: None for b in _BOTS}
        self._battery: dict[str, float | None] = {b: None for b in _BOTS}
        self._blocked: dict[str, float] = {b: 0.0 for b in _BOTS}
        # #126 freshness: monotonic arrival time of the latest /amcl_pose per bot
        # (None until the first pose) -> pose_age computed in _check_safety.
        self._last_pose_t: dict[str, float | None] = {b: None for b in _BOTS}

        sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST, depth=1
        )
        # ros2.md: set QoS explicitly. The safety-critical stop / event publishers
        # are RELIABLE so a dropped estop Twist or event is retried.
        reliable_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE, history=HistoryPolicy.KEEP_LAST, depth=10
        )
        # doc05 §4-1 stop-overlay feed (/bot{n}/stop_state): its OWN profile, NOT the
        # depth-10 estop/event profile above. RELIABLE so a dropped state is retried;
        # KEEP_LAST/depth 1 because only the latest state matters (this is a level
        # channel re-published every tick, not a history to replay); VOLATILE because
        # transient_local would redeliver a stale — possibly permissive — deadline to a
        # late-joining driver (fail-OPEN, doc05 §4-1 "transient_local は使わない").
        stop_state_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            durability=DurabilityPolicy.VOLATILE,
        )

        self._cmd_pub: dict[str, Publisher] = {}
        self._stop_state_pub: dict[str, Publisher] = {}
        self._cancel_cli: dict[str, Client] = {}
        for bot in _BOTS:
            # b=bot binds the loop variable per-callback (late-binding closure pitfall).
            self.create_subscription(
                PoseWithCovarianceStamped,
                f"/{bot}/amcl_pose",
                lambda msg, b=bot: self._on_pose(b, msg),
                sensor_qos,
            )
            self.create_subscription(
                BatteryState,
                f"/{bot}/battery",
                lambda msg, b=bot: self._on_battery(b, msg),
                sensor_qos,
            )
            # doc23 A-5③ gate input. /{bot}/odom is an existing doc03:77 contract
            # topic (nav_msgs/Odometry) — an INDEPENDENT source from AMCL, which is
            # what makes it a valid witness that the bot is (not) moving.
            self.create_subscription(
                Odometry,
                f"/{bot}/odom",
                lambda msg, b=bot: self._on_odom(b, msg),
                sensor_qos,
            )
            # Stop goes to /cmd_vel/emergency (twist_mux prio 100), never /cmd_vel (doc15).
            self._cmd_pub[bot] = self.create_publisher(
                Twist, f"/{bot}/cmd_vel/emergency", reliable_qos
            )
            # doc05 §4-1 producer: per-bot stop-overlay feed for the L0' driver. Own QoS
            # profile (RELIABLE/KEEP_LAST/depth1/VOLATILE), never the estop depth-10 one.
            self._stop_state_pub[bot] = self.create_publisher(
                String, f"/{bot}/stop_state", stop_state_qos
            )
            self._cancel_cli[bot] = self.create_client(
                CancelGoal, f"/{bot}/navigate_to_pose/_action/cancel_goal"
            )

        self._event_pub = self.create_publisher(String, "/emergency/event", reliable_qos)
        # Abort an in-flight character-LLM negotiation on estop (doc14:241-247 R2 / doc03:108).
        # Published only on the physical emergency-STOP, NOT on low-harm recovery (see _publish_event).
        self._negotiation_abort_pub = self.create_publisher(
            String, "/negotiation/abort", reliable_qos
        )
        # Operator emergency-stop request (doc05 §5): a fleet-wide LATCH — engage
        # holds the estop for ALL bots until an explicit clear; silence never clears.
        # RELIABLE/KEEP_LAST(10): no comparable command-ish subscription exists here
        # (all sensor subs are BEST_EFFORT) and a dropped engage/clear must be
        # retried, so it reuses the safety-critical reliable_qos profile above.
        self._op_latch = gl.OperatorStopLatch()
        self.create_subscription(
            String, "/operator/stop_request", self._on_operator_stop, reliable_qos
        )
        self.create_timer(0.05, self._check_safety)  # 50ms reflex
        self.get_logger().info("emergency_guardian running (50ms reflex)")

    # --- callbacks: store latest only, no logic ---
    def _on_pose(self, bot: str, msg: PoseWithCovarianceStamped) -> None:
        now = time.monotonic()
        p = msg.pose.pose.position
        self._pose[bot] = (p.x, p.y)
        self._last_pose_t[bot] = now  # #126 freshness: stamp arrival (wall-monotonic)
        self._blocked[bot] = self._tracker.update(bot, p.x, p.y, now)
        self._gate.on_pose(bot)  # doc23 A-5③: pose arrived -> gate accumulators reset

    def _on_odom(self, bot: str, msg: Odometry) -> None:
        # doc23 A-5③ gate input: marshal only. All accumulation / staleness /
        # non-finite handling lives in the rclpy-free gl.PoseGateTracker (R-26).
        # The twist is NOT read: the gate is displacement-only (doc23:349) — an
        # instantaneous-speed term deadlocked departures in the 2026-08-17 sim run.
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        self._gate.on_odom(
            bot, p.x, p.y, gl.yaw_from_quaternion(q.x, q.y, q.z, q.w), time.monotonic()
        )

    def _on_battery(self, bot: str, msg: BatteryState) -> None:
        # #44: marshal via the rclpy-free, unit-tested gl.marshal_battery (single
        # shared normalizer + configured scale) so this 50ms reflex and the State
        # Cache agree and battery_is_critical sees a 0..100 percent. Non-finite ->
        # keep last good (sticky-stop); guard_logic treats None as unknown (no estop).
        self._battery[bot] = gl.marshal_battery(
            self._battery[bot], msg.percentage, self._battery_scale
        )

    # --- 50ms timer: pure decide + side effects ---
    def _check_safety(self) -> None:
        now = time.monotonic()  # one clock for both bots' pose_age (#126 freshness)
        a = self._bot_state("bot1", now)
        b = self._bot_state("bot2", now)
        decisions = gl.evaluate(
            a,
            b,
            distance_threshold=self._dist_threshold,
            blocked_timeout=self._blocked_timeout,
            pose_freshness_timeout=self._freshness_timeout,
            pose_gate_motion_epsilon=self._gate_motion_eps,
            pose_gate_angular_epsilon=self._gate_angular_eps,
        )
        # #126 edge-trigger: only NEWLY-active (bot, reason) alarms emit an
        # /emergency/event — a held condition must not re-spam the LLM-review stream
        # at 20Hz. The physical stop stays level: _emergency_stop re-asserts the zero
        # Twist on every tick regardless (twist_mux prio-100 input expires after 0.5s).
        rising = self._latch.rising(decisions)
        for dec in decisions:
            emit_event = (dec.bot, dec.reason) in rising
            if dec.action == "estop":
                self._emergency_stop(dec, emit_event=emit_event)
            else:
                self._trigger_recovery(dec, emit_event=emit_event)
        # doc05 §4-1 producer: publish the stop-overlay feed for EVERY bot on EVERY tick
        # (stop-requested or not), derived from THIS tick's `decisions` and the SAME
        # monotonic `now` sampled above. Kept AFTER the estop loop so it can never delay
        # or suppress a physical stop; purely additive to the existing estop path.
        self._publish_stop_state(decisions, now)

    def _bot_state(self, bot: str, now: float) -> gl.BotState:
        # #126: pose_age = now - last /amcl_pose arrival (None until the 1st pose,
        # so the pure logic never estops a not-yet-localized bot). Trailing arg =
        # doc05 §5 operator latch, fed IDENTICALLY to every bot (fleet-wide stop).
        last_t = self._last_pose_t[bot]
        pose_age = None if last_t is None else now - last_t
        x, y = self._xy(bot)
        # doc23 A-5③: None pair when odom is absent / stale -> gate fails closed.
        disp, dyaw = self._gate.snapshot(bot, now, stale_after=self._odom_freshness_timeout)
        batt, blocked = self._battery[bot], self._blocked[bot]
        return gl.BotState(bot, x, y, batt, blocked, pose_age, disp, dyaw, self._op_latch.engaged)

    def _xy(self, bot: str) -> tuple[float | None, float | None]:
        p = self._pose[bot]
        return (p[0], p[1]) if p is not None else (None, None)

    def _emergency_stop(self, dec: gl.Decision, *, emit_event: bool) -> None:
        # Physical stop is HELD on every tick the condition is active (the twist_mux
        # prio-100 emergency input ages out after its 0.5s timeout, so it must be
        # re-asserted) — independent of the event edge-trigger below.
        self._cancel_all_goals(dec.bot)  # async, never blocks the 50ms timer
        self._cmd_pub[dec.bot].publish(Twist())  # all-zero stop (twist_mux prio 100)
        if emit_event:  # #126: rising edge only (doc12 edge-trigger); shape unchanged
            # is_estop=True -> also fire /negotiation/abort (doc14:241-247 R2): a physical
            # emergency stop aborts any in-flight character-LLM negotiation + discards its proposal.
            self._publish_event(
                dec, action_taken=["nav2_goal_cancel", "cmd_vel_stop"], is_estop=True
            )

    def _trigger_recovery(self, dec: gl.Decision, *, emit_event: bool) -> None:
        # Low-harm: a structured event only (the bot may be legitimately idle). Edge-
        # triggered too, so a sustained blocked-timeout does not re-spam at 20Hz.
        if emit_event:
            self._publish_event(dec, action_taken=["nav2_recovery"])

    def _publish_stop_state(self, decisions: list[gl.Decision], now: float) -> None:
        """doc05 §4-1 producer: publish ``/{bot}/stop_state`` for every bot this tick.

        Periodic (every 50ms tick, stop-requested or not) so the L0' stop overlay has a
        live feed whose very silence is a fault (fail-closed freshness, §4-1). The stop
        flag is derived per bot from the SAME tick's ``decisions`` via the pure,
        unit-tested ``gl.stop_requested_for``, so it is physically synchronised with the
        estop ``_emergency_stop`` asserts above (recovery decisions are low-harm and do
        NOT request a stop). ``valid_until`` reuses the SINGLE ``now`` the caller already
        sampled from ``time.monotonic()`` (§4-1 CLOCK_MONOTONIC — never a wall clock nor
        a fresh sample), so across ticks the deadline is monotonically non-decreasing.
        The payload is a 2-key, always-serialisable dict and this runs AFTER the estop
        assertion, so it can neither delay nor suppress a physical stop.
        """
        valid_until = now + DEFAULT_STOP_STATE_VALID_WINDOW_S
        for bot in _BOTS:
            payload = {
                "stop_requested": gl.stop_requested_for(decisions, bot),
                "valid_until": valid_until,
            }
            self._stop_state_pub[bot].publish(String(data=json.dumps(payload)))

    def _publish_event(
        self, dec: gl.Decision, action_taken: list[str], *, is_estop: bool = False
    ) -> None:
        self._seq += 1
        # One clock for both the id prefix and the timestamp field (UTC epoch) so
        # they always agree for log correlation.
        now = time.time()
        event_id = (
            f"emg-{datetime.fromtimestamp(now, UTC).strftime('%Y%m%d%H%M%S')}-{self._seq:04d}"
        )
        event = gl.build_event(
            event_id, dec.bot, dec.reason, now, action_taken=action_taken, detail=dec.detail
        )
        self._event_pub.publish(String(data=json.dumps(event)))
        # doc14:241-247 R2 — on a physical emergency stop only, abort any in-flight character-LLM
        # negotiation (same event_id correlates the two, doc03:108). Recovery events do NOT abort
        # (a blocked-timeout is the deadlock fallback the negotiation resolves, doc08a:363-372).
        if is_estop:
            abort = gl.build_abort(dec.reason, dec.bot, event_id)
            self._negotiation_abort_pub.publish(String(data=json.dumps(abort)))

    # --- Nav2 cross-process cancel: cancel ALL goals, crash-safe with no server ---
    def _cancel_all_goals(self, bot: str) -> None:
        cli = self._cancel_cli[bot]
        if not cli.service_is_ready():
            # No Nav2 server (e.g. dev) -> skip. Never wait_for_service in the 50ms loop.
            return
        # Zeroed goal_info (empty uuid + zero stamp) == "cancel all goals" for any
        # ROS 2 action server. Fire-and-forget: do not await the future here.
        cli.call_async(CancelGoal.Request())

    # --- operator stop request (doc05 §5): marshal payloads into the pure latch ---
    def _on_operator_stop(self, msg: String) -> None:
        # All parse/latch semantics live in the rclpy-free gl.OperatorStopLatch
        # (R-26). An invalid payload returns None = IGNORED, never a clear
        # (fail-safe); it is logged loudly so a wedged producer is visible.
        action = self._op_latch.feed(msg.data)
        if action is None:
            self.get_logger().warning(
                f"ignoring invalid /operator/stop_request payload: {msg.data[:200]!r}"
            )
        else:
            latched = self._op_latch.engaged
            self.get_logger().info(f"operator stop request: {action} (latched={latched})")


def main() -> None:
    rclpy.init()
    node: EmergencyGuardian | None = None
    try:
        # Constructed inside the try (as node_runtime.run_node does): a refused start
        # (validate_battery_scale) still reaches try_shutdown() and still exits non-zero.
        node = EmergencyGuardian()
        gc.disable()  # R-40: reduce GC jitter on the 50ms loop (best-effort)
        gc.freeze()  # promote setup objects to the permanent generation
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        # SIGINT -> KeyboardInterrupt; SIGTERM -> ExternalShutdownException (Humble tears the
        # context down before this finally runs). Both are a normal stop, not a failure.
        # Measured on the entry point: the textbook pattern exited 1 on both signals. Under
        # warehouse-safety.service (Type=simple) that is journalled as status=1/FAILURE on a
        # routine stop, and off the stop path Restart=on-failure + StartLimitIntervalSec=0
        # would restart L1 for nothing. (What systemd sees through the unit's `ros2 run`
        # wrapper is a #634 board item.) Nothing here is a safety guarantee -- the estop
        # authority is the level-re-asserted twist_mux prio-100 zero on the 50ms loop,
        # never a message published on the way out.
        pass
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
