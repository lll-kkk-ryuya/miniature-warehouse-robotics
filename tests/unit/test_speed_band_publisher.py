"""独立オラクルによる speed band publisher（runtime speed limiter ②）の安全 unit（R-26）。

このファイルは **仕様のみ**から書かれている。仕様の出典:

- docs/adr/0012-speed-band-no-l2-best-effort.md 決定 3（クランプ =
  ``min(帯値, ①, MAX_LINEAR_VELOCITY)``）・決定 4（起動時 fail-closed 検証:
  有限・v_floor 以上・①以下・単調）・決定 7（R-26 unit 6 本）・決定 8
  （``percentage = false``）
- docs/mode-m1/04-runtime-speed-limiter.md §3-2（``0.0`` は「制限なし」ゆえ
  計算経路に流さない）・§4 制約 1（同じ不変条件の二重化）
- docs/mode-x-er/09-hand-raise-summon.md T-5 :392-397（未検出時: 保持窓 →
  タイムアウトで安定段復帰・起動既定は安定段）

期待値は手計算リテラル。cap は凍結契約
``warehouse_interfaces.safety.MAX_LINEAR_VELOCITY`` から import し、0.3 の
ハードコードは契約 pin の 1 本のみ（tests/unit/test_m1_clamp.py と同じ流儀）。
node モジュールは rclpy に依存するため import せず、決定 7 ③（cmd_vel 非
publish）は AST 検査で pin する（L2-G8 / #565 AST floor guard と同系）。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY
from warehouse_perception.speed_band_core import (
    BandConfigError,
    BandHold,
    BandTable,
    compute_speed_limit,
    parse_band_event,
    validate_band_table,
)

pytestmark = pytest.mark.safety

_NODE_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "ws"
    / "src"
    / "warehouse_perception"
    / "warehouse_perception"
    / "speed_band_node.py"
)


def _valid_table() -> BandTable:
    return validate_band_table(0.08, 0.15, 0.26, operating_vx_max=0.26, v_floor=0.05)


# ---------------------------------------------------------------- ① クランプ必経
def test_compute_returns_band_value_inside_envelope() -> None:
    table = _valid_table()
    assert compute_speed_limit(table, "slowest") == pytest.approx(0.08)
    assert compute_speed_limit(table, "stable") == pytest.approx(0.15)
    assert compute_speed_limit(table, "fastest") == pytest.approx(0.26)


def test_compute_clamps_to_operating_vx_max_even_if_table_bypassed_validation() -> None:
    # 二重化の実体: validation を経ない直構築 table でも min() が①で頭打ちにする
    # （doc04 §4 制約 1）。min→max / min 削除の mutation はここで赤くなる。
    table = BandTable(slowest=0.08, stable=0.15, fastest=0.9, operating_vx_max=0.26, v_floor=0.05)
    assert compute_speed_limit(table, "fastest") == pytest.approx(0.26)


def test_compute_clamps_to_frozen_cap_contract_pin() -> None:
    # 契約 pin（このファイルで 0.3 をハードコードする唯一の行）。
    table = BandTable(slowest=0.08, stable=0.15, fastest=0.9, operating_vx_max=0.9, v_floor=0.05)
    assert compute_speed_limit(table, "fastest") == pytest.approx(0.3)
    assert pytest.approx(0.3) == MAX_LINEAR_VELOCITY


# ------------------------------------------------- ② 0.0 / 非有限 / 負 / floor 未満
@pytest.mark.parametrize(
    "bands",
    [
        (0.0, 0.15, 0.26),  # 0.0 = Nav2 の「制限なし」センチネル → 計算経路に流さない
        (float("nan"), 0.15, 0.26),
        (0.08, float("inf"), 0.26),
        (0.08, 0.15, -0.1),
        (0.04, 0.15, 0.26),  # v_floor=0.05 未満
    ],
)
def test_validation_rejects_unpublishable_band_values(bands) -> None:
    with pytest.raises(BandConfigError):
        validate_band_table(*bands, operating_vx_max=0.26, v_floor=0.05)


def test_compute_never_returns_zero_or_nonfinite() -> None:
    # validation を迂回した壊れ table でも 0.0 / NaN を publish 値として返さない。
    broken = BandTable(
        slowest=0.0, stable=float("nan"), fastest=0.26, operating_vx_max=0.26, v_floor=0.05
    )
    with pytest.raises(BandConfigError):
        compute_speed_limit(broken, "slowest")
    with pytest.raises(BandConfigError):
        compute_speed_limit(broken, "stable")


# ------------------------------------------------------------ ③ cmd_vel 非 publish
def test_node_publishes_only_speed_limit_topic() -> None:
    source = _NODE_SOURCE.read_text(encoding="utf-8")
    assert "cmd_vel" not in source  # velocity producer 化の禁止（ADR-0012 決定 7 ③）
    tree = ast.parse(source)
    publisher_topics = [
        node.args[1].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_publisher"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
    ]
    assert publisher_topics == ["speed_limit"]  # 相対名 1 本のみ（決定 11）


# ------------------------------------------------------- ④ 単調性・⑤ ①超え・⑥ 除算安全
def test_validation_rejects_non_monotonic_tables() -> None:
    with pytest.raises(BandConfigError):
        validate_band_table(0.20, 0.15, 0.26, operating_vx_max=0.26, v_floor=0.05)
    with pytest.raises(BandConfigError):
        validate_band_table(0.08, 0.26, 0.15, operating_vx_max=0.26, v_floor=0.05)


def test_validation_rejects_band_above_operating_vx_max() -> None:
    # launch 明示縮退（max_linear_velocity:=0.1 等）で①が config 期の帯値より
    # 低い run では、帯機構は publish せず起動拒否する（ADR-0012 決定 4 =
    # 検証 REF-1 の封じ込め。①超えの値がワイヤに乗る経路を残さない）。
    with pytest.raises(BandConfigError):
        validate_band_table(0.08, 0.10, 0.26, operating_vx_max=0.10, v_floor=0.05)


@pytest.mark.parametrize(
    "vx_max",
    [0.0, -0.3, float("nan"), float("inf"), MAX_LINEAR_VELOCITY * 1.01],
)
def test_validation_rejects_bad_operating_vx_max(vx_max) -> None:
    # ① は Nav2 側で ratio = speed_limit / base_vx_max の分母になる（doc04
    # §2-2）。0 / 負 / 非有限 / 契約超えは起動段階で落とす（決定 7 ⑥）。
    with pytest.raises(BandConfigError):
        validate_band_table(0.08, 0.15, 0.26, operating_vx_max=vx_max, v_floor=0.05)


# ------------------------------------------------------------------- T-5 / parse
def test_band_hold_boots_on_stable_and_times_out_to_stable() -> None:
    hold = BandHold(hold_timeout_s=2.0)
    assert hold.current(now=100.0) == "stable"  # 起動既定 = 安定段（T-5）
    assert hold.on_band("fastest", now=100.0) is True
    assert hold.current(now=101.9) == "fastest"  # 保持窓の内側はホールド
    assert hold.on_band("fastest", now=101.0) is False  # 実効帯と同じ再確定 → 遷移なし
    assert hold.current(now=102.9) == "fastest"  # 再確定（101.0）起点で保持延長
    assert hold.current(now=103.5) == "stable"  # タイムアウト → 安定段復帰
    # 失効後の再確定は「実効帯 stable → fastest」の遷移＝即時 publish 対象
    # （ADR-0012 決定 5 の「帯遷移」は実効帯の遷移であって保持値の差分ではない）。
    assert hold.on_band("fastest", now=104.0) is True
    assert hold.on_band("garbage", now=104.0) is False  # 未知の帯は無視（fail-closed）
    assert hold.current(now=104.5) == "fastest"


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        json.dumps(["speed_band"]),
        json.dumps({"event": "summon", "band": "fastest"}),
        json.dumps({"event": "speed_band", "band": "warp"}),
        json.dumps({"band": "stable"}),
    ],
)
def test_parse_band_event_fail_closed(payload) -> None:
    assert parse_band_event(payload) is None


def test_parse_band_event_accepts_documented_form() -> None:
    payload = json.dumps({"event": "speed_band", "band": "slowest"})
    assert parse_band_event(payload) == "slowest"
