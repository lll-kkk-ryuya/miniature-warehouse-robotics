"""独立オラクルによる 03_Traffic_Signals 二段レート時系列判定の安全 unit（R-26）。

このファイルは **仕様のみ**から書かれている。期待値は合成生成パラメータからの**手計算
リテラル**で、実装の式を再導出しない（doc20 §9 の tautological / impl-coupled 禁止 =
docs/architecture/20-dev-quality-and-testing.md:139）。仕様の出典:

- docs/mode-outdoor/04-perception-sidewalk-and-signals.md:85-88（出力契約表: GREEN は
  時間窓多数決 + ROI 整合 + 鮮度／`GREEN_FLASHING` は 0.5 s = 2 Hz／`UNKNOWN` はそれ以外
  すべて）・:90（GREEN 遷移のみ非対称に厳しく・**GREEN 離脱は 1 フレーム**）
- 同 :305（一次情報 [D] = 警察庁 仕様書 0.5 s）・:306（10 Hz と 2 Hz は整数比で位相が
  回らない・duty 未確定）・:307（二段レート・状態決定順序・f_B は非整数比・窓は秒数と
  有効サンプル数下限で定義しフレーム数で定義しない）・:308（露出固定 = `OQ-OD4L`）
- 同 :261（per-frame 5 クラス）・:262（点滅を直接クラス化した公開モデルは無い）
- 同 :379 追補 ③ #1（親 §4 の「N フレーム」は例示 → 8/10 も固定値にしない）・:479 追補 ④ §2 項目 3（証拠の不在を否定と読まない）
  ・:480 同 項目 4（GREEN の直交条件）・:482 同 項目 6（ValidationError を safety loop
  へ持ち込まない）
- 親 §7 P-1: 「青点滅 → 青」「赤 → 青」= **0 件**

**合成列がオラクル**: 日本の公開歩行者用信号データセットは無く（:261）カメラも未購入のため、
真値を持つ時系列はテスト側で生成する（生成器は本ファイル内・実装を一切参照しない）。
node 側は存在しない（本スライスは純ロジックのみ）ので、rclpy 非依存・GREEN 経路 1 か所・
パラメータ既定なしは AST で pin する（test_speed_band_publisher.py と同じ流儀）。
"""

from __future__ import annotations

import ast
import math
import random
from dataclasses import dataclass
from pathlib import Path

import pytest
from warehouse_interfaces.perception import LampEvidence, SignalState
from warehouse_perception.signal_temporal_core import (
    NOMINAL_FLASH_PERIOD_S,
    EvidenceSample,
    FlashDetector,
    LuminanceSample,
    SignalTemporalConfigError,
    SignalTemporalParams,
    SignalWindow,
)

pytestmark = [pytest.mark.unit, pytest.mark.safety]

_CORE_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "ws"
    / "src"
    / "warehouse_perception"
    / "warehouse_perception"
    / "signal_temporal_core.py"
)

# ─────────────────────────────────────────────────────────────────────────────
# 合成生成器（テスト側のオラクル）
#
# 真値 = 灯器の状態セグメント列。両レートは**同じ真値**から標本化するので、レート A と
# レート B は物理的に矛盾しない（分類器が消灯相を GREEN と誤る `liar` だけが例外で、
# これは :307 が名指しする既知の誤り経路）。
# ─────────────────────────────────────────────────────────────────────────────

_GREEN = "green"  # 定常青
_RED = "red"  # 定常赤
_FLASH = "flash"  # 青点滅（周期 NOMINAL_FLASH_PERIOD_S）
_DARK = "dark"  # 消灯（灯器は見えているが点いていない）
_HIDDEN = "hidden"  # 遮蔽・欠落（NOT_VISIBLE）
_KINDS = (_GREEN, _RED, _FLASH, _DARK, _HIDDEN)

_LIT_RATIO = 0.90  # 点灯時の緑面積比（on_threshold 0.6 より十分上）
_UNLIT_RATIO = 0.05  # 消灯時の緑面積比（off_threshold 0.2 より十分下）
_RED_LIT_RATIO = 0.85  # 赤点灯時の赤面積比
_RED_BASE_RATIO = 0.02  # 赤が点いていないときの赤面積比
_BASE_EXPOSURE = 0.5


@dataclass(frozen=True)
class _Segment:
    kind: str
    start_s: float
    end_s: float


def _kind_at(segments: list[_Segment], t: float) -> str:
    for seg in segments:
        if seg.start_s <= t < seg.end_s:
            return seg.kind
    return segments[-1].kind


def _lit_at(segments: list[_Segment], t: float, duty: float) -> bool:
    """真値: その瞬間、緑灯が点いているか。"""
    kind = _kind_at(segments, t)
    if kind == _GREEN:
        return True
    if kind == _FLASH:
        return math.fmod(t, NOMINAL_FLASH_PERIOD_S) < duty * NOMINAL_FLASH_PERIOD_S
    return False


