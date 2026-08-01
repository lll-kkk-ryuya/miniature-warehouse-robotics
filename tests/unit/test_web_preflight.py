"""web_bridge heavy-dep preflight hint (#283) — and the anti-drift pin on its file:line cites.

Independent oracle: the hint table's ``deploy/dev/Dockerfile:NN`` citations are checked against
the **real Dockerfile**, not against a copy of the table. If the pip block is reordered the
cited line stops naming its package and these go red — the #165 line-drift class, caught by a
unit instead of by an operator reading a wrong hint at 2am.

The AST checks lock the property that makes the hint reachable at all: ``preflight``/``cli``
must not import the very modules whose absence they explain (``web_bridge_node`` imports
rclpy/uvicorn at module load, so it cannot be the one to report them missing).
"""

import ast
import sys
import types
from pathlib import Path

import pytest
from warehouse_web_bridge import preflight

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PKG_DIR = _REPO_ROOT / "ws/src/warehouse_web_bridge/warehouse_web_bridge"
_DOCKERFILE = _REPO_ROOT / preflight.DOCKERFILE

# The deps that must NOT be importable at module load for the hint path to survive.
_HEAVY = {"rclpy", "uvicorn", "fastapi", "std_msgs", "websockets", "langfuse"}


def _dockerfile_lines() -> list[str]:
    return _DOCKERFILE.read_text(encoding="utf-8").splitlines()


