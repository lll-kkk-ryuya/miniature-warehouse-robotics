"""Plugin-emitted validation findings: namespaced codes, policy clamp, fail-closed registry.

Solves the structural gap flagged by adversarial review: a plugin reason_code (e.g.
``target_out_of_zone``, docs/productization/09-run-manifest-and-plugin-composition.md:204)
can NOT enter the frozen ``ValidationCode`` enum (exactly 9, validator/report.py:69-88).
Plugin findings therefore live in a SIBLING typed model family that:

- carries a plugin-namespaced code (``<plugin_id>:<reason_code>``) that is structurally
  disjoint from the 9 frozen codes (lowercase + mandatory ``:`` vs UPPERCASE, no ``:``),
- REUSES the frozen ``DispatchEffect`` / ``Severity`` vocabulary (report.py:46-66) so the
  most-severe-wins aggregation lattice (report.py:98-105, doc02 =
  docs/mode-x-er/02-l3-planning-core.md:304) applies uniformly to core + plugin findings,
- is clamped by a ``PluginDispatchPolicy`` (a plugin REQUESTS an effect; policy is the
  ceiling — ``emergency_stop`` only for allowlisted plugins; clamps are recorded in
  ``clamped_from``),
- fails closed on undeclared codes: a finding whose ``reason_code`` is not in the plugin
  manifest ``emits.reason_codes`` (doc09:201-204) is converted to ``needs_clarification``
  (human review), never silent pass / never auto-emergency
  (docs/productization/10-llm-assisted-rule-authoring.md:394-395 fail-closed principle).

Two variants are implemented ON PURPOSE (design-fork comparison, Grill Q1-(1)):
- Variant A :class:`NamespacedPluginRuleResult` — single ``code`` field holding the full
  ``<plugin_id>:<reason_code>`` string (pattern-validated).
- Variant B :class:`StructuredPluginRuleResult` — separate ``plugin_id`` + ``reason_code``
  fields; the full namespaced code is a derived property. Matches doc10:396 ("same
  reason_code from multiple plugins is distinguished by box / stage / plugin_id") and the
  decision_event shape (docs/productization/05-decision-observability-and-tooling.md:48-64,
  bare ``reason_code`` field + separate attribution fields).

This module is bridge-local; it does not touch ``warehouse_interfaces`` or the frozen
validator vocabulary.
"""

from __future__ import annotations

import re

from pydantic import ConfigDict, Field, field_validator, model_validator

from warehouse_llm_bridge.robotics_planning_core.models.base import _BridgeModel
from warehouse_llm_bridge.robotics_planning_core.validator.report import (
    _EFFECT_TO_STATUS,
    DispatchEffect,
    Severity,
)

# --- code vocabulary patterns --------------------------------------------------------------
# plugin_id follows the manifest form "l3.zone_policy" (doc09:192); reason_code follows the
# manifest emits form "target_out_of_zone" (doc09:204) — lowercase snake_case segments. The
# frozen ValidationCode values are UPPERCASE with no ":" (report.py:79-87), so a namespaced
# plugin code can never collide with (or be smuggled into) the frozen 9.
PLUGIN_ID_PATTERN = r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*"
REASON_CODE_PATTERN = r"[a-z][a-z0-9_]*"
_PLUGIN_ID_RE = re.compile(rf"^{PLUGIN_ID_PATTERN}$")
_REASON_CODE_RE = re.compile(rf"^{REASON_CODE_PATTERN}$")
_NAMESPACED_CODE_RE = re.compile(rf"^{PLUGIN_ID_PATTERN}:{REASON_CODE_PATTERN}$")

# --- composition-reserved reason codes -----------------------------------------------------
# Emitted only by the composition layer itself (never declarable by a plugin manifest):
# fail-closed conversions (doc10:394-395) and crash isolation (Grill Q6 option (b)).
UNDECLARED_REASON_CODE = "undeclared_reason_code"
SPOOFED_PLUGIN_ID_REASON_CODE = "spoofed_plugin_id"
MALFORMED_FINDING_REASON_CODE = "malformed_finding"
PLUGIN_CRASH_REASON_CODE = "plugin_crash"
RESERVED_REASON_CODES: frozenset[str] = frozenset(
    {
        UNDECLARED_REASON_CODE,
        SPOOFED_PLUGIN_ID_REASON_CODE,
        MALFORMED_FINDING_REASON_CODE,
        PLUGIN_CRASH_REASON_CODE,
    }
)

