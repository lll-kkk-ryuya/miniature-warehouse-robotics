"""L4 ER audio transport selection (config-driven, offline, pure).

L4 owns transport selection (``robotics/__init__.py:3-4``). This module resolves *which wire*
the ER **audio** leg should use from the ``robotics.er_gateway`` config sub-tree
(``config/warehouse.base.yaml``): ``Transport.HERMES`` (via a forked ``input_audio`` gateway) iff
a forked gateway is configured AND declares audio capability, else ``Transport.DIRECT`` — the
**permanent fail-safe fallback** (PR #355 / doc06 §5 補遺:269
``deploy/hermes/er-audio-fork/TRANSPORT-FLIP-PLAN.md`` §2.1).

Safe-by-default: with either key absent/empty/false the resolver returns ``DIRECT``, so unchanged
config keeps today's direct-audio behavior. Vanilla Hermes cannot carry ``input_audio`` (returns
HTTP 400 ``unsupported_content_type``; doc06 §5:159) — only a gateway that applied
``0001-input_audio-passthrough.patch`` may set ``audio_input_audio_supported: true``; selecting
``HERMES`` against an unforked gateway is operator error, so absent/false MUST stay ``DIRECT``
(TRANSPORT-FLIP-PLAN §3.1 rule 3).

``Transport`` is an **observation-only audit tag** (``adapters/enums.py``; doc03:75): this resolver
*selects the intended wire* and stamps the audit tag — it NEVER dispatches, opens a socket, or acts
as an execution-branch key. The live audio transport seam itself is deferred to #344
(``adapters/gemini_er.py`` ``propose_plan`` raises ``NotImplementedError`` on the live path);
this module only decides the intent so the live seam and the Langfuse tag can consult one source.
"""

from collections.abc import Mapping

from warehouse_llm_bridge.robotics.adapters.enums import Transport


def resolve_audio_transport(er_gateway_cfg: Mapping[str, object] | None) -> Transport:
    """Resolve the ER audio leg transport from the ``robotics.er_gateway`` config sub-tree.

    Returns ``Transport.HERMES`` iff ``base_url`` is a non-empty string AND
    ``audio_input_audio_supported`` is ``True``; otherwise ``Transport.DIRECT`` (the permanent
    fail-safe fallback). ``None`` / missing keys / non-mapping / wrong-typed values all resolve to
    ``DIRECT`` so a malformed or absent config never silently selects an unforked-Hermes audio wire
    that would 400 (fail-safe to the shipped direct-audio behavior; doc06 §5 補遺:269).

    Pure and offline: reads only the passed mapping, performs no I/O, and never dispatches — the
    returned ``Transport`` is an observation/selection tag, not an execution-branch key (doc03:75).

    Args:
        er_gateway_cfg: the ``cfg["robotics"]["er_gateway"]`` mapping (or ``None`` when the sub-tree
            is absent), e.g. ``{"base_url": "http://127.0.0.1:8644",
            "audio_input_audio_supported": True}``.

    Returns:
        ``Transport.HERMES`` when a forked audio gateway is configured, else ``Transport.DIRECT``.
    """
    if not isinstance(er_gateway_cfg, Mapping):
        return Transport.DIRECT
    base_url = er_gateway_cfg.get("base_url")
    supported = er_gateway_cfg.get("audio_input_audio_supported")
    # Require an explicit non-empty base_url string AND the capability flag to be exactly True.
    # `is True` rejects truthy non-bools (e.g. the string "true", 1) so only a deliberate boolean
    # flag flips the wire — the fork capability is a hard, unambiguous operator declaration.
    if isinstance(base_url, str) and base_url.strip() and supported is True:
        return Transport.HERMES
    return Transport.DIRECT
