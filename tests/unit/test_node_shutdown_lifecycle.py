"""Node process-lifecycle pins: every rclpy ``main()`` must stop cleanly on Humble.

Oracle (measured on the Jetson board, ROS 2 Humble / rclpy 3.3.21, 2026-09-10 with a
bare probe node — not derived from any implementation in this repo): rclpy's signal
handler shuts the context down BEFORE ``main()``'s ``finally`` runs, for SIGINT and
SIGTERM alike (``rclpy.ok()`` is already False there). Under that:

    main() pattern                                              SIGINT   SIGTERM
    suppress(KeyboardInterrupt) + bare rclpy.shutdown()         exit 1   exit 1
    except (KeyboardInterrupt, ExternalShutdownException)
      + rclpy.try_shutdown()                                    exit 0   exit 0
    except KeyboardInterrupt + own SIGTERM handler raising
      KeyboardInterrupt + `if rclpy.ok(): rclpy.shutdown()`     exit 0   exit 0 (*)

    (*) only because the node owns a timer: a Python-level SIGTERM handler cannot
        wake rcl_wait, so a timer-less node hangs until its next event.

Why a non-zero exit matters in prod: deploy/jetson/systemd/*.service run with
``Restart=on-failure``, so a routine ``systemctl stop`` is logged as a failure and a
real crash hides in the same traceback noise (docs/setup/jetson-deploy.md, systemd
unit table). Safety never depends on the shutdown-path message: m1_driver's W-1
brakes 0.5 s after the last command (docs/mode-m1/02-m1-driver-and-watchdog.md:62).

Three layers of pins, all AST (rclpy is not importable on the host):

A. ``warehouse_teleop.node_runtime`` — the helper that encodes the safe pattern.
B. the two teleop entry points delegate to it and keep their own guarantees (the
   final zero is best-effort; the keyboard node ALWAYS restores the terminal).
C. a repo-wide RATCHET over every top-level ``main()`` that calls ``rclpy.init``:
   the set of mains still using an unsafe pattern must equal the recorded baseline
   exactly. Fixing one means deleting it from the baseline; a new unsafe node fails
   CI. (Same "replace grep-and-hope" idea as tests/unit/test_py310_compat.py.)
   The classifier is self-checked against the three measured rows above, plus
   synthesised counter-examples for the three shapes a naive AST reading waves
   through:

   (i)  a ``try`` / ``contextlib.suppress`` around ``rclpy.shutdown()`` only counts
        as a guard when it actually catches the error that call raises on a dead
        context (``RCLError``, a private subclass of ``RuntimeError``). ``except
        ValueError`` is a guard in shape only and still exits 1.
   (ii) the "own SIGTERM handler" route to exit 0 additionally requires the module
        to own a timer: row 3's ``(*)`` is a PRECONDITION, not a remark — a
        Python-level handler cannot wake ``rcl_wait``, so a timer-less node hangs
        instead of stopping.
   (iii) the ratchet walks ``main()`` functions, but what actually ships is each
        setup.py ``console_scripts`` target. A wrapper entry point (``cli:main``
        that delegates to another module's ``main``) is pinned through its
        delegate; a wrapper that owned a context itself would be invisible.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.safety]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WS_SRC = _REPO_ROOT / "ws" / "src"
_TELEOP = _WS_SRC / "warehouse_teleop" / "warehouse_teleop"
_RUNTIME = _TELEOP / "node_runtime.py"
_JOY = _TELEOP / "teleop_joy.py"
_KEYBOARD = _TELEOP / "teleop_keyboard.py"

# Directories under ws/src that are never part of the shipped node code.
_SKIP_DIR_NAMES = {"test", "tests", "build", "install", "log", "__pycache__"}

# ── C. ratchet baseline ──────────────────────────────────────────────────────
# ws/src-relative paths whose main() still uses a pattern that exits non-zero on a
# normal Humble stop (see the oracle table). DELETE an entry when you fix that node.
# NEVER add an entry for a new node — follow warehouse_teleop.node_runtime's three
# rules instead (catch both normal-stop exceptions, best-effort context use after
# the spin, rclpy.try_shutdown()).
KNOWN_UNSAFE_STOP_ON_HUMBLE: frozenset[str] = frozenset(
    {
        "warehouse_llm_bridge/warehouse_llm_bridge/character_node.py",
        "warehouse_llm_bridge/warehouse_llm_bridge/llm_bridge.py",
        "warehouse_llm_bridge/warehouse_llm_bridge/operator_feedback/notice_node.py",
        "warehouse_llm_bridge/warehouse_llm_bridge/x_er_bridge.py",
        "warehouse_nav2_bridge/warehouse_nav2_bridge/nav2_bridge.py",
        "warehouse_orchestrator/warehouse_orchestrator/kpi_collector.py",
        "warehouse_perception/warehouse_perception/speed_band_node.py",
        "warehouse_sim/warehouse_sim/battery_publisher.py",
        "warehouse_traffic/warehouse_traffic/traffic_manager.py",
        "warehouse_traffic/warehouse_traffic/virtual_scan.py",
        "warehouse_web_bridge/warehouse_web_bridge/web_bridge_node.py",
    }
)


# ── AST helpers ──────────────────────────────────────────────────────────────
def _module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _top_level_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _method(tree: ast.Module, cls: str, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return item
    raise AssertionError(f"{cls}.{name} not found")


def _pre_order(node: ast.AST):  # noqa: ANN202 - generator of ast nodes
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _pre_order(child)


def _call_names(node: ast.AST) -> list[str]:
    """Unparsed callee of every Call under ``node``, in SOURCE order (pre-order DFS —
    ``ast.walk`` is breadth-first and would reorder calls nested at different depths)."""
    return [ast.unparse(n.func) for n in _pre_order(node) if isinstance(n, ast.Call)]


def _call(node: ast.AST, callee: str) -> ast.Call:
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and ast.unparse(n.func) == callee:
            return n
    raise AssertionError(f"no call to {callee}")


def _keyword(call: ast.Call, name: str) -> str:
    for kw in call.keywords:
        if kw.arg == name:
            return ast.unparse(kw.value)
    raise AssertionError(f"call has no keyword {name!r}: {ast.unparse(call)}")


def _caught_exception_names(fn: ast.AST) -> set[str]:
    """Exception names caught by ``except`` handlers or ``contextlib.suppress(...)``."""
    caught: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.ExceptHandler) and n.type is not None:
            t = n.type
            elts = t.elts if isinstance(t, ast.Tuple) else [t]
            caught.update(ast.unparse(e) for e in elts)
        elif isinstance(n, ast.With):
            for item in n.items:
                call = item.context_expr
                if isinstance(call, ast.Call) and ast.unparse(call.func).endswith("suppress"):
                    caught.update(ast.unparse(a) for a in call.args)
    return caught


_NOT_A_HANDLER = frozenset({"signal.SIG_IGN", "signal.SIG_DFL"})


def _installs_sigterm_handler(tree: ast.Module) -> bool:
    """A Python-level SIGTERM handler that will actually unwind the spin.

    ``signal.signal(SIGTERM, SIG_IGN)`` (never stops) and ``SIG_DFL`` (dies with 143)
    install nothing that reaches ``finally``, so they do not count.
    """
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Call)
            and ast.unparse(n.func) == "signal.signal"
            and len(n.args) >= 2
            and ast.unparse(n.args[0]) == "signal.SIGTERM"
            and ast.unparse(n.args[1]) not in _NOT_A_HANDLER
        ):
            return True
    return False


def _owns_a_timer(tree: ast.Module) -> bool:
    """The module creates an rclpy timer, so ``rcl_wait`` returns on its own.

    Precondition of the oracle table's row-3 ``(*)``: a Python-level SIGTERM handler
    only runs between wait iterations, so without a periodic wake-up the node hangs
    on SIGTERM instead of exiting 0.
    """
    # Only the node object's own timer counts (``self.create_timer`` inside the node class,
    # or ``node.create_timer`` on the main()-local node) -- a scheduler's ``create_timer`` or
    # a helper class that is never spun does not wake rcl_wait.
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "create_timer"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id in {"self", "node"}
        for n in ast.walk(tree)
    )


# ``rclpy.shutdown()`` on an already-dead context raises ``RCLError`` -- a private
# subclass of ``RuntimeError``. A try/suppress around that call is only a guard when
# it catches THAT; anything narrower (``except ValueError``) still exits 1.
_SHUTDOWN_ERROR_GUARDS = frozenset(
    {
        "BaseException",
        "Exception",
        "RuntimeError",
        "RCLError",
        "exceptions.RCLError",
        "rclpy.exceptions.RCLError",
    }
)


def _covers_shutdown_error(exprs: list[ast.expr]) -> bool:
    """True when these ``except`` / ``suppress`` types catch a failing shutdown."""
    flat: list[ast.expr] = []
    for e in exprs:
        flat.extend(e.elts if isinstance(e, ast.Tuple) else [e])
    return any(ast.unparse(e) in _SHUTDOWN_ERROR_GUARDS for e in flat)


def _suppresses_shutdown_error(item: ast.withitem) -> bool:
    call = item.context_expr
    if not (isinstance(call, ast.Call) and ast.unparse(call.func).endswith("suppress")):
        return False
    return _covers_shutdown_error(list(call.args))


def _reraises(handler: ast.ExceptHandler) -> bool:
    """``except X: ...; raise`` swallows nothing -- the traceback still ends the process."""
    return any(isinstance(stmt, ast.Raise) and stmt.exc is None for stmt in handler.body)


def _handlers_cover_shutdown_error(handlers: list[ast.ExceptHandler]) -> bool:
    live = [h for h in handlers if not _reraises(h)]
    # A bare ``except:`` catches everything (ruff E722 has its own opinion on that).
    if any(h.type is None for h in live):
        return True
    return _covers_shutdown_error([h.type for h in live if h.type is not None])


def _shutdown_calls_are_guarded(fn: ast.AST) -> bool:
    """Every ``rclpy.shutdown()`` sits under ``if rclpy.ok()`` or inside a try/suppress
    that actually catches the resulting ``RCLError`` (see ``_SHUTDOWN_ERROR_GUARDS``)."""

    def visit(node: ast.AST, guarded: bool) -> bool:
        if (
            isinstance(node, ast.Call)
            and ast.unparse(node.func) == "rclpy.shutdown"
            and not guarded
        ):
            return False
        if isinstance(node, ast.If) and "rclpy.ok()" in ast.unparse(node.test):
            inner = all(visit(c, True) for c in node.body)
            rest = all(visit(c, guarded) for c in node.orelse)
            return inner and rest
        if isinstance(node, ast.With) and any(_suppresses_shutdown_error(i) for i in node.items):
            return all(visit(c, True) for c in node.body)
        if isinstance(node, ast.Try) and _handlers_cover_shutdown_error(node.handlers):
            body_ok = all(visit(c, True) for c in node.body)
            others = [*node.handlers, *node.orelse, *node.finalbody]
            return body_ok and all(visit(c, guarded) for c in others)
        return all(visit(c, guarded) for c in ast.iter_child_nodes(node))

    return visit(fn, False)


def _stop_pattern_is_safe(tree: ast.Module, main: ast.FunctionDef) -> bool:
    """The measured-safe rows of the oracle table, as a static predicate over main()."""
    calls = _call_names(main)
    if any(c.endswith("run_node") for c in calls):
        return True  # delegates to the pinned helper (layer A)
    caught = _caught_exception_names(main)
    sigint_ok = "KeyboardInterrupt" in caught or "BaseException" in caught
    # Row 3 of the table only reaches exit 0 because the node owns a timer (*).
    sigterm_ok = "ExternalShutdownException" in caught or (
        _installs_sigterm_handler(tree) and _owns_a_timer(tree)
    )
    shutdown_ok = "rclpy.try_shutdown" in calls or _shutdown_calls_are_guarded(main)
    return sigint_ok and sigterm_ok and shutdown_ok


def _rclpy_mains() -> dict[str, tuple[ast.Module, ast.FunctionDef]]:
    """Every top-level ``main()`` under ws/src that owns a context.

    Either it calls ``rclpy.init`` itself or it delegates the whole lifecycle to a
    ``run_node`` helper (layer A) — both shapes are in scope of the ratchet.
    """
    found: dict[str, tuple[ast.Module, ast.FunctionDef]] = {}
    for path in sorted(_WS_SRC.rglob("*.py")):
        rel = path.relative_to(_WS_SRC)
        if _SKIP_DIR_NAMES & set(rel.parts[:-1]):
            continue
        tree = _module(path)
        main = _top_level_function(tree, "main")
        if main is None:
            continue
        calls = _call_names(main)
        if "rclpy.init" not in calls and not any(c.endswith("run_node") for c in calls):
            continue
        found[rel.as_posix()] = (tree, main)
    return found


# ── A. node_runtime encodes the safe pattern ─────────────────────────────────
def test_run_node_treats_sigterm_like_ctrl_c() -> None:
    tree = _module(_RUNTIME)
    tuple_node = next(
        n
        for n in tree.body
        if isinstance(n, (ast.Assign, ast.AnnAssign))
        and "NORMAL_STOP_EXCEPTIONS"
        in ast.unparse(n.targets[0] if isinstance(n, ast.Assign) else n.target)
    )
    value = tuple_node.value
    assert isinstance(value, ast.Tuple)
    names = {ast.unparse(e) for e in value.elts}
    assert names == {"KeyboardInterrupt", "ExternalShutdownException"}, names
    run_node = _top_level_function(tree, "run_node")
    assert run_node is not None
    assert "NORMAL_STOP_EXCEPTIONS" in _caught_exception_names(run_node)


def test_run_node_never_calls_bare_shutdown() -> None:
    tree = _module(_RUNTIME)
    assert "rclpy.shutdown" not in _call_names(tree), "use rclpy.try_shutdown (idempotent)"
    run_node = _top_level_function(tree, "run_node")
    assert run_node is not None
    outer = next(n for n in ast.walk(run_node) if isinstance(n, ast.Try) and n.finalbody)
    assert "rclpy.try_shutdown" in [c for s in outer.finalbody for c in _call_names(s)]


def test_best_effort_checks_the_context_before_acting_then_swallows_runtime_error() -> None:
    fn = _top_level_function(_module(_RUNTIME), "best_effort")
    assert fn is not None
    guard_idx = action_idx = None
    for i, stmt in enumerate(fn.body):
        src = ast.unparse(stmt)
        if guard_idx is None and src.startswith("if not rclpy.ok():"):
            assert any(isinstance(b, ast.Return) for b in stmt.body), "guard must return early"
            guard_idx = i
        if action_idx is None and "action()" in src:
            action_idx = i
    assert guard_idx is not None, "best_effort lacks the `if not rclpy.ok(): return` guard"
    assert action_idx is not None and guard_idx < action_idx, "guard must precede the action"
    # RCLError / InvalidHandle are private subclasses of RuntimeError: that public
    # base is what the helper may swallow — nothing broader (a bug must still surface).
    assert _caught_exception_names(fn) == {"RuntimeError"}


def test_run_node_orders_exit_hook_then_destroy_then_shutdown() -> None:
    fn = _top_level_function(_module(_RUNTIME), "run_node")
    assert fn is not None
    outer = next(n for n in ast.walk(fn) if isinstance(n, ast.Try) and n.finalbody)
    calls = [c for s in outer.finalbody for c in _call_names(s)]
    order = [c for c in calls if c in {"on_exit", "node.destroy_node", "rclpy.try_shutdown"}]
    assert order == ["on_exit", "node.destroy_node", "rclpy.try_shutdown"], calls
    # The exit hook is wrapped so that destroy/try_shutdown still run if it raises.
    inner = [n for s in outer.finalbody for n in ast.walk(s) if isinstance(n, ast.Try)]
    assert inner and inner[0].finalbody, "on_exit must be inside its own try/finally"


# ── B. the teleop entry points delegate and keep their own guarantees ────────
def test_teleop_joy_main_delegates_with_its_best_effort_final_zero() -> None:
    tree = _module(_JOY)
    main = _top_level_function(tree, "main")
    assert main is not None
    call = _call(main, "run_node")
    assert ast.unparse(call.args[0]) == "TeleopJoy"
    assert _keyword(call, "on_exit") == "TeleopJoy.publish_stop"
    stop = _method(tree, "TeleopJoy", "publish_stop")
    assert "best_effort" in _call_names(stop), "the final zero must be best-effort"
    assert ast.unparse(_call(stop, "best_effort").args[0]) == "self._on_timer"
    # No direct context handling left in the node file: the idiom lives in node_runtime.
    assert not any(c.startswith("rclpy.") for c in _call_names(tree)), _call_names(tree)


def test_teleop_keyboard_main_delegates_and_always_restores_the_terminal() -> None:
    tree = _module(_KEYBOARD)
    main = _top_level_function(tree, "main")
    assert main is not None
    call = _call(main, "run_node")
    assert ast.unparse(call.args[0]) == "TeleopKeyboard"
    assert _keyword(call, "spin") == "_spin_until_quit"
    assert _keyword(call, "on_exit") == "_stop_and_restore"
    hook = _top_level_function(tree, "_stop_and_restore")
    assert hook is not None
    calls = _call_names(hook)
    assert calls.index("node.stop") < calls.index("node.restore_terminal"), calls
    stop = _method(tree, "TeleopKeyboard", "stop")
    assert "best_effort" in _call_names(stop), "the quit/exit zero must be best-effort"
    loop = _top_level_function(tree, "_spin_until_quit")
    assert loop is not None and "rclpy.spin_once" in _call_names(loop)
    assert "node.shutdown_requested" in ast.unparse(loop), "quit flag must break the loop"


# ── C. repo-wide ratchet ─────────────────────────────────────────────────────
_ORACLE_ROWS = {
    # S-5: ignoring SIGTERM is not handling it (the node never stops).
    "sigterm_ignored_never_stops": (
        "import signal\nimport rclpy\n"
        "def main():\n"
        "    rclpy.init()\n    node = object()\n"
        "    node.create_timer(0.1, lambda: None)\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "    try:\n        rclpy.spin(node)\n"
        "    except KeyboardInterrupt:\n        pass\n"
        "    finally:\n        if rclpy.ok():\n            rclpy.shutdown()\n",
        False,
    ),
    # S-6: a scheduler's create_timer is not an rclpy timer -> rcl_wait is never woken.
    "sigterm_override_with_foreign_timer_hangs": (
        "import signal\nimport rclpy\n"
        "def main():\n"
        "    rclpy.init()\n    node = object()\n    scheduler = object()\n"
        "    scheduler.create_timer(5)\n"
        "    def _h(signum, frame):\n        raise KeyboardInterrupt\n"
        "    signal.signal(signal.SIGTERM, _h)\n"
        "    try:\n        rclpy.spin(node)\n"
        "    except KeyboardInterrupt:\n        pass\n"
        "    finally:\n        if rclpy.ok():\n            rclpy.shutdown()\n",
        False,
    ),
    # S-7: catching RuntimeError only to re-raise it swallows nothing.
    "guard_that_reraises_is_no_guard": (
        "import rclpy\nfrom rclpy.executors import ExternalShutdownException\n"
        "def main():\n"
        "    rclpy.init()\n    node = object()\n"
        "    try:\n        rclpy.spin(node)\n"
        "    except (KeyboardInterrupt, ExternalShutdownException):\n        pass\n"
        "    finally:\n        try:\n            rclpy.shutdown()\n"
        "        except RuntimeError:\n            raise\n",
        False,
    ),
    "canonical_exit_1": (
        "import contextlib\nimport rclpy\n"
        "def main():\n"
        "    rclpy.init()\n    node = object()\n"
        "    try:\n        with contextlib.suppress(KeyboardInterrupt):\n"
        "            rclpy.spin(node)\n"
        "    finally:\n        rclpy.shutdown()\n",
        False,
    ),
    "both_exceptions_try_shutdown_exit_0": (
        "import rclpy\nfrom rclpy.executors import ExternalShutdownException\n"
        "def main():\n"
        "    rclpy.init()\n    node = object()\n"
        "    try:\n        rclpy.spin(node)\n"
        "    except (KeyboardInterrupt, ExternalShutdownException):\n        pass\n"
        "    finally:\n        rclpy.try_shutdown()\n",
        True,
    ),
    "sigterm_override_guarded_exit_0": (
        # The create_timer call is what makes this row's (*) hold: it is the periodic
        # rcl_wait wake-up that lets the Python-level handler run (cf. driver_node).
        "import signal\nimport rclpy\n"
        "def main():\n"
        "    rclpy.init()\n    node = object()\n"
        "    node.create_timer(0.1, lambda: None)\n"
        "    def _h(signum, frame):\n        raise KeyboardInterrupt\n"
        "    signal.signal(signal.SIGTERM, _h)\n"
        "    try:\n        rclpy.spin(node)\n"
        "    except KeyboardInterrupt:\n        pass\n"
        "    finally:\n        if rclpy.ok():\n            rclpy.shutdown()\n",
        True,
    ),
    # (ii) Same shape WITHOUT a timer: the handler cannot wake rcl_wait, so SIGTERM
    # hangs rather than exiting 0 — the row above minus its stated precondition.
    "sigterm_override_without_a_timer_hangs": (
        "import signal\nimport rclpy\n"
        "def main():\n"
        "    rclpy.init()\n    node = object()\n"
        "    def _h(signum, frame):\n        raise KeyboardInterrupt\n"
        "    signal.signal(signal.SIGTERM, _h)\n"
        "    try:\n        rclpy.spin(node)\n"
        "    except KeyboardInterrupt:\n        pass\n"
        "    finally:\n        if rclpy.ok():\n            rclpy.shutdown()\n",
        False,
    ),
    # Guarded shutdown but SIGTERM unhandled: ExternalShutdownException escapes.
    "guarded_shutdown_but_sigterm_unhandled": (
        "import contextlib\nimport rclpy\n"
        "def main():\n"
        "    rclpy.init()\n    node = object()\n"
        "    try:\n        rclpy.spin(node)\n"
        "    except KeyboardInterrupt:\n        pass\n"
        "    finally:\n        with contextlib.suppress(Exception):\n"
        "            rclpy.shutdown()\n",
        False,
    ),
    # Both exceptions handled but a bare shutdown after the context died.
    "both_exceptions_bare_shutdown": (
        "import rclpy\nfrom rclpy.executors import ExternalShutdownException\n"
        "def main():\n"
        "    rclpy.init()\n    node = object()\n"
        "    try:\n        rclpy.spin(node)\n"
        "    except (KeyboardInterrupt, ExternalShutdownException):\n        pass\n"
        "    finally:\n        rclpy.shutdown()\n",
        False,
    ),
    # (i) A try/suppress whose type does NOT cover the RCLError that a dead-context
    # shutdown raises: the traceback still escapes, so this is row 1 in disguise.
    "both_exceptions_shutdown_in_unrelated_try": (
        "import rclpy\nfrom rclpy.executors import ExternalShutdownException\n"
        "def main():\n"
        "    rclpy.init()\n    node = object()\n"
        "    try:\n        rclpy.spin(node)\n"
        "    except (KeyboardInterrupt, ExternalShutdownException):\n        pass\n"
        "    finally:\n        try:\n            rclpy.shutdown()\n"
        "        except ValueError:\n            pass\n",
        False,
    ),
    "both_exceptions_shutdown_in_unrelated_suppress": (
        "import contextlib\nimport rclpy\n"
        "from rclpy.executors import ExternalShutdownException\n"
        "def main():\n"
        "    rclpy.init()\n    node = object()\n"
        "    try:\n        rclpy.spin(node)\n"
        "    except (KeyboardInterrupt, ExternalShutdownException):\n        pass\n"
        "    finally:\n        with contextlib.suppress(ValueError):\n"
        "            rclpy.shutdown()\n",
        False,
    ),
    # ...and the positive control: suppressing the RuntimeError family DOES make the
    # bare shutdown survivable (row 2's outcome by a different, still-valid route).
    "both_exceptions_shutdown_suppressing_runtime_error_exit_0": (
        "import contextlib\nimport rclpy\n"
        "from rclpy.executors import ExternalShutdownException\n"
        "def main():\n"
        "    rclpy.init()\n    node = object()\n"
        "    try:\n        rclpy.spin(node)\n"
        "    except (KeyboardInterrupt, ExternalShutdownException):\n        pass\n"
        "    finally:\n        with contextlib.suppress(RuntimeError):\n"
        "            rclpy.shutdown()\n",
        True,
    ),
}


@pytest.mark.parametrize("row", sorted(_ORACLE_ROWS))
def test_classifier_reproduces_the_measured_oracle_rows(row: str) -> None:
    source, expected_safe = _ORACLE_ROWS[row]
    tree = ast.parse(source)
    main = _top_level_function(tree, "main")
    assert main is not None
    assert _stop_pattern_is_safe(tree, main) is expected_safe


def test_every_rclpy_main_is_discovered() -> None:
    mains = _rclpy_mains()
    # The three nodes this slice guarantees must be in scope of the ratchet at all.
    for rel in (
        "warehouse_teleop/warehouse_teleop/teleop_joy.py",
        "warehouse_teleop/warehouse_teleop/teleop_keyboard.py",
        "warehouse_m1_driver/warehouse_m1_driver/driver_node.py",
    ):
        assert rel in mains, sorted(mains)
    missing = sorted(KNOWN_UNSAFE_STOP_ON_HUMBLE - set(mains))
    assert not missing, (
        f"baseline entries that no longer exist / no longer own a context: {missing}"
    )


def _console_script_strings(tree: ast.Module) -> list[str]:
    """The ``exe = module:function`` strings of ``setup(entry_points=...)``."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and ast.unparse(node.func).endswith("setup")):
            continue
        for kw in node.keywords:
            if kw.arg != "entry_points":
                continue

            try:
                entry_points = ast.literal_eval(kw.value)
            except (ValueError, TypeError, SyntaxError):
                # Not a plain literal (computed dict): fall back to string scraping.
                return [
                    n.value
                    for n in ast.walk(kw.value)
                    if isinstance(n, ast.Constant)
                    and isinstance(n.value, str)
                    and "=" in n.value
                    and ":" in n.value
                ]
            return list(entry_points.get("console_scripts", []))
    return []