def _luminance(
    segments: list[_Segment],
    *,
    end_s: float,
    span_s: float,
    fps: float,
    duty: float = 0.5,
    jitter_s: float = 0.0,
    exposure_drift_s: float = 0.0,
    rng: random.Random | None = None,
) -> list[LuminanceSample]:
    """レート A（ROI 輝度サンプラ・カメラ fps）を真値から標本化する。"""
    out: list[LuminanceSample] = []
    count = int(round(span_s * fps))
    for k in range(1, count + 1):
        t = end_s - span_s + k / fps
        shake = rng.uniform(-jitter_s, jitter_s) if (rng is not None and jitter_s) else 0.0
        lit = _lit_at(segments, t, duty)
        kind = _kind_at(segments, t)
        out.append(
            LuminanceSample(
                stamp_s=t + shake,
                green_ratio=_LIT_RATIO if lit else _UNLIT_RATIO,
                red_ratio=_RED_LIT_RATIO if kind == _RED else _RED_BASE_RATIO,
                exposure=_BASE_EXPOSURE + exposure_drift_s * k,
            )
        )
    return out


def _evidence_at(segments: list[_Segment], t: float, duty: float, liar: bool) -> LampEvidence:
    """真値 → 1 フレームの分類器証拠（:261 の 5 クラス）。"""
    kind = _kind_at(segments, t)
    if kind == _RED:
        return LampEvidence.RED
    if kind == _HIDDEN:
        return LampEvidence.NOT_VISIBLE
    if kind == _GREEN:
        return LampEvidence.GREEN
    if kind == _DARK:
        return LampEvidence.OFF_OR_UNLIT
    # _FLASH: 正直な分類器は消灯相を OFF_OR_UNLIT と出す。`liar` は :307 が名指しする
    # 「分類器が消灯相を GREEN と出す誤り」経路。
    if _lit_at(segments, t, duty) or liar:
        return LampEvidence.GREEN
    return LampEvidence.OFF_OR_UNLIT


def _evidence(
    segments: list[_Segment],
    *,
    end_s: float,
    span_s: float,
    hz: float,
    duty: float = 0.5,
    liar: bool = False,
    roi_consistent: bool = True,
    jitter_s: float = 0.0,
    rng: random.Random | None = None,
) -> list[EvidenceSample]:
    """レート B（分類器・f_B）を真値から標本化する。"""
    out: list[EvidenceSample] = []
    count = int(round(span_s * hz))
    for k in range(1, count + 1):
        t = end_s - span_s + k / hz
        shake = rng.uniform(-jitter_s, jitter_s) if (rng is not None and jitter_s) else 0.0
        out.append(
            EvidenceSample(
                stamp_s=t + shake,
                evidence=_evidence_at(segments, t, duty, liar),
                roi_consistent=roi_consistent,
            )
        )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# テスト固定のパラメータ（**実装の既定ではない**: 既定は存在しない）
#
# 手計算の前提: 窓 = (8.0, 10.0]・fps 30 → レート A 60 本・f_B 9 Hz → レート B 18 本。
# majority_fraction 0.8 は親 §4:85 の 8/10 を写した値。:379 追補 ③ #1 が「N フレーム」を
# 例示と明示したので、そこに乗る share も**固定値として扱わない**（既定は存在しない）。
# ─────────────────────────────────────────────────────────────────────────────

_END_S = 10.0
_SPAN_S = 2.0
_FPS = 30.0
_FB_HZ = 9.0
_LUM_COUNT = 60  # 2.0 s × 30 fps
_EV_COUNT = 18  # 2.0 s × 9 Hz


def _params(**overrides: object) -> SignalTemporalParams:
    base: dict[str, object] = {
        "window_span_s": _SPAN_S,
        "min_samples": 12,
        "majority_fraction": 0.8,
        "classifier_rate_hz": _FB_HZ,
        "period_tolerance_s": 0.08,
        "on_threshold": 0.6,
        "off_threshold": 0.2,
        "exposure_tolerance": 0.05,
        "red_sync_delta": 0.10,
        "min_rising_edges": 2,
        "min_luminance_samples": 10,
    }
    base.update(overrides)
    return SignalTemporalParams(**base)  # type: ignore[arg-type]


def _whole_window(kind: str) -> list[_Segment]:
    return [_Segment(kind, _END_S - _SPAN_S, _END_S + 1.0)]


def _observe(
    luminance: list[LuminanceSample],
    evidence: list[EvidenceSample],
    *,
    params: SignalTemporalParams | None = None,
    window_end_s: float = _END_S,
    frame_digest: str | None = None,
    device_frame_seq: int | None = None,
    processing_latency_s: float | None = None,
):
    window = SignalWindow(params or _params(), "xw-shibuya-01")
    return window.evaluate(
        window_end_s=window_end_s,
        luminance=luminance,
        evidence=evidence,
        frame_digest=frame_digest,
        device_frame_seq=device_frame_seq,
        processing_latency_s=processing_latency_s,
    )


# ──────────────────────────────────────────── 1. 真理表（04:307 の順序 + 04:90）


