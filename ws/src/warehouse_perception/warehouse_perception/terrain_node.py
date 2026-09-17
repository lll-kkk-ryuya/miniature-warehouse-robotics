"""rclpy shell for the 07_Output_Adapters terrain publisher (``terrain_publisher``).

Publishes the two 04_Perception terrain outputs on RELATIVE topic names, so they
resolve under the robot namespace (``/bot1/cliff_scan`` and
``/bot1/terrain/coverage``; an absolute name would cross namespaces):

* ``cliff_scan`` — ``sensor_msgs/LaserScan`` carrying ONLY ``DROP_DETECTED``
  cells, the VirtualScan pattern reused for a negative obstacle
  (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:56``). ``UNKNOWN``
  never becomes a wall (``:173``): unobserved area leaves through the other
  topic instead.
* ``terrain/coverage`` — ``std_msgs/String`` holding
  ``TerrainCoverage.model_dump_json()``, the frozen 追補 ④ contract, which is
  what carries ``UNKNOWN``, the observed extent and the quality self-report.

Both are stamped with the INCOMING depth frame's own ``header.stamp`` — an old
depth frame is never re-stamped with "now" (``:177``) — and both are emitted
exactly once per depth frame, with no timer: re-publishing would advertise a
stale observation as a fresh one. A silent ``cliff_scan`` is therefore normal
when no frames arrive, and noticing that silence is X2's job, not this node's
(``:66`` / ``docs/mode-outdoor/09-external-review-v3-response.md`` §2-b).

Safe-OFF and fail-closed, the ``speed_band_node`` / ADR-0012 Decision 4 shape:
``enabled`` defaults to False and the node then creates no subscription, no
publisher and no timer; with ``enabled:=true`` every geometric and threshold
parameter must be injected, because each one is declared with a value that
cannot pass validation (``0.0`` / ``0`` / ``""``) and ``params_from_mapping``
aborts startup rather than guessing. The node also refuses to start outside a
robot namespace, since the scan's ``frame_id`` would then name no robot.

This node is a producer for the L1 costmap / L1 collision_monitor (cliff_scan)
and for X2 / 06 (coverage). Layer: **自律走行（安全層外）** — it holds no
actuation authority and publishes nothing on ``cmd_vel``, stop or speed topics
(``:189`` / ``:512``). Wiring (launch, ``perception.terrain.*`` config, the
consumer-side costmap layer and ``sensor_health`` mapping) is a follow-up
slice; see the package CLAUDE.md and 追補 ⑨ hand-offs.
"""

from __future__ import annotations

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo, Image, LaserScan
from std_msgs.msg import String
from warehouse_description.robot_dimensions import BASE_FRAME

from warehouse_perception.terrain_core import (
    CameraIntrinsics,
    TerrainObservation,
    analyze_depth_frame,
)
from warehouse_perception.terrain_node_core import (
    PARAM_KEYS,
    coverage_json,
    decode_depth_image,
    intrinsics_from_camera_info,
    laser_scan_fields,
    params_from_mapping,
    processing_latency_s,
    resolve_frame_id,
    stamp_to_seconds,
)

