"""独立オラクルによる 07_Output_Adapters node の marshalling 核（R-26 安全 unit）。

期待値はすべて**仕様から手計算したリテラル**で、実装を呼んで作らない。仕様の出典:

- `docs/mode-outdoor/04-perception-sidewalk-and-signals.md:174`（無効深度 = NaN /
  inf / 0 / 空点群 → 「品質不成立」。**例外を上げない**）
- 同 `:173`（`UNKNOWN` は LaserScan に落とさない＝非 DROP は `inf` のまま）
- 同 `:177`（変換後も**元の計測時刻**を維持）
- 同 `:176`（距離の datum = 車輪接地点・footprint ＝ `reference` の対象）
- 同 追補 ⑤ §2（[:526](../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md)）
  ＋ 追補 ⑤-1 §1（[:907](../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md)）
  ＝ パラメータは**全部注入・既定なし**・違反は構築時 `ValueError`
- 追補 ⑨（本 PR・`OQ-OD4Y-i` 裁定 6〜8: 対応 encoding 2 種・`pixel_stride` と
  intrinsics の同率縮小・sentinel で起動時 fail-closed）

深度サンプルの合成は `struct.pack` で行い、`16UC1` の mm → m 係数・エンディアン・
stride の intrinsics 縮小は**テスト側で独立に計算**する（doc20 §9 独立オラクル）。
node モジュール（rclpy 依存）は import せず、AST pin は
`tests/unit/test_terrain_node_pins.py` が持つ。
"""

from __future__ import annotations

import math
import struct

import pytest
from warehouse_interfaces.perception import TerrainCoverage, TerrainState
from warehouse_perception.terrain_core import CliffScan, analyze_depth_frame
from warehouse_perception.terrain_node_core import (
    DEPTH_ENCODINGS,
    PARAM_KEYS,
    coverage_json,
    decode_depth_image,
    intrinsics_from_camera_info,
    laser_scan_fields,
    params_from_mapping,
    processing_latency_s,
    resolve_frame_id,
    stamp_to_seconds,
)

pytestmark = [pytest.mark.unit, pytest.mark.safety]


# ── 注入パラメータ（テストが caller として渡す値。既定ではない） ─────────────
def _valid_params() -> dict[str, object]:
    """検証を通る最小の注入セット。値は任意＝「既定」ではなく fixture。"""
    return {
        "camera_height_m": 0.30,
        "pitch_down_rad": 0.2618,
        "reference_offset_m": 0.10,
        "cell_size_m": 0.10,
        "corridor_half_width_m": 0.25,
        "forward_range_m": 2.00,
        "min_points_per_cell": 3,
        "drop_threshold_m": 0.05,
        "min_valid_fraction": 0.30,
        "plane_tolerance_m": 0.02,
        "ransac_iterations": 20,
        "ransac_seed": 7,
        "max_plane_tilt_rad": 0.10,
        "max_plane_offset_m": 0.05,
        "angle_min_rad": -0.50,
        "angle_max_rad": 0.50,
        "ray_count": 20,
        "range_min_m": 0.10,
        "range_max_m": 3.00,
        "reference": "wheel_contact",
        "pixel_stride": 1,
    }


def _pack_16uc1(rows_mm: list[list[int]], *, big_endian: bool) -> bytes:
    prefix = ">" if big_endian else "<"
    return b"".join(struct.pack(f"{prefix}{len(r)}H", *r) for r in rows_mm)


def _pack_32fc1(rows_m: list[list[float]], *, big_endian: bool) -> bytes:
    prefix = ">" if big_endian else "<"
    return b"".join(struct.pack(f"{prefix}{len(r)}f", *r) for r in rows_m)


# ── 裁定 6: encoding 2 種と mm → m ────────────────────────────────────────
def test_16uc1_is_millimetres_converted_to_metres() -> None:
    """独立オラクル: 仕様「16UC1 は mm」より期待値は raw / 1000 の手計算リテラル。"""
    data = _pack_16uc1([[1000, 2500], [0, 65535]], big_endian=False)
    rows = decode_depth_image(
        encoding="16UC1",
        width=2,
        height=2,
        step=4,
        is_bigendian=0,
        data=data,
    )
    assert rows == [
        [pytest.approx(1.0), pytest.approx(2.5)],
        [pytest.approx(0.0), pytest.approx(65.535)],
    ]