def test_steady_green_window_is_green() -> None:
    """定常 GREEN（OFF 0 件・ROI 整合・サンプル数十分）→ GREEN。"""
    segments = _whole_window(_GREEN)
    obs = _observe(
        _luminance(segments, end_s=_END_S, span_s=_SPAN_S, fps=_FPS),
        _evidence(segments, end_s=_END_S, span_s=_SPAN_S, hz=_FB_HZ),
    )
    assert obs is not None
    assert obs.state is SignalState.GREEN
    # 手計算リテラル: 2.0 s × 9 Hz = 18 本・消灯相 0 件・全数 NOT_VISIBLE でない。
    assert obs.sample_count == _EV_COUNT
    assert obs.off_phase_count == 0
    assert obs.quality.valid_fraction == pytest.approx(1.0)
    # 連続点灯は「点滅していない」の積極証拠（None ではない）。
    assert obs.is_flashing is False
    assert obs.measured_period_s is None
    assert obs.roi_consistent is True
    assert obs.window_span_s == pytest.approx(_SPAN_S)
    # 窓内最新証拠の元計測時刻（付け直し禁止・04:177）= 8.0 + 18/9 = 10.0。
    assert obs.source_stamp_s == pytest.approx(_END_S)


def test_steady_green_with_one_off_sample_is_unknown() -> None:
    """窓内 OFF 相 1 件 → UNKNOWN（GREEN の直交条件・04:307 / :480）。

    他の AND 項（多数決 17/18 = 0.944 >= 0.8・ROI 整合・is_flashing False・最新 GREEN・
    サンプル数 18 >= 12）はすべて成立させ、**OFF 1 件だけ**で GREEN を落とす。
    """
    segments = _whole_window(_GREEN)
    evidence = _evidence(segments, end_s=_END_S, span_s=_SPAN_S, hz=_FB_HZ)
    middle = evidence[9]
    evidence[9] = EvidenceSample(
        stamp_s=middle.stamp_s,
        evidence=LampEvidence.OFF_OR_UNLIT,
        roi_consistent=True,
    )
    obs = _observe(_luminance(segments, end_s=_END_S, span_s=_SPAN_S, fps=_FPS), evidence)
    assert obs is not None
    assert obs.state is SignalState.UNKNOWN
    assert obs.off_phase_count == 1
    assert obs.sample_count == _EV_COUNT
    assert obs.is_flashing is False
    # 消灯相は「見えた」観測（遮蔽ではない）＝有効側に数える（:261 / 暫定 OQ-OD4Z-f）。
    assert obs.quality.valid_fraction == pytest.approx(1.0)


def test_steady_red_window_is_red() -> None:
    """定常 RED → RED（多数決 18/18）。"""
    segments = _whole_window(_RED)
    obs = _observe(
        _luminance(segments, end_s=_END_S, span_s=_SPAN_S, fps=_FPS),
        _evidence(segments, end_s=_END_S, span_s=_SPAN_S, hz=_FB_HZ),
    )
    assert obs is not None
    assert obs.state is SignalState.RED
    assert obs.sample_count == _EV_COUNT
    assert obs.off_phase_count == 0
    # 緑が一度も点いていない窓は「点滅していない」を**主張できない**（消灯相を切り取った
    # だけかもしれない）→ 判定不能（04:479）。
    assert obs.is_flashing is None


def test_flashing_50_percent_duty_is_green_flashing_with_measured_period() -> None:
    """点滅（duty 50 %）→ GREEN_FLASHING・周期 0.5 s（手計算リテラル）。

    真値の立ち上がりは t = 8.5 / 9.0 / 9.5 / 10.0（t mod 0.5 == 0）。fps 30 の標本格子
    8.0 + k/30 はこの 4 点を厳密に含む（k = 15 / 30 / 45 / 60）ので、立ち上がりエッジ
    間隔は厳密に 0.5 s・その中央値も 0.5 s。
    """
    segments = _whole_window(_FLASH)
    obs = _observe(
        _luminance(segments, end_s=_END_S, span_s=_SPAN_S, fps=_FPS, duty=0.5),
        _evidence(segments, end_s=_END_S, span_s=_SPAN_S, hz=_FB_HZ, duty=0.5),
    )
    assert obs is not None
    assert obs.state is SignalState.GREEN_FLASHING
    assert obs.is_flashing is True
    assert obs.measured_period_s == pytest.approx(0.5, abs=1e-9)
    assert obs.measured_period_s == pytest.approx(NOMINAL_FLASH_PERIOD_S, abs=0.08)


def test_flashing_beats_classifier_green_majority() -> None:
    """**罠**: duty 75 % + 分類器が消灯相を GREEN と出す → レート B は満票 GREEN。

    それでもレート A が勝って GREEN_FLASHING（04:307 の順序: `is_flashing` が先）。
    GREEN の他の AND 項がすべて成立していることを明示し、「10 Hz 多数決だけなら青に
    なる」窓（:306）をこのテストが実際に踏んでいることを示す。
    """
    segments = _whole_window(_FLASH)
    evidence = _evidence(segments, end_s=_END_S, span_s=_SPAN_S, hz=_FB_HZ, duty=0.75, liar=True)
    green_share = sum(1 for s in evidence if s.evidence is LampEvidence.GREEN) / len(evidence)
    assert green_share == pytest.approx(1.0)  # 多数決 1.0 >= 0.8
    obs = _observe(
        _luminance(segments, end_s=_END_S, span_s=_SPAN_S, fps=_FPS, duty=0.75),
        evidence,
    )
    assert obs is not None
    # GREEN の直交条件はすべて成立している ── 落としているのはレート A ただ 1 つ。
    assert obs.off_phase_count == 0
    assert obs.roi_consistent is True
    assert obs.sample_count == _EV_COUNT >= 12
    assert evidence[-1].evidence is LampEvidence.GREEN
    assert obs.state is SignalState.GREEN_FLASHING
    assert obs.measured_period_s == pytest.approx(0.5, abs=1e-9)


