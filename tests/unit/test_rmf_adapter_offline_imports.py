"""The offline core MUST stay import-clean of ROS/RMF so it runs on the host (doc16 §11).

The package contract (ws/src/warehouse_rmf_adapter/CLAUDE.md) is that the GATE-前
offline modules import neither ``rclpy`` nor ``rmf_*`` nor ``nav2_msgs`` — those are
apt-only and arrive at the R-38 gate (#187). This AST guard fails loudly if a future
edit pulls a ROS/RMF import into the offline slice (which would break host CI, where
.github/workflows/ci.yml installs no rclpy). Mirrors the ast-based structural checks
used elsewhere (e.g. test_modec_noactuation.py).
"""

import ast
import importlib
from pathlib import Path

import pytest

_PKG_DIR = (
    Path(__file__).resolve().parents[2]
    / "ws"
    / "src"
    / "warehouse_rmf_adapter"
    / "warehouse_rmf_adapter"
)

# Offline (GATE-前) modules — the import-clean slice. fleet_adapter.py is the GATE-time
# EasyFullControl shell: it too must stay import-clean pre-gate (it only raises
# NotImplementedError), so it is included here.
_OFFLINE_MODULES = ("nav2_router", "robot_driver", "fleet", "fleet_adapter")

_FORBIDDEN_ROOTS = {
    "rclpy",
    "rmf_fleet_adapter",
    "rmf_fleet_adapter_python",
    "rmf_task_ros2",
    "nav2_msgs",
    "action_msgs",
    "geometry_msgs",
}


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.unit
@pytest.mark.parametrize("mod", _OFFLINE_MODULES)
def test_offline_module_has_no_ros_or_rmf_import(mod: str) -> None:
    roots = _imported_roots(_PKG_DIR / f"{mod}.py")
    leaked = roots & _FORBIDDEN_ROOTS
    assert not leaked, (
        f"{mod}.py imports ROS/RMF pre-gate: {sorted(leaked)} (CLAUDE.md §offline / 11c:273 §3.5 D)"
    )


@pytest.mark.unit
@pytest.mark.parametrize("mod", _OFFLINE_MODULES)  # includes fleet_adapter — no hardcoded drift
def test_offline_module_actually_imports_on_host(mod: str) -> None:
    """Proves every offline module (incl. the GATE shell) is host-importable at load."""
    # import_module raises on a ROS import leak; `is not None` is always true, so assert
    # the actual module identity instead of a tautology.
    obj = importlib.import_module(f"warehouse_rmf_adapter.{mod}")
    assert obj.__name__ == f"warehouse_rmf_adapter.{mod}"


@pytest.mark.unit
def test_gate_shell_still_raises_not_implemented() -> None:
    """The GATE-time EasyFullControl shell must stay inert pre-#187 (no live wiring yet)."""
    from warehouse_rmf_adapter.fleet_adapter import WarehouseRmfFleetAdapterDesign

    with pytest.raises(NotImplementedError):
        WarehouseRmfFleetAdapterDesign()
