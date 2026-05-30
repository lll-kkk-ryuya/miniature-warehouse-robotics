"""Single source of the minicar's frozen skeleton names + Python-side deploy params.

The URDF↔world interface — link names, sensor ``frame_id``, footprint — is fixed here
and in ``minicar.urdf.xacro`` so ``warehouse_sim`` (sim) and the real Yahboom car
reference identical names (doc16 §9). The canonical TF tree (doc09 §TFツリー / doc07 T6) is::

    map → bot{n}/odom → bot{n}/base_link → { bot{n}/lidar_link, bot{n}/imu_link }

Body geometry (sizes) lives in the xacro — the URDF's natural home. This module owns
the *names* and the deployment-side values Python needs (nav footprint radius, spawn
height). Every hardware number is PROVISIONAL until the Yahboom car is measured
(Phase 1, R-04 / R-42); see ``PROVISIONAL``. To re-confirm after measurement, edit this
file (Python consumers) and the matching xacro property — the unit tests guard drift.
"""

# ── Frozen frame / link names (contract: doc09 TF tree, doc16 §9) ──────────────
# Note: the kickoff's "laser" was an example; the repo contract is "lidar_link".
BASE_FRAME = "base_link"
LIDAR_FRAME = "lidar_link"  # MS200 mount; /bot{n}/scan header.frame_id = bot{n}/lidar_link
IMU_FRAME = "imu_link"
ODOM_FRAME = "odom"  # /bot{n}/odom child_frame_id = bot{n}/base_link

# 4-wheel skid-steer (Yahboom MicroROS car). Wheels are model-internal — not in the
# doc09 TF tree, but their link names are part of the URDF↔world interface (doc16 §9).
WHEEL_LINKS: tuple[str, ...] = (
    "wheel_front_left",
    "wheel_front_right",
    "wheel_rear_left",
    "wheel_rear_right",
)

# Contract link names that warehouse_sim + real hardware must reference identically.
FROZEN_LINK_NAMES: tuple[str, ...] = (BASE_FRAME, LIDAR_FRAME, IMU_FRAME, *WHEEL_LINKS)

# Sensor / odom frame_id contract (consumed by AMCL / Nav2 / warehouse_traffic).
FROZEN_FRAME_IDS: dict[str, str] = {
    "lidar": LIDAR_FRAME,
    "imu": IMU_FRAME,
    "odom": ODOM_FRAME,
}

# ── Python-side deployment params (PROVISIONAL) ────────────────────────────────
# Nav2/AMCL inflation radius. 75mm per R-42: doc11a's ROBOT_RADIUS=0.1 conflicts with a
# ~150mm body and would block the 200mm bottleneck aisle. Consumed by warehouse_bringup.
ROBOT_RADIUS = 0.075  # m  # TODO(Phase 1 実測, R-42)
# Spawn z so the wheels rest on the ground plane (~ wheel radius). Matches xacro wheel_radius.
SPAWN_Z = 0.033  # m  # TODO(Phase 1 実測)

# Machine-checkable provisional flags: the unit test asserts these stay marked until the
# robot is measured, so a stale "confirmed" value can't slip through unflagged.
PROVISIONAL: dict[str, str] = {
    "ROBOT_RADIUS": "Phase 1 実測 (R-42): doc11a の 0.1 は ~150mm 車体と矛盾",
    "SPAWN_Z": "Phase 1 実測: wheel radius 依存",
}
