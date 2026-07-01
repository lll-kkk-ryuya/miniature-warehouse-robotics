"""Visual Resolver output models (XER3, doc02:126-132,143,252).

The Visual Resolver turns an image-space object target (a pixel detection) into a *map*
target and snaps it to a frozen ``KNOWN_LOCATIONS`` key, or marks it ``unresolved`` so it
is NEVER actuated (the 0-dispatch path, doc02:151,68).

Bridge-local (発明), NOT a frozen ``warehouse_interfaces`` contract: doc02:5 declares every
schema/class/interface in doc02 internal/illustrative. These models stay in the bridge until
XER1-XER2 stabilize the shape (models/base.py:8-12, doc02:278). They reuse the frozen location
*vocabulary* (``KNOWN_LOCATIONS``) but invent no new location.

Field shape source of truth (docs, not invented):
- ``ResolvedTarget`` keys ``target_id / resolution / destination / confidence / reason``:
  doc02:126-131 (the output example).
- ``ResolutionResult`` wraps ``list[ResolvedTarget]`` as the ``resolve()`` return: doc02:252.

ADJUDICATED (recorded here per docs-first): the resolved-target kind key is ``"resolution"``
(doc02:128 + docs/mode-x/08x-robotics-bridge-mode-x.md:280 = 2 independent sources), NOT
``"kind"``. doc02:211 (and docs/mode-x/08x:370,538) spell it ``"kind"`` inside the *Command
Compiler*'s nested ``resolved_target`` example (a different, downstream object). This module
uses ``resolution``; reconciling the ``"kind"`` spellings is a docs-reconcile follow-up
(CLAUDE.md).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from warehouse_llm_bridge.robotics_planning_core.models.base import _BridgeModel


class Resolution(StrEnum):
    """The two ``ResolvedTarget.resolution`` values (doc02:128,151).

    bridge-local (発明), not frozen. ``StrEnum`` mirrors the frozen-contract convention
    (schemas.py:17,135; doc06 §1:52). Only ``KNOWN_LOCATION`` carries a ``destination``;
    ``UNRESOLVED`` is the 0-dispatch path and NEVER yields one (doc02:151,68).
    """

    KNOWN_LOCATION = "known_location"
    UNRESOLVED = "unresolved"


class UnresolvedReason(StrEnum):
    """Why a target could not be snapped to a known location (doc02:151).

    bridge-local (発明), not frozen. doc02:151 names map-out / outside-valid-polygon /
    reprojection-error-too-large as unresolved causes; ``beyond_snap_radius`` (the snap
    distance gate, doc02:150) and ``no_calibration`` (empty/degenerate homography, doc02:148)
    are this slice's explicit reason codes for the same fail-closed behaviour. Stable strings
    so audit / operators can explain a 0-dispatch (doc02:99,107).
    """

    OFF_MAP = "off_map"
    OUTSIDE_VALID_POLYGON = "outside_valid_polygon"
    BEYOND_SNAP_RADIUS = "beyond_snap_radius"
    REPROJECTION_ERROR_TOO_LARGE = "reprojection_error_too_large"
    NO_CALIBRATION = "no_calibration"


class ResolvedTarget(_BridgeModel):
    """One resolved (or unresolved) visual target (doc02:126-131).

    bridge-local (発明), not a frozen ``warehouse_interfaces`` contract (doc02:5).

    Invariant (0-dispatch, doc02:151,68): if ``resolution`` is ``unresolved`` then
    ``destination`` is ``None``. An unresolved target never reaches actuation; the Command
    Compiler only compiles ``resolution == known_location`` (doc06:126). This invariant is
    enforced by the resolver, which only ever sets ``destination`` on the known-location path.
    """

    target_id: str
    resolution: Resolution
    destination: str | None = None
    confidence: float
    reason: str

    @model_validator(mode="after")
    def _unresolved_has_no_destination(self) -> Self:
        """Type-enforce the 0-dispatch invariant (doc02:151,68).

        Defense-in-depth: the resolver already only ever sets ``destination`` on the
        known-location path, but pinning the invariant on the model itself means an
        ``unresolved`` target can NEVER carry a destination regardless of construction site —
        the contract is enforced, not merely conventional.
        """
        if self.resolution is Resolution.UNRESOLVED and self.destination is not None:
            raise ValueError(
                "unresolved ResolvedTarget must have destination=None (0-dispatch, doc02:151,68)"
            )
        return self


class ResolutionResult(_BridgeModel):
    """The ``VisualTaskResolver.resolve()`` return: a list of resolved targets (doc02:252).

    bridge-local (発明), not frozen (doc02:5). Wraps ``list[ResolvedTarget]`` so the return is
    one typed object (the documented signature is ``resolve(...) -> ResolutionResult``,
    doc02:251-252) rather than a bare list, leaving room for audit metadata later without a
    breaking change.
    """

    targets: list[ResolvedTarget] = Field(default_factory=list)
