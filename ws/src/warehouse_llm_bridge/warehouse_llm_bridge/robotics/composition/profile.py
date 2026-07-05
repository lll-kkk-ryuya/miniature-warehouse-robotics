"""Site profile loader + version/content-hash + approved-hash verification (S3 spike).

Closes reproducibility gap ①: the run manifest pins *plugins* by version
(``id + version + profile`` — docs/productization/09-run-manifest-and-plugin-composition.md:142)
but names the *profile* by a bare name, while claiming the run can be reproduced later
(same doc :171-174). If an SI edits ``safety.yaml`` / ``calibration.json`` on site, the manifest
line is unchanged and the "reproducible" claim becomes false. This module gives the profile
bundle an identity (customer/site/version) plus a **content hash**, and a fail-closed
``verify_against_approved`` gate so a drifted bundle cannot silently pass for the approved one
(promotion lifecycle ``draft -> ... -> approved`` and rollback to a previous *profile version*:
docs/productization/10-llm-assisted-rule-authoring.md:151-153).

Docs grounding:
- Bundle shape ``site_profiles/<customer>/<site>/`` with locations/robots/cameras/calibration/
  safety/traffic/nav2/eval/plugin_profiles artifacts:
  docs/productization/04-box-storage-and-reuse-guidelines.md:87-103.
- "run manifest へ渡す profile 名" (the profile is referenced by name from the manifest):
  docs/productization/04:114.
- Merge semantics mirror the project-canonical config overlay ("base + overlay, 後勝ち"):
  .claude/rules/environments.md:9. The *merged* profile is what a run effectively consumes.

Two content-hash methods are implemented **side by side** (open question Q2) so tests can
measure what each detects and misses:
- (a) ``merged_canonical``: SHA-256 of the canonical JSON (sorted keys, compact separators) of
  the deep-merged parsed profile — pins the *effective semantic composition* (whitespace /
  comment churn invisible; catches merge-order swaps that change effective values; cannot
  attribute a change to a file).
- (b) ``files``: SHA-256 per artifact file over raw bytes — byte-exact review pin with per-file
  attribution (catches comment/whitespace edits; blind to merge-order swaps because no file
  byte changes).

bridge-local (proposal-status docs, doc04:5 / doc09:5-8): nothing here is promoted to
``warehouse_interfaces`` and no new config key / ROS topic is added.

Design proposals recorded here (not defined by docs — flagged for the docs lane):
- Profile *version* is read from an optional ``profile.yaml`` (key ``version``) at the bundle
  root; doc04:87-103 does not define a version artifact yet doc10:153 requires rollback "to the
  previous profile version".
- The *approved* record lives in ``APPROVED.yaml`` at the bundle root (excluded from the hashed
  content), written only by the human review step (doc10:143-145); it is a review artifact, not
  runtime config.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from warehouse_llm_bridge.robotics_planning_core.models.base import _BridgeModel

# Bundle-root artifacts with special roles (design proposals, see module docstring).
PROFILE_MANIFEST_FILENAME = "profile.yaml"
APPROVED_RECORD_FILENAME = "APPROVED.yaml"

# Artifacts whose drift is safety-relevant: a mismatch on these must FAIL preflight, not merely
# be recorded (they carry safety thresholds and the pixel->map calibration that steers real
# motion; doc04:105-112 lists both as site-profile content).
SAFETY_CRITICAL_ARTIFACTS: frozenset[str] = frozenset({"safety.yaml", "calibration.json"})

_ARTIFACT_SUFFIXES = (".yaml", ".yml", ".json")

HASH_ALGORITHM = "sha256"


class SiteProfileError(ValueError):
    """A site profile bundle is missing, unreadable, or unparseable (fail-closed)."""


class SiteProfile(_BridgeModel):
    """A loaded ``site_profiles/<customer>/<site>/`` bundle (doc04:87-103).

    ``files`` maps the artifact's bundle-relative POSIX path to its raw text. The approved
    record (``APPROVED.yaml``) is NOT part of ``files``: it attests the content, so it must not
    hash itself.
    """

    customer: str
    site: str
    version: str | None = None
    files: dict[str, str] = Field(default_factory=dict)

    @property
    def profile_key(self) -> str:
        """Stable identity key ``<customer>/<site>`` (the doc04:87-88 directory levels)."""
        return f"{self.customer}/{self.site}"


class ProfileContentHash(_BridgeModel):
    """Both content-hash methods, computed together (open question Q2: keep both).

    ``merged_canonical`` pins the effective semantic composition; ``files`` gives byte-exact,
    per-file attribution. ``merge_order`` records how the merged view was composed (it is NOT
    folded into ``merged_canonical`` so tests can measure order-swap detection honestly).
    """

    algorithm: str = HASH_ALGORITHM
    merged_canonical: str
    files: dict[str, str] = Field(default_factory=dict)
    merge_order: list[str] = Field(default_factory=list)


class ApprovedProfileRecord(_BridgeModel):
    """The reviewed-and-approved pin for one profile bundle (doc10:151-153 ``approved``).

    Written by the human review step (doc10:143-145), never by the runtime. ``content_hash``
    is the full two-method hash so verification can both gate (merged) and attribute (files).
    """

    customer: str
    site: str
    version: str | None = None
    content_hash: ProfileContentHash
    approved_by: str | None = None
    approved_at: str | None = None


class VerificationStatus(StrEnum):
    """Outcome of comparing a loaded bundle against its approved record (fail-closed)."""

    VERIFIED = "verified"
    MISMATCH = "mismatch"
    UNAPPROVED = "unapproved"
    IDENTITY_MISMATCH = "identity_mismatch"


class ProfileVerification(_BridgeModel):
    """Result of :func:`verify_against_approved` — gate verdict + per-file attribution."""

    status: VerificationStatus
    merged_match: bool = False
    changed_files: list[str] = Field(default_factory=list)
    added_files: list[str] = Field(default_factory=list)
    removed_files: list[str] = Field(default_factory=list)
    safety_critical_mismatch: bool = False

    @property
    def permits_run(self) -> bool:
        """True iff the bundle is byte- and semantics-identical to the approved record."""
        return self.status is VerificationStatus.VERIFIED

    def assert_verified(self) -> None:
        """Fail-closed guard: raise unless the bundle matches its approved record."""
        if not self.permits_run:
            raise SiteProfileError(
                f"site profile verification failed: status={self.status.value} "
                f"changed={self.changed_files} added={self.added_files} "
                f"removed={self.removed_files}"
            )


# --- loading ---------------------------------------------------------------------------------


def load_site_profile(base_dir: Path, customer: str, site: str) -> SiteProfile:
    """Load the ``site_profiles/<customer>/<site>/`` bundle (doc04:87-103) into a model.

    Collects every ``*.yaml`` / ``*.yml`` / ``*.json`` artifact under the bundle root
    (recursively, so ``nav2/`` and ``plugin_profiles/`` are included — doc04:97-102), excluding
    the approved record. The profile version comes from the optional ``profile.yaml`` ``version``
    key (design proposal, module docstring). Missing bundle => :class:`SiteProfileError`.
    """
    root = base_dir / customer / site
    if not root.is_dir():
        raise SiteProfileError(f"site profile bundle not found: {root}")
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _ARTIFACT_SUFFIXES:
            continue
        rel = path.relative_to(root).as_posix()
        if rel == APPROVED_RECORD_FILENAME:
            continue
        files[rel] = path.read_text(encoding="utf-8")
    version = _read_version(files)
    return SiteProfile(customer=customer, site=site, version=version, files=files)


def _read_version(files: Mapping[str, str]) -> str | None:
    """Extract the ``version`` key from ``profile.yaml`` if the bundle carries one."""
    raw = files.get(PROFILE_MANIFEST_FILENAME)
    if raw is None:
        return None
    parsed = _parse_artifact(PROFILE_MANIFEST_FILENAME, raw)
    if isinstance(parsed, Mapping):
        version = parsed.get("version")
        if version is not None:
            return str(version)
    return None


def load_approved_record(base_dir: Path, customer: str, site: str) -> ApprovedProfileRecord | None:
    """Read the bundle's ``APPROVED.yaml`` review pin, or ``None`` when never approved."""
    path = base_dir / customer / site / APPROVED_RECORD_FILENAME
    if not path.is_file():
        return None
    parsed = _parse_artifact(APPROVED_RECORD_FILENAME, path.read_text(encoding="utf-8"))
    if not isinstance(parsed, Mapping):
        raise SiteProfileError(f"approved record is not a mapping: {path}")
    return ApprovedProfileRecord.model_validate(parsed)


