"""``run_manifest.v1`` — the bridge-local run manifest schema (doc09:42-135).

Promotes the ``run_manifest.proposal`` example (doc09:57-122) to a validated ``v1`` pydantic
schema. This model is deliberately BRIDGE-LOCAL and NOT placed in ``warehouse_interfaces``:
doc09:8 explicitly adds no frozen contract, and the manifest is a run artifact schema, not a
cross-track wire contract.

Migration note (``run_manifest.proposal`` -> ``run_manifest.v1``):
    The v1 shape is a superset of the proposal example (doc09:57-122): same top-level keys
    (``run_id`` / ``boxes`` / ``expected_emitters`` / ``score_specs``), same box shape
    (``enabled`` / ``profile`` / ``plugins[{id,version,profile}]``). v1 ADDS the optional
    ``profile_version`` / ``profile_content_hash`` fields on both boxes and plugins (the S3
    lane contract, see below) and TIGHTENS validation (fail-closed rules below). A proposal
    file migrates by rewriting ``schema_version: run_manifest.proposal`` to
    ``schema_version: run_manifest.v1``; any other ``schema_version`` value — including the
    old proposal marker — is REJECTED fail-closed so that a stale or unknown manifest can
    never be silently interpreted as the current schema.

Fail-closed validation decisions (all reject at parse time, before any run starts):
    - unknown ``schema_version`` (including ``run_manifest.proposal``) rejects;
    - unknown keys reject (``extra="forbid"``). This intentionally diverges from the frozen
      contract convention (``_BridgeModel``/``schemas._Model`` use ``extra="ignore"``): the
      manifest is an AUTHORED record, and silently ignoring a typo (``enabld:``,
      ``expected_emitter:``) would be exactly the fail-open composition drift this slice
      exists to close;
    - ``boxes`` must be non-empty (a run with zero boxes is not a run — the "empty manifest"
      fail mode is closed at the schema, not deferred to preflight);
    - ``expected_emitters`` must be non-empty, unique, and every entry must name a DECLARED
      and ENABLED box (doc09:124-127: ``enabled: false`` / exclusion means "not used", which
      contradicts "expected to emit");
    - a plugin ``id`` may appear at most once per box AND at most once across boxes (a plugin
      manifest binds a plugin to exactly one box — doc09:192-196 ``box: l3_validator``);
    - ``run_id`` is restricted to a filesystem-safe token because it becomes the
      ``out/runs/<run_id>/`` directory name (doc09:48).

S3 lane contract (profile identity fields):
    ``profile_version`` and ``profile_content_hash`` are OPTIONAL identity fields for the
    site-profile parameter set named by ``profile`` (doc09:129-174). THIS lane only defines
    the slots; COMPUTING the content hash (algorithm, canonicalization, which file bytes are
    hashed) is owned by the S3 lane. Until S3 lands, producers leave them ``None``; consumers
    must treat ``None`` as "profile content identity not attested", not as "unversioned".
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RUN_MANIFEST_SCHEMA_VERSION = "run_manifest.v1"

# Filesystem-safe run_id: it names the out/runs/<run_id>/ artifact directory (doc09:48).
_RUN_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class _ManifestModel(BaseModel):
    """Strict base for manifest models: unknown keys are authoring errors (fail-closed)."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PluginSpec(_ManifestModel):
    """One enabled plugin of a box: ``{id, version, profile}`` (doc09:61-84, 144-155)."""

    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    # Name of the site-specific parameter set passed to the plugin (doc09:131-134).
    profile: str | None = None
    # S3 lane contract: optional identity of the profile CONTENT (see module docstring).
    profile_version: str | None = None
    profile_content_hash: str | None = None


class BoxSpec(_ManifestModel):
    """One box entry: ``enabled`` + box-level ``profile`` + optional ``plugins`` (doc09:61-104)."""

    enabled: bool
    # Box-wide site profile name (used by plugin-less boxes like traffic/navigation,
    # doc09:131-133).
    profile: str | None = None
    # S3 lane contract: optional identity of the box profile content (see module docstring).
    profile_version: str | None = None
    profile_content_hash: str | None = None
    plugins: tuple[PluginSpec, ...] = ()

    @model_validator(mode="after")
    def _no_duplicate_plugin_ids(self) -> BoxSpec:
        seen: set[str] = set()
        for plugin in self.plugins:
            if plugin.id in seen:
                raise ValueError(f"duplicate plugin id {plugin.id!r} within one box")
            seen.add(plugin.id)
        return self