# Declared values that CANNOT pass validation, by parameter name. This is the
# fail-closed startup contract (ADR-0012 Decision 4): the geometry of a camera
# that has not been mounted, let alone measured, has no defensible default
# (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:181`` /
# 追補 ⑤ §2), so "not injected" must abort instead of guessing. ``0.0`` is
# rejected by ``_positive``, ``0`` by ``_int_at_least(..., 1)`` and ``""`` by the
# non-empty check.
#
# FOUR of them are sentinels whose value is ALSO legal, and that is stated here
# rather than hidden (``OQ-OD4Y-i2``, 追補 ⑨): ``reference_offset_m`` (a camera
# mounted exactly over the datum has offset 0 — the sign is deliberately
# unconstrained), ``angle_min_rad`` / ``angle_max_rad`` (0 is a valid bearing
# bound, though the PAIR fails because ``angle_max > angle_min`` is required)
# and ``ransac_seed`` (every int is a legal seed; its group is gated by
# ``ransac_iterations``). Injecting NOTHING still aborts — ``camera_height_m``
# alone sees to that — so the startup gate holds; what these four cannot do is
# catch a partial injection that omits exactly one of them. The unit
# ``test_terrain_node_core.py`` pins the set so it cannot grow unnoticed.
_SENTINELS: dict[str, object] = {
    # MountingGeometry — docs :181 (h / θ) and :176 (the datum).
    "camera_height_m": 0.0,
    "pitch_down_rad": 0.0,
    "reference_offset_m": 0.0,
    # TerrainGridParams — 追補 ⑤ §2.
    "cell_size_m": 0.0,
    "corridor_half_width_m": 0.0,
    "forward_range_m": 0.0,
    "min_points_per_cell": 0,
    "drop_threshold_m": 0.0,
    "min_valid_fraction": 0.0,
    # GroundFitParams — 追補 ⑤ §2 + 追補 ⑧ §2 (the two constrained-RANSAC bounds).
    "plane_tolerance_m": 0.0,
    "ransac_iterations": 0,
    "ransac_seed": 0,
    "max_plane_tilt_rad": 0.0,
    "max_plane_offset_m": 0.0,
    # CliffScanParams — 追補 ⑤ §2 (the indoor virtual_scan tunables are NOT reused).
    "angle_min_rad": 0.0,
    "angle_max_rad": 0.0,
    "ray_count": 0,
    "range_min_m": 0.0,
    "range_max_m": 0.0,
    # reference: the datum label. Vocabulary undecided (OQ-OD4Y-h) => no default.
    "reference": "",
    # pixel_stride: >= 1; 0 aborts (追補 ⑨ 裁定 7).
    "pixel_stride": 0,
}