def test_same_evidence_without_rate_a_is_unknown_not_green() -> None:
    """同じレート B（満票 GREEN）でもレート A 不在なら UNKNOWN（fail-closed・04:479）。

    直前のテストと対にして「多数決だけを防波堤にしない」（:306）を両側から示す。
    """
    segments = _whole_window(_FLASH)
    evidence = _evidence(segments, end_s=_END_S, span_s=_SPAN_S, hz=_FB_HZ, duty=0.75, liar=True)
    obs = _observe([], evidence)
    assert obs is not None
    assert obs.is_flashing is None
    assert obs.state is SignalState.UNKNOWN


def test_undeterminable_flash_with_green_majority_is_unknown() -> None:
    """`is_flashing=None`（エッジ不足）∧ GREEN 多数 → UNKNOWN（fail-closed）。

    窓の前半だけ消灯 → 立ち上がりエッジ 1 本 < min_rising_edges 2 → 判定不能。
    """
    segments = [_Segment(_DARK, 8.0, 8.4), _Segment(_GREEN, 8.4, 11.0)]
    luminance = _luminance(segments, end_s=_END_S, span_s=_SPAN_S, fps=_FPS)
    # レート B は全数 GREEN に固定（多数決 1.0）＝落とすのはレート A だけ。
    evidence = _evidence(_whole_window(_GREEN), end_s=_END_S, span_s=_SPAN_S, hz=_FB_HZ)
    obs = _observe(luminance, evidence)
    assert obs is not None
    assert obs.is_flashing is None
    assert obs.off_phase_count == 0
    assert obs.state is SignalState.UNKNOWN


def test_exposure_drift_makes_flash_undeterminable() -> None:
    """露出が窓内で動いたら判定不能（`OQ-OD4L` = 04:308 / :349）。

    自動露出と LED PWM のエイリアシングが偽 OFF を作るため、定常青の窓でも
    `is_flashing` を名乗らせない → GREEN は出ない。
    """
    segments = _whole_window(_GREEN)
    luminance = _luminance(
        segments,
        end_s=_END_S,
        span_s=_SPAN_S,
        fps=_FPS,
        exposure_drift_s=0.01,  # 60 本で 0.60 の幅 > exposure_tolerance 0.05
    )
    obs = _observe(luminance, _evidence(segments, end_s=_END_S, span_s=_SPAN_S, hz=_FB_HZ))
    assert obs is not None
    assert obs.is_flashing is None
    assert obs.state is SignalState.UNKNOWN


@pytest.mark.parametrize(
    ("plurality", "filler"),
    [
        (LampEvidence.GREEN, LampEvidence.NOT_VISIBLE),
        (LampEvidence.RED, LampEvidence.NOT_VISIBLE),
    ],
)
def test_plurality_below_the_majority_decides_nothing(
    plurality: LampEvidence, filler: LampEvidence
) -> None:
    """12/18 = 0.667 は「多数決」ではない ── 過半数ではなく **majority_fraction** で決める。

    `majority_fraction` は 8/10 を写した 0.8（N が例示 = :379 追補 ③ #1 ゆえ share も凍結しない）。0.5 超の相対多数
    で GREEN / RED を名乗らせると、票割れした窓が判定に化ける。GREEN 側は他の AND 項
    （OFF 0 件・ROI 整合・最新 GREEN・サンプル数 18 >= 12・is_flashing False）をすべて
    成立させてあるので、落としているのは多数決ただ 1 つ。
    """
    segments = _whole_window(_GREEN)
    base = _evidence(segments, end_s=_END_S, span_s=_SPAN_S, hz=_FB_HZ)
    evidence = [
        EvidenceSample(
            stamp_s=sample.stamp_s,
            evidence=filler if index < 6 else plurality,
            roi_consistent=True,
        )
        for index, sample in enumerate(base)
    ]
    share = sum(1 for s in evidence if s.evidence is plurality) / len(evidence)
    assert 0.5 < share < 0.8  # 相対多数だが majority_fraction には届かない
    obs = _observe(_luminance(segments, end_s=_END_S, span_s=_SPAN_S, fps=_FPS), evidence)
    assert obs is not None
    assert obs.state is SignalState.UNKNOWN
    assert obs.off_phase_count == 0
    assert obs.quality.valid_fraction == pytest.approx(12 / 18)


def test_all_dark_window_cannot_claim_not_flashing() -> None:
    """緑が一度も点いていない窓は「点滅していない」を主張できない（判定不能）。

    窓が点滅の消灯相を切り取っただけかもしれないため。`False`（= 点滅なしの積極証拠）を
    名乗れるのは**連続点灯**の窓だけ ── 証拠の不在を否定と読まない（04:479）。
    """
    dark = [
        LuminanceSample(
            stamp_s=_END_S - _SPAN_S + k / _FPS,
            green_ratio=_UNLIT_RATIO,
            red_ratio=_RED_BASE_RATIO,
            exposure=_BASE_EXPOSURE,
        )
        for k in range(1, _LUM_COUNT + 1)
    ]
    verdict = FlashDetector(_params()).verdict(dark)
    assert verdict.is_flashing is None
    assert verdict.measured_period_s is None

    lit = [
        LuminanceSample(
            stamp_s=sample.stamp_s,
            green_ratio=_LIT_RATIO,
            red_ratio=_RED_BASE_RATIO,
            exposure=_BASE_EXPOSURE,
        )
        for sample in dark
    ]
    assert FlashDetector(_params()).verdict(lit).is_flashing is False


