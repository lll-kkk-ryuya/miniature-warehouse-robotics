"""独立オラクルによる M1 body-velocity clamp（L0'）の安全 unit（R-26）。

このファイルは **仕様のみ**から書かれている。対象実装
``ws/src/warehouse_m1_driver/warehouse_m1_driver/clamp.py`` は意図的に一度も読んで
いない。これは docs/architecture/20-dev-quality-and-testing.md:131-143 §9 が禁じる
tautological（実装と同じ式で期待値を再計算する）/ implementation-coupled テストを
**構造的に**避けるための担保である（期待値は known-good リテラル・手計算の worked
example・仕様値から取る＝doc20:139）。

対象仕様::

    clamp_body_velocity(vx: float, vy: float, wz: float) -> tuple[float, float, float]

1. ``vx``/``vy``/``wz`` のいずれかが非有限（NaN / ±inf）なら ``(0.0, 0.0, 0.0)``
   （fail-safe stop）。±cap へスナップしてはならない。
2. 線速度は **ベクトルの大きさ** でクランプする。``mag = hypot(vx, vy)`` が
   ``MAX_LINEAR_VELOCITY`` 以下なら素通し。超える場合は **方向を保ったまま**
   ``mag == MAX_LINEAR_VELOCITY`` になるようスケールする（軸独立クランプは不可）。
3. ``wz`` はこのスライスではクランプせず素通し。
4. ``vx == vy == 0.0`` でゼロ除算しない。

cap は凍結契約 ``warehouse_interfaces.safety.MAX_LINEAR_VELOCITY``
（ws/src/warehouse_interfaces/warehouse_interfaces/safety.py:18 / .claude/rules/safety.md:3）
から import する。期待値に 0.3 をハードコードしない（唯一の例外＝下の契約 pin 1本）。

``hypothesis`` はプロジェクト依存ではない（pyproject.toml:19-24）ため、プロパティ
テストは **固定 seed の決定的サンプル** で書く（tests/unit/test_wo_kpi.py:339-344 と
同じ流儀）。
"""

import itertools
import math
import random

import pytest
from warehouse_interfaces.safety import MAX_LINEAR_VELOCITY
from warehouse_m1_driver.clamp import clamp_body_velocity

# 仕様値（凍結契約）。テスト内で 0.3 リテラルは使わない。
CAP = MAX_LINEAR_VELOCITY

# 浮動小数の許容差。安全側の invariant は cap + 1e-9 を上限とする（仕様どおり）。
TOL = 1e-9

NON_FINITE = (float("nan"), float("inf"), float("-inf"))

STOP = (0.0, 0.0, 0.0)


def _mag(vx: float, vy: float) -> float:
    """独立オラクル: 線速度ベクトルの大きさ（invariant 検査用）。"""
    return math.hypot(vx, vy)


def _angle(vx: float, vy: float) -> float:
    """独立オラクル: 線速度ベクトルの向き（invariant 検査用）。"""
    return math.atan2(vy, vx)


def _finite_samples(rng: random.Random, count: int, lo_exp: float, hi_exp: float):
    """対数一様な大きさ × 一様な角度の決定的サンプル（(vx, vy, wz) を yield）。"""
    for _ in range(count):
        mag = 10.0 ** rng.uniform(lo_exp, hi_exp)
        ang = rng.uniform(-math.pi, math.pi)
        yield mag * math.cos(ang), mag * math.sin(ang), rng.uniform(-100.0, 100.0)


# ── A. 契約 pin / 形 ─────────────────────────────────────────────────────────


@pytest.mark.safety
def test_cap_contract_is_03_ms() -> None:
    """cap は 0.3 m/s（.claude/rules/safety.md:3 / safety.py:18）。

    このファイル唯一の 0.3 リテラル。以下の worked example（例: 3-4-5 の
    ``(0.6, 0.8) -> (0.18, 0.24)``）は cap=0.3 を前提に手計算しているので、cap が
    動いたらここが最初に赤くなる。
    """
    assert MAX_LINEAR_VELOCITY == 0.3


@pytest.mark.safety
def test_returns_three_floats() -> None:
    """戻りは 3 要素タプル（仕様のシグネチャ）。2要素/リスト返しを落とす。"""
    out = clamp_body_velocity(0.1, 0.2, 0.3)
    assert isinstance(out, tuple)
    assert len(out) == 3
    assert all(isinstance(v, float) for v in out)


# ── B. 非有限 → fail-safe stop ───────────────────────────────────────────────

