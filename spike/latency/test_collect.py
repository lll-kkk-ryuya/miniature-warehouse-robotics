"""Pure unit tests for collect.py — no network, no SDK, no out/ side effects.

Mirrors test_stats.py discipline: fabricate measure.py-shaped report dicts and pin
the deterministic RESULT.md §1/§2/§3 derivation, especially the survivorship-bias
viability gate (a low survivor p95 must NOT pass a 429/timeout-heavy provider).
"""

import json

import pytest
from collect import (
    COMMANDER_PROVIDERS,
    DEFERRED_PROVIDERS,
    _ms,
    blocked_timeout_for,
    condition_mismatch_warning,
    conditions_present,
    format_section1,
    format_section3_table,
    load_reports,
    provider_status,
    render,
    verdict,
)
from measure import IN_CYCLE_TIMEOUT_S, MAX_MISS_RATE


def fake_report(
    provider: str,
    *,
    p95_s: float = 2.0,
    miss: float = 0.0,
    samples: bool = True,
    n_err: int = 0,
    run_utc: str = "2026-06-15T10:00:00+00:00",
    condition: str = "fairness-off",
    floor_s: float | None = 0.05,
) -> dict:
    """A measure.py ``_build_report``-shaped dict (seconds), enough for collect.py."""
    report = {
        "provider_label": provider,
        "condition": condition,
        "model": "hermes-agent",
        "run_utc": run_utc,
        "n_requested": 120,
        "n_ok": 0 if not samples else 120 - n_err,
        "n_err": n_err,
        "n_over_in_cycle_timeout": 0,
        "missed_cycle_rate": miss,
        "gateway_floor_s": floor_s,
    }
    if samples:
        report["summary_s"] = {
            "p50": p95_s * 0.5,
            "p95": p95_s,
            "p99": p95_s * 1.05,
            "mean": p95_s * 0.55,
            "min": p95_s * 0.4,
            "max": p95_s * 1.1,
            "stdev": p95_s * 0.1,
            "n": 120 - n_err,
        }
    return report


# ---------- blocked_timeout_for (§3) ----------


@pytest.mark.parametrize(
    ("cycle", "expected"),
    [(3.0, 10.0), (4.0, 12.0), (5.0, 15.0), (3.5, 10.5), (0.0, 10.0), (3.333, 10.0)],
)
def test_blocked_timeout_for(cycle, expected):
    assert blocked_timeout_for(cycle) == pytest.approx(expected)


def test_blocked_timeout_boundary_at_10_over_3():
    # 3*cycle crosses 10 exactly at cycle = 10/3
    assert blocked_timeout_for(10.0 / 3.0) == pytest.approx(10.0)
    assert blocked_timeout_for(10.0 / 3.0 + 0.001) > 10.0


# ---------- provider_status: viability gate (§2 step0) ----------


def test_status_clean_provider_viable():
    s = provider_status(fake_report("anthropic", p95_s=2.0, miss=0.0))
    assert s["viable"] and s["p95_ok"] and s["has_samples"]


def test_status_p95_boundary_inclusive():
    # p95 == 2.5 is OK (≤); 2.6 is not
    assert provider_status(fake_report("openai", p95_s=IN_CYCLE_TIMEOUT_S))["p95_ok"] is True
    assert provider_status(fake_report("openai", p95_s=2.6))["p95_ok"] is False


def test_status_miss_boundary_inclusive():
    assert provider_status(fake_report("google", miss=MAX_MISS_RATE))["viable"] is True
    assert provider_status(fake_report("google", miss=MAX_MISS_RATE + 0.001))["viable"] is False


def test_status_survivorship_bias_low_p95_high_miss_is_not_viable():
    # The crux: survivor p95 looks great (2.0s) but 30% of calls errored/timed out.
    s = provider_status(fake_report("xai", p95_s=2.0, miss=0.30, n_err=36))
    assert s["p95_ok"] is True
    assert s["viable"] is False  # viability gate rejects it (doc08:140)


def test_status_no_samples_is_not_viable():
    s = provider_status(fake_report("openai", samples=False, miss=1.0, n_err=120))
    assert s["has_samples"] is False
    assert s["viable"] is False
    assert s["p95_ok"] is False
    assert s["p95_s"] is None


# ---------- verdict: cross-provider §2 ----------


def _three(p95s, miss=(0.0, 0.0, 0.0)):
    provs = ("anthropic", "openai", "google")
    return {p: fake_report(p, p95_s=v, miss=m) for p, v, m in zip(provs, p95s, miss, strict=True)}


def test_verdict_holds_when_all_viable_and_worst_p95_under_cutoff():
    v = verdict(_three((1.8, 2.0, 1.2)))
    assert v["holds"] is True
    assert v["decision"] == "HOLD 3s cycle"
    assert v["worst_p95_s"] == pytest.approx(2.0)
    assert v["worst_provider"] == "openai"
    assert v["cycle_total_s"] == 3.0
    assert v["blocked_timeout_s"] == pytest.approx(10.0)


