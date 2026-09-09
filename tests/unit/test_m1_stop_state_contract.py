"""R-26 safety units for the stop-overlay INPUT CONTRACT (L0', 停止上乗せ).

Oracle: docs/mode-m1/05-operation-state-and-stop-authority.md §4-1 (the wire
contract) plus the §4 R-26 list it extends — not the implementation. Every
expected value below is a literal transcribed from the doc, never imported
from the module under test (importing the constant would make the assertion a
tautology that no mutation can kill).

Contract as pinned by doc05 §4-1:

  topic    /bot{n}/stop_state          (per-bot)
  type     std_msgs/String  (JSON)
  payload  {"stop_requested": <bool>, "valid_until": <float>}
  QoS      RELIABLE / KEEP_LAST depth 1 / VOLATILE (never transient_local)
  clock    one shared monotonic clock; wall clock is forbidden
  deadline monotonically non-decreasing watermark; regressing / non-finite /
           non-positive deadlines REVOKE immediately (they are not ignored)
  window   clipped by stop_state_max_validity_s, a param SEPARATE from W-1's
           cmd_vel_timeout_s
  malformed payloads REVOKE (opposite direction to /operator/stop_request,
           because this input *permits* driving rather than engaging a stop)

Requirements exercised here (doc05 §4-1 R-26 additive ⑨⑩⑪ plus the §4 ①〜⑧
list re-anchored to the wire): a malformed or replayed message must never
grant driving, must not poison the watermark, and the default-disabled
configuration must not even create the subscription.
"""

from __future__ import annotations

import ast
import json
import math
from pathlib import Path

import pytest
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY
from warehouse_m1_driver.driver_core import M1DriverCore
from warehouse_m1_driver.stop_state import decode_stop_state

pytestmark = [pytest.mark.safety, pytest.mark.unit]

# --- spec literals (doc05 §4-1). Do not import these from the implementation.
SPEC_TOPIC_TEMPLATE = "/{bot}/stop_state"
SPEC_MAX_VALIDITY_S = 0.5
SPEC_QOS_RELIABILITY = "RELIABLE"
SPEC_QOS_HISTORY = "KEEP_LAST"
SPEC_QOS_DEPTH = 1
SPEC_QOS_DURABILITY = "VOLATILE"
SPEC_ENABLE_PARAM = "stop_overlay_enabled"
SPEC_WINDOW_PARAM = "stop_state_max_validity_s"

CAP = MAX_LINEAR_VELOCITY

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PKG = _REPO_ROOT / "ws/src/warehouse_m1_driver/warehouse_m1_driver"
_DRIVER_NODE_PY = _PKG / "driver_node.py"
_STOP_STATE_PY = _PKG / "stop_state.py"


class FakeBackend:
    """Records every call with its arguments, in order."""

    def __init__(self) -> None:
        self.log: list[tuple] = []

    def set_body_velocity(self, vx: float, vy: float, wz: float) -> None:
        self.log.append(("set_body_velocity", vx, vy, wz))

    def stop_brake(self) -> None:
        self.log.append(("stop_brake",))

    def reset_state(self) -> None:
        self.log.append(("reset_state",))

    def close(self) -> None:
        self.log.append(("close",))

    @property
    def moved(self) -> bool:
        """True if any frame carried a non-zero velocity component."""
        return any(
            call[0] == "set_body_velocity" and any(v != 0.0 for v in call[1:]) for call in self.log
        )


def payload(stop_requested: bool, valid_until: float) -> str:
    return json.dumps({"stop_requested": stop_requested, "valid_until": valid_until})


def feed(core: M1DriverCore, raw: str, now: float, window: float = SPEC_MAX_VALIDITY_S) -> None:
    """The node's wire path: decode, then hand the pair to the core."""
    stop_requested, valid_until = decode_stop_state(raw, now, window)
    core.on_stop_state(stop_requested, valid_until, now)