# vx/vy/wz それぞれが「有限代表値 or NaN or +inf or -inf」を取る 4^3 = 64 通り。
_FINITE_BASE = (0.1, 0.2, 0.5)  # mag = hypot(0.1, 0.2) ≈ 0.2236 < cap → 素通し
_NON_FINITE_GRID = list(
    itertools.product(
        (_FINITE_BASE[0], *NON_FINITE),
        (_FINITE_BASE[1], *NON_FINITE),
        (_FINITE_BASE[2], *NON_FINITE),
    )
)


@pytest.mark.safety
@pytest.mark.parametrize(("vx", "vy", "wz"), _NON_FINITE_GRID)
def test_non_finite_anywhere_is_full_stop(vx: float, vy: float, wz: float) -> None:
    """3引数 × {finite, NaN, +inf, -inf} の全 64 組合せ。

    1つでも非有限なら ``(0.0, 0.0, 0.0)``、全て有限なら素通し。
    """
    out = clamp_body_velocity(vx, vy, wz)
    if all(math.isfinite(v) for v in (vx, vy, wz)):
        assert out == _FINITE_BASE
    else:
        assert out == STOP


@pytest.mark.safety
@pytest.mark.parametrize("bad", NON_FINITE)
def test_non_finite_is_not_snapped_to_cap(bad: float) -> None:
    """非有限は「不明」→ 停止であって ±cap ではない（safety.py:26-34 と同型）。

    ``max(-cap, min(v, cap))`` 系の素朴なクランプは +inf → +cap、-inf → -cap を
    返してしまう（＝知らない指令で全速前進）。
    """
    out = clamp_body_velocity(bad, 0.0, 0.0)
    assert out == STOP
    assert out != (CAP, 0.0, 0.0)
    assert out != (-CAP, 0.0, 0.0)


@pytest.mark.safety
def test_nan_linear_does_not_pass_through() -> None:
    """NaN は比較が常に False。``if mag > cap:`` 型の実装は NaN を素通しする。

    素通しすると下流（ドライバ/STM32）へ NaN が届く＝暴走。全出力が有限な 0.0 で
    あることまで確認する。
    """
    out = clamp_body_velocity(float("nan"), float("nan"), float("nan"))
    assert out == STOP
    assert all(math.isfinite(v) for v in out)
    assert not any(math.isnan(v) for v in out)


# ── C. cap 以下 / cap ちょうど → 素通し ───────────────────────────────────────


@pytest.mark.safety
@pytest.mark.parametrize(
    ("vx", "vy", "wz"),
    [
        (0.0, 0.0, 0.0),
        (0.05, 0.0, 0.0),
        (0.0, 0.05, 0.0),
        (-0.05, 0.0, 1.0),
        (0.1, 0.1, -1.0),  # mag ≈ 0.1414
        (-0.2, -0.1, 0.25),  # mag ≈ 0.2236
        (0.0, -0.29, 0.0),
        (1e-9, -1e-9, 0.0),
    ],
)
def test_under_cap_passes_through_unchanged(vx: float, vy: float, wz: float) -> None:
    """cap 未満は一切変えない（正規化して cap まで **増速** しない）。

    条件分岐を落として常に ``k = cap / mag`` を掛ける実装は、0.05 m/s の指令を
    0.3 m/s に **増速** する（安全上最悪クラス）。ここが落とす。
    """
    assert clamp_body_velocity(vx, vy, wz) == (vx, vy, wz)


# hypot が **厳密に** cap になる境界ペア（0.18^2 + 0.24^2 = 0.09 = 0.3^2、3-4-5）。
_EXACT_BOUNDARY = [
    (CAP, 0.0),
    (-CAP, 0.0),
    (0.0, CAP),
    (0.0, -CAP),
    (0.18, 0.24),
    (0.24, 0.18),
    (-0.18, 0.24),
    (0.18, -0.24),
    (-0.24, -0.18),
]


@pytest.mark.safety
@pytest.mark.parametrize(("vx", "vy"), _EXACT_BOUNDARY)
def test_exactly_at_cap_is_not_altered(vx: float, vy: float) -> None:
    """``mag == cap`` ちょうどは許容（``<=``）。**厳密に**入力と一致すること。

    ``<`` と ``<=`` の取り違え、および「境界を超過扱いして安全マージンで縮める」
    実装（``k = cap * 0.99 / mag`` 等）をここで落とす。近似ではなく厳密比較。
    """
    assert _mag(vx, vy) == CAP  # オラクル前提: 境界値が浮動小数で厳密に cap
    assert clamp_body_velocity(vx, vy, 0.0) == (vx, vy, 0.0)


