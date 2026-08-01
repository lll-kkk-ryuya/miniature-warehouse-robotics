"""nav2_bridge startup preflight for unprovisioned pip deps (#283 DoD③).

``fastapi`` / ``pydantic`` / ``uvicorn`` are pip deps that ``colcon build`` never installs
(``ws/src/warehouse_nav2_bridge/setup.py`` install_requires; provisioned for dev/sim in
``deploy/dev/Dockerfile:41-48``). Before this slice the node died on a bare
``ModuleNotFoundError`` — #283 records exactly that ("nav2_bridge が uvicorn 不在で死亡").

The oracles here are deliberately **outside** the implementation:

* the covered dep set is compared against ``setup.py``'s ``install_requires`` (a separate
  artifact — adding a pip dep there without extending the preflight turns this red),
* the hint's usefulness is judged by what an operator needs (the missing name + a runnable
  install command), not by echoing the implementation's format string,
* the *ordering* guarantee (hint wins over the import that used to explode) is read out of
  ``nav2_bridge.py``'s AST, which is un-importable here — it needs ROS.

No ROS, no fastapi/uvicorn: the fake importer simulates absence.
"""

import ast
import re
from pathlib import Path

import pytest
from warehouse_nav2_bridge import preflight

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PKG = _REPO_ROOT / "ws/src/warehouse_nav2_bridge"
_SETUP_PY = _PKG / "setup.py"
_NODE_MODULE = _PKG / "warehouse_nav2_bridge/nav2_bridge.py"

# Not a pip dependency of this package: setuptools is the build backend itself, always
# present wherever the package was installed from.
_BUILD_BACKEND = {"setuptools"}


def _fake_importer(absent):
    """Importer that raises ``ModuleNotFoundError`` for ``absent`` (an unprovisioned image)."""
    absent = set(absent)

    def _import(name: str) -> object:
        if name in absent:
            raise ModuleNotFoundError(f"No module named {name!r}")
        return object()

    return _import


def _declared_pip_distributions() -> set[str]:
    """Distribution names in setup.py ``install_requires`` (independent of preflight.py)."""
    tree = ast.parse(_SETUP_PY.read_text(encoding="utf-8"))
    requirements: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "install_requires":
            requirements = [ast.literal_eval(elt) for elt in node.value.elts]
    assert requirements, f"install_requires not found in {_SETUP_PY}"
    names = {re.match(r"[A-Za-z0-9_.\-]+", req).group(0) for req in requirements}
    return names - _BUILD_BACKEND


def _main_function() -> ast.FunctionDef:
    tree = ast.parse(_NODE_MODULE.read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    return main


def _module_level_imported_roots() -> set[str]:
    tree = ast.parse(_NODE_MODULE.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_every_declared_pip_dep_is_covered_by_the_preflight():
    """setup.py is the oracle: a new pip dep there must also gain a startup hint."""
    assert set(preflight.RUNTIME_PIP_MODULES.values()) == _declared_pip_distributions()


def test_fully_provisioned_environment_passes_silently():
    assert preflight.missing_runtime_modules(_fake_importer(absent=())) == []
    assert preflight.require_runtime_deps(_fake_importer(absent=())) is None


def test_missing_dep_is_named_and_the_fix_is_runnable():
    """What an operator needs: which module is gone, and a command that installs it."""
    with pytest.raises(SystemExit) as excinfo:
        preflight.require_runtime_deps(_fake_importer(absent={"uvicorn"}))

    message = str(excinfo.value.code)
    assert "uvicorn" in message
    assert "fastapi" not in message  # present deps are not blamed
    install = re.search(r"pip3? install [^()]*", message)
    assert install, f"no install command in: {message}"
    assert "uvicorn" in install.group(0)
    # A nonzero exit: SystemExit with a str code exits 1 (CPython), never 0.
    assert excinfo.value.code != 0


def test_every_missing_module_appears_in_the_hint_in_declaration_order():
    declared = list(preflight.RUNTIME_PIP_MODULES)
    missing = preflight.missing_runtime_modules(_fake_importer(absent=set(declared)))
    assert missing == declared

    message = preflight.format_missing_hint(missing)
    # Every declared module is blamed, and the operator reads them in declaration order
    # (checked by first appearance, so this does not depend on the hint's wording).
    for module in declared:
        assert module in message, f"{module} missing from: {message}"
    positions = [message.index(module) for module in declared]
    assert positions == sorted(positions), f"names out of declaration order: {message}"


def test_partial_install_blames_the_sub_dependency_that_actually_failed():
    """`uvicorn` installed but its own dep gone still raises ModuleNotFoundError.

    Blaming only `uvicorn` would send the operator to reinstall a package that is already
    there; what they need is the name of the module that really could not be imported.
    """

    def _import(name: str) -> object:
        if name == "uvicorn":
            raise ModuleNotFoundError("No module named 'h11'", name="h11")
        return object()

    with pytest.raises(SystemExit) as excinfo:
        preflight.require_runtime_deps(_import)

    message = str(excinfo.value.code)
    assert "h11" in message, f"real culprit not named in: {message}"
    assert "uvicorn" in message  # ...and which declared dep it came in under


def test_broken_install_keeps_its_own_traceback():
    """Only ImportError means 'not provisioned'; anything else must not be swallowed."""

    def _explode(name: str) -> object:
        raise RuntimeError(f"{name} imported but its C extension is broken")

    with pytest.raises(RuntimeError):
        preflight.missing_runtime_modules(_explode)


def test_hint_cannot_leak_a_secret_because_it_never_reads_the_environment(monkeypatch):
    """rules/safety.md: a startup log must not carry .env values."""
    sentinel = "sentinel-never-print-this"
    monkeypatch.setenv("API_SERVER_KEY", sentinel)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", sentinel)
    with_env = preflight.format_missing_hint(["uvicorn"])

    monkeypatch.delenv("API_SERVER_KEY")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY")
    without_env = preflight.format_missing_hint(["uvicorn"])

    assert with_env == without_env
    assert sentinel not in with_env


def test_node_module_defers_both_pip_imports_to_runtime():
    """A module-level ``import uvicorn``/``fastapi`` would explode before the hint can run."""
    assert not _module_level_imported_roots() & set(preflight.RUNTIME_PIP_MODULES)


def test_main_preflights_before_ros_init_and_before_importing_uvicorn():
    """The hint is worthless if it runs after the import (or after ROS side effects)."""
    main = _main_function()
    preflight_calls = [
        n.lineno
        for n in ast.walk(main)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "require_runtime_deps"
    ]
    assert len(preflight_calls) == 1, "main() must call require_runtime_deps exactly once"

    uvicorn_imports = [
        n.lineno
        for n in ast.walk(main)
        if isinstance(n, ast.Import) and any(a.name == "uvicorn" for a in n.names)
    ]
    assert uvicorn_imports, "uvicorn must be imported inside main() (lazy)"

    rclpy_inits = [
        n.lineno
        for n in ast.walk(main)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "init"
    ]
    assert rclpy_inits, "main() must still start rclpy"

    assert preflight_calls[0] < min(uvicorn_imports)
    assert preflight_calls[0] < min(rclpy_inits)