# --- canonicalization + hashing --------------------------------------------------------------


def canonical_json(data: object) -> str:
    """Canonical JSON: sorted keys, compact separators, ASCII, no NaN (deterministic).

    ``allow_nan=False`` makes a NaN/Infinity threshold fail loudly at hash time instead of
    producing a non-portable canonical form (fail-closed).
    """
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def _sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _parse_artifact(rel_path: str, raw: str) -> Any:
    """Parse one artifact (YAML or JSON) fail-closed: unparseable => :class:`SiteProfileError`."""
    try:
        if rel_path.lower().endswith(".json"):
            return json.loads(raw) if raw.strip() else None
        return yaml.safe_load(raw)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise SiteProfileError(f"unparseable profile artifact {rel_path}: {exc}") from exc


def deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Recursive last-wins mapping merge — the project's config overlay semantics
    (.claude/rules/environments.md:9 "base + overlay ... 後勝ち")."""
    merged: dict[str, Any] = dict(base)
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def resolve_merge_order(profile: SiteProfile, merge_order: Sequence[str] | None) -> list[str]:
    """Resolve the file merge order: explicit entries first, the rest appended sorted.

    Every explicit entry must exist in the bundle (fail-closed: a stale order list referencing
    a removed artifact is a composition error, not something to skip silently).
    """
    if merge_order is None:
        return sorted(profile.files)
    order: list[str] = []
    for rel in merge_order:
        if rel not in profile.files:
            raise SiteProfileError(f"merge_order references a missing artifact: {rel}")
        if rel not in order:
            order.append(rel)
    order.extend(rel for rel in sorted(profile.files) if rel not in order)
    return order


def merged_profile_view(
    profile: SiteProfile, merge_order: Sequence[str] | None = None
) -> dict[str, Any]:
    """The effective merged profile dict a run consumes (deep-merged, last wins).

    Mapping documents merge into the shared namespace (overlay semantics); a non-mapping
    document (list/scalar top level) is kept addressable under the reserved
    ``__documents__.<rel_path>`` key so no artifact silently drops out of the hash.
    """
    merged: dict[str, Any] = {}
    for rel in resolve_merge_order(profile, merge_order):
        parsed = _parse_artifact(rel, profile.files[rel])
        if parsed is None:
            continue
        if isinstance(parsed, Mapping):
            merged = deep_merge(merged, parsed)
        else:
            merged = deep_merge(merged, {"__documents__": {rel: parsed}})
    return merged


def compute_content_hash(
    profile: SiteProfile, merge_order: Sequence[str] | None = None
) -> ProfileContentHash:
    """Compute BOTH hash methods (Q2): merged-canonical SHA-256 + per-file raw SHA-256."""
    order = resolve_merge_order(profile, merge_order)
    merged = merged_profile_view(profile, order)
    merged_hash = _sha256_hex(canonical_json(merged).encode("utf-8"))
    file_hashes = {rel: _sha256_hex(text.encode("utf-8")) for rel, text in profile.files.items()}
    return ProfileContentHash(merged_canonical=merged_hash, files=file_hashes, merge_order=order)


# --- approval + verification -----------------------------------------------------------------


def approve(
    profile: SiteProfile,
    content_hash: ProfileContentHash,
    *,
    approved_by: str,
    approved_at: str,
) -> ApprovedProfileRecord:
    """Build the review pin for a bundle (pure — persistence stays a human review act,
    doc10:143-145; the runtime never writes ``APPROVED.yaml``)."""
    return ApprovedProfileRecord(
        customer=profile.customer,
        site=profile.site,
        version=profile.version,
        content_hash=content_hash,
        approved_by=approved_by,
        approved_at=approved_at,
    )


def verify_against_approved(
    profile: SiteProfile,
    content_hash: ProfileContentHash,
    approved: ApprovedProfileRecord | None,
) -> ProfileVerification:
    """Fail-closed comparison of a loaded bundle against its approved record.

    - No approved record => ``unapproved`` (a never-reviewed bundle must not pass as reviewed,
      doc09:176-179 "approved 以上だけ run manifest で有効化").
    - Wrong customer/site/version identity => ``identity_mismatch``.
    - Any merged or per-file difference => ``mismatch`` with per-file attribution (method (b))
      and a ``safety_critical_mismatch`` flag for :data:`SAFETY_CRITICAL_ARTIFACTS`.
    """
    if approved is None:
        return ProfileVerification(status=VerificationStatus.UNAPPROVED)
    if (
        approved.customer != profile.customer
        or approved.site != profile.site
        or approved.version != profile.version
    ):
        return ProfileVerification(status=VerificationStatus.IDENTITY_MISMATCH)

    merged_match = approved.content_hash.merged_canonical == content_hash.merged_canonical
    approved_files = approved.content_hash.files
    current_files = content_hash.files
    changed = sorted(
        rel
        for rel in approved_files.keys() & current_files.keys()
        if approved_files[rel] != current_files[rel]
    )
    added = sorted(current_files.keys() - approved_files.keys())
    removed = sorted(approved_files.keys() - current_files.keys())
    clean = merged_match and not changed and not added and not removed
    touched = set(changed) | set(added) | set(removed)
    return ProfileVerification(
        status=VerificationStatus.VERIFIED if clean else VerificationStatus.MISMATCH,
        merged_match=merged_match,
        changed_files=changed,
        added_files=added,
        removed_files=removed,
        safety_critical_mismatch=bool(touched & SAFETY_CRITICAL_ARTIFACTS),
    )


# --- effective-composition record shape (S2 integration proposal) ----------------------------


def composition_record(
    profile: SiteProfile,
    content_hash: ProfileContentHash,
    verification: ProfileVerification,
    approved: ApprovedProfileRecord | None = None,
    calibration_governance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Proposed ``site_profile`` block for S2's ``out/runs/<run_id>/effective_composition.json``.

    Shape proposal (S3 -> S2 interface): everything Eval/Observability needs to answer
    "which profile content, exactly, did this run use, and was it the approved one?"
    (doc09:171-174). JSON-serializable by construction; S2 embeds it as-is.
    """
    record: dict[str, Any] = {
        "schema_version": "effective_composition.site_profile.s3-proposal",
        "site_profile": {
            "customer": profile.customer,
            "site": profile.site,
            "version": profile.version,
            "content_hash": content_hash.model_dump(),
            "approved": approved.model_dump() if approved is not None else None,
            "verification": verification.model_dump(),
        },
    }
    if calibration_governance is not None:
        record["calibration_governance"] = dict(calibration_governance)
    return record
