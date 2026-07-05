"""Site profile loader + two content-hash methods + fail-closed verification (S3 spike).

Measures open question Q2 with an executable comparison matrix — which tamper class each hash
method detects/misses:

| Case                                            | merged-canonical | per-file raw |
|-------------------------------------------------|------------------|--------------|
| 1-char threshold edit in safety.yaml            | DETECTS          | DETECTS      |
| comment / whitespace-only edit                  | misses           | DETECTS      |
| merge-order swap, overlapping key (value flips) | DETECTS          | misses       |
| merge-order swap, disjoint keys (same result)   | misses           | misses (semantic no-op) |
| which file changed (attribution)                | no               | yes          |

Grounding: bundle shape docs/productization/04-box-storage-and-reuse-guidelines.md:87-103;
run-manifest reproducibility claim docs/productization/09-run-manifest-and-plugin-composition.md:171-174;
approved-only enablement doc09:176-179; lifecycle/rollback doc10:151-153. Offline, no ROS.
"""

import json
from pathlib import Path

import pytest
from warehouse_llm_bridge.robotics.composition.profile import (
    ApprovedProfileRecord,
    SiteProfileError,
    VerificationStatus,
    approve,
    canonical_json,
    composition_record,
    compute_content_hash,
    load_approved_record,
    load_site_profile,
    verify_against_approved,
)

CUSTOMER = "customer_a"
SITE = "site_01"

SAFETY_YAML = """\
# Site safety thresholds (site profile artifact, doc04:96)
emergency_min_distance: 0.3
calibration:
  max_reprojection_error: 3.0
"""

CALIBRATION_JSON = json.dumps(
    {
        "camera_id": "cam_overhead",
        "map_frame": "map",
        "homography": [[0.01, 0.0, 0.0], [0.0, 0.01, 0.0], [0.0, 0.0, 1.0]],
        "reprojection_error": 1.2,
        "valid_polygon": [[0.0, 0.0], [2.0, 0.0], [2.0, 1.0], [0.0, 1.0]],
    },
    indent=2,
)

LOCATIONS_YAML = """\
locations:
  shelf_1: [0.5, 0.3]
  shelf_2: [1.2, 0.6]
"""

PROFILE_YAML = """\
version: "1.2.0"
"""

# Two plugin profiles sharing the ``defaults.snap_radius_m`` key: merge order decides the
# effective value (last wins, .claude/rules/environments.md:9 overlay semantics).
ZONE_POLICY_YAML = """\
defaults:
  snap_radius_m: 0.25
profiles:
  customer_a:
    zone_artifact: zones/zone_a.geojson
    target_rules:
      red_box:
        must_be_inside: zone_a
"""

VISUAL_PROFILE_YAML = """\
defaults:
  snap_radius_m: 0.30
profiles:
  customer_a:
    max_reprojection_error: 3.0
"""


def _write_bundle(base: Path) -> Path:
    root = base / CUSTOMER / SITE
    (root / "plugin_profiles").mkdir(parents=True)
    (root / "profile.yaml").write_text(PROFILE_YAML, encoding="utf-8")
    (root / "safety.yaml").write_text(SAFETY_YAML, encoding="utf-8")
    (root / "calibration.json").write_text(CALIBRATION_JSON, encoding="utf-8")
    (root / "locations.yaml").write_text(LOCATIONS_YAML, encoding="utf-8")
    (root / "plugin_profiles" / "l3_zone_policy.yaml").write_text(
        ZONE_POLICY_YAML, encoding="utf-8"
    )
    (root / "plugin_profiles" / "l3_visual_resolver.yaml").write_text(
        VISUAL_PROFILE_YAML, encoding="utf-8"
    )
    return root


@pytest.fixture()
def bundle(tmp_path: Path) -> Path:
    _write_bundle(tmp_path)
    return tmp_path


def _approved(base: Path) -> ApprovedProfileRecord:
    profile = load_site_profile(base, CUSTOMER, SITE)
    content = compute_content_hash(profile)
    return approve(profile, content, approved_by="reviewer", approved_at="2026-07-01")


# --- loading ----------------------------------------------------------------------------------


def test_load_bundle_collects_artifacts_and_version(bundle: Path):
    profile = load_site_profile(bundle, CUSTOMER, SITE)
    assert profile.customer == CUSTOMER
    assert profile.site == SITE
    assert profile.version == "1.2.0"
    assert set(profile.files) == {
        "profile.yaml",
        "safety.yaml",
        "calibration.json",
        "locations.yaml",
        "plugin_profiles/l3_zone_policy.yaml",
        "plugin_profiles/l3_visual_resolver.yaml",
    }