def test_32fc1_is_metres_and_passes_samples_through_unclassified() -> None:
    """`0` / `NaN` / `inf` は**そのまま**返る（分類は `depth_validity` の仕事 = :174）。"""
    data = _pack_32fc1([[1.5, 0.0], [float("nan"), float("inf")]], big_endian=False)
    rows = decode_depth_image(
        encoding="32FC1", width=2, height=2, step=8, is_bigendian=0, data=data
    )
    assert rows[0] == [pytest.approx(1.5), pytest.approx(0.0)]
    assert math.isnan(rows[1][0])
    assert math.isinf(rows[1][1])


def test_byte_order_flag_is_honoured_and_is_not_a_no_op() -> None:
    """big-endian バッファを little として読むと**別の値**になる（endian mutation の的）。"""
    big = _pack_16uc1([[1000, 2500]], big_endian=True)
    correct = decode_depth_image(
        encoding="16UC1", width=2, height=1, step=4, is_bigendian=1, data=big
    )
    assert correct == [[pytest.approx(1.0), pytest.approx(2.5)]]
    # 1000 = 0x03E8 -> big-endian bytes 03 E8 -> little-endian read 0xE803 = 59395
    wrong = decode_depth_image(
        encoding="16UC1", width=2, height=1, step=4, is_bigendian=0, data=big
    )
    # 2500 = 0x09C4 -> big-endian bytes 09 C4 -> little-endian read 0xC409 = 50185
    assert wrong == [[pytest.approx(59.395), pytest.approx(50.185)]]


def test_only_two_encodings_are_supported() -> None:
    assert set(DEPTH_ENCODINGS) == {"16UC1", "32FC1"}


# ── 裁定 6: 壊れた frame は「空 rows」であって例外ではない ────────────────
@pytest.mark.parametrize(
    ("kwargs", "why"),
    [
        ({"encoding": "mono8"}, "未対応 encoding"),
        ({"encoding": "32FC1"}, "step が 32FC1 の 1 行を保持できない"),
        ({"step": 3}, "step < width * 2"),
        ({"data": b"\x01\x02"}, "バッファ不足"),
        ({"width": 0}, "幅 0"),
        ({"height": 0}, "高さ 0"),
        ({"step": 0}, "step 0"),
        ({"pixel_stride": 0}, "stride 0"),
    ],
)
def test_undecodable_frames_yield_no_rows_without_raising(kwargs: dict, why: str) -> None:
    base = {
        "encoding": "16UC1",
        "width": 2,
        "height": 2,
        "step": 4,
        "is_bigendian": 0,
        "data": _pack_16uc1([[1000, 2000], [3000, 4000]], big_endian=False),
        "pixel_stride": 1,
    }
    assert decode_depth_image(**{**base, **kwargs}) == [], why


def test_an_empty_frame_is_an_observation_not_an_error() -> None:
    """空 rows → `valid_fraction = 0.0` / 全 UNKNOWN / 全 inf（:173 / :174）。"""
    params = params_from_mapping(_valid_params())
    intrinsics = intrinsics_from_camera_info([200.0, 0.0, 160.0, 0.0, 200.0, 120.0, 0.0, 0.0, 1.0])
    assert intrinsics is not None
    observation = analyze_depth_frame(
        [],
        params=params.terrain_params(intrinsics),
        scan=params.scan,
        source_stamp_s=12.5,
        reference=params.reference,
        device_frame_seq=None,
        processing_latency_s=None,
    )
    assert observation.coverage.state is TerrainState.UNKNOWN
    assert observation.coverage.quality.valid_fraction == pytest.approx(0.0)
    assert all(math.isinf(r) for r in observation.cliff.ranges)


def test_row_padding_beyond_the_pixels_is_skipped() -> None:
    """`step > width * 2`（行末パディング）は正当で、画素だけを読む。"""
    padded = (
        struct.pack("<2H", 1000, 2000) + b"\xff\xff" + struct.pack("<2H", 3000, 4000) + b"\x00\x00"
    )
    rows = decode_depth_image(
        encoding="16UC1", width=2, height=2, step=6, is_bigendian=0, data=padded
    )
    assert rows == [
        [pytest.approx(1.0), pytest.approx(2.0)],
        [pytest.approx(3.0), pytest.approx(4.0)],
    ]