def assert_revokes(raw: str, window: float = SPEC_MAX_VALIDITY_S) -> None:
    """Assert the spec BEHAVIOUR of "revoke now", not the returned tuple shape.

    doc05 §4-1: an update we cannot fully understand must DROP the standing
    permission (not be ignored and coast to the old deadline), and must not
    poison the watermark for the next legitimate update.

    BOTH halves are asserted, deliberately:

    (a) the decoder must signal *revoke*, not merely "fail to grant". Behaviour
        alone cannot see this — a decoder that returned a tiny positive
        deadline would also produce no motion, because the core's watermark
        happens to reject it. That masking is an accident of the surrounding
        state, not the contract.
    (b) the resulting behaviour through the core, so a change that merely
        relabels the revoke sentinel cannot pass while driving regresses.
    """
    stop_requested, valid_until = decode_stop_state(raw, 100.0, window)
    assert stop_requested is True and not math.isfinite(valid_until), (
        "decoder must return the revoke signal, not a deadline that merely fails to grant"
    )
    core, backend = enabled_core()
    feed(core, payload(False, 100.4), 100.0)  # a standing permission
    feed(core, raw, 100.05, window)  # the suspect update
    core.on_cmd_vel(0.1, 0.0, 0.0, 100.1)  # still inside the OLD deadline
    assert not backend.moved, "suspect update was ignored instead of revoking"
    feed(core, payload(False, 100.6), 100.2)  # a later legitimate state
    core.on_cmd_vel(0.1, 0.0, 0.0, 100.3)
    assert backend.moved, "revoking raised the watermark and locked the producer out"


# --------------------------------------------------------------------------
# ⑨ decode: the contracted shape, and everything that is not it
# --------------------------------------------------------------------------


def test_valid_non_stop_payload_grants_until_its_deadline() -> None:
    assert decode_stop_state(payload(False, 100.4), now=100.0, max_validity_s=0.5) == (False, 100.4)


def test_valid_stop_payload_reports_the_stop_request() -> None:
    stop_requested, valid_until = decode_stop_state(payload(True, 100.4), 100.0, 0.5)
    assert stop_requested is True
    assert valid_until == 100.4


def test_integer_deadline_is_accepted_as_a_float() -> None:
    """JSON has one number type; `"valid_until": 100` is a legal deadline."""
    assert decode_stop_state('{"stop_requested": false, "valid_until": 100}', 99.9, 0.5) == (
        False,
        100.0,
    )


def test_unknown_keys_are_ignored_forward_compatibility() -> None:
    raw = '{"stop_requested": false, "valid_until": 100.4, "reason": "none", "seq": 7}'
    assert decode_stop_state(raw, 100.0, 0.5) == (False, 100.4)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        "{",
        '{"stop_requested": false, "valid_until": 100.4',  # truncated
    ],
)
def test_unparseable_payload_revokes(raw: str) -> None:
    assert_revokes(raw)


@pytest.mark.parametrize("raw", ["[]", "123", '"engage"', "null", "true"])
def test_non_object_json_revokes(raw: str) -> None:
    assert_revokes(raw)


def test_deeply_nested_payload_revokes_instead_of_crashing() -> None:
    """RecursionError is not a ValueError: unhandled it would kill the driver."""
    assert_revokes("[" * 20000 + "]" * 20000)


@pytest.mark.parametrize("digits", [309, 4400])
def test_unrepresentable_integer_deadline_revokes_instead_of_crashing(digits: int) -> None:
    """A legal JSON int that float() cannot represent raises OverflowError.

    ~350 bytes from any graph participant must revoke, never escape the
    decoder (doc05 §4-1 R-26 ⑨).
    """
    huge = "9" * digits
    assert_revokes(f'{{"stop_requested": false, "valid_until": {huge}}}')


def test_duplicate_keys_revoke() -> None:
    """JSON last-wins would let a stop request be overwritten by a grant."""
    assert_revokes('{"stop_requested": true, "valid_until": 100.4, "stop_requested": false}')


