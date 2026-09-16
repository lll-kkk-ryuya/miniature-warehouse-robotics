"""R-26 safety units for the Emergency Guardian's scan-liveness estop (``scan_stale``, L1).

Oracle: docs/architecture/12-infrastructure-common.md 末尾【2026-09-16 追補】(3) — the
ruling that, because Humble 1.1.20's ``nav2_collision_monitor`` only DROPS a stale or
absent source's points and passes cmd_vel through (fail-open), lidar loss must stop the
bot in the Emergency Guardian. Every expected value below is a literal transcribed from
that doc / the config files, never imported from the module under test (importing the
constant would make the assertion a tautology no mutation can kill).

Contract as pinned by the doc:

  input     /{bot}/scan ARRIVAL age on the node's monotonic clock (header stamp unused)
  rule      scan_age > safety.scan_freshness_timeout (strict)      -> estop "scan_stale"
            scan_age None (never received) AND odom_seen           -> estop "scan_stale"
            scan_age None AND not odom_seen (absent bot)            -> silent
  level     auto-clears when scans resume (EdgeLatch re-arms); never latched
  event     type="scan_stale" is additive; core keys unchanged (doc12:141-150)
  config    safety.scan_freshness_timeout = 1.0 s, inherited from the node-level
            source_timeout in collision_monitor.yaml:58 (provisional, live-tune)

Two halves, the same split as ``test_guardian_stop_state.py``: a PURE-logic half with an
independent oracle (mutation-killable on the host) and an AST-WIRING half that pins the
rclpy node's subscription / marshalling / plumbing by parsing its source (CI has no rclpy).
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest
import yaml
from warehouse_safety.guard_logic import (
    BotState,
    Decision,
    EdgeLatch,
    build_event,
    evaluate,
    stop_requested_for,
    validate_scan_freshness_timeout,
)

_REPO = Path(__file__).resolve().parents[2]
_GUARDIAN_PY = (
    _REPO / "ws" / "src" / "warehouse_safety" / "warehouse_safety" / "emergency_guardian.py"
)
_BASE_YAML = _REPO / "config" / "warehouse.base.yaml"
_CM_YAML = _REPO / "ws" / "src" / "warehouse_bringup" / "config" / "collision_monitor.yaml"

# --- config mirrors (literals from the docs / config, not imports) -----------------
THRESH = 0.3  # = safety.emergency_min_distance
TIMEOUT = 10.0  # = safety.blocked_timeout
FRESHNESS = 1.0  # = safety.pose_freshness_timeout
SCAN_FRESHNESS = 1.0  # = safety.scan_freshness_timeout (doc12 末尾【2026-09-16 追補】(3) step 4)
SPEC_TOPIC_TEMPLATE = "/{}/scan"  # doc03:78 contract topic, per bot


def _bot(
    name: str,
    x: float | None = 5.0,
    y: float | None = 5.0,
    batt: float = 100.0,
    blocked: float = 0.0,
    pose_age: float | None = None,
    *,
    scan_age: float | None = None,
    odom_seen: bool = False,
) -> BotState:
    return BotState(name, x, y, batt, blocked, pose_age, scan_age=scan_age, odom_seen=odom_seen)


def _far_bot() -> BotState:
    """A second bot far away, never localized, no scan, no odom -> contributes nothing."""
    return BotState("bot2", 99.0, 99.0, 100.0, 0.0, None)


def _evaluate(a: BotState, b: BotState) -> list:
    return evaluate(
        a,
        b,
        distance_threshold=THRESH,
        blocked_timeout=TIMEOUT,
        pose_freshness_timeout=FRESHNESS,
        scan_freshness_timeout=SCAN_FRESHNESS,
    )


def _scan(decs: list) -> list:
    return [d for d in decs if d.reason == "scan_stale"]


# ============================================================================
# Pure-logic half (independent oracle)
# ============================================================================


@pytest.mark.safety
def test_stale_scan_estops_the_bot() -> None:
    decs = _evaluate(_bot("bot1", scan_age=SCAN_FRESHNESS + 0.5), _far_bot())
    stale = _scan(decs)
    assert len(stale) == 1
    d = stale[0]
    # Precautionary ESTOP (physical stop + goal cancel), never a low-harm recovery.
    assert d.bot == "bot1" and d.action == "estop"
    assert d.detail == {"scan_age": 1.5, "freshness_timeout": 1.0}


@pytest.mark.safety
def test_fresh_scan_is_silent() -> None:
    decs = _evaluate(_bot("bot1", scan_age=0.1, odom_seen=True), _far_bot())
    assert not _scan(decs)


@pytest.mark.safety
def test_threshold_is_strict() -> None:
    # Exactly at the window is NOT stale (strict >, mirroring pose_stale / blocked_timeout);
    # the very next representable float above it IS.
    at = _evaluate(_bot("bot1", scan_age=SCAN_FRESHNESS), _far_bot())
    assert not _scan(at)
    just_over = _evaluate(_bot("bot1", scan_age=math.nextafter(SCAN_FRESHNESS, 2.0)), _far_bot())
    assert len(_scan(just_over)) == 1


@pytest.mark.safety
@pytest.mark.parametrize("age", [0.083, 0.167, 0.5, 0.99])
def test_normal_lidar_cadence_does_not_false_fire(age: float) -> None:
    # T-mini Plus scans at 6-12 Hz (doc23:494) = 83-167 ms between scans; the 1.0 s window is
    # ~6-12 periods, so a normal / jittery cadence must never trip the estop.
    decs = _evaluate(_bot("bot1", scan_age=age, odom_seen=True), _far_bot())
    assert not _scan(decs)


@pytest.mark.safety
def test_never_received_scan_is_stale_only_with_the_odom_witness() -> None:
    # A bot that has proven itself alive (odom arrived) but whose lidar never came up must
    # fail CLOSED ...
    alive = _evaluate(_bot("bot1", scan_age=None, odom_seen=True), _far_bot())
    stale = _scan(alive)
    assert len(stale) == 1 and stale[0].action == "estop"
    assert stale[0].detail == {"scan_age": None, "freshness_timeout": 1.0}
    # ... while an ABSENT bot (single-bot ADR-0006: bot2 never publishes anything) stays
    # silent — otherwise every single-bot run would carry a permanent bot2 estop.
    absent = _evaluate(_bot("bot1", scan_age=None, odom_seen=False), _far_bot())
    assert not _scan(absent)


@pytest.mark.safety
def test_default_botstate_fields_are_a_safe_absence() -> None:
    # Positional construction (the pre-existing 5-arg shape) must not trip scan_stale:
    # scan_age defaults to None and odom_seen to False.
    legacy = BotState("bot1", 5.0, 5.0, 100.0, 0.0)
    assert legacy.scan_age is None and legacy.odom_seen is False
    assert not _scan(_evaluate(legacy, _far_bot()))


@pytest.mark.safety
def test_only_the_stale_bot_is_stopped() -> None:
    decs = _evaluate(
        _bot("bot1", 5.0, 5.0, scan_age=SCAN_FRESHNESS + 1.0, odom_seen=True),
        _bot("bot2", 0.0, 0.0, scan_age=0.05, odom_seen=True),
    )
    assert [d.bot for d in _scan(decs)] == ["bot1"]


@pytest.mark.safety
def test_both_bots_stale_estop_independently() -> None:
    decs = _evaluate(
        _bot("bot1", 5.0, 5.0, scan_age=2.0, odom_seen=True),
        _bot("bot2", 0.0, 0.0, scan_age=None, odom_seen=True),  # never received, alive
    )
    assert sorted(d.bot for d in _scan(decs)) == ["bot1", "bot2"]
    assert all(d.action == "estop" for d in _scan(decs))


@pytest.mark.safety
def test_scan_stale_is_additive_to_other_reasons() -> None:
    # scan_stale can only ADD an estop: a critical battery on the same bot still fires.
    decs = _evaluate(_bot("bot1", batt=5.0, scan_age=3.0, odom_seen=True), _far_bot())
    reasons = sorted(d.reason for d in decs if d.bot == "bot1")
    assert reasons == ["battery_critical", "scan_stale"]
    assert all(d.action == "estop" for d in decs if d.bot == "bot1")


@pytest.mark.safety
def test_scan_stale_auto_clears_when_scans_resume() -> None:
    # Level, not latched: stale -> fires; a fresh scan on the SAME bot next tick -> gone.
    # Sequential in ONE test so any sneaky cross-call latch state is exercised in-process.
    stale = _evaluate(_bot("bot1", scan_age=SCAN_FRESHNESS + 0.5, odom_seen=True), _far_bot())
    assert _scan(stale)
    fresh = _evaluate(_bot("bot1", scan_age=0.1, odom_seen=True), _far_bot())
    assert not _scan(fresh)
    # Same for the never-received case: the first scan (age ~0) releases it.
    never = _evaluate(_bot("bot1", scan_age=None, odom_seen=True), _far_bot())
    assert _scan(never)
    first = _evaluate(_bot("bot1", scan_age=0.0, odom_seen=True), _far_bot())
    assert not _scan(first)


@pytest.mark.safety
def test_scan_stale_requests_the_stop_overlay() -> None:
    # doc05 §4-1: the /bot{n}/stop_state flag keys on action=="estop", so scan_stale reaches
    # the L0' overlay (and through it the teleop path) like every other estop reason.
    assert stop_requested_for([Decision("bot1", "estop", "scan_stale")], "bot1") is True
    assert stop_requested_for([Decision("bot1", "estop", "scan_stale")], "bot2") is False


@pytest.mark.safety
def test_build_event_scan_stale_type_is_additive() -> None:
    # A NEW /emergency/event `type` value only; the frozen core keys (doc12:141-150) are
    # unchanged, so State Cache ingestion / downstream consumers are unaffected.
    detail = {"scan_age": 1.7, "freshness_timeout": 1.0}
    event = build_event("emg-1", "bot1", "scan_stale", 1.0, detail=detail)
    assert event["type"] == "scan_stale"
    assert event["severity"] == "critical"
    assert event["action_taken"] == ["nav2_goal_cancel", "cmd_vel_stop"]  # estop set
    assert event["requires_llm_review"] is True
    assert event["detail"] == detail
    assert set(event) == {
        "event_id",
        "robot",
        "type",
        "severity",
        "action_taken",
        "timestamp",
        "requires_llm_review",
        "detail",
    }


@pytest.mark.safety
def test_scan_stale_event_edge_triggers_not_spam() -> None:
    latch = EdgeLatch()
    stale = [Decision("bot1", "estop", "scan_stale")]
    assert latch.rising(stale) == {("bot1", "scan_stale")}  # rising edge -> emit
    assert latch.rising(stale) == set()  # held -> no re-spam at 20 Hz
    assert latch.rising([]) == set()  # scans resume -> latch resets
    assert latch.rising(stale) == {("bot1", "scan_stale")}  # recurs -> rises again


# --- startup validation of the config value ---------------------------------------


@pytest.mark.safety
@pytest.mark.parametrize("value", [1.0, 0.5, 2, 0.083])
def test_validate_accepts_finite_positive_numbers(value: float) -> None:
    out = validate_scan_freshness_timeout(value)
    assert isinstance(out, float) and out == float(value)


@pytest.mark.safety
@pytest.mark.parametrize(
    "value", [0, 0.0, -1.0, math.nan, math.inf, -math.inf, True, False, "1.0", None, [1.0]]
)
def test_validate_rejects_non_finite_non_positive_or_non_numeric(value: object) -> None:
    # A NaN would silently disable the guard (every `>` comparison is False = fail-OPEN);
    # zero / negative would estop every tick; bools / strings are config typos. All must
    # refuse the node's startup instead of degrading quietly.
    with pytest.raises(ValueError):
        validate_scan_freshness_timeout(value)


# --- config pins ---------------------------------------------------------------------


@pytest.mark.safety
def test_config_default_is_documented_and_inherits_the_cm_source_timeout() -> None:
    base = yaml.safe_load(_BASE_YAML.read_text(encoding="utf-8"))
    value = base["safety"]["scan_freshness_timeout"]
    assert value == SCAN_FRESHNESS  # doc12 末尾【2026-09-16 追補】(3) step 4: 1.0 s, provisional
    assert validate_scan_freshness_timeout(value) == 1.0
    # The doc derives the value from the node-level source_timeout in collision_monitor.yaml
    # ("同一値を継承"). Pin the inheritance so a live re-tune of either side forces the other
    # to be re-decided in docs rather than drifting silently.
    cm = yaml.safe_load(_CM_YAML.read_text(encoding="utf-8"))
    assert cm["collision_monitor"]["ros__parameters"]["source_timeout"] == value


# ============================================================================
# AST-wiring half (the rclpy node; CI has no rclpy)
# ============================================================================


def _tree() -> ast.Module:
    return ast.parse(_GUARDIAN_PY.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found in emergency_guardian.py")


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _fstring_parts(node: ast.expr) -> tuple[str, list[str]]:
    """Collapse an f-string to (template with {} for holes, interpolated Names)."""
    assert isinstance(node, ast.JoinedStr), ast.dump(node)
    template: list[str] = []
    names: list[str] = []
    for part in node.values:
        if isinstance(part, ast.Constant):
            template.append(str(part.value))
        else:
            assert isinstance(part, ast.FormattedValue), ast.dump(part)
            assert isinstance(part.value, ast.Name), "topic interpolation must be a bare loop var"
            template.append("{}")
            names.append(part.value.id)
    return "".join(template), names


def _scan_subscription(tree: ast.Module) -> ast.Call:
    """The one create_subscription whose topic f-string is ``/{}/scan``."""
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_subscription"
        and len(node.args) >= 3
        and isinstance(node.args[1], ast.JoinedStr)
        and _fstring_parts(node.args[1])[0] == SPEC_TOPIC_TEMPLATE
    ]
    assert len(matches) == 1, f"expected exactly one /{{}}/scan subscription, got {len(matches)}"
    return matches[0]


def test_node_subscribes_scan_per_bot_with_sensor_qos() -> None:
    tree = _tree()
    call = _scan_subscription(tree)
    # sensor_msgs/LaserScan (doc03:78) on the BEST_EFFORT sensor profile the poses use.
    assert isinstance(call.args[0], ast.Name) and call.args[0].id == "LaserScan"
    assert isinstance(call.args[3], ast.Name) and call.args[3].id == "sensor_qos"
    # Per bot: inside the single `for bot in _BOTS` loop, interpolating that loop var.
    loops = [
        loop
        for loop in ast.walk(tree)
        if isinstance(loop, ast.For) and any(node is call for node in ast.walk(loop))
    ]
    assert len(loops) == 1, "scan subscription is not inside a single for loop"
    assert isinstance(loops[0].iter, ast.Name) and loops[0].iter.id == "_BOTS"
    _, names = _fstring_parts(call.args[1])
    assert names == [loops[0].target.id], "topic does not interpolate this loop's bot var"
    # Callback routes to _on_scan (a lambda binding the loop var, like the other feeds).
    cb = call.args[2]
    assert isinstance(cb, ast.Lambda)
    body = cb.body
    assert isinstance(body, ast.Call) and isinstance(body.func, ast.Attribute)
    assert body.func.attr == "_on_scan"


def test_on_scan_stamps_the_monotonic_arrival_only() -> None:
    """Marshal only: ONE statement, `self._last_scan_t[bot] = time.monotonic()`; the
    message header/stamp is never read (sim/real clock offsets must not leak in)."""
    fn = _function(_tree(), "_on_scan")
    stmts = [s for s in fn.body if not _is_docstring(s)]
    assert len(stmts) == 1, "marshal only: exactly one statement"
    (stmt,) = stmts
    assert isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
    target = stmt.targets[0]
    assert isinstance(target, ast.Subscript)
    assert isinstance(target.value, ast.Attribute) and target.value.attr == "_last_scan_t"
    assert isinstance(stmt.value, ast.Call)
    assert isinstance(stmt.value.func, ast.Attribute) and stmt.value.func.attr == "monotonic"
    assert isinstance(stmt.value.func.value, ast.Name) and stmt.value.func.value.id == "time"
    forbidden = {"header", "stamp", "ranges", "intensities"}
    touched = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert not (touched & forbidden), f"_on_scan must not inspect the message: {touched}"


def test_on_odom_sets_the_sticky_alive_witness() -> None:
    fn = _function(_tree(), "_on_odom")
    sets = [
        s
        for s in fn.body
        if isinstance(s, ast.Assign)
        and isinstance(s.targets[0], ast.Subscript)
        and isinstance(s.targets[0].value, ast.Attribute)
        and s.targets[0].value.attr == "_odom_seen"
    ]
    assert len(sets) == 1, "_on_odom must set self._odom_seen[bot] exactly once"
    assert isinstance(sets[0].value, ast.Constant) and sets[0].value.value is True


def test_check_safety_passes_the_scan_window_from_the_validated_attribute() -> None:
    fn = _function(_tree(), "_check_safety")
    calls = [
        c
        for c in ast.walk(fn)
        if isinstance(c, ast.Call)
        and isinstance(c.func, ast.Attribute)
        and c.func.attr == "evaluate"
    ]
    assert len(calls) == 1
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert "scan_freshness_timeout" in kw, "evaluate() must receive scan_freshness_timeout"
    v = kw["scan_freshness_timeout"]
    assert isinstance(v, ast.Attribute) and v.attr == "_scan_freshness_timeout"
    assert isinstance(v.value, ast.Name) and v.value.id == "self"


def test_bot_state_carries_scan_age_and_odom_seen() -> None:
    fn = _function(_tree(), "_bot_state")
    calls = [
        c
        for c in ast.walk(fn)
        if isinstance(c, ast.Call)
        and isinstance(c.func, ast.Attribute)
        and c.func.attr == "BotState"
    ]
    assert len(calls) == 1
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert isinstance(kw.get("scan_age"), ast.Name) and kw["scan_age"].id == "scan_age"
    seen = kw.get("odom_seen")
    assert isinstance(seen, ast.Subscript)
    assert isinstance(seen.value, ast.Attribute) and seen.value.attr == "_odom_seen"


def test_init_hard_indexes_and_validates_the_config_key() -> None:
    """`cfg["safety"]["scan_freshness_timeout"]` (a missing key fails loudly) flows through
    `gl.validate_scan_freshness_timeout` into `self._scan_freshness_timeout`."""
    fn = _function(_tree(), "__init__")
    assigns = [
        s
        for s in ast.walk(fn)
        if isinstance(s, ast.Assign)
        and isinstance(s.targets[0], ast.Attribute)
        and s.targets[0].attr == "_scan_freshness_timeout"
    ]
    assert len(assigns) == 1
    value = assigns[0].value
    assert isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
    assert value.func.attr == "validate_scan_freshness_timeout"
    assert isinstance(value.func.value, ast.Name) and value.func.value.id == "gl"
    subs = [
        n
        for n in ast.walk(value)
        if isinstance(n, ast.Subscript)
        and isinstance(n.slice, ast.Constant)
        and n.slice.value == "scan_freshness_timeout"
    ]
    assert len(subs) == 1, "config key must be hard-indexed (no .get fallback)"
    assert not any(isinstance(n, ast.Attribute) and n.attr == "get" for n in ast.walk(value)), (
        "no .get() default may mask a missing scan_freshness_timeout"
    )