def test_red_rising_in_sync_denies_flash_verdict() -> None:
    """赤が緑と同期して増える窓は点滅と認めない（04:307 の AND 項）。

    周期は 0.5 s ちょうどでも、全体照度の変動疑いがあるため判定不能に倒す。
    """
    params = _params()
    samples = []
    for k in range(1, _LUM_COUNT + 1):
        t = _END_S - _SPAN_S + k / _FPS
        lit = math.fmod(t, NOMINAL_FLASH_PERIOD_S) < 0.5 * NOMINAL_FLASH_PERIOD_S
        samples.append(
            LuminanceSample(
                stamp_s=t,
                green_ratio=_LIT_RATIO if lit else _UNLIT_RATIO,
                # 赤も同位相で 0.30 上がる > red_sync_delta 0.10
                red_ratio=0.35 if lit else 0.05,
                exposure=_BASE_EXPOSURE,
            )
        )
    verdict = FlashDetector(params).verdict(samples)
    assert verdict.is_flashing is None
    # 測った数値は隠さない（拒否の理由が見えるように）。
    assert verdict.measured_period_s == pytest.approx(0.5, abs=1e-9)


def test_alternation_off_the_statutory_period_is_undeterminable() -> None:
    """0.5 s から外れた交番は点滅と認めず、かつ「点滅なし」とも言わない。"""
    params = _params()
    samples = []
    for k in range(1, _LUM_COUNT + 1):
        t = _END_S - _SPAN_S + k / _FPS
        lit = math.fmod(t, 0.2) < 0.1  # 周期 0.2 s（|0.2 - 0.5| = 0.3 > 0.08）
        samples.append(
            LuminanceSample(
                stamp_s=t,
                green_ratio=_LIT_RATIO if lit else _UNLIT_RATIO,
                red_ratio=_RED_BASE_RATIO,
                exposure=_BASE_EXPOSURE,
            )
        )
    verdict = FlashDetector(params).verdict(samples)
    assert verdict.is_flashing is None
    assert verdict.measured_period_s == pytest.approx(0.2, abs=1e-9)


# ──────────────────────────────────────────── 2. GREEN 離脱は 1 フレーム（04:90）


@pytest.mark.parametrize(
    ("last_evidence", "expected_off", "expected_valid_fraction"),
    [
        (LampEvidence.RED, 0, 1.0),
        (LampEvidence.NOT_VISIBLE, 0, 17 / 18),
        (LampEvidence.OFF_OR_UNLIT, 1, 1.0),
    ],
)
def test_green_exits_on_the_latest_single_non_green_sample(
    last_evidence: LampEvidence, expected_off: int, expected_valid_fraction: float
) -> None:
    """GREEN 判定は多数決（17/18 = 0.944 >= 0.8）でも、**最新 1 件**が GREEN でなければ即離脱。

    RED / NOT_VISIBLE の 2 例は `off_phase_count == 0` のまま落ちるので、離脱規則が
    OFF 相条件とは独立に効いていることを示す（04:90 の非対称）。
    """
    segments = _whole_window(_GREEN)
    evidence = _evidence(segments, end_s=_END_S, span_s=_SPAN_S, hz=_FB_HZ)
    evidence[-1] = EvidenceSample(
        stamp_s=evidence[-1].stamp_s, evidence=last_evidence, roi_consistent=True
    )
    obs = _observe(_luminance(segments, end_s=_END_S, span_s=_SPAN_S, fps=_FPS), evidence)
    assert obs is not None
    assert obs.state is SignalState.UNKNOWN
    assert obs.off_phase_count == expected_off
    assert obs.quality.valid_fraction == pytest.approx(expected_valid_fraction)


def test_roi_inconsistent_latest_sample_denies_green() -> None:
    """ROI 整合は GREEN の AND 項（04:85）。最新証拠が ROI 不整合なら GREEN にしない。"""
    segments = _whole_window(_GREEN)
    evidence = _evidence(segments, end_s=_END_S, span_s=_SPAN_S, hz=_FB_HZ)
    evidence[-1] = EvidenceSample(
        stamp_s=evidence[-1].stamp_s, evidence=LampEvidence.GREEN, roi_consistent=False
    )
    obs = _observe(_luminance(segments, end_s=_END_S, span_s=_SPAN_S, fps=_FPS), evidence)
    assert obs is not None
    assert obs.roi_consistent is False
    assert obs.state is SignalState.UNKNOWN


def test_too_few_samples_denies_green() -> None:
    """窓は秒数 **と** 有効サンプル数下限で定義（04:307）。下限未満なら GREEN にしない。"""
    segments = _whole_window(_GREEN)
    full = _evidence(segments, end_s=_END_S, span_s=_SPAN_S, hz=_FB_HZ)
    obs = _observe(
        _luminance(segments, end_s=_END_S, span_s=_SPAN_S, fps=_FPS),
        full[-11:],  # 11 本 < min_samples 12
    )
    assert obs is not None
    assert obs.sample_count == 11
    assert obs.state is SignalState.UNKNOWN