@pytest.mark.safety
def test_just_below_and_just_above_cap() -> None:
    """境界近傍にデッドバンドを作らない（``>=``/``>`` のズレを大きさで検出）。"""
    below = CAP - 1e-6
    out_below = clamp_body_velocity(below, 0.0, 0.0)
    assert out_below == (below, 0.0, 0.0)

    above = CAP + 1e-6
    out_above = clamp_body_velocity(above, 0.0, 0.0)
    assert out_above[0] == pytest.approx(CAP, rel=1e-12)
    assert _mag(out_above[0], out_above[1]) <= CAP + TOL


# ── D. ベクトルクランプ（本命: 軸独立クランプを落とす） ───────────────────────


@pytest.mark.safety
def test_diagonal_over_cap_scales_vector_not_per_axis() -> None:
    """``(0.3, 0.3)`` は **軸ごと** に見れば両方 cap 以下だが、合成は 0.424 m/s。

    期待値は手計算の worked example: ``k = cap / (cap*sqrt(2)) = 1/sqrt(2)`` より
    ``(cap/sqrt(2), cap/sqrt(2)) ≈ (0.2121, 0.2121)``。軸独立クランプ実装
    （``(clamp(vx), clamp(vy))`` = ``(0.3, 0.3)`` をそのまま返す）を確実に落とす。
    """
    out = clamp_body_velocity(CAP, CAP, 0.0)
    expected = CAP / math.sqrt(2.0)  # ≈ 0.21213203435596423

    assert out[0] == pytest.approx(expected, rel=1e-12)
    assert out[1] == pytest.approx(expected, rel=1e-12)
    assert out[2] == 0.0
    # 軸独立クランプなら (0.3, 0.3) のまま = 合成 0.424 m/s で cap 超過。
    assert out[:2] != (CAP, CAP)
    assert _mag(out[0], out[1]) == pytest.approx(CAP, rel=1e-12)


@pytest.mark.safety
@pytest.mark.parametrize(
    ("vx", "vy"),
    [
        (CAP, CAP),  # mag ≈ 0.4243
        (0.25, 0.25),  # mag ≈ 0.3536
        (0.2, 0.29),  # mag ≈ 0.3523
        (-CAP, CAP),
        (CAP, -CAP),
        (-0.2, -0.25),  # mag ≈ 0.3202
        (0.29, 0.29),
    ],
)
def test_axis_independent_clamp_is_rejected(vx: float, vy: float) -> None:
    """各成分は cap 以下だが合成が cap 超過のケース群。

    軸独立クランプ（各軸を個別に ±cap へ丸める）実装はこれらを **無変更** で通す
    ため、合成の大きさが cap を超えたままになる。合成が cap になることを要求する。
    """
    assert _mag(vx, vy) > CAP  # オラクル前提: 入力は合成で超過している
    assert max(abs(vx), abs(vy)) <= CAP  # かつ軸ごとには超過していない
    out = clamp_body_velocity(vx, vy, 0.0)
    assert _mag(out[0], out[1]) == pytest.approx(CAP, rel=1e-12)


@pytest.mark.safety
@pytest.mark.parametrize(
    ("vx", "vy", "exp_x", "exp_y"),
    [
        # 3-4-5 三角形: mag=1.0 → k=cap → (0.6*cap, 0.8*cap) = (0.18, 0.24)
        (0.6, 0.8, 0.6 * CAP, 0.8 * CAP),
        (-0.6, 0.8, -0.6 * CAP, 0.8 * CAP),
        (0.6, -0.8, 0.6 * CAP, -0.8 * CAP),
        (-0.6, -0.8, -0.6 * CAP, -0.8 * CAP),
        (0.8, 0.6, 0.8 * CAP, 0.6 * CAP),
        # mag=10.0 → 同じ単位ベクトル → 同じ結果（大きさ非依存で方向だけ効く）
        (6.0, 8.0, 0.6 * CAP, 0.8 * CAP),
        (-8.0, 6.0, -0.8 * CAP, 0.6 * CAP),
    ],
)
def test_worked_examples_3_4_5(vx: float, vy: float, exp_x: float, exp_y: float) -> None:
    """手計算 worked example（全4象限＋軸入替）。

    ``(0.6, 0.8)`` は mag=1.0 の単位ベクトルなので、期待値はそのまま ``cap`` 倍＝
    ``(0.18, 0.24)``。実装から再導出していない独立オラクル。
    """
    out = clamp_body_velocity(vx, vy, 0.0)
    assert out[0] == pytest.approx(exp_x, rel=1e-12)
    assert out[1] == pytest.approx(exp_y, rel=1e-12)


