"""`warehouse_perception.evaluation_core`（10_Evaluation・観測面 offline）の unit。

**仕様のみ**から書く（doc20 §9 独立オラクル＝
docs/architecture/20-dev-quality-and-testing.md:139）。期待値は手作りサンプル列から
**手で数えた／手で計算したリテラル**であり、実装から再導出しない。percentile の期待値も
線形補間の定義から手計算して書く（`eval_sdk.stats.percentile` を呼び直さない）。

仕様の出典:

- docs/mode-outdoor/04-perception-sidewalk-and-signals.md:117（P-1 = 「青点滅→青」
  「赤→青」0 件が必達・`UNKNOWN` 率は運用指標）
- docs/mode-outdoor/04-perception-sidewalk-and-signals.md:263（1 記録 → 1 ラベル →
  N 系統を同一 bag に再生 → 同一混同行列・P-1 を満たさない系統は不採用）
- docs/mode-outdoor/04-perception-sidewalk-and-signals.md:203 / :205（撮影 → 消費の
  遅延を指標に・走行距離当たりの誤停止）
- docs/mode-outdoor/04-perception-sidewalk-and-signals.md:418（負の遅延 = 時計取違え。
  受け入れると死んだ経路が「速い」に見える → 捨てずに別集計する）

layer: 10_Evaluation = **観測面（横断・offline）**。停止・許可・駆動に一切関与しない。
P-1 ゲート関数は「危険な取り違えを 0 件に保つ」判定そのものなので `safety` marker を
併記する（本モジュールは走行時に動かないが、系統の採否を決める根拠になる）。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from warehouse_interfaces.perception import SignalState
from warehouse_perception.evaluation_core import (
    METRIC_LATENCY_MAX,
    METRIC_LATENCY_NEGATIVE,
    METRIC_LATENCY_P50,
    METRIC_LATENCY_P95,
    METRIC_LATENCY_SAMPLES,
    METRIC_P1_GATE_PASS,
    METRIC_P1_VIOLATIONS,
    METRIC_SIGNAL_SAMPLES,
    METRIC_UNKNOWN_RATE,
    EvalSample,
    capture_to_consume_latency,
    compare,
    drive_metrics,
    false_stops_per_km,
    latency_metrics,
    miss_rate_by_distance_band,
    miss_rate_metrics,
    p1_gate,
    p1_violations,
    read_jsonl,
    signal_confusion,
    signal_metrics,
    to_evaluation_record,
    unknown_rate,
)

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]
_MODULE_SOURCE = (
    _REPO / "ws" / "src" / "warehouse_perception" / "warehouse_perception" / "evaluation_core.py"
)

_GREEN = SignalState.GREEN
_FLASH = SignalState.GREEN_FLASHING
_RED = SignalState.RED
_UNKNOWN = SignalState.UNKNOWN


def _sample(truth: str, pred: str, **kwargs: object) -> EvalSample:
    return EvalSample(stamp_s=kwargs.pop("stamp_s", 0.0), truth=truth, pred=pred, **kwargs)


# ------------------------------------------------------------------ 混同行列
def test_confusion_counts_match_a_hand_tally() -> None:
    """手で数えた 7 サンプル: GREEN→GREEN×2 / FLASH→GREEN×1 / RED→RED×2 /
    RED→UNKNOWN×1 / UNKNOWN→UNKNOWN×1。"""
    samples = [
        _sample("GREEN", "GREEN"),
        _sample("GREEN", "GREEN"),
        _sample("GREEN_FLASHING", "GREEN"),
        _sample("RED", "RED"),
        _sample("RED", "RED"),
        _sample("RED", "UNKNOWN"),
        _sample("UNKNOWN", "UNKNOWN"),
    ]

    confusion = signal_confusion(samples)

    assert confusion[(_GREEN, _GREEN)] == 2
    assert confusion[(_FLASH, _GREEN)] == 1
    assert confusion[(_RED, _RED)] == 2
    assert confusion[(_RED, _UNKNOWN)] == 1
    assert confusion[(_UNKNOWN, _UNKNOWN)] == 1
    assert sum(confusion.values()) == 7


def test_confusion_has_all_sixteen_cells_even_when_empty() -> None:
    """キーの不在を「0 件」と読ませない。P-1 の 2 セルは常に存在する。"""
    confusion = signal_confusion([])

    assert len(confusion) == 16
    assert confusion[(_FLASH, _GREEN)] == 0
    assert confusion[(_RED, _GREEN)] == 0
    assert sum(confusion.values()) == 0


def test_confusion_refuses_a_label_outside_signal_state() -> None:
    with pytest.raises(ValueError, match="not a SignalState"):
        signal_confusion([_sample("GREEN", "BLINKING_BLUE")])


# ------------------------------------------------------------------ P-1（safety）
@pytest.mark.safety
def test_p1_counts_both_forbidden_confusions() -> None:
    """FLASH→GREEN が 2 件・RED→GREEN が 3 件 → 手計算で 5 件。"""
    samples = (
        [_sample("GREEN_FLASHING", "GREEN")] * 2
        + [_sample("RED", "GREEN")] * 3
        + [_sample("GREEN", "GREEN")] * 4
        + [_sample("RED", "RED")] * 6
    )

    confusion = signal_confusion(samples)

    assert p1_violations(confusion) == 5
    assert p1_gate(confusion) is False


@pytest.mark.safety
def test_p1_gate_passes_only_with_zero_violations() -> None:
    """危険でない取り違え（RED→UNKNOWN・GREEN→RED 等）は P-1 を割らない。"""
    confusion = signal_confusion(
        [
            _sample("RED", "UNKNOWN"),
            _sample("GREEN", "RED"),
            _sample("GREEN_FLASHING", "RED"),
            _sample("GREEN", "UNKNOWN"),
        ]
    )

    assert p1_violations(confusion) == 0
    assert p1_gate(confusion) is True


@pytest.mark.safety
@pytest.mark.parametrize("truth", ["GREEN_FLASHING", "RED"])
def test_a_single_violation_of_either_pair_fails_the_gate(truth: str) -> None:
    """0 件が必達（04:117）＝どちらのペアも 1 件で不合格。片方だけ数える実装は
    ここで赤くなる（mutation 感度: doc20 §9 規律 2）。"""
    confusion = signal_confusion([_sample(truth, "GREEN")] + [_sample("RED", "RED")] * 99)

    assert p1_violations(confusion) == 1
    assert p1_gate(confusion) is False


@pytest.mark.safety
def test_p1_gate_passes_on_an_empty_bag() -> None:
    """0 サンプルは「違反 0 件」。合格は「評価した」ことを意味しない——
    `signal.sample_count` を並べて読むのは呼び出し側の義務。"""
    assert p1_gate(signal_confusion([])) is True


# ------------------------------------------------------------------ UNKNOWN 率
def test_unknown_rate_is_the_share_of_unknown_predictions() -> None:
    """8 サンプル中、予測が UNKNOWN なのは 2 件 → 手計算 0.25。
    真値が UNKNOWN の行（ラベル不能フレーム）は分母に残す。"""
    samples = (
        [_sample("RED", "UNKNOWN")]
        + [_sample("UNKNOWN", "UNKNOWN")]
        + [_sample("GREEN", "GREEN")] * 3
        + [_sample("RED", "RED")] * 3
    )

    assert unknown_rate(signal_confusion(samples)) == pytest.approx(0.25)


def test_unknown_rate_is_none_without_samples() -> None:
    """None は 0.0 ではない（追補 ④ §2-3 の読み方）。"""
    assert unknown_rate(signal_confusion([])) is None


# ------------------------------------------------------------------ 距離帯別 miss
_BANDS = [("0-5m", 0.0, 5.0), ("5-15m", 5.0, 15.0)]


def test_miss_rate_by_band_matches_a_hand_tally() -> None:
    """近帯 4 件中 1 件 miss → 0.25／遠帯 2 件中 1 件 miss → 0.5（手計算）。
    miss = 真値が確立しているのに予測が UNKNOWN。取り違え（RED→GREEN）は miss でなく
    混同行列側の事象。"""
    samples = [
        _sample("RED", "RED", distance_m=1.0),
        _sample("RED", "UNKNOWN", distance_m=2.0),  # miss（近）
        _sample("GREEN", "GREEN", distance_m=3.0),
        _sample("RED", "GREEN", distance_m=4.0),  # 取り違え = miss ではない
        _sample("GREEN", "UNKNOWN", distance_m=6.0),  # miss（遠）
        _sample("GREEN", "GREEN", distance_m=14.999),
    ]

    report = miss_rate_by_distance_band(samples, _BANDS)

    assert [row.band for row in report.bands] == ["0-5m", "5-15m"]
    assert (report.bands[0].total, report.bands[0].misses) == (4, 1)
    assert report.bands[0].rate == pytest.approx(0.25)
    assert (report.bands[1].total, report.bands[1].misses) == (2, 1)
    assert report.bands[1].rate == pytest.approx(0.5)
    assert report.unbanded_samples == 0
    assert report.out_of_band_samples == 0


def test_unbanded_and_out_of_band_samples_are_counted_not_dropped() -> None:
    samples = [
        _sample("RED", "RED"),  # distance_m 無し
        _sample("RED", "UNKNOWN"),  # distance_m 無し
        _sample("GREEN", "GREEN", distance_m=99.0),  # どの帯にも入らない
        _sample("GREEN", "GREEN", distance_m=1.0),
    ]

    report = miss_rate_by_distance_band(samples, _BANDS)

    assert report.unbanded_samples == 2
    assert report.out_of_band_samples == 1
    assert report.bands[0].total == 1


def test_an_empty_band_reports_none_not_zero() -> None:
    report = miss_rate_by_distance_band([_sample("RED", "RED", distance_m=1.0)], _BANDS)

    assert report.bands[1].total == 0
    assert report.bands[1].rate is None


def test_band_edges_are_lower_inclusive_and_upper_exclusive() -> None:
    report = miss_rate_by_distance_band(
        [
            _sample("RED", "RED", distance_m=0.0),
            _sample("RED", "RED", distance_m=5.0),
        ],
        _BANDS,
    )

    assert (report.bands[0].total, report.bands[1].total) == (1, 1)


def test_missing_labels_can_be_injected_for_a_detector_evaluation() -> None:
    """既定は `{"UNKNOWN"}`（04:117）だが、検出器評価は自分の語彙を注入する。"""
    samples = [
        _sample("person", "none", distance_m=1.0),
        _sample("person", "person", distance_m=2.0),
    ]

    report = miss_rate_by_distance_band(samples, _BANDS, missing_labels={"none"})

    assert (report.bands[0].total, report.bands[0].misses) == (2, 1)
    assert report.bands[0].rate == pytest.approx(0.5)


@pytest.mark.parametrize(
    "bands",
    [
        [],
        [("", 0.0, 1.0)],
        [("a", 0.0, 1.0), ("a", 1.0, 2.0)],
        [("a", 2.0, 1.0)],
        [("a", -1.0, 1.0)],
        [("a", 0.0, 10.0), ("b", 5.0, 20.0)],  # 重なり = 二重計上
        [("a", 0.0, float("inf"))],
    ],
)
def test_malformed_band_tables_are_refused(bands: list) -> None:
    with pytest.raises(ValueError):
        miss_rate_by_distance_band([], bands)


# ------------------------------------------------------------------ 誤停止 / 走行距離
def test_false_stops_per_km_divides_by_the_injected_distance() -> None:
    """3 件 / 1500 m = 2.0 件/km（手計算）。"""
    assert false_stops_per_km(3, 1500.0) == pytest.approx(2.0)


def test_false_stops_per_km_is_none_without_travel() -> None:
    """走行距離 0 に「距離当たり」は無い。0.0 も inf もデータが支えない主張。"""
    assert false_stops_per_km(2, 0.0) is None


@pytest.mark.parametrize(
    ("stops", "travelled"),
    [(-1, 100.0), (0, float("nan")), (1, float("inf"))],
)
def test_false_stops_per_km_refuses_impossible_inputs(stops: int, travelled: float) -> None:
    with pytest.raises(ValueError):
        false_stops_per_km(stops, travelled)


# ------------------------------------------------------------------ 撮影 → 消費の遅延
def _latency_samples(latencies: list[float], base: float = 100.0) -> list[EvalSample]:
    return [
        EvalSample(stamp_s=base, truth="RED", pred="RED", consumed_s=base + latency)
        for latency in latencies
    ]


def test_latency_percentiles_match_the_hand_computed_interpolation() -> None:
    """[0.1, 0.2, 0.3, 0.4]（n=4）。線形補間の定義から手計算:
    p50: rank = 3 × 0.50 = 1.5 → 0.2 + (0.3 − 0.2) × 0.5  = 0.25
    p95: rank = 3 × 0.95 = 2.85 → 0.3 + (0.4 − 0.3) × 0.85 = 0.385
    max = 0.4。"""
    stats = capture_to_consume_latency(_latency_samples([0.3, 0.1, 0.4, 0.2]))

    assert stats.n == 4
    assert stats.p50_s == pytest.approx(0.25)
    assert stats.p95_s == pytest.approx(0.385)
    assert stats.max_s == pytest.approx(0.4)
    assert stats.negative_count == 0
    assert stats.missing_consumed == 0


def test_negative_latencies_are_counted_separately_and_excluded_from_percentiles() -> None:
    """負の遅延は時計取違え（04:418）。捨てずに数え、統計には入れない——
    入れれば死んだ経路が「速い」に見え、黙って捨てれば同じ故障が隠れる。
    期待値は上のテストと同一（負を除いた 4 件は同じ列）。"""
    stats = capture_to_consume_latency(_latency_samples([0.3, -0.5, 0.1, 0.4, -0.01, 0.2]))

    assert stats.negative_count == 2
    assert stats.n == 4
    assert stats.p50_s == pytest.approx(0.25)
    assert stats.p95_s == pytest.approx(0.385)
    assert stats.max_s == pytest.approx(0.4)


def test_samples_without_a_consumption_time_are_counted_separately() -> None:
    samples = _latency_samples([0.1, 0.2]) + [_sample("RED", "RED")]

    stats = capture_to_consume_latency(samples)

    assert stats.missing_consumed == 1
    assert stats.n == 2


def test_latency_stats_are_none_when_no_usable_sample_exists() -> None:
    stats = capture_to_consume_latency(_latency_samples([-1.0]))

    assert stats.n == 0
    assert stats.negative_count == 1
    assert (stats.p50_s, stats.p95_s, stats.max_s) == (None, None, None)


def test_non_finite_timestamps_are_refused() -> None:
    samples = [EvalSample(stamp_s=float("nan"), truth="RED", pred="RED", consumed_s=1.0)]

    with pytest.raises(ValueError, match="non-finite"):
        capture_to_consume_latency(samples)


# ------------------------------------------------------------------ N 系統の比較表
def test_compare_marks_a_p1_failing_system_as_rejected() -> None:
    """同一 bag・同一指標で N 系統を並べ、P-1 を満たさない系統を不採用と明示（04:263）。"""
    clean = [_sample("RED", "RED"), _sample("GREEN_FLASHING", "GREEN_FLASHING")]
    unsafe = [_sample("RED", "GREEN"), _sample("GREEN_FLASHING", "GREEN_FLASHING")]
    cautious = [_sample("RED", "UNKNOWN"), _sample("GREEN_FLASHING", "GREEN_FLASHING")]

    rows = compare({"hsv": clean, "tlr": unsafe, "vlm": cautious})

    assert [row.model for row in rows] == ["hsv", "tlr", "vlm"]
    assert [row.rejected for row in rows] == [False, True, False]
    assert [row.p1_violations for row in rows] == [0, 1, 0]
    assert [row.samples for row in rows] == [2, 2, 2]
    assert rows[2].unknown_rate == pytest.approx(0.5)


def test_compare_of_no_systems_is_an_empty_table() -> None:
    assert compare({}) == []


# ------------------------------------------------------------------ metrics キー案
def test_signal_metrics_use_the_proposed_key_vocabulary() -> None:
    confusion = signal_confusion(
        [
            _sample("RED", "GREEN"),
            _sample("GREEN", "GREEN"),
            _sample("RED", "UNKNOWN"),
            _sample("GREEN_FLASHING", "GREEN_FLASHING"),
        ]
    )

    metrics = signal_metrics(confusion)

    assert metrics["signal.confusion.RED_to_GREEN"] == 1.0
    assert metrics["signal.confusion.GREEN_FLASHING_to_GREEN"] == 0.0
    assert metrics[METRIC_P1_VIOLATIONS] == 1.0
    assert metrics[METRIC_P1_GATE_PASS] == 0.0
    assert metrics[METRIC_SIGNAL_SAMPLES] == 4.0
    assert metrics[METRIC_UNKNOWN_RATE] == pytest.approx(0.25)
    # 16 セル + 違反数 + gate + 総数 + UNKNOWN 率
    assert len(metrics) == 20


def test_unknown_rate_key_is_absent_when_it_cannot_be_computed() -> None:
    """キーの不在 = 「計算できなかった」であって 0 ではない。"""
    assert METRIC_UNKNOWN_RATE not in signal_metrics(signal_confusion([]))


def test_miss_rate_metrics_carry_the_band_sample_count() -> None:
    report = miss_rate_by_distance_band(
        [
            _sample("RED", "UNKNOWN", distance_m=1.0),
            _sample("RED", "RED", distance_m=2.0),
        ],
        _BANDS,
    )

    metrics = miss_rate_metrics(report)

    assert metrics["detect.miss_rate@0-5m"] == pytest.approx(0.5)
    assert metrics["detect.samples@0-5m"] == 2.0
    assert metrics["detect.samples@5-15m"] == 0.0
    assert "detect.miss_rate@5-15m" not in metrics


def test_latency_metrics_always_ship_the_negative_count() -> None:
    metrics = latency_metrics(capture_to_consume_latency(_latency_samples([0.1, 0.2, 0.3, 0.4])))

    assert metrics[METRIC_LATENCY_NEGATIVE] == 0.0
    assert metrics[METRIC_LATENCY_SAMPLES] == 4.0
    assert metrics[METRIC_LATENCY_P50] == pytest.approx(0.25)
    assert metrics[METRIC_LATENCY_P95] == pytest.approx(0.385)
    assert metrics[METRIC_LATENCY_MAX] == pytest.approx(0.4)


def test_drive_metrics_omit_the_rate_without_travel() -> None:
    assert drive_metrics(2, 0.0) == {"drive.travelled_m": 0.0}
    assert drive_metrics(3, 1500.0)["drive.false_stops_per_km"] == pytest.approx(2.0)


# ------------------------------------------------------------------ EvaluationRecord
def test_to_evaluation_record_wraps_the_flat_metric_map() -> None:
    record = to_evaluation_record("bag-0007", {"signal.p1_violations": 0.0})

    assert record.dataset_id == "bag-0007"
    assert record.metrics == {"signal.p1_violations": 0.0}


def test_to_evaluation_record_refuses_an_empty_dataset_id() -> None:
    """出所の無い数値は証拠にならない（04:472）。"""
    with pytest.raises(ValidationError):
        to_evaluation_record("  ", {"signal.p1_violations": 0.0})


def test_to_evaluation_record_refuses_a_non_finite_metric() -> None:
    with pytest.raises(ValidationError):
        to_evaluation_record("bag-0007", {"latency.capture_to_consume_p50_s": float("inf")})


# ------------------------------------------------------------------ JSONL 入力（例示）
def _jsonl(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "samples.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_read_jsonl_parses_the_documented_example_shape(tmp_path: Path) -> None:
    path = _jsonl(
        tmp_path,
        [
            json.dumps({"stamp_s": 10.0, "truth": "RED", "pred": "RED"}),
            "",
            json.dumps(
                {
                    "stamp_s": 10.5,
                    "truth": "GREEN_FLASHING",
                    "pred": "GREEN",
                    "distance_m": 12.0,
                    "consumed_s": 10.62,
                    "crossing_id": "x-001",
                    "unknown_future_key": 1,
                }
            ),
        ],
    )

    samples = read_jsonl(path)

    assert len(samples) == 2
    assert samples[0] == EvalSample(stamp_s=10.0, truth="RED", pred="RED")
    assert samples[1].distance_m == pytest.approx(12.0)
    assert samples[1].consumed_s == pytest.approx(10.62)
    assert samples[1].crossing_id == "x-001"


@pytest.mark.parametrize("missing", ["stamp_s", "truth", "pred"])
def test_read_jsonl_names_the_line_of_a_missing_required_key(tmp_path: Path, missing: str) -> None:
    payload = {"stamp_s": 1.0, "truth": "RED", "pred": "RED"}
    del payload[missing]
    path = _jsonl(
        tmp_path,
        [
            json.dumps({"stamp_s": 0.0, "truth": "RED", "pred": "RED"}),
            json.dumps({"stamp_s": 0.5, "truth": "RED", "pred": "RED"}),
            json.dumps(payload),
        ],
    )

    with pytest.raises(ValueError, match=rf":3: missing required key '{missing}'"):
        read_jsonl(path)


def test_read_jsonl_names_the_line_of_invalid_json(tmp_path: Path) -> None:
    path = _jsonl(
        tmp_path,
        [json.dumps({"stamp_s": 0.0, "truth": "RED", "pred": "RED"}), "{not json"],
    )

    with pytest.raises(ValueError, match=":2: invalid JSON"):
        read_jsonl(path)


@pytest.mark.parametrize(
    "payload",
    [
        {"stamp_s": "ten", "truth": "RED", "pred": "RED"},
        {"stamp_s": 1.0, "truth": "", "pred": "RED"},
        {"stamp_s": 1.0, "truth": "RED", "pred": 7},
        {"stamp_s": 1.0, "truth": "RED", "pred": "RED", "distance_m": -1.0},
        {"stamp_s": 1.0, "truth": "RED", "pred": "RED", "consumed_s": "soon"},
    ],
)
def test_read_jsonl_refuses_malformed_values_with_a_line_number(
    tmp_path: Path, payload: dict
) -> None:
    path = _jsonl(tmp_path, [json.dumps(payload)])

    with pytest.raises(ValueError, match=":1:"):
        read_jsonl(path)


def test_read_jsonl_refuses_a_line_that_is_not_an_object(tmp_path: Path) -> None:
    path = _jsonl(tmp_path, ["[1, 2, 3]"])

    with pytest.raises(ValueError, match=":1: each line must be a JSON object"):
        read_jsonl(path)


def test_read_jsonl_of_an_empty_file_is_an_empty_list(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")

    assert read_jsonl(path) == []


# ------------------------------------------------------------------ AST / text pins
_FORBIDDEN_IMPORT_ROOTS = {
    "rclpy",  # 観測面 offline: ROS context を開かない
    "torch",
    "tensorrt",  # OQ-OD4U: engine はボード上（CI にも無い）
    "numpy",  # CI に無い（純 python + pyyaml + eval_sdk）
    "cv2",
    "onnxruntime",
    "requests",
    "urllib",
}

# 走行系トピック名。随伴 Mac は stop producer になれない（docs/mode-outdoor/02:137）。
_FORBIDDEN_TOKENS = ("cmd_vel", "stop_request", "speed_limit")


def _imported_roots(source: Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_module_imports_no_runtime_or_inference_dependency() -> None:
    forbidden = _imported_roots(_MODULE_SOURCE) & _FORBIDDEN_IMPORT_ROOTS

    assert not forbidden, f"offline evaluation core imports {sorted(forbidden)}"


def test_module_depends_only_on_the_frozen_contract_and_eval_sdk() -> None:
    """共有依存は `warehouse_interfaces` のみ（parallel-workflow.md §2.1）＋
    domain 非依存の `eval_sdk`（一方向依存 = ws/src/eval_sdk/CLAUDE.md「依存」節）。"""
    roots = _imported_roots(_MODULE_SOURCE)
    warehouse = {root for root in roots if root.startswith("warehouse_")}

    assert warehouse <= {"warehouse_interfaces", "warehouse_perception"}, warehouse
    assert "eval_sdk" in roots


def test_module_mentions_no_actuation_topic() -> None:
    source = _MODULE_SOURCE.read_text(encoding="utf-8")
    present = [token for token in _FORBIDDEN_TOKENS if token in source]

    assert not present, f"offline evaluation core mentions actuation topics: {present}"


def test_module_defines_no_ros_node_class() -> None:
    """10_Evaluation は node 0（走行時に動かない = docs/mode-outdoor/02:161）。"""
    tree = ast.parse(_MODULE_SOURCE.read_text(encoding="utf-8"))
    bases = [
        base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        for base in node.bases
    ]

    assert "Node" not in bases, bases
