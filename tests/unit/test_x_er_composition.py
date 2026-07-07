"""XER6 Lane A — ``build_x_er_runtime`` startup gates (doc08 §4 steps 1-7, R-26).

Offline, deterministic, no ROS/network. Every fail-closed gate of the X-ER composition startup
is pinned so that weakening any single guard turns at least one test red:

- expected values come from independent oracles (the doc08 §4 invariants + the underlying
  composition modules' own typed errors), never from re-running the implementation;
- the resolver geometry / site-profile fixture style is lifted from
  ``tests/unit/test_calibration_source.py`` (itself verbatim from ``test_l3_pipeline.py``) so
  the governed calibration these tests admit is the SAME one that compiles a real command
  there — no fixture drift.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from warehouse_llm_bridge.robotics.composition import (
    CompositionError,
    ConstructedBox,
    ManifestReconciliationError,
    PluginCodeRegistry,
    PluginComposition,
    build_effective_composition,
    hookimpl,
    load_run_manifest_text,
    preflight_composition,
)
from warehouse_llm_bridge.robotics.composition.calibration_gate import CalibrationDecision
from warehouse_llm_bridge.robotics.composition.calibration_source import (
    GovernedCalibrationUnavailableError,
)
from warehouse_llm_bridge.robotics.composition.profile import (
    SiteProfileError,
    approve,
    compute_content_hash,
    load_site_profile,
)
from warehouse_llm_bridge.robotics_planning_core.validator.seams import Calibration
from warehouse_llm_bridge.x_er_composition import (
    XErCompositionError,
    XErRuntime,
    build_x_er_runtime,
    cross_check_composition,
)

CAMERA = "cam0"
CEILING = 3.0
CUSTOMER = "customer_a"
SITE = "site_01"
ZONE = "l3.zone_policy"

# Resolver geometry lifted verbatim from tests/unit/test_calibration_source.py (itself from
# test_l3_pipeline.py) so the admitted calibration here equals the one that compiles there.
_A = 0.5 / 390.0
_C = 0.2 - 420 * _A
_E = (0.30 - 0.28) / (310 - 280)
_F = 0.30 - 310 * _E
_HOMOGRAPHY = [[_A, 0.0, _C], [0.0, _E, _F], [0.0, 0.0, 1.0]]
_VALID_POLYGON = [[-0.5, -0.5], [2.0, -0.5], [2.0, 1.5], [-0.5, 1.5]]

# `locations` block in the config/warehouse.base.yaml:39-48 shape ({name: {x, y}}).
_LOCATIONS_CFG: dict[str, dict[str, float]] = {
    "shelf_1": {"x": 0.2, "y": 0.3},
    "shelf_2": {"x": 0.7, "y": 0.3},
    "shelf_3": {"x": 1.2, "y": 0.3},
}

_SAFETY_WITH_CEILING = f"calibration:\n  max_reprojection_error: {CEILING}\n"

RUN_MANIFEST_ZERO_PLUGINS = """\
schema_version: run_manifest.v1
run_id: x_er_unit_zero_plugins
boxes:
  l3_validator:
    enabled: true
    profile: customer_a
  hardware:
    enabled: false
    profile: yahboom_micro_ros
expected_emitters:
  - l3_validator
"""

RUN_MANIFEST_ONE_PLUGIN = """\
schema_version: run_manifest.v1
run_id: x_er_unit_one_plugin
boxes:
  l3_validator:
    enabled: true
    profile: customer_a
    plugins:
      - id: l3.zone_policy
        version: 0.1.0
        profile: customer_a
expected_emitters:
  - l3_validator
"""

PLUGIN_MANIFEST_ZONE = """\
plugin_id: l3.zone_policy
box: l3_validator
kind: plugin
version: 0.1.0
status: approved
hook_points:
  - validate_plan
emits:
  box: l3_validator
  reason_codes:
    - target_out_of_zone
