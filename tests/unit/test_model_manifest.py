"""`warehouse_perception.model_manifest`（09_Runtime・offline）の unit。

**仕様のみ**から書く（doc20 §9 独立オラクル＝
docs/architecture/20-dev-quality-and-testing.md:139）。期待値は手作りの値か、
テスト側が `hashlib` で独立に計算した digest であって、実装から再導出しない。

仕様の出典:

- docs/mode-outdoor/04-perception-sidewalk-and-signals.md:459 / :465 / :467 / :471
  （追補 ④ §1-5 = `ModelManifest` の field と fail 方向。`weights_sha256` は 64 hex・
  `label_order` 空は不可・`tensorrt_version` / `gpu_arch` の空は拒否しない・
  `evaluation` は省略不可）
- docs/mode-outdoor/04-perception-sidewalk-and-signals.md:358（`OQ-OD4U` engine は
  ボード上で焼く＝host tool は engine を焼かない・読まない）
- docs/mode-outdoor/04-perception-sidewalk-and-signals.md:235（例 manifest の
  `license: Apache-2.0` の出典 = RF-DETR 行 [D]）

layer: 09_Runtime_and_Models = 基盤（単一 layer に帰属させない）。本モジュールは
観測面 offline tool であり、走行時に動かない。
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from warehouse_perception.model_manifest import (
    load_manifest,
    main,
    sha256_of_file,
    verify_weights,
)

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]
_MODULE_SOURCE = (
    _REPO / "ws" / "src" / "warehouse_perception" / "warehouse_perception" / "model_manifest.py"
)
_EXAMPLE_MANIFEST = (
    _REPO / "ws" / "src" / "warehouse_perception" / "manifests" / "example.rf-detr-nano.yaml"
)

# 64 hex の形だけを満たす手書きの値（実 digest ではない）。
_HEX64 = "ab" * 32


def _valid_document() -> dict:
    """追補 ④ §1-5 の必須 field をすべて埋めた手作りの manifest（値は手書き）。"""
    return {
        "model_name": "unit-test-model",
        "weights_sha256": _HEX64,
        "input_size": [320, 240],
        "channel_order": "BGR",
        "normalization": "unit-test normalization",
        "resize_method": "unit-test resize",
        "output_interpretation": "unit-test output",
        "label_order": ["person", "bicycle"],
        "onnx_opset": 17,
        "tensorrt_version": "",
        "gpu_arch": "",
        "license": "Apache-2.0",
        "engine_built_on_board": False,
        "evaluation": {"dataset_id": "unit-bag-0001", "metrics": {"signal.p1_violations": 0.0}},
    }


def _write(tmp_path: Path, document: object, name: str = "manifest.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    return path


# ------------------------------------------------------------------ load_manifest
def test_valid_manifest_loads_with_the_written_values(tmp_path: Path) -> None:
    manifest = load_manifest(_write(tmp_path, _valid_document()))

    assert manifest.model_name == "unit-test-model"
    assert manifest.weights_sha256 == _HEX64
    assert manifest.input_size == (320, 240)
    assert manifest.channel_order.value == "BGR"
    assert manifest.label_order == ["person", "bicycle"]
    assert manifest.onnx_opset == 17
    assert manifest.engine_built_on_board is False
    assert manifest.evaluation.dataset_id == "unit-bag-0001"
    assert manifest.evaluation.metrics == {"signal.p1_violations": 0.0}


def test_empty_tensorrt_and_gpu_arch_are_accepted(tmp_path: Path) -> None:
    """engine 未作成（ONNX 止まり）の採用単位は正当 = 04:467 / :468。"""
    manifest = load_manifest(_write(tmp_path, _valid_document()))

    assert manifest.tensorrt_version == ""
    assert manifest.gpu_arch == ""


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    """ハブ既存方針 `extra="ignore"` を継承している（追補 ④ §1 前文）。"""
    document = _valid_document()
    document["future_field_not_in_v0"] = "ignored"

    assert load_manifest(_write(tmp_path, document)).model_name == "unit-test-model"


def test_missing_evaluation_is_refused(tmp_path: Path) -> None:
    """評価の無いモデルは 1 採用単位にならない = 04:471。"""
    document = _valid_document()
    del document["evaluation"]

    with pytest.raises(ValidationError):
        load_manifest(_write(tmp_path, document))


def test_empty_label_order_is_refused(tmp_path: Path) -> None:
    """index が何かを言えない manifest は manifest ではない = 04:465。"""
    document = _valid_document()
    document["label_order"] = []

    with pytest.raises(ValidationError):
        load_manifest(_write(tmp_path, document))


@pytest.mark.parametrize(
    "bad_hash",
    [
        "not-hex" + "0" * 57,  # 64 文字だが hex ではない
        "ab" * 31,  # 62 桁（短い）
        "ab" * 33,  # 66 桁（長い）
        "",
    ],
)
def test_non_sha256_weights_hash_is_refused(tmp_path: Path, bad_hash: str) -> None:
    """非 hex・桁違いは `ValidationError` = 04:459。"""
    document = _valid_document()
    document["weights_sha256"] = bad_hash

    with pytest.raises(ValidationError):
        load_manifest(_write(tmp_path, document))


def test_empty_license_is_refused(tmp_path: Path) -> None:
    """配布可否（AGPL 境界）は manifest と一緒に動く = 04:469。"""
    document = _valid_document()
    document["license"] = "   "

    with pytest.raises(ValidationError):
        load_manifest(_write(tmp_path, document))


def test_non_finite_metric_is_refused(tmp_path: Path) -> None:
    """非有限は「品質不成立」の印であって値ではない = 04:473。"""
    document = _valid_document()
    document["evaluation"]["metrics"] = {"signal.unknown_rate": float("nan")}

    with pytest.raises(ValidationError):
        load_manifest(_write(tmp_path, document))


@pytest.mark.parametrize("document", [[1, 2, 3], "scalar", None])
def test_non_mapping_document_is_refused(tmp_path: Path, document: object) -> None:
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        load_manifest(_write(tmp_path, document))


# ------------------------------------------------------------------ hash / weights
def test_sha256_matches_an_independently_computed_digest(tmp_path: Path) -> None:
    """期待値はテスト側が `hashlib` で独立に計算する（実装から再導出しない）。"""
    payload = b"warehouse perception weights placeholder\n" * 4096
    weights = tmp_path / "weights.bin"
    weights.write_bytes(payload)

    assert sha256_of_file(weights) == hashlib.sha256(payload).hexdigest()


def test_verify_weights_true_on_match_and_false_on_mismatch(tmp_path: Path) -> None:
    payload = b"exactly these bytes"
    weights = tmp_path / "weights.bin"
    weights.write_bytes(payload)
    document = _valid_document()
    document["weights_sha256"] = hashlib.sha256(payload).hexdigest()
    manifest = load_manifest(_write(tmp_path, document))

    assert verify_weights(manifest, weights) is True

    weights.write_bytes(payload + b"!")
    assert verify_weights(manifest, weights) is False


def test_verify_weights_accepts_an_uppercase_digest_in_the_manifest(tmp_path: Path) -> None:
    payload = b"case does not change the bytes"
    weights = tmp_path / "weights.bin"
    weights.write_bytes(payload)
    document = _valid_document()
    document["weights_sha256"] = hashlib.sha256(payload).hexdigest().upper()
    manifest = load_manifest(_write(tmp_path, document))

    assert verify_weights(manifest, weights) is True


def test_verify_weights_raises_when_the_file_is_missing(tmp_path: Path) -> None:
    """読めない = 「照合できなかった」。`False`（＝照合して違った）と同じ顔をさせない。"""
    manifest = load_manifest(_write(tmp_path, _valid_document()))

    with pytest.raises(OSError):
        verify_weights(manifest, tmp_path / "absent.bin")


# ------------------------------------------------------------------ CLI exit codes
def test_cli_exits_zero_for_a_valid_manifest(tmp_path: Path) -> None:
    assert main(["validate", str(_write(tmp_path, _valid_document()))]) == 0


def test_cli_exits_one_for_an_invalid_manifest(tmp_path: Path) -> None:
    document = _valid_document()
    document["onnx_opset"] = 0

    assert main(["validate", str(_write(tmp_path, document))]) == 1


def test_cli_exits_one_for_a_missing_file(tmp_path: Path) -> None:
    assert main(["validate", str(tmp_path / "nope.yaml")]) == 1


def test_cli_checks_the_weights_digest(tmp_path: Path) -> None:
    payload = b"cli weights"
    weights = tmp_path / "weights.bin"
    weights.write_bytes(payload)
    document = _valid_document()
    document["weights_sha256"] = hashlib.sha256(payload).hexdigest()
    manifest_path = _write(tmp_path, document)

    assert main(["validate", str(manifest_path), "--weights", str(weights)]) == 0

    weights.write_bytes(payload + b" drifted")
    assert main(["validate", str(manifest_path), "--weights", str(weights)]) == 1


def test_cli_exits_one_when_the_weights_file_is_unreadable(tmp_path: Path) -> None:
    manifest_path = _write(tmp_path, _valid_document())

    assert main(["validate", str(manifest_path), "--weights", str(tmp_path / "absent.bin")]) == 1


# ------------------------------------------------------------------ example manifest
def test_the_example_manifest_loads() -> None:
    """例ファイルが `load_manifest` を通ることを pin（manifests/README.md §4）。"""
    manifest = load_manifest(_EXAMPLE_MANIFEST)

    # license の出典 = 04:235 の RF-DETR 行 [D]。
    assert manifest.license == "Apache-2.0"
    # 評価済みの「形」の例: ダミー id ("UNEVALUATED" 等) を名乗らない・指標 0 件でない。
    assert manifest.evaluation.dataset_id == "example-bag-0000"
    assert manifest.evaluation.metrics
    # engine 未作成の採用単位であることを記録している（04:467 / :468 / OQ-OD4U）。
    assert manifest.engine_built_on_board is False


def test_the_example_manifest_passes_the_cli() -> None:
    assert main(["validate", str(_EXAMPLE_MANIFEST)]) == 0


def test_the_example_manifest_carries_no_real_weights_digest() -> None:
    """placeholder であることの pin（実測値・実 digest を例に書かない）。"""
    manifest = load_manifest(_EXAMPLE_MANIFEST)

    assert manifest.weights_sha256 == "0" * 64


# ------------------------------------------------------------------ AST / text pins
_FORBIDDEN_IMPORT_ROOTS = {
    "rclpy",  # offline tool: no ROS context
    "torch",  # not in CI, not a dependency
    "tensorrt",  # OQ-OD4U: engines are burned on the board, never here
    "numpy",
    "cv2",
    "onnxruntime",
    "requests",  # no model download / external API
    "urllib",
}

# 走行系トピック名。観測面 offline のモジュールに現れてはいけない
# （随伴 Mac は stop producer になれない = docs/mode-outdoor/02:137）。
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

    assert not forbidden, f"offline manifest tool imports {sorted(forbidden)}"


def test_module_depends_only_on_the_frozen_contract_among_warehouse_packages() -> None:
    """共有依存は `warehouse_interfaces` のみ（parallel-workflow.md §2.1）。"""
    warehouse = {root for root in _imported_roots(_MODULE_SOURCE) if root.startswith("warehouse_")}

    assert warehouse <= {"warehouse_interfaces", "warehouse_perception"}, warehouse


def test_module_mentions_no_actuation_topic() -> None:
    source = _MODULE_SOURCE.read_text(encoding="utf-8")
    present = [token for token in _FORBIDDEN_TOKENS if token in source]

    assert not present, f"offline manifest tool mentions actuation topics: {present}"
