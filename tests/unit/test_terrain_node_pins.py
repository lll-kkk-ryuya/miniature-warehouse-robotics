"""`terrain_publisher`（07_Output_Adapters node）の構造 pin（AST・R-26）。

node モジュールは rclpy / sensor_msgs に依存するため **host / CI では import
できない**。ゆえに「何を publish するか」「stamp をどこから取るか」「既定で何も
作らないか」「終了が 3 規則を満たすか」を **AST で** 固定する
（`tests/unit/test_speed_band_publisher.py` の流儀・#565 AST floor guard と同系）。

固定する主張と出典:

- **publish は 2 topic のみ**（`cliff_scan` / `terrain/coverage`）。`cmd_vel` /
  `stop_request` / `stop_state` / `speed_limit` の producer にならない＝
  **自律走行（安全層外）** の producer という layer 帰属そのもの
  （`docs/mode-outdoor/04-perception-sidewalk-and-signals.md:189` / `:512`）。
- **相対名**であること（`/bot{n}` namespace 下で解決＝追補 ⑨ 裁定 1。絶対名は
  namespace を跨ぐ）。
- **stamp は入力 depth frame 由来**で `now()` 由来でない（`:177`「変換後も元の
  計測時刻を維持」）。
- **`enabled` が False なら subscription も publisher も作らない**
  （safe-OFF＝`speed_band_node` / ADR-0012 決定 4 と同型）。
- **終了 3 規則**（両例外を握って normal return・best-effort・`try_shutdown`）＝
  `tests/unit/test_node_shutdown_lifecycle.py` のラチェットに最初から合格する形。
- **numpy / torch を import しない**（P1 原則＝
  `docs/architecture/23-perception-and-localization.md:37`）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from warehouse_perception.terrain_node_core import PARAM_KEYS

pytestmark = [pytest.mark.unit, pytest.mark.safety]

_NODE_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "ws"
    / "src"
    / "warehouse_perception"
    / "warehouse_perception"
    / "terrain_node.py"
)


def _source() -> str:
    return _NODE_SOURCE.read_text(encoding="utf-8")


def _tree() -> ast.Module:
    return ast.parse(_source())


def _class_def(name: str) -> ast.ClassDef:
    for node in _tree().body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _function(name: str) -> ast.FunctionDef:
    for node in _tree().body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found")


def _method(cls: str, name: str) -> ast.FunctionDef:
    for item in _class_def(cls).body:
        if isinstance(item, ast.FunctionDef) and item.name == name:
            return item
    raise AssertionError(f"{cls}.{name} not found")


def _calls(node: ast.AST, attr: str) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr
    ]


# ── ① publish する topic は 2 本だけ・相対名 ─────────────────────────────
def test_the_node_publishes_exactly_the_two_terrain_topics() -> None:
    topics = [
        call.args[1].value
        for call in _calls(_tree(), "create_publisher")
        if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant)
    ]
    assert sorted(topics) == ["cliff_scan", "terrain/coverage"]
    # 引数に定数以外を渡して pin をすり抜けるリファクタを封じる。
    assert len(topics) == len(_calls(_tree(), "create_publisher"))


def test_the_published_topic_names_are_relative() -> None:
    """絶対名は namespace を跨ぐ（追補 ⑨ 裁定 1・ADR-0012 決定 11 と同じ理由）。"""
    for call in _calls(_tree(), "create_publisher"):
        assert not call.args[1].value.startswith("/"), ast.dump(call)


def test_the_publisher_message_types_are_the_contracted_ones() -> None:
    types = {
        (call.args[0].id, call.args[1].value)
        for call in _calls(_tree(), "create_publisher")
        if isinstance(call.args[0], ast.Name)
    }
    assert types == {("LaserScan", "cliff_scan"), ("String", "terrain/coverage")}


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """docstring の Constant の id 集合（散文は pin の対象外＝出典を書けなくなる）。"""
    ids: set[int] = set()
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(scope, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            ids.add(id(body[0].value))
    return ids


def _executable_strings_and_identifiers() -> list[str]:
    """実行時に効く文字列（docstring を除く）と、全識別子。

    散文（docstring / コメント）で `cmd_vel` に言及するのは設計上必要なので、
    pin は「topic 名になり得るもの」＝**実行される文字列と識別子**だけを見る。
    """
    tree = _tree()
    docstrings = _docstring_nodes(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                found.append(node.value)
        elif isinstance(node, ast.Name):
            found.append(node.id)
        elif isinstance(node, ast.Attribute):
            found.append(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.append(node.name)
    return found


@pytest.mark.parametrize(
    "token", ["cmd_vel", "stop_request", "stop_state", "speed_limit", "emergency"]
)
def test_the_node_is_not_a_producer_of_any_motion_or_stop_topic(token: str) -> None:
    """安全層外の producer である以上、実行される名前にこれらの語が現れてはならない。

    散文での言及（「この node は `cmd_vel` を出さない」）は許すが、**topic 名に
    なり得る文字列と識別子**からは完全に排除する。
    """
    offenders = [s for s in _executable_strings_and_identifiers() if token in s]
    assert not offenders, offenders


def test_the_node_creates_no_timer() -> None:
    """周期 re-publish をしない＝古い観測を新しい stamp で流さない（裁定 5）。"""
    assert _calls(_tree(), "create_timer") == []


# ── ② stamp は入力 msg の header 由来（`now()` 由来でない） ───────────────
def _assignments_to_attribute_chain(chain: tuple[str, ...]) -> list[ast.Assign]:
    """`<何か>.header.stamp = ...` のような属性連鎖への代入を拾う。"""
    found: list[ast.Assign] = []
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            parts: list[str] = []
            cursor: ast.AST = target
            while isinstance(cursor, ast.Attribute):
                parts.append(cursor.attr)
                cursor = cursor.value
            if tuple(reversed(parts)) == chain:
                found.append(node)
    return found


def test_every_outgoing_stamp_is_copied_from_the_incoming_message_header() -> None:
    """`msg.header.stamp` をそのまま載せる。`now()` 由来の代入は 1 本も無い（:177）。"""
    assignments = _assignments_to_attribute_chain(("header", "stamp"))
    assert assignments, "header.stamp への代入が 1 本も無い"
    for assign in assignments:
        value = assign.value
        assert isinstance(value, ast.Attribute) and value.attr == "stamp", ast.dump(value)
        inner = value.value
        assert isinstance(inner, ast.Attribute) and inner.attr == "header", ast.dump(value)
        # 右辺に Call が含まれないこと = now() / to_msg() 由来でないこと。
        assert not [n for n in ast.walk(value) if isinstance(n, ast.Call)], ast.dump(value)


def test_the_clock_is_never_read_on_the_publishing_path() -> None:
    """`get_clock()` は遅延の自己申告専用（`_now`）で、stamp 生成には使わない。"""
    publish = _method("TerrainPublisher", "_publish")
    assert _calls(publish, "get_clock") == []
    assert "to_msg" not in ast.dump(ast.Module(body=[publish], type_ignores=[]))
    # 時計を読む唯一の場所。
    owners = {
        fn.name
        for fn in ast.walk(_class_def("TerrainPublisher"))
        if isinstance(fn, ast.FunctionDef) and _calls(fn, "get_clock")
    }
    assert owners == {"_now"}


def test_the_device_frame_counter_is_reported_as_absent_not_invented() -> None:
    """`sensor_msgs/Image` は機器フレーム番号を運ばない → `None`（追補 ③ #5）。"""
    keywords = [
        kw
        for call in ast.walk(_tree())
        if isinstance(call, ast.Call)
        for kw in call.keywords
        if kw.arg == "device_frame_seq"
    ]
    assert len(keywords) == 1
    assert isinstance(keywords[0].value, ast.Constant) and keywords[0].value.value is None


