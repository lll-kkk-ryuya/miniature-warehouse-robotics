"""``decision_events.jsonl`` envelope validation — doc09 実装順序 step 3.

Implements docs/productization/09-run-manifest-and-plugin-composition.md:489
("``decision_events.jsonl`` の envelope を Pydantic / JSON Schema で検証する") as a
bridge-local pydantic model + a file validator that returns typed rows and typed errors.

The envelope mirrors the REAL producer shapes found on main — it invents no field:

- ``operator_notice.v0`` payload keys (the only code producer that serializes a
  decision_event to JSON today):
  ``warehouse_llm_bridge/operator_feedback/publisher.py:67-79`` (``_V0_PAYLOAD_KEYS``)
  with ``SCHEMA_VERSION_V0 = "operator_notice.v0"`` (publisher.py:51).
- The documented decision_event basic form (docs/productization/05:49-64,
  ``schema_version: "proposal"`` at :63) adds ``trace_id`` / ``input_ref`` /
  ``output_ref`` / ``profile`` — all optional here because "``trace_id`` は …
  audit / event 側に常に存在するとは限らない" (doc09:41-42).

Fail-closed / fail-open split (each mirrors an existing convention):

- **Unknown ``schema_version`` is a typed error** (reason literal
  ``schema_version_mismatch``, the eval box reason_code catalog —
  docs/productization/06:250). This mirrors the fail-closed ``run_manifest.v1``
  rule (doc09:157-160: unknown schema_version rejects).
- **Extra keys are IGNORED**, mirroring the producer-side decode convention
  (``operator_feedback/models.py:8-9`` "``from_payload`` keeps ONLY the known keys
  (``extra=ignore`` shape)") and the bridge-local model base
  (``robotics_planning_core/models/base.py:19-20``).
- ``decision`` must be in the fixed vocabulary (doc05:69; consumed from
  ``operator_feedback.models.DECISION_VOCAB`` — models.py:37-46, invented nowhere).
- ``gen_id`` is ``int | None`` (producer: ``DecisionEvent.gen_id`` models.py:132;
  join key semantics: doc09:34,41). A non-coercible ``gen_id`` is a typed
  validation error here (this module's job IS 検証, doc09:489), unlike the
  speak-path producer which silently degrades to ``None`` (models.py:152-158).
- ``plugin_id`` is ``str | None`` (mode-x-er/05 §8.11): plugin-emitted rows
  (``StructuredPluginRuleResult.to_decision_event_fields`` — the doc09:374-381
  Variant B separate-field form) carry their attribution; rows from non-plugin
  producers omit it. A malformed ``plugin_id`` (UPPERCASE / colon-containing /
  empty — outside the manifest form, doc09:231,380-381) is a typed validation
  error (fail-closed), never silently dropped. The operator_notice.v0 WIRE key
  set is unchanged (mode-x-er/05 §8.11 — this is a row/validation-layer field).

This module is bridge-local (no ``warehouse_interfaces`` edit, no eval_sdk edit —
eval_sdk stays composition-agnostic, doc09:479-483). Pure host-runnable: stdlib +
pydantic only (doc16 §11).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ConfigDict, ValidationError, field_validator

from warehouse_llm_bridge.operator_feedback.models import DECISION_VOCAB
from warehouse_llm_bridge.operator_feedback.publisher import SCHEMA_VERSION_V0
from warehouse_llm_bridge.robotics_planning_core.models.base import _BridgeModel

from .plugin_results import PLUGIN_ID_PATTERN

#: Anchored manifest-form matcher for the optional ``plugin_id`` field (mode-x-er/05 §8.11).
#: The pattern itself is the single composition-layer source (plugin_results.py:52 —
#: lowercase dotted, doc09:231), compiled here from the PUBLIC constant.
_PLUGIN_ID_RE = re.compile(rf"^{PLUGIN_ID_PATTERN}$")

#: The documented decision_event basic-form version (docs/productization/05:63).
SCHEMA_VERSION_PROPOSAL = "proposal"

#: Accepted envelope versions — the real code producer (publisher.py:51) plus the
#: documented basic form (doc05:63). Anything else is fail-closed rejected, mirroring
#: the run_manifest.v1 rule (doc09:157-160).
KNOWN_ENVELOPE_SCHEMA_VERSIONS: frozenset[str] = frozenset(
    {SCHEMA_VERSION_V0, SCHEMA_VERSION_PROPOSAL}
)

# Typed error kinds for :class:`EnvelopeError`. ``schema_version_mismatch`` reuses the
# documented eval box reason_code literal (doc06:250); the other two are bridge-local
# (internal validator taxonomy, not decision_event reason_codes).
ERROR_JSON_DECODE = "json_decode"
ERROR_SCHEMA_VERSION_MISMATCH = "schema_version_mismatch"
ERROR_VALIDATION = "validation"


class DecisionEventEnvelope(_BridgeModel):
    """One validated ``decision_events.jsonl`` line (doc09:489).

    Field set = ``_V0_PAYLOAD_KEYS`` (publisher.py:67-79) ∪ the doc05:49-64 basic-form
    extras. Defaults mirror the producer-side decode defaults
    (``DecisionEvent``, operator_feedback/models.py:125-136).
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    schema_version: str
    decision: str
    timestamp: str = ""
    run_id: str = ""
    gen_id: int | None = None
    robot: str = ""
    box: str = ""
    stage: str = ""
    reason_code: str = ""
    reason_detail: str = ""
    message_for_operator: str = ""
    # doc05:49-64 basic-form-only fields (not in the v0 wire payload; optional per
    # doc09:41-42 "audit / event 側に常に存在するとは限らない").
    trace_id: str | None = None
    input_ref: str | None = None
    output_ref: str | None = None
    profile: str | None = None
    # Plugin attribution (mode-x-er/05 §8.11; doc09:374-381 Variant B separate-field form).
    # Present on plugin-emitted rows (to_decision_event_fields), absent everywhere else —
    # NOT part of the operator_notice.v0 wire key set (publisher.py:66-78 unchanged).
    plugin_id: str | None = None

    @field_validator("schema_version")
    @classmethod
    def _known_schema_version(cls, value: str) -> str:
        """Fail closed on unknown envelope versions (doc09:157-160 precedent)."""
        if value not in KNOWN_ENVELOPE_SCHEMA_VERSIONS:
            raise ValueError(
                f"unknown schema_version {value!r}: accepted versions are "
                f"{sorted(KNOWN_ENVELOPE_SCHEMA_VERSIONS)}"
            )
        return value

    @field_validator("decision")
    @classmethod
    def _known_decision(cls, value: str) -> str:
        """``decision`` is a fixed vocabulary (doc05:69 / operator_feedback models.py:37-46)."""
        if value not in DECISION_VOCAB:
            raise ValueError(
                f"decision {value!r} is not in the fixed vocabulary {sorted(DECISION_VOCAB)}"
            )
        return value

    @field_validator("plugin_id")
    @classmethod
    def _valid_plugin_id(cls, value: str | None) -> str | None:
        """Fail closed on a malformed ``plugin_id`` (mode-x-er/05 §8.11).

        ``None`` / absent keeps the pre-plugin behaviour. A present value must match the
        manifest form (lowercase dotted, doc09:231) — UPPERCASE or colon-containing values
        (a smuggled ``<plugin_id>:<reason_code>`` full code, doc09:380-381) are typed errors,
        so attribution is never silently corrupted.
        """
        if value is None:
            return None
        if not _PLUGIN_ID_RE.match(value):
            raise ValueError(
                f"plugin_id must match the manifest form {PLUGIN_ID_PATTERN} "
                f"(lowercase dotted, e.g. 'l3.zone_policy'), got {value!r}"
            )
        return value


