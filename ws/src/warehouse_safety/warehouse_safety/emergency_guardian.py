"""Emergency Guardian — 50ms reflex safety node (doc12:95-151). LLM-independent.

On a 50ms timer it estops on inter-robot proximity / critical battery and
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

import contextlib
import gc
import json
import time
from datetime import UTC, datetime

import rclpy
from action_msgs.srv import CancelGoal
from geometry_msgs.msg import PoseWithCovarianceStamped, Twist
from rclpy.client import Client
from rclpy.node import Node
from rclpy.publisher import Publisher
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import BatteryState
from std_msgs.msg import String
from warehouse_interfaces.config import load_config
from warehouse_interfaces.safety import BATTERY_PERCENTAGE_SCALE_DEFAULT, validate_battery_scale

from warehouse_safety import guard_logic as gl

_BOTS: tuple[str, ...] = ("bot1", "bot2")


class EmergencyGuardian(Node):
    def __init__(self) -> None:
        super().__init__("emergency_guardian")
        cfg = load_config()
        # Tunables read from config (NOT hardcoded). emergency_min_distance is the
        # inter-robot collision distance — a different concept from the speed cap;
        # blocked_timeout is added to config/warehouse.base.yaml by this track.
        dist = cfg["safety"]["emergency_min_distance"]
        blocked = cfg["safety"]["blocked_timeout"]
        self._dist_threshold = self.declare_parameter("emergency_min_distance", dist).value
        self._blocked_timeout = self.declare_parameter("blocked_timeout", blocked).value
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
        # #126 edge-trigger: latch active (bot, reason) alarms so /emergency/event
        # fires on the rising edge only (the physical stop below stays level).
        self._latch = gl.EdgeLatch()
        self._pose: dict[str, tuple[float, float] | None] = {b: None for b in _BOTS}
        self._battery: dict[str, float | None] = {b: None for b in _BOTS}
        self._blocked: dict[str, float] = {b: 0.0 for b in _BOTS}

        sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST, depth=1
        )
        # ros2.md: set QoS explicitly. The safety-critical stop / event publishers
        # are RELIABLE so a dropped estop Twist or event is retried.
        reliable_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE, history=HistoryPolicy.KEEP_LAST, depth=10
        )

        self._cmd_pub: dict[str, Publisher] = {}
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
            # Stop goes to /cmd_vel/emergency (twist_mux prio 100), never /cmd_vel (doc15).
            self._cmd_pub[bot] = self.create_publisher(
                Twist, f"/{bot}/cmd_vel/emergency", reliable_qos
            )
            self._cancel_cli[bot] = self.create_client(
                CancelGoal, f"/{bot}/navigate_to_pose/_action/cancel_goal"
            )

        self._event_pub = self.create_publisher(String, "/emergency/event", reliable_qos)
        self.create_timer(0.05, self._check_safety)  # 50ms reflex
        self.get_logger().info("emergency_guardian running (50ms reflex)")

    # --- callbacks: store latest only, no logic ---
    def _on_pose(self, bot: str, msg: PoseWithCovarianceStamped) -> None:
        p = msg.pose.pose.position
        self._pose[bot] = (p.x, p.y)
        self._blocked[bot] = self._tracker.update(bot, p.x, p.y, time.monotonic())

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
        a = gl.BotState("bot1", *self._xy("bot1"), self._battery["bot1"], self._blocked["bot1"])
        b = gl.BotState("bot2", *self._xy("bot2"), self._battery["bot2"], self._blocked["bot2"])
        decisions = gl.evaluate(
            a, b, distance_threshold=self._dist_threshold, blocked_timeout=self._blocked_timeout
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

    def _xy(self, bot: str) -> tuple[float | None, float | None]:
        p = self._pose[bot]
        return (p[0], p[1]) if p is not None else (None, None)

    def _emergency_stop(self, dec: gl.Decision, *, emit_event: bool) -> None:
        # Physical stop is HELD on every tick the condition is active (the twist_mux
        # prio-100 emergency input ages out after its 0.5s timeout, so it must be
        # re-asserted) — independent of the event edge-trigger below.
        self._cancel_all_goals(dec.bot)  # async, never blocks the 50ms timer
        self._cmd_pub[dec.bot].publish(Twist())  # all-zero stop (twist_mux prio 100)
        # TODO(Mode-A): also abort character-LLM negotiation -> /negotiation when
        # that contract lands (doc14); the topic does not exist yet, so defer.
        if emit_event:  # #126: rising edge only (doc12 edge-trigger); shape unchanged
            self._publish_event(dec, action_taken=["nav2_goal_cancel", "cmd_vel_stop"])

    def _trigger_recovery(self, dec: gl.Decision, *, emit_event: bool) -> None:
        # Low-harm: a structured event only (the bot may be legitimately idle). Edge-
        # triggered too, so a sustained blocked-timeout does not re-spam at 20Hz.
        if emit_event:
            self._publish_event(dec, action_taken=["nav2_recovery"])

    def _publish_event(self, dec: gl.Decision, action_taken: list[str]) -> None:
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

    # --- Nav2 cross-process cancel: cancel ALL goals, crash-safe with no server ---
    def _cancel_all_goals(self, bot: str) -> None:
        cli = self._cancel_cli[bot]
        if not cli.service_is_ready():
            # No Nav2 server (e.g. dev) -> skip. Never wait_for_service in the 50ms loop.
            return
        # Zeroed goal_info (empty uuid + zero stamp) == "cancel all goals" for any
        # ROS 2 action server. Fire-and-forget: do not await the future here.
        cli.call_async(CancelGoal.Request())


def main() -> None:
    rclpy.init()
    node = EmergencyGuardian()
    gc.disable()  # R-40: reduce GC jitter on the 50ms loop (best-effort)
    gc.freeze()  # promote setup objects to the permanent generation
    try:
        with contextlib.suppress(KeyboardInterrupt):
            rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