def test_missing_bundle_fails_closed(tmp_path: Path):
    with pytest.raises(SiteProfileError, match="not found"):
        load_site_profile(tmp_path, "nobody", "nowhere")


def test_approved_record_is_excluded_from_hashed_content(bundle: Path):
    profile = load_site_profile(bundle, CUSTOMER, SITE)
    before = compute_content_hash(profile)
    record = _approved(bundle)
    approved_path = bundle / CUSTOMER / SITE / "APPROVED.yaml"
    approved_path.write_text(
        json.dumps(record.model_dump()), encoding="utf-8"
    )  # JSON is valid YAML
    reloaded = load_site_profile(bundle, CUSTOMER, SITE)
    after = compute_content_hash(reloaded)
    assert "APPROVED.yaml" not in reloaded.files
    assert after == before  # the attestation must not hash itself
    loaded = load_approved_record(bundle, CUSTOMER, SITE)
    assert loaded is not None and loaded.content_hash == record.content_hash


def test_unparseable_artifact_fails_closed(bundle: Path):
    (bundle / CUSTOMER / SITE / "safety.yaml").write_text("a: [unclosed", encoding="utf-8")
    profile = load_site_profile(bundle, CUSTOMER, SITE)
    with pytest.raises(SiteProfileError, match="unparseable"):
        compute_content_hash(profile)


# --- canonical JSON ---------------------------------------------------------------------------


def test_canonical_json_is_key_order_independent_and_compact():
    assert canonical_json({"b": 1, "a": [1, 2]}) == canonical_json({"a": [1, 2], "b": 1})
    assert canonical_json({"a": 1}) == '{"a":1}'


def test_canonical_json_rejects_nan_fail_closed():
    with pytest.raises(ValueError):
        canonical_json({"threshold": float("nan")})


# --- Q2 comparison matrix ---------------------------------------------------------------------


def test_one_char_threshold_tamper_detected_by_both_and_fails_closed(bundle: Path):
    """Row 1: SI flips ``0.3`` -> ``0.4`` in safety.yaml. Both methods detect; verify FAILs."""
    approved = _approved(bundle)
    safety = bundle / CUSTOMER / SITE / "safety.yaml"
    safety.write_text(safety.read_text().replace("0.3", "0.4", 1), encoding="utf-8")

    tampered = load_site_profile(bundle, CUSTOMER, SITE)
    content = compute_content_hash(tampered)
    assert content.merged_canonical != approved.content_hash.merged_canonical  # (a) detects
    assert content.files["safety.yaml"] != approved.content_hash.files["safety.yaml"]  # (b)

    verification = verify_against_approved(tampered, content, approved)
    assert verification.status is VerificationStatus.MISMATCH
    assert verification.changed_files == ["safety.yaml"]
    assert verification.safety_critical_mismatch is True
    assert not verification.permits_run
    with pytest.raises(SiteProfileError, match="verification failed"):
        verification.assert_verified()


def test_comment_only_edit_detected_only_by_file_hash(bundle: Path):
    """Row 2: comment churn — semantics identical => merged hash misses, file hash detects."""
    approved = _approved(bundle)
    safety = bundle / CUSTOMER / SITE / "safety.yaml"
    safety.write_text("# reviewed on site 2026-07-02\n" + safety.read_text(), encoding="utf-8")

    edited = load_site_profile(bundle, CUSTOMER, SITE)
    content = compute_content_hash(edited)
    assert content.merged_canonical == approved.content_hash.merged_canonical  # (a) misses
    assert content.files["safety.yaml"] != approved.content_hash.files["safety.yaml"]  # (b)

    verification = verify_against_approved(edited, content, approved)
    assert verification.merged_match is True
    assert verification.status is VerificationStatus.MISMATCH  # file method still gates it
    assert verification.changed_files == ["safety.yaml"]


def test_merge_order_swap_overlapping_key_detected_only_by_merged_hash(bundle: Path):
    """Row 3: same bytes, different merge order over an overlapping key => only (a) detects."""
    profile = load_site_profile(bundle, CUSTOMER, SITE)
    zone = "plugin_profiles/l3_zone_policy.yaml"  # defaults.snap_radius_m: 0.25
    visual = "plugin_profiles/l3_visual_resolver.yaml"  # defaults.snap_radius_m: 0.30
    rest = sorted(set(profile.files) - {zone, visual})
    order_a = compute_content_hash(profile, merge_order=[zone, visual, *rest])
    order_b = compute_content_hash(profile, merge_order=[visual, zone, *rest])
    assert order_a.files == order_b.files  # (b) blind: no file byte changed
    assert order_a.merged_canonical != order_b.merged_canonical  # (a) catches the flip


