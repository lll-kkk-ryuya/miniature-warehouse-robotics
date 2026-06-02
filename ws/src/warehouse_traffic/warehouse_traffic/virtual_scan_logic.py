"""Rclpy-free geometry for the Multi-Robot Costmap Layer (virtual LaserScan).

Design source: ``docs/mode-a/11a-traffic-mode-a.md:158-321`` (virtual LaserScan
injection — the other robot is published as a phantom obstacle into the own
robot's Nav2 ``obstacle_layer``). The math is kept rclpy-free so it is
unit-testable on the host; ``virtual_scan.py`` wraps it in a ROS node.

Single source of truth: ``ROBOT_RADIUS`` is imported from
``warehouse_description.robot_dimensions`` (``robot_dimensions.py:45``, R-42 =
0.075 m), an allowed shared asset (``parallel-workflow.md`` §2.1). It is NOT
re-hardcoded here — doc11a's older 0.1 is superseded.
"""

import math

from warehouse_description.robot_dimensions import ROBOT_RADIUS

# Virtual-scan tunables (doc11a:192-196, 305-312). Phase-dependent; see TODOs.
ANGULAR_WIDTH = 0.26  # rad (±15°) injected around the bearing to the other robot
MAX_RANGE = 2.0  # m  LaserScan range_max (also Nav2 obstacle/raytrace max range)
RANGE_MIN = 0.05  # m  LaserScan range_min (doc11a:246)
SUPPRESSION_RANGE = 1.0  # m  no publish if robots are farther apart (doc11a:195,316)
NUM_RAYS = 360  # 1° increment
PUBLISH_PERIOD_S = 0.1  # 10 Hz (doc11a:219-220)

ANGLE_MIN = -math.pi
ANGLE_MAX = math.pi
ANGLE_INCREMENT = 2.0 * math.pi / NUM_RAYS


def quat_to_yaw(x: float, y: float, z: float, w: float) -> float:
    """Yaw (rad) from a quaternion (doc11a:260-266)."""
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def relative_distance_bearing(
    own_x: float, own_y: float, own_yaw: float, other_x: float, other_y: float
) -> tuple[float, float]:
    """Return (distance, bearing) to the other robot in the own base_link frame.

    Bearing is ``atan2(dy, dx) - own_yaw`` (doc11a:227-237), i.e. relative to the
    own robot's heading.
    """
    dx = other_x - own_x
    dy = other_y - own_y
    distance = math.hypot(dx, dy)
    bearing = math.atan2(dy, dx) - own_yaw
    return distance, bearing


def should_publish(distance: float, suppression_range: float = SUPPRESSION_RANGE) -> bool:
    """True if the other robot is close enough to inject (doc11a:231-232,316)."""
    return distance <= suppression_range


def build_ranges(
    distance: float,
    bearing: float,
    *,
    robot_radius: float = ROBOT_RADIUS,
    angular_width: float = ANGULAR_WIDTH,
    num_rays: int = NUM_RAYS,
    range_min: float = RANGE_MIN,
) -> list[float]:
    """Build the ``ranges`` array: a ±``angular_width`` wedge at ``distance``.

    Rays outside the wedge are ``inf`` (no obstacle); inside, the range is
    ``max(distance - robot_radius, range_min)`` so the phantom sits at the other
    robot's near edge (doc11a:250-256). ``angle_min = -pi`` so index 0 maps to a
    bearing of ``-pi`` (doc11a:243-251).
    """
    increment = 2.0 * math.pi / num_rays
    ranges = [math.inf] * num_rays
    center_idx = int((bearing + math.pi) / increment) % num_rays
    half_width = int(angular_width / increment)
    near_edge = max(distance - robot_radius, range_min)
    for i in range(-half_width, half_width + 1):
        idx = (center_idx + i) % num_rays
        ranges[idx] = near_edge
    return ranges