@pytest.mark.safety
@pytest.mark.parametrize(
    ("vx", "vy", "exp_x", "exp_y"),
    [
        (5.0, 0.0, CAP, 0.0),
        (-5.0, 0.0, -CAP, 0.0),
        (0.0, 5.0, 0.0, CAP),
        (0.0, -5.0, 0.0, -CAP),
        (1e6, 0.0, CAP, 0.0),
        (0.0, -1e6, 0.0, -CAP),
    ],
)
def test_single_axis_over_cap_scales_to_cap(
    vx: float, vy: float, exp_x: float, exp_y: float
) -> None:
    """軸単独の超過（``vy`` 単独＝メカナム横行を含む）。

    ``mag`` を ``abs(vx)`` だけで計算する実装は ``(0.0, 5.0)`` を素通しする＝
    横方向 5 m/s。ここで落ちる。
    """
    out = clamp_body_velocity(vx, vy, 0.0)
    assert out[0] == pytest.approx(exp_x, rel=1e-12)
    assert out[1] == pytest.approx(exp_y, rel=1e-12)


# ── E. 方向保存 ──────────────────────────────────────────────────────────────

_ANGLES = [i * math.pi / 8.0 for i in range(-8, 8)]  # 全4象限 + 軸上を 16 分割


@pytest.mark.safety
@pytest.mark.parametrize("angle", _ANGLES)
@pytest.mark.parametrize("mag", [0.3000001, 0.5, 1.0, 7.5, 1000.0])
def test_direction_preserved_when_scaled(angle: float, mag: float) -> None:
    """スケール後も atan2 の角度が不変・成分の符号も不変・大きさは cap。

    成分の入替え（``return (out_y, out_x, wz)``）や、片軸だけスケールする実装、
    絶対値化してしまう実装をまとめて落とす。
    """
    vx, vy = mag * math.cos(angle), mag * math.sin(angle)
    out = clamp_body_velocity(vx, vy, 0.0)

    assert _mag(out[0], out[1]) == pytest.approx(CAP, rel=1e-12)
    assert _angle(out[0], out[1]) == pytest.approx(_angle(vx, vy), abs=1e-9)
    assert math.copysign(1.0, out[0]) == math.copysign(1.0, vx)
    assert math.copysign(1.0, out[1]) == math.copysign(1.0, vy)


@pytest.mark.safety
@pytest.mark.parametrize(
    ("vx", "vy"),
    [(1.0, 2.0), (3.0, -1.5), (-4.0, 0.5), (-2.0, -6.0), (0.5, 0.4)],
)
def test_component_ratio_preserved(vx: float, vy: float) -> None:
    """``vy/vx`` の比が保たれる（＝方向保存の別表現）。"""
    out = clamp_body_velocity(vx, vy, 0.0)
    assert out[1] / out[0] == pytest.approx(vy / vx, rel=1e-9)


# ── F. ゼロ・ゼロ除算 ────────────────────────────────────────────────────────


@pytest.mark.safety
@pytest.mark.parametrize("wz", [0.0, 1.0, -2.5])
def test_zero_linear_does_not_divide_by_zero(wz: float) -> None:
    """``(0, 0)`` でゼロ除算しない（``ZeroDivisionError`` なら test error）。"""
    out = clamp_body_velocity(0.0, 0.0, wz)
    assert out == (0.0, 0.0, wz)
    assert all(math.isfinite(v) for v in out)


@pytest.mark.safety
def test_negative_zero_linear_is_safe() -> None:
    """``-0.0`` 入力でも例外を出さず、大きさ 0 のまま。"""
    out = clamp_body_velocity(-0.0, -0.0, 0.0)
    assert _mag(out[0], out[1]) == 0.0
    assert all(math.isfinite(v) for v in out)


# ── G. wz 素通し ─────────────────────────────────────────────────────────────


@pytest.mark.safety
@pytest.mark.parametrize("wz", [0.0, 0.5, -0.5, 1.0, 99.0, -99.0, 1000.0])
def test_wz_passes_through_when_linear_is_untouched(wz: float) -> None:
    """線速度が cap 以下のとき ``wz`` は厳密に素通し（このスライスでは無制限）。

    ``wz`` に線速度 cap を適用してしまう実装（99.0 → 0.3）を落とす。
    """
    out = clamp_body_velocity(0.1, 0.0, wz)
    assert out[2] == wz


