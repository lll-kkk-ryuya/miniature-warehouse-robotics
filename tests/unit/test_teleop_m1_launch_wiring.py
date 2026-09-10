"""Mode M1 standalone launch の配線 unit（`m1_teleop.launch.py`）— 仕様のみの独立オラクル。

`tests/unit/test_teleop_joymap.py` が pure 写像の**内部**（クランプ・latch・鮮度）を pin する
のに対し、本ファイルはそれを実機で成立させる**前提の固定側**——`joy_node` の起動 param と、
launch が何を起動し何を起動しないか——を pin する。

仕様の出典（すべて実 Read。§5-1/§5-2 は PR #641 の docs で先に land する）:

- docs/mode-m1/03-joystick-teleop-bringup.md:52 — `joy_node` 側の前提 2 つ（`autorepeat_rate`
  を 0 にしない・`sticky_buttons` は false）。**宣言はここ、強制は launch**（同 §5-1 が
  「強制点は現状コマンドだけ・launch はまだ無い」と名指ししている欠落 = CLAUDE.md 残件⑧）。
- 同 §5-1 の param 表 — `autorepeat_rate:=20.0` / `sticky_buttons:=false` / `deadzone:=0.0`
  / `device_id:=0`。`deadzone` 0.0 の理由は「中立判定の正本を teleop 側 0.1 に 1 か所へ寄せる」。
- 同 §5-1「3 プロセスの起動」— `m1_driver` は §2 プローブと G-g の**後**に別端末で上げる。
- 同 :50 — M0-M2 bring-up は standalone（Nav2 / twist_mux を立てない）。
- docs/mode-m1/02-m1-driver-and-watchdog.md:65 / 02:76 — W-4（車輪を完全に浮かせる・主電源
  カットオフに手を掛けたまま）。W-3 不在の間 W-4 は必須層 → launch が driver を勝手に
  起動してはならない（「teleop を上げる」が「車輪へ通電する」を意味しない）。
- 同 §5-2 手順 6 — index の正は `/joy` の実測。既定は `joymap` の単一ソースから来ること。
- .claude/rules/safety.md:7（独立オラクル + mutation）。

期待値は仕様値のリテラルをテスト側に置く（launch から import して同語反復にしない）。launch は
`launch_ros` を import するため**実行せず AST で読む**（pure-CI ホストでも動く。先例
`tests/unit/test_speed_band_bringup_wiring.py`）。実行経路は末尾で importorskip して補う。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.safety]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PKG = _REPO_ROOT / "ws/src/warehouse_teleop"
_LAUNCH = _PKG / "launch/m1_teleop.launch.py"
_JOYMAP = _PKG / "warehouse_teleop/joymap.py"
_SETUP = _PKG / "setup.py"

# ── 仕様値（03 §5-1 の表。実装から import しない） ──────────────────────────────
SPEC_AUTOREPEAT_RATE_HZ = 20.0
SPEC_STICKY_BUTTONS = False
SPEC_JOY_NODE_DEADZONE = 0.0
# joy_node に渡す param はこの 4 つ**だけ**（余分な param は 03 §5-1 に根拠が無い）。
SPEC_JOY_NODE_PARAMS = {"device_id", "autorepeat_rate", "sticky_buttons", "deadzone"}
# launch arg 名 → joymap 側の既定定数名（リテラルを launch に書かない担保）。
SPEC_INDEX_ARGS = {
    "deadman_button": "DEFAULT_DEADMAN_BUTTON",
    "estop_button": "DEFAULT_ESTOP_BUTTON",
    "estop_button_alt": "DEFAULT_ESTOP_BUTTON_ALT",
}
# standalone 構成で起動してよい Node は (package, executable) のこの 2 組だけ。
SPEC_ALLOWED_NODES = {("joy", "joy_node"), ("warehouse_teleop", "teleop_joy")}


def _launch_tree() -> ast.Module:
    return ast.parse(_LAUNCH.read_text(encoding="utf-8"))


def _calls(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]


def _kwarg(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _const(node: ast.expr | None):
    assert isinstance(node, ast.Constant), f"定数リテラルではない: {node and ast.dump(node)}"
    return node.value


def _node_calls() -> list[ast.Call]:
    return _calls(_launch_tree(), "Node")


def _node_named(executable: str) -> ast.Call:
    hits = [c for c in _node_calls() if _const(_kwarg(c, "executable")) == executable]
    assert len(hits) == 1, f"{executable} の Node が 1 つではない（{len(hits)} 個）"
    return hits[0]


def _params_dict(call: ast.Call) -> dict[str, ast.expr]:
    """`parameters=[{...}]` の dict を「キー名 → 値 AST」で返す（1 dict のみ許す）。"""
    parameters = _kwarg(call, "parameters")
    assert isinstance(parameters, ast.List) and len(parameters.elts) == 1, "parameters は dict 1 個"
    literal = parameters.elts[0]
    assert isinstance(literal, ast.Dict)
    entries: dict[str, ast.expr] = {}
    for key, value in zip(literal.keys, literal.values, strict=True):
        assert isinstance(key, ast.Constant) and isinstance(key.value, str)
        entries[key.value] = value
    return entries


def _launch_config_name(node: ast.expr, *, value_type: str | None = None) -> str:
    """`LaunchConfiguration("x")`（必要なら `ParameterValue(..., value_type=T)` 包み）→ "x"。"""
    inner = node
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "ParameterValue":
            assert node.args, "ParameterValue に substitution が渡っていない"
            inner = node.args[0]
            declared = [kw.value for kw in node.keywords if kw.arg == "value_type"]
            assert len(declared) == 1, "value_type が無い（substitution は str で届く）"
            assert isinstance(declared[0], ast.Name)
            if value_type is not None:
                assert declared[0].id == value_type, f"value_type が {value_type} でない"
        elif value_type is not None:
            raise AssertionError(f"型指定なしで渡している: {ast.unparse(node)}")
    assert isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name), ast.unparse(node)
    assert inner.func.id == "LaunchConfiguration", f"launch arg 由来でない: {ast.unparse(node)}"
    return _const(inner.args[0])


def _is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)


# ───────────────── (a) joy_node: 03:52 の前提を launch が固定していること ─────────────────
def test_joy_node_pins_the_two_declared_prerequisites() -> None:
    """`autorepeat_rate` 20.0 / `sticky_buttons` False を launch が渡す（03:52 / §5-1）。

    どちらも「渡し忘れても起動はする」param であり、症状は走行中にしか出ない
    （押しっぱなしで /joy が止まる → 鮮度ガードが正常操作を stale 判定 ／ トグル化で
    rising edge が壊れ非常停止 latch が成立しない）。ここが強制点。
    """
    params = _params_dict(_node_named("joy_node"))

    rate = _const(params["autorepeat_rate"])
    assert rate == SPEC_AUTOREPEAT_RATE_HZ
    assert rate != 0.0, "0 は 03:52 が名指しで禁じている値"
    assert isinstance(rate, float), "double 宣言の param に int リテラルを渡さない"
    assert _const(params["sticky_buttons"]) is SPEC_STICKY_BUTTONS


def test_joy_node_deadzone_is_disabled_so_neutral_has_one_home() -> None:
    """`joy_node` 側 `deadzone` は 0.0（中立判定の正本は teleop 側 0.1 = §5-1 表）。"""
    assert _const(_params_dict(_node_named("joy_node"))["deadzone"]) == SPEC_JOY_NODE_DEADZONE


def test_joy_node_takes_exactly_the_documented_params() -> None:
    """joy_node の param 集合は §5-1 の 4 つと完全一致（根拠の無い param を足さない）。"""
    call = _node_named("joy_node")
    assert _const(_kwarg(call, "package")) == "joy"
    params = _params_dict(call)
    assert set(params) == SPEC_JOY_NODE_PARAMS
    assert _launch_config_name(params["device_id"], value_type="int") == "device_id"


# ───────────────── (b) teleop_joy: 実測 index を上書きできる口があること ─────────────────
def test_teleop_joy_params_are_all_overridable_launch_args() -> None:
    """`bot` と index 3 つが launch arg 由来（§5-2 手順 6: 実測が正 → 起動時に上書き）。"""
    call = _node_named("teleop_joy")
    assert _const(_kwarg(call, "package")) == "warehouse_teleop"
    params = _params_dict(call)
    assert set(params) == {"bot", *SPEC_INDEX_ARGS}
    assert _launch_config_name(params["bot"]) == "bot"
    for name in SPEC_INDEX_ARGS:
        assert _launch_config_name(params[name], value_type="int") == name


# ───────────────── (c) W-4: launch が車輪に通電しない・standalone を崩さない ─────────────────
def test_launch_starts_nothing_but_the_two_standalone_nodes() -> None:
    """起動する Node は joy_node と teleop_joy だけ（W-4 = 02:65 / 02:76 / standalone = 03:50）。

    `m1_driver` は §2 プローブと G-g を通した後、車輪を浮かせ主電源カットオフに手を掛けた
    状態で**別端末**から上げる（§5-1 ③）。launch がここに driver を足すと「teleop を上げる」
    が「車輪へ通電する」になり、W-3 不在下で必須の W-4 が構造的に迂回される。Nav2 /
    twist_mux も M0-M2 では立てない。
    """
    started = {
        (_const(_kwarg(call, "package")), _const(_kwarg(call, "executable")))
        for call in _node_calls()
    }
    assert started == SPEC_ALLOWED_NODES


def test_launch_code_mentions_no_driver_or_mux_wiring() -> None:
    """コード側（docstring/コメントを除く）に driver / mux / nav2 の配線語が出ないこと。

    `IncludeLaunchDescription` などで間接的に上げる経路も塞ぐ。散文で理由を書けるよう
    docstring は除外して**コードだけ**を見る（先例 test_speed_band_bringup_wiring）。
    """
    tree = _launch_tree()
    body = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    sources = [ast.unparse(stmt) for func in body for stmt in func.body if not _is_docstring(stmt)]
    sources += [
        ast.unparse(stmt)
        for stmt in tree.body
        if not _is_docstring(stmt) and not isinstance(stmt, ast.FunctionDef)
    ]
    code = "\n".join(sources)
    for forbidden in ("m1_driver", "warehouse_m1_driver", "twist_mux", "nav2", "Include"):
        assert forbidden not in code, f"launch のコードに {forbidden} が現れている"


# ───────────────── (d) index 既定は joymap の単一ソースから来ること ─────────────────
def test_index_defaults_are_imported_constants_not_literals() -> None:
    """`default_value=str(DEFAULT_*)`（リテラル `'6'` 等を launch に書かない）。

    ボタン並びの真実は `joymap` 側 1 か所（03:52 の既定を保持する定数）。launch にリテラルを
    置くと、実測で既定を直したときに launch だけ古い並びのまま静かに残る。
    """
    declared = {
        _const(call.args[0] if call.args else _kwarg(call, "name")): call
        for call in _calls(_launch_tree(), "DeclareLaunchArgument")
    }
    joymap_names = {
        target.id
        for node in ast.parse(_JOYMAP.read_text(encoding="utf-8")).body
        for target in (
            [node.target] if isinstance(node, ast.AnnAssign) else getattr(node, "targets", [])
        )
        if isinstance(target, ast.Name)
    }

    for arg, constant in SPEC_INDEX_ARGS.items():
        assert arg in declared, f"launch arg {arg} が宣言されていない"
        default = _kwarg(declared[arg], "default_value")
        assert isinstance(default, ast.Call), f"{arg} の既定がリテラル: {ast.unparse(default)}"
        assert isinstance(default.func, ast.Name) and default.func.id == "str"
        assert len(default.args) == 1 and isinstance(default.args[0], ast.Name)
        assert default.args[0].id == constant, f"{arg} の既定が {constant} 由来でない"
        assert constant in joymap_names, f"{constant} が joymap.py に無い（腐った import）"

    assert "bot" in declared and "device_id" in declared


# ───────────────── (e) install: launch が share/ に入ること ─────────────────
def test_setup_installs_the_launch_directory() -> None:
    """`setup.py` が `launch/*.launch.py` を share へ install する（先例 bringup/setup.py:14）。

    これが無いと `ros2 launch warehouse_teleop m1_teleop.launch.py` は
    「file not found」になり、運用は再び手コマンドへ戻る＝この PR の目的が消える。
    """
    tree = ast.parse(_SETUP.read_text(encoding="utf-8"))
    setup_calls = _calls(tree, "setup")
    assert len(setup_calls) == 1
    data_files = _kwarg(setup_calls[0], "data_files")
    assert isinstance(data_files, ast.List)
    entries = [ast.unparse(entry) for entry in data_files.elts]
    launch_entries = [entry for entry in entries if "launch" in entry]
    assert len(launch_entries) == 1, f"launch install の行が 1 つではない: {launch_entries}"
    entry = launch_entries[0]
    assert "share/{package_name}/launch" in entry, entry
    assert "glob(" in entry and "launch/*.launch.py" in entry, entry


# ───────────────── (f) 実行経路（ROS 環境でのみ走る） ─────────────────
def test_launch_description_builds_in_a_ros_environment() -> None:
    """ROS 環境では実際に `generate_launch_description()` が 5 arg + 2 Node を返す。"""
    pytest.importorskip("launch")
    pytest.importorskip("launch_ros")

    import importlib.util

    from launch.actions import DeclareLaunchArgument
    from launch_ros.actions import Node

    spec = importlib.util.spec_from_file_location("m1_teleop_launch", _LAUNCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    entities = module.generate_launch_description().entities

    assert len([e for e in entities if isinstance(e, DeclareLaunchArgument)]) == 5
    assert len([e for e in entities if isinstance(e, Node)]) == 2
    assert len(entities) == 7
