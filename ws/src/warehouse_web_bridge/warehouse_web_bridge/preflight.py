"""Actionable hints for a missing heavy runtime dependency (#283, web_bridge half).

``colcon build`` does **not** pip-install a package's ``setup.py`` ``install_requires``, so a
dev/sim image that predates the gateway starts it and dies on a bare
``ModuleNotFoundError: No module named 'uvicorn'`` — exactly how ``nav2_bridge`` died in the
#283 report. The fix an operator needs (which pip line, in which provisioning file) is one
lookup away, so we print it instead of a raw traceback.

**Why this is its own module**: ``web_bridge_node`` imports ``rclpy``/``uvicorn`` at module
load (its docstring says so), so it cannot be imported at all in the very environment whose
failure we are explaining. This module is stdlib-only — importable, and unit-testable, with no
ROS and no pip extras.

**Deliberate duplication**: the ``nav2_bridge`` half of #283 is a separate lane and keeps its
own copy. Sharing a helper would mean one package importing another's internals, which
parallel-workflow §2.1 forbids (shared code belongs in ``warehouse_interfaces``, and a startup
hint is not a contract). Two ~40-line tables in two packages beats a cross-track import.

Every ``deploy/dev/Dockerfile`` line number below is pinned by
``tests/unit/test_web_preflight.py``, which re-reads the real Dockerfile and fails if a cited
line stops naming its package — so this table cannot drift silently (#165 line-drift class).
"""

from __future__ import annotations

from dataclasses import dataclass

#: The dev/sim image's pip block (``deploy/dev/Dockerfile``). ``ros2 run`` uses the system
#: Python, so these are the packages that must exist in the image, not in a venv.
DOCKERFILE = "deploy/dev/Dockerfile"
PIP_BLOCK_LINES = (41, 48)


@dataclass(frozen=True)
class DepHint:
    """How to provision one missing dependency: the fix command + where it is declared."""

    fix: str
    source: str


def _pip_hint(spec: str, dockerfile_line: int | None) -> DepHint:
    where = (
        f"{DOCKERFILE}:{dockerfile_line}"
        if dockerfile_line is not None
        else f"{DOCKERFILE}:{PIP_BLOCK_LINES[0]}-{PIP_BLOCK_LINES[1]} (NOT yet listed there)"
    )
    return DepHint(fix=f"pip3 install --break-system-packages {spec}   # PEP 668", source=where)


# pip-provisioned deps. The specs mirror this package's setup.py install_requires; the line
# numbers point at the dev image's pip block (see module docstring — pinned by unit).
_PIP_DEPS: dict[str, DepHint] = {
    "fastapi": _pip_hint('"fastapi>=0.110"', 44),
    "uvicorn": _pip_hint('"uvicorn>=0.27"', 45),
    # websockets backs the /ws fan-out and is in setup.py install_requires, but is NOT in the
    # dev image's pip block today (uvicorn's WS support needs it) — flagged, not invented.
    "websockets": _pip_hint('"websockets>=12,<14"', None),
    # langfuse is an OPTIONAL extra: without it trace_id degrades to null (fail-open, doc22:194).
    # It is never required to start, but naming it keeps the hint useful if it is imported.
    "langfuse": _pip_hint('"langfuse>=4.7,<5"', 47),
}

# ROS 2 runtime: not pip-installable — the environment must be sourced.
_ROS_HINT = DepHint(
    fix="source /opt/ros/jazzy/setup.bash   # ROS 2 Jazzy runtime (not a pip package)",
    source="deploy/dev (tiryoh/ros2-desktop-vnc:jazzy base image)",
)
_ROS_DEPS = frozenset({"rclpy", "std_msgs", "rosidl_runtime_py", "builtin_interfaces"})

# Workspace packages: built by colcon, then sourced from the overlay.
_WORKSPACE_HINT = DepHint(
    fix="colcon build && source ws/install/setup.bash   # workspace overlay not sourced",
    source="ws/src/<pkg> (ament_python)",
)
_WORKSPACE_DEPS = frozenset({"warehouse_interfaces", "warehouse_web_bridge", "eval_sdk"})

_UNKNOWN_HINT = DepHint(
    fix="check this package's setup.py install_requires / package.xml exec_depend",
    source=f"{DOCKERFILE}:{PIP_BLOCK_LINES[0]}-{PIP_BLOCK_LINES[1]} (dev/sim image pip block)",
)


def missing_module_name(exc: BaseException) -> str | None:
    """The **top-level** module name an ``ImportError`` is about (``rclpy.qos`` → ``rclpy``).

    Returns ``None`` when the exception carries no usable name (a bare ``ImportError("...")``),
    so the caller degrades to the generic hint rather than guessing. Never raises.
    """
    name = getattr(exc, "name", None)
    if not isinstance(name, str) or not name.strip():
        return None
    return name.strip().split(".", 1)[0]


def hint_for(module: str | None) -> DepHint:
    """The provisioning hint for a top-level module name (generic hint when unrecognized)."""
    if not module:
        return _UNKNOWN_HINT
    if module in _PIP_DEPS:
        return _PIP_DEPS[module]
    if module in _ROS_DEPS:
        return _ROS_HINT
    if module in _WORKSPACE_DEPS:
        return _WORKSPACE_HINT
    return _UNKNOWN_HINT


def missing_dependency_hint(exc: BaseException) -> str:
    """A multi-line, operator-actionable message for a failed heavy import (#283).

    Total by construction: any exception (even one with no ``name``) yields the generic hint,
    so the preflight path can never itself raise and mask the real failure.
    """
    module = missing_module_name(exc)
    hint = hint_for(module)
    subject = f"missing dependency '{module}'" if module else f"import failed ({exc})"
    return (
        f"web_bridge cannot start: {subject}.\n"
        f"  fix           : {hint.fix}\n"
        f"  provisioned by: {hint.source}\n"
        "  note          : colcon does NOT pip-install setup.py install_requires (#283); "
        "the dev/sim image provisions the gateway's heavy deps."
    )
