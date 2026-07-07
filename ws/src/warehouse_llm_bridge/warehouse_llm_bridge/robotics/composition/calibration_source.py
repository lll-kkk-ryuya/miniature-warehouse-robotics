"""Production-input seam: a governed :class:`Calibration` for ``compile_raw_output`` (XER6-PENDING).

WHAT THIS IS. ``composition/calibration_gate.py`` builds a
:class:`~warehouse_llm_bridge.robotics.composition.calibration_gate.GovernedCalibrationLoader`
that closes the resolver's self-certification hole UPSTREAM (a ``reprojection_error=None``
calibration skips ``visual_resolver/resolver.py:172`` Gate 2 and snaps to a real known location).
But that loader's ``load`` returns ``None`` for a rejected calibration, whereas the L3 pipeline
entry point demands a concrete artifact: ``pipeline.compile_raw_output(*, calibration: Calibration,
...)`` (pipeline.py:90-93) has NO ``| None`` on ``calibration``. This module is the tiny
composition-root adapter between those two shapes: it turns "the gate rejected this camera" into
a **fail-closed raise** (never a ``None`` that a caller could accidentally paper over) and hands
back only a governance-passed :class:`Calibration` that is safe to feed as
``compile_raw_output(calibration=...)``.

STATUS — XER6-PENDING (DRAFT). The RUNNING caller that would invoke this does NOT exist on
``origin/main``: there is no ``x_er_bridge`` node / composition-root wiring ``compile_raw_output``
into a live cycle (XER6 / #342; ADR-0003 item ``## Consequences`` — "稼働 Bridge cycle
(``compile_raw_output`` を呼ぶ経路) への配線は XER6 pending"). This module provides ONLY the
constructible, unit-tested seam that XER6 will call; it wires nothing into a running node, reads
no config, opens no socket, and dispatches no motion. It mirrors the landed
``robotics/adapter_factory.py:77`` ``build_er_adapter`` pattern (loaded-profile in, constructed
seam out — pure, injectable, offline).

WHY A RAISE, NOT A ``None``. ``GovernedCalibrationLoader.load`` returning ``None`` is the right
shape for a *loader* Protocol (``validator/seams.py:45``: ``load(id) -> Calibration | None``): a
downstream Visual Resolver that gets ``None`` simply resolves nothing (0-dispatch, safe). But the
production BUILD path (``compile_raw_output``) cannot start a cycle without a calibration, and a
silent ``None`` there would be a wiring bug waiting to happen. So the composition-root helper is
fail-closed: an unavailable / rejected calibration is an operational error the operator must see,
raised as :class:`GovernedCalibrationUnavailableError`, never a plan compiled against a missing or
uncertified calibration.

bridge-local (proposal-status docs, doc04:5 / doc09:5-8): nothing here is promoted to
``warehouse_interfaces``; no new config key / ROS topic is added.
"""

from __future__ import annotations

from warehouse_llm_bridge.robotics.composition.calibration_gate import (
    CalibrationDecision,
    CalibrationGateEntry,
    CalibrationGovernancePolicy,
    GovernedCalibrationLoader,
    build_calibration_loader,
)
from warehouse_llm_bridge.robotics.composition.profile import SiteProfile
from warehouse_llm_bridge.robotics_planning_core.validator.seams import Calibration


class GovernedCalibrationUnavailableError(ValueError):
    """No governance-passed calibration exists for a requested ``camera_id`` (fail-closed).

    Raised by :func:`resolve_governed_calibration` when the camera is absent from the profile
    bundle or the governance gate rejected it. It is deliberately NOT a ``None`` return: the
    production build path (``compile_raw_output``) must not start a cycle against a missing or
    uncertified calibration, so an unavailable calibration surfaces as an operational error rather
    than a silent skip. ``entry`` (when present) carries the machine-readable gate reasons so the
    caller / audit can explain the block.
    """

    def __init__(self, camera_id: str, entry: CalibrationGateEntry | None) -> None:
        self.camera_id = camera_id
        self.entry = entry
        if entry is None:
            detail = "no calibration for this camera in the profile bundle"
        else:
            detail = f"gate decision={entry.decision.value} reasons={entry.reasons}"
        super().__init__(f"governed calibration unavailable for {camera_id!r}: {detail}")


