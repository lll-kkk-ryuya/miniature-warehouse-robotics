"""bringup 配線側の安全 unit（地形 publisher スライス）— 仕様のみから書いた独立オラクル。

`tests/unit/test_terrain_node_core.py` / `test_terrain_node_pins.py` が producer の**内部**
（depth decode・sentinel・0 actuation）を pin するのに対し、本ファイルは producer を
**起動する側**——`ws/src/warehouse_bringup/launch/nav2_bringup.launch.py` の
`_terrain_config` / `_terrain_group` と `config/warehouse.base.yaml` の
`perception.terrain.*`——を pin する。`tests/unit/test_speed_band_bringup_wiring.py`
（速度帯スライスの同型）と同じ手法。

仕様の出典（すべて実 Read。行は origin/main c0381a1 で確認）:

- docs/mode-outdoor/04-perception-sidewalk-and-signals.md:943 裁定 8 — 21 param は
  **全部注入・既定なし**。ROS param の宣言既定は「検証で落ちる sentinel」であり、
  値を持たずに `enabled` を立てれば**起動時 abort**（fail-closed）。config キー
  `perception.terrain.*` は `speed_bands.*` と同型、という案がここで実体になる。
- 同 :941 裁定 6 — `depth_topic` / `camera_info_topic` は param・既定 `""`（= 購読しない
  safe-OFF）。`enabled` が true で片方でも空なら起動時 abort。
- 同 :978-980 fail 方向 — `enabled` 未設定は subscription 0 / publisher 0 / timer 0、
  `enabled:=true` かつ param 未注入 / 入力 topic 空は起動時 abort。
- 同 :992 hand-off ③ — 値は**車体組立とカメラ取付の実測後**（`OQ-OD45` :143 /
  `OQ-OD4Q` :354 / `OQ-OD4Z-d1` :897）。launch も base config も値を**発明しない**。
- 同 :930 レイヤ注記 — 本 node は自律走行（安全層外）の producer。`cmd_vel` /
  `stop_request` / `stop_state` / `speed_limit` の producer にならない。配線側でも
  remap でそこへ向けない（node 側 AST pin の配線版）。
- docs/adr/0012-speed-band-no-l2-best-effort.md:21 決定 4 — config 注入＋起動時
  fail-closed の先例（`_speed_band_group` がその実体）。
- .claude/rules/environments.md — base + overlay・base は共通のみ。実値は
  `config/<env>/warehouse.yaml`。
- .claude/rules/safety.md:7（独立オラクル + mutation）。

期待値は仕様値・手計算リテラル。**21 / 23 という本数も、キー名の並びも、ここで再ハード
コードしない**: param 名の単一ソースは `warehouse_perception.terrain_node_core.PARAM_KEYS`
（rclpy 非依存ゆえ host で import できる）、型の単一ソースは `terrain_node.py` の
`_SENTINELS`（rclpy を import するので **AST で読む**＝`test_terrain_node_pins.py` の手法）。
launch は `nav2_common` / `launch_ros` を import するため**実行せず AST で読む**。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml
from warehouse_perception.terrain_node_core import PARAM_KEYS

pytestmark = [pytest.mark.unit, pytest.mark.safety]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LAUNCH = _REPO_ROOT / "ws/src/warehouse_bringup/launch/nav2_bringup.launch.py"
_TERRAIN_NODE = _REPO_ROOT / "ws/src/warehouse_perception/warehouse_perception/terrain_node.py"
_BASE_CONFIG = _REPO_ROOT / "config/warehouse.base.yaml"

# 仕様値（04:941 裁定 6）: param 面のうち PARAM_KEYS に**入らない** 3 本。node は
# `enabled`(bool) と 2 本の入力 topic(str) を別に declare する。テスト側リテラル。
SPEC_GATE_KEY = "enabled"
SPEC_TOPIC_KEYS = ("depth_topic", "camera_info_topic")
# 配線が起動する実体（04 追補 ⑨ §6 / warehouse_perception/setup.py の console_script）。
SPEC_PACKAGE = "warehouse_perception"
SPEC_EXECUTABLE = "terrain_publisher"
# 走行・停止・速度の topic 語彙（04:930 レイヤ注記）。配線側がこれらに触れたら赤。
SPEC_ACTUATION_TOKENS = ("cmd_vel", "stop_request", "stop_state", "speed_limit")


def _launch_tree() -> ast.Module:
    return ast.parse(_LAUNCH.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() が launch に無い")


def _is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)


def _executable_code(scope: ast.FunctionDef) -> str:
    """docstring を除いた**コードだけ**を文字列化する（コメントは AST に無い）。

    散文に語を書いただけで赤/緑が動く脆いテキスト検査を避けるため。
    """
    body = [node for node in scope.body if not _is_docstring(node)]
    return "\n".join(ast.unparse(node) for node in body)


def _module_constant(tree: ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} が見つからない")


def _declared_sentinels() -> dict[str, object]:
    """node の `_SENTINELS` を AST で読む（terrain_node は rclpy を import する）。"""
    return _module_constant(ast.parse(_TERRAIN_NODE.read_text(encoding="utf-8")), "_SENTINELS")


def _calls_to(scope: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(scope)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]


def _keyword(call: ast.Call, arg: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == arg:
            return kw.value
    return None


# ──────────────── 配線されていること（起動しなければ全機能が無い） ────────────────
def test_the_terrain_group_is_added_once_per_robot_from_the_bringup_loop() -> None:
    """`_terrain_group` が per-robot ループから 1 回だけ呼ばれ、結果が ld に入る。

    「関数は書いたが誰も呼んでいない」＝node が永久に起動しない、という最も静かな
    配線事故をここで落とす。
    """
    tree = _launch_tree()
    gen = _function(tree, "generate_launch_description")
    calls = _calls_to(gen, "_terrain_group")
    assert len(calls) == 1, f"_terrain_group() の呼び出しが 1 回ではない（{len(calls)}）"
    passed = [arg.id for arg in calls[0].args if isinstance(arg, ast.Name)]
    assert passed[:1] == ["robot"], f"第1引数が robot でない: {passed}"
    assert "use_sim_time" in passed, f"use_sim_time が渡っていない: {passed}"

    # per-robot ループの内側にあること（共有 map_server 側に置くと namespace が付かない）。
    robot_loops = [
        node
        for node in ast.walk(gen)
        if isinstance(node, ast.For)
        and isinstance(node.target, ast.Name)
        and node.target.id == "robot"
    ]
    assert len(robot_loops) == 1
    assert _calls_to(robot_loops[0], "_terrain_group"), "per-robot ループの外に居る"
    # 既存の speed band 配線を壊していない（同じループに両方が居る）。
    assert _calls_to(robot_loops[0], "_speed_band_group"), "速度帯の配線が消えている"


def test_the_terrain_node_starts_under_the_robot_namespace_with_sim_time() -> None:
    """`PushRosNamespace(robot)` + `SetParameter("use_sim_time", …)` + 1 Node。

    namespace push が無いと相対名 `cliff_scan` が `/cliff_scan` に解決し、2 台構成で
    両機の崖が同じ topic に混ざる（04 裁定 1 が絶対名を禁じた理由そのもの）。
    """
    group = _function(_launch_tree(), "_terrain_group")

    pushes = _calls_to(group, "PushRosNamespace")
    assert len(pushes) == 1
    assert isinstance(pushes[0].args[0], ast.Name) and pushes[0].args[0].id == "robot"

    set_params = _calls_to(group, "SetParameter")
    assert len(set_params) == 1
    first = set_params[0].args[0]
    assert isinstance(first, ast.Constant) and first.value == "use_sim_time"

    nodes = _calls_to(group, "Node")
    assert len(nodes) == 1, "起動するのは terrain_publisher 1 本だけ"
    for arg, expected in (("package", SPEC_PACKAGE), ("executable", SPEC_EXECUTABLE)):
        value = _keyword(nodes[0], arg)
        assert isinstance(value, ast.Constant) and value.value == expected, arg
    name = _keyword(nodes[0], "name")
    assert isinstance(name, ast.Constant) and name.value == SPEC_EXECUTABLE


# ──────────────── safe-OFF（既定で何も起動しない） ────────────────
def test_the_group_is_empty_unless_config_enables_it() -> None:
    """`enabled` が偽なら `[]` を返し、しかも**生成より前に**返る（04:978）。

    早期 return が Node 構築より後ろにあると「OFF なのに Node を作ってから捨てる」形に
    なり、将来の副作用（param 解決・警告）を招く。順序ごと pin する。
    """
    group = _function(_launch_tree(), "_terrain_group")
    empty_returns = [
        node
        for node in ast.walk(group)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.List)
        and node.value.elts == []
    ]
    assert len(empty_returns) == 1, "OFF 経路の `return []` が 1 つではない"

    guards = [node for node in ast.walk(group) if isinstance(node, ast.If)]
    assert any(any(r is empty_returns[0] for r in ast.walk(guard)) for guard in guards), (
        "`return []` が条件分岐の中に無い（無条件 OFF / 無条件 ON）"
    )
    gate_source = "\n".join(ast.unparse(g.test) for g in guards)
    assert SPEC_GATE_KEY in gate_source, "gate が `enabled` を読んでいない"

    creations = [
        node.lineno
        for node in ast.walk(group)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"Node", "GroupAction"}
    ]
    assert creations, "Node / GroupAction が無い"
    assert empty_returns[0].lineno < min(creations), "早期 return が生成より後ろにある"


def test_the_config_block_is_read_through_perception_then_terrain() -> None:
    """`load_config()["perception"]["terrain"]` を辿り、dict でなければ空に倒す。

    キー経路を間違えると「overlay に書いたのに効かない」＝**黙って OFF のまま**になる。
    dict 以外（誤って文字列を書いた overlay）でも例外にせず空 dict に倒すのは、
    speed_bands と同じ形（起動失敗の責任は node の fail-closed 検証に集約する）。
    """
    config_fn = _function(_launch_tree(), "_terrain_config")
    code = _executable_code(config_fn)
    assert "load_config()" in code
    # ast.unparse は引用符を正規化するので、構造（get の第1引数）で見る。
    lookups = [
        node.args[0].value
        for node in ast.walk(config_fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    ]
    assert lookups == ["perception", "terrain"], f"キー経路が違う: {lookups}"
    assert "isinstance" in code, "dict 以外を空に倒すガードが無い"


# ──────────────── 転送するキー集合（node の宣言集合と 1:1） ────────────────
def test_forwarded_parameter_names_are_exactly_the_nodes_declared_surface() -> None:
    """転送集合 = {enabled} ∪ 入力 topic 2 本 ∪ `PARAM_KEYS`。

    rclpy は**未宣言 param を拒否**するので余剰キーは起動失敗になり、欠落キーは
    sentinel 残置＝起動 abort（04:979）。どちらも「enabled にした瞬間に落ちる」形の
    事故なので、静的にここで落とす。**21 / 23 を再ハードコードせず**、単一ソース
    （`PARAM_KEYS` と node の `_SENTINELS`）と突き合わせる。
    """
    tree = _launch_tree()
    group = _function(tree, "_terrain_group")
    code = _executable_code(group)

    # ① 入力 topic 2 本（モジュール定数）。
    topic_keys = tuple(_module_constant(tree, "_TERRAIN_TOPIC_KEYS"))
    assert topic_keys == SPEC_TOPIC_KEYS, topic_keys
    assert "_TERRAIN_TOPIC_KEYS" in code, "topic キーを転送していない"

    # ② gate キーは dict リテラルに直接置かれる。
    literal_keys = {
        literal.value
        for node in ast.walk(group)
        if isinstance(node, ast.Dict)
        for literal in node.keys
        if isinstance(literal, ast.Constant) and isinstance(literal.value, str)
    }
    assert literal_keys == {SPEC_GATE_KEY}, f"リテラルのキーが想定外: {literal_keys}"

    # ③ 21 param は単一ソース `PARAM_KEYS` を**そのまま反復**する（再列挙しない）。
    imported = {
        alias.name
        for node in ast.walk(group)
        if isinstance(node, ast.ImportFrom)
        and node.module == "warehouse_perception.terrain_node_core"
        for alias in node.names
    }
    assert "PARAM_KEYS" in imported, "PARAM_KEYS を単一ソースから import していない"
    assert any(
        isinstance(node, ast.For)
        and isinstance(node.iter, ast.Name)
        and node.iter.id == "PARAM_KEYS"
        for node in ast.walk(group)
    ), "PARAM_KEYS を反復していない（本数を再ハードコードした疑い）"

    # ④ 合成した面が node の宣言面（_SENTINELS ∪ enabled ∪ topics）と一致する。
    forwarded = {SPEC_GATE_KEY, *topic_keys, *PARAM_KEYS}
    declared = {SPEC_GATE_KEY, *SPEC_TOPIC_KEYS, *_declared_sentinels()}
    assert forwarded == declared, forwarded ^ declared

    # ⑤ 未知キーを素通しさせない（overlay の typo が rclpy の起動失敗になるのを防ぐ）。
    assert "**terrain" not in code, "config を丸ごと展開している（未知キーが届く）"
    assert ".update(" not in code, "config を丸ごと update している（未知キーが届く）"


def test_absent_keys_are_left_unset_so_the_node_can_fail_closed() -> None:
    """config に無いキーは**転送しない**（launch が既定を与えない・04:943 裁定 8）。

    ここで `else:` 側に既定を入れると、node の sentinel が「注入済みの正当値」に
    すり替わり、**未実測の幾何で崖判定が動き出す**（fail-closed が fail-open へ反転）。
    """
    group = _function(_launch_tree(), "_terrain_group")
    code = _executable_code(group)
    assert re.search(r"if key not in terrain:\s*\n\s*continue", code), (
        "欠落キーを skip する分岐が無い"
    )
    assert ".get(key" not in code, "既定つき get で欠落キーを埋めている"


def test_no_parameter_value_literal_is_invented_in_the_launch() -> None:
    """`_terrain_config` / `_terrain_group` に**数値リテラルが 1 つも無い**こと。

    04:992 hand-off ③ は「値は車体組立とカメラ取付の実測後」と言う。launch に
    `pixel_stride: 1` のような「無害に見える既定」が 1 つでも入ると、それは
    docs に無い値の発明であり、しかも**最も気づかれない**形の発明になる。
    bool（`enabled` の True / False）は値ではなく gate なので除外する。
    """
    tree = _launch_tree()
    for fn_name in ("_terrain_config", "_terrain_group"):
        numbers = [
            node.value
            for node in ast.walk(_function(tree, fn_name))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)
        ]
        assert numbers == [], f"{fn_name}() に数値リテラル {numbers}"


# ──────────────── 型の単一ソース（宣言 sentinel の型に合わせる） ────────────────
def test_int_and_str_coercions_match_the_nodes_declared_sentinel_types() -> None:
    """launch の int / str キー表 == node の `_SENTINELS` の int / str 型キー。

    rclpy は宣言値から param の型を固定する。`ray_count` を float で渡せば declare 時に
    型不一致で落ち、`reference_offset_m` を int で渡しても同じく落ちる——どちらも
    「overlay に正しい数を書いたのに起動しない」という診断しにくい失敗になる。
    型の正本は node 側 1 か所だけ、をここで強制する。
    """
    tree = _launch_tree()
    sentinels = _declared_sentinels()
    expected_int = {k for k, v in sentinels.items() if type(v) is int}
    expected_str = {k for k, v in sentinels.items() if type(v) is str}

    assert set(_module_constant(tree, "_TERRAIN_INT_KEYS")) == expected_int
    assert set(_module_constant(tree, "_TERRAIN_STR_KEYS")) == expected_str
    # 残りは double。3 つの集合で PARAM_KEYS を過不足なく覆う（float 表を別に持たない）。
    assert expected_int | expected_str <= set(PARAM_KEYS)
    assert len(set(PARAM_KEYS) - expected_int - expected_str) == len(
        {k for k, v in sentinels.items() if type(v) is float}
    )

    code = _executable_code(_function(tree, "_terrain_group"))
    assert "int(" in code and "float(" in code and "str(" in code, "型変換が欠けている"


# ──────────────── 0 actuation（配線側でも走行系に触れない） ────────────────
def test_the_terrain_wiring_is_not_a_velocity_or_stop_producer() -> None:
    """04:930 レイヤ注記を配線側で二重化する。

    node 本体の 0 actuation は `test_terrain_node_pins.py` が pin するが、launch が
    remap で `cliff_scan` を `cmd_vel` 系や `stop_request` へ向ければ同じ事故が起きる。
    """
    tree = _launch_tree()
    for fn_name in ("_terrain_config", "_terrain_group"):
        code = _executable_code(_function(tree, fn_name))
        for token in SPEC_ACTUATION_TOKENS:
            assert token not in code, f"{fn_name}() が {token} に触れている"
        assert "remappings" not in code, f"{fn_name}() が remap を持つ"


# ──────────────── base config（値を持たない・既定 OFF） ────────────────
def _terrain_block() -> dict:
    block = yaml.safe_load(_BASE_CONFIG.read_text(encoding="utf-8")).get("perception")
    assert isinstance(block, dict), "perception ブロックが base config に無い"
    terrain = block.get("terrain")
    assert isinstance(terrain, dict), "perception.terrain ブロックが base config に無い"
    return terrain


def test_base_config_terrain_is_off_and_carries_no_measured_value() -> None:
    """base は `enabled: false` **のみ**。実値は env overlay（environments.md）。

    base に 1 つでも実値が入ると、それは「全環境で共通に正しい幾何」の主張になる
    （カメラは未購入・車体は未組立＝04:992）。dev の Gazebo に depth camera は無い。
    """
    terrain = _terrain_block()
    assert terrain["enabled"] is False
    assert set(terrain) == {SPEC_GATE_KEY}, f"base に値が入っている: {sorted(terrain)}"


def test_the_commented_placeholders_cover_the_whole_parameter_surface() -> None:
    """コメントアウトの placeholder が 23 キーを**漏れなく**列挙している。

    このブロックは「true にする前に何を overlay で与えるか」の唯一の手引き。node に
    param が増えた（`PARAM_KEYS` が伸びた）のに手引きが古いままだと、運用者は
    揃えたつもりで起動 abort に当たる。手引きの網羅性を機械で保つ。
    """
    text = _BASE_CONFIG.read_text(encoding="utf-8")
    block = text[text.index("\nperception:") :]
    placeholders = set(re.findall(r"^\s+#\s*([a-z_]+):\s*<", block, flags=re.MULTILINE))
    assert placeholders == {*SPEC_TOPIC_KEYS, *PARAM_KEYS}, placeholders ^ {
        *SPEC_TOPIC_KEYS,
        *PARAM_KEYS,
    }
    # placeholder は必ずコメントのまま（`<…>` は YAML では読めないので、外れた瞬間に
    # load_config 全体が壊れる）。上の test が enabled 単独を assert して二重化。
    assert "<" not in yaml.safe_load(text)["perception"]["terrain"].__repr__()