@dataclass(frozen=True)
class EnvelopeError:
    """One typed rejection: which line, which kind, why."""

    line_no: int
    kind: str  # ERROR_JSON_DECODE | ERROR_SCHEMA_VERSION_MISMATCH | ERROR_VALIDATION
    message: str


@dataclass(frozen=True)
class ValidatedEvent:
    """One accepted line with its 1-indexed source line number."""

    line_no: int
    event: DecisionEventEnvelope


@dataclass(frozen=True)
class EnvelopeValidation:
    """Outcome of validating one ``decision_events.jsonl`` file.

    ``artifact_missing`` is True when the file does not exist (doc09:142-143 —
    a whole-file absence is ``artifact_missing``, not a parse error).
    """

    path: str
    artifact_missing: bool
    rows: tuple[ValidatedEvent, ...]
    errors: tuple[EnvelopeError, ...]

    @property
    def ok(self) -> bool:
        """True when the artifact exists and every non-blank line validated."""
        return not self.artifact_missing and not self.errors


def validate_decision_event_payload(
    payload: dict[str, Any], *, line_no: int = 0
) -> tuple[DecisionEventEnvelope | None, EnvelopeError | None]:
    """Validate one already-decoded payload dict; return (event, None) or (None, error).

    ``schema_version`` problems (missing / non-string / unknown) are classified as
    ``schema_version_mismatch`` (doc06:250 literal); every other pydantic failure is
    ``validation``. Never raises.
    """
    version = payload.get("schema_version")
    if not isinstance(version, str) or version not in KNOWN_ENVELOPE_SCHEMA_VERSIONS:
        return None, EnvelopeError(
            line_no=line_no,
            kind=ERROR_SCHEMA_VERSION_MISMATCH,
            message=(f"schema_version {version!r} not in {sorted(KNOWN_ENVELOPE_SCHEMA_VERSIONS)}"),
        )
    try:
        return DecisionEventEnvelope.model_validate(payload), None
    except ValidationError as exc:
        return None, EnvelopeError(line_no=line_no, kind=ERROR_VALIDATION, message=str(exc))


def validate_decision_events_file(path: Path) -> EnvelopeValidation:
    """Validate every line of ``decision_events.jsonl`` → typed rows + typed errors.

    - Missing file → ``artifact_missing=True`` (doc09:142-143), zero rows, zero errors.
    - Blank lines are skipped.
    - A malformed / rejected line becomes ONE typed error; remaining lines still parse
      (an offline report must not die on one bad row — E-G1 spirit, doc06:251).
    """
    if not path.exists():
        return EnvelopeValidation(path=str(path), artifact_missing=True, rows=(), errors=())
    rows: list[ValidatedEvent] = []
    errors: list[EnvelopeError] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append(
                    EnvelopeError(line_no=line_no, kind=ERROR_JSON_DECODE, message=str(exc))
                )
                continue
            if not isinstance(payload, dict):
                errors.append(
                    EnvelopeError(
                        line_no=line_no,
                        kind=ERROR_VALIDATION,
                        message=f"line is not a JSON object: {type(payload).__name__}",
                    )
                )
                continue
            event, error = validate_decision_event_payload(payload, line_no=line_no)
            if event is not None:
                rows.append(ValidatedEvent(line_no=line_no, event=event))
            elif error is not None:
                errors.append(error)
    return EnvelopeValidation(
        path=str(path), artifact_missing=False, rows=tuple(rows), errors=tuple(errors)
    )


def decision_event_json_schema() -> dict[str, Any]:
    """JSON Schema form of the envelope (doc09:489 "Pydantic / JSON Schema")."""
    return DecisionEventEnvelope.model_json_schema()