def _entry_point_specs() -> dict[str, tuple[str, str, str]]:
    """``<pkg dir>:<exe>`` -> (package dir, dotted module, function) for all of ws/src."""
    specs: dict[str, tuple[str, str, str]] = {}
    for setup_py in sorted(_WS_SRC.glob("*/setup.py")):
        pkg_dir = setup_py.parent.name
        for raw in _console_script_strings(_module(setup_py)):
            exe, _, target = raw.partition("=")
            module, _, func = target.strip().partition(":")
            specs[f"{pkg_dir}:{exe.strip()}"] = (pkg_dir, module, func or "main")
    return specs


def _resolve_module(module: str, pkg_dir: str) -> Path | None:
    """File backing a dotted module, if it lives under ws/src (own package first)."""
    tail = Path(*module.split(".")).with_suffix(".py")
    own = _WS_SRC / pkg_dir / tail
    if own.is_file():
        return own
    for base in sorted(p for p in _WS_SRC.iterdir() if p.is_dir()):
        if (base / tail).is_file():
            return base / tail
    return None


def _imports_rclpy(tree: ast.Module) -> bool:
    for n in ast.walk(tree):
        if isinstance(n, ast.Import) and any(a.name.split(".")[0] == "rclpy" for a in n.names):
            return True
        if isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "rclpy":
            return True
    return any(c.endswith("run_node") for c in _call_names(tree))