safety_boundary:
  may_dispatch_motion: false
  may_write_cmd_vel: false
"""


class ZonePolicyPlugin:
    """Minimal well-behaved hookimpl (returns no findings)."""

    @hookimpl
    def validate_plan(self, plan, context):
        return []


# --- fixture builders --------------------------------------------------------------------------


def _write_site_bundle(
    tmp_path: Path,
    *,
    reprojection_error: float | None = 1.0,
    safety_yaml: str | None = _SAFETY_WITH_CEILING,
    approved: bool = True,
) -> Path:
    """Write ``site_profiles/customer_a/site_01/`` and return the ``base_dir``."""
    base_dir = tmp_path / "site_profiles"
    root = base_dir / CUSTOMER / SITE
    root.mkdir(parents=True)
    (root / "calibration.json").write_text(
        json.dumps(
            {
                "camera_id": CAMERA,
                "map_frame": "map",
                "homography": _HOMOGRAPHY,
                "reprojection_error": reprojection_error,
                "valid_polygon": _VALID_POLYGON,
            }
        ),
        encoding="utf-8",
    )
    if safety_yaml is not None:
        (root / "safety.yaml").write_text(safety_yaml, encoding="utf-8")
    if approved:
        profile = load_site_profile(base_dir, CUSTOMER, SITE)
        record = approve(
            profile,
            compute_content_hash(profile),
            approved_by="reviewer",
            approved_at="2026-07-01",
        )
        # JSON is valid YAML (same trick as test_site_profile_hashing.py).
        (root / "APPROVED.yaml").write_text(json.dumps(record.model_dump()), encoding="utf-8")
    return base_dir


def _write_run_manifest(tmp_path: Path, text: str = RUN_MANIFEST_ZERO_PLUGINS) -> Path:
    path = tmp_path / "run_manifest.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _write_plugin_manifest(tmp_path: Path, text: str = PLUGIN_MANIFEST_ZONE) -> Path:
    path = tmp_path / "plugin_zone.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _cfg(
    *,
    run_manifest: Path | str,
    base_dir: Path | str,
    plugin_manifests: list[str] | None = None,
    snap_radius_m: float | None = 0.25,
    calibration_id: str = CAMERA,
    locations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    visual: dict[str, Any] = {}
    if snap_radius_m is not None:
        visual["snap_radius_m"] = snap_radius_m
    return {
        "locations": dict(_LOCATIONS_CFG) if locations is None else dict(locations),
        "mode_x_er": {
            "enabled": True,
            "execution_profile": "x_lite",
            "calibration_id": calibration_id,
            "visual": visual,
            "run_manifest": str(run_manifest),
            "plugin_manifests": plugin_manifests if plugin_manifests is not None else [],
            "site_profile": {"base_dir": str(base_dir), "customer": CUSTOMER, "site": SITE},
        },
    }


def _build(
    tmp_path: Path,
    cfg: Mapping[str, Any],
    *,
    plugin_factories: Mapping[str, Callable[[], object]] | None = None,
    write_artifacts: bool = True,
) -> XErRuntime:
    return build_x_er_runtime(
        cfg,
        plugin_factories=plugin_factories,
        write_artifacts=write_artifacts,
        out_root=tmp_path / "out" / "runs",
    )


# --- happy paths -------------------------------------------------------------------------------


@pytest.mark.safety
def test_happy_path_zero_plugins_builds_full_runtime(tmp_path: Path):
    base_dir = _write_site_bundle(tmp_path)
    cfg = _cfg(run_manifest=_write_run_manifest(tmp_path), base_dir=base_dir)
    runtime = _build(tmp_path, cfg)

    assert isinstance(runtime, XErRuntime)
    assert runtime.run_manifest.run_id == "x_er_unit_zero_plugins"
    # Vacuous-pass preflight: zero declared == zero registered (preflight.py:81-83).
    assert runtime.preflight_report.declared_plugin_ids == frozenset()
    assert runtime.composition.registered_plugin_ids() == frozenset()
    # Governed calibration: the exact artifact the bundle certified (independent oracle values).
    assert isinstance(runtime.calibration, Calibration)
    assert runtime.calibration.camera_id == CAMERA
    assert runtime.calibration.reprojection_error == 1.0
    # VisualPolicy derives from config, converted to the resolver's tuple shape (policy.py:64).
    assert runtime.visual_policy.snap_radius_m == 0.25
    assert dict(runtime.visual_policy.location_coords) == {
        "shelf_1": (0.2, 0.3),
        "shelf_2": (0.7, 0.3),
        "shelf_3": (1.2, 0.3),
    }


@pytest.mark.safety
def test_happy_path_effective_composition_covers_every_enabled_box(tmp_path: Path):
    """doc08 §4 step7: EVERY enabled box gets an entry; not-in-process boxes get stage=None.

    Mutation oracle: if the wiring skipped an enabled box, record.py:181-186 raises and this
    test (plus every other happy path) goes red — coverage is enforced, not assumed.
    """
    base_dir = _write_site_bundle(tmp_path)
    cfg = _cfg(run_manifest=_write_run_manifest(tmp_path), base_dir=base_dir)
    runtime = _build(tmp_path, cfg)

    effective_boxes = {box.box_id: box for box in runtime.effective_composition.boxes}
    assert set(effective_boxes) == {"l3_validator", "hardware"}  # manifest-declared set
    assert effective_boxes["l3_validator"].enabled is True
    assert effective_boxes["l3_validator"].class_name is None  # stage=None witness
    assert effective_boxes["hardware"].enabled is False
    # The governance blocks are embedded (record.py:161-165 S3 slots).
    assert runtime.effective_composition.site_profile is not None
    assert runtime.effective_composition.calibration_governance is not None
    cameras = runtime.effective_composition.calibration_governance["cameras"]
    assert [entry["camera_id"] for entry in cameras] == [CAMERA]
    assert cameras[0]["decision"] == CalibrationDecision.ACCEPTED.value


@pytest.mark.safety
def test_happy_path_one_plugin_registered_under_manifest_plugin_id(tmp_path: Path):
    base_dir = _write_site_bundle(tmp_path)
    cfg = _cfg(
        run_manifest=_write_run_manifest(tmp_path, RUN_MANIFEST_ONE_PLUGIN),
        base_dir=base_dir,
        plugin_manifests=[str(_write_plugin_manifest(tmp_path))],
    )
    runtime = _build(tmp_path, cfg, plugin_factories={ZONE: ZonePolicyPlugin})

    assert runtime.composition.registered_plugin_ids() == frozenset({ZONE})
    assert runtime.preflight_report.declared_plugin_ids == frozenset({ZONE})
    (l3_box,) = runtime.effective_composition.boxes
    (plugin,) = l3_box.plugins
    # Witness fields derive from type() of the CONSTRUCTED instance (record.py:157-159).
    assert (plugin.id, plugin.class_name) == (ZONE, "ZonePolicyPlugin")


# --- step 1: run manifest gate -------------------------------------------------------------


@pytest.mark.safety
def test_missing_mode_x_er_section_refuses(tmp_path: Path):
    with pytest.raises(XErCompositionError, match="mode_x_er"):
        build_x_er_runtime({"locations": _LOCATIONS_CFG})


@pytest.mark.safety
def test_empty_run_manifest_path_refuses(tmp_path: Path):
    base_dir = _write_site_bundle(tmp_path)
    cfg = _cfg(run_manifest="", base_dir=base_dir)
    with pytest.raises(XErCompositionError, match="run_manifest"):
        _build(tmp_path, cfg)


@pytest.mark.safety
def test_unknown_schema_version_is_rejected(tmp_path: Path):
    """Malformed manifests keep their typed loader error (module error policy: propagate)."""
    base_dir = _write_site_bundle(tmp_path)
    bad = RUN_MANIFEST_ZERO_PLUGINS.replace("run_manifest.v1", "run_manifest.v2")
    cfg = _cfg(run_manifest=_write_run_manifest(tmp_path, bad), base_dir=base_dir)
    with pytest.raises(ValidationError, match="schema_version"):
        _build(tmp_path, cfg)


# --- step 2: two-manifest reconciliation ----------------------------------------------------


@pytest.mark.safety
def test_run_declared_plugin_without_plugin_manifest_refuses(tmp_path: Path):
    base_dir = _write_site_bundle(tmp_path)
    cfg = _cfg(
        run_manifest=_write_run_manifest(tmp_path, RUN_MANIFEST_ONE_PLUGIN),
        base_dir=base_dir,
        plugin_manifests=[],  # run declares l3.zone_policy but no manifest is given
    )
    with pytest.raises(ManifestReconciliationError, match="no plugin manifest"):
        _build(tmp_path, cfg, plugin_factories={ZONE: ZonePolicyPlugin})


# --- step 4: factory coverage ---------------------------------------------------------------


@pytest.mark.safety
def test_run_declared_plugin_without_factory_refuses(tmp_path: Path):
    base_dir = _write_site_bundle(tmp_path)
    cfg = _cfg(
        run_manifest=_write_run_manifest(tmp_path, RUN_MANIFEST_ONE_PLUGIN),
        base_dir=base_dir,
        plugin_manifests=[str(_write_plugin_manifest(tmp_path))],
    )
    with pytest.raises(XErCompositionError, match="no factory"):
        _build(tmp_path, cfg, plugin_factories={})


# --- step 5: preflight + triple cross-check -------------------------------------------------


@pytest.mark.safety
def test_extra_registered_plugin_fails_preflight_and_triple_cross_check():
    """Extra-registered mismatch, exercised against the REAL gates the builder runs.

    ``build_x_er_runtime`` constructs registrations only from run-declared ids, so this state
    is reachable only if that wiring (or an underlying gate) is weakened — which is exactly
    what these two independent oracles stay red against.
    """
    run_manifest = load_run_manifest_text(RUN_MANIFEST_ONE_PLUGIN)
    registry = PluginCodeRegistry(
        declared_emits={
            ZONE: frozenset({"target_out_of_zone"}),
            "l3.rogue": frozenset({"target_out_of_zone"}),
        }
    )
    composition = PluginComposition(registry=registry)
    composition.register(ZonePolicyPlugin(), ZONE)
    composition.register(ZonePolicyPlugin(), "l3.rogue")  # registered but NOT run-declared

    with pytest.raises(CompositionError, match="does not declare"):
        preflight_composition(run_manifest, composition)
    with pytest.raises(XErCompositionError, match="triple cross-check"):
        cross_check_composition(
            run_manifest,
            composition,
            [],  # manifest-present side empty too => inequality
        )


@pytest.mark.safety
def test_triple_cross_check_passes_only_on_full_equality():
    run_manifest = load_run_manifest_text(RUN_MANIFEST_ZERO_PLUGINS)
    composition = PluginComposition(registry=PluginCodeRegistry(declared_emits={}))
    cross_check_composition(run_manifest, composition, [])  # vacuous equality: no raise


# --- step 6: site profile gate + governed calibration ---------------------------------------


@pytest.mark.safety
def test_profile_hash_mismatch_refuses(tmp_path: Path):
    """A bundle edited AFTER approval (safety.yaml drift) must not start a run."""
    base_dir = _write_site_bundle(tmp_path)
    drifted = base_dir / CUSTOMER / SITE / "safety.yaml"
    drifted.write_text(_SAFETY_WITH_CEILING + "# post-approval edit\n", encoding="utf-8")
    cfg = _cfg(run_manifest=_write_run_manifest(tmp_path), base_dir=base_dir)
    with pytest.raises(SiteProfileError, match="mismatch"):
        _build(tmp_path, cfg)


@pytest.mark.safety
def test_unapproved_profile_refuses(tmp_path: Path):
    base_dir = _write_site_bundle(tmp_path, approved=False)
    cfg = _cfg(run_manifest=_write_run_manifest(tmp_path), base_dir=base_dir)
    with pytest.raises(SiteProfileError, match="unapproved"):
        _build(tmp_path, cfg)


@pytest.mark.safety
def test_rejected_calibration_raises_never_none(tmp_path: Path):
    """The self-cert hole (reprojection_error=None) refuses startup — no None reaches L3."""
    base_dir = _write_site_bundle(tmp_path, reprojection_error=None)
    cfg = _cfg(run_manifest=_write_run_manifest(tmp_path), base_dir=base_dir)
    with pytest.raises(GovernedCalibrationUnavailableError) as excinfo:
        _build(tmp_path, cfg)
    assert excinfo.value.camera_id == CAMERA
    assert excinfo.value.entry is not None
    assert excinfo.value.entry.decision is CalibrationDecision.REJECTED


@pytest.mark.safety
def test_unknown_calibration_id_refuses(tmp_path: Path):
    base_dir = _write_site_bundle(tmp_path)
    cfg = _cfg(
        run_manifest=_write_run_manifest(tmp_path),
        base_dir=base_dir,
        calibration_id="no_such_camera",
    )
    with pytest.raises(GovernedCalibrationUnavailableError):
        _build(tmp_path, cfg)


@pytest.mark.safety
def test_empty_calibration_id_refuses(tmp_path: Path):
    base_dir = _write_site_bundle(tmp_path)
    cfg = _cfg(run_manifest=_write_run_manifest(tmp_path), base_dir=base_dir, calibration_id="")
    with pytest.raises(XErCompositionError, match="calibration_id"):
        _build(tmp_path, cfg)


@pytest.mark.safety
def test_empty_site_profile_keys_refuse(tmp_path: Path):
    base_dir = _write_site_bundle(tmp_path)
    cfg = _cfg(run_manifest=_write_run_manifest(tmp_path), base_dir=base_dir)
    cfg["mode_x_er"]["site_profile"]["customer"] = ""
    with pytest.raises(XErCompositionError, match="site_profile.customer"):
        _build(tmp_path, cfg)


# --- step 7: ConstructedBox coverage (independent oracle on the guard the builder relies on) --


@pytest.mark.safety
def test_missing_constructed_box_coverage_raises():
    """record.py:181-186 refuses a record missing an enabled box — the guard that turns any
    weakened builder coverage loop into a raise (see the happy-path coverage test)."""
    run_manifest = load_run_manifest_text(RUN_MANIFEST_ZERO_PLUGINS)
    composition = PluginComposition(registry=PluginCodeRegistry(declared_emits={}))
    report = preflight_composition(run_manifest, composition)
    with pytest.raises(CompositionError, match="never constructed"):
        build_effective_composition(run_manifest, report, {})  # l3_validator entry missing
    # And the complete stage=None coverage the builder produces IS accepted:
    record = build_effective_composition(
        run_manifest, report, {"l3_validator": ConstructedBox(stage=None)}
    )
    assert [box.box_id for box in record.boxes if box.enabled] == ["l3_validator"]


# --- execution_profile gate (doc08 §3: x_lite | x_rmf; x_rmf => NotImplementedError) ---------


@pytest.mark.safety
def test_execution_profile_x_rmf_refuses_startup_not_implemented(tmp_path: Path):
    """R-26 (doc08 §2/#346): declaring the unimplemented ``x_rmf`` backend must refuse
    startup with ``NotImplementedError`` — never start and silently compile under the
    ``compile_raw_output`` x_lite default (pipeline.py:97). Goes red if the startup gate is
    dropped: the build would then succeed on an otherwise-valid cfg."""
    base_dir = _write_site_bundle(tmp_path)
    cfg = _cfg(run_manifest=_write_run_manifest(tmp_path), base_dir=base_dir)
    cfg["mode_x_er"]["execution_profile"] = "x_rmf"
    with pytest.raises(NotImplementedError, match="x_rmf"):
        _build(tmp_path, cfg)


@pytest.mark.safety
def test_unknown_execution_profile_refuses(tmp_path: Path):
    """A value outside the doc08 §3 vocabulary is malformed config => startup refusal."""
    base_dir = _write_site_bundle(tmp_path)
    cfg = _cfg(run_manifest=_write_run_manifest(tmp_path), base_dir=base_dir)
    cfg["mode_x_er"]["execution_profile"] = "warp_drive"
    with pytest.raises(XErCompositionError, match="execution_profile"):
        _build(tmp_path, cfg)


@pytest.mark.safety
def test_missing_execution_profile_refuses(tmp_path: Path):
    """The frozen key must be present (base.yaml ships x_lite); absence refuses startup."""
    base_dir = _write_site_bundle(tmp_path)
    cfg = _cfg(run_manifest=_write_run_manifest(tmp_path), base_dir=base_dir)
    del cfg["mode_x_er"]["execution_profile"]
    with pytest.raises(XErCompositionError, match="execution_profile"):
        _build(tmp_path, cfg)


# --- VisualPolicy config gates ---------------------------------------------------------------


@pytest.mark.safety
def test_missing_snap_radius_refuses_no_hardcoded_default(tmp_path: Path):
    base_dir = _write_site_bundle(tmp_path)
    cfg = _cfg(run_manifest=_write_run_manifest(tmp_path), base_dir=base_dir, snap_radius_m=None)
    with pytest.raises(XErCompositionError, match="snap_radius_m"):
        _build(tmp_path, cfg)


@pytest.mark.safety
def test_missing_locations_block_refuses(tmp_path: Path):
    base_dir = _write_site_bundle(tmp_path)
    cfg = _cfg(run_manifest=_write_run_manifest(tmp_path), base_dir=base_dir)
    del cfg["locations"]
    with pytest.raises(XErCompositionError, match="locations"):
        _build(tmp_path, cfg)


@pytest.mark.safety
def test_malformed_location_entry_refuses(tmp_path: Path):
    base_dir = _write_site_bundle(tmp_path)
    cfg = _cfg(
        run_manifest=_write_run_manifest(tmp_path),
        base_dir=base_dir,
        locations={"shelf_1": {"x": 0.2}},  # y missing
    )
    with pytest.raises(XErCompositionError, match="locations.shelf_1.y"):
        _build(tmp_path, cfg)


# --- artifacts -------------------------------------------------------------------------------


def test_artifacts_written_when_write_artifacts_true(tmp_path: Path):
    base_dir = _write_site_bundle(tmp_path)
    cfg = _cfg(run_manifest=_write_run_manifest(tmp_path), base_dir=base_dir)
    runtime = _build(tmp_path, cfg, write_artifacts=True)

    assert runtime.out_dir == tmp_path / "out" / "runs" / "x_er_unit_zero_plugins"
    assert (runtime.out_dir / "manifest.yaml").is_file()
    effective_path = runtime.out_dir / "effective_composition.json"
    assert effective_path.is_file()
    payload = json.loads(effective_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "x_er_unit_zero_plugins"
    # The witness on disk carries the governance blocks the runtime embedded (step 6/7 join).
    assert payload["site_profile"]["customer"] == CUSTOMER
    assert payload["calibration_governance"]["cameras"][0]["camera_id"] == CAMERA


def test_write_artifacts_false_writes_nothing(tmp_path: Path):
    base_dir = _write_site_bundle(tmp_path)
    cfg = _cfg(run_manifest=_write_run_manifest(tmp_path), base_dir=base_dir)
    runtime = _build(tmp_path, cfg, write_artifacts=False)

    assert runtime.out_dir is None
    assert not (tmp_path / "out").exists()
