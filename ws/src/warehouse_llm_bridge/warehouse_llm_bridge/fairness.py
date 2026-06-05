"""Phase 4 comparison fairness guard for Hermes self-learning (#103, doc08 §比較の公平性).

doc08 freezes the policy (#111): a Phase 4 four-provider comparison run MUST disable
Hermes long-term learning — ``memory`` (MEMORY.md/USER.md), ``session_search`` (FTS5
past-conversation recall) and ``skills`` — so that memory/learning drift is not
mistaken for an LLM *capability* difference and the comparison stays reproducible
(R-36, ``docs/shared/07-research-notes.md``).

Scope of this guard — the honest contract (do not oversell it):
    This Bridge-side guard does NOT and CANNOT turn Hermes memory off. The Bridge
    speaks a STATELESS ``/v1/chat/completions`` to Hermes (``hermes_client.py``) and
    can neither read nor set Hermes's loaded memory state; that state lives in a
    SEPARATE Hermes config on a process that starts BEFORE the Bridge with no
    hot-reload (``13-hermes-setup.md`` §8 / :544). The AUTHORITATIVE OFF is the
    Hermes config (``memory.memory_enabled:false`` + ``user_profile_enabled:false``
    + excluding the ``memory``/``skills``/``session_search`` toolsets) — see
    ``13-hermes-setup.md`` §"Memory / Skills / session_search — 比較公平性のための OFF 機構".

What this guard DOES: it reads the warehouse config's declared *intent*
(``hermes.memory_enabled`` / ``skills_enabled`` / ``comparison_run``, defaults OFF)
and, for a comparison run, REFUSES TO START (raises) if that intent is internally
inconsistent — i.e. the run is marked as a Phase 4 comparison but memory or skills
are declared ON. It is a fail-closed consistency check that mirrors
``warehouse_interfaces.config._validate_safety`` (abort at config-load so a
misconfigured fairness run fails loudly at startup instead of silently producing
contaminated comparison metrics). Each knob is validated INDEPENDENTLY — never gate
one behind another's presence (#44 lesson, ``config.py:73-75``): a comparison run
with ``memory_enabled`` absent/None is treated as OFF (safe), while ``skills_enabled``
True still aborts on its own.
"""

from dataclasses import dataclass
from typing import Any

# Substring every fairness log line / abort message carries so operators (and the
# unit tests) can grep one marker regardless of language (doc08:313 「公平性ガード」).
FAIRNESS_LOG_PREFIX = "公平性ガード / fairness guard"


@dataclass(frozen=True)
class MemoryPolicy:
    """Resolved Hermes self-learning *intent* from warehouse config (#103).

    These are operator-declared intent flags, NOT a control of Hermes — see the
    module docstring. ``comparison_run`` marks a Phase 4 comparison run (which
    ``traffic_mode`` alone cannot distinguish from a Mode A entertainment run,
    since both run ``traffic_mode: none``; doc08:313-314).
    """

    memory_enabled: bool
    skills_enabled: bool
    comparison_run: bool


def resolve_memory_policy(cfg: dict[str, Any]) -> MemoryPolicy:
    """Read ``hermes.{memory_enabled,skills_enabled,comparison_run}`` (default OFF).

    Resolution follows the merged warehouse config (base → overlay → ``WAREHOUSE__*``
    env, ``warehouse_interfaces.config.load_config``), so e.g.
    ``WAREHOUSE__HERMES__COMPARISON_RUN=true`` flips a run to comparison mode without
    a code change. Missing keys default to OFF — the fairness-safe default, so a run
    can never accidentally enable learning by omission.
    """
    hermes = cfg.get("hermes") or {}
    return MemoryPolicy(
        memory_enabled=bool(hermes.get("memory_enabled", False)),
        skills_enabled=bool(hermes.get("skills_enabled", False)),
        comparison_run=bool(hermes.get("comparison_run", False)),
    )


class FairnessViolationError(ValueError):
    """A Phase 4 comparison run was configured with memory/skills declared ON."""


def check_fairness(policy: MemoryPolicy) -> list[str]:
    """Return the list of fairness violations for *policy* (empty when consistent).

    A non-comparison run is always consistent (Mode A entertainment may enable
    memory; Mode C/WO default OFF via the OFF default). For a comparison run each
    knob is checked INDEPENDENTLY (#44).
    """
    if not policy.comparison_run:
        return []
    violations: list[str] = []
    if policy.memory_enabled:
        violations.append("hermes.memory_enabled must be false for a comparison run")
    if policy.skills_enabled:
        violations.append("hermes.skills_enabled must be false for a comparison run")
    return violations


def assert_fairness(policy: MemoryPolicy) -> None:
    """Raise :class:`FairnessViolationError` if a comparison run declares learning ON.

    Fail-closed: aborts node startup (like ``_validate_safety``) so a misconfigured
    comparison run never produces contaminated metrics that look valid.
    """
    violations = check_fairness(policy)
    if violations:
        raise FairnessViolationError(
            f"{FAIRNESS_LOG_PREFIX}: comparison run requires Hermes memory/skills OFF, "
            f"but warehouse config declares them ON — {'; '.join(violations)}. "
            "Authoritative OFF lives in the Hermes config (13-hermes-setup.md §OFF 機構); "
            "this Bridge guard only refuses an internally inconsistent run."
        )


def fairness_log_line(policy: MemoryPolicy) -> str:
    """One-line startup log describing the resolved fairness intent (doc08:313)."""
    if policy.comparison_run:
        return (
            f"{FAIRNESS_LOG_PREFIX}: comparison run — Hermes memory/skills declared OFF "
            "(intent asserted; authoritative OFF = Hermes config, 13-hermes-setup.md §OFF 機構)"
        )
    return (
        f"{FAIRNESS_LOG_PREFIX}: non-comparison run — "
        f"memory_enabled={policy.memory_enabled} skills_enabled={policy.skills_enabled} "
        "(Mode A entertainment may enable; Mode C/WO default OFF, doc08:314-315)"
    )
