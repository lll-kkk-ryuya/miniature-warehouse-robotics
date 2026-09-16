"""R-26 pins for the 150 mm plain-wheel operating profile (bringup-owned params).

What this file guards
---------------------
``ws/src/warehouse_bringup/config/m1_wheel_plain150.yaml`` is the param injection
that ``docs/mode-m1/02-m1-driver-and-watchdog.md:135`` mandates
("運用値 … は bringup/launch の param 注入で入れる（bringup 所有・別 PR）"). It is a
plain data file: nothing type-checks it, nothing runs it in CI, and the failure
modes are all silent until the robot is moving:

* **a key that is not a declared parameter** — ROS 2 rejects an unknown key from a
  params file and ``m1_driver`` dies at startup. A typo (``wheel_scale_`` /
  ``odom_enable``) is invisible in review and only shows up as a driver that will
  not come up, at the field, with the wheels already swapped;
* **an integer where the declared default is a float** — ``counts_per_rev`` is
  declared with ``FW_ENCODER_COUNTS_PER_WHEEL_REV = 2464.0`` (DOUBLE), so a bare
  ``2464`` is an INTEGER override and rclpy raises ``InvalidParameterTypeException``;
* **a half-finished retreat edit** — 07:243 says the 144 mm retreat changes
  exactly ``k`` 1.875 -> 1.8 and ``wheel_diameter_m`` 0.150 -> 0.144. Changing one
  and not the other leaves the drive scaled for one wheel and the odometry
  measured for another, with no error anywhere;
* **a value the driver would refuse** — ``wheel_scale`` outside [1.0, 2.5] latches
  ``config_error`` and brakes on every command and every tick (fail-closed by
  design), i.e. a bad file here means a robot that will not move at all.

The oracles live on THIS side and come from the docs and the frozen contract,
never from the YAML or from the driver: ``k`` is recomputed as ``0.150 / 0.080``
from the geometry rule in ``docs/mode-m1/02-m1-driver-and-watchdog.md:121`` /
``docs/mode-outdoor/07-drivetrain-and-wheel-sizing.md:252``, and the speed bound
is imported from ``warehouse_interfaces.safety``. The declared-parameter set is
read by AST-walking ``driver_node.py`` (rclpy is absent in pure CI, so the node
module is never imported or executed — the same idiom as
``tests/unit/test_m1_driver_node_odom_wiring.py``), and the YAML is parsed with
PyYAML like ``tests/unit/test_nav2_params_safety.py``.

``test_a_misspelled_key_is_rejected_by_this_suites_own_check`` is the
mutation-style self-check: it mutates a copy of the real file and asserts the
declared-parameter check goes red, so the check cannot silently degrade into a
tautology that passes on anything.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest
import yaml
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY
from warehouse_m1_driver.driver_core import M1DriverCore
from warehouse_m1_driver.odom_core import WheelOdometry

pytestmark = [pytest.mark.safety, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROFILE = _REPO_ROOT / "ws/src/warehouse_bringup/config/m1_wheel_plain150.yaml"
_DRIVER_NODE = _REPO_ROOT / "ws/src/warehouse_m1_driver/warehouse_m1_driver/driver_node.py"

# ── spec literals (from the docs, NOT read back from the implementation) ─────
#: The diameter the firmware's speed/encoder maths assumes and cannot be told
#: about (docs/shared/02-hardware-design.md:749, docs/mode-m1/02:29-33).
SPEC_FW_ASSUMED_DIAMETER_M = 0.080
#: ユーザー裁定 2026-09-13 = 150 mm, 案 A' (07:242).
SPEC_TRUE_DIAMETER_M = 0.150
#: k = D / 80 mm, stated as 1.875 in the docs (02:135, 07:252) AND recomputed
#: below from the rule — the two must agree, otherwise one of them is a typo.
SPEC_WHEEL_SCALE = 1.875
#: Range outside which the driver is fail-closed (02:125 / 07:252).
SPEC_WHEEL_SCALE_RANGE = (1.0, 2.5)
#: The retreat option, if 事前相談 rules the firmware clamp is not 構造 (07:243).
SPEC_RETREAT = {"wheel_scale": 1.8, "wheel_diameter_m": 0.144}
#: EXACTLY the keys this profile may carry — the drivetrain facts of the swap
#: (07:252-256 "150 mm での値") plus the geometry constant odometry needs. This
#: is an EQUALITY set, not a minimum: the driver also declares safety
#: parameters (cmd_vel_timeout_s, stop_overlay_enabled,
#: stop_state_max_validity_s), and a wheel-swap profile is not the place from
#: which W-1 timeouts or the stop overlay get redefined. Growing this set is a
#: deliberate decision that must change this line.
SPEC_EXPECTED_KEYS = frozenset(
    {"wheel_scale", "wheel_diameter_m", "lateral_enabled", "odom_enabled", "counts_per_rev"}
)
#: Declared safety parameters that must never appear in this file (the profile
#: is a drivetrain description; stop authority is owned elsewhere).
SPEC_SAFETY_KEYS_NEVER_HERE = frozenset(
    {"cmd_vel_timeout_s", "stop_overlay_enabled", "stop_state_max_validity_s"}
)
#: PROVISIONAL / not-yet-measured values that must stay on the driver defaults
#: until a G-W gate produces a number (02:126, 02:130-131, 07:253, 07:257-258).
#: ``yaw_scale`` is here and not in the profile on purpose: 07:253 gives its
#: 150 mm value as 実測 (G-W4/G-W9), and 1.0 is verbatim the driver default, so
#: writing it would silently override a measured default once G-W4 lands one.
SPEC_MUST_BE_ABSENT = frozenset(
    {"yaw_scale", "track_m", "wheel_signs", "odom_twist_cov", "odom_pose_cov", "odom_period_s"}
)
#: Rounding slack for one divide plus one multiply at this magnitude.
EPS = 1e-12


def _profile_text() -> str:
    return _PROFILE.read_text(encoding="utf-8")


def _profile_doc() -> dict:
    return yaml.safe_load(_profile_text())


def _params(doc: dict | None = None) -> dict:
    return (doc if doc is not None else _profile_doc())["/**"]["ros__parameters"]


def _declared_parameter_names() -> set[str]:
    """Every ``self.declare_parameter("<name>", ...)`` in driver_node.py.

    AST, not import: rclpy is not installed in pure CI, and executing the node
    module is exactly what this suite must not need in order to be trustworthy.
    """
    tree = ast.parse(_DRIVER_NODE.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "declare_parameter" or not node.args:
            continue
        first = node.args[0]
        assert isinstance(first, ast.Constant) and isinstance(first.value, str), (
            f"declare_parameter の第1引数が文字列リテラルでない: {ast.unparse(node)}"
        )
        names.add(first.value)
    return names


def _undeclared_keys(params: dict) -> set[str]:
    """The check under test: keys a ROS 2 params file may NOT contain."""
    return set(params) - _declared_parameter_names()


# ── (a) the file loads, and loads as a params file for any namespace ─────────


def test_the_profile_is_a_ros2_params_file_keyed_for_any_namespace() -> None:
    """Top-level key must be ``/**`` (twist_mux.yaml:21-29 precedent).

    ``m1_driver`` runs as ``/m1_driver`` in the root namespace in standalone
    bring-up (doc03:50), but a bare ``m1_driver:`` key matches ONLY the root
    namespace: the day the driver is namespaced per bot, the params would
    silently not load and the robot would drive 1.875x too fast with the clamp
    still reporting 0.3 m/s.
    """
    doc = _profile_doc()
    assert list(doc) == ["/**"], f"top-level キーは /** 1 個: {list(doc)}"
    assert list(doc["/**"]) == ["ros__parameters"]
    assert isinstance(_params(doc), dict) and _params(doc), "ros__parameters が空"


def test_every_key_is_a_parameter_the_driver_actually_declares() -> None:
    """An undeclared key makes ``m1_driver`` refuse to start (ROS 2 semantics)."""
    declared = _declared_parameter_names()
    # Guard the guard: if the AST scan ever returns nothing, the subset check
    # below would still pass for an empty profile but fail loudly here.
    assert {"wheel_scale", "odom_enabled", "counts_per_rev"} <= declared, (
        f"AST 走査が declare_parameter を取りこぼしている: {sorted(declared)}"
    )
    assert _undeclared_keys(_params()) == set()


def test_the_profile_carries_exactly_the_drivetrain_keys_and_nothing_else() -> None:
    """EQUALITY, not "at least": scope creep here is silent and dangerous.

    The declared-parameter check above is a subset test, so it happily accepts
    a profile that also sets ``cmd_vel_timeout_s`` (the W-1 freshness timeout),
    ``stop_overlay_enabled`` or ``stop_state_max_validity_s`` — all declared,
    all safety-owned, none of them a fact about which wheels are bolted on.
    A "wheel profile" that quietly lengthens the W-1 timeout is exactly the
    kind of drift a params file makes invisible, so the allowed set is pinned
    exactly and has to be edited on purpose.
    """
    assert set(_params()) == set(SPEC_EXPECTED_KEYS)
    assert SPEC_SAFETY_KEYS_NEVER_HERE & set(_params()) == set()


def test_a_safety_parameter_smuggled_in_is_rejected_by_the_equality_check() -> None:
    """Self-check: the equality test catches what the declared-key test cannot.

    ``cmd_vel_timeout_s`` IS declared, so ``_undeclared_keys`` stays empty —
    this asserts that blind spot exists and that the equality check closes it.
    """
    for smuggled in sorted(SPEC_SAFETY_KEYS_NEVER_HERE):
        mutated = dict(_params())
        mutated[smuggled] = 5.0
        assert _undeclared_keys(mutated) == set(), (
            f"{smuggled} は declare 済のはず（この test の前提）"
        )
        assert set(mutated) != set(SPEC_EXPECTED_KEYS), f"{smuggled} を検出できていない"


def test_a_misspelled_key_is_rejected_by_this_suites_own_check() -> None:
    """Mutation-style self-check: the check above must be able to go red.

    Mutates a COPY of the real file's parsed content (never the file) with the
    typo class that actually happens — a trailing/plural/verb-form slip — and
    asserts each one is caught. Without this, a broken ``_undeclared_keys``
    (e.g. one that returned ``set()`` unconditionally) would pass forever.
    """
    for typo, real in (
        ("wheel_scale_", "wheel_scale"),
        ("odom_enable", "odom_enabled"),
        ("wheel_diameter", "wheel_diameter_m"),
        ("lateral_enable", "lateral_enabled"),
        ("counts_per_revolution", "counts_per_rev"),
    ):
        mutated = dict(_params())
        mutated[typo] = mutated.pop(real)
        assert _undeclared_keys(mutated) == {typo}, f"typo {typo!r} を検出できていない"


# ── (b) the values themselves ────────────────────────────────────────────────


def test_wheel_scale_is_the_diameter_ratio_and_the_documented_number() -> None:
    """k = D / 80 mm, computed here, must equal the 1.875 the docs state."""
    computed = SPEC_TRUE_DIAMETER_M / SPEC_FW_ASSUMED_DIAMETER_M
    assert computed == pytest.approx(SPEC_WHEEL_SCALE, abs=EPS)
    assert _params()["wheel_scale"] == pytest.approx(computed, abs=EPS)
    assert _params()["wheel_scale"] == SPEC_WHEEL_SCALE


def test_wheel_diameter_is_the_true_150_mm_not_the_firmware_assumption() -> None:
    diameter = _params()["wheel_diameter_m"]
    assert diameter == SPEC_TRUE_DIAMETER_M
    # The whole point of the parameter: it must NOT be the 0.080 the firmware
    # believes (02:129 — "真の周長 × 0x0D 生カウントで積分").
    assert diameter != SPEC_FW_ASSUMED_DIAMETER_M


def test_the_scale_and_the_diameter_describe_the_same_wheel() -> None:
    """Catches a half-finished 144 mm retreat edit (07:243).

    The retreat changes exactly two numbers. Changing one of them leaves the
    drive scaled for one wheel and the odometry measured for another — silent,
    and wrong in both the speed and the distance.
    """
    params = _params()
    assert params["wheel_scale"] == pytest.approx(
        params["wheel_diameter_m"] / SPEC_FW_ASSUMED_DIAMETER_M, abs=EPS
    )
    # And it must be one of the two diameters the design actually sanctions.
    assert (params["wheel_scale"], params["wheel_diameter_m"]) in {
        (SPEC_WHEEL_SCALE, SPEC_TRUE_DIAMETER_M),
        (SPEC_RETREAT["wheel_scale"], SPEC_RETREAT["wheel_diameter_m"]),
    }


def test_lateral_motion_is_disabled_and_odometry_is_enabled() -> None:
    """Real booleans, not the strings "false"/"true" (YAML quoting slip)."""
    params = _params()
    assert params["lateral_enabled"] is False
    assert params["odom_enabled"] is True


def test_counts_per_rev_is_a_float_literal_matching_the_declared_type() -> None:
    """DOUBLE vs INTEGER: ``2464`` would abort the node at startup.

    ``declare_parameter("counts_per_rev", FW_ENCODER_COUNTS_PER_WHEEL_REV)``
    with a 2464.0 default makes the parameter type DOUBLE; rclpy raises
    ``InvalidParameterTypeException`` on an integer override.
    """
    value = _params()["counts_per_rev"]
    assert isinstance(value, float) and not isinstance(value, bool)
    assert value == 2464.0
    # Belt and braces: the source literal must carry a decimal point, because a
    # bare 2464 parses to int and only THAT is what rclpy rejects.
    assert "counts_per_rev: 2464.0" in _profile_text()


def test_yaw_scale_is_absent_so_a_measured_default_is_never_overridden() -> None:
    """07:253 gives yaw_scale's 150 mm value as 実測, not as a number.

    Writing the driver's own default (1.0) here would be inert today and
    harmful the day G-W4 produces a real factor and it lands as the driver
    default: this file would silently pull it back to 1.0. The file must say
    so, so the next person does not "helpfully" add it back.
    """
    assert "yaw_scale" not in _params()
    text = _profile_text()
    assert "yaw_scale" in text, "不在の理由が書かれていない（黙って落としたのと区別がつかない）"
    assert "# TODO(実測)" in text and "G-W4" in text


def test_provisional_values_are_left_on_the_driver_defaults() -> None:
    """yaw_scale / track_m / wheel_signs / covariances are PROVISIONAL.

    (02:126, 02:130-131, 07:253, 07:257-258.) Copying a provisional number
    here creates a second place to forget after G-W1, and makes an unmeasured
    value look decided.
    """
    assert SPEC_MUST_BE_ABSENT & set(_params()) == set()


def test_the_file_states_when_it_may_be_applied() -> None:
    """The operator-facing preconditions must be IN the file, not only in docs.

    This file is read at the field, alone, by whoever is about to type the
    command. Applying it before the swap (or before the gates) is the mistake
    it exists to prevent.
    """
    text = _profile_text()
    for token in ("--params-file", "G-W1", "G-W4", "m1_probe", "docs/mode-m1/02", "07:243"):
        assert token in text, f"運用条件の記載が無い: {token}"


# ── (c) the values, fed to the real code, behave ─────────────────────────────


class _FakeBackend:
    """The two calls the dispatch path can make (backend.MotionBackend seam)."""

    def __init__(self) -> None:
        self.velocity_calls: list[tuple[float, float, float]] = []
        self.brake_calls: int = 0

    def set_body_velocity(self, vx: float, vy: float, wz: float) -> None:
        self.velocity_calls.append((vx, vy, wz))

    def stop_brake(self) -> None:
        self.brake_calls += 1

    def reset_state(self) -> None:  # pragma: no cover - not exercised here
        pass

    def close(self) -> None:  # pragma: no cover - not exercised here
        pass


def _core_from_profile() -> tuple[M1DriverCore, _FakeBackend, dict]:
    params = _params()
    backend = _FakeBackend()
    core = M1DriverCore(
        backend,
        wheel_scale=params["wheel_scale"],
        # yaw_scale intentionally NOT passed: the profile does not set it, so
        # the driver default is what actually runs (see SPEC_MUST_BE_ABSENT).
        lateral_enabled=params["lateral_enabled"],
    )
    return core, backend, params


def test_the_profile_is_accepted_by_the_driver_core() -> None:
    """A rejected config is not "a warning": it brakes on every command."""
    core, backend, _ = _core_from_profile()
    assert core.config_error is None
    lo, hi = SPEC_WHEEL_SCALE_RANGE
    assert lo <= core.wheel_scale <= hi
    core.on_cmd_vel(0.1, 0.0, 0.0, now=0.0)
    assert backend.brake_calls == 0, "正しい profile なのに brake している"
    assert len(backend.velocity_calls) == 1


def test_full_speed_command_stays_under_the_frozen_cap_on_the_real_robot() -> None:
    """hypot(wire) x k <= MAX_LINEAR_VELOCITY — the property the swap must keep.

    The clamp bounds ACTUAL units and runs FIRST; ``_to_wire`` then divides by
    k. So the number on the wire is smaller than the cap, and the speed of the
    real 150 mm robot is what the cap describes.
    """
    core, backend, params = _core_from_profile()
    k = params["wheel_scale"]
    over_cap = [
        (MAX_LINEAR_VELOCITY, 0.0, 0.0),
        (MAX_LINEAR_VELOCITY * 10, 0.0, 0.0),
        (-MAX_LINEAR_VELOCITY, 0.0, 0.5),
        (MAX_LINEAR_VELOCITY, MAX_LINEAR_VELOCITY, -0.5),
        (1.0, -1.0, 0.0),
    ]
    for index, (vx, vy, wz) in enumerate(over_cap):
        core.on_cmd_vel(vx, vy, wz, now=index * 0.01)
        wire_vx, wire_vy, _ = backend.velocity_calls[-1]
        real_speed = math.hypot(wire_vx, wire_vy) * k
        assert real_speed <= MAX_LINEAR_VELOCITY + EPS, (
            f"{(vx, vy, wz)} -> wire {(wire_vx, wire_vy)} = 実速度 {real_speed}"
        )
        # Every sample above asks for AT LEAST the cap (the lateral ones lose
        # vy before the clamp, leaving >= cap forward), so the clamp must
        # SATURATE at the cap, not stop short of it and not zero the command.
        # Without this, a profile that made the robot crawl would still pass
        # the <= assertion above.
        assert real_speed == pytest.approx(MAX_LINEAR_VELOCITY, abs=EPS), (
            f"{(vx, vy, wz)} -> 実速度 {real_speed} が上限で飽和していない"
        )


def test_lateral_commands_are_zeroed_before_the_clamp() -> None:
    """lateral_enabled: false — plain wheels cannot translate sideways."""
    core, backend, _ = _core_from_profile()
    core.on_cmd_vel(0.0, MAX_LINEAR_VELOCITY, 0.0, now=0.0)
    wire_vx, wire_vy, _ = backend.velocity_calls[-1]
    assert wire_vy == 0.0
    # And the forward component must not have absorbed it.
    assert wire_vx == 0.0


def test_without_this_profile_the_same_command_would_drive_1_875x_too_fast() -> None:
    """The failure this file prevents, stated as a test (07:252).

    Stock defaults (k = 1.0) with 150 mm wheels fitted: the wire value equals
    the commanded 0.3, and the real robot then does 0.3 x 1.875 = 0.5625 m/s
    while the clamp still reports 0.3. Fail-open.
    """
    unconfigured_backend = _FakeBackend()
    unconfigured = M1DriverCore(unconfigured_backend)  # today's defaults
    unconfigured.on_cmd_vel(MAX_LINEAR_VELOCITY, 0.0, 0.0, now=0.0)
    wire_vx, _, _ = unconfigured_backend.velocity_calls[-1]
    real_speed = wire_vx * SPEC_WHEEL_SCALE
    assert real_speed > MAX_LINEAR_VELOCITY
    assert real_speed == pytest.approx(MAX_LINEAR_VELOCITY * SPEC_WHEEL_SCALE, abs=EPS)


# ── (d) the odometry geometry the profile switches on ────────────────────────


def test_the_profile_geometry_is_accepted_by_the_odometry_integrator() -> None:
    """Constructing WheelOdometry with these values must not raise.

    ``odom_enabled: true`` is only safe if the geometry it turns on is valid:
    the ctor rejects non-finite/non-positive values, so a typo'd diameter would
    otherwise take the driver down at startup, after the swap, in the field.
    """
    params = _params()
    odom = WheelOdometry(
        counts_per_rev=params["counts_per_rev"],
        wheel_diameter_m=params["wheel_diameter_m"],
        track_m=0.194,  # driver default, PROVISIONAL (02:130) — not in the profile
    )
    # Independent oracle: one full wheel revolution must roll the TRUE
    # circumference, pi x 0.150 m, not the firmware's pi x 0.080 m.
    assert odom.m_per_count * params["counts_per_rev"] == pytest.approx(
        math.pi * SPEC_TRUE_DIAMETER_M, abs=EPS
    )
    assert odom.m_per_count * params["counts_per_rev"] != pytest.approx(
        math.pi * SPEC_FW_ASSUMED_DIAMETER_M, abs=EPS
    )