def resolve_governed_calibration(
    profile: SiteProfile,
    camera_id: str,
    *,
    policy: CalibrationGovernancePolicy | None = None,
) -> Calibration:
    """Resolve ONE governance-passed :class:`Calibration` for ``compile_raw_output`` (XER6-PENDING).

    The composition-root seam a future ``x_er_bridge`` node will call to obtain the ``calibration=``
    argument for :func:`~warehouse_llm_bridge.robotics_planning_core.pipeline.compile_raw_output`
    (pipeline.py:93). Builds a :class:`GovernedCalibrationLoader` from the loaded ``profile``
    (via :func:`build_calibration_loader`, so the site's ``safety.yaml`` calibration ceiling /
    waivers apply) and returns the calibration for ``camera_id`` ONLY if the gate ACCEPTED or
    WAIVED it. A rejected or absent camera is **fail-closed**: it raises
    :class:`GovernedCalibrationUnavailableError` rather than returning ``None`` — so the self-cert hole
    (``reprojection_error=None`` skipping resolver.py:172 Gate 2) can never reach the resolver
    through this production-input path except via a deliberate, recorded waiver.

    Pure / offline: reads only the already-loaded ``profile`` bundle; no config, network, or
    filesystem I/O; no actuation. XER6-PENDING — no running caller exists on ``origin/main``.

    Args:
        profile: a loaded ``site_profiles/<customer>/<site>/`` bundle
            (:func:`~warehouse_llm_bridge.robotics.composition.profile.load_site_profile`).
        camera_id: the camera whose calibration the resolver needs (the ``Calibration.camera_id``
            identity, ``validator/seams.py:31``).
        policy: OPTIONAL calibration governance policy override, injected for tests; defaults to the
            policy derived from the profile's ``safety.yaml`` (``build_calibration_loader``).

    Returns:
        the accepted-or-waived :class:`Calibration`, safe to pass as
        ``compile_raw_output(calibration=...)``.

    Raises:
        GovernedCalibrationUnavailableError: the camera is not in the bundle, or the gate rejected it.
    """
    loader: GovernedCalibrationLoader = build_calibration_loader(profile, policy=policy)
    calibration = loader.load(camera_id)
    if calibration is None:
        # ``load`` returns None for BOTH "unknown camera" and "gate rejected"; the precomputed
        # report distinguishes them (a rejected camera has an entry, an absent one does not).
        raise GovernedCalibrationUnavailableError(camera_id, loader.report().entry_for(camera_id))
    return calibration


def resolve_governed_calibration_with_loader(
    profile: SiteProfile,
    camera_id: str,
    *,
    policy: CalibrationGovernancePolicy | None = None,
) -> tuple[Calibration, GovernedCalibrationLoader]:
    """Like :func:`resolve_governed_calibration` but also return the built loader (XER6-PENDING).

    XER6's composition root needs the governance witness for TWO purposes: (1) the resolved
    calibration to feed ``compile_raw_output``, and (2) the full gate report to embed in the
    effective-composition record (``GovernedCalibrationLoader.report().as_composition_block()`` —
    the ``calibration_governance`` block of ``effective_composition.json``, calibration_gate.py:140
    / record.py). Returning the loader lets the caller do both from one build instead of gating the
    same profile twice. Same fail-closed contract as :func:`resolve_governed_calibration`.
    """
    loader: GovernedCalibrationLoader = build_calibration_loader(profile, policy=policy)
    calibration = loader.load(camera_id)
    if calibration is None:
        raise GovernedCalibrationUnavailableError(camera_id, loader.report().entry_for(camera_id))
    return calibration, loader


# The waived / accepted decisions the seam admits (rejection -> raise, never returned). Exposed so
# a caller/test can assert on the admitted set without importing the enum members individually.
ADMITTED_DECISIONS = frozenset({CalibrationDecision.ACCEPTED, CalibrationDecision.WAIVED})
