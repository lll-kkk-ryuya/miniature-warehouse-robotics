"""Commander system-prompt provider — Langfuse Prompt Management with a code fallback.

The commander system prompts are MANAGED in Langfuse Prompt Management — the editable,
versioned source of truth (doc08 §Langfuse Prompt Management 方針). The operational prompt
text is fetched ONCE at node startup via
``get_client().get_prompt(name, label, fallback=..., cache_ttl_seconds=...).prompt`` so it can
be revised and version-pinned (Phase 4 four-provider fairness, doc08 §比較検証ログ) WITHOUT a
code redeploy. The fetched text is held on the ``HermesClient`` for the node's lifetime — a
new prompt version is picked up on the next NODE RESTART (no per-cycle re-fetch). The hardcoded Japanese constants in :mod:`~warehouse_llm_bridge.hermes_client`
(``SYSTEM_PROMPT`` / ``MODE_A_RULES`` / ``MODE_C_PROMPT``, composed by ``build_system_prompt``)
are NO LONGER the management surface: they are demoted to a **fail-open fallback** used only
when Langfuse is unreachable, the prompt is not seeded, or langfuse is absent (offline unit
tests / CI).

Fail-open discipline mirrors :mod:`~warehouse_llm_bridge.tracing`: langfuse is lazily imported
(a pip extra, not a pytest/ruff dependency) and ANY error degrades to the code fallback so the
robot demo never stops on a Langfuse outage (doc08:333 Langfuse fail-open). The SAME fallback
text is what :mod:`warehouse_llm_bridge.seed_prompts` upserts to Langfuse, so the code fallback
and the managed prompt share ONE source and cannot drift.
"""

import logging
from dataclasses import dataclass

from warehouse_llm_bridge.hermes_client import MODE_A_TRAFFIC_MODES, build_system_prompt

log = logging.getLogger(__name__)

# Langfuse prompt names — one prompt per ACTUALLY-SENT unit (doc08 §Langfuse Prompt
# Management 方針): Mode A/B sends base+rules composed; Mode C sends its standalone prompt.
# The character-LLM persona prompt (doc14) is a future addition, out of scope here.
PROMPT_NAME_MODE_AB = "warehouse-commander-mode-ab"
PROMPT_NAME_MODE_C = "warehouse-commander-mode-c"

# Defaults when the optional ``hermes.prompts`` config block is absent. Behaviour-preserving:
# fetch from Langfuse, fall back to the code constant (so a run with no Langfuse keys / no
# seed still gets a valid prompt). ``label="production"`` is the version pin compared runs
# share (fairness, doc08 §比較検証ログ).
DEFAULT_PROMPT_SOURCE = "langfuse"
DEFAULT_PROMPT_LABEL = "production"
DEFAULT_CACHE_TTL_SECONDS = 300


def prompt_name(mode: str) -> str:
    """Langfuse prompt name for the given ``traffic_mode`` (the actually-sent unit)."""
    return PROMPT_NAME_MODE_AB if mode in MODE_A_TRAFFIC_MODES else PROMPT_NAME_MODE_C


def commander_fallback_text(mode: str) -> str:
    """The fail-open fallback prompt text — the code-constant composition (doc08a/08c).

    Identical to the historical ``build_system_prompt(mode)`` output: this is BOTH the text
    used when Langfuse is unavailable AND the text :mod:`warehouse_llm_bridge.seed_prompts`
    upserts — one source, no drift (doc08 §Langfuse Prompt Management 方針).
    """
    return build_system_prompt(mode)


@dataclass
class ResolvedPrompt:
    """A resolved commander prompt: the system-prompt text + optional trace-link object.

    ``langfuse_prompt`` (the SDK prompt object) is passed as ``langfuse_prompt=`` to link the
    generation for prompt-level analytics (Pattern A, doc08:375); it is ``None`` when the code
    fallback was used (so a fallback is never mislabelled as a managed prompt version).
    """

    text: str
    langfuse_prompt: object | None = None
    is_fallback: bool = True