def test_verdict_extends_when_worst_p95_over_cutoff():
    v = verdict(_three((1.8, 2.0, 3.1)))  # google slow
    assert v["holds"] is False
    assert v["worst_provider"] == "google"
    assert v["cycle_total_s"] is None
    assert v["blocked_timeout_s"] is None
    assert "EXTEND" in v["decision"]


def test_verdict_extends_when_one_provider_not_viable_despite_good_p95():
    # survivorship: all p95 ≤ 2.5 but one provider 20% missed → must NOT hold
    v = verdict(_three((1.8, 2.0, 1.5), miss=(0.0, 0.20, 0.0)))
    assert v["all_viable"] is False
    assert v["holds"] is False


def test_verdict_incomplete_sweep_does_not_hold():
    # only 2 of 3 commander providers measured
    reports = {p: fake_report(p, p95_s=2.0) for p in ("anthropic", "openai")}
    v = verdict(reports)
    assert v["complete_sweep"] is False
    assert "google" in v["missing"]
    assert v["holds"] is False  # provisional — cannot hold on an incomplete sweep


def test_verdict_empty_reports():
    v = verdict({})
    assert v["measured"] == []
    assert v["holds"] is False
    assert v["worst_p95_s"] is None


def test_verdict_grok_always_listed_deferred_not_dropped():
    v = verdict(_three((1.8, 2.0, 1.2)))
    assert "xai" in v["deferred"]
    assert v["deferred"]["xai"] == DEFERRED_PROVIDERS["xai"]


def test_verdict_ignores_non_commander_providers():
    reports = _three((1.8, 2.0, 1.2))
    reports["xai"] = fake_report("xai", p95_s=9.9, miss=0.9)  # should not affect verdict
    v = verdict(reports)
    assert v["holds"] is True  # xai not in COMMANDER_PROVIDERS
    assert "xai" not in v["measured"]


def test_verdict_mixed_samples_and_no_samples_no_crash():
    # One provider has zero successful samples (p95_s=None) while others have samples.
    # worst-case p95 must be max of the NON-None values (the None filter must hold), and
    # the no-sample provider makes the sweep not hold. Catches dropping the None filter
    # (collect.py p95s list comprehension) → max([..., None]) TypeError.
    reports = {
        "anthropic": fake_report("anthropic", p95_s=2.0, miss=0.0),
        "openai": fake_report("openai", samples=False, miss=1.0, n_err=120),
        "google": fake_report("google", p95_s=1.5, miss=0.0),
    }
    v = verdict(reports)
    assert v["worst_p95_s"] == pytest.approx(2.0)  # not None; not a crash
    assert v["worst_provider"] == "anthropic"
    assert v["holds"] is False  # openai non-viable (no samples)


# ---------- load_reports ----------


def _write(tmp_path, report):
    name = f"{report['provider_label']}_{report['condition']}_120_{report['run_utc']}.json"
    (tmp_path / name.replace(":", "")).write_text(json.dumps(report), encoding="utf-8")


def test_load_reports_newest_per_provider(tmp_path):
    _write(tmp_path, fake_report("anthropic", p95_s=2.0, run_utc="2026-06-15T09:00:00+00:00"))
    _write(tmp_path, fake_report("anthropic", p95_s=1.5, run_utc="2026-06-15T11:00:00+00:00"))
    reports = load_reports(tmp_path)
    assert set(reports) == {"anthropic"}
    assert reports["anthropic"]["summary_s"]["p95"] == pytest.approx(1.5)  # newer wins


def test_load_reports_condition_filter(tmp_path):
    _write(tmp_path, fake_report("openai", condition="fairness-off"))
    _write(tmp_path, fake_report("google", condition="default"))
    assert set(load_reports(tmp_path, condition="fairness-off")) == {"openai"}
    assert set(load_reports(tmp_path, condition="default")) == {"google"}


def test_load_reports_skips_malformed(tmp_path):
    (tmp_path / "broken_fairness-off_120_x.json").write_text("{not json", encoding="utf-8")
    _write(tmp_path, fake_report("anthropic"))
    assert set(load_reports(tmp_path)) == {"anthropic"}


def test_load_reports_missing_dir():
    assert load_reports(__import__("pathlib").Path("/nonexistent/xyz")) == {}


def test_load_reports_equal_timestamp_later_filename_wins(tmp_path):
    # Same provider + identical run_utc, distinct filenames. load_reports iterates
    # sorted(glob) and uses '>=', so the lexicographically-later file (b) replaces the
    # earlier (a) on the tie. Catches the '>=' → '>' mutation (which would keep a).
    ts = "2026-06-15T10:00:00+00:00"
    a = fake_report("anthropic", p95_s=2.0, run_utc=ts)
    b = fake_report("anthropic", p95_s=1.0, run_utc=ts)
    (tmp_path / "anthropic_fairness-off_120_a.json").write_text(json.dumps(a), encoding="utf-8")
    (tmp_path / "anthropic_fairness-off_120_b.json").write_text(json.dumps(b), encoding="utf-8")
    reports = load_reports(tmp_path)
    assert reports["anthropic"]["summary_s"]["p95"] == pytest.approx(1.0)  # b wins on '>='


# ---------- formatting / render smoke ----------


