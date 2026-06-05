"""Sim battery publisher node — feeds ``/bot{n}/battery`` so bots reach the situation JSON.

Rationale + the split-brain-proof design live in :mod:`warehouse_sim.battery` (#44 / #156 /
doc03 §トピック設計 / doc12:207). This node only marshals: read the robot list and the
SINGLE battery scale from config — the same ``safety.battery_percentage_scale`` key the
State Cache (``warehouse_state.state_cache``) and Emergency Guardian
(``warehouse_safety.emergency_guardian``) read — validate it fail-fast
(``validate_battery_scale``: a typo refuses to start rather than silently publishing an
out-of-band value), then publish a deterministic ``BatteryState`` per bot.

All numeric logic is in the rclpy-free :class:`warehouse_sim.battery.BatteryDrainModel`
so it is unit-testable without ROS (doc16 §11); this node only wraps it.
"""

import contextlib
import math

import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import BatteryState
from warehouse_interfaces.config import load_config
from warehouse_interfaces.safety import (
    BATTERY_PERCENTAGE_SCALE_DEFAULT,
    validate_battery_scale,
)

from warehouse_sim.battery import BatteryDrainModel

_NOMINAL_VOLTAGE = 11.1  # 3S Li-ion nominal; cosmetic — consumers read only ``percentage``.


class SimBatteryPublisher(Node):
    """Publish a synthetic ``BatteryState`` per configured bot on ``/{bot}/battery``."""

    def __init__(self) -> None:
        super().__init__("sim_battery_publisher")
        cfg = load_config()
        # The producer derives its bots from config (single source). The two CONSUMERS that
        # gate the situation JSON hardcode _BOTS=("bot1","bot2") — warehouse_state.state_cache
        # and warehouse_safety.emergency_guardian, per doc12 — and agree today (config IS
        # bot1,bot2). If a config robot id is ever added/renamed, those consumer _BOTS must be
        # updated too, or the new bot's /battery is published but never subscribed and the bot
        # is silently dropped from the situation JSON (aggregator._is_complete stays False).
        self._bots = [r["id"] for r in cfg["robots"]]

        # #44: the ONE battery scale source — the same config key the State Cache and the
        # Emergency Guardian read — so the producer (sim) and the two consumers can never
        # normalize differently (split-brain). validate_battery_scale fails fast on a typo
        # (loud refuse-to-start), exactly like the two consuming nodes do.
        scale = cfg.get("safety", {}).get(
            "battery_percentage_scale", BATTERY_PERCENTAGE_SCALE_DEFAULT
        )
        self._scale = validate_battery_scale(
            self.declare_parameter("battery_percentage_scale", scale).value
        )

        self._model = BatteryDrainModel(
            initial_pct=float(self.declare_parameter("initial_percent", 100.0).value),
            drain_pct_per_min=float(self.declare_parameter("drain_percent_per_minute", 1.0).value),
            floor_pct=float(self.declare_parameter("floor_percent", 60.0).value),
        )
        rate_hz = float(self.declare_parameter("publish_rate_hz", 1.0).value)
        if not math.isfinite(rate_hz) or rate_hz <= 0.0:
            # fail-fast (parity with BatteryDrainModel / validate_battery_scale): a 0 or
            # non-finite rate would ZeroDivisionError / break the timer period below.
            raise ValueError(f"publish_rate_hz must be finite and > 0; got {rate_hz}")

        # battery feeds the critical-battery estop (warehouse_safety, 50ms reflex): publish
        # RELIABLE so no reading is dropped. RELIABLE offered is compatible with the
        # BEST_EFFORT subscribers in state_cache.py:75 and emergency_guardian.py:104.
        qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE, history=HistoryPolicy.KEEP_LAST, depth=10
        )
        self._pubs = {
            bot: self.create_publisher(BatteryState, f"/{bot}/battery", qos) for bot in self._bots
        }

        # Captured lazily on the first tick: under use_sim_time the clock reads 0 until gz
        # /clock starts, so anchoring elapsed time here keeps the drain on the sim timeline.
        self._start = None
        self.create_timer(1.0 / rate_hz, self._tick)
        self.get_logger().info(
            f"sim_battery_publisher: {len(self._bots)} bots, scale={self._scale}, "
            f"{self._model.initial_pct:.0f}%→floor {self._model.floor_pct:.0f}% "
            f"@ {self._model.drain_pct_per_min:.2f}%/min, {rate_hz:.1f}Hz"
        )

    def _tick(self) -> None:
        now = self.get_clock().now()
        if self._start is None:
            self._start = now
        elapsed_s = (now - self._start).nanoseconds / 1e9
        raw = self._model.raw_at(elapsed_s, self._scale)
        for bot in self._bots:
            msg = BatteryState()
            msg.header.stamp = now.to_msg()
            msg.header.frame_id = bot
            msg.percentage = raw  # the only field consumers read; in the config scale (#44)
            msg.voltage = _NOMINAL_VOLTAGE
            msg.present = True
            msg.power_supply_status = BatteryState.POWER_SUPPLY_STATUS_DISCHARGING
            msg.power_supply_health = BatteryState.POWER_SUPPLY_HEALTH_GOOD
            msg.power_supply_technology = BatteryState.POWER_SUPPLY_TECHNOLOGY_LION
            self._pubs[bot].publish(msg)


def main() -> None:
    rclpy.init()
    node = SimBatteryPublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        with contextlib.suppress(Exception):
            rclpy.shutdown()


if __name__ == "__main__":
    main()
