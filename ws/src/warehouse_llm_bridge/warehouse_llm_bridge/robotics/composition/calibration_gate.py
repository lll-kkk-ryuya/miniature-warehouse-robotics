"""Calibration governance gate + the (XER6-pending) production ``CalibrationLoader`` wiring (S3 spike).

Closes self-certification gap ②: the Visual Resolver's Gate 2 is driven by the reprojection
error the calibration artifact declares about ITSELF, and skips entirely when that field is
``None`` (``visual_resolver/resolver.py:172`` — ``if calibration.reprojection_error is not
None and ...``). A plausible-but-wrong homography that simply omits its error self-passes the
gate and can snap a detection to a wrong known location => real motion to the wrong place.

This module plugs the hole UPSTREAM, without editing the resolver. STATUS: this loader is NOT
yet on the production dispatch path — on ``origin/main`` ``pipeline.py`` still receives a
``Calibration`` directly (:88,:147) and ``build_calibration_loader`` is called nowhere outside
``composition/`` (wiring = XER6-pending). ONCE WIRED, the production build path will obtain
calibrations only through a :class:`GovernedCalibrationLoader`, which refuses to hand
out any calibration that

- declares no ``reprojection_error`` at all (self-cert skip => reject),
- declares a non-finite error (NaN/inf => reject),
- exceeds the site's configured ceiling (reject),
- or has no ceiling configured for the site (reject — a site that never set the ceiling has
  not reviewed its calibration quality, fail-closed),

unless an **explicit, recorded waiver** admits it (waiver = who/why/when, carried into the
effective-composition record). A waived-``None`` calibration still hits the resolver's own
Gate 2 skip, but now only through a deliberate, attributable decision instead of silence.

Wiring mirrors the landed production factory pattern (config -> constructed object, pure,
injectable, no I/O beyond reading the already-loaded profile bundle):
``robotics/adapter_factory.py:130`` ``build_er_adapter``. It finally *consumes* the
``CalibrationLoader`` seam that ``validator/seams.py:39`` declared but nothing wired
("Default impl is in-memory; a file/version-managed loader replaces it per site",
seams.py:42 / docs/mode-x-er/02-l3-planning-core.md:156 "version-managed file, never
hardcoded").

Design proposals recorded here (not defined by docs — flagged for the docs lane):
- ``calibration.json`` may hold one calibration object, a list, or a ``camera_id -> object``
  mapping (doc04:93-94 names the artifact but not its internal shape).
- The governance policy lives in the site profile's ``safety.yaml`` under
  ``calibration: {max_reprojection_error: <float>, waivers: [...]}`` (doc04:105-110 puts
  "safety threshold" and "camera id と calibration" in the site profile; the exact key path is
  this spike's proposal).

bridge-local (proposal-status docs): nothing here is promoted to ``warehouse_interfaces``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import Field

from warehouse_llm_bridge.robotics.composition.profile import (
    SiteProfile,
    SiteProfileError,
    _parse_artifact,
)
from warehouse_llm_bridge.robotics_planning_core.models.base import _BridgeModel
from warehouse_llm_bridge.robotics_planning_core.validator.seams import (
    Calibration,
    CalibrationLoader,
    InMemoryCalibrationLoader,
)

CALIBRATION_ARTIFACT = "calibration.json"
SAFETY_ARTIFACT = "safety.yaml"
_POLICY_KEY = "calibration"


class CalibrationWaiver(_BridgeModel):
    """An explicit, recorded exception admitting a calibration the gate would reject.

    All fields are provenance: a waiver without who/why/when is not an accountable decision,
    so they are required (the recorded-waiver clause of the profile gate).
    """

    camera_id: str
    reason: str
    approved_by: str
    granted_at: str


class CalibrationGovernancePolicy(_BridgeModel):
    """Site-level calibration admission policy (injected, never hardcoded — doc02 §156 spirit).

    ``max_reprojection_error=None`` means the site never configured a ceiling: the gate then
    rejects every non-waived calibration (fail-closed), it does NOT default to a magic number.
    """

    max_reprojection_error: float | None = None
    waivers: list[CalibrationWaiver] = Field(default_factory=list)

    def waiver_for(self, camera_id: str) -> CalibrationWaiver | None:
        """Return the waiver covering ``camera_id``, if one was granted."""
        for waiver in self.waivers:
            if waiver.camera_id == camera_id:
                return waiver
        return None


class CalibrationDecision(StrEnum):
    """Gate verdict per camera. Only ``accepted``/``waived`` calibrations are loadable."""

    ACCEPTED = "accepted"
    WAIVED = "waived"
    REJECTED = "rejected"


class CalibrationGateEntry(_BridgeModel):
    """One camera's gate outcome — verdict + machine-readable reasons + waiver provenance."""

    camera_id: str
    decision: CalibrationDecision
    reprojection_error: float | None = None
    reasons: list[str] = Field(default_factory=list)
    waiver: CalibrationWaiver | None = None


