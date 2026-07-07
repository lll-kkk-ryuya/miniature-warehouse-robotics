"""X-ER offline e2e fixtures — run manifest / plugin manifest / site profile / calibration / cfg.

Lane-D fixture kit for the XER6 ``x_er_bridge`` offline test layer (doc08 §8 layer ①:
docs/mode-x-er/08-x-er-bridge-node-spec.md — "X-ER 用 run manifest fixture（新規作成・現存
fixture は Mode A probe のみ）"). Everything here is deterministic, offline, ROS-free.

Contents (each mirrors a landed canonical usage — grounded, not invented):

- :func:`x_er_run_manifest_yaml` — the X-ER ``run_manifest.v1`` document (doc08 §9 "enabled box
  集合 … fixture 作成時に確定"; shape mirrors ``robotics/composition/fixtures.py`` Mode A probe).
- :func:`x_er_plugin_manifest_yaml` — a per-plugin ``plugin.yaml`` (doc09:231-257 shape, mirrors
  ``tests/unit/test_plugin_manifest_loader.py`` ``_plugin_manifest_dict``).
- :func:`write_site_profile_bundle` — materializes a tmp ``site_profiles/<customer>/<site>/``
  bundle WITH the ``APPROVED.yaml`` content-hash record (mirrors
  ``tests/unit/test_site_profile_hashing.py`` ``_write_bundle`` / ``_approved``), so the doc08 §4
  step6 gate (``verify_against_approved(...).assert_verified()``) passes.
- :func:`dev_calibration_yaml` — the 5-field calibration artifact content (``camera_id /
  map_frame / homography(3x3) / reprojection_error / valid_polygon`` = doc02:149 / doc06:105 /
  doc08 §3), with the VERIFIED red/blue geometry lifted verbatim from
  ``tests/unit/test_l3_pipeline.py:159-169`` (itself from ``test_visual_resolver.py``): red_box
  pixel (420,310) -> (0.2,0.3) -> shelf_1; blue_box pixel (810,280) -> (0.7,0.28) -> shelf_2.
- :func:`build_x_er_cfg` / :func:`write_x_er_cfg_tree` — the warehouse cfg dict with the frozen
  ``mode_x_er:`` block (doc08 §3) + the ``locations`` block copied from
  ``config/warehouse.base.yaml:39-48`` (self-checked against the real file below).

Self-check tests live IN this module (it deliberately does NOT import the lane-A/B modules
``x_er_composition`` / ``x_er_cycle``, so the fixtures stay green while those lanes land). Run
them explicitly pre-Integrate::

    python -m pytest tests/unit/x_er_fixtures.py -q

``tests/unit/test_x_er_offline_e2e.py`` re-imports these test functions so the normal suite
collects them once the e2e module becomes importable (Integrate phase). That re-import block
is their ONLY collection point in a normal ``pytest tests/unit`` run (this module does not
match pytest's ``python_files`` pattern) — do not trim it; see the warning at the import site.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
import yaml
from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_llm_bridge.robotics.composition.calibration_source import (
    GovernedCalibrationUnavailableError,
    resolve_governed_calibration,
)
from warehouse_llm_bridge.robotics.composition.loader import load_run_manifest_text
from warehouse_llm_bridge.robotics.composition.plugin_manifest import (
    build_plugin_code_registry,
    load_plugin_manifests,
)
from warehouse_llm_bridge.robotics.composition.plugin_results import (
    VALIDATE_PLAN_BOX,
    VALIDATE_PLAN_STAGE,
)
from warehouse_llm_bridge.robotics.composition.profile import (
    SiteProfileError,
    approve,
    compute_content_hash,
    load_approved_record,
    load_site_profile,
    verify_against_approved,
)
from warehouse_llm_bridge.robotics_planning_core.fixtures.red_blue_sequence import INNER_PLAN
from warehouse_llm_bridge.robotics_planning_core.models import RoboticsPlanDraft
from warehouse_llm_bridge.robotics_planning_core.validator.seams import Calibration
from warehouse_llm_bridge.robotics_planning_core.visual_resolver import (
    Resolution,
    VisualPolicy,
    VisualTaskResolver,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

# ── identity constants (doc08 §3: calibration_id ≡ camera_id ≡ artifact file stem) ──────────

CALIBRATION_ID = "dev-sim-v1"
X_ER_RUN_ID = "x_er_offline_e2e"
X_ER_PLUGIN_ID = "l3.zone_policy"  # the doc09:192 manifest example plugin_id
X_ER_PLUGIN_REASON_CODE = "target_out_of_zone"  # doc09:204 declared emit
X_ER_CUSTOMER = "customer_a"
X_ER_SITE = "site_01"

# ── verified red/blue geometry (VERBATIM from tests/unit/test_l3_pipeline.py:159-169) ───────
# red_box pixel (420,310) -> map (0.2, 0.3) == shelf_1; blue_box pixel (810,280) -> (0.7, 0.28)
# -> within snap radius of shelf_2 (0.7, 0.3). Same values as test_visual_resolver.py:106.

_A = 0.5 / 390.0
_C = 0.2 - 420 * _A
_E = (0.30 - 0.28) / (310 - 280)
_F = 0.30 - 310 * _E
HOMOGRAPHY: list[list[float]] = [[_A, 0.0, _C], [0.0, _E, _F], [0.0, 0.0, 1.0]]
VALID_POLYGON: list[list[float]] = [[-0.5, -0.5], [2.0, -0.5], [2.0, 1.5], [-0.5, 1.5]]
REPROJECTION_ERROR = 1.0
MAX_REPROJECTION_ERROR = 3.0  # site safety ceiling (mirrors test_calibration_source.py CEILING)
SNAP_RADIUS_M = 0.25  # doc08 §3 example value (same as the existing offline fixtures)

# ── locations block, copied from config/warehouse.base.yaml:39-48 (self-checked below) ──────

BASE_LOCATIONS: dict[str, dict[str, float]] = {
    "shelf_1": {"x": 0.2, "y": 0.3},
    "shelf_2": {"x": 0.7, "y": 0.3},
    "shelf_3": {"x": 1.2, "y": 0.3},
    "berth_A": {"x": 0.2, "y": 0.8},
    "berth_B": {"x": 0.7, "y": 0.8},
    "shipping_station": {"x": 0.2, "y": 0.1},
    "charging_station": {"x": 1.2, "y": 0.1},
    "retreat_A": {"x": 0.45, "y": 0.85},
    "retreat_B": {"x": 0.95, "y": 0.85},
}


def location_coords(locations: dict[str, dict[str, float]]) -> dict[str, tuple[float, float]]:
    """Derive ``VisualPolicy.location_coords`` from the cfg ``locations`` block (doc08 §3:
    "location_coords は config ``locations`` から導出＝新規座標 key は発明しない")."""
    return {name: (spec["x"], spec["y"]) for name, spec in locations.items()}


# ── run manifest fixture (doc08 §9: enabled box set fixed here) ──────────────────────────────


def x_er_run_manifest_yaml(
    *, run_id: str = X_ER_RUN_ID, plugin_ids: tuple[str, ...] = (X_ER_PLUGIN_ID,)
) -> str:
    """The X-ER ``run_manifest.v1`` document (offline X-lite slice).

    Enabled box set (doc08 §9 residual, fixed by this fixture): ``l4_bridge`` (ER adapter +
    x_er_bridge cycle), ``l3_validator`` (L3 Planning Core + validate_plan plugins) and
    ``l2_governance`` (WarehouseTools / Policy Gate). The live-sim boxes (navigation / safety /
    hardware) are OMITTED = "not used in this run" (doc09:124), matching the offline layer ①
    where ``nav2_forwarder=None`` (doc08 §8).
    """
    validator_box: dict[str, Any] = {"enabled": True, "profile": "x_lite_default"}
    if plugin_ids:
        validator_box["plugins"] = [
            {"id": plugin_id, "version": "0.1.0", "profile": X_ER_CUSTOMER}
            for plugin_id in plugin_ids
        ]
    doc: dict[str, Any] = {
        "schema_version": "run_manifest.v1",
        "run_id": run_id,
        "boxes": {
            "l4_bridge": {"enabled": True, "profile": "x_er_commander"},
            VALIDATE_PLAN_BOX: validator_box,
            "l2_governance": {"enabled": True, "profile": "mini_warehouse_default"},
        },
        "expected_emitters": ["l4_bridge", VALIDATE_PLAN_BOX, "l2_governance"],
        "score_specs": ["result"],
    }
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def x_er_plugin_manifest_yaml(
    plugin_id: str = X_ER_PLUGIN_ID,
    *,
    reason_codes: tuple[str, ...] = (X_ER_PLUGIN_REASON_CODE,),
) -> str:
    """One per-plugin ``plugin.yaml`` (doc09:231-257; mirrors test_plugin_manifest_loader.py)."""
    doc: dict[str, Any] = {
        "plugin_id": plugin_id,
        "box": VALIDATE_PLAN_BOX,
        "kind": "plugin",
        "version": "0.1.0",
        "status": "standard",  # doc09:235 example value (free string)
        "hook_points": [VALIDATE_PLAN_STAGE],
        "emits": {"box": VALIDATE_PLAN_BOX, "reason_codes": list(reason_codes)},
        "requires": {"artifacts": [], "profiles": [X_ER_CUSTOMER]},
        "fixtures": [],
        "safety_boundary": {"may_dispatch_motion": False, "may_write_cmd_vel": False},
    }
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


# ── calibration artifact content (5 frozen fields, doc02:149 / doc06:105 / doc08 §3) ────────


def dev_calibration_yaml(
    calibration_id: str = CALIBRATION_ID, *, reprojection_error: float | None = REPROJECTION_ERROR
) -> str:
    """The ``config/<env>/calibration/<id>.yaml`` artifact content (doc08 §3: stem ≡ camera_id).

    Exactly the 5 doc-literal fields (doc02:149, verbatim in doc08 §3) with the verified
    red/blue geometry — no extra keys, no code-constant thresholds beyond the artifact itself.
    """
    artifact: dict[str, Any] = {
        "camera_id": calibration_id,
        "map_frame": "map",
        "homography": HOMOGRAPHY,
        "reprojection_error": reprojection_error,
        "valid_polygon": VALID_POLYGON,
    }
    return yaml.safe_dump(artifact, sort_keys=False, indent=2, default_flow_style=False)


def calibration_from_yaml(text: str) -> Calibration:
    """Parse a 5-field calibration artifact into the landed ``Calibration`` model."""
    return Calibration.model_validate(yaml.safe_load(text))


# ── site profile bundle (doc08 §4 step6 gate inputs) ─────────────────────────────────────────


def site_safety_yaml(max_reprojection_error: float = MAX_REPROJECTION_ERROR) -> str:
    """The site ``safety.yaml`` with the calibration governance ceiling (calibration_gate)."""
    return f"calibration:\n  max_reprojection_error: {max_reprojection_error}\n"


def site_calibration_json(
    calibration_id: str = CALIBRATION_ID, *, reprojection_error: float | None = REPROJECTION_ERROR
) -> str:
    """The bundle ``calibration.json`` — same 5 fields/geometry as the dev YAML artifact."""
    return json.dumps(
        {
            "camera_id": calibration_id,
            "map_frame": "map",
            "homography": HOMOGRAPHY,
            "reprojection_error": reprojection_error,
            "valid_polygon": VALID_POLYGON,
        },
        indent=2,
    )


def write_site_profile_bundle(
    base_dir: Path,
    *,
    calibration_id: str = CALIBRATION_ID,
    reprojection_error: float | None = REPROJECTION_ERROR,
    max_reprojection_error: float = MAX_REPROJECTION_ERROR,
    customer: str = X_ER_CUSTOMER,
    site: str = X_ER_SITE,
    approved: bool = True,
) -> Path:
    """Materialize ``<base_dir>/<customer>/<site>/`` WITH the ``APPROVED.yaml`` hash record.

    Mirrors ``test_site_profile_hashing.py`` (``_write_bundle`` + ``_approved``; the JSON dump of
    the record is valid YAML, :146-147): after this, the doc08 §4 step6 sequence
    ``load_site_profile -> compute_content_hash -> load_approved_record ->
    verify_against_approved(...).assert_verified()`` passes, and
    ``resolve_governed_calibration(profile, camera_id=calibration_id)`` returns the calibration.
    Returns ``base_dir`` (the ``site_profile.base_dir`` cfg value).
    """
    root = base_dir / customer / site
    root.mkdir(parents=True, exist_ok=True)
    (root / "profile.yaml").write_text('version: "1.0.0"\n', encoding="utf-8")
    (root / "safety.yaml").write_text(site_safety_yaml(max_reprojection_error), encoding="utf-8")
    (root / "calibration.json").write_text(
        site_calibration_json(calibration_id, reprojection_error=reprojection_error),
        encoding="utf-8",
    )
    if approved:
        profile = load_site_profile(base_dir, customer, site)
        record = approve(
            profile,
            compute_content_hash(profile),
            approved_by="reviewer",
            approved_at="2026-07-07",
        )
        # JSON is valid YAML (canonical trick from test_site_profile_hashing.py:145-147).
        (root / "APPROVED.yaml").write_text(json.dumps(record.model_dump()), encoding="utf-8")
    return base_dir


# ── full cfg dict (doc08 §3 frozen mode_x_er block + base.yaml locations) ────────────────────


def build_x_er_cfg(
    *,
    run_manifest_path: Path | str,
    plugin_manifest_paths: tuple[Path | str, ...] = (),
    site_base_dir: Path | str,
    enabled: bool = True,
    execution_profile: str = "x_lite",
    calibration_id: str = CALIBRATION_ID,
    snap_radius_m: float = SNAP_RADIUS_M,
    customer: str = X_ER_CUSTOMER,
    site: str = X_ER_SITE,
) -> dict[str, Any]:
    """The warehouse cfg mapping ``build_x_er_runtime`` consumes (doc08 §3, frozen key set).

    ``locations`` is a fresh copy of the ``config/warehouse.base.yaml`` block (bringup-owned;
    self-checked against the real file below) — ``location_coords`` derive from it, no new
    coordinate key is invented (doc08 §3).
    """
    return {
        "locations": {name: dict(spec) for name, spec in BASE_LOCATIONS.items()},
        "mode_x_er": {
            "enabled": enabled,
            "execution_profile": execution_profile,
            "calibration_id": calibration_id,
            "visual": {"snap_radius_m": snap_radius_m},
            "run_manifest": str(run_manifest_path),
            "plugin_manifests": [str(path) for path in plugin_manifest_paths],
            "site_profile": {"base_dir": str(site_base_dir), "customer": customer, "site": site},
        },
    }


def write_x_er_cfg_tree(
    tmp_path: Path,
    *,
    plugin_ids: tuple[str, ...] = (X_ER_PLUGIN_ID,),
    **cfg_overrides: Any,
) -> dict[str, Any]:
    """Materialize run manifest + plugin manifests + approved site bundle; return the cfg dict."""
    run_manifest_path = tmp_path / "run_manifest.yaml"
    run_manifest_path.write_text(x_er_run_manifest_yaml(plugin_ids=plugin_ids), encoding="utf-8")
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(exist_ok=True)
    plugin_manifest_paths: list[Path] = []
    for plugin_id in plugin_ids:
        path = plugin_dir / f"{plugin_id}.plugin.yaml"
        path.write_text(x_er_plugin_manifest_yaml(plugin_id), encoding="utf-8")
        plugin_manifest_paths.append(path)
    site_base_dir = write_site_profile_bundle(tmp_path / "site_profiles")
    return build_x_er_cfg(
        run_manifest_path=run_manifest_path,
        plugin_manifest_paths=tuple(plugin_manifest_paths),
        site_base_dir=site_base_dir,
        **cfg_overrides,
    )


# ═════════════════════════════════ self-check tests ═════════════════════════════════════════
# Deliberately colocated: they exercise ONLY landed modules (no x_er_composition / x_er_cycle),
# so they run before the lane-A/B modules exist: python -m pytest tests/unit/x_er_fixtures.py


@pytest.mark.unit
def test_x_er_run_manifest_fixture_is_valid_run_manifest_v1():
    manifest = load_run_manifest_text(x_er_run_manifest_yaml())
    assert manifest.run_id == X_ER_RUN_ID
    assert manifest.enabled_boxes() == ("l4_bridge", VALIDATE_PLAN_BOX, "l2_governance")
    assert manifest.enabled_plugin_owners() == {X_ER_PLUGIN_ID: VALIDATE_PLAN_BOX}


@pytest.mark.unit
def test_x_er_run_manifest_plugin_less_variant_is_valid():
    manifest = load_run_manifest_text(x_er_run_manifest_yaml(plugin_ids=()))
    assert manifest.enabled_plugin_owners() == {}


@pytest.mark.unit
def test_x_er_plugin_manifest_reconciles_with_run_manifest():
    run_manifest = load_run_manifest_text(x_er_run_manifest_yaml())
    plugin_manifests = load_plugin_manifests([x_er_plugin_manifest_yaml()])
    registry, report = build_plugin_code_registry(run_manifest, plugin_manifests)
    assert report.matched == (X_ER_PLUGIN_ID,)
    assert registry.is_declared(X_ER_PLUGIN_ID, X_ER_PLUGIN_REASON_CODE)


@pytest.mark.safety
def test_site_profile_bundle_verifies_against_approved_record(tmp_path: Path):
    """doc08 §4 step6 happy path: the exact landed call sequence passes on the fixture bundle."""
    base_dir = write_site_profile_bundle(tmp_path / "site_profiles")
    profile = load_site_profile(base_dir, X_ER_CUSTOMER, X_ER_SITE)
    content_hash = compute_content_hash(profile)
    approved = load_approved_record(base_dir, X_ER_CUSTOMER, X_ER_SITE)
    verification = verify_against_approved(profile, content_hash, approved)
    verification.assert_verified()  # raises => the fixture cannot start a composed run


@pytest.mark.safety
def test_tampered_bundle_fails_closed(tmp_path: Path):
    """R-26: a post-approval safety.yaml edit must refuse startup (doc08 §4 fail-closed)."""
    base_dir = write_site_profile_bundle(tmp_path / "site_profiles")
    safety = base_dir / X_ER_CUSTOMER / X_ER_SITE / "safety.yaml"
    safety.write_text(safety.read_text().replace("3.0", "9.0", 1), encoding="utf-8")
    profile = load_site_profile(base_dir, X_ER_CUSTOMER, X_ER_SITE)
    verification = verify_against_approved(
        profile,
        compute_content_hash(profile),
        load_approved_record(base_dir, X_ER_CUSTOMER, X_ER_SITE),
    )
    assert verification.permits_run is False
    assert verification.safety_critical_mismatch is True
    with pytest.raises(SiteProfileError):
        verification.assert_verified()


@pytest.mark.safety
def test_governed_calibration_resolves_for_fixture_bundle(tmp_path: Path):
    """doc08 §5 step4 input: calibration_id is passed AS camera_id (identity is one string)."""
    base_dir = write_site_profile_bundle(tmp_path / "site_profiles")
    profile = load_site_profile(base_dir, X_ER_CUSTOMER, X_ER_SITE)
    calibration = resolve_governed_calibration(profile, CALIBRATION_ID)
    assert calibration.camera_id == CALIBRATION_ID
    assert calibration.homography == HOMOGRAPHY
    assert calibration.reprojection_error == REPROJECTION_ERROR


@pytest.mark.safety
def test_uncertified_bundle_calibration_is_rejected_fail_closed(tmp_path: Path):
    """R-26: the fixture knob for the self-cert hole — ``reprojection_error: null`` in the
    bundle must raise, never hand back an artifact (doc08 §6 calibration 拒否 => 0 dispatch)."""
    base_dir = write_site_profile_bundle(tmp_path / "site_profiles", reprojection_error=None)
    profile = load_site_profile(base_dir, X_ER_CUSTOMER, X_ER_SITE)
    with pytest.raises(GovernedCalibrationUnavailableError):
        resolve_governed_calibration(profile, CALIBRATION_ID)


@pytest.mark.safety
def test_geometry_red_blue_snap_via_dev_calibration():
    """Independent geometry oracle (mirrors test_visual_resolver.py:106): the calibration
    artifact + the base.yaml-derived location_coords snap red->shelf_1 and blue->shelf_2."""
    calibration = calibration_from_yaml(dev_calibration_yaml())
    policy = VisualPolicy(
        location_coords=location_coords(BASE_LOCATIONS), snap_radius_m=SNAP_RADIUS_M
    )
    draft = RoboticsPlanDraft.model_validate(INNER_PLAN)
    result = VisualTaskResolver(policy).resolve(draft, calibration)
    by_id = {target.target_id: target for target in result.targets}
    assert by_id["red_box"].resolution is Resolution.KNOWN_LOCATION
    assert by_id["red_box"].destination == "shelf_1"
    assert by_id["blue_box"].resolution is Resolution.KNOWN_LOCATION
    assert by_id["blue_box"].destination == "shelf_2"
    for target in result.targets:
        assert target.destination in KNOWN_LOCATIONS


@pytest.mark.safety
def test_checked_in_dev_calibration_artifact_matches_fixture_geometry():
    """The committed ``config/dev/calibration/dev-sim-v1.yaml`` (doc08 §3 dev artifact) parses
    to exactly the 5 frozen fields with the verified geometry; the stem IS the camera_id."""
    artifact_path = _REPO_ROOT / "config" / "dev" / "calibration" / f"{CALIBRATION_ID}.yaml"
    parsed = yaml.safe_load(artifact_path.read_text(encoding="utf-8"))
    assert set(parsed) == {
        "camera_id",
        "map_frame",
        "homography",
        "reprojection_error",
        "valid_polygon",
    }
    assert parsed["camera_id"] == artifact_path.stem == CALIBRATION_ID
    # R-26 self-cert hole guard: the dev artifact must carry a real, finite reprojection_error
    # (a ``null`` here would skip resolver.py:172 Gate 2 — the exact hole #416 closes).
    assert isinstance(parsed["reprojection_error"], float)
    assert math.isfinite(parsed["reprojection_error"])
    assert parsed == yaml.safe_load(dev_calibration_yaml())


@pytest.mark.unit
def test_cfg_locations_match_base_yaml():
    """Anti-drift oracle: the fixture ``locations`` block equals the bringup-owned
    ``config/warehouse.base.yaml`` block byte-for-byte in parsed form (doc08 §3: derive, don't
    invent)."""
    base = yaml.safe_load(
        (_REPO_ROOT / "config" / "warehouse.base.yaml").read_text(encoding="utf-8")
    )
    assert base["locations"] == BASE_LOCATIONS
    assert set(BASE_LOCATIONS) == KNOWN_LOCATIONS  # frozen vocabulary, locations.py:23


@pytest.mark.unit
def test_cfg_mode_x_er_block_matches_doc08_frozen_keys(tmp_path: Path):
    """The frozen doc08 §3 key set, exactly — a key added/renamed by any lane goes red here."""
    cfg = write_x_er_cfg_tree(tmp_path)
    assert set(cfg) == {"locations", "mode_x_er"}
    assert set(cfg["mode_x_er"]) == {
        "enabled",
        "execution_profile",
        "calibration_id",
        "visual",
        "run_manifest",
        "plugin_manifests",
        "site_profile",
    }
    assert set(cfg["mode_x_er"]["visual"]) == {"snap_radius_m"}
    assert set(cfg["mode_x_er"]["site_profile"]) == {"base_dir", "customer", "site"}
    assert cfg["mode_x_er"]["execution_profile"] == "x_lite"
    assert cfg["mode_x_er"]["calibration_id"] == CALIBRATION_ID


@pytest.mark.unit
def test_write_x_er_cfg_tree_materializes_loadable_artifacts(tmp_path: Path):
    cfg = write_x_er_cfg_tree(tmp_path)
    manifest = load_run_manifest_text(
        Path(cfg["mode_x_er"]["run_manifest"]).read_text(encoding="utf-8")
    )
    plugin_manifests = load_plugin_manifests(
        [Path(p).read_text(encoding="utf-8") for p in cfg["mode_x_er"]["plugin_manifests"]]
    )
    registry, _report = build_plugin_code_registry(manifest, plugin_manifests)
    assert registry.is_registered(X_ER_PLUGIN_ID)
    site = cfg["mode_x_er"]["site_profile"]
    profile = load_site_profile(Path(site["base_dir"]), site["customer"], site["site"])
    verify_against_approved(
        profile,
        compute_content_hash(profile),
        load_approved_record(Path(site["base_dir"]), site["customer"], site["site"]),
    ).assert_verified()