def test_window_is_seconds_not_frames() -> None:
    """窓は `source_stamp` 基準の秒数（04:307 / :379）。窓外のサンプルは数に入らない。

    3.0 s 分のレート B を渡しても、窓 (8.0, 10.0] に入る 18 本だけが数えられる。
    """
    segments = _whole_window(_GREEN)
    wide = _evidence(segments, end_s=_END_S, span_s=3.0, hz=_FB_HZ)
    assert len(wide) == 27
    obs = _observe(_luminance(segments, end_s=_END_S, span_s=_SPAN_S, fps=_FPS), wide)
    assert obs is not None
    assert obs.sample_count == _EV_COUNT
    assert obs.state is SignalState.GREEN


# ──────────────────────────────────────────── 3. 構築時検証（既定なし・整数比の罠）


@pytest.mark.parametrize("rate_hz", [10.0, 12.0, 30.0, 2.0, 4.0])
def test_integer_multiple_classifier_rate_is_refused(rate_hz: float) -> None:
    """f_B が 2 Hz の整数倍なら構築時に `ValueError`（04:307 / :306）。"""
    with pytest.raises(SignalTemporalConfigError):
        _params(classifier_rate_hz=rate_hz)


@pytest.mark.parametrize("rate_hz", [9.0, 7.5, 11.0, 13.0])
def test_non_integer_ratio_classifier_rate_is_accepted(rate_hz: float) -> None:
    """非整数比（9 Hz = 4.5 倍・7.5 Hz = 3.75 倍）は受け付ける。"""
    assert _params(classifier_rate_hz=rate_hz).classifier_rate_hz == pytest.approx(rate_hz)


def test_parameters_have_no_defaults() -> None:
    """パラメータはすべて注入必須 ── 既定値は 1 つも無い（docs が数値を決めていない）。"""
    with pytest.raises(TypeError):
        SignalTemporalParams()  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "overrides",
    [
        {"window_span_s": 0.0},
        {"window_span_s": -1.0},
        {"window_span_s": math.inf},
        {"min_samples": 0},
        {"min_samples": 1.5},
        {"majority_fraction": 0.5},
        {"majority_fraction": 1.5},
        {"period_tolerance_s": 0.0},
        {"period_tolerance_s": NOMINAL_FLASH_PERIOD_S},
        {"on_threshold": 0.2, "off_threshold": 0.6},
        {"on_threshold": 0.5, "off_threshold": 0.5},
        {"on_threshold": 1.5},
        {"exposure_tolerance": -0.01},
        {"red_sync_delta": -0.01},
        {"min_rising_edges": 1},
        {"min_luminance_samples": 1},
        {"classifier_rate_hz": 0.0},
        {"classifier_rate_hz": math.nan},
        {"window_span_s": "2.0"},
        {"majority_fraction": True},
    ],
)
def test_unusable_parameters_are_refused_at_construction(overrides: dict[str, object]) -> None:
    """使えない境界は**構築時に**落とす（judgement の中では落とさない）。"""
    with pytest.raises(SignalTemporalConfigError):
        _params(**overrides)


def test_crossing_id_must_name_a_registered_crossing() -> None:
    """`crossing_id` は構築時に検証（registry の正本は 02・04 は consume するだけ）。"""
    for bad in ("", "   ", None, 7):
        with pytest.raises(SignalTemporalConfigError):
            SignalWindow(_params(), bad)  # type: ignore[arg-type]


# ──────────────────────────────────────────── 4. data では例外を上げない（04:482）


def test_evaluate_never_raises_on_data_anomalies() -> None:
    """NaN 比率・非有限 stamp・逆順・空窓は例外にせず禁止側へ倒す。"""
    segments = _whole_window(_GREEN)
    luminance = _luminance(segments, end_s=_END_S, span_s=_SPAN_S, fps=_FPS)
    evidence = _evidence(segments, end_s=_END_S, span_s=_SPAN_S, hz=_FB_HZ)

    # (a) 非有限 stamp のサンプルは窓に置けない → 落ちるだけ。
    poisoned_stamp = [
        LuminanceSample(stamp_s=math.nan, green_ratio=0.9, red_ratio=0.0, exposure=0.5),
        LuminanceSample(stamp_s=math.inf, green_ratio=0.9, red_ratio=0.0, exposure=0.5),
        *luminance,
    ]
    obs = _observe(poisoned_stamp, evidence)
    assert obs is not None
    assert obs.state is SignalState.GREEN

    # (b) NaN 比率は窓全体を判定不能にする（fail-closed）。
    poisoned_ratio = [
        *luminance,
        LuminanceSample(stamp_s=9.9, green_ratio=math.nan, red_ratio=0.0, exposure=0.5),
    ]
    obs = _observe(poisoned_ratio, evidence)
    assert obs is not None
    assert obs.is_flashing is None
    assert obs.state is SignalState.UNKNOWN

    # (c) 読めない型は NaN 扱い（例外にしない）。
    obs = _observe(
        [
            *luminance,
            LuminanceSample(stamp_s=9.8, green_ratio="bright", red_ratio=None, exposure=0.5),  # type: ignore[arg-type]
        ],
        evidence,
    )
    assert obs is not None
    assert obs.state is SignalState.UNKNOWN

    # (d) 逆順で渡しても結果は同じ（窓は時刻で定義され、到着順ではない）。
    reversed_obs = _observe(list(reversed(luminance)), list(reversed(evidence)))
    assert reversed_obs is not None
    assert reversed_obs.state is SignalState.GREEN

    # (e) 空窓・非有限の窓端 → 観測なし（source_stamp を捏造しない）。
    assert _observe([], []) is None
    assert _observe(luminance, evidence, window_end_s=math.nan) is None
    assert _observe(luminance, evidence, window_end_s=0.0) is None