@pytest.mark.safety
@pytest.mark.parametrize("wz", [0.0, 0.5, -0.5, 99.0, -99.0])
def test_wz_unchanged_when_linear_is_clamped(wz: float) -> None:
    """線速度がスケールされても ``wz`` は変わらない。

    スケール係数 ``k`` を ``wz`` にも掛ける実装（99.0 → 5.94）を落とす。
    """
    out = clamp_body_velocity(3.0, 4.0, wz)  # mag=5.0 → k=cap/5
    assert out[2] == wz
    assert _mag(out[0], out[1]) == pytest.approx(CAP, rel=1e-12)


# ── H. プロパティ（固定 seed の決定的サンプル。hypothesis は依存に無い） ──────


@pytest.mark.safety
def test_output_magnitude_never_exceeds_cap() -> None:
    """本質 invariant: 任意の有限入力に対し出力の合成速度 ≤ cap + 1e-9。

    大きさは 1e-9 〜 1e9 の対数一様、角度は全周。出力は常に有限であることも要求
    （NaN/inf を下流に流さない）。
    """
    rng = random.Random(20260806)
    for vx, vy, wz in _finite_samples(rng, 2000, lo_exp=-9.0, hi_exp=9.0):
        out = clamp_body_velocity(vx, vy, wz)
        assert all(math.isfinite(v) for v in out), (vx, vy, wz, out)
        assert _mag(out[0], out[1]) <= CAP + TOL, (vx, vy, out)


@pytest.mark.safety
def test_random_invariants_passthrough_scale_direction_and_wz() -> None:
    """cap 以下＝厳密素通し / cap 超過＝大きさ cap かつ角度不変 / wz は常に不変。"""
    rng = random.Random(4242)
    seen_under = seen_over = 0
    for vx, vy, wz in _finite_samples(rng, 1500, lo_exp=-3.0, hi_exp=3.0):
        out = clamp_body_velocity(vx, vy, wz)
        assert out[2] == wz, (vx, vy, wz, out)

        if _mag(vx, vy) <= CAP:
            seen_under += 1
            assert out[0] == pytest.approx(vx, abs=1e-15, rel=1e-15)
            assert out[1] == pytest.approx(vy, abs=1e-15, rel=1e-15)
        else:
            seen_over += 1
            assert _mag(out[0], out[1]) == pytest.approx(CAP, rel=1e-12), (vx, vy, out)
            assert _angle(out[0], out[1]) == pytest.approx(_angle(vx, vy), abs=1e-9)

    # サンプルが片側に偏っていない（テスト自身の健全性チェック）。
    assert seen_under > 100
    assert seen_over > 100


@pytest.mark.safety
def test_clamp_is_idempotent() -> None:
    """``clamp(clamp(x)) == clamp(x)``（2度掛けても減速しない＝過剰スケール検出）。"""
    rng = random.Random(31337)
    for vx, vy, wz in _finite_samples(rng, 500, lo_exp=-2.0, hi_exp=4.0):
        once = clamp_body_velocity(vx, vy, wz)
        twice = clamp_body_velocity(*once)
        assert twice[0] == pytest.approx(once[0], rel=1e-12, abs=1e-15)
        assert twice[1] == pytest.approx(once[1], rel=1e-12, abs=1e-15)
        assert twice[2] == once[2]


@pytest.mark.safety
@pytest.mark.parametrize(
    ("vx", "vy"),
    [(1e150, 0.0), (0.0, 1e200), (1e200, 1e200), (-1e300, 1e300), (1e308, 0.0)],
)
def test_extreme_finite_inputs_do_not_raise_and_stay_capped(vx: float, vy: float) -> None:
    """極大だが **有限** な入力で例外を出さず、cap を超えない。

    ``math.sqrt(vx**2 + vy**2)`` 型の実装は ``1e200**2`` で ``OverflowError`` を
    投げる（``math.hypot`` は投げない）。安全層が落ちる＝クランプが効かないので、
    「例外を出さない」ことをここで固定する。結果は cap までスケール、または
    fail-safe stop のどちらでもよいが、**cap 超過は不可**。
    """
    out = clamp_body_velocity(vx, vy, 0.0)
    assert all(math.isfinite(v) for v in out), out
    assert _mag(out[0], out[1]) <= CAP + TOL, out