@pytest.mark.parametrize(
    "raw",
    [
        "{}",
        '{"valid_until": 100.4}',  # missing stop_requested
        '{"stop_requested": false}',  # missing valid_until
        '{"stop_requested": "false", "valid_until": 100.4}',  # string, not bool
        '{"stop_requested": 0, "valid_until": 100.4}',  # int, not bool
        '{"stop_requested": null, "valid_until": 100.4}',
        '{"stop_requested": false, "valid_until": "100.4"}',  # string deadline
        '{"stop_requested": false, "valid_until": null}',
        '{"stop_requested": false, "valid_until": true}',  # bool must not read as 1.0
    ],
)
def test_missing_or_mistyped_keys_revoke(raw: str) -> None:
    assert_revokes(raw)


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_deadline_revokes(bad: str) -> None:
    assert_revokes(f'{{"stop_requested": false, "valid_until": {bad}}}')


@pytest.mark.parametrize("bad", [0.0, -1.0, -1e300])
def test_non_positive_deadline_revokes(bad: float) -> None:
    assert_revokes(payload(False, bad))


# --------------------------------------------------------------------------
# ⑩ the permission window is clipped by its own (separate) parameter
# --------------------------------------------------------------------------


def test_over_long_deadline_is_clipped_to_the_window_ceiling() -> None:
    """A producer bug must not turn into an unbounded driving permission."""
    _, valid_until = decode_stop_state(payload(False, 1.0e9), now=100.0, max_validity_s=0.5)
    assert valid_until == pytest.approx(100.5)


def test_deadline_inside_the_window_is_left_alone() -> None:
    _, valid_until = decode_stop_state(payload(False, 100.2), now=100.0, max_validity_s=0.5)
    assert valid_until == pytest.approx(100.2)


@pytest.mark.parametrize("bad_window", [0.0, -1.0, float("nan"), float("inf")])
def test_degenerate_window_falls_back_to_the_default_instead_of_disarming(
    bad_window: float,
) -> None:
    """A DEGENERATE param (non-finite / non-positive) must not remove the ceiling.

    Scope is deliberately exactly that: a large *finite* window is a trusted
    operator setting, pinned separately below. Claiming more here would be an
    oracle the implementation does not hold.
    """
    _, valid_until = decode_stop_state(payload(False, 1.0e9), now=100.0, max_validity_s=bad_window)
    assert valid_until == pytest.approx(100.0 + SPEC_MAX_VALIDITY_S)


def test_a_large_finite_window_is_honoured_as_a_trusted_operator_setting() -> None:
    """DOCUMENTED, NOT ENDORSED — doc05 §4-1「開いている点」.

    ``stop_state_max_validity_s`` is trusted the same way W-1's
    ``cmd_vel_timeout_s`` is (both disarm their floor if an operator sets them
    absurdly high; that idiom predates this slice). Pinned so the fail-open
    surface is visible in the suite instead of merely implied, and so a future
    hard ceiling lands as a deliberate, reviewed change to this test.
    """
    _, valid_until = decode_stop_state(payload(False, 1.0e9), now=100.0, max_validity_s=3600.0)
    assert valid_until == pytest.approx(3700.0)


def test_window_ceiling_is_not_the_w1_command_timeout_knob() -> None:
    """doc05 §4 / §4-1: separate params — moving one must not move the other.

    Driving W-1 to a long timeout leaves the overlay window at its own value.
    """
    backend = FakeBackend()
    core = M1DriverCore(backend, cmd_timeout_s=60.0, stop_overlay_enabled=True)
    feed(core, payload(False, 1.0e9), now=100.0, window=0.2)
    core.on_cmd_vel(0.1, 0.0, 0.0, 100.1)
    assert backend.moved  # inside the 0.2 s overlay window
    backend.log.clear()
    core.on_cmd_vel(0.1, 0.0, 0.0, 100.3)  # past it, though W-1 is nowhere near
    assert backend.log == [("stop_brake",)]


# --------------------------------------------------------------------------
# ①〜③ end to end over the wire: stop / stale / never-received / malformed
# --------------------------------------------------------------------------


def enabled_core() -> tuple[M1DriverCore, FakeBackend]:
    backend = FakeBackend()
    return M1DriverCore(backend, cmd_timeout_s=0.5, stop_overlay_enabled=True), backend