# ── 裁定 7: pixel_stride と intrinsics の同率縮小 ─────────────────────────
def test_pixel_stride_keeps_every_nth_row_and_column_from_index_zero() -> None:
    rows_mm = [
        [1000, 1100, 1200, 1300],
        [2000, 2100, 2200, 2300],
        [3000, 3100, 3200, 3300],
        [4000, 4100, 4200, 4300],
    ]
    rows = decode_depth_image(
        encoding="16UC1",
        width=4,
        height=4,
        step=8,
        is_bigendian=0,
        data=_pack_16uc1(rows_mm, big_endian=False),
        pixel_stride=2,
    )
    # 行 0,2 / 列 0,2 を手で選んだ期待値（実装を呼ばずに書き下す）。
    assert rows == [
        [pytest.approx(1.0), pytest.approx(1.2)],
        [pytest.approx(3.0), pytest.approx(3.2)],
    ]


def test_intrinsics_shrink_by_exactly_the_same_factor_as_the_pixels() -> None:
    """`fx, fy, cx, cy` を **すべて** 1/stride にする（pinhole は線形＝数学的に等価）。"""
    k = [400.0, 0.0, 320.0, 0.0, 380.0, 240.0, 0.0, 0.0, 1.0]
    full = intrinsics_from_camera_info(k)
    half = intrinsics_from_camera_info(k, pixel_stride=2)
    assert full is not None and half is not None
    assert (full.fx, full.fy, full.cx, full.cy) == (400.0, 380.0, 320.0, 240.0)
    assert (half.fx, half.fy, half.cx, half.cy) == (200.0, 190.0, 160.0, 120.0)


def test_subsampling_keeps_the_back_projected_ray_identical() -> None:
    """等価性の直接検査: 元画素 `u` と間引き後 `u/s` が同じ正規化座標を与える。

    `(u − cx) / fx` と `(u/s − cx/s) / (fx/s)` の一致。片方だけ縮小する mutation
    （例 `cx` を縮めない）はここで赤くなる。
    """
    k = [400.0, 0.0, 320.0, 0.0, 380.0, 240.0, 0.0, 0.0, 1.0]
    full = intrinsics_from_camera_info(k)
    half = intrinsics_from_camera_info(k, pixel_stride=2)
    assert full is not None and half is not None
    for u in (0, 64, 320, 640):
        assert (u - full.cx) / full.fx == pytest.approx((u / 2 - half.cx) / half.fx)
    for v in (0, 48, 240, 480):
        assert (v - full.cy) / full.fy == pytest.approx((v / 2 - half.cy) / half.fy)


@pytest.mark.parametrize(
    ("k", "why"),
    [
        ([400.0, 0.0, 320.0, 0.0, 380.0, 240.0, 0.0, 0.0], "9 要素でない"),
        ([0.0] * 9, "未校正（fx = 0）"),
        ([400.0, 0.0, 320.0, 0.0, 0.0, 240.0, 0.0, 0.0, 1.0], "fy = 0"),
        ([-400.0, 0.0, 320.0, 0.0, 380.0, 240.0, 0.0, 0.0, 1.0], "fx < 0"),
        ([400.0, 0.0, -1.0, 0.0, 380.0, 240.0, 0.0, 0.0, 1.0], "cx < 0"),
        ([float("nan"), 0.0, 320.0, 0.0, 380.0, 240.0, 0.0, 0.0, 1.0], "NaN"),
        ([float("inf"), 0.0, 320.0, 0.0, 380.0, 240.0, 0.0, 0.0, 1.0], "inf"),
        (["400", 0.0, 320.0, 0.0, 380.0, 240.0, 0.0, 0.0, 1.0], "非数値"),
    ],
)
def test_unusable_camera_info_yields_none_never_a_guess(k: list, why: str) -> None:
    assert intrinsics_from_camera_info(k) is None, why


def test_camera_info_refuses_a_stride_below_one() -> None:
    k = [400.0, 0.0, 320.0, 0.0, 380.0, 240.0, 0.0, 0.0, 1.0]
    assert intrinsics_from_camera_info(k, pixel_stride=0) is None


# ── 裁定 8: sentinel は必ず起動時に落ちる ────────────────────────────────
def test_the_full_injected_set_builds() -> None:
    params = params_from_mapping(_valid_params())
    assert params.reference == "wheel_contact"
    assert params.pixel_stride == 1
    assert params.grid.forward_cell_count == 20


# node が宣言する既定値（`terrain_node._SENTINELS` と同じ表を、テスト側が
# **仕様から独立に**書き下したもの。AST pin 側で 2 つの表の一致を検査する）。
_DECLARED_SENTINELS: dict[str, object] = {name: 0.0 for name in PARAM_KEYS}
_DECLARED_SENTINELS.update(
    {
        "min_points_per_cell": 0,
        "ransac_iterations": 0,
        "ransac_seed": 0,
        "ray_count": 0,
        "pixel_stride": 0,
        "reference": "",
    }
)

