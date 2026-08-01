"""Startup dependency preflight for the Nav2 Bridge runtime (#283 DoD③).

The node needs two **pip** packages at runtime — ``fastapi`` and ``uvicorn`` — because the
REST surface is FastAPI served by uvicorn (doc mode-a/12a:202,221). They are declared in
``ws/src/warehouse_nav2_bridge/setup.py`` ``install_requires``, but ``colcon build`` /
``rosdep`` install **no** pip packages: an image whose pip block never provisioned them
(``deploy/dev/Dockerfile:41-48``) killed the node with a bare
``ModuleNotFoundError: No module named 'uvicorn'`` — the exact failure #283 records
("実際 nav2_bridge が uvicorn 不在で死亡"). This module turns that into one actionable line.

**Why a separate module.** ``nav2_bridge.py`` cannot be imported without ROS
(``Nav2BridgeNode`` subclasses ``rclpy.node.Node`` at class-definition time), so a hint
living there would be unreachable from the host unit tests. Nothing heavy is imported here
(no rclpy / uvicorn / fastapi), so ``tests/unit/test_nav2_bridge_preflight.py`` exercises the
real code path in a plain interpreter.

**Scope = pip deps only.** ROS deps (``rclpy``, ``nav2_simple_commander``) are ament/rosdep
provided and ``import rclpy`` runs at ``nav2_bridge.py`` module load — i.e. *before* ``main()``
— so they are out of this preflight's reach by construction (CLAUDE.md 前提・未確定).

**No secrets.** The message is a pure function of the missing module names; it never reads
the environment or config, so a startup log can never carry an ``.env`` value
(``.claude/rules/safety.md``).
"""

import importlib
from collections.abc import Callable, Sequence

# Runtime module → pip distribution to install. Version constraints deliberately live in
# ONE place (setup.py ``install_requires``); copying them here would drift silently, so the
# hint points at setup.py instead of repeating pins.
RUNTIME_PIP_MODULES: dict[str, str] = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
}


def probe_runtime_modules(
    importer: Callable[[str], object] | None = None,
) -> dict[str, ImportError]:
    """Import each declared runtime module; return ``{module: error}`` for the failures.

    ``importer`` is injectable (default :func:`importlib.import_module`) so the unit tests can
    simulate an unprovisioned image without uninstalling anything. Declaration order is
    preserved, and only ``ImportError`` (incl. ``ModuleNotFoundError``) is treated as "missing"
    — any other exception is a broken install and must keep its own traceback.
    """
    import_module = importer or importlib.import_module
    failures: dict[str, ImportError] = {}
    for module in RUNTIME_PIP_MODULES:
        try:
            import_module(module)
        except ImportError as exc:
            failures[module] = exc
    return failures


def missing_runtime_modules(importer: Callable[[str], object] | None = None) -> list[str]:
    """Names of the declared runtime modules that cannot be imported (declaration order)."""
    return list(probe_runtime_modules(importer))


def format_missing_hint(missing: Sequence[str]) -> str:
    """Render the actionable provisioning hint for ``missing`` module names.

    Pure: derived only from the given names (no env / config read → no secret can leak).
    """
    plural = "dependency" if len(missing) == 1 else "dependencies"
    names = ", ".join(missing)
    dists = " ".join(RUNTIME_PIP_MODULES.get(m, m) for m in missing)
    return (
        f"nav2_bridge preflight: missing pip runtime {plural}: {names}. "
        "Declared in ws/src/warehouse_nav2_bridge/setup.py (install_requires), but "
        "colcon build / rosdep install no pip packages. "
        "Fix: rebuild the dev/sim image whose pip block provisions them "
        "(deploy/dev/Dockerfile), or install into the running container: "
        f"pip3 install --break-system-packages {dists} "
        "(version pins stay in setup.py: pip3 install -e ws/src/warehouse_nav2_bridge)."
    )


def require_runtime_deps(importer: Callable[[str], object] | None = None) -> None:
    """Exit with the provisioning hint if any pip runtime dep is missing; else return.

    Raises ``SystemExit`` carrying the hint (same idiom as
    ``warehouse_mcp_server/server.py:136-139``): the operator gets one actionable line and a
    nonzero exit instead of a bare import traceback from deep inside startup.
    """
    failures = probe_runtime_modules(importer)
    if failures:
        raise SystemExit(format_missing_hint(list(failures))) from next(iter(failures.values()))
