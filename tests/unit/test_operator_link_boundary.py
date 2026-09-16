"""AST pin: the operator-link logic stays PURE and its message set stays CLOSED.

``docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md:42`` demands that the
remote-operator face enumerate "運ぶ意味を閉集合で列挙" and carry an R-26-grade AST
pin of the same kind ``web_bridge`` carries (``OQ-OD24``); ``:47`` explains why:
the observe-only gateway and the control face degrade in OPPOSITE directions, so
neither may quietly grow the other's powers.

This file is the mirror image of ``tests/unit/test_web_bridge_noactuation.py``.
Where that one proves the observe-only gateway creates no actuation path, this
one proves the control-side LOGIC is a library and nothing else:

  * it imports no transport and no ROS (``rclpy`` / ``websockets`` / ``fastapi`` /
    ``uvicorn`` / ``asyncio``), so it can hold no socket, no event loop, no
    publisher — the node, the port and the connection split are a later slice
    (``OQ-OD23`` / ``OQ-OD90``) and must not sneak in through this one;
  * it names no actuation topic: the ONLY topic it knows is the frozen
    ``/operator/stop_request`` constant, IMPORTED from :mod:`warehouse_teleop.joymap`
    rather than spelled again (doc03:112 is the single source);
  * :class:`~warehouse_teleop.operator_link_logic.ControlKind` equals the
    documented closed set exactly, so widening it is a deliberate contract change
    rather than a one-line edit.

Written as a source scan so it runs on a host with no ROS 2 and no web stack,
and so a regression is caught at the source, not at runtime.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from warehouse_teleop.joymap import DEFAULT_OPERATOR_STOP_TOPIC
from warehouse_teleop.operator_link_logic import ControlKind

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "ws/src/warehouse_teleop/warehouse_teleop/operator_link_logic.py"

# Transport / runtime packages the pure logic must never reach for. ``asyncio``
# is in the list because an event loop here would mean this module owns the
# connection, which is exactly the boundary 02:42 draws.
_FORBIDDEN_IMPORTS = {"rclpy", "websockets", "fastapi", "uvicorn", "asyncio"}

# rclpy actuation seams (same set as the web_bridge pin).
_FORBIDDEN_CALLS = {"create_publisher", "create_client", "create_subscription", "ActionClient"}

# Actuation sinks that must not appear as text anywhere, docstrings included:
# naming one would mean this module had started to think about driving.
_FORBIDDEN_SUBSTRINGS = ("cmd_vel", "goal_pose", "navigate_to_pose", "/api/v1/")


def _source() -> str:
    assert _MODULE.is_file(), f"module not found: {_MODULE}"
    return _MODULE.read_text(encoding="utf-8")


def _tree() -> ast.Module:
    return ast.parse(_source())


def _docstring_ids(tree: ast.Module) -> set[int]:
    """Identity of every Constant node that is a docstring (prose, not wire).

    Docstrings are excluded from the topic-literal scan on purpose: this file
    WANTS the design docs and the frozen topic explained in prose. What it
    forbids is a topic string that could be handed to a publisher.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


@pytest.mark.safety
@pytest.mark.unit
def test_imports_no_transport_or_ros_runtime():
    imported: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    leaked = imported & _FORBIDDEN_IMPORTS
    assert not leaked, (
        "operator_link_logic is pure logic (02:42 — the node / port / connection "
        f"split is a separate slice, OQ-OD23 / OQ-OD90): {sorted(leaked)}"
    )


@pytest.mark.safety
@pytest.mark.unit
def test_creates_no_publisher_subscription_service_or_action_client():
    offending = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in _FORBIDDEN_CALLS:
                offending.append(f"{name}(...)")
    assert not offending, (
        f"operator_link_logic must touch no ROS graph seam (05:95 R-26 row): {offending}"
    )


@pytest.mark.safety
@pytest.mark.unit
def test_names_no_topic_outside_the_imported_operator_stop_constant():
    tree = _tree()
    docstrings = _docstring_ids(tree)
    offending = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and node.value.startswith("/")
    ]
    assert not offending, (
        "the only topic this module may name is the frozen /operator/stop_request "
        f"constant imported from joymap (doc03:112 single source): {offending}"
    )


@pytest.mark.safety
@pytest.mark.unit
def test_the_operator_stop_topic_is_imported_not_respelled():
    tree = _tree()
    imported_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "warehouse_teleop.joymap"
        for alias in node.names
    }
    assert "DEFAULT_OPERATOR_STOP_TOPIC" in imported_names, (
        "the topic name must come from joymap, not from a second literal (doc03:112)"
    )
    # and the re-export really is that constant, not a look-alike
    from warehouse_teleop.operator_link_logic import OPERATOR_STOP_TOPIC

    assert OPERATOR_STOP_TOPIC == DEFAULT_OPERATOR_STOP_TOPIC


@pytest.mark.safety
@pytest.mark.unit
def test_mentions_no_actuation_sink_anywhere_in_the_source():
    source = _source()
    offending = [needle for needle in _FORBIDDEN_SUBSTRINGS if needle in source]
    assert not offending, (
        "operator_link_logic returns a Joy-equivalent sample and hands it to the "
        f"existing teleop path; it must not name the drive sinks itself: {offending}"
    )


@pytest.mark.safety
@pytest.mark.unit
def test_reuses_joymap_freshness_instead_of_re_implementing_it():
    # 02:53 — the remote path reuses the local joystick's pure logic (OQ-OD27).
    imported_names = {
        alias.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.ImportFrom) and node.module == "warehouse_teleop.joymap"
        for alias in node.names
    }
    assert {"joy_is_stale", "OPERATOR_STOP_PAYLOAD", "ESTOP_ACTION_ENGAGE"} <= imported_names


@pytest.mark.safety
@pytest.mark.unit
def test_control_kind_is_exactly_the_documented_closed_set():
    # 02:76 items (1)-(4) + operator authority (09:108). Item (5), important
    # state, is OUTBOUND telemetry and is deliberately not an inbound kind.
    assert {member.name for member in ControlKind} == {
        "HEARTBEAT",
        "TELEOP",
        "STOP_REQUEST",
        "CROSSING_APPROVAL",
        "AUTHORITY",
    }
    assert {member.value for member in ControlKind} == {
        "heartbeat",
        "teleop",
        "stop_request",
        "crossing_approval",
        "authority",
    }


@pytest.mark.safety
@pytest.mark.unit
def test_control_kind_membership_is_pinned_in_the_source_too():
    # A member added in source but shadowed at runtime (or vice versa) would
    # slip past the import-based check above.
    class_defs = [
        node
        for node in ast.walk(_tree())
        if isinstance(node, ast.ClassDef) and node.name == "ControlKind"
    ]
    assert len(class_defs) == 1
    members = {
        target.id
        for stmt in class_defs[0].body
        if isinstance(stmt, ast.Assign)
        for target in stmt.targets
        if isinstance(target, ast.Name)
    }
    assert members == {
        "HEARTBEAT",
        "TELEOP",
        "STOP_REQUEST",
        "CROSSING_APPROVAL",
        "AUTHORITY",
    }