# **sentinel が正当な値でもある 4 本**（隠さず列挙する＝`OQ-OD4Y-i2`・追補 ⑨）。
# `0` に物理的な意味があるため、単体では検証で落とせない:
#   - `reference_offset_m` — datum がカメラ原点に一致する取付は有り得る
#     （符号を拘束しないのが `MountingGeometry` の明示的な設計）
#   - `angle_min_rad` / `angle_max_rad` — `0` は正当な方位境界。ただし**両方**が
#     `0` なら `angle_max > angle_min` が破れて落ちる＝対として fail-closed
#   - `ransac_seed` — どの int も seed として合法（追補 ⑤ §2 は再現性のために
#     「既定なし」とだけ言う）。同じ群の `ransac_iterations = 0` が落とす
# 全体としての fail-closed は保たれる（下の「何も注入しない起動」テスト）。
_SENTINEL_IS_A_LEGAL_VALUE = frozenset(
    {"reference_offset_m", "angle_min_rad", "angle_max_rad", "ransac_seed"}
)


@pytest.mark.parametrize("name", sorted(set(PARAM_KEYS) - _SENTINEL_IS_A_LEGAL_VALUE))
def test_every_parameter_left_at_its_declared_sentinel_aborts(name: str) -> None:
    """node の宣言既定（`0.0` / `0` / `""`）は**必ず**検証で落ちる。

    1 本でも「0 のまま通る」パラメータがあれば、それは発明された既定値になる。
    """
    values = _valid_params()
    values[name] = _DECLARED_SENTINELS[name]
    with pytest.raises(ValueError):
        params_from_mapping(values)


@pytest.mark.parametrize("name", sorted(_SENTINEL_IS_A_LEGAL_VALUE))
def test_the_four_sentinels_that_are_legal_values_are_exactly_these(name: str) -> None:
    """既知の穴を**明示的に固定**する（黙って通るのではなく、記録された 4 件）。

    この 4 本だけは、他を全部注入したうえで sentinel のまま残すと起動が通る。
    増えたら赤くなる（上の網羅テストが対称に守る）。
    """
    values = _valid_params()
    values[name] = _DECLARED_SENTINELS[name]
    assert params_from_mapping(values) is not None


def test_the_bearing_window_sentinels_are_fail_closed_as_a_pair() -> None:
    """`angle_min` / `angle_max` は片方なら通るが、**両方**未注入なら落ちる。"""
    values = _valid_params()
    values["angle_min_rad"] = 0.0
    values["angle_max_rad"] = 0.0
    with pytest.raises(ValueError):
        params_from_mapping(values)


def test_the_seed_sentinel_is_covered_by_its_own_group() -> None:
    """`ransac_seed = 0` は通るが、同じ群の `ransac_iterations = 0` が起動を止める。"""
    values = _valid_params()
    values["ransac_seed"] = 0
    assert params_from_mapping(values).ground_fit.ransac_seed == 0
    values["ransac_iterations"] = 0
    with pytest.raises(ValueError):
        params_from_mapping(values)


def test_a_bare_declaration_with_no_injection_aborts() -> None:
    """裁定 8 の本体: 宣言既定だけで起動しようとすると必ず落ちる。"""
    with pytest.raises(ValueError):
        params_from_mapping(dict(_DECLARED_SENTINELS))


@pytest.mark.parametrize("name", sorted(PARAM_KEYS))
def test_every_parameter_is_required(name: str) -> None:
    values = _valid_params()
    del values[name]
    with pytest.raises(ValueError):
        params_from_mapping(values)


def test_the_floor_band_and_the_drop_band_must_stay_disjoint() -> None:
    """群をまたぐ唯一の相互検査は `TerrainParams` の 1 か所に残っている（:196）。"""
    values = _valid_params()
    values["drop_threshold_m"] = values["plane_tolerance_m"]
    params = params_from_mapping(values)  # 群ごとの検証は通る
    intrinsics = intrinsics_from_camera_info([200.0, 0.0, 160.0, 0.0, 200.0, 120.0, 0.0, 0.0, 1.0])
    assert intrinsics is not None
    with pytest.raises(ValueError):
        params.terrain_params(intrinsics)