def _imports_of_ratcheted_modules(
    tree: ast.Module, pkg_dir: str, mains: dict[str, tuple[ast.Module, ast.FunctionDef]]
) -> list[tuple[str, str]]:
    """(callable local name, ws/src-relative target) for imports of a ratcheted module.

    Both wrapper shapes: ``from <mod> import main as run`` -> ``("run", rel)`` and
    ``import <mod>`` -> ``("<mod>.main", rel)``. Imports nested inside a function count
    (warehouse_web_bridge.cli defers its import on purpose, #283).
    """
    found: list[tuple[str, str]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            target = _resolve_module(n.module, pkg_dir)
            if target is None or target.relative_to(_WS_SRC).as_posix() not in mains:
                continue
            rel = target.relative_to(_WS_SRC).as_posix()
            found.extend(
                (alias.asname or alias.name, rel) for alias in n.names if alias.name == "main"
            )
        elif isinstance(n, ast.Import):
            for alias in n.names:
                target = _resolve_module(alias.name, pkg_dir)
                if target is None or target.relative_to(_WS_SRC).as_posix() not in mains:
                    continue
                local = alias.asname or alias.name
                found.append((f"{local}.main", target.relative_to(_WS_SRC).as_posix()))
    return found


def _classify_entry_point(
    pkg_dir: str, module: str, func: str, mains: dict[str, tuple[ast.Module, ast.FunctionDef]]
) -> tuple[str, str]:
    """(verdict, target) for one console_scripts target. Only 3 verdicts are allowed."""
    path = _resolve_module(module, pkg_dir)
    if path is None:
        return "unresolved-module", module
    rel = path.relative_to(_WS_SRC).as_posix()
    if func == "main" and rel in mains:
        return "pinned", rel  # the ratchet already classifies this main()
    tree = _module(path)
    fn = _top_level_function(tree, func)
    if fn is None:
        return "no-such-function", f"{rel}:{func}"
    # A wrapper (warehouse_web_bridge.cli:main, #283) owns no context of its own: it
    # imports another module's main() and calls it. Pinned through that delegate.
    called = set(_call_names(fn))
    reachable = _imports_of_ratcheted_modules(tree, pkg_dir, mains)
    for local, target_rel in reachable:
        if local in called:
            return "delegates", target_rel
    if _imports_rclpy(tree):
        return "owns-a-context-but-unpinned", rel
    if reachable:
        # It pulls in a node module but we cannot see it call that module's main():
        # never fall through to "pure CLI" here, or a wrapper hides the context again.
        return "reaches-a-node-module-without-delegating", rel
    return "pure-cli", rel  # e.g. m1_probe, kpi_report: no ROS context at all


def test_console_script_entry_points_reach_a_pinned_lifecycle() -> None:
    """What ships is setup.py's console_scripts, not the set of ``main()`` functions.

    Every executable must therefore be (a) a main() the ratchet classifies, (b) a
    wrapper delegating to one, or (c) a pure CLI with no ROS context. A fourth case —
    an entry point that spins a node the ratchet never sees — is the hole this closes.
    """
    mains = _rclpy_mains()
    specs = _entry_point_specs()
    # Independent oracle for the AST scan itself: a plain regex over the setup.py text.
    # Without this, a setup.py the scan cannot parse makes the whole test vacuously green.
    declared = {
        f"{p.parent.name}:{exe}"
        for p in sorted(_WS_SRC.glob("*/setup.py"))
        for exe in re.findall(
            r"""['"]\s*([\w.-]+)\s*=\s*[\w.]+:\w+\s*['"]""", p.read_text(encoding="utf-8")
        )
    }
    # Key-set equality (not a count): a scan that drops one entry and invents another
    # (an f-string fragment with an empty exe name) must not cancel out to green.
    assert set(specs) == declared, (
        f"setup.py scan vs regex disagree: missing={sorted(declared - set(specs))} "
        f"extra={sorted(set(specs) - declared)}"
    )
    verdicts = {exe: _classify_entry_point(*spec, mains) for exe, spec in specs.items()}
    stray = {
        exe: v for exe, v in verdicts.items() if v[0] not in {"pinned", "delegates", "pure-cli"}
    }
    assert not stray, (
        "console_scripts entry points that no shutdown pin reaches: "
        f"{stray}. Point the executable at a ratcheted main(), or delegate to one."
    )


def test_the_m1_path_nodes_stop_cleanly() -> None:
    mains = _rclpy_mains()
    for rel in (
        "warehouse_teleop/warehouse_teleop/teleop_joy.py",
        "warehouse_teleop/warehouse_teleop/teleop_keyboard.py",
        "warehouse_m1_driver/warehouse_m1_driver/driver_node.py",
    ):
        tree, main = mains[rel]
        assert _stop_pattern_is_safe(tree, main), rel


def test_unsafe_stop_patterns_match_the_recorded_baseline_exactly() -> None:
    mains = _rclpy_mains()
    unsafe = {rel for rel, (tree, main) in mains.items() if not _stop_pattern_is_safe(tree, main)}
    regressed = sorted(unsafe - KNOWN_UNSAFE_STOP_ON_HUMBLE)
    fixed = sorted(KNOWN_UNSAFE_STOP_ON_HUMBLE - unsafe)
    assert not regressed, (
        "new main() with an unsafe Humble stop pattern (exit 1 on Ctrl-C / SIGTERM): "
        f"{regressed}. Use warehouse_teleop.node_runtime's three rules."
    )
    assert not fixed, (
        f"these nodes now stop cleanly — delete them from KNOWN_UNSAFE_STOP_ON_HUMBLE: {fixed}"
    )
