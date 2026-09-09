"""R-26 safety units for the stop-overlay PRODUCER (Emergency Guardian, L1).

Oracle: docs/mode-m1/05-operation-state-and-stop-authority.md §4-1 (the wire
contract the producer must satisfy) — never the implementation. Every expected
value below is a literal transcribed from the doc, never imported from the
module under test (importing the constant would make the assertion a tautology
no mutation can kill). This is the producer mirror of the consumer's
``tests/unit/test_m1_stop_state_contract.py``.

Two layers, the same split as that consumer file:

* a PURE-logic half (``gl.stop_requested_for``) with an independent oracle — the
  stop-flag polarity and per-bot isolation, mutation-killable on the host;
* an AST-WIRING half (§4-1 R-26 ⑪): CI has no rclpy, so the node's publisher /
  QoS / periodic-publish / derivation wiring is pinned by parsing the source,
  exactly the ``test_emergency_mirror_wiring.py`` /
  ``test_speed_band_bringup_wiring.py`` idiom. Pins are EXACT (not substring) so
  a hardcoded ``True``/``False``, a dropped publish, an inverted polarity, or a
  ``transient_local`` QoS fails rather than passing green.

Contract as pinned by doc05 §4-1:

  topic    /bot{n}/stop_state          (per-bot)
  type     std_msgs/String  (JSON)
  payload  {"stop_requested": <bool>, "valid_until": <float>}
  QoS      RELIABLE / KEEP_LAST depth 1 / VOLATILE (never transient_local)
  clock    one shared CLOCK_MONOTONIC; wall clock is forbidden
  publish  periodic — every tick, stop-requested or not
  deadline valid_until = now + window; monotonically non-decreasing (because now
           is monotonic and the window is a positive constant)
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest
from warehouse_safety.guard_logic import Decision, stop_requested_for

pytestmark = [pytest.mark.safety, pytest.mark.unit]

# --- spec literals (doc05 §4-1). Do NOT import these from the implementation. ---
SPEC_TOPIC_TEMPLATE = "/{}/stop_state"  # f-string collapsed form (see _fstring_parts)
SPEC_WINDOW_S = 0.5  # borrowed from the consumer ceiling (twist_mux timeout), §4-1
SPEC_QOS_RELIABILITY = "RELIABLE"
SPEC_QOS_HISTORY = "KEEP_LAST"
SPEC_QOS_DEPTH = 1
SPEC_QOS_DURABILITY = "VOLATILE"
SPEC_WINDOW_CONST = "DEFAULT_STOP_STATE_VALID_WINDOW_S"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUARDIAN_PY = _REPO_ROOT / "ws/src/warehouse_safety/warehouse_safety/emergency_guardian.py"


def _estop(bot: str, reason: str = "near_collision") -> Decision:
    return Decision(bot, "estop", reason)


def _recovery(bot: str) -> Decision:
    return Decision(bot, "recovery", "blocked_timeout")


# --------------------------------------------------------------------------
# pure logic: the stop flag is True iff THIS bot has an estop this tick
# --------------------------------------------------------------------------


def test_stop_requested_true_iff_the_bot_has_an_estop_this_tick() -> None:
    decs = [_estop("bot1")]
    assert stop_requested_for(decs, "bot1") is True
    assert stop_requested_for(decs, "bot2") is False  # a different bot's estop is not mine


def test_no_decisions_means_no_stop_request() -> None:
    # An all-clear tick publishes stop_requested=False (permit), never True.
    assert stop_requested_for([], "bot1") is False


def test_recovery_decision_is_not_a_stop_request() -> None:
    # blocked_timeout is low-harm (event only); the overlay must keep driving.
    assert stop_requested_for([_recovery("bot1")], "bot1") is False


def test_stop_request_is_isolated_per_bot() -> None:
    decs = [_estop("bot1"), _recovery("bot2")]
    assert stop_requested_for(decs, "bot1") is True
    assert stop_requested_for(decs, "bot2") is False


@pytest.mark.parametrize(
    "reason", ["near_collision", "battery_critical", "pose_stale", "operator_stop_request"]
)
def test_any_estop_reason_sets_the_flag(reason: str) -> None:
    # The flag keys on action=="estop", NOT on a specific reason — every estop
    # reason must stop the overlay, including the latched operator request.
    assert stop_requested_for([Decision("bot1", "estop", reason)], "bot1") is True


def test_mixed_estop_and_recovery_on_the_same_bot_is_a_stop() -> None:
    # A bot blocked AND in proximity must read as stop (estop wins over recovery).
    assert stop_requested_for([_recovery("bot1"), _estop("bot1")], "bot1") is True


# --------------------------------------------------------------------------
# AST wiring (§4-1 R-26 ⑪): CI has no rclpy — parse the source instead
# --------------------------------------------------------------------------


def _tree() -> ast.Module:
    return ast.parse(_GUARDIAN_PY.read_text(encoding="utf-8"))


def _module_constant(tree: ast.Module, name: str) -> object:
    """Read a module constant by literal-eval (Assign or AnnAssign)."""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return ast.literal_eval(value)
    raise AssertionError(f"module constant {name} not found in emergency_guardian.py")


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found in emergency_guardian.py")


def _is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)


def _code_body(fn: ast.FunctionDef) -> str:
    """Unparse a function's CODE only (drop the docstring).

    Prose mentioning ``time.monotonic()`` / ``gl.stop_requested_for`` must not let a
    substring check pass on the comment instead of the code it is asserting about.
    """
    return "\n".join(ast.unparse(stmt) for stmt in fn.body if not _is_docstring(stmt))


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


def _stop_state_publisher(tree: ast.Module) -> ast.Call:
    """The one create_publisher whose topic f-string is ``/{}/stop_state``.

    Keyed on the TOPIC, so a second String publisher cannot silently capture the
    pin, and a ``+ "_x"``-style typo fails the exact template check below.
    """
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_publisher"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.JoinedStr)
        and _fstring_parts(node.args[1])[0] == SPEC_TOPIC_TEMPLATE
    ]
    assert len(matches) == 1, f"expected exactly one /{{}}/stop_state publisher, got {len(matches)}"
    return matches[0]


def test_producer_window_matches_the_consumer_ceiling_borrow() -> None:
    """doc05 §4-1: the window is BORROWED from the consumer's 0.5s (twist_mux
    timeout), not a freshly invented literal. A drift from 0.5 fails here."""
    assert _module_constant(_tree(), SPEC_WINDOW_CONST) == SPEC_WINDOW_S


def test_producer_window_is_finite_and_positive() -> None:
    # A non-positive / non-finite window would make valid_until <= now (always
    # expired = overlay permanently stopped) or non-finite (consumer revokes).
    window = _module_constant(_tree(), SPEC_WINDOW_CONST)
    assert isinstance(window, float)
    assert math.isfinite(window) and window > 0.0


def test_publisher_has_the_contracted_topic_and_type() -> None:
    call = _stop_state_publisher(_tree())
    assert isinstance(call.args[0], ast.Name) and call.args[0].id == "String"  # std_msgs/String
    template, names = _fstring_parts(call.args[1])
    assert template == SPEC_TOPIC_TEMPLATE, f"topic shape != contract: {template!r}"
    assert len(names) == 1, "topic must interpolate exactly the loop bot var"


def test_publisher_is_created_for_every_bot_in_the_bots_loop() -> None:
    """Per-bot: the publisher lives in ``for bot in _BOTS`` and interpolates that
    loop var — not a single hardcoded ``/bot1/stop_state`` (bot2 would go silent)."""
    tree = _tree()
    call = _stop_state_publisher(tree)
    loops = [
        loop
        for loop in ast.walk(tree)
        if isinstance(loop, ast.For) and any(node is call for node in ast.walk(loop))
    ]
    assert len(loops) == 1, "stop_state publisher is not inside a single for loop"
    loop = loops[0]
    assert isinstance(loop.iter, ast.Name) and loop.iter.id == "_BOTS", ast.dump(loop.iter)
    _, names = _fstring_parts(call.args[1])
    assert names == [loop.target.id], "topic does not interpolate this loop's bot var"


def test_publisher_qos_is_reliable_keep_last_1_volatile() -> None:
    """§4-1: RELIABLE / KEEP_LAST / depth 1 / VOLATILE — its OWN profile, explicit.

    transient_local would replay a stale (possibly permissive) deadline to a
    late-joining driver = fail-open; the depth-10 estop profile must not be reused.
    """
    tree = _tree()
    call = _stop_state_publisher(tree)
    qos_arg = call.args[2]
    assert isinstance(qos_arg, ast.Name), "QoS passed as a raw value, not a named QoSProfile"
    qos_calls = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == qos_arg.id for t in node.targets)
        and isinstance(node.value, ast.Call)
    ]
    assert len(qos_calls) == 1, f"{qos_arg.id} is not bound exactly once"
    qos = qos_calls[0]
    assert isinstance(qos.func, ast.Name) and qos.func.id == "QoSProfile"
    kwargs = {kw.arg: kw.value for kw in qos.keywords}
    reliability = kwargs.get("reliability")
    assert isinstance(reliability, ast.Attribute) and reliability.attr == SPEC_QOS_RELIABILITY
    history = kwargs.get("history")
    assert isinstance(history, ast.Attribute) and history.attr == SPEC_QOS_HISTORY
    depth = kwargs.get("depth")
    assert isinstance(depth, ast.Constant) and depth.value == SPEC_QOS_DEPTH
    durability = kwargs.get("durability")
    assert isinstance(durability, ast.Attribute) and durability.attr == SPEC_QOS_DURABILITY, (
        "durability=VOLATILE not explicit (transient_local would fail-open)"
    )


def test_check_safety_publishes_stop_state_every_tick_unconditionally() -> None:
    """§4-1 "常時流れる設計": the publish runs on EVERY tick, fed THIS tick's
    decisions + the reused monotonic now — not guarded by a stop condition."""
    tree = _tree()
    check = _function(tree, "_check_safety")
    calls = [
        node
        for node in ast.walk(check)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_publish_stop_state"
    ]
    assert len(calls) == 1, "expected exactly one _publish_stop_state call in _check_safety"
    call = calls[0]
    # It must NOT be nested in an if/for — wrapping it in `if <stop>:` would make the
    # feed go silent when no stop is active (fail-closed the consumer but not "periodic").
    for node in ast.walk(check):
        if isinstance(node, (ast.If, ast.For)):
            assert not any(n is call for n in ast.walk(node)), (
                "_publish_stop_state is inside a branch/loop — not published every tick"
            )
    assert len(call.args) == 2
    assert isinstance(call.args[0], ast.Name) and call.args[0].id == "decisions"
    assert isinstance(call.args[1], ast.Name) and call.args[1].id == "now"


def test_check_safety_samples_the_monotonic_clock_once() -> None:
    """The 50ms tick binds ``now = time.monotonic()`` once and reuses it, so the
    published deadline shares the consumer's CLOCK_MONOTONIC domain (§4-1)."""
    check = _function(_tree(), "_check_safety")
    now_binds = [
        node
        for node in check.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "now" for t in node.targets)
    ]
    assert len(now_binds) == 1, "`now` must be bound exactly once in _check_safety"
    assert ast.unparse(now_binds[0].value) == "time.monotonic()"