def test_unusable_quality_plumbing_is_reported_absent_not_raised() -> None:
    """負の latency / 負の frame_seq / 非 str digest は「不在」として運ぶ（例外にしない）。

    契約は負の `processing_latency_s` を `ValidationError` で拒否するが、その例外を
    safety loop へ持ち込まないのは呼び出し側の責務（04:482）。
    """
    segments = _whole_window(_GREEN)
    obs = _observe(
        _luminance(segments, end_s=_END_S, span_s=_SPAN_S, fps=_FPS),
        _evidence(segments, end_s=_END_S, span_s=_SPAN_S, hz=_FB_HZ),
        frame_digest=17,  # type: ignore[arg-type]
        device_frame_seq=-3,
        processing_latency_s=-0.2,
    )
    assert obs is not None
    assert obs.quality.frame_digest is None
    assert obs.quality.device_frame_seq is None
    assert obs.quality.processing_latency_s is None
    assert obs.state is SignalState.GREEN


def test_quality_plumbing_passes_through_unchanged() -> None:
    """使える値はそのまま運ぶ（04 は digest を計算しない ── 画像段が持つ）。"""
    segments = _whole_window(_GREEN)
    obs = _observe(
        _luminance(segments, end_s=_END_S, span_s=_SPAN_S, fps=_FPS),
        _evidence(segments, end_s=_END_S, span_s=_SPAN_S, hz=_FB_HZ),
        frame_digest="sha256:cafe",
        device_frame_seq=4211,
        processing_latency_s=0.031,
    )
    assert obs is not None
    assert obs.quality.frame_digest == "sha256:cafe"
    assert obs.quality.device_frame_seq == 4211
    assert obs.quality.processing_latency_s == pytest.approx(0.031)


def test_observation_carries_no_max_age() -> None:
    """鮮度は消費側の義務（04:382 / :477）── producer は `max_age` を持たない。"""
    segments = _whole_window(_GREEN)
    obs = _observe(
        _luminance(segments, end_s=_END_S, span_s=_SPAN_S, fps=_FPS),
        _evidence(segments, end_s=_END_S, span_s=_SPAN_S, hz=_FB_HZ),
    )
    assert obs is not None
    assert not hasattr(obs, "max_age")
    assert "max_age" not in obs.model_dump()


def test_hidden_window_reports_low_valid_fraction_and_unknown() -> None:
    """全数 NOT_VISIBLE → 有効比 0.0・UNKNOWN（遮蔽を消灯と同一視しない・:261）。"""
    segments = _whole_window(_HIDDEN)
    obs = _observe(
        _luminance(segments, end_s=_END_S, span_s=_SPAN_S, fps=_FPS),
        _evidence(segments, end_s=_END_S, span_s=_SPAN_S, hz=_FB_HZ),
    )
    assert obs is not None
    assert obs.state is SignalState.UNKNOWN
    assert obs.quality.valid_fraction == pytest.approx(0.0)
    assert obs.off_phase_count == 0


# ──────────────────────────────────────────── 5. P-1 property（親 §7）


def _random_segments(rng: random.Random) -> list[_Segment]:
    """窓 (8.0, 10.0] を覆うランダムなセグメント列を作る。

    境界はレート A の格子（1/30 s）にスナップし、各セグメントは観測可能な長さを持つ:
    点滅は **2 周期 = 1.0 s 以上**（短い点滅は ON 相だけを切り取られて原理的に定常青と
    区別できず、判定器の欠陥ではなく生成器の作為になる）、他は 0.2 s 以上。
    """
    start = _END_S - _SPAN_S
    while True:
        count = rng.choice((1, 1, 2, 2, 3))
        kinds = [rng.choice(_KINDS) for _ in range(count)]
        minima = [1.0 if k == _FLASH else 0.2 for k in kinds]
        if sum(minima) <= _SPAN_S:
            break
    slack = _SPAN_S - sum(minima)
    cuts = sorted(rng.uniform(0.0, slack) for _ in range(count - 1))
    extras = [b - a for a, b in zip([0.0, *cuts], [*cuts, slack], strict=True)]
    segments: list[_Segment] = []
    edge = start
    for index, (kind, minimum, extra) in enumerate(zip(kinds, minima, extras, strict=True)):
        raw_stop = edge + minimum + extra
        # 中間境界はレート A の格子へスナップ。最終セグメントは窓端を越えて伸ばす。
        stop = (
            start + round((raw_stop - start) * _FPS) / _FPS if index < count - 1 else _END_S + 1.0
        )
        segments.append(_Segment(kind, edge, stop))
        edge = stop
    return segments