class CalibrationGateReport(_BridgeModel):
    """All gate outcomes for a bundle — the calibration_governance composition-record block."""

    max_reprojection_error: float | None = None
    entries: list[CalibrationGateEntry] = Field(default_factory=list)

    def entry_for(self, camera_id: str) -> CalibrationGateEntry | None:
        for entry in self.entries:
            if entry.camera_id == camera_id:
                return entry
        return None

    @property
    def admitted_camera_ids(self) -> frozenset[str]:
        return frozenset(
            entry.camera_id
            for entry in self.entries
            if entry.decision is not CalibrationDecision.REJECTED
        )

    def as_composition_block(self) -> dict[str, Any]:
        """JSON-serializable ``calibration_governance`` block for effective_composition.json."""
        return {
            "policy": {"max_reprojection_error": self.max_reprojection_error},
            "cameras": [entry.model_dump() for entry in self.entries],
        }


def gate_calibration(
    calibration: Calibration, policy: CalibrationGovernancePolicy
) -> CalibrationGateEntry:
    """Judge one calibration against the site policy (pure, deterministic, fail-closed).

    Rejection reasons are stable strings so operators/audit can explain a blocked camera
    (mirrors the resolver's stable ``UnresolvedReason`` strings, visual_resolver/models.py).
    """
    error = calibration.reprojection_error
    reasons: list[str] = []
    if error is None:
        # THE hole this gate exists for: a None error would skip resolver.py:172 Gate 2.
        reasons.append("reprojection_error_missing_self_cert")
    elif not math.isfinite(error):
        reasons.append("reprojection_error_not_finite")
    elif policy.max_reprojection_error is None or not math.isfinite(policy.max_reprojection_error):
        reasons.append("no_reprojection_ceiling_configured")
    elif error > policy.max_reprojection_error:
        reasons.append("reprojection_error_above_ceiling")

    if not reasons:
        return CalibrationGateEntry(
            camera_id=calibration.camera_id,
            decision=CalibrationDecision.ACCEPTED,
            reprojection_error=error,
        )
    waiver = policy.waiver_for(calibration.camera_id)
    if waiver is not None:
        return CalibrationGateEntry(
            camera_id=calibration.camera_id,
            decision=CalibrationDecision.WAIVED,
            reprojection_error=error,
            reasons=reasons,
            waiver=waiver,
        )
    return CalibrationGateEntry(
        camera_id=calibration.camera_id,
        decision=CalibrationDecision.REJECTED,
        reprojection_error=error,
        reasons=reasons,
    )


class GovernedCalibrationLoader:
    """A :class:`CalibrationLoader` (seams.py:39 Protocol) that enforces the profile gate.

    ``load`` returns ``None`` for any calibration the gate rejected, so a self-certified-silent
    (``reprojection_error=None``) artifact can never reach ``VisualTaskResolver`` through the
    production path — the resolver.py:172 skip becomes unreachable except via a recorded waiver.
    Decisions are precomputed at construction (deterministic; the report never mutates at
    ``load`` time).
    """

    def __init__(self, inner: CalibrationLoader, policy: CalibrationGovernancePolicy) -> None:
        self._inner = inner
        self._policy = policy
        self._decisions: dict[str, CalibrationGateEntry] = {}

    def load(self, calibration_id: str) -> Calibration | None:
        calibration = self._inner.load(calibration_id)
        if calibration is None:
            return None
        entry = self._decisions.get(calibration_id)
        if entry is None:
            entry = gate_calibration(calibration, self._policy)
            self._decisions[calibration_id] = entry
        if entry.decision is CalibrationDecision.REJECTED:
            return None
        return calibration

    def report(self) -> CalibrationGateReport:
        """Gate outcomes for every calibration judged so far (composition-record source)."""
        return CalibrationGateReport(
            max_reprojection_error=self._policy.max_reprojection_error,
            entries=list(self._decisions.values()),
        )