def test_format_section1_marks_absent_and_deferred():
    table = format_section1(_three((1.8, 2.0, 1.2)))
    assert "Claude" in table and "Gemini" in table
    assert "DEFERRED" in table  # Grok row, no report


def test_format_section1_converts_to_ms():
    table = format_section1({"anthropic": fake_report("anthropic", p95_s=2.0)})
    assert "| 2000 |" in table or " 2000 " in table  # 2.0s → 2000ms somewhere in the row


def test_render_hold_case_shows_unchanged_timeout():
    text = render(_three((1.8, 2.0, 1.2)))
    assert "HOLD 3s cycle" in text
    assert "10.0s" in text  # blocked_timeout unchanged
    assert "§3" in text


def test_render_extend_case():
    text = render(_three((1.8, 2.0, 3.1)))
    assert "EXTEND" in text


def test_render_no_reports():
    text = render({})
    assert "NO commander reports" in text


@pytest.mark.parametrize(
    ("seconds", "ms"),
    [(2.0, 2000), (0.042, 42), (0.0009, 1), (0.0005, 0), (0.0001, 0), (1.8255, 1826)],
)
def test_ms_rounding(seconds, ms):
    # round(), not int(): round(0.9)=1 but int(0.9)=0 → the 0.0009 case pins it.
    assert _ms(seconds) == ms


def test_ms_none_passthrough():
    assert _ms(None) is None


def test_commander_set_matches_result_md():
    # guard: the judged group is exactly the 3 with keys present (Grok excluded)
    assert COMMANDER_PROVIDERS == ("anthropic", "openai", "google")
    assert "xai" not in COMMANDER_PROVIDERS


# ---------- #3: §3 table has a direct regression test ----------


def test_format_section3_table_rows():
    table = format_section3_table()
    assert "| 3.0 | 9.0 | **10.0** |" in table
    assert "| 4.0 | 12.0 | **12.0** |" in table
    assert "| 5.0 | 15.0 | **15.0** |" in table


# ---------- #2: 'reports exist but none match --condition' guard ----------


def test_conditions_present_tally(tmp_path):
    _write(tmp_path, fake_report("anthropic", condition="unknown"))
    _write(tmp_path, fake_report("openai", condition="unknown"))
    _write(tmp_path, fake_report("google", condition="fairness-off"))
    assert conditions_present(tmp_path) == {"unknown": 2, "fairness-off": 1}


def test_condition_mismatch_warning_fires_on_unknown(tmp_path):
    # The footgun: operator forgot measure.py --condition → all reports are 'unknown';
    # collect.py --condition fairness-off would silently drop them all.
    _write(tmp_path, fake_report("anthropic", condition="unknown"))
    _write(tmp_path, fake_report("openai", condition="unknown"))
    w = condition_mismatch_warning(tmp_path, "fairness-off")
    assert w is not None
    assert "NONE match" in w and "fairness-off" in w and "unknown=2" in w


def test_condition_mismatch_warning_silent_when_match_exists(tmp_path):
    _write(tmp_path, fake_report("anthropic", condition="fairness-off"))
    assert condition_mismatch_warning(tmp_path, "fairness-off") is None


def test_condition_mismatch_warning_silent_when_no_filter_or_empty(tmp_path):
    _write(tmp_path, fake_report("anthropic", condition="unknown"))
    assert condition_mismatch_warning(tmp_path, None) is None  # no --condition → no guard
    assert condition_mismatch_warning(tmp_path / "empty", "fairness-off") is None  # no reports


# ---------- #1: cross-condition visibility ----------


def test_render_warns_on_mixed_conditions():
    reports = {
        "anthropic": fake_report("anthropic", p95_s=2.0, condition="fairness-off"),
        "openai": fake_report("openai", p95_s=2.0, condition="default"),
        "google": fake_report("google", p95_s=1.0, condition="fairness-off"),
    }
    text = render(reports)
    assert "MIXED conditions" in text


def test_render_shows_per_provider_condition():
    text = render(_three((1.8, 2.0, 1.2)))  # all fairness-off
    assert "[fairness-off]" in text
    assert "MIXED conditions" not in text  # single condition → no warning


# ---------- #4: EXTEND wording distinguishes cause ----------


def test_render_extend_viability_says_replace_not_extend_cycle():
    # survivorship: good p95 but flaky (20% missed) → should say SUSPECT/REPLACE, and
    # must NOT tell the operator to extend the cycle (p95 is fine).
    text = render(_three((1.8, 2.0, 1.5), miss=(0.0, 0.20, 0.0)))
    assert "viability gate FAILED" in text
    assert "SUSPECT/REPLACE" in text
    assert "EXTEND cycle to 4-5s" not in text  # p95 fine → must NOT advise extending
    assert "NOT a cycle-length issue" in text  # §3 tail reflects the same


def test_render_extend_slow_p95_says_extend_cycle():
    text = render(_three((1.8, 2.0, 3.1)))  # google slow, all viable
    assert "worst-case p95 3100ms > 2.5s → EXTEND cycle" in text
    assert "viability gate FAILED" not in text  # all viable → not a viability failure
