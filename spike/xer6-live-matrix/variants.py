"""Variant catalog for the XER6 live matrix: manifest paths, factories, cfg, expectations.

Each variant = one committed ``run_manifest.v1`` (manifests/) + its committed plugin manifests
(plugins/*.plugin.yaml) + a runtime-materialized APPROVED site bundle + a ``plugin_factories``
map for ``build_x_er_runtime`` (x_er_composition.py:119-126). The cfg dict reuses the landed
fixture kit ``build_x_er_cfg`` (tests/unit/x_er_fixtures.py:272-301) so the frozen doc08 §3
``mode_x_er:`` key shape is inherited, not re-invented; live mode adds the
``robotics.er_gateway`` sub-tree (exact keys per robotics/transport.py:49-58).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from plugins import (
    CONFIDENCE_POLICY_ID,
    ESCALATION_PROBE_ID,
    ZONE_EVERYWHERE,
    ZONE_NOWHERE,
    ZONE_POLICY_ID,
    ConfidencePolicyPlugin,
    EscalationProbePlugin,
    ZonePolicyPlugin,
)

from tests.unit.x_er_fixtures import HOMOGRAPHY, build_x_er_cfg, write_site_profile_bundle

SPIKE_DIR = Path(__file__).resolve().parent
MANIFEST_DIR = SPIKE_DIR / "manifests"
PLUGIN_MANIFEST_DIR = SPIKE_DIR / "plugins"

CUSTOMER_A = "customer_a"
CUSTOMER_B = "customer_b"
SITE = "site_01"


@dataclass(frozen=True)
class VariantSpec:
    """One matrix cell: composition inputs + the tiered expectations the harness asserts."""

    key: str
    title: str
    manifest: str  # file under manifests/
    plugin_manifests: tuple[str, ...]  # files under plugins/
    customer: str
    site: str = SITE
    # zero-arg factories per run-declared plugin id (x_er_composition.py:122,138-141)
    plugin_factories: Mapping[str, Callable[[], object]] = field(default_factory=dict)
    # --- expectation tier (live-invariant; offline adds strict red->blue where marked) ---
    expect_cycle1_reject: bool = False  # plugin_rejected + 0 dispatch + store untouched
    expect_error_full_codes: tuple[str, ...] = ()  # namespaced codes in plugin_errors
    expect_warning_plugin_ids: tuple[str, ...] = ()  # attributions in plugin_warnings
    expect_clamped_from: str | None = None  # e.g. "emergency_stop" (variant D)
    strict_red_blue_offline: bool = False  # offline: bot1->shelf_1 then bot2->shelf_2


VARIANTS: dict[str, VariantSpec] = {
    spec.key: spec
    for spec in (
        VariantSpec(
            key="A",
            title="zero-plugin baseline",
            manifest="variant_a.yaml",
            plugin_manifests=(),
            customer=CUSTOMER_A,
            strict_red_blue_offline=True,
        ),
        VariantSpec(
            key="B_in",
            title="zone_policy, permissive zone (in-zone pass)",
            manifest="variant_b.yaml",
            plugin_manifests=("l3_zone_policy.plugin.yaml",),
            customer=CUSTOMER_A,
            plugin_factories={
                ZONE_POLICY_ID: lambda: ZonePolicyPlugin(
                    zone_polygon=ZONE_EVERYWHERE, homography=HOMOGRAPHY
                )
            },
            strict_red_blue_offline=True,
        ),
        VariantSpec(
            key="B_out",
            title="zone_policy, disjoint zone (deterministic reject)",
            manifest="variant_b.yaml",
            plugin_manifests=("l3_zone_policy.plugin.yaml",),
            customer=CUSTOMER_A,
            plugin_factories={
                ZONE_POLICY_ID: lambda: ZonePolicyPlugin(
                    zone_polygon=ZONE_NOWHERE, homography=HOMOGRAPHY
                )
            },
            expect_cycle1_reject=True,
            expect_error_full_codes=(f"{ZONE_POLICY_ID}:target_out_of_zone",),
        ),
        VariantSpec(
            key="C",
            title="two plugins + customer_b site swap",
            manifest="variant_c.yaml",
            plugin_manifests=(
                "l3_zone_policy.plugin.yaml",
                "l3_confidence_policy.plugin.yaml",
            ),
            customer=CUSTOMER_B,
            plugin_factories={
                ZONE_POLICY_ID: lambda: ZonePolicyPlugin(
                    zone_polygon=ZONE_EVERYWHERE, homography=HOMOGRAPHY
                ),
                CONFIDENCE_POLICY_ID: lambda: ConfidencePolicyPlugin(threshold=1.01),
            },
            expect_warning_plugin_ids=(CONFIDENCE_POLICY_ID,),
            strict_red_blue_offline=True,
        ),
        VariantSpec(
            key="D",
            title="emergency-escalation clamp probe",
            manifest="variant_d.yaml",
            plugin_manifests=("l3_escalation_probe.plugin.yaml",),
            customer=CUSTOMER_A,
            plugin_factories={ESCALATION_PROBE_ID: EscalationProbePlugin},
            expect_cycle1_reject=True,
            expect_error_full_codes=(f"{ESCALATION_PROBE_ID}:keepout_breach",),
            expect_clamped_from="emergency_stop",
        ),
    )
}

DEFAULT_ORDER: tuple[str, ...] = ("A", "B_in", "B_out", "C", "D")


def materialize_site_bundles(base_dir: Path) -> Path:
    """Write APPROVED bundles for both customers once per batch (x_er_fixtures.py:229-266)."""
    write_site_profile_bundle(base_dir, customer=CUSTOMER_A, site=SITE)
    write_site_profile_bundle(base_dir, customer=CUSTOMER_B, site=SITE)
    return base_dir


def build_variant_cfg(
    spec: VariantSpec,
    *,
    site_base_dir: Path,
    gateway_base_url: str | None = None,
) -> dict[str, Any]:
    """The merged cfg mapping for this variant; live mode adds ``robotics.er_gateway``.

    ``build_x_er_runtime`` reads only ``mode_x_er`` + ``locations`` (x_er_composition.py:150,240)
    and ``build_er_adapter`` reads only ``robotics.er_gateway`` (adapter_factory.py:64-74), so
    the union is safe to hand to both.
    """
    cfg = build_x_er_cfg(
        run_manifest_path=MANIFEST_DIR / spec.manifest,
        plugin_manifest_paths=tuple(
            PLUGIN_MANIFEST_DIR / name for name in spec.plugin_manifests
        ),
        site_base_dir=site_base_dir,
        customer=spec.customer,
        site=spec.site,
    )
    if gateway_base_url is not None:
        cfg["robotics"] = {
            "er_gateway": {
                # Exact frozen keys (transport.py:51-56): non-empty base_url + the capability
                # flag exactly True => Transport.HERMES; anything else fail-safes to DIRECT.
                "base_url": gateway_base_url,
                "audio_input_audio_supported": True,
            }
        }
    return cfg