def test_p1_property_flashing_or_red_windows_never_yield_green() -> None:
    """親 §7 P-1: 真値が「点滅」または「赤」を含む窓の出力が GREEN = **0 件**。

    seed 固定・N = 240。duty・jitter・分類器の嘘・露出ノイズをランダムに振る。
    """
    rng = random.Random(20260917)
    trials = 240
    violations: list[tuple[int, list[_Segment]]] = []
    risky = 0
    greens = 0
    states: dict[SignalState, int] = {}

    for trial in range(trials):
        segments = _random_segments(rng)
        duty = rng.choice((0.5, 0.6, 0.7, 0.75))
        jitter = rng.uniform(0.0, 0.3 / _FPS)
        liar = rng.random() < 0.5
        luminance = _luminance(
            segments,
            end_s=_END_S,
            span_s=_SPAN_S,
            fps=_FPS,
            duty=duty,
            jitter_s=jitter,
            rng=rng,
        )
        evidence = _evidence(
            segments,
            end_s=_END_S,
            span_s=_SPAN_S,
            hz=_FB_HZ,
            duty=duty,
            liar=liar,
            jitter_s=jitter,
            rng=rng,
        )
        obs = _observe(luminance, evidence)
        assert obs is not None
        states[obs.state] = states.get(obs.state, 0) + 1
        if obs.state is SignalState.GREEN:
            greens += 1
        truth_is_risky = any(
            seg.kind in (_FLASH, _RED) and seg.start_s < _END_S and seg.end_s > _END_S - _SPAN_S
            for seg in segments
        )
        if truth_is_risky:
            risky += 1
            if obs.state is SignalState.GREEN:
                violations.append((trial, segments))

    assert violations == [], f"P-1 違反 {len(violations)} 件: {violations[:3]}"
    # 非空虚性: 危険な窓を十分踏み、かつ GREEN を出す窓も存在する（常に UNKNOWN を返す
    # 実装ならこの assert が落ちる）。
    assert risky >= 100, f"危険窓が少なすぎる ({risky}/{trials}) — property が空虚"
    assert greens >= 10, f"GREEN が出る窓が少なすぎる ({greens}/{trials}) — property が空虚"
    assert states[SignalState.GREEN_FLASHING] >= 10


# ──────────────────────────────────────────── 6. AST pin（純ロジック境界）


def _core_tree() -> ast.Module:
    return ast.parse(_CORE_SOURCE.read_text(encoding="utf-8"))


def test_core_imports_no_ros_and_no_numpy() -> None:
    """純ロジック境界: rclpy / numpy / ROS msg に依存しない（host で R-26 が回る）。"""
    forbidden = {"rclpy", "numpy", "rosidl_runtime_py", "nav2_msgs", "std_msgs", "sensor_msgs"}
    imported: set[str] = set()
    for node in ast.walk(_core_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & forbidden), f"禁止 import: {sorted(imported & forbidden)}"


def test_core_names_no_actuation_topic() -> None:
    """L4 publish-only: 運動・停止・速度制限のいずれにも触れない（0 actuation）。"""
    source = _CORE_SOURCE.read_text(encoding="utf-8")
    for token in ("cmd_vel", "stop_request", "speed_limit", "stop_state"):
        assert token not in source, f"L4 知覚が {token} に言及している"


def test_green_is_assigned_at_exactly_one_place() -> None:
    """`SignalState.GREEN` を作る経路は 1 か所だけ（迂回路を作らせない）。

    `SignalState.GREEN_FLASHING` / `LampEvidence.GREEN` は別属性・別 enum なので数えない。
    """
    occurrences = [
        node
        for node in ast.walk(_core_tree())
        if isinstance(node, ast.Attribute)
        and node.attr == "GREEN"
        and isinstance(node.value, ast.Name)
        and node.value.id == "SignalState"
    ]
    assert len(occurrences) == 1, f"SignalState.GREEN が {len(occurrences)} か所にある"


def test_parameter_dataclass_declares_no_defaults() -> None:
    """パラメータ dataclass に既定値を置かない（docs が決めていない数値を発明しない）。"""
    (params_class,) = [
        node
        for node in _core_tree().body
        if isinstance(node, ast.ClassDef) and node.name == "SignalTemporalParams"
    ]
    annotated = [node for node in params_class.body if isinstance(node, ast.AnnAssign)]
    assert annotated, "パラメータ field が 1 つも無い"
    for field in annotated:
        name = getattr(field.target, "id", "?")
        assert field.value is None, f"{name} に既定値がある"


def test_only_numeric_module_constant_is_the_nominal_flash_period() -> None:
    """モジュール定数として置いてよい数値は 0.5 s（[D] 04:305）ただ 1 つ。

    しきい値・許容幅・レート・窓長はすべて注入（既定なし）＝コードに数値を焼かない。
    """
    numeric: dict[str, float] = {}
    for node in _core_tree().body:
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)):
            if isinstance(value.value, bool):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    numeric[target.id] = float(value.value)
    assert numeric == {"NOMINAL_FLASH_PERIOD_S": 0.5}, f"想定外の数値定数: {numeric}"