def test_a_non_numeric_parameter_is_refused_rather_than_coerced() -> None:
    values = _valid_params()
    values["cell_size_m"] = "0.10"
    with pytest.raises(ValueError):
        params_from_mapping(values)


def test_a_bool_is_not_accepted_where_an_int_is_required() -> None:
    values = _valid_params()
    values["ray_count"] = True
    with pytest.raises(ValueError):
        params_from_mapping(values)


# ── 07 adapter: LaserScan フィールドと coverage JSON ──────────────────────
def _cliff(ranges: tuple[float, ...]) -> CliffScan:
    return CliffScan(
        ranges=ranges,
        angle_min_rad=-0.5,
        angle_max_rad=0.5,
        angle_increment_rad=0.25,
        range_min_m=0.1,
        range_max_m=3.0,
        source_stamp_s=12.5,
        drop_cell_count=1,
        omitted_cell_count=0,
    )


def test_laser_scan_fields_keep_every_non_drop_bearing_at_inf() -> None:
    """`UNKNOWN` を壁として書かない（:173）。inf → 0.0 に落とす mutation の的。"""
    inf = math.inf
    fields = laser_scan_fields(_cliff((inf, 1.25, inf, inf)))
    assert fields["ranges"] == [inf, pytest.approx(1.25), inf, inf]
    assert fields["angle_min"] == pytest.approx(-0.5)
    assert fields["angle_max"] == pytest.approx(0.5)
    assert fields["angle_increment"] == pytest.approx(0.25)
    assert fields["range_min"] == pytest.approx(0.1)
    assert fields["range_max"] == pytest.approx(3.0)


def test_laser_scan_fields_names_match_the_message_and_invent_no_sweep_period() -> None:
    fields = laser_scan_fields(_cliff((math.inf,)))
    assert set(fields) == {
        "angle_min",
        "angle_max",
        "angle_increment",
        "range_min",
        "range_max",
        "ranges",
    }
    assert "scan_time" not in fields and "time_increment" not in fields


def test_coverage_json_round_trips_through_the_frozen_contract() -> None:
    params = params_from_mapping(_valid_params())
    intrinsics = intrinsics_from_camera_info([200.0, 0.0, 160.0, 0.0, 200.0, 120.0, 0.0, 0.0, 1.0])
    assert intrinsics is not None
    observation = analyze_depth_frame(
        [],
        params=params.terrain_params(intrinsics),
        scan=params.scan,
        source_stamp_s=12.5,
        reference=params.reference,
        device_frame_seq=None,
        processing_latency_s=0.25,
    )
    payload = coverage_json(observation.coverage)
    restored = TerrainCoverage.model_validate_json(payload)
    assert restored.source_stamp_s == pytest.approx(12.5)
    assert restored.reference == "wheel_contact"
    assert restored.state is TerrainState.UNKNOWN
    assert restored.quality.processing_latency_s == pytest.approx(0.25)
    assert restored.quality.device_frame_seq is None


# ── :177 元計測時刻・遅延の自己申告 ───────────────────────────────────────
def test_stamp_seconds_is_the_original_measurement_time() -> None:
    assert stamp_to_seconds(1700000000, 500000000) == pytest.approx(1700000000.5)
    assert stamp_to_seconds(0, 0) == pytest.approx(0.0)


def test_processing_latency_is_now_minus_the_source_stamp() -> None:
    assert processing_latency_s(12.75, 12.5) == pytest.approx(0.25)
    assert processing_latency_s(12.5, 12.5) == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("now_s", "stamp_s"),
    [
        (12.0, 12.5),  # 未来 stamp = 時計取違え
        (float("nan"), 12.5),
        (12.5, float("inf")),
    ],
)
def test_a_latency_that_cannot_be_stated_is_none_not_a_negative_number(
    now_s: float, stamp_s: float
) -> None:
    """契約は非有限を拒否し、負の遅延に意味づけが無い → `None`（証拠の不在）。"""
    assert processing_latency_s(now_s, stamp_s) is None


# ── frame_id ─────────────────────────────────────────────────────────────
def test_frame_id_is_the_namespaced_base_link() -> None:
    assert resolve_frame_id("/bot1", "base_link") == "bot1/base_link"
    assert resolve_frame_id("/bot2/", "base_link") == "bot2/base_link"


@pytest.mark.parametrize("namespace", ["/", "", "///"])
def test_a_namespace_that_names_no_robot_is_refused(namespace: str) -> None:
    with pytest.raises(ValueError):
        resolve_frame_id(namespace, "base_link")