def test_stop_request_over_the_wire_yields_zero_output() -> None:
    core, backend = enabled_core()
    feed(core, payload(True, 100.4), 100.0)
    core.on_cmd_vel(0.2, 0.0, 0.5, 100.1)
    assert not backend.moved
    assert ("stop_brake",) in backend.log


def test_nothing_received_yet_holds_the_stop_side() -> None:
    core, backend = enabled_core()
    core.on_cmd_vel(0.2, 0.0, 0.0, 100.0)
    assert not backend.moved


def test_permission_expires_at_its_deadline() -> None:
    core, backend = enabled_core()
    feed(core, payload(False, 100.4), 100.0)
    core.on_cmd_vel(0.1, 0.0, 0.0, 100.3)
    assert backend.moved
    backend.log.clear()
    core.on_cmd_vel(0.1, 0.0, 0.0, 100.5)
    assert not backend.moved


def test_malformed_message_revokes_a_standing_permission_immediately() -> None:
    """Not "ignore and coast to the deadline" — the permission drops at once."""
    core, backend = enabled_core()
    feed(core, payload(False, 100.4), 100.0)
    feed(core, "{garbage", 100.1)
    core.on_cmd_vel(0.1, 0.0, 0.0, 100.2)  # still well inside the old deadline
    assert not backend.moved


def test_stop_request_revokes_a_standing_permission_immediately() -> None:
    core, backend = enabled_core()
    feed(core, payload(False, 100.4), 100.0)
    feed(core, payload(True, 100.4), 100.1)
    core.on_cmd_vel(0.1, 0.0, 0.0, 100.2)
    assert not backend.moved


# --------------------------------------------------------------------------
# deadline discipline: the monotonic watermark
# --------------------------------------------------------------------------


def test_regressing_deadline_is_rejected_and_revokes() -> None:
    """A replayed / out-of-order state must not resurrect an older permission."""
    core, backend = enabled_core()
    feed(core, payload(False, 100.4), 100.0)
    feed(core, payload(False, 100.2), 100.05)  # regression below the watermark
    core.on_cmd_vel(0.1, 0.0, 0.0, 100.1)
    assert not backend.moved


def test_a_rejected_message_does_not_raise_the_watermark() -> None:
    """After a malformed update, the next legitimate state must still be accepted."""
    core, backend = enabled_core()
    feed(core, payload(False, 100.4), 100.0)
    feed(core, '{"stop_requested": false, "valid_until": "oops"}', 100.05)
    feed(core, payload(False, 100.6), 100.1)
    core.on_cmd_vel(0.1, 0.0, 0.0, 100.2)
    assert backend.moved


def test_clipping_keeps_later_legitimate_deadlines_acceptable() -> None:
    """An over-long deadline is clipped, so it cannot lock out the producer."""
    core, backend = enabled_core()
    feed(core, payload(False, 1.0e9), now=100.0)  # clipped to 100.5
    feed(core, payload(False, 100.7), now=100.2)  # above the clipped watermark
    core.on_cmd_vel(0.1, 0.0, 0.0, 100.6)
    assert backend.moved


def test_equal_deadline_is_not_a_regression() -> None:
    """Monotonically NON-decreasing: a repeated deadline stays acceptable."""
    core, backend = enabled_core()
    feed(core, payload(False, 100.4), 100.0)
    feed(core, payload(False, 100.4), 100.1)
    core.on_cmd_vel(0.1, 0.0, 0.0, 100.2)
    assert backend.moved


# --------------------------------------------------------------------------
# ④ negative oracle: disabled is the default and changes nothing
# --------------------------------------------------------------------------


def test_wire_feed_is_inert_while_the_overlay_is_disabled() -> None:
    backend = FakeBackend()
    core = M1DriverCore(backend, cmd_timeout_s=0.5)  # default: disabled
    feed(core, payload(True, 100.4), 100.0)  # a stop request…
    core.on_cmd_vel(0.1, 0.0, 0.0, 100.1)
    assert backend.log == [("set_body_velocity", 0.1, 0.0, 0.0)]  # …changes nothing