class TerrainPublisher(Node):
    def __init__(self) -> None:
        super().__init__("terrain_publisher")
        self.declare_parameter("enabled", False)
        # "" = subscribe nothing (the speed_band_node safe-OFF convention).
        # Wire the depth / CameraInfo topics explicitly when a camera exists.
        self.declare_parameter("depth_topic", "")
        self.declare_parameter("camera_info_topic", "")
        for name, sentinel in _SENTINELS.items():
            self.declare_parameter(name, sentinel)

        if not bool(self.get_parameter("enabled").value):
            self.get_logger().info(
                "terrain_publisher disabled (safe-OFF default): "
                "no subscription, no publisher, no timer."
            )
            return

        depth_topic = str(self.get_parameter("depth_topic").value)
        camera_info_topic = str(self.get_parameter("camera_info_topic").value)
        if not depth_topic or not camera_info_topic:
            raise ValueError(
                "terrain_publisher enabled but not wired: depth_topic="
                f"{depth_topic!r} camera_info_topic={camera_info_topic!r}. "
                "Both are required; an enabled node with no input would "
                "silently publish nothing while looking healthy."
            )

        # Fail-closed startup validation: a sentinel raises and aborts.
        self._params = params_from_mapping(
            {name: self.get_parameter(name).value for name in PARAM_KEYS}
        )
        # Refuses the root namespace: a frame_id naming no robot is worse than
        # not starting, because a consumer would resolve it against its own TF.
        self._frame_id = resolve_frame_id(self.get_namespace(), BASE_FRAME)

        # QoS depth 10, the value the repo's existing LaserScan / observation
        # producers already use — warehouse_traffic/virtual_scan.py's
        # create_publisher(LaserScan, .../virtual_scan, 10) and
        # speed_band_node.py's create_publisher(SpeedLimit, "speed_limit", 10).
        # Not a new number: matching the established producer keeps the
        # advisory produce/consume agreement of doc03 in one shape.
        self._scan_pub = self.create_publisher(LaserScan, "cliff_scan", 10)
        self._coverage_pub = self.create_publisher(String, "terrain/coverage", 10)

        self._intrinsics: CameraIntrinsics | None = None
        self.create_subscription(CameraInfo, camera_info_topic, self._on_camera_info, 10)
        self.create_subscription(Image, depth_topic, self._on_depth, 10)
        self.get_logger().info(
            f"terrain_publisher: depth={depth_topic} camera_info={camera_info_topic} "
            f"-> cliff_scan + terrain/coverage (frame {self._frame_id})"
        )

    def _on_camera_info(self, msg: CameraInfo) -> None:
        """Latch the intrinsics, rescaled for the configured subsampling."""
        intrinsics = intrinsics_from_camera_info(
            list(msg.k), pixel_stride=self._params.pixel_stride
        )
        if intrinsics is None:
            # An uncalibrated / malformed k is not an error to crash on: the
            # node simply has no geometry and keeps publishing nothing.
            self.get_logger().warning(
                "CameraInfo carries no usable intrinsics (k is not a calibrated "
                "3x3): no terrain output until a calibrated CameraInfo arrives."
            )
            return
        self._intrinsics = intrinsics

    def _on_depth(self, msg: Image) -> None:
        """One depth frame in, one cliff_scan + one coverage out (裁定 5)."""
        if self._intrinsics is None:
            # Without intrinsics there is no back-projection at all; publishing
            # an all-UNKNOWN frame would claim an observation we never made.
            return
        source_stamp_s = stamp_to_seconds(msg.header.stamp.sec, msg.header.stamp.nanosec)
        rows = decode_depth_image(
            encoding=msg.encoding,
            width=msg.width,
            height=msg.height,
            step=msg.step,
            is_bigendian=msg.is_bigendian,
            data=bytes(msg.data),
            pixel_stride=self._params.pixel_stride,
        )
        observation = analyze_depth_frame(
            rows,
            params=self._params.terrain_params(self._intrinsics),
            scan=self._params.scan,
            source_stamp_s=source_stamp_s,
            reference=self._params.reference,
            # The device's own frame counter is not carried by sensor_msgs/Image
            # (追補 ③ #5 wants it for frozen-frame detection); claiming one we
            # do not have would be worse than reporting its absence.
            device_frame_seq=None,
            processing_latency_s=processing_latency_s(self._now(), source_stamp_s),
        )
        self._publish(observation, msg)

    def _now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def _publish(self, observation: TerrainObservation, msg: Image) -> None:
        scan = LaserScan()
        # The measurement time, taken from the incoming depth frame's own header
        # — never self.get_clock().now() (docs :177). The node's clock is read
        # only to report processing latency, never to stamp an observation.
        scan.header.stamp = msg.header.stamp
        scan.header.frame_id = self._frame_id
        for field, value in laser_scan_fields(observation.cliff).items():
            setattr(scan, field, value)
        self._scan_pub.publish(scan)

        coverage = String()
        coverage.data = coverage_json(observation.coverage)
        self._coverage_pub.publish(coverage)


def main() -> None:
    rclpy.init()
    node = TerrainPublisher()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        # SIGINT -> KeyboardInterrupt; SIGTERM -> ExternalShutdownException (Humble tears the
        # context down before this finally runs). Both are a normal stop, not a failure: the node
        # is launched per bot under a systemd unit with Restart=on-failure, so a non-zero exit on
        # a routine stop is journalled as status=1/FAILURE and hides real crashes in the same
        # noise. Nothing is published on the way out and nothing should be: the outputs are
        # observations, and a final frame emitted at shutdown would carry a measurement time
        # nobody measured. Consumers must already cope with the stream simply stopping — that
        # silence is X2's to detect (docs/mode-outdoor/04-perception-sidewalk-and-signals.md:66),
        # and the physical stop is always twist_mux prio-100 / Layer 0, never this topic.
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