# decision_event constants for this hook point (doc05:55-56 box/stage; doc09:193,199).
VALIDATE_PLAN_BOX = "l3_validator"
VALIDATE_PLAN_STAGE = "validate_plan"

# --- effect severity lattice ---------------------------------------------------------------
# Ordering consistent with the frozen aggregation priority (report.py:98-105 via
# _EFFECT_TO_STATUS + _STATUS_PRIORITY; doc02:304 "emergency_stop > rejected >
# needs_clarification > accepted"). Pinned against the frozen maps by unit test.
EFFECT_ORDER: dict[DispatchEffect, int] = {
    DispatchEffect.NONE: 0,
    DispatchEffect.NEEDS_CLARIFICATION: 1,
    DispatchEffect.BLOCK: 2,
    DispatchEffect.EMERGENCY_STOP: 3,
}


class _PluginFindingBase(_BridgeModel):
    """Shared shape of a plugin finding — mirrors the frozen ``RuleResult`` fields
    (report.py:121-126) minus ``code``/``severity`` (code is variant-specific; severity is
    DERIVED from the effect to remove an inconsistency channel: blocking -> error,
    none -> warning, doc02:312-314).

    Frozen (immutable) for the same defense-in-depth reason as ``RuleResult`` (report.py:114).
    ``clamped_from`` records the originally REQUESTED effect when the policy clamp lowered it
    (None when no clamp happened).
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    message_for_operator: str
    dispatch_effect: DispatchEffect
    field_path: str = ""
    debug_detail: str = ""
    clamped_from: DispatchEffect | None = None

    # NOTE: each variant provides ``plugin_id`` / ``reason_code`` (A: derived properties,
    # B: validated fields). They are deliberately NOT declared here: a base property is a
    # data descriptor and would shadow variant B's pydantic fields.

    @property
    def full_code(self) -> str:
        """The namespaced ``<plugin_id>:<reason_code>`` string (Langfuse-tag friendly)."""
        return f"{self.plugin_id}:{self.reason_code}"

    # -- uniform derived vocabulary --------------------------------------------------------
    @property
    def severity(self) -> Severity:
        """Derived: blocking effects -> error (errors[]), none -> warning (doc02:312-314)."""
        if self.dispatch_effect is DispatchEffect.NONE:
            return Severity.WARNING
        return Severity.ERROR

    @property
    def decision(self) -> str:
        """decision_event fixed vocabulary (doc05:69). Blocking effects map through the
        frozen effect->status table (status literals ARE the decision literals);
        ``none`` maps to ``warning`` (a non-blocking finding)."""
        forced = _EFFECT_TO_STATUS.get(self.dispatch_effect)
        return forced.value if forced is not None else "warning"


class NamespacedPluginRuleResult(_PluginFindingBase):
    """Variant A — single ``code`` field carrying ``<plugin_id>:<reason_code>``.

    Pattern-validated (typo/format resistance); ``plugin_id`` / ``reason_code`` are derived
    by splitting. Its decision_event serialization is faithful to the single-field
    philosophy: ``reason_code`` carries the FULL namespaced code (consumers that want the
    bare reason axis must split the string — this is the measured ergonomic cost).
    """

    code: str

    @field_validator("code")
    @classmethod
    def _valid_code(cls, value: str) -> str:
        if not _NAMESPACED_CODE_RE.match(value):
            raise ValueError(
                f"plugin code must match '<plugin_id>:<reason_code>' "
                f"({PLUGIN_ID_PATTERN}:{REASON_CODE_PATTERN}), got {value!r}"
            )
        return value

    @classmethod
    def from_parts(
        cls, *, plugin_id: str, reason_code: str, **fields: object
    ) -> NamespacedPluginRuleResult:
        return cls(code=f"{plugin_id}:{reason_code}", **fields)  # type: ignore[arg-type]

    @property
    def plugin_id(self) -> str:
        return self.code.split(":", 1)[0]

    @property
    def reason_code(self) -> str:
        return self.code.split(":", 1)[1]

    @property
    def full_code(self) -> str:
        return self.code

    def to_decision_event_fields(self) -> dict[str, object]:
        """decision_event fields (doc05:48-64 subset owned by this layer).

        Single-field philosophy: ``reason_code`` = the namespaced code as stored.
        ``plugin_id`` must be recovered by string parsing (the comparison point).
        """
        return {
            "box": VALIDATE_PLAN_BOX,
            "stage": VALIDATE_PLAN_STAGE,
            "decision": self.decision,
            "reason_code": self.code,
            "reason_detail": self.message_for_operator,
            "plugin_id": self.code.split(":", 1)[0],
        }


class StructuredPluginRuleResult(_PluginFindingBase):
    """Variant B — separate ``plugin_id`` + ``reason_code`` fields (each pattern-validated).

    Matches doc10:396 (box/stage/plugin_id distinguish the same reason_code across plugins)
    and the decision_event shape (doc05:58 bare ``reason_code``). The namespaced full code
    is a derived property (Langfuse tag / display), so variant B is an ergonomic superset:
    both the bare axis and the namespaced axis exist without string parsing.
    """

    plugin_id: str
    reason_code: str

    @field_validator("plugin_id")
    @classmethod
    def _valid_plugin_id(cls, value: str) -> str:
        if not _PLUGIN_ID_RE.match(value):
            raise ValueError(f"plugin_id must match {PLUGIN_ID_PATTERN}, got {value!r}")
        return value

    @field_validator("reason_code")
    @classmethod
    def _valid_reason_code(cls, value: str) -> str:
        if not _REASON_CODE_RE.match(value):
            raise ValueError(f"reason_code must match {REASON_CODE_PATTERN}, got {value!r}")
        return value

    @classmethod
    def from_parts(
        cls, *, plugin_id: str, reason_code: str, **fields: object
    ) -> StructuredPluginRuleResult:
        return cls(plugin_id=plugin_id, reason_code=reason_code, **fields)  # type: ignore[arg-type]

    def to_decision_event_fields(self) -> dict[str, object]:
        """decision_event fields — direct field mapping, no string parsing (doc05:48-64)."""
        return {
            "box": VALIDATE_PLAN_BOX,
            "stage": VALIDATE_PLAN_STAGE,
            "decision": self.decision,
            "reason_code": self.reason_code,
            "reason_detail": self.message_for_operator,
            "plugin_id": self.plugin_id,
        }


PluginFinding = NamespacedPluginRuleResult | StructuredPluginRuleResult


class PluginDispatchPolicy(_BridgeModel):
    """Policy ceiling for plugin-REQUESTED dispatch effects (Grill Q1-(2)).

    A plugin requests an effect; the policy clamps it — a plugin can never self-escalate
    above what the policy allows. ``emergency_stop`` is NEVER grantable via ``max_effect``:
    it requires per-plugin allowlisting (``emergency_stop_allowlist``), mirroring the
    principle that emergency/lower-layer safety is not weakened or delegated casually
    (doc10:393; plugins must not become a safety path, doc09:290-298).

    Allowlist authority (ADR-0003 item 6 / doc09:388-390): the AUTHORITATIVE emergency_stop
    allowlist is the project BASE ``PlanPolicy`` (the Core ceiling, policy.py). A site profile
    or run manifest may only NARROW it (remove ids), never ADD an emergency-capable plugin the
    base did not grant. Construct a per-run policy from the base ceiling with
    :meth:`derive_from_base`, which enforces that narrow-only invariant. Direct construction
    (passing ``emergency_stop_allowlist`` in) remains supported for tests/callers that already
    hold the authoritative set — it is an unchecked path, so production wiring goes through
    ``derive_from_base``.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    max_effect: DispatchEffect = DispatchEffect.BLOCK
    emergency_stop_allowlist: frozenset[str] = Field(default_factory=frozenset)

    @field_validator("max_effect")
    @classmethod
    def _no_blanket_emergency(cls, value: DispatchEffect) -> DispatchEffect:
        if value is DispatchEffect.EMERGENCY_STOP:
            raise ValueError(
                "max_effect must not be emergency_stop; grant it per-plugin via "
                "emergency_stop_allowlist"
            )
        return value

    @classmethod
    def derive_from_base(
        cls,
        base_allowlist: frozenset[str],
        *,
        requested_allowlist: frozenset[str] | None = None,
        max_effect: DispatchEffect = DispatchEffect.BLOCK,
    ) -> PluginDispatchPolicy:
        """Derive a run policy from the project BASE ceiling, NARROW-ONLY (ADR-0003 item 6).

        ``base_allowlist`` is the authoritative project ``PlanPolicy.emergency_stop_allowlist``
        (the Core ceiling, doc09:388-390). ``requested_allowlist`` is what a site profile / run
        manifest asks for; ``None`` means "inherit the base unchanged".

        Fail-closed narrow-only invariant: the request may only be a SUBSET of the base
        (``requested ⊆ base``). A request that adds an id the base did not grant is REJECTED
        (``ValueError``) — a site/run can revoke emergency authority but can never manufacture
        it. The resulting policy's allowlist is exactly the (validated) request, or the base
        when no request is given.
        """
        if requested_allowlist is None:
            resolved = frozenset(base_allowlist)
        else:
            requested = frozenset(requested_allowlist)
            added = requested - frozenset(base_allowlist)
            if added:
                raise ValueError(
                    "emergency_stop_allowlist may only NARROW the base ceiling "
                    "(ADR-0003 item 6 / doc09:388-390); a site profile / run manifest cannot "
                    f"ADD emergency-capable plugins the base did not grant: {sorted(added)} "
                    f"(base={sorted(base_allowlist)})"
                )
            resolved = requested
        return cls(max_effect=max_effect, emergency_stop_allowlist=resolved)

    def ceiling_for(self, plugin_id: str) -> DispatchEffect:
        if plugin_id in self.emergency_stop_allowlist:
            return DispatchEffect.EMERGENCY_STOP
        return self.max_effect