def test_disabled_core_matches_a_run_that_never_saw_the_wire_at_all() -> None:
    """Bit-identical call log, not merely "also drives"."""
    a_backend, b_backend = FakeBackend(), FakeBackend()
    a = M1DriverCore(a_backend, cmd_timeout_s=0.5)
    b = M1DriverCore(b_backend, cmd_timeout_s=0.5)
    for t, raw in [(100.0, payload(True, 100.4)), (100.2, "{garbage")]:
        feed(a, raw, t)
    for core in (a, b):
        core.on_cmd_vel(0.25, 0.0, 0.4, 100.3)
        core.on_watchdog_tick(100.35)
        core.on_watchdog_tick(101.0)
        core.shutdown_sequence()
    assert a_backend.log == b_backend.log


# --------------------------------------------------------------------------
# ⑤⑥⑦ the surrounding floors keep working over the wire path
# --------------------------------------------------------------------------


def test_wire_granted_pass_through_still_goes_through_the_clamp() -> None:
    core, backend = enabled_core()
    feed(core, payload(False, 100.4), 100.0)
    core.on_cmd_vel(10.0, 0.0, 0.0, 100.1)
    sent = [c for c in backend.log if c[0] == "set_body_velocity"]
    assert len(sent) == 1
    assert math.hypot(sent[0][1], sent[0][2]) <= CAP + 1e-12


def test_w1_still_brakes_even_while_the_wire_grants_permission() -> None:
    """AND composition: a fresh permission cannot revive a dead command stream."""
    core, backend = enabled_core()
    feed(core, payload(False, 1.0e9), 100.0)
    core.on_cmd_vel(0.1, 0.0, 0.0, 100.0)
    backend.log.clear()
    core.on_watchdog_tick(100.8)  # > cmd_timeout_s
    assert backend.log == [("stop_brake",)]


def test_overlay_stop_brakes_on_the_watchdog_even_with_a_fresh_command_stream() -> None:
    core, backend = enabled_core()
    core.on_cmd_vel(0.1, 0.0, 0.0, 100.0)
    feed(core, payload(True, 100.9), 100.0)
    backend.log.clear()
    core.on_watchdog_tick(100.1)  # W-1 fresh, overlay says stop
    assert backend.log == [("stop_brake",)]


def test_w2_shutdown_sequence_is_unchanged_by_the_wire_path() -> None:
    core, backend = enabled_core()
    feed(core, payload(False, 100.4), 100.0)
    core.shutdown_sequence()
    core.shutdown_sequence()
    assert backend.log == [("stop_brake",), ("reset_state",)]


# --------------------------------------------------------------------------
# ⑧ injected clock only — no wall clock inside the decoder
# --------------------------------------------------------------------------


def test_decoder_reads_no_clock_of_its_own() -> None:
    source = _STOP_STATE_PY.read_text(encoding="utf-8")
    for forbidden in ("import time", "time.time", "time.monotonic", "datetime"):
        assert forbidden not in source, f"{forbidden} in stop_state.py — clock must be injected"


# --------------------------------------------------------------------------
# ⑪ wiring layer AST pins (CI has no rclpy; parse the source instead —
#    test_speed_band_bringup_wiring.py / test_emergency_mirror_wiring.py idiom)
# --------------------------------------------------------------------------


def _node_tree() -> ast.Module:
    return ast.parse(_DRIVER_NODE_PY.read_text(encoding="utf-8"))


def _calls(tree: ast.AST, func_name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == func_name
    ]


def _stop_state_subscription(tree: ast.Module) -> ast.Call:
    """The one subscription whose callback is the stop-state handler.

    Keyed on the CALLBACK, not on the message type: a second String
    subscription must not be able to silently capture this pin.
    """
    matches = [
        call
        for call in _calls(tree, "create_subscription")
        if len(call.args) >= 3 and ast.unparse(call.args[2]) == "self._on_stop_state"
    ]
    assert len(matches) == 1, (
        f"expected exactly one _on_stop_state subscription, got {len(matches)}"
    )
    return matches[0]


