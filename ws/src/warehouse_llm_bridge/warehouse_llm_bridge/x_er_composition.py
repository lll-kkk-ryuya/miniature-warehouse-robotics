"""X-ER composition startup: config -> fail-closed :class:`XErRuntime` (XER6 Lane A, doc08 §4).

Implements the composition startup sequence of docs/mode-x-er/08-x-er-bridge-node-spec.md §4
(steps 1-7) as ONE pure, ROS-free builder that the ``x_er_bridge`` node calls exactly once at
startup. :func:`build_x_er_runtime` either returns a fully constructed :class:`XErRuntime` or
raises — there is no partially initialized runtime, so any exception is a startup refusal
(0 dispatch, doc08 §6 "起動時: §4 のどの step の raise でも node は cycle を開始しない").

Error policy (one consistent rule, noted per doc08 §4 "fail-closed"):

- failures detected by the underlying composition layer PROPAGATE UNCHANGED with their typed
  diagnostics (``pydantic.ValidationError`` for a malformed / unknown-``schema_version`` run
  manifest, ``ManifestReconciliationError``, ``CompositionError``, ``PluginCompositionError``,
  ``SiteProfileError``, ``GovernedCalibrationUnavailableError``, ``OSError`` /
  ``yaml.YAMLError`` for unreadable files);
- the gates THIS module itself owns (config-shape validation of the frozen ``mode_x_er:`` keys,
  factory coverage, the delegated triple cross-check) raise :class:`XErCompositionError`, a
  subclass of the composition layer's ``CompositionError`` so both families read uniformly as
  "composition refused" to the caller.

Step 8 of doc08 §4 (ER adapter construction) is deliberately NOT here: the adapter is injected
into the cycle (doc08 §8 offline tests bypass the factory), so this builder stays free of any
transport/env concern.

bridge-local: nothing here touches ``warehouse_interfaces``; no ROS import, no network, no
actuation. The only filesystem I/O is reading the config-named manifest/profile artifacts and
(optionally) writing the ``out/runs/<run_id>/`` witness pair (record.py:242).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from warehouse_llm_bridge.robotics.composition import (
    CompositionError,
    ConstructedBox,
    EffectiveComposition,
    PluginComposition,
    PluginDispatchPolicy,
    PluginManifest,
    PreflightReport,
    RunManifest,
    build_effective_composition,
    build_plugin_code_registry,
    load_plugin_manifests,
    load_run_manifest,
    preflight_composition,
    write_run_artifacts,
)
from warehouse_llm_bridge.robotics.composition.calibration_source import (
    resolve_governed_calibration_with_loader,
)
from warehouse_llm_bridge.robotics.composition.profile import (
    composition_record,
    compute_content_hash,
    load_approved_record,
    load_site_profile,
    verify_against_approved,
)
from warehouse_llm_bridge.robotics_planning_core.command_compiler import ExecutionProfile
from warehouse_llm_bridge.robotics_planning_core.validator import PlanPolicy
from warehouse_llm_bridge.robotics_planning_core.validator.seams import Calibration
from warehouse_llm_bridge.robotics_planning_core.visual_resolver import VisualPolicy


class XErCompositionError(CompositionError):
    """A gate owned by the X-ER startup wiring refused the composition (fail-closed).

    Subclass of the composition layer's :class:`CompositionError` (preflight.py:33) so callers
    treat both the wiring's own refusals and the underlying layer's refusals as the same
    "composition refused => 0 dispatch" family.
    """


@dataclass(frozen=True)
class XErRuntime:
    """Everything the x_er_bridge cycle needs, built once at startup (doc08 §4 output).

    ``out_dir`` is the ``out/runs/<run_id>/`` witness directory (record.py:242), or ``None``
    when ``write_artifacts=False``.
    """

    run_manifest: RunManifest
    composition: PluginComposition
    preflight_report: PreflightReport
    calibration: Calibration
    visual_policy: VisualPolicy
    effective_composition: EffectiveComposition
    out_dir: Path | None


def cross_check_composition(
    run_manifest: RunManifest,
    composition: PluginComposition,
    plugin_manifests: Sequence[PluginManifest],
) -> None:
    """Triple cross-check: run-declared == registered hookimpls == plugin-manifest-present.

    plugin_manifest.py:22-27 explicitly delegates this cross-check to XER6 (this module).
    Steps 2 (reconcile) and 5 (preflight) each enforce one PAIR of the equality; this explicit
    triple check is the single guard that stays red if either underlying gate is weakened
    (e.g. an ``allow_unlisted`` leak). Raises :class:`XErCompositionError` on any inequality.
    """
    run_declared = frozenset(run_manifest.enabled_plugin_owners())
    registered = frozenset(composition.registered_plugin_ids())
    manifest_present = frozenset(manifest.plugin_id for manifest in plugin_manifests)
    if not (run_declared == registered == manifest_present):
        raise XErCompositionError(
            "composition triple cross-check failed (run-declared == registered == "
            f"plugin-manifest-present, plugin_manifest.py:22-27): "
            f"run-declared={sorted(run_declared)} registered={sorted(registered)} "
            f"plugin-manifest-present={sorted(manifest_present)}"
        )


def build_x_er_runtime(
    cfg: Mapping[str, Any],
    *,
    plugin_factories: Mapping[str, Callable[[], object]] | None = None,
    base_policy: PlanPolicy | None = None,
    write_artifacts: bool = True,
    out_root: Path | None = None,
) -> XErRuntime:
    """Run the doc08 §4 startup sequence (steps 1-7), fail-closed at every gate.

    Args:
        cfg: the merged warehouse config mapping. Consumed keys are the frozen doc08 §3 set
            (``mode_x_er.{execution_profile,run_manifest,plugin_manifests,calibration_id,
            visual.snap_radius_m,site_profile.{base_dir,customer,site}}``) plus the existing
            top-level ``locations`` block (name -> {x, y}) for the Visual Resolver coordinates.
            ``execution_profile`` must be ``x_lite``: ``x_rmf`` refuses startup with
            ``NotImplementedError`` (doc08 §2/#346) and any other value refuses with
            :class:`XErCompositionError` — the config can never silently fall back to the
            ``compile_raw_output`` default (pipeline.py:97).
        plugin_factories: ``plugin_id -> zero-arg constructor`` for every run-declared plugin.
            A run-declared plugin with no factory refuses startup (a silently absent plugin is
            the fail-open hole the preflight exists for). Surplus factories are ignored — the
            run manifest, not the factory map, is the witness of intent.
        base_policy: the project base :class:`PlanPolicy` whose ``emergency_stop_allowlist``
            is the Core ceiling for the narrow-only dispatch-policy derivation
            (plugin_results.py:272). Defaults to ``PlanPolicy()`` (empty allowlist).
        write_artifacts: write the ``out/runs/<run_id>/`` witness pair (step 7). ``False``
            skips writing and leaves ``XErRuntime.out_dir`` as ``None``.
        out_root: artifact root override; ``None`` uses the repo-relative default
            ``out/runs`` (record.py:48 ``DEFAULT_RUNS_ROOT``, relative to cwd).
    """
    section = _mode_x_er_section(cfg)

    # doc08 §3 execution_profile gate (before any composition step): only the x_lite backend
    # is implemented — x_rmf must refuse startup (NotImplementedError fail-closed, doc08
    # §2/#346) instead of being silently ignored while compile_raw_output's x_lite default
    # applies (pipeline.py:97). The value is validated, not threaded: XErRuntime is the
    # frozen inter-module surface, and x_lite is exactly the pipeline default the cycle runs.
    _execution_profile(section)

    # Step 1 — run manifest (loader.py:37; empty path refuses before any file I/O).
    run_manifest = load_run_manifest(Path(_required_str(section, "run_manifest")))

    # Step 2 — plugin manifests + declared-emits registry (plugin_manifest.py:205,301).
    plugin_manifests = load_plugin_manifests(
        [path.read_text(encoding="utf-8") for path in _plugin_manifest_paths(section)]
    )
    registry, _reconciliation = build_plugin_code_registry(run_manifest, plugin_manifests)

    # Step 3 — narrow-only dispatch policy from the Core ceiling (plugin_results.py:272).
    resolved_base = base_policy if base_policy is not None else PlanPolicy()
    dispatch_policy = PluginDispatchPolicy.derive_from_base(resolved_base.emergency_stop_allowlist)

    # Step 4 — construct + register every run-declared hookimpl under its manifest plugin_id
    # (plugins.py:123,150). Missing factory = the plugin cannot exist in-process => refuse.
    factories = plugin_factories if plugin_factories is not None else {}
    owners = run_manifest.enabled_plugin_owners()
    missing_factories = sorted(set(owners) - set(factories))
    if missing_factories:
        raise XErCompositionError(
            f"run manifest {run_manifest.run_id!r} declares plugin(s) with no factory: "
            f"{missing_factories}; a declared plugin that cannot be constructed must refuse "
            "startup (fail-closed)"
        )
    composition = PluginComposition(registry=registry, dispatch_policy=dispatch_policy)
    constructed_plugins: dict[str, object] = {}
    for plugin_id in owners:
        impl = factories[plugin_id]()
        composition.register(impl, plugin_id)
        constructed_plugins[plugin_id] = impl

    # Step 5 — preflight (preflight.py:57) + the XER6-delegated triple cross-check.
    preflight_report = preflight_composition(run_manifest, composition)
    cross_check_composition(run_manifest, composition, plugin_manifests)

    # Step 6 — site profile gate (profile.py:161,287,197,320) then the governed calibration
    # (calibration_source.py). The *_with_loader variant is used because the gate report must
    # be embedded into the effective-composition witness (its documented XER6 purpose).
    base_dir, customer, site = _site_profile_keys(section)
    profile = load_site_profile(base_dir, customer, site)
    content_hash = compute_content_hash(profile)
    approved = load_approved_record(base_dir, customer, site)
    verification = verify_against_approved(profile, content_hash, approved)
    verification.assert_verified()
    calibration, loader = resolve_governed_calibration_with_loader(
        profile, _required_str(section, "calibration_id")
    )

    # Step 7 — effective-composition witness + artifacts (record.py:139,242). Every enabled
    # box gets a ConstructedBox entry; boxes this process does not construct in-process carry
    # stage=None (record.py:181-183 raises on any missing coverage).
    # DELIBERATE (this slice): stage=None for ALL X-ER boxes, including the validator box
    # whose stage objects (PlanValidator / VisualTaskResolver / WarehouseNavCompiler) DO run
    # in-process — they are constructed per cycle inside x_er_cycle, not held by this
    # startup builder, so the witness conservatively under-claims (never claims more than
    # this builder constructed). Recording per-cycle stages is a follow-up (flagged residual).
    constructed = {
        box_id: ConstructedBox(
            stage=None,
            plugins={
                plugin_id: constructed_plugins[plugin_id]
                for plugin_id, owner_box in owners.items()
                if owner_box == box_id
            },
        )
        for box_id in run_manifest.enabled_boxes()
    }
    effective = build_effective_composition(
        run_manifest,
        preflight_report,
        constructed,
        site_profile=composition_record(profile, content_hash, verification, approved)[
            "site_profile"
        ],
        calibration_governance=loader.report().as_composition_block(),
    )
    out_dir: Path | None = None
    if write_artifacts:
        out_dir = write_run_artifacts(run_manifest, effective, runs_root=out_root)

    visual_policy = VisualPolicy(
        location_coords=_location_coords(cfg),
        snap_radius_m=_snap_radius(section),
    )

    return XErRuntime(
        run_manifest=run_manifest,
        composition=composition,
        preflight_report=preflight_report,
        calibration=calibration,
        visual_policy=visual_policy,
        effective_composition=effective,
        out_dir=out_dir,
    )


# --- config-shape gates (the frozen doc08 §3 key set; every violation refuses startup) --------


def _mode_x_er_section(cfg: Mapping[str, Any]) -> Mapping[str, Any]:
    section = cfg.get("mode_x_er")
    if not isinstance(section, Mapping):
        raise XErCompositionError(
            "config key 'mode_x_er' is missing or not a mapping (doc08 §3 frozen key set)"
        )
    return section


def _execution_profile(section: Mapping[str, Any]) -> ExecutionProfile:
    """Validate the frozen ``mode_x_er.execution_profile`` key (doc08 §3: ``x_lite | x_rmf``).

    ``x_rmf`` is out of XER6 scope and must be a startup refusal — ``NotImplementedError``
    fail-closed (doc08 §2/#346), mirroring the compiler's own backend guard
    (compiler.py:79-83). Any value outside the documented vocabulary (missing key included)
    is malformed config and refuses with :class:`XErCompositionError`.
    """
    value = section.get("execution_profile")
    if not isinstance(value, str) or value not in {profile.value for profile in ExecutionProfile}:
        raise XErCompositionError(
            f"config key 'mode_x_er.execution_profile' must be one of "
            f"{sorted(profile.value for profile in ExecutionProfile)}, got {value!r} (doc08 §3)"
        )
    profile = ExecutionProfile(value)
    if profile is not ExecutionProfile.X_LITE:
        raise NotImplementedError(
            f"mode_x_er.execution_profile {value!r} is out of XER6 scope — startup refused "
            "(NotImplementedError fail-closed, doc08 §2/#346; the RmfTaskCompiler plugin is "
            "deferred, doc02:234,240)"
        )
    return profile


def _required_str(section: Mapping[str, Any], key: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value:
        raise XErCompositionError(
            f"config key 'mode_x_er.{key}' must be a non-empty string, got {value!r} "
            "(empty = composition refused, doc08 §3)"
        )
    return value


def _plugin_manifest_paths(section: Mapping[str, Any]) -> list[Path]:
    # Missing key == the doc08 §3 base default `[]` (zero plugin manifests, safe side).
    raw = section.get("plugin_manifests", [])
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise XErCompositionError(
            f"config key 'mode_x_er.plugin_manifests' must be a list of paths, got {raw!r}"
        )
    paths: list[Path] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry:
            raise XErCompositionError(
                f"config key 'mode_x_er.plugin_manifests' entries must be non-empty "
                f"strings, got {entry!r}"
            )
        paths.append(Path(entry))
    return paths


def _site_profile_keys(section: Mapping[str, Any]) -> tuple[Path, str, str]:
    raw = section.get("site_profile")
    if not isinstance(raw, Mapping):
        raise XErCompositionError(
            "config key 'mode_x_er.site_profile' is missing or not a mapping (doc08 §3)"
        )
    base_dir = raw.get("base_dir")
    customer = raw.get("customer")
    site = raw.get("site")
    for key, value in (("base_dir", base_dir), ("customer", customer), ("site", site)):
        if not isinstance(value, str) or not value:
            raise XErCompositionError(
                f"config key 'mode_x_er.site_profile.{key}' must be a non-empty string, "
                f"got {value!r} (empty = composition refused, doc08 §3)"
            )
    return Path(base_dir), customer, site


def _snap_radius(section: Mapping[str, Any]) -> float:
    visual = section.get("visual")
    if not isinstance(visual, Mapping):
        raise XErCompositionError(
            "config key 'mode_x_er.visual' is missing or not a mapping (doc08 §3)"
        )
    value = visual.get("snap_radius_m")
    # No default here on purpose: thresholds must come from config, never a code constant
    # (doc02:98; doc08 §3 "コード定数禁止").
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise XErCompositionError(
            f"config key 'mode_x_er.visual.snap_radius_m' must be a number, got {value!r} "
            "(no hardcoded default — doc02:98)"
        )
    return float(value)


def _location_coords(cfg: Mapping[str, Any]) -> dict[str, tuple[float, float]]:
    """``locations: {name: {x, y}}`` (config/warehouse.base.yaml:39-48) -> name -> (x, y)."""
    raw = cfg.get("locations")
    if not isinstance(raw, Mapping) or not raw:
        raise XErCompositionError(
            "config key 'locations' is missing or empty; the Visual Resolver coordinates "
            "derive from it (doc08 §3 — no new coordinate key is invented)"
        )
    coords: dict[str, tuple[float, float]] = {}
    for name, entry in raw.items():
        if not isinstance(entry, Mapping):
            raise XErCompositionError(
                f"config key 'locations.{name}' must be a mapping with x/y, got {entry!r}"
            )
        x, y = entry.get("x"), entry.get("y")
        for axis, value in (("x", x), ("y", y)):
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise XErCompositionError(
                    f"config key 'locations.{name}.{axis}' must be a number, got {value!r}"
                )
        coords[str(name)] = (float(x), float(y))
    return coords