def clamp_finding[F: _PluginFindingBase](finding: F, policy: PluginDispatchPolicy) -> F:
    """Clamp the requested effect to the policy ceiling; record the request in
    ``clamped_from`` when lowered (Grill Q1-(2)). Requests at/below the ceiling pass
    through unchanged. The clamp only LOWERS — it never raises a plugin's effect."""
    ceiling = policy.ceiling_for(finding.plugin_id)
    if EFFECT_ORDER[finding.dispatch_effect] <= EFFECT_ORDER[ceiling]:
        return finding
    return finding.model_copy(
        update={"dispatch_effect": ceiling, "clamped_from": finding.dispatch_effect}
    )


class PluginCodeRegistry(_BridgeModel):
    """Declared-emits registry built from plugin manifests (doc09:184-219).

    ``declared_emits`` maps ``plugin_id -> frozenset(reason_codes)`` (manifest
    ``emits.reason_codes``, doc09:201-204). A finding whose code is not declared here is
    converted to a fail-closed ``needs_clarification`` result (Grill Q1-(3)). Reserved
    composition codes are NOT declarable (a plugin cannot impersonate the composition layer).
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    declared_emits: dict[str, frozenset[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_vocabulary(self) -> PluginCodeRegistry:
        for plugin_id, codes in self.declared_emits.items():
            if not _PLUGIN_ID_RE.match(plugin_id):
                raise ValueError(f"invalid plugin_id {plugin_id!r} (must match manifest form)")
            for code in codes:
                if not _REASON_CODE_RE.match(code):
                    raise ValueError(f"invalid reason_code {code!r} declared by {plugin_id!r}")
                if code in RESERVED_REASON_CODES:
                    raise ValueError(f"{plugin_id!r} declares reserved composition code {code!r}")
        return self

    def is_registered(self, plugin_id: str) -> bool:
        return plugin_id in self.declared_emits

    def is_declared(self, plugin_id: str, reason_code: str) -> bool:
        return reason_code in self.declared_emits.get(plugin_id, frozenset())

    @classmethod
    def from_manifest_dicts(cls, manifests: list[dict]) -> PluginCodeRegistry:
        """Build from parsed plugin manifest dicts (doc09:191-219 YAML shape) — the S2
        integration seam: S2 parses/validates manifests, this consumes only
        ``plugin_id`` + ``emits.reason_codes``."""
        emits: dict[str, frozenset[str]] = {}
        for manifest in manifests:
            plugin_id = manifest["plugin_id"]
            if plugin_id in emits:
                raise ValueError(f"duplicate plugin_id in manifests: {plugin_id!r}")
            declared = manifest.get("emits", {}).get("reason_codes", [])
            emits[plugin_id] = frozenset(declared)
        return cls(declared_emits=emits)