def test_publish_stop_state_reuses_now_and_never_resamples_a_clock() -> None:
    """valid_until = now + window (monotone), and the producer takes NO clock of its
    own — a fresh time.monotonic()/time.time()/get_clock() here could diverge from
    the consumer's clock, and a constant valid_until would break monotonicity (§4-1)."""
    body = _code_body(_function(_tree(), "_publish_stop_state"))
    assert f"now + {SPEC_WINDOW_CONST}" in body, "valid_until is not `now + window`"
    assert "time.monotonic" not in body
    assert "time.time" not in body
    assert "get_clock" not in body


def test_stop_flag_is_the_bare_pure_helper_call_not_wrapped_or_hardcoded() -> None:
    """The payload ``stop_requested`` value must be EXACTLY ``gl.stop_requested_for(
    decisions, bot)`` — not a hardcoded bool, and not a ``not`` / ``and`` / ``or``
    wrapper around it (§4-1 R-26 ⑪ "guard の極性〔``if not …`` / ``… or True`` を通さない〕").

    A substring check ("gl.stop_requested_for(...)" in body) would let a node-site
    ``not gl.stop_requested_for(...)`` or ``... and False`` slip through: CI cannot run
    the node, and the pure suite exercises the helper directly (never the node's
    wrapper), so that fail-OPEN mutant (stop_requested=False DURING a real estop) would
    otherwise survive green. Pin the AST node itself instead.
    """
    fn = _function(_tree(), "_publish_stop_state")
    dicts = [node for node in ast.walk(fn) if isinstance(node, ast.Dict)]
    assert len(dicts) == 1, "expected exactly one payload dict literal in _publish_stop_state"
    keyed: dict[object, ast.expr] = {}
    for key, value in zip(dicts[0].keys, dicts[0].values, strict=True):
        assert isinstance(key, ast.Constant), ast.dump(key)  # no **spread / computed keys
        keyed[key.value] = value
    assert set(keyed) == {"stop_requested", "valid_until"}, "payload keys != §4-1 contract"
    flag = keyed["stop_requested"]
    # EXACTLY the bare call: UnaryOp(not ...) / BoolOp(... and/or ...) / Constant all fail.
    assert isinstance(flag, ast.Call), f"stop_requested is not a bare call: {ast.dump(flag)}"
    assert ast.unparse(flag) == "gl.stop_requested_for(decisions, bot)", ast.unparse(flag)
    # valid_until is the monotone `now + window` (bound above); a bare constant here fails.
    assert isinstance(keyed["valid_until"], ast.Name) and keyed["valid_until"].id == "valid_until"


