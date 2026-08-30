"""bringup 配線側の安全 unit（速度帯スライス 2）— 仕様のみから書いた独立オラクル。

`tests/unit/test_speed_band_publisher.py` が publisher の**内部**（クランプ・fail-closed
検証・0 cmd_vel）を pin するのに対し、本ファイルは publisher を Nav2 スタックへ**つなぐ側**
——launch の配線・`nav2_params.yaml`・`config/warehouse.base.yaml`——を pin する。

仕様の出典（すべて実 Read）:

- docs/adr/0012-speed-band-no-l2-best-effort.md:20 決定 3 — ①は launch が MPPI
  `FollowPath.vx_max` へ注入するのと **同一の解決値**（LOWER-only CLI override 込み）を
  同一 launch から publisher の param として渡す＝**真実の源を 2 つにしない**。
- 同 :21 決定 4 — 帯テーブルは config 注入＋起動時 fail-closed 検証（有限・`V_FLOOR`
  以上・①以下・単調）。`0.0` は Nav2 の `NO_SPEED_LIMIT` ゆえ計算経路に流さない。
- 同 :22 決定 5 — 20Hz 周期送出＋帯遷移時即時。`reset_period` は `nav2_params.yaml` で
  **明示設定する**（無活動 > reset_period で MPPI が帯を①へ戻すため）。
- 同 :28 決定 11 — `/bot{n}/speed_limit` の publisher は帯ノード 1 本に限定。costmap
  `filters` に SpeedFilter を入れない・`nav2_route` AdjustSpeedLimit を使わない
  （Nav2 は last-writer-wins で調停しないため、第 2 の publisher は黙って競合する）。
- docs/mode-m1/04-runtime-speed-limiter.md:128 — 帯の実値は config 注入・**コード定数禁止**。
  実値は OQ-T1（最速段＝S-SPEED 実測）/ OQ-T2（最遅段・安定段・保持窓）が未決。
- .claude/rules/safety.md:7（独立オラクル + mutation）。

期待値はすべて仕様値・手計算リテラル。cap は凍結契約から import する（0.3 の再ハードコード
はしない）。launch は `nav2_common` / `launch_ros` を import するため**実行せず AST で読む**
（host の pure-CI でも動く＝`tests/unit/test_bringup_launch.py` が importorskip で skip する
経路の穴を埋める）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY

pytestmark = [pytest.mark.unit, pytest.mark.safety]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LAUNCH = _REPO_ROOT / "ws/src/warehouse_bringup/launch/nav2_bringup.launch.py"
_NAV2_PARAMS = _REPO_ROOT / "ws/src/warehouse_bringup/config/nav2_params.yaml"
_BASE_CONFIG = _REPO_ROOT / "config/warehouse.base.yaml"

# 仕様値（ADR-0012:21 の例示 V_FLOOR）。実装から import せずテスト側にリテラルで置く。
SPEC_V_FLOOR_MPS = 0.05
# 帯テーブルの config キー（node の param 名と 1:1・doc04 追補③）。
BAND_VALUE_KEYS = ("band_slowest_mps", "band_stable_mps", "band_fastest_mps")


def _launch_tree() -> ast.Module:
    return ast.parse(_LAUNCH.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() が launch に無い")


def _is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)


def _dict_entry(scope: ast.AST, key: str) -> ast.expr:
    """`scope` 配下の dict リテラルから `key` に対応する値ノードを 1 つだけ取り出す。"""
    hits = [
        value
        for node in ast.walk(scope)
        if isinstance(node, ast.Dict)
        for literal, value in zip(node.keys, node.values, strict=True)
        if isinstance(literal, ast.Constant) and literal.value == key
    ]
    assert len(hits) == 1, f"キー {key!r} の出現が 1 回ではない（{len(hits)} 回）"
    return hits[0]


# ─────────────────── 決定 3: ①の真実の源が 1 つであること（配線の要） ───────────────────
def test_band_cap_and_mppi_vx_max_come_from_the_same_resolved_value() -> None:
    """publisher の `operating_vx_max` と MPPI `vx_max` が**同一の解決値**を受ける。

    ADR-0012 決定 3 の構造的担保。天井を「config 運用値」から独立に読み直す実装
    （`_operating_vx_max()` を publisher 側でもう一度呼ぶ等）は、`max_linear_velocity:=0.1`
    のような LOWER-only override 構成で帯が①を超える経路を作る＝ここで赤くなる。
    """
    tree = _launch_tree()

    # MPPI 側: RewrittenYaml param_rewrites の "vx_max" は launch ローカルの vx_max。
    mppi = _dict_entry(_function(tree, "_per_robot_group"), "vx_max")
    assert isinstance(mppi, ast.Name) and mppi.id == "vx_max", ast.dump(mppi)

    # publisher 側: ParameterValue(vx_max, value_type=float) — 同じ名前を渡す。
    band = _dict_entry(_function(tree, "_speed_band_group"), "operating_vx_max")
    assert isinstance(band, ast.Call), ast.dump(band)
    assert isinstance(band.func, ast.Name) and band.func.id == "ParameterValue"
    assert band.args and isinstance(band.args[0], ast.Name)
    assert band.args[0].id == mppi.id, "publisher が MPPI と別の値を受けている"
    # str で届くと double 宣言の param が起動時に落ちるため型指定は必須。
    types = [kw.value for kw in band.keywords if kw.arg == "value_type"]
    assert len(types) == 1
    assert isinstance(types[0], ast.Name) and types[0].id == "float"


def test_the_single_vx_max_binding_is_the_clamped_expression() -> None:
    """`vx_max` は生の launch arg ではなくクランプ式に 1 度だけ束縛される。

    これが無いと決定 3 は「同じ名前を渡している」だけになり、その名前が
    `LaunchConfiguration("max_linear_velocity")` 生値なら契約上限クランプを失う。
    """
    gen = _function(_launch_tree(), "generate_launch_description")
    bindings = [
        node
        for node in ast.walk(gen)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "vx_max" for t in node.targets)
    ]
    assert len(bindings) == 1
    value = bindings[0].value
    assert isinstance(value, ast.Call)
    assert isinstance(value.func, ast.Name) and value.func.id == "PythonExpression"
    expression = ast.dump(value)
    assert "min" in expression and "MAX_LINEAR_VELOCITY" in expression

    # 同一の vx_max が Nav2 群と帯 publisher の両方へ渡る。
    for callee in ("_per_robot_group", "_speed_band_group"):
        calls = [
            node
            for node in ast.walk(gen)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == callee
        ]
        assert len(calls) == 1, f"{callee}() の呼び出しが 1 回ではない"
        passed = {arg.id for arg in calls[0].args if isinstance(arg, ast.Name)}
        assert "vx_max" in passed, f"{callee}() に vx_max が渡っていない"


def test_band_publisher_is_not_wired_as_a_velocity_producer() -> None:
    """配線側も `cmd_vel` に触れない（決定 7 ③を launch 側で二重化）。

    node 本体の 0 cmd_vel は publisher 側 unit が pin するが、launch が remap で
    `speed_limit` を `cmd_vel` 系へ向けてしまえば同じ事故が起きる。
    """
    tree = _launch_tree()
    group = _function(tree, "_speed_band_group")
    # docstring / コメントの散文ではなく **コードだけ**を見る（散文に cmd_vel と書いた
    # だけで赤くなる/緑になる脆いテキスト検査を避ける）。
    body = [node for node in group.body if not _is_docstring(node)]
    code = "\n".join(ast.unparse(node) for node in body)
    assert "cmd_vel" not in code
    assert "remappings" not in code
    # 起動するのは #569 の実体 1 本だけ（決定 11 の単一 publisher 規律）。
    assert _dict_entry(group, "enabled") is not None
    packages = [
        kw.value.value
        for node in ast.walk(group)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Node"
        for kw in node.keywords
        if kw.arg == "package" and isinstance(kw.value, ast.Constant)
    ]
    assert packages == ["warehouse_perception"]


# ───────────────────── 決定 11: 第 2 の speed_limit 源を config で作らない ─────────────────────
def test_nav2_params_declare_no_second_speed_limit_source() -> None:
    """Nav2 は SpeedLimit を調停しない（last-writer-wins）＝第 2 の publisher を足さない。

    costmap の SpeedFilter（`filters:`）や `nav2_route` の AdjustSpeedLimit を有効化すると
    帯 publisher と黙って競合し、どちらが勝つかは到着順で決まる（ADR-0012:28 決定 11）。
    起動時アサートの静的な半分。
    """
    params = _NAV2_PARAMS.read_text(encoding="utf-8")
    assert "SpeedFilter" not in params
    assert "nav2_route" not in params
    assert "filters:" not in params


def test_mppi_reset_period_is_explicit() -> None:
    """`reset_period` を明示設定する（ADR-0012:22 決定 5）。

    無活動が `reset_period` を超えると Humble MPPI の `Optimizer::reset()` が
    `base_constraints` へ戻す＝**帯が黙って①（速い側）へ消える**。周期送出の周期が
    この値より十分短いことを読み手が検算できるよう、暗黙の upstream 既定に任せない。
    """
    params = yaml.safe_load(_NAV2_PARAMS.read_text(encoding="utf-8"))
    follow_path = params["controller_server"]["ros__parameters"]["FollowPath"]
    assert "reset_period" in follow_path, "FollowPath.reset_period が暗黙の upstream 既定のまま"
    reset_period = follow_path["reset_period"]
    assert isinstance(reset_period, float)
    assert reset_period > 0.0
    # 20Hz 送出（決定 5）が reset 窓の内側に十分収まること。手計算: 1/20 = 0.05s。
    assert reset_period > 0.05


# ─────────────────── 決定 4: config 側テーブルの健全性（帯値を発明しない） ───────────────────
def _speed_bands() -> dict:
    block = yaml.safe_load(_BASE_CONFIG.read_text(encoding="utf-8")).get("speed_bands")
    assert isinstance(block, dict), "speed_bands ブロックが base config に無い"
    return block


def test_speed_bands_default_to_safe_off() -> None:
    """既定は OFF＝未設定なら従来挙動（①起動基準値のみ）に落ちる（doc09:406 additive/safe-OFF）。"""
    block = _speed_bands()
    assert block["enabled"] is False


def test_committed_band_table_is_complete_and_inside_the_envelope() -> None:
    """base config に帯値を置くなら、**揃っていて・単調で・契約上限以下**であること。

    実値は OQ-T1 / OQ-T2 待ちで現在は未設定（コメントアウトのプレースホルダ）。この
    テストは「値が入った瞬間」に効く: 半分だけ埋めた表・逆順の表・契約超えの値・`0.0`
    （= Nav2 の制限なしセンチネル）を CI で落とす。ロボット起動時の fail-closed 検証
    （ADR-0012 決定 4）と同じ不変条件を、期待値リテラル側から二重化する。
    """
    block = _speed_bands()
    present = [key for key in BAND_VALUE_KEYS if key in block]
    if not present:
        assert block["enabled"] is False, "帯値なしで enabled=true は起動時に落ちる"
        return

    assert present == list(BAND_VALUE_KEYS), f"帯テーブルが不完全: {present}"
    floor = float(block.get("v_floor_mps", SPEC_V_FLOOR_MPS))
    assert floor > 0.0
    values = [float(block[key]) for key in BAND_VALUE_KEYS]
    for key, value in zip(BAND_VALUE_KEYS, values, strict=True):
        assert value >= floor, f"{key}={value} が v_floor={floor} 未満"
        assert value <= MAX_LINEAR_VELOCITY, f"{key}={value} が凍結契約上限を超える"
    assert values == sorted(values), f"帯が単調でない（最遅 <= 安定 <= 最速）: {values}"
    if block["enabled"] is True:
        assert "hold_timeout_s" in block, "enabled=true には T-5 保持窓が要る"
        assert float(block["hold_timeout_s"]) > 0.0
