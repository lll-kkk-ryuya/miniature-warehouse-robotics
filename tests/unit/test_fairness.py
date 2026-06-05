"""Tests for the Phase 4 comparison fairness guard (#103, doc08 §比較の公平性).

The forced-OFF invariant is a HARD invariant: if it regresses, a Phase 4 four-provider
comparison silently mixes memory/learning drift into the result (R-36). The kickoff
treats it as unit-required even though it is not an Emergency Guardian / Policy Gate.
These are pure-logic tests (no ROS), so they carry the ``unit`` marker; the config
resolution case exercises the real base→overlay→env path via ``load_config``.
"""

from pathlib import Path

import pytest
from warehouse_interfaces.config import load_config
from warehouse_llm_bridge.fairness import (
    FAIRNESS_LOG_PREFIX,
    FairnessViolationError,
    MemoryPolicy,
    assert_fairness,
    check_fairness,
    fairness_log_line,
    resolve_memory_policy,
)


@pytest.mark.unit
def test_resolve_defaults_off_when_hermes_absent() -> None:
    # Fairness-safe default: a run can never enable learning by omission.
    policy = resolve_memory_policy({})
    assert policy == MemoryPolicy(False, False, False)


@pytest.mark.unit
def test_resolve_reads_declared_intent() -> None:
    cfg = {"hermes": {"memory_enabled": True, "skills_enabled": True, "comparison_run": True}}
    policy = resolve_memory_policy(cfg)
    assert policy == MemoryPolicy(True, True, True)


@pytest.mark.unit
def test_resolve_missing_keys_default_off() -> None:
    # Only comparison_run set; memory/skills absent -> OFF (safe), not raising here.
    policy = resolve_memory_policy({"hermes": {"comparison_run": True}})
    assert policy == MemoryPolicy(False, False, True)


@pytest.mark.unit
def test_non_comparison_run_allows_memory_on() -> None:
    # Mode A entertainment: memory ON is allowed outside a comparison run (doc08:312).
    policy = MemoryPolicy(memory_enabled=True, skills_enabled=True, comparison_run=False)
    assert check_fairness(policy) == []
    assert_fairness(policy)  # does not raise


@pytest.mark.unit
def test_comparison_run_all_off_passes() -> None:
    policy = MemoryPolicy(memory_enabled=False, skills_enabled=False, comparison_run=True)
    assert check_fairness(policy) == []
    assert_fairness(policy)  # does not raise


@pytest.mark.unit
def test_comparison_run_memory_on_aborts() -> None:
    policy = MemoryPolicy(memory_enabled=True, skills_enabled=False, comparison_run=True)
    with pytest.raises(FairnessViolationError, match="memory_enabled must be false"):
        assert_fairness(policy)


@pytest.mark.unit
def test_comparison_run_skills_on_aborts_independently() -> None:
    # #44 lesson: each knob is validated INDEPENDENTLY. memory absent/OFF must NOT
    # mask skills_enabled=True — the comparison run still aborts on skills alone.
    policy = MemoryPolicy(memory_enabled=False, skills_enabled=True, comparison_run=True)
    violations = check_fairness(policy)
    assert violations == ["hermes.skills_enabled must be false for a comparison run"]
    with pytest.raises(FairnessViolationError, match="skills_enabled must be false"):
        assert_fairness(policy)


@pytest.mark.unit
def test_comparison_run_both_on_reports_both() -> None:
    policy = MemoryPolicy(memory_enabled=True, skills_enabled=True, comparison_run=True)
    assert check_fairness(policy) == [
        "hermes.memory_enabled must be false for a comparison run",
        "hermes.skills_enabled must be false for a comparison run",
    ]


@pytest.mark.unit
def test_log_line_marks_comparison_and_non_comparison() -> None:
    comp = fairness_log_line(MemoryPolicy(False, False, True))
    non = fairness_log_line(MemoryPolicy(True, False, False))
    assert FAIRNESS_LOG_PREFIX in comp and "comparison run" in comp
    assert FAIRNESS_LOG_PREFIX in non and "memory_enabled=True" in non


@pytest.mark.unit
def test_violation_message_carries_fairness_marker() -> None:
    policy = MemoryPolicy(memory_enabled=True, skills_enabled=False, comparison_run=True)
    with pytest.raises(FairnessViolationError) as exc:
        assert_fairness(policy)
    assert FAIRNESS_LOG_PREFIX in str(exc.value)


@pytest.mark.unit
def test_env_override_drives_guard_through_load_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # DoD: the toggle is config-driven via base→overlay→env (doc08:316). An env var
    # flips a run to a comparison run; a left-on memory intent then aborts.
    base = tmp_path / "base.yaml"
    base.write_text("hermes:\n  base_url: http://localhost:8642\n  memory_enabled: true\n")
    monkeypatch.setenv("WAREHOUSE__HERMES__COMPARISON_RUN", "true")
    cfg = load_config([base])
    policy = resolve_memory_policy(cfg)
    assert policy == MemoryPolicy(memory_enabled=True, skills_enabled=False, comparison_run=True)
    with pytest.raises(FairnessViolationError):
        assert_fairness(policy)
