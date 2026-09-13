"""Wiring pins for the M1 odometry publisher (`driver_node.py`) — AST, not import.

CI has no rclpy, so this layer is never EXECUTED by any unit: it is read as
source (idiom of `tests/unit/test_m1_stop_state_contract.py` §⑪ /
`tests/unit/test_teleop_m1_launch_wiring.py`). Because nothing runs it, the
pins are EXACT rather than substring — a weakened guard or a callback that
publishes a fabricated velocity would otherwise pass the whole suite.

What the docs require of this layer (all re-read, file:line):

* `docs/architecture/23-perception-and-localization.md:163` — `odom -> base_link`
  is broadcast by ekf_node and by NOTHING else; the M1 driver must not emit
  odom TF. `docs/mode-m1/02-m1-driver-and-watchdog.md:54` repeats it for this
  very node ("TF は出さない"). Hence: no tf2_ros import, no TransformBroadcaster,
  no sendTransform — anywhere in the file.
* `docs/architecture/03-software-architecture.md:77` — the contracted topic is
  `/bot{n}/odom`, type `nav_msgs/Odometry`.
* `ws/src/warehouse_description/warehouse_description/robot_dimensions.py:19,27`
  — `BASE_FRAME` / `ODOM_FRAME` are the frozen frame names (`:27` states
  `/bot{n}/odom child_frame_id = bot{n}/base_link`); they are imported, never
  retyped as local string literals.
* `docs/mode-m1/02-m1-driver-and-watchdog.md:49` — odom comes from the raw
  `0x0D` encoder counts, `:29-33` — never from the firmware's reported speed.
* `docs/architecture/23-perception-and-localization.md:183` — the EKF consumes
  the VELOCITIES, so the twist has to be filled.
* Default-off, like `stop_overlay_enabled` (doc05 §4 table row 1): the
  standalone bring-up graph (`docs/mode-m1/03-joystick-teleop-bringup.md:50`)
  must gain no publisher and no timer unless the parameter is set.

Expected values are literals here; the implementation is not imported.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.safety]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PKG = _REPO_ROOT / "ws/src/warehouse_m1_driver/warehouse_m1_driver"
_DRIVER_NODE_PY = _PKG / "driver_node.py"
_ODOM_CORE_PY = _PKG / "odom_core.py"

# ── spec literals ────────────────────────────────────────────────────────────
SPEC_ODOM_ENABLE_PARAM = "odom_enabled"
SPEC_ODOM_MSG_TYPE = "Odometry"
SPEC_ODOM_TOPIC = "f'/{bot}/odom'"
SPEC_HEADER_FRAME = "f'{bot}/{ODOM_FRAME}'"
SPEC_CHILD_FRAME = "f'{bot}/{BASE_FRAME}'"
SPEC_FRAME_NAME_MODULE = "warehouse_description.robot_dimensions"
#: docs/shared/02-hardware-design.md:749 (ENCODER_CIRCLE_205 / 251.327 mm).
SPEC_FW_COUNTS_PER_REV = 2464.0
SPEC_FW_ASSUMED_DIAMETER_M = 0.080
#: name -> default. A string value is compared as an EXPRESSION (a named
#: constant), anything else as the literal value it must evaluate to.
SPEC_NEW_PARAM_DEFAULTS = {
    "wheel_scale": 1.0,
    "yaw_scale": 1.0,
    "lateral_enabled": True,
    "odom_enabled": False,
    "wheel_diameter_m": "FW_ASSUMED_WHEEL_DIAMETER_M",
    "track_m": 0.194,
    "counts_per_rev": "FW_ENCODER_COUNTS_PER_WHEEL_REV",
    "wheel_signs": [1, 1, 1, 1],
    "odom_period_s": 0.04,
    "odom_twist_cov": 0.02,
    "odom_pose_cov": 1e3,
}
#: Statements the odom callback must contain, verbatim.
SPEC_CALLBACK_STATEMENTS = (
    "counts = self._backend.read_encoders()",
    "sample = self._odom.update(counts, time.monotonic())",
    "msg.header.frame_id = self._odom_frame_id",
    "msg.child_frame_id = self._odom_child_frame_id",
    "msg.pose.pose.position.x = sample.x",
    "msg.pose.pose.position.y = sample.y",
    "msg.twist.twist.linear.x = sample.vx",
    "msg.twist.twist.angular.z = sample.wz",
    "self._odom_pub.publish(msg)",
)


def _source() -> str:
    return _DRIVER_NODE_PY.read_text(encoding="utf-8")


def _tree() -> ast.Module:
    return ast.parse(_source())


def _calls(tree: ast.AST, attr: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
    ]


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    return next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _assignments(function: ast.FunctionDef) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            out[ast.unparse(node.targets[0])] = ast.unparse(node.value)
    return out


# ── no TF, ever ──────────────────────────────────────────────────────────────


def _symbols() -> set[str]:
    """Every name the CODE uses: identifiers, attributes and imported modules.

    Prose is excluded on purpose — this file's own docstring has to be able to
    say the words "TransformBroadcaster" while the code must never use one.
    """
    out: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
            out.update(alias.name for alias in node.names)
    return out


@pytest.mark.parametrize(
    "forbidden",
    [
        "tf2_ros",
        "TransformBroadcaster",
        "StaticTransformBroadcaster",
        "sendTransform",
        "TransformStamped",
    ],
)
def test_the_driver_never_broadcasts_tf(forbidden: str) -> None:
    """doc23:163 / doc02:54 — ekf_node is the sole owner of odom -> base_link."""
    assert forbidden not in _symbols(), (
        f"{forbidden} used in driver_node.py — odom->base_link has exactly one owner (ekf_node)"
    )


def test_no_broadcaster_of_any_kind_is_constructed() -> None:
    assert not any("Broadcaster" in symbol for symbol in _symbols())


# ── the publisher exists only under the parameter ────────────────────────────


def _odom_publisher(tree: ast.Module) -> ast.Call:
    matches = [
        call
        for call in _calls(tree, "create_publisher")
        if call.args and ast.unparse(call.args[0]) == SPEC_ODOM_MSG_TYPE
    ]
    assert len(matches) == 1, f"expected exactly one Odometry publisher, got {len(matches)}"
    return matches[0]


def test_publishes_the_contracted_topic_and_type() -> None:
    call = _odom_publisher(_tree())
    assert ast.unparse(call.args[0]) == SPEC_ODOM_MSG_TYPE  # nav_msgs/Odometry, doc03:77
    assert ast.unparse(call.args[1]) == SPEC_ODOM_TOPIC


def test_the_publisher_and_timer_are_reachable_only_through_the_parameter_guard() -> None:
    """Default-off must leave the standalone graph unchanged (no pub, no timer).

    The guard's test is matched EXACTLY, so `if not ...` or `... or True`
    fails rather than passing on a substring.
    """
    tree = _tree()
    setup = _function(tree, "_setup_odom")
    # Everything that creates the odom ROS entities lives in _setup_odom ...
    assert id(_odom_publisher(tree)) in {id(c) for c in _calls(setup, "create_publisher")}
    odom_timers = [
        call
        for call in _calls(tree, "create_timer")
        if len(call.args) >= 2 and ast.unparse(call.args[1]) == "self._on_odom"
    ]
    assert len(odom_timers) == 1
    assert id(odom_timers[0]) in {id(c) for c in _calls(setup, "create_timer")}
    # ... and _setup_odom is called only inside the parameter guard.
    guard_test = f"bool(self.get_parameter('{SPEC_ODOM_ENABLE_PARAM}').value)"
    guarded_calls = [
        call
        for node in ast.walk(tree)
        if isinstance(node, ast.If) and ast.unparse(node.test) == guard_test
        for inner in node.body
        for call in _calls(inner, "_setup_odom")
    ]
    all_setup_calls = _calls(tree, "_setup_odom")
    assert len(all_setup_calls) == 1
    assert id(all_setup_calls[0]) in {id(c) for c in guarded_calls}


def test_the_odom_timer_period_comes_from_the_parameter() -> None:
    tree = _tree()
    timer = next(
        call
        for call in _calls(tree, "create_timer")
        if len(call.args) >= 2 and ast.unparse(call.args[1]) == "self._on_odom"
    )
    period_expr = ast.unparse(timer.args[0])
    source_of_period = _assignments(_function(tree, "_setup_odom")).get(period_expr, period_expr)
    # A hardcoded period would ignore the firmware's 25 Hz report rate.
    assert "odom_period_s" in source_of_period
    # ... and it is hardened the same way the watchdog period is (0/NaN would
    # break the timer and silently stop the odom stream).
    assert "_positive_or_default" in source_of_period


# ── frame names come from the frozen single source ───────────────────────────


def test_frame_names_are_imported_from_robot_dimensions() -> None:
    imported = {
        alias.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.ImportFrom) and node.module == SPEC_FRAME_NAME_MODULE
        for alias in node.names
    }
    assert {"BASE_FRAME", "ODOM_FRAME"} <= imported


def test_header_and_child_frames_use_the_frozen_names() -> None:
    assignments = _assignments(_function(_tree(), "_setup_odom"))
    assert assignments["self._odom_frame_id"] == SPEC_HEADER_FRAME
    assert assignments["self._odom_child_frame_id"] == SPEC_CHILD_FRAME


def test_no_local_frame_string_literals() -> None:
    """A bare 'odom' / 'base_link' literal is how a frame drifts out of the tree."""
    literals = {
        node.value
        for node in ast.walk(_tree())
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "odom" not in literals
    assert "base_link" not in literals


# ── the callback integrates counts, it does not invent them ──────────────────


@pytest.mark.parametrize("statement", SPEC_CALLBACK_STATEMENTS)
def test_callback_wires_encoder_counts_through_to_the_publisher(statement: str) -> None:
    callback = _function(_tree(), "_on_odom")
    statements = [ast.unparse(node) for node in ast.walk(callback)]
    assert statement in statements


def test_callback_drops_unusable_samples_instead_of_publishing_them() -> None:
    """`None` from either stage must return, never fall through to publish."""
    callback = _function(_tree(), "_on_odom")
    guards = [
        ast.unparse(node.test)
        for node in ast.walk(callback)
        if isinstance(node, ast.If) and any(isinstance(s, ast.Return) for s in node.body)
    ]
    assert "counts is None" in guards
    assert "sample is None" in guards


def test_callback_uses_a_monotonic_clock_for_the_integration() -> None:
    body = ast.unparse(_function(_tree(), "_on_odom"))
    assert "time.monotonic()" in body
    assert "time.time()" not in body


def test_callback_does_not_read_the_firmware_reported_speed() -> None:
    """doc02:29-33 帰結①: the firmware's body speed carries the X3 geometry error.

    Pinned positively: `read_encoders` is the ONLY thing the callback is
    allowed to ask the backend for, so no `get_motion_data` / speed getter can
    creep in as an "easier" odometry source.
    """
    callback = _function(_tree(), "_on_odom")
    backend_methods = {
        node.func.attr
        for node in ast.walk(callback)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and ast.unparse(node.func.value) == "self._backend"
    }
    assert backend_methods == {"read_encoders"}


# ── parameters ───────────────────────────────────────────────────────────────


def _declared_defaults() -> dict[str, ast.expr]:
    return {
        ast.literal_eval(call.args[0]): call.args[1]
        for call in _calls(_tree(), "declare_parameter")
        if len(call.args) >= 2 and isinstance(call.args[0], ast.Constant)
    }


@pytest.mark.parametrize(("name", "expected"), sorted(SPEC_NEW_PARAM_DEFAULTS.items()))
def test_new_parameters_exist_with_the_documented_defaults(name: str, expected) -> None:
    declared = _declared_defaults()
    assert name in declared, f"{name} is not declared"
    node = declared[name]
    if isinstance(expected, str):
        assert ast.unparse(node) == expected
    else:
        assert ast.literal_eval(node) == expected


def test_the_pre_existing_parameters_are_untouched() -> None:
    declared = _declared_defaults()
    assert "cmd_vel_timeout_s" in declared
    assert "stop_overlay_enabled" in declared
    assert ast.literal_eval(declared["stop_overlay_enabled"]) is False


def test_the_firmware_constants_carry_the_documented_values() -> None:
    """docs/shared/02-hardware-design.md:749, pinned in the source they live in."""
    source = _ODOM_CORE_PY.read_text(encoding="utf-8")
    assert f"FW_ENCODER_COUNTS_PER_WHEEL_REV: float = {SPEC_FW_COUNTS_PER_REV}" in source
    assert f"FW_ASSUMED_WHEEL_DIAMETER_M: float = {SPEC_FW_ASSUMED_DIAMETER_M}" in source