def test_merge_order_swap_disjoint_keys_is_a_semantic_noop_missed_by_both(bundle: Path):
    """Row 4: order swap of files with disjoint keys — effective composition identical."""
    profile = load_site_profile(bundle, CUSTOMER, SITE)
    safety = "safety.yaml"
    locations = "locations.yaml"
    rest = sorted(set(profile.files) - {safety, locations})
    order_a = compute_content_hash(profile, merge_order=[safety, locations, *rest])
    order_b = compute_content_hash(profile, merge_order=[locations, safety, *rest])
    assert order_a.files == order_b.files
    assert order_a.merged_canonical == order_b.merged_canonical


def test_merged_hash_has_no_attribution_file_hashes_pinpoint_the_artifact(bundle: Path):
    """Row 5: only the per-file method says WHICH artifact drifted (calibration.json here)."""
    approved = _approved(bundle)
    calib = bundle / CUSTOMER / SITE / "calibration.json"
    calib.write_text(calib.read_text().replace("1.2", "1.3", 1), encoding="utf-8")

    tampered = load_site_profile(bundle, CUSTOMER, SITE)
    content = compute_content_hash(tampered)
    # (a) is a single digest: it flags drift but carries zero attribution.
    assert content.merged_canonical != approved.content_hash.merged_canonical
    # (b) localizes the drift to exactly one artifact.
    verification = verify_against_approved(tampered, content, approved)
    assert verification.changed_files == ["calibration.json"]
    assert verification.safety_critical_mismatch is True


# --- fail-closed verification edges -----------------------------------------------------------


def test_untampered_bundle_verifies(bundle: Path):
    approved = _approved(bundle)
    profile = load_site_profile(bundle, CUSTOMER, SITE)
    verification = verify_against_approved(profile, compute_content_hash(profile), approved)
    assert verification.status is VerificationStatus.VERIFIED
    assert verification.permits_run
    verification.assert_verified()  # must not raise


def test_never_approved_bundle_is_unapproved_not_verified(bundle: Path):
    profile = load_site_profile(bundle, CUSTOMER, SITE)
    verification = verify_against_approved(profile, compute_content_hash(profile), None)
    assert verification.status is VerificationStatus.UNAPPROVED
    assert not verification.permits_run


def test_version_bump_without_reapproval_is_identity_mismatch(bundle: Path):
    approved = _approved(bundle)
    (bundle / CUSTOMER / SITE / "profile.yaml").write_text('version: "1.3.0"\n', encoding="utf-8")
    profile = load_site_profile(bundle, CUSTOMER, SITE)
    verification = verify_against_approved(profile, compute_content_hash(profile), approved)
    assert verification.status is VerificationStatus.IDENTITY_MISMATCH
    assert not verification.permits_run


def test_added_and_removed_artifacts_are_attributed(bundle: Path):
    approved = _approved(bundle)
    root = bundle / CUSTOMER / SITE
    (root / "traffic.yaml").write_text("traffic_mode: none\n", encoding="utf-8")
    (root / "locations.yaml").unlink()
    profile = load_site_profile(bundle, CUSTOMER, SITE)
    verification = verify_against_approved(profile, compute_content_hash(profile), approved)
    assert verification.status is VerificationStatus.MISMATCH
    assert verification.added_files == ["traffic.yaml"]
    assert verification.removed_files == ["locations.yaml"]


def test_merge_order_referencing_missing_artifact_fails_closed(bundle: Path):
    profile = load_site_profile(bundle, CUSTOMER, SITE)
    with pytest.raises(SiteProfileError, match="missing artifact"):
        compute_content_hash(profile, merge_order=["deleted.yaml"])


# --- effective-composition record (S2 interface proposal) ------------------------------------


def test_composition_record_is_json_serializable_and_complete(bundle: Path):
    approved = _approved(bundle)
    profile = load_site_profile(bundle, CUSTOMER, SITE)
    content = compute_content_hash(profile)
    verification = verify_against_approved(profile, content, approved)
    record = composition_record(profile, content, verification, approved=approved)

    encoded = json.loads(json.dumps(record))  # must survive a JSON round-trip untouched
    block = encoded["site_profile"]
    assert block["customer"] == CUSTOMER
    assert block["site"] == SITE
    assert block["version"] == "1.2.0"
    assert block["content_hash"]["algorithm"] == "sha256"
    assert block["content_hash"]["merged_canonical"] == content.merged_canonical
    assert block["content_hash"]["files"]["safety.yaml"] == content.files["safety.yaml"]
    assert block["verification"]["status"] == "verified"
    assert block["approved"]["approved_by"] == "reviewer"