def test_node_subscribes_the_contracted_topic_with_the_contracted_type() -> None:
    call = _stop_state_subscription(_node_tree())
    assert ast.unparse(call.args[0]) == "String"  # std_msgs/String, doc05 §4-1
    # Exact, not substring: `...format(bot=bot) + '_x'` must fail this pin.
    assert ast.unparse(call.args[1]) == "STOP_STATE_TOPIC_TEMPLATE.format(bot=bot)"


def test_topic_template_matches_the_contract() -> None:
    source = _STOP_STATE_PY.read_text(encoding="utf-8")
    assert f'STOP_STATE_TOPIC_TEMPLATE: Final[str] = "{SPEC_TOPIC_TEMPLATE}"' in source


def test_subscription_qos_is_reliable_keep_last_1_volatile() -> None:
    call = _stop_state_subscription(_node_tree())
    qos = next(
        (
            arg
            for arg in list(call.args) + [kw.value for kw in call.keywords]
            if isinstance(arg, ast.Call) and ast.unparse(arg.func) == "QoSProfile"
        ),
        None,
    )
    assert qos is not None, "stop_state subscription must set an explicit QoSProfile"
    fields = {kw.arg: ast.unparse(kw.value) for kw in qos.keywords}
    assert fields["reliability"] == f"ReliabilityPolicy.{SPEC_QOS_RELIABILITY}"
    assert fields["history"] == f"HistoryPolicy.{SPEC_QOS_HISTORY}"
    assert fields["depth"] == str(SPEC_QOS_DEPTH)
    # transient_local would replay a stale permission to a late subscriber.
    assert fields["durability"] == f"DurabilityPolicy.{SPEC_QOS_DURABILITY}"


def test_subscription_only_exists_while_the_overlay_is_enabled() -> None:
    """Default-off must leave the standalone ROS graph untouched, not just the
    command path (doc05 §4 table row 1 / §4-1 consumer row).

    The guard's TEST is matched exactly, so an inverted (`if not ...`) or
    weakened (`... or True`) guard fails rather than passing on a substring.
    """
    tree = _node_tree()
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and ast.unparse(node.test) == f"self._core.{SPEC_ENABLE_PARAM}":
            for inner in node.body:
                guarded.update(id(c) for c in _calls(inner, "create_subscription"))
    assert id(_stop_state_subscription(tree)) in guarded


def test_callback_feeds_the_decoded_pair_through_to_the_core_unaltered() -> None:
    """The whole point of the wiring: decode -> core, with nothing hardcoded.

    Pinned by exact source shape because CI cannot import rclpy to run it.
    Without this, `on_stop_state(False, ...)` (every stop read as "no stop" =
    fail-open) or dropping the core call entirely passes the whole suite.
    """
    callback = next(
        node
        for node in ast.walk(_node_tree())
        if isinstance(node, ast.FunctionDef) and node.name == "_on_stop_state"
    )
    # ast.unparse parenthesises tuple targets on some versions and not others;
    # normalise so the pin is about the wiring, not the Python minor version.
    statements = [
        ast.unparse(stmt).replace("(stop_requested, valid_until)", "stop_requested, valid_until")
        for stmt in callback.body
    ]
    assert "now = time.monotonic()" in statements
    assert (
        "stop_requested, valid_until = decode_stop_state(msg.data, now, "
        "self._stop_state_max_validity_s)" in statements
    )
    assert "self._core.on_stop_state(stop_requested, valid_until, now)" in statements


def test_node_declares_the_separate_window_parameter() -> None:
    declared = {
        ast.literal_eval(call.args[0])
        for call in _calls(_node_tree(), "declare_parameter")
        if call.args and isinstance(call.args[0], ast.Constant)
    }
    assert SPEC_WINDOW_PARAM in declared
    assert SPEC_ENABLE_PARAM in declared
    assert "cmd_vel_timeout_s" in declared  # W-1's own knob stays distinct


def test_callback_stamps_the_message_with_the_monotonic_clock() -> None:
    tree = _node_tree()
    callback = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_on_stop_state"
    )
    body = ast.unparse(callback)
    assert "time.monotonic()" in body
    assert "time.time()" not in body
    assert "get_clock" not in body  # ROS time would not match the core's clock