def _prompts_config(cfg: dict) -> dict:
    """Read the optional ``hermes.prompts`` config block (additive; absent/malformed -> {}).

    Always returns a dict so the caller's ``.get`` lookups can never raise on a malformed
    config (e.g. ``hermes: "x"`` or ``prompts: "langfuse"`` scalars) — part of the never-raises
    fail-open contract of :func:`resolve_commander_prompt`.
    """
    hermes = cfg.get("hermes") if isinstance(cfg, dict) else None
    prompts = hermes.get("prompts") if isinstance(hermes, dict) else None
    return prompts if isinstance(prompts, dict) else {}


def _get_client() -> object:
    """Return the langfuse client; raises if langfuse is unavailable (lazy pip extra).

    Isolated here so tests can patch ``prompts._get_client`` deterministically without a
    real network call (the langfuse SDK is not a pytest/ruff dependency).
    """
    from langfuse import get_client

    return get_client()


def resolve_commander_prompt(mode: str, cfg: dict) -> ResolvedPrompt:
    """Resolve the commander system prompt for ``mode``: Langfuse-managed, code fallback.

    ``hermes.prompts.source == "code"`` returns the code fallback verbatim (Langfuse
    untouched — for CI / fully-offline runs). Otherwise fetch ``get_prompt(name, label,
    cache_ttl).prompt`` from Langfuse, degrading to the code constant on ANY error (langfuse
    absent / not seeded / auth / network) so the commander always has a valid prompt
    (fail-open, doc08:333). Never raises.
    """
    fallback = commander_fallback_text(mode)
    pcfg = _prompts_config(cfg)
    source = pcfg.get("source", DEFAULT_PROMPT_SOURCE)
    if source == "code":
        return ResolvedPrompt(text=fallback, langfuse_prompt=None, is_fallback=True)

    key = "mode_ab" if mode in MODE_A_TRAFFIC_MODES else "mode_c"
    # ``names`` may be absent / a non-dict (misconfigured YAML scalar): guard with isinstance
    # so this never raises (the per-mode default name is used when names is malformed/absent).
    names_cfg = pcfg.get("names")
    name = (names_cfg.get(key) if isinstance(names_cfg, dict) else None) or prompt_name(mode)
    label = pcfg.get("label", DEFAULT_PROMPT_LABEL)
    cache_ttl = pcfg.get("cache_ttl_seconds", DEFAULT_CACHE_TTL_SECONDS)

    try:
        client = _get_client()
        prompt = client.get_prompt(
            name, label=label, fallback=fallback, cache_ttl_seconds=cache_ttl
        )
        text = getattr(prompt, "prompt", None)
        is_fb = bool(getattr(prompt, "is_fallback", False))
        if not isinstance(text, str) or not text:
            # the call SUCCEEDED but returned no usable text -> degrade to the code fallback
            # (distinct from a call failure; logged separately for legible debugging).
            log.warning(
                "langfuse prompt %r returned an empty/non-text body; using code fallback "
                "(fail-open, doc08:333)",
                name,
            )
            return ResolvedPrompt(text=fallback, langfuse_prompt=None, is_fallback=True)
        if is_fb:
            log.warning(
                "langfuse prompt %r unavailable; using code fallback (fail-open, doc08:333)",
                name,
            )
        # Only link a genuinely-fetched prompt; never link the SDK's fallback object (it has
        # no real version) so prompt-level analytics stay accurate.
        return ResolvedPrompt(
            text=text, langfuse_prompt=None if is_fb else prompt, is_fallback=is_fb
        )
    except Exception as exc:  # SDK absent / not-found / auth / network — fail-open to code
        log.warning(
            "langfuse get_prompt(%r) failed (%s); using code fallback (fail-open, doc08:333)",
            name,
            exc,
        )
        return ResolvedPrompt(text=fallback, langfuse_prompt=None, is_fallback=True)