# ── ③ safe-OFF: enabled=False なら何も作らない ───────────────────────────
def test_the_disabled_path_returns_before_creating_anything() -> None:
    """`enabled` が False のとき publisher / subscription に到達しない構造。

    値ベースでは確かめられない（rclpy が無い）ので、`__init__` の中で
    「`enabled` を見る if の中の bare `return`」が、あらゆる
    `create_publisher` / `create_subscription` より**前**にあることを固定する。
    """
    init = _method("TerrainPublisher", "__init__")
    guard_line: int | None = None
    for node in ast.walk(init):
        if not isinstance(node, ast.If):
            continue
        if "enabled" not in ast.unparse(node.test):
            continue
        returns = [n for n in ast.walk(node) if isinstance(n, ast.Return) and n.value is None]
        if returns:
            guard_line = max(r.lineno for r in returns)
    assert guard_line is not None, "enabled を見る早期 return が無い"
    creations = _calls(init, "create_publisher") + _calls(init, "create_subscription")
    assert creations, "有効時の生成が 1 本も無い"
    for call in creations:
        assert call.lineno > guard_line, f"safe-OFF の return より前に生成: {ast.dump(call)}"


def test_the_enabled_default_is_false() -> None:
    declarations = [
        call
        for call in _calls(_tree(), "declare_parameter")
        if call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value == "enabled"
    ]
    assert len(declarations) == 1
    assert declarations[0].args[1].value is False


