"""Uniform rclpy process lifecycle for warehouse nodes (Humble-safe stop).

Why this exists (measured on the Jetson, ROS 2 Humble, rclpy 3.3.21, 2026-09-10):
rclpy's signal handler shuts the context down *before* ``main()``'s ``finally``
runs, for SIGINT (Ctrl-C) and SIGTERM (``systemctl stop`` / ``timeout``) alike.
The textbook ``except KeyboardInterrupt: ... finally: rclpy.shutdown()`` then dies
with ``RCLError: failed to shutdown: rcl_shutdown already called`` (SIGINT) or with
an uncaught ``ExternalShutdownException`` (SIGTERM) -- exit 1 either way. That hides
real failures behind a routine traceback and makes every ``Restart=on-failure``
systemd unit (deploy/jetson/systemd/*.service) log a normal stop as a failure.

Three rules, pinned by tests/unit/test_node_shutdown_lifecycle.py:

1. A normal stop is ``KeyboardInterrupt`` OR ``ExternalShutdownException``; both
   make :func:`run_node` return normally (exit 0).
2. Anything that touches the ROS context after the spin (a courtesy zero twist, a
   final status message) goes through :func:`best_effort`: skipped when the context
   is already gone, and a ``RuntimeError`` raised mid-call (``RCLError`` and
   ``InvalidHandle`` are private subclasses of it) is swallowed. Safety must never
   depend on such a message -- for teleop the guarantee is m1_driver's W-1
   freshness brake (docs/mode-m1/02 §3), not the final zero.
3. ``rclpy.try_shutdown()`` instead of ``rclpy.shutdown()`` (idempotent).

Package-local on purpose: a package may depend only on ``warehouse_interfaces`` /
``warehouse_description`` (.claude/rules/parallel-workflow.md §2.1), and the
interfaces hub is a pure-Python contract package without rclpy (doc16 §4). Other
packages copy the three rules or adopt this module once a shared home is decided
(follow-up recorded in warehouse_teleop/CLAUDE.md).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node

NodeT = TypeVar("NodeT", bound=Node)

#: "The operator / supervisor asked us to stop" -- never "we failed".
NORMAL_STOP_EXCEPTIONS: tuple[type[BaseException], ...] = (
    KeyboardInterrupt,
    ExternalShutdownException,
)


def context_is_live() -> bool:
    """True while the default rclpy context can still publish / log."""
    return rclpy.ok()


def best_effort(action: Callable[[], None]) -> bool:
    """Run ``action`` only while the ROS context is alive; never raise because of it.

    Returns True when the action ran to completion, False when it was skipped
    (context already gone) or died on the context mid-call. Use it for courtesy
    messages on the shutdown path -- never for anything safety depends on.
    """
    if not rclpy.ok():
        return False
    try:
        action()
    except RuntimeError:
        # RCLError / InvalidHandle: the context died between the check and the call.
        return False
    return True


def run_node(
    node_factory: Callable[[], NodeT],
    *,
    args: list[str] | None = None,
    spin: Callable[[NodeT], None] = rclpy.spin,
    on_exit: Callable[[NodeT], None] | None = None,
) -> None:
    """``rclpy.init`` -> build -> spin -> clean stop, with Humble's signal semantics.

    ``spin`` defaults to ``rclpy.spin``; pass a custom loop (e.g. a ``spin_once``
    loop watching a quit flag). ``on_exit`` always runs once the node exists -- on a
    normal stop, on a crash, and after the context is gone -- so anything inside it
    that needs the context must go through :func:`best_effort`. A construction
    failure propagates: a node that cannot start is a failure (exit non-zero).
    """
    rclpy.init(args=args)
    node: NodeT | None = None
    try:
        node = node_factory()
        spin(node)
    except NORMAL_STOP_EXCEPTIONS:
        # SIGINT -> KeyboardInterrupt; SIGTERM -> ExternalShutdownException.
        pass
    finally:
        try:
            if node is not None and on_exit is not None:
                on_exit(node)
        finally:
            if node is not None:
                node.destroy_node()
            rclpy.try_shutdown()
