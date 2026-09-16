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