def test_the_input_topics_default_to_not_subscribing() -> None:
    defaults = {
        call.args[0].value: call.args[1].value
        for call in _calls(_tree(), "declare_parameter")
        if len(call.args) >= 2
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[1], ast.Constant)
    }
    assert defaults["depth_topic"] == ""
    assert defaults["camera_info_topic"] == ""


# ── ④ 宣言した param 集合と core が読む集合が一致する ────────────────────
def _declared_sentinels() -> dict[str, object]:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "_SENTINELS":
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "_SENTINELS" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("_SENTINELS not found")


def test_the_node_declares_exactly_the_parameters_the_core_reads() -> None:
    """宣言漏れ（core が KeyError→abort）も余剰宣言（誰も読まない旋盤）も防ぐ。"""
    assert set(_declared_sentinels()) == set(PARAM_KEYS)


def test_every_declared_sentinel_is_a_zero_or_empty_value() -> None:
    """既定は必ず「使えない印」＝ 0 / 0.0 / ""（実在しそうな値を置かない）。"""
    for name, value in _declared_sentinels().items():
        assert value in (0, 0.0, ""), f"{name} の宣言既定が sentinel でない: {value!r}"


def test_the_subscriptions_and_publishers_use_depth_ten() -> None:
    """既存 producer（virtual_scan / speed_limit）と同じ深さ（裁定 4・新しい値ではない）。"""
    for call in _calls(_tree(), "create_publisher"):
        assert isinstance(call.args[2], ast.Constant) and call.args[2].value == 10
    for call in _calls(_tree(), "create_subscription"):
        assert isinstance(call.args[3], ast.Constant) and call.args[3].value == 10


# ── ⑤ 終了 3 規則（ラチェット baseline に足さない形） ─────────────────────
def test_main_catches_both_normal_stop_exceptions_and_calls_try_shutdown() -> None:
    main = _function("main")
    handlers = [n for n in ast.walk(main) if isinstance(n, ast.ExceptHandler)]
    caught = {
        ast.unparse(name)
        for handler in handlers
        for name in (handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type])
        if handler.type is not None
    }
    assert {"KeyboardInterrupt", "ExternalShutdownException"} <= caught
    calls = {ast.unparse(n.func) for n in ast.walk(main) if isinstance(n, ast.Call)}
    assert "rclpy.try_shutdown" in calls
    assert "rclpy.shutdown" not in calls  # 素の shutdown は Humble で exit 1
    assert "node.destroy_node" in calls


def test_the_shutdown_ratchet_baseline_is_untouched_by_this_node() -> None:
    """新 node は `KNOWN_UNSAFE_STOP_ON_HUMBLE` に**入れない**（最初から合格する）。"""
    baseline = (Path(__file__).resolve().parent / "test_node_shutdown_lifecycle.py").read_text(
        encoding="utf-8"
    )
    assert "terrain_node" not in baseline


# ── ⑥ 重い依存を持ち込まない（P1 原則） ──────────────────────────────────
@pytest.mark.parametrize("module", ["numpy", "torch", "tensorrt", "cv2", "cv_bridge"])
def test_the_node_imports_no_heavy_runtime(module: str) -> None:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] != module for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != module


def test_the_node_depends_only_on_the_shared_contract_packages() -> None:
    """他トラックの内部 module を import しない（parallel-workflow §2.1）。"""
    roots = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert roots <= {
        "__future__",
        "rclpy",
        "sensor_msgs",
        "std_msgs",
        "warehouse_description",  # 共有資産（BASE_FRAME）
        "warehouse_perception",  # 自パッケージ
    }, sorted(roots)