def governance_policy_from_profile(profile: SiteProfile) -> CalibrationGovernancePolicy:
    """Read the calibration policy from the bundle's ``safety.yaml`` (key path = proposal).

    Missing artifact / missing key / non-mapping shape all yield the empty policy
    (``max_reprojection_error=None``, no waivers) — which the gate treats as reject-all
    (fail-closed), never as a permissive default.
    """
    raw = profile.files.get(SAFETY_ARTIFACT)
    if raw is None:
        return CalibrationGovernancePolicy()
    parsed = _parse_artifact(SAFETY_ARTIFACT, raw)
    if not isinstance(parsed, Mapping):
        return CalibrationGovernancePolicy()
    section = parsed.get(_POLICY_KEY)
    if not isinstance(section, Mapping):
        return CalibrationGovernancePolicy()
    return CalibrationGovernancePolicy.model_validate(section)


def parse_calibrations(profile: SiteProfile) -> dict[str, Calibration]:
    """Parse ``calibration.json`` into ``camera_id -> Calibration`` (shapes: see module doc).

    Indexed by the artifact's own ``camera_id`` field (the seams.py:31 identity); a mapping key
    that disagrees with the embedded ``camera_id`` is a bundle inconsistency => fail-closed.
    Absent artifact => empty dict (a site without cameras is legal, doc04 bundle is a superset).
    """
    raw = profile.files.get(CALIBRATION_ARTIFACT)
    if raw is None:
        return {}
    parsed = _parse_artifact(CALIBRATION_ARTIFACT, raw)
    if parsed is None:
        return {}
    items: list[Any]
    if isinstance(parsed, Mapping):
        if "camera_id" in parsed:
            items = [parsed]
        else:
            items = []
            for key, value in parsed.items():
                if not isinstance(value, Mapping):
                    raise SiteProfileError(
                        f"{CALIBRATION_ARTIFACT}: entry {key!r} is not a calibration object"
                    )
                if "camera_id" in value and value["camera_id"] != key:
                    raise SiteProfileError(
                        f"{CALIBRATION_ARTIFACT}: key {key!r} disagrees with embedded "
                        f"camera_id {value['camera_id']!r}"
                    )
                items.append({**value, "camera_id": value.get("camera_id", key)})
    elif isinstance(parsed, list):
        items = parsed
    else:
        raise SiteProfileError(f"{CALIBRATION_ARTIFACT}: unsupported top-level shape")

    calibrations: dict[str, Calibration] = {}
    for item in items:
        calibration = Calibration.model_validate(item)
        if calibration.camera_id in calibrations:
            raise SiteProfileError(
                f"{CALIBRATION_ARTIFACT}: duplicate camera_id {calibration.camera_id!r}"
            )
        calibrations[calibration.camera_id] = calibration
    return calibrations


def build_calibration_loader(
    profile: SiteProfile,
    *,
    policy: CalibrationGovernancePolicy | None = None,
) -> GovernedCalibrationLoader:
    """Construct the production calibration loader from a loaded site profile.

    The ``adapter_factory.build_er_adapter`` pattern (adapter_factory.py:130): config/profile in,
    constructed seam out, pure, injectable (``policy`` overrides the profile-derived one for
    tests). Every calibration is gated at build time so the report is complete even for cameras
    never ``load``-ed.
    """
    resolved_policy = policy if policy is not None else governance_policy_from_profile(profile)
    calibrations = parse_calibrations(profile)
    loader = GovernedCalibrationLoader(InMemoryCalibrationLoader(calibrations), resolved_policy)
    for camera_id in calibrations:
        loader.load(camera_id)  # precompute the gate decision (report completeness)
    return loader