def test_stop_state_is_published_per_bot_as_a_json_string() -> None:
    fn = _function(_tree(), "_publish_stop_state")
    body = _code_body(fn)
    assert "self._stop_state_pub[bot].publish(String(data=json.dumps(payload)))" in body
    loops = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Name) and node.iter.id == "_BOTS"
    ]
    assert len(loops) == 1, "_publish_stop_state must iterate every bot in _BOTS"


# --------------------------------------------------------------------------
# non-regression: this slice is ADDITIVE — the existing estop path is unchanged
# --------------------------------------------------------------------------


def test_existing_emergency_stop_publish_is_unchanged() -> None:
    """doc05 §4-1 is additive: the estop zero-Twist to /cmd_vel/emergency + the
    Nav2 goal cancel must still fire every tick. Deleting the estop publish must
    go red here (it is not exercised by the rclpy-free guard_logic suite)."""
    body = _code_body(_function(_tree(), "_emergency_stop"))
    assert "self._cmd_pub[dec.bot].publish(Twist())" in body
    assert "self._cancel_all_goals(dec.bot)" in body


def test_estop_profile_stays_depth10_and_is_distinct_from_stop_state_profile() -> None:
    """The stop_state depth-1 feed must not reuse or mutate the estop depth-10
    profile (silent QoS no-match would fail-open the estop/event path)."""
    tree = _tree()
    reliable = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "reliable_qos" for t in node.targets)
        and isinstance(node.value, ast.Call)
    ]
    assert len(reliable) == 1, "reliable_qos (estop/event profile) must be bound once"
    depth = {kw.arg: kw.value for kw in reliable[0].keywords}.get("depth")
    assert isinstance(depth, ast.Constant) and depth.value == 10