class RunManifest(_ManifestModel):
    """The ``run_manifest.v1`` record of what one run enabled (doc09:42-127)."""

    schema_version: str
    run_id: str = Field(min_length=1, pattern=_RUN_ID_PATTERN)
    boxes: dict[str, BoxSpec] = Field(min_length=1)
    expected_emitters: tuple[str, ...] = Field(min_length=1)
    score_specs: tuple[str, ...] = ()

    @field_validator("schema_version")
    @classmethod
    def _pin_schema_version(cls, value: str) -> str:
        """Fail closed on any schema_version other than the pinned v1 (module docstring)."""
        if value != RUN_MANIFEST_SCHEMA_VERSION:
            raise ValueError(
                f"unknown schema_version {value!r}: this loader only accepts "
                f"{RUN_MANIFEST_SCHEMA_VERSION!r} (run_manifest.proposal files must be "
                "migrated; see manifest.py migration note)"
            )
        return value

    @field_validator("run_id")
    @classmethod
    def _run_id_is_safe_path_component(cls, value: str) -> str:
        # The regex already excludes "/", but be explicit about the traversal tokens.
        if value in {".", ".."}:
            raise ValueError(f"run_id {value!r} is not a valid artifact directory name")
        return value

    @model_validator(mode="after")
    def _cross_field_consistency(self) -> RunManifest:
        # A plugin id is bound to exactly one box (doc09:192-196); a cross-box duplicate would
        # make the preflight/effective-composition mapping ambiguous.
        owner_by_plugin: dict[str, str] = {}
        for box_id, box in self.boxes.items():
            for plugin in box.plugins:
                other = owner_by_plugin.get(plugin.id)
                if other is not None:
                    raise ValueError(
                        f"plugin id {plugin.id!r} declared under two boxes "
                        f"({other!r} and {box_id!r}); a plugin belongs to exactly one box"
                    )
                owner_by_plugin[plugin.id] = box_id

        # expected_emitters entries must be declared, enabled boxes (doc09:124-127).
        seen_emitters: set[str] = set()
        for emitter in self.expected_emitters:
            if emitter in seen_emitters:
                raise ValueError(f"duplicate expected_emitters entry {emitter!r}")
            seen_emitters.add(emitter)
            box = self.boxes.get(emitter)
            if box is None:
                raise ValueError(
                    f"expected_emitters names undeclared box {emitter!r} "
                    "(doc09:124: exclusion from boxes means 'not used in this run')"
                )
            if not box.enabled:
                raise ValueError(
                    f"expected_emitters names disabled box {emitter!r} "
                    "(doc09:124: enabled: false contradicts 'expected to emit')"
                )
        return self

    # ── read helpers (pure derivations; no policy) ──────────────────────────────

    def enabled_boxes(self) -> tuple[str, ...]:
        """Box ids with ``enabled: true``, in declaration order."""
        return tuple(box_id for box_id, box in self.boxes.items() if box.enabled)

    def enabled_plugin_owners(self) -> dict[str, str]:
        """``plugin_id -> box_id`` for plugins of ENABLED boxes only.

        Plugins listed under a disabled box are NOT part of this run's composition
        (doc09:124) and are therefore excluded — the preflight neither requires nor
        permits them to be registered.
        """
        return {
            plugin.id: box_id
            for box_id, box in self.boxes.items()
            if box.enabled
            for plugin in box.plugins
        }

    def enabled_boxes_not_expected(self) -> tuple[str, ...]:
        """Enabled boxes missing from ``expected_emitters`` (doc09:125 surfacing helper).

        Whether such a box is a manifest inconsistency depends on whether the box is an
        EMITTING box — knowledge the manifest alone does not carry — so this is surfaced
        for the caller/Eval join rather than rejected at the schema.
        """
        expected = set(self.expected_emitters)
        return tuple(box_id for box_id in self.enabled_boxes() if box_id not in expected)
