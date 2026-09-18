"""Distro-conditional collision_monitor parameters (doc12 末尾【2026-09-16 追補】(2) 追記).

``collision_monitor.yaml`` is the ROS 2 **Humble** truth (ADR-0008): nav2_collision_monitor
1.1.20 declares no per-source ``<src>.source_timeout`` (humble source.cpp:67-74; Iron is the same,
iron source.cpp:64-69) and a stale/absent source merely drops its points. Jazzy 1.3.x DOES declare
the key (jazzy source.cpp:74-78, default = the node-level value) and turns an invalid source into a
STOP (jazzy collision_monitor_node.cpp:438-443), so the by-design-silent-when-far ``virtual_scan``
(SUPPRESSION_RANGE 1.0 m) would false-STOP the Jazzy dev cockpit 1.0 s after the robots part.

PR#229 shipped ``virtual_scan.source_timeout: 0.0`` for exactly that (jazzy source.cpp:88 guards
with ``source_timeout_.seconds() != 0.0`` -> 0.0 disables the timeout for that one source). #682
removed the Jazzy-only key from the yaml; this module puts it back at LAUNCH time, keyed on
``ROS_DISTRO``, so the yaml never carries a key its target distro does not declare.

Pure function (no ROS imports) so the rule is unit-testable in pure CI (R-26:
tests/unit/test_collision_monitor_distro_params.py); nav2_bringup.launch.py passes
``os.environ.get("ROS_DISTRO")``. 参照日 2026-09-16.
"""

#: Distros verified (primary sources above) NOT to declare a per-source ``source_timeout``.
#: Everything else — Jazzy, Kilted, Rolling and any future distro — follows the Jazzy rule.
NO_PER_SOURCE_TIMEOUT_DISTROS: frozenset[str] = frozenset({"humble", "iron"})

#: "No timeout for this source" on Jazzy+ (the ``!= 0.0`` guard). Same value PR#229 shipped.
VIRTUAL_SCAN_NO_TIMEOUT_S: float = 0.0


def virtual_scan_timeout_overrides(ros_distro: str | None) -> list[dict]:
    """Extra launch ``parameters`` entries for the collision_monitor Node on this distro.

    Returns ``[]`` for Humble/Iron (the yaml is complete there) and
    ``[{"virtual_scan": {"source_timeout": 0.0}}]`` otherwise. An unset or unknown distro
    injects: where the key is undeclared the override is a harmless undeclared override, where it
    is declared omitting it means a false STOP at >1.0 m — injecting is the safe side both ways.
    Only ``virtual_scan`` is touched; the real ``scan`` keeps the distro's own dropout semantics.
    """
    if ros_distro in NO_PER_SOURCE_TIMEOUT_DISTROS:
        return []
    return [{"virtual_scan": {"source_timeout": VIRTUAL_SCAN_NO_TIMEOUT_S}}]


# ── cliff_scan source arming (04 追補 ⑩ §3) ───────────────────────────────────────────────
# Second launch-time override of the same Node, and it lives here for the same reason the
# virtual_scan one does: ``collision_monitor.yaml`` stays the static Humble truth and anything
# CONDITIONAL is injected after the file so it wins. The condition differs — distro above,
# CONFIG here (``perception.terrain.enabled``, the key the bringup lane puts in
# ``config/warehouse.base.yaml``; read-only for us).
#
# Why the source is declared DISABLED in the yaml and armed here rather than simply always-on:
# the cliff producer (``warehouse_perception.terrain_publisher``) is itself default-OFF and the
# dev cockpit (Jazzy + Gazebo) has no depth camera, so an ENABLED source with no publisher is an
# "invalid source" STOP on Jazzy (jazzy collision_monitor_node.cpp:437-447) — the cockpit would
# stop the moment it starts. A DISABLED source is skipped BEFORE that check on both distros
# (humble collision_monitor_node.cpp:357-360 ``if (source->getEnabled())`` / jazzy :437), so the
# default-off yaml is inert everywhere and this override is the single arming point. 参照日
# 2026-09-18.
#
# NOT here: a per-source ``source_timeout``. OQ-OD44 is ruled as "scan 型" (04 追補 ⑩ §1):
# silence is not normal for a cliff sensor, so the node-level timeout must keep applying (on
# Jazzy+ that makes a dead cliff_scan a STOP — the wanted direction). The Humble TARGET drops
# the points instead (fail-open, doc12 末尾【2026-09-16 追補】(1)); that liveness duty is X2's
# (04:66) / the Guardian's ``scan_stale`` (doc12 追補 (3)), not this monitor's.

#: collision_monitor source name of the cliff virtual scan (doc03:322 / 04 追補 ⑨ hand-off ①).
CLIFF_SOURCE_NAME: str = "cliff_scan"

#: config path of the producer gate the arming follows (bringup lane owns the key's VALUE).
TERRAIN_ENABLED_CONFIG_PATH: tuple[str, ...] = ("perception", "terrain", "enabled")


def terrain_enabled(config: dict | None) -> bool:
    """``perception.terrain.enabled`` from a loaded config, defaulting to False.

    Mirrors ``nav2_bringup.launch.py::_speed_bands`` (missing / non-dict block -> treat as
    absent). Absent config, absent section and a non-mapping section all mean OFF.

    The value itself is coerced with ``bool()``, so a TRUTHY NON-BOOL opens the gate — a
    quoted ``enabled: "false"`` in YAML is the string ``"false"`` and is truthy, exactly as
    in ``_speed_band_group``. That looseness is DELIBERATE, not an oversight: the producer
    gate ``_terrain_group`` (nav2_bringup.launch.py:522) reads the same key with the same
    ``bool(...)`` and its docstring promises the key is ONE truth for producer and consumer.
    A stricter rule here (``is True``) would desynchronise them for such a value and leave
    the L1 source DISABLED while the cliff producer publishes — blind reflex, the one
    direction that must not happen. Parity beats strictness; the shared looseness is
    recorded in 04 追補 ⑩ §3 (OQ-OD4Y-l6) rather than papered over.
    """
    node: object = config
    for key in TERRAIN_ENABLED_CONFIG_PATH[:-1]:
        if not isinstance(node, dict):
            return False
        node = node.get(key, {})
    if not isinstance(node, dict):
        return False
    return bool(node.get(TERRAIN_ENABLED_CONFIG_PATH[-1], False))


def cliff_sources(config: dict | None) -> list[dict]:
    """Extra collision_monitor ``parameters`` entries arming the ``cliff_scan`` source.

    ``[]`` when the terrain producer is off (the yaml default ``enabled: false`` stands), else
    ``[{"cliff_scan": {"enabled": True}}]``. Only ``enabled`` is touched: ``type`` / ``topic``
    stay in the yaml, and no ``source_timeout`` is injected (see the note above).
    """
    if not terrain_enabled(config):
        return []
    return [{CLIFF_SOURCE_NAME: {"enabled": True}}]
