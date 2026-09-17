"""独立オラクルによる 01_Geometry / 07 coverage・cliff の安全 unit（R-26）。

このファイルは **仕様と合成シーンの生成パラメータだけ**から書かれている。期待値は
すべて手計算リテラルで、モジュールから再導出しない（tautological 禁止 =
docs/architecture/20-dev-quality-and-testing.md:131 §9）。

仕様の出典（行は worktree の実体で確認済み）:

- docs/mode-outdoor/04-perception-sidewalk-and-signals.md:56（`considerDrop` =
  地面推定より下の点／`noDataObstacle` = 点数不足セル = 未観測 = 通行不可）
- 同 :173（`FLOOR_CONFIRMED` / `DROP_DETECTED` / `UNKNOWN` の三値・**`UNKNOWN` は
  LaserScan に落ちない**）・:174（無効深度 = NaN / inf / 0 / 空 / 有効画素過少 /
  凍結フレーム）・:175（今から踏む領域）・:176（距離は車輪接地点・footprint 基準）・
  :177（元計測時刻の維持）・:181（取付幾何 `h / tan θ`）
- 同 :196（`FLOOR_CONFIRMED` = 床を観測できた に限定・段差高は別 field・
  `UNKNOWN` ≠ `DROP_DETECTED`）・:203（04 は自己申告・判定は X2）・
  :331（停止距離の不等式の評価点は X2 / 09。04 は観測済み距離を出すだけ）
- 追補 ④ :427（`confirmed_distance_m=None` は「0 m 確認」ではない）・
  :434（通行可否 field を持たない）
- docs/mode-outdoor/09-external-review-v3-response.md:213（順序 5 = terrain 契約の型
  + coverage 判定 unit）・:225（F5 = 深度を NaN / inf / 空 / 静止画にしても品質不成立）

**合成シーンの生成器はこのファイル側**にあり、ピンホール逆投影を module とは独立に
書き下している（同じ式を 2 度書くのではなく、**シーン → 深度画像**という逆向きの
写像を書く）。段差シーンの期待値は生成パラメータから手で出る:

    下り段差 d = 1.00 m・段差 0.06 m・カメラ高 h = 0.30 m のとき、
    下段面を見る最初の点は X = d·(h + 段差)/h = 1.00 × 0.36/0.30 = **1.20 m**
    （その間 [1.00, 1.20) は縁に遮蔽される影 = 点が返らない = `UNKNOWN`）。
    datum は 0.20 m 前方・cell 0.10 m なので
      confirmed = 1.00 − 0.20 = **0.80 m**（cell 8 個）
      nearest_drop = 1.20 − 0.20 = **1.00 m**（cell 10 の手前端）
      step_height = **−0.06 m**（符号あり）
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest
from warehouse_interfaces.perception import TerrainCoverage, TerrainState
from warehouse_perception.terrain_core import (
    CameraIntrinsics,
    CliffScanParams,
    GroundFitParams,
    GroundPlane,
    MountingGeometry,
    TerrainGridParams,
    TerrainParams,
    analyze_depth_frame,
    back_project,
    classify_cells,
    corridor_lateral_indices,
    depth_validity,
    terrain_coverage,
)

pytestmark = [pytest.mark.unit, pytest.mark.safety]

_MODULE_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "ws"
    / "src"
    / "warehouse_perception"
    / "warehouse_perception"
    / "terrain_core.py"
)

# ---------------------------------------------------------------- 合成シーンの定数
# すべて「この試験のためのカメラ」であって既定値ではない（module 側に既定は無い）。
ROWS, COLS = 48, 64
CAMERA_HEIGHT_M = 0.30
PITCH_DOWN_RAD = math.radians(35.0)
REFERENCE_OFFSET_M = 0.20
CELL_SIZE_M = 0.10
DROP_THRESHOLD_M = 0.04
PLANE_TOLERANCE_M = 0.02
FORWARD_RANGE_M = 1.20  # = 12 cell。平坦床が回廊全体を覆う長さ
DROP_AT_M = 1.00
DROP_DEPTH_M = 0.06
REFERENCE = "base_footprint_front"
SOURCE_STAMP_S = 12.5


def _intrinsics() -> CameraIntrinsics:
    return CameraIntrinsics(fx=50.0, fy=50.0, cx=32.0, cy=24.0)


def _mounting() -> MountingGeometry:
    return MountingGeometry(
        camera_height_m=CAMERA_HEIGHT_M,
        pitch_down_rad=PITCH_DOWN_RAD,
        reference_offset_m=REFERENCE_OFFSET_M,
    )


def _grid_params(*, min_valid_fraction: float = 0.20) -> TerrainGridParams:
    return TerrainGridParams(
        cell_size_m=CELL_SIZE_M,
        corridor_half_width_m=0.15,
        forward_range_m=FORWARD_RANGE_M,
        min_points_per_cell=3,
        drop_threshold_m=DROP_THRESHOLD_M,
        min_valid_fraction=min_valid_fraction,
    )


def _params(*, seed: int = 7, min_valid_fraction: float = 0.20) -> TerrainParams:
    return TerrainParams(
        intrinsics=_intrinsics(),
        mounting=_mounting(),
        grid=_grid_params(min_valid_fraction=min_valid_fraction),
        ground_fit=GroundFitParams(
            plane_tolerance_m=PLANE_TOLERANCE_M, ransac_iterations=16, ransac_seed=seed
        ),
    )


def _scan_params() -> CliffScanParams:
    return CliffScanParams(
        angle_min_rad=-math.pi / 2.0,
        angle_max_rad=math.pi / 2.0,
        ray_count=90,
        range_min_m=0.05,
        range_max_m=3.0,
    )


def synthetic_depth_frame(
    *,
    drop_at_m: float | None = None,
    drop_depth_m: float = 0.0,
    blind_band_m: tuple[float, float] | None = None,
    true_camera_height_m: float = CAMERA_HEIGHT_M,
) -> list[list[float | None]]:
    """シーン（水平面 + 任意の下り段差 + 任意の無反射帯）→ 深度画像を作る。

    module の逆投影ではなく **投影**を書く: 画像行 v の光線方向を光学系で作り、
    高さ s の水平面との交点までの深度 z = (s − h) / C を求める（C = 光線の上向き
    成分／単位深度）。段差は「床を先に当て、交点が d を越えていたら下段面へ当て直す」
    ——縁の背後 [d, d·(h+段差)/h) は幾何的に影になり点が返らない。

    ``blind_band_m`` は「その X 帯だけ深度が返らない」（濡れ面・黒い舗装）を模し、
    None を書き込む（04:174 の無効深度）。
    """
    intrinsics = _intrinsics()
    cos_pitch = math.cos(PITCH_DOWN_RAD)
    sin_pitch = math.sin(PITCH_DOWN_RAD)
    frame: list[list[float | None]] = []
    for v in range(ROWS):
        dy = (v - intrinsics.cy) / intrinsics.fy
        forward_per_depth = cos_pitch - dy * sin_pitch
        up_per_depth = -sin_pitch - dy * cos_pitch
        depth = (0.0 - true_camera_height_m) / up_per_depth
        x_body = depth * forward_per_depth
        if drop_at_m is not None and x_body >= drop_at_m:
            depth = (-drop_depth_m - true_camera_height_m) / up_per_depth
            x_body = depth * forward_per_depth
        blind = blind_band_m is not None and blind_band_m[0] <= x_body < blind_band_m[1]
        frame.append([None if blind else depth] * COLS)
    return frame


def _analyze(frame, *, params: TerrainParams | None = None):
    return analyze_depth_frame(
        frame,
        params=params if params is not None else _params(),
        scan=_scan_params(),
        source_stamp_s=SOURCE_STAMP_S,
        reference=REFERENCE,
        device_frame_seq=9,
        processing_latency_s=0.03,
    )


def _row_letters(observation) -> str:
    symbols = {
        TerrainState.FLOOR_CONFIRMED: "F",
        TerrainState.DROP_DETECTED: "D",
        TerrainState.UNKNOWN: "?",
    }
    return "".join(symbols[state] for state in observation.grid.row_states())


_IDEAL_GROUND = GroundPlane(a=0.0, b=0.0, c=0.0, inlier_count=0, point_count=0, used_prior=True)


def _points_in_cell(
    ix: int, iy: int, height_m: float, count: int
) -> list[tuple[float, float, float]]:
    """cell (ix, iy) の内側に ``count`` 個、高さ ``height_m`` の点を置く。"""
    return [
        (
            REFERENCE_OFFSET_M + (ix + 0.2 + 0.05 * k) * CELL_SIZE_M,
            (iy + 0.5) * CELL_SIZE_M,
            height_m,
        )
        for k in range(count)
    ]


# ------------------------------------------------------------------ 平坦床（基準）
def test_flat_floor_confirms_the_whole_corridor_and_emits_no_cliff() -> None:
    observation = _analyze(synthetic_depth_frame())

    assert _row_letters(observation) == "FFFFFFFFFFFF"  # 12 cell = 1.20 m
    coverage = observation.coverage
    assert isinstance(coverage, TerrainCoverage)
    assert coverage.state == TerrainState.FLOOR_CONFIRMED
    assert coverage.confirmed_distance_m == pytest.approx(1.20)
    assert coverage.nearest_drop_distance_m is None  # 未検出（不存在ではない）
    assert coverage.step_height_m is None
    # v0 は単位・統計的意味が未定のため発明しない（OQ-OD4Y-c / 04:196）。
    assert coverage.slope is None
    assert coverage.roughness_m is None
    assert coverage.estimate_error_m is None
    # 元計測時刻と基準はそのまま運ぶ（04:177 / :176）。
    assert coverage.source_stamp_s == SOURCE_STAMP_S
    assert coverage.reference == REFERENCE
    assert coverage.quality.valid_fraction == pytest.approx(1.0)
    assert coverage.quality.device_frame_seq == 9
    assert coverage.quality.processing_latency_s == pytest.approx(0.03)
    assert coverage.quality.frame_digest == observation.validity.frame_digest
    assert all(math.isinf(value) for value in observation.cliff.ranges)


def test_optical_axis_meets_the_ground_at_h_over_tan_theta() -> None:
    """取付幾何の worked example（04:181）: h = 0.30・θ = 35° → 約 0.4285 m。"""
    intrinsics = _intrinsics()
    frame = synthetic_depth_frame()
    centre_depth = frame[int(intrinsics.cy)][int(intrinsics.cx)]
    assert centre_depth is not None
    expected_x = CAMERA_HEIGHT_M / math.tan(PITCH_DOWN_RAD)
    # 逆投影した光軸上の点が h / tan θ に立つ（module 側の式とは独立に、
    # 射影 → 逆投影の往復が幾何と一致することを見る）。
    ((x_body, y_body, z_body),) = back_project(
        [(int(intrinsics.cx), int(intrinsics.cy), centre_depth)],
        intrinsics=intrinsics,
        mounting=_mounting(),
    )
    assert x_body == pytest.approx(expected_x)
    assert y_body == pytest.approx(0.0, abs=1e-12)
    assert z_body == pytest.approx(0.0, abs=1e-12)


# ------------------------------------------------------------------------ 下り段差
def test_step_down_reports_drop_with_hand_computed_distances() -> None:
    observation = _analyze(synthetic_depth_frame(drop_at_m=DROP_AT_M, drop_depth_m=DROP_DEPTH_M))

    # F×8（0.20–1.00 m）→ 影 2 cell（1.00–1.20 m・遮蔽で点なし）→ D（1.20 m 以遠）。
    assert _row_letters(observation) == "FFFFFFFF??DD"
    coverage = observation.coverage
    assert coverage.state == TerrainState.DROP_DETECTED
    assert coverage.confirmed_distance_m == pytest.approx(DROP_AT_M - REFERENCE_OFFSET_M)
    expected_drop_x = DROP_AT_M * (CAMERA_HEIGHT_M + DROP_DEPTH_M) / CAMERA_HEIGHT_M
    assert coverage.nearest_drop_distance_m == pytest.approx(expected_drop_x - REFERENCE_OFFSET_M)
    # 量子化は必ずロボット側（手前）へ倒れる。
    assert coverage.nearest_drop_distance_m <= expected_drop_x - REFERENCE_OFFSET_M + 1e-12
    assert expected_drop_x - REFERENCE_OFFSET_M - coverage.nearest_drop_distance_m < CELL_SIZE_M
    assert coverage.step_height_m == pytest.approx(-DROP_DEPTH_M)  # 下りは負


def test_shadow_behind_the_edge_is_unknown_not_drop() -> None:
    """縁の背後の遮蔽帯は「見えていない」であって「落ちている」ではない（04:196）。"""
    observation = _analyze(synthetic_depth_frame(drop_at_m=DROP_AT_M, drop_depth_m=DROP_DEPTH_M))
    rows = observation.grid.row_states()
    assert rows[8] == TerrainState.UNKNOWN
    assert rows[9] == TerrainState.UNKNOWN
    assert observation.grid.cells.get((8, 0)) is None  # 点が 1 つも返っていない


def test_cliff_scan_carries_only_drop_cells_and_keeps_the_source_stamp() -> None:
    observation = _analyze(synthetic_depth_frame(drop_at_m=DROP_AT_M, drop_depth_m=DROP_DEPTH_M))
    cliff = observation.cliff

    assert len(cliff.ranges) == 90
    assert cliff.source_stamp_s == SOURCE_STAMP_S  # 付け直さない（04:177）
    assert cliff.omitted_cell_count == 0
    finite = [value for value in cliff.ranges if math.isfinite(value)]
    assert finite, "崖があるのに 1 本も撃たれていない"
    assert len(finite) < len(cliff.ranges), "崖の無い方位まで埋めている"
    # 最近接光線 = cell (10, -1)/(10, 0) の中心（datum から X = 1.05・|Y| = 0.05）。
    assert min(finite) == pytest.approx(math.hypot(1.05, 0.05))
    # DROP cell の数だけが投影対象で、UNKNOWN は 1 つも入らない。
    assert cliff.drop_cell_count == sum(
        1 for cell in observation.grid.cells.values() if cell.state == TerrainState.DROP_DETECTED
    )


def test_unknown_never_enters_the_cliff_scan() -> None:
    """04:173 — `UNKNOWN` は LaserScan に落ちない（未観測を壁に化けさせない）。"""
    observation = _analyze(synthetic_depth_frame(blind_band_m=(0.60, 0.70)))
    assert TerrainState.UNKNOWN in observation.grid.row_states()
    assert observation.cliff.drop_cell_count == 0
    assert all(math.isinf(value) for value in observation.cliff.ranges)


# -------------------------------------------------------------------- 無効深度
def test_depth_dropout_band_stops_the_confirmed_run_before_the_gap() -> None:
    observation = _analyze(synthetic_depth_frame(blind_band_m=(0.60, 0.70)))

    # 帯 [0.60, 0.70) は cell 4（datum から 0.40–0.50 m）に一致する。
    assert _row_letters(observation) == "FFFF?FFFFFFF"
    coverage = observation.coverage
    assert coverage.state == TerrainState.FLOOR_CONFIRMED
    # 連続性が切れた時点で止まる。gap の向こうの床は数に入れない。
    assert coverage.confirmed_distance_m == pytest.approx(0.40)
    # 無効は画像 3 行 ×64 列 = 192 / 3072 画素 → 有効率 2880/3072 = 0.9375。
    assert coverage.quality.valid_fraction == pytest.approx(0.9375)
    assert observation.validity.total_pixels == ROWS * COLS
    assert observation.validity.valid_count == ROWS * COLS - 192


@pytest.mark.parametrize(
    "frame",
    [
        pytest.param([], id="empty-frame"),
        pytest.param([[] for _ in range(4)], id="empty-rows"),
        pytest.param([[float("nan")] * 4 for _ in range(4)], id="all-nan"),
        pytest.param([[float("inf")] * 4 for _ in range(4)], id="all-inf"),
        pytest.param([[0.0] * 4 for _ in range(4)], id="all-zero"),
        pytest.param([[-1.5] * 4 for _ in range(4)], id="all-negative"),
        pytest.param([["nope", None] * 2 for _ in range(4)], id="non-numeric"),
        pytest.param([[True] * 4 for _ in range(4)], id="bool-is-not-a-depth"),
    ],
)
def test_broken_depth_frames_fold_into_unknown_without_raising(frame) -> None:
    """F5（09:225）: NaN / inf / 空 / 静止画でも品質不成立。例外は上げない。"""
    observation = _analyze(frame)
    assert observation.coverage.state == TerrainState.UNKNOWN
    assert observation.coverage.confirmed_distance_m is None
    assert observation.coverage.nearest_drop_distance_m is None
    assert observation.coverage.quality.valid_fraction == pytest.approx(0.0)
    assert all(math.isinf(value) for value in observation.cliff.ranges)


def test_valid_fraction_is_the_plain_ratio_on_a_hand_written_frame() -> None:
    validity = depth_validity([[1.0, float("nan"), 2.0], [None, 0.5, 0.0]])
    assert validity.total_pixels == 6
    assert validity.valid_count == 3
    assert validity.valid_fraction == pytest.approx(0.5)
    assert validity.valid_pixels == ((0, 0, 1.0), (2, 0, 2.0), (1, 1, 0.5))


# ---------------------------------------------------------------- cell 判定規則
def test_drop_threshold_boundary_is_inclusive_and_shallower_dips_are_unknown() -> None:
    """ちょうど閾値 = DROP。僅かに浅い窪みは DROP でも FLOOR でもない。"""
    params = _params()

    exactly = classify_cells(
        _points_in_cell(3, 0, -DROP_THRESHOLD_M, 5), _IDEAL_GROUND, params=params
    )
    assert exactly.state_at(3, 0) == TerrainState.DROP_DETECTED

    just_shallower = classify_cells(
        _points_in_cell(3, 0, -(DROP_THRESHOLD_M - 1e-4), 5), _IDEAL_GROUND, params=params
    )
    # 平面帯（±0.02）からも外れている → 床を観測できていない = UNKNOWN。
    assert just_shallower.state_at(3, 0) == TerrainState.UNKNOWN

    inside_plane_band = classify_cells(
        _points_in_cell(3, 0, -PLANE_TOLERANCE_M, 5), _IDEAL_GROUND, params=params
    )
    assert inside_plane_band.state_at(3, 0) == TerrainState.FLOOR_CONFIRMED


def test_positive_obstacle_hiding_the_floor_is_unknown_not_floor_and_not_drop() -> None:
    """床より高い点しか無い cell は「床を観測できていない」（04:196）。"""
    grid = classify_cells(_points_in_cell(2, 0, 0.25, 8), _IDEAL_GROUND, params=_params())
    assert grid.state_at(2, 0) == TerrainState.UNKNOWN
    cell = grid.cells[(2, 0)]
    assert cell.point_count == 8
    assert cell.floor_point_count == 0
    assert cell.drop_point_count == 0


@pytest.mark.parametrize(
    ("height_m", "count", "expected"),
    [
        (0.0, 2, TerrainState.UNKNOWN),
        (0.0, 3, TerrainState.FLOOR_CONFIRMED),
        # 証拠の下限は崖にも効く: 2 点しか無い cell は「落ちている」とも言えない。
        # 未観測 = 通行不可として UNKNOWN で出し、LaserScan には落とさない（04:173）。
        (-DROP_DEPTH_M, 2, TerrainState.UNKNOWN),
        (-DROP_DEPTH_M, 3, TerrainState.DROP_DETECTED),
    ],
)
def test_min_points_per_cell_is_the_evidence_floor(height_m, count, expected) -> None:
    """noDataObstacle（04:56）: 点数不足は UNKNOWN。min_points_per_cell = 3。"""
    grid = classify_cells(_points_in_cell(1, 0, height_m, count), _IDEAL_GROUND, params=_params())
    assert grid.state_at(1, 0) == expected


def test_one_unobserved_lateral_bin_withholds_the_row() -> None:
    """回廊の 1 レーンでも未観測なら床は名乗れない（04:175）。"""
    params = _params()
    corridor = corridor_lateral_indices(params.grid)
    assert corridor == (-2, -1, 0, 1)
    points: list[tuple[float, float, float]] = []
    for iy in corridor[:-1]:  # 1 本だけ埋めずに残す
        points += _points_in_cell(0, iy, 0.0, 5)
    grid = classify_cells(points, _IDEAL_GROUND, params=params)
    assert grid.row_state(0) == TerrainState.UNKNOWN

    points += _points_in_cell(0, corridor[-1], 0.0, 5)
    assert classify_cells(points, _IDEAL_GROUND, params=params).row_state(0) == (
        TerrainState.FLOOR_CONFIRMED
    )


def test_one_drop_bin_makes_the_whole_row_a_drop() -> None:
    """危険の正の観測が優先する（床が 3 レーン見えていても行は DROP）。"""
    params = _params()
    corridor = corridor_lateral_indices(params.grid)
    points: list[tuple[float, float, float]] = []
    for iy in corridor[:-1]:
        points += _points_in_cell(0, iy, 0.0, 5)
    points += _points_in_cell(0, corridor[-1], -DROP_DEPTH_M, 5)
    assert classify_cells(points, _IDEAL_GROUND, params=params).row_state(0) == (
        TerrainState.DROP_DETECTED
    )


def test_corridor_bins_are_symmetric_about_the_body_axis() -> None:
    centres = [(iy + 0.5) * CELL_SIZE_M for iy in corridor_lateral_indices(_params().grid)]
    assert centres == pytest.approx([-0.15, -0.05, 0.05, 0.15])
    assert max(abs(value) for value in centres) <= 0.15 + 1e-12


# ------------------------------------------------------------------ 地面推定（事前値）
def test_mounting_geometry_is_a_prior_the_observation_overrides() -> None:
    """h を 3 cm 誤って注入しても、フィットが観測から床面を取り戻す（案 B・04:44）。"""
    observation = _analyze(synthetic_depth_frame(true_camera_height_m=0.33))

    assert observation.plane.used_prior is False
    assert observation.plane.c == pytest.approx(-0.03, abs=1e-9)
    assert observation.plane.a == pytest.approx(0.0, abs=1e-9)
    assert observation.plane.b == pytest.approx(0.0, abs=1e-9)
    assert observation.plane.inlier_fraction == pytest.approx(1.0)
    # 事前値のままなら残差 −0.03 は平面帯（±0.02）の外＝全 cell UNKNOWN になる。
    assert observation.coverage.state == TerrainState.FLOOR_CONFIRMED
    assert observation.coverage.confirmed_distance_m == pytest.approx(1.20)


def test_prior_survives_when_no_sample_beats_it() -> None:
    observation = _analyze(synthetic_depth_frame())
    assert observation.plane.used_prior is True
    assert (observation.plane.a, observation.plane.b, observation.plane.c) == (0.0, 0.0, 0.0)


def test_ground_fit_is_reproducible_and_the_state_does_not_depend_on_the_seed() -> None:
    frame = synthetic_depth_frame(true_camera_height_m=0.33)
    first = _analyze(frame, params=_params(seed=7))
    again = _analyze(frame, params=_params(seed=7))
    assert first.plane == again.plane
    assert first.coverage == again.coverage

    for seed in (0, 12345, -99):
        other = _analyze(frame, params=_params(seed=seed))
        assert other.coverage.state == first.coverage.state
        assert other.coverage.confirmed_distance_m == pytest.approx(
            first.coverage.confirmed_distance_m
        )
        assert other.plane.c == pytest.approx(-0.03, abs=1e-9)


# ------------------------------------------------------------------- frame_digest
def test_frame_digest_is_stable_for_the_payload_and_changes_with_one_pixel() -> None:
    frame = synthetic_depth_frame()
    copy = [list(row) for row in frame]
    assert depth_validity(frame).frame_digest == depth_validity(copy).frame_digest

    mutated = [list(row) for row in frame]
    mutated[10][10] = mutated[10][10] + 1e-6
    assert depth_validity(mutated).frame_digest != depth_validity(frame).frame_digest

    # 凍結フレーム検出の材料なので、無効値の入れ替えも指紋を変える（追補 ③ #5）。
    nan_swapped = [list(row) for row in frame]
    nan_swapped[0][0] = float("nan")
    zero_swapped = [list(row) for row in frame]
    zero_swapped[0][0] = 0.0
    assert depth_validity(nan_swapped).frame_digest != depth_validity(zero_swapped).frame_digest
    assert len(depth_validity(frame).frame_digest) == 64


# ----------------------------------------------------------------- 有効画素率ゲート
def _coverage_from_rows(
    *, drop_row: int | None, floor_rows: int, valid_fraction: float, min_valid_fraction: float
):
    """床 ``floor_rows`` 行 + 任意の落下行だけを持つ grid から coverage を作る。"""
    params = _params(min_valid_fraction=min_valid_fraction)
    corridor = corridor_lateral_indices(params.grid)
    points: list[tuple[float, float, float]] = []
    for ix in range(floor_rows):
        for iy in corridor:
            points += _points_in_cell(ix, iy, 0.0, 5)
    if drop_row is not None:
        for iy in corridor:
            points += _points_in_cell(drop_row, iy, -DROP_DEPTH_M, 5)
    grid = classify_cells(points, _IDEAL_GROUND, params=params)
    return terrain_coverage(
        grid,
        params=params,
        valid_fraction=valid_fraction,
        source_stamp_s=SOURCE_STAMP_S,
        reference=REFERENCE,
        frame_digest="deadbeef",
        device_frame_seq=None,
        processing_latency_s=None,
    )


def test_quality_gate_withholds_the_floor_but_keeps_the_drop_evidence() -> None:
    """有効画素過少（04:174）は床の主張だけを取り下げ、危険の観測は消さない。"""
    established = _coverage_from_rows(
        drop_row=3, floor_rows=3, valid_fraction=0.95, min_valid_fraction=0.20
    )
    assert established.confirmed_distance_m == pytest.approx(0.30)
    assert established.state == TerrainState.DROP_DETECTED
    assert established.nearest_drop_distance_m == pytest.approx(0.30)
    assert established.step_height_m == pytest.approx(-DROP_DEPTH_M)

    not_established = _coverage_from_rows(
        drop_row=3, floor_rows=3, valid_fraction=0.10, min_valid_fraction=0.20
    )
    assert not_established.confirmed_distance_m is None  # 床は名乗れない
    assert not_established.state == TerrainState.DROP_DETECTED  # 危険観測は残る
    assert not_established.nearest_drop_distance_m == pytest.approx(0.30)


def test_quality_gate_alone_yields_unknown_when_there_is_no_drop() -> None:
    coverage = _coverage_from_rows(
        drop_row=None, floor_rows=5, valid_fraction=0.10, min_valid_fraction=0.20
    )
    assert coverage.state == TerrainState.UNKNOWN
    assert coverage.confirmed_distance_m is None
    assert coverage.nearest_drop_distance_m is None
    # 境界（ちょうど min_valid_fraction）は成立側。
    exactly = _coverage_from_rows(
        drop_row=None, floor_rows=5, valid_fraction=0.20, min_valid_fraction=0.20
    )
    assert exactly.state == TerrainState.FLOOR_CONFIRMED
    assert exactly.confirmed_distance_m == pytest.approx(0.50)


def test_confirmed_distance_none_is_not_zero_confirmed() -> None:
    """追補 ④ :427 の読み方を型の上で固定する。"""
    params = _params()
    grid = classify_cells([], _IDEAL_GROUND, params=params)
    coverage = terrain_coverage(
        grid,
        params=params,
        valid_fraction=1.0,
        source_stamp_s=SOURCE_STAMP_S,
        reference=REFERENCE,
        frame_digest=None,
        device_frame_seq=None,
        processing_latency_s=None,
    )
    assert coverage.confirmed_distance_m is None
    assert coverage.confirmed_distance_m != 0.0
    assert coverage.state == TerrainState.UNKNOWN
    assert coverage.quality.frame_digest is None


# ------------------------------------------------------- パラメータ検証（呼び出し側の誤り）
@pytest.mark.parametrize(
    ("factory", "kwargs"),
    [
        (CameraIntrinsics, dict(fx=0.0, fy=50.0, cx=32.0, cy=24.0)),
        (CameraIntrinsics, dict(fx=float("nan"), fy=50.0, cx=32.0, cy=24.0)),
        (CameraIntrinsics, dict(fx=50.0, fy=-1.0, cx=32.0, cy=24.0)),
        (CameraIntrinsics, dict(fx=True, fy=50.0, cx=32.0, cy=24.0)),
        (CameraIntrinsics, dict(fx="50", fy=50.0, cx=32.0, cy=24.0)),
        (CameraIntrinsics, dict(fx=50.0, fy=50.0, cx=-1.0, cy=24.0)),
        (MountingGeometry, dict(camera_height_m=0.0, pitch_down_rad=0.5, reference_offset_m=0.2)),
        (MountingGeometry, dict(camera_height_m=0.3, pitch_down_rad=0.0, reference_offset_m=0.2)),
        (
            MountingGeometry,
            dict(camera_height_m=0.3, pitch_down_rad=math.pi / 2.0, reference_offset_m=0.2),
        ),
        (
            MountingGeometry,
            dict(camera_height_m=0.3, pitch_down_rad=0.5, reference_offset_m=float("inf")),
        ),
        (
            TerrainGridParams,
            dict(
                cell_size_m=0.0,
                corridor_half_width_m=0.15,
                forward_range_m=1.2,
                min_points_per_cell=3,
                drop_threshold_m=0.04,
                min_valid_fraction=0.2,
            ),
        ),
        (
            TerrainGridParams,
            dict(
                cell_size_m=0.1,
                corridor_half_width_m=0.15,
                forward_range_m=0.05,  # 1 cell に満たない
                min_points_per_cell=3,
                drop_threshold_m=0.04,
                min_valid_fraction=0.2,
            ),
        ),
        (
            TerrainGridParams,
            dict(
                cell_size_m=0.1,
                corridor_half_width_m=0.15,
                forward_range_m=1.2,
                min_points_per_cell=0,
                drop_threshold_m=0.04,
                min_valid_fraction=0.2,
            ),
        ),
        (
            TerrainGridParams,
            dict(
                cell_size_m=0.1,
                corridor_half_width_m=0.15,
                forward_range_m=1.2,
                min_points_per_cell=3,
                drop_threshold_m=0.04,
                min_valid_fraction=0.0,
            ),
        ),
        (
            TerrainGridParams,
            dict(
                cell_size_m=0.1,
                corridor_half_width_m=0.15,
                forward_range_m=1.2,
                min_points_per_cell=3,
                drop_threshold_m=0.04,
                min_valid_fraction=1.5,
            ),
        ),
        (GroundFitParams, dict(plane_tolerance_m=0.0, ransac_iterations=8, ransac_seed=1)),
        (GroundFitParams, dict(plane_tolerance_m=0.02, ransac_iterations=0, ransac_seed=1)),
        (GroundFitParams, dict(plane_tolerance_m=0.02, ransac_iterations=8, ransac_seed=1.5)),
        (
            CliffScanParams,
            dict(
                angle_min_rad=1.0,
                angle_max_rad=1.0,
                ray_count=90,
                range_min_m=0.05,
                range_max_m=3.0,
            ),
        ),
        (
            CliffScanParams,
            dict(
                angle_min_rad=-1.0,
                angle_max_rad=1.0,
                ray_count=0,
                range_min_m=0.05,
                range_max_m=3.0,
            ),
        ),
        (
            CliffScanParams,
            dict(
                angle_min_rad=-1.0,
                angle_max_rad=1.0,
                ray_count=90,
                range_min_m=3.0,
                range_max_m=0.05,
            ),
        ),
    ],
)
def test_parameter_mistakes_raise_at_construction(factory, kwargs) -> None:
    with pytest.raises(ValueError):
        factory(**kwargs)


def test_drop_band_must_not_overlap_the_floor_band() -> None:
    """同じ点が「床」と「崖」を同時に名乗れる設定を起動時に拒む。"""
    with pytest.raises(ValueError, match="must exceed"):
        TerrainParams(
            intrinsics=_intrinsics(),
            mounting=_mounting(),
            grid=TerrainGridParams(
                cell_size_m=0.1,
                corridor_half_width_m=0.15,
                forward_range_m=1.2,
                min_points_per_cell=3,
                drop_threshold_m=0.02,
                min_valid_fraction=0.2,
            ),
            ground_fit=GroundFitParams(plane_tolerance_m=0.02, ransac_iterations=8, ransac_seed=1),
        )


# ------------------------------------------------------------------------- AST pin
def _module_tree() -> ast.Module:
    return ast.parse(_MODULE_SOURCE.read_text(encoding="utf-8"))


def test_module_imports_no_ros_and_no_numpy() -> None:
    """純ロジック（rclpy 非依存・numpy 非依存＝CI に numpy が無い）。"""
    imported: set[str] = set()
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "rclpy" not in imported
    assert "numpy" not in imported
    assert "warehouse_interfaces" in imported  # 依存してよい共有 package のみ


def test_module_never_names_a_motion_topic() -> None:
    """自律走行 安全層外の producer＝actuation 権限を持たない（0 cmd_vel）。"""
    source = _MODULE_SOURCE.read_text(encoding="utf-8")
    for forbidden in ("cmd_vel", "stop_request", "speed_limit"):
        assert forbidden not in source


def test_no_dataclass_field_carries_a_default() -> None:
    """しきい値の既定値を作らない（docs が数値を pin していない）。"""
    offenders: list[str] = []
    for node in ast.walk(_module_tree()):
        if not isinstance(node, ast.ClassDef):
            continue
        decorators = [
            decorator.func if isinstance(decorator, ast.Call) else decorator
            for decorator in node.decorator_list
        ]
        names = {
            decorator.id if isinstance(decorator, ast.Name) else getattr(decorator, "attr", "")
            for decorator in decorators
        }
        if "dataclass" not in names:
            continue
        for statement in node.body:
            if isinstance(statement, ast.AnnAssign) and statement.value is not None:
                offenders.append(f"{node.name}.{ast.unparse(statement.target)}")
    assert offenders == [], f"パラメータ dataclass に既定値がある: {offenders}"
