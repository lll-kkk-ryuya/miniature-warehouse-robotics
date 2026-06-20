"""web_bridge OBSERVE-ONLY contract (doc22 §1.3 / §12.3, R-26).

``web_bridge`` stands up a new HTTP/WS server inside the robot process — the textbook place
for an actuation path to sneak in later (doc22:281). doc22 demands this be locked by a unit,
not prose. Mirrors the AST source-scan technique of ``tests/unit/test_modec_noactuation.py``
(no ROS import needed): we parse every module in the package and assert the gateway

  * creates NO publisher / service client / action client (it only ever *subscribes* and
    serves read endpoints — doc22:283), and
  * references NO actuation sink: ``/cmd_vel*`` / ``goal_pose`` / ``navigate_to_pose`` / the
    Nav2 Bridge REST routes / the Warehouse MCP tools (doc22:25,:96,:283).

A regression that wired any of these into the observe-only gateway would defeat the R-26
boundary that keeps "browser → robot" impossible (doc22:24). The scan covers the whole
package so it keeps holding as S2 adds the rclpy node + FastAPI app.
"""

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PKG_DIR = _REPO_ROOT / "ws/src/warehouse_web_bridge/warehouse_web_bridge"

# rclpy actuation seams: publishing, calling a service, or driving a Nav2 action. Subscribing
# (``create_subscription``) is the gateway's whole job and is explicitly allowed (doc22:283).
_FORBIDDEN_CALLS = {"create_publisher", "create_client", "ActionClient"}

# Actuation-sink modules the observe-only gateway must never import (doc22:96,:283). The Nav2
# Bridge / MCP server are where real "drive a robot" forwards live.
_FORBIDDEN_IMPORTS = {"warehouse_nav2_bridge", "warehouse_mcp_server"}

# Substrings of actuation topics/routes that must not appear as string literals (doc22:25,:96).
_FORBIDDEN_TOPIC_SUBSTRINGS = ("cmd_vel", "goal_pose", "navigate_to_pose", "/api/v1/navigate")

# Mutating HTTP verbs the observe-only serving face must never register (doc22:246,:283).
_MUTATING_HTTP = {"post", "put", "delete", "patch"}


def _package_modules() -> list[Path]:
    mods = sorted(_PKG_DIR.glob("*.py"))
    assert mods, f"no modules found under {_PKG_DIR}"
    return mods


def _iter_nodes() -> list[tuple[Path, ast.AST]]:
    out: list[tuple[Path, ast.AST]] = []
    for path in _package_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        out.extend((path, node) for node in ast.walk(tree))
    return out


@pytest.mark.safety
@pytest.mark.unit
def test_no_publisher_service_or_action_client():
    offending = []
    for path, node in _iter_nodes():
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in _FORBIDDEN_CALLS:
                offending.append(f"{path.name}: {name}(...)")
    assert not offending, (
        "web_bridge is observe-only (doc22 §12.3 R-26): it must create no publisher / "
        f"service client / action client. Found: {offending}"
    )


@pytest.mark.safety
@pytest.mark.unit
def test_no_actuation_sink_imports():
    imported: set[str] = set()
    for _path, node in _iter_nodes():
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    leaked = imported & _FORBIDDEN_IMPORTS
    assert not leaked, (
        f"web_bridge must not import actuation-sink packages (doc22:96,:283): {sorted(leaked)}"
    )


@pytest.mark.safety
@pytest.mark.unit
def test_no_actuation_topic_string_literals():
    offending = []
    for path, node in _iter_nodes():
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for needle in _FORBIDDEN_TOPIC_SUBSTRINGS:
                if needle in node.value:
                    offending.append(f"{path.name}: {node.value!r}")
    assert not offending, (
        "web_bridge must reference no actuation topic/route (doc22:25,:96 — raw ROS graph "
        f"and Nav2 drive routes are out of scope): {offending}"
    )


@pytest.mark.safety
@pytest.mark.unit
def test_app_serving_face_exposes_no_mutating_http_routes():
    # doc22:246,:283 — the serving face is observe-only: GET + a receive-only WebSocket, and a
    # read-only static mount. No @app.post/put/delete/patch (or upload) may ever appear, so a
    # browser → robot write path cannot be introduced through the gateway's HTTP surface.
    app_py = _PKG_DIR / "app.py"
    tree = ast.parse(app_py.read_text(encoding="utf-8"))
    offending = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _MUTATING_HTTP
    ]
    assert not offending, (
        f"web_bridge app must be observe-only (GET + receive-only WS only, doc22:283): {offending}"
    )