def _module_level_imports(path: Path) -> set[str]:
    """Top-level module names imported at MODULE level (function-local imports excluded)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:  # module level only — not ast.walk
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


# ── the cited pip lines really are the pip lines (anti-drift) ─────────────────────────────


@pytest.mark.unit
def test_pip_block_bounds_point_at_a_real_pip_install_block() -> None:
    lines = _dockerfile_lines()
    start, end = preflight.PIP_BLOCK_LINES
    assert "pip3 install" in lines[start - 1], lines[start - 1]
    # every line but the last continues with a backslash; the last one closes the block
    for n in range(start, end):
        assert lines[n - 1].rstrip().endswith("\\"), f"{preflight.DOCKERFILE}:{n} not continued"
    assert not lines[end - 1].rstrip().endswith("\\"), f"pip block does not end at :{end}"


@pytest.mark.unit
@pytest.mark.parametrize("module", ["fastapi", "uvicorn", "langfuse"])
def test_cited_dockerfile_line_names_its_package(module: str) -> None:
    hint = preflight.hint_for(module)
    path, _, lineno = hint.source.partition(":")
    assert path == preflight.DOCKERFILE
    line = _dockerfile_lines()[int(lineno) - 1]
    assert module in line, f"{hint.source} no longer names {module!r}: {line!r}"


@pytest.mark.unit
def test_websockets_is_flagged_as_not_provisioned_by_the_dev_image() -> None:
    # setup.py install_requires has it (uvicorn's /ws fan-out needs it) but the dev image's
    # pip block does not — the hint says so. When a future PR adds it, this goes red and the
    # table must be re-pinned rather than quietly lying.
    assert "websockets" not in _DOCKERFILE.read_text(encoding="utf-8")
    assert "NOT yet listed" in preflight.hint_for("websockets").source


# ── hint content ──────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_pip_dep_hint_names_the_spec_and_where_it_is_provisioned() -> None:
    msg = preflight.missing_dependency_hint(ModuleNotFoundError(name="uvicorn"))
    assert "uvicorn" in msg
    assert "uvicorn>=0.27" in msg  # the spec an operator can paste
    assert "deploy/dev/Dockerfile:45" in msg  # where it belongs


@pytest.mark.unit
def test_ros_dep_hint_says_source_the_overlay_not_pip() -> None:
    msg = preflight.missing_dependency_hint(ModuleNotFoundError(name="rclpy.qos"))
    assert "/opt/ros/jazzy/setup.bash" in msg
    assert "pip3 install" not in msg  # rclpy is not pip-installable — never suggest it


@pytest.mark.unit
def test_workspace_dep_hint_says_colcon_build() -> None:
    msg = preflight.missing_dependency_hint(ModuleNotFoundError(name="warehouse_interfaces"))
    assert "colcon build" in msg


@pytest.mark.unit
@pytest.mark.parametrize(
    "name,expected", [("rclpy.qos", "rclpy"), ("std_msgs.msg", "std_msgs"), ("uvicorn", "uvicorn")]
)
def test_submodule_names_resolve_to_their_top_level_package(name: str, expected: str) -> None:
    assert preflight.missing_module_name(ModuleNotFoundError(name=name)) == expected


@pytest.mark.unit
def test_unknown_and_nameless_import_errors_degrade_instead_of_raising() -> None:
    assert preflight.missing_module_name(ImportError("cannot import name 'x'")) is None
    msg = preflight.missing_dependency_hint(ImportError("cannot import name 'x'"))
    assert "web_bridge cannot start" in msg
    assert preflight.DOCKERFILE in msg  # generic pointer, still actionable
    # an unrecognized module must not be given a made-up pip spec
    unknown = preflight.missing_dependency_hint(ModuleNotFoundError(name="totally_unknown_pkg"))
    assert "setup.py install_requires" in unknown


# ── the hint path stays importable in the broken environment ──────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("module", ["preflight.py", "cli.py"])
def test_hint_modules_import_no_heavy_dependency_at_module_level(module: str) -> None:
    leaked = _module_level_imports(_PKG_DIR / module) & _HEAVY
    assert not leaked, f"{module} would fail to import in the env it must explain: {leaked}"


@pytest.mark.unit
def test_cli_defers_the_node_import_into_main() -> None:
    # If web_bridge_node were imported at module level the preflight would never run.
    tree = ast.parse((_PKG_DIR / "cli.py").read_text(encoding="utf-8"))
    node_imports = [
        n
        for n in tree.body
        if isinstance(n, ast.ImportFrom) and n.module == "warehouse_web_bridge.web_bridge_node"
    ]
    assert not node_imports, "cli must import the node inside main(), not at module level"


@pytest.mark.unit
def test_cli_reports_a_blocked_node_import_as_a_workspace_problem(monkeypatch, capsys) -> None:
    from warehouse_web_bridge import cli

    # None in sys.modules is the stdlib's own "this import is blocked" marker, so the import
    # raises ImportError with no ROS needed. The name it carries is the NODE module, whose
    # top-level segment is the package itself — hence the workspace hint, not a pip one.
    monkeypatch.setitem(sys.modules, "warehouse_web_bridge.web_bridge_node", None)
    assert cli.main() == 1
    err = capsys.readouterr().err
    assert "web_bridge cannot start" in err
    assert "colcon build" in err  # the hint this path actually produces


@pytest.mark.unit
@pytest.mark.parametrize(
    "module,spec,where",
    [("fastapi", "fastapi>=0.110", "deploy/dev/Dockerfile:44"), ("uvicorn", "uvicorn>=0.27", None)],
)
def test_cli_reports_a_lazily_imported_dep_that_fails_at_run_time(
    monkeypatch, capsys, module: str, spec: str, where: str | None
) -> None:
    # fastapi is imported INSIDE create_app and websockets by uvicorn at serve time, so their
    # absence surfaces after the node import succeeded. Without guarding the call too, the
    # operator gets the bare traceback #283 is about, and the fastapi/websockets rows of the
    # hint table would be unreachable decoration.
    from warehouse_web_bridge import cli

    def _explode() -> None:
        raise ModuleNotFoundError(f"No module named {module!r}", name=module)

    fake = types.ModuleType("warehouse_web_bridge.web_bridge_node")
    fake.main = _explode
    monkeypatch.setitem(sys.modules, "warehouse_web_bridge.web_bridge_node", fake)
    assert cli.main() == 1
    err = capsys.readouterr().err
    assert spec in err
    if where is not None:
        assert where in err


@pytest.mark.unit
def test_cli_does_not_disguise_an_unrelated_import_error_as_provisioning(monkeypatch) -> None:
    # The guard must not swallow runtime bugs: an ImportError the table cannot explain keeps
    # its traceback rather than sending the operator to the Dockerfile.
    from warehouse_web_bridge import cli

    def _explode() -> None:
        raise ImportError("cannot import name 'Nope' from 'somewhere'")

    fake = types.ModuleType("warehouse_web_bridge.web_bridge_node")
    fake.main = _explode
    monkeypatch.setitem(sys.modules, "warehouse_web_bridge.web_bridge_node", fake)
    with pytest.raises(ImportError, match="Nope"):
        cli.main()


@pytest.mark.unit
def test_is_provisioning_failure_only_claims_modules_the_table_can_explain() -> None:
    known = [
        ModuleNotFoundError(name="fastapi"),
        ModuleNotFoundError(name="websockets"),
        ModuleNotFoundError(name="rclpy.qos"),
        ModuleNotFoundError(name="eval_sdk"),
    ]
    assert all(preflight.is_provisioning_failure(exc) for exc in known)
    assert not preflight.is_provisioning_failure(ModuleNotFoundError(name="totally_unknown_pkg"))
    assert not preflight.is_provisioning_failure(ImportError("cannot import name 'x'"))


@pytest.mark.unit
def test_cli_runs_the_gateway_when_the_deps_are_present(monkeypatch) -> None:
    from warehouse_web_bridge import cli

    calls: list[int] = []
    fake = types.ModuleType("warehouse_web_bridge.web_bridge_node")
    fake.main = lambda: calls.append(1)
    monkeypatch.setitem(sys.modules, "warehouse_web_bridge.web_bridge_node", fake)
    assert cli.main() == 0
    assert calls == [1]
