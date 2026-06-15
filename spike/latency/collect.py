#!/usr/bin/env python3
"""collect.py — turn measure.py's JSON reports into the RESULT.md verdict (R-07/R-46).

This is the **transcription + judgment** half of the latency spike. ``measure.py``
produces one ``out/<provider>_<condition>_<n>_<utc>.json`` per provider run; this
script reads those reports and mechanically derives:

* **§1 table** — p50/p95/p99/mean/max/err/missed%/floor in **ms** (the reports store
  seconds), so the hand-conversion the README §"結果の取り込み" asks for cannot drift.
* **§2 verdict** — the doc-cited rule, applied deterministically: first the
  **viability gate** (each provider's ``missed_cycle_rate`` ≤ threshold — survivor
  p95 alone hides a 429/timeout-heavy provider, doc08:140), then the **worst-case
  p95** across the commander provider group vs the 2.5s in-cycle cutoff (doc06:104 /
  RESULT.md §2). "Holds" ⇔ all measured providers viable AND worst-case p95 ≤ 2.5s.
* **§3 BLOCKED_TIMEOUT** — ``max(10, 3*cycle)`` as a pure function (doc07:256, a
  **(b) docs example**, not a frozen value). For the "hold" case the cycle is 3.0s
  (→ 10.0s, unchanged). For the "extend" case the cycle_total is an **operator
  judgment** (``wait + p95 response``, RESULT.md §2 step4 — the wait term is a design
  choice, not in docs), so this script prints the reference table for cycles {3,4,5}
  rather than inventing the pick (docs-first「発明しない」).

Single source of truth: the thresholds (``MAX_MISS_RATE`` / ``IN_CYCLE_TIMEOUT_S``)
are **imported from measure.py**, never redefined here, so the per-provider gate and
the cross-provider verdict cannot diverge.

Scope: read-only. Reads ``out/*.json`` only — **no network, no paid API call, no
gateway**. It fabricates nothing: a provider with no report stays ``—`` (Grok is
DEFERRED, not silently dropped — no xAI key, RESULT.md §0/§1). It does NOT edit
RESULT.md or any config/scheduler constant (that is the operator's §7 follow-up,
owned by other tracks — spike/latency/CLAUDE.md edit boundary).
"""

import argparse
import json
from pathlib import Path

from measure import IN_CYCLE_TIMEOUT_S, MAX_MISS_RATE  # single source of truth (no redefine)

# Commander model per provider (doc13:184-195 / RESULT.md §0). For labelling/notes
# only — measure.py records model="hermes-agent" (the gateway alias), so this map is
# NOT used to verify which provider was actually active (that is a measure-time check
# against GET /v1/models, out of this read-only helper's scope).
EXPECTED_MODELS: dict[str, str] = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-4o",
    "google": "gemini-2.5-flash",
    "xai": "grok-4.3",
}
# The judged commander group (RESULT.md §0/§1). Grok is deferred until XAI_API_KEY
# exists in ~/.hermes/.env — surfaced explicitly so it is never a silent drop.
COMMANDER_PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "google")
DEFERRED_PROVIDERS: dict[str, str] = {"xai": "no XAI_API_KEY in ~/.hermes/.env"}

# §1 column order → RESULT.md §1 table. None → rendered as "—".
PROVIDER_LABELS = {
    "anthropic": "Claude",
    "openai": "GPT",
    "google": "Gemini",
    "xai": "Grok",
}


def blocked_timeout_for(cycle_total_s: float) -> float:
    """``max(10, 3*cycle)`` — R-46 / doc07:256 (a docs **example**, not frozen)."""
    return max(10.0, 3.0 * cycle_total_s)


def _ms(seconds: float | None) -> int | None:
    """Seconds → integer ms (nearest), or None passthrough."""
    return None if seconds is None else round(seconds * 1000)


def load_reports(out_dir: Path, condition: str | None = None) -> dict[str, dict]:
    """Read ``out/*.json``; keep the NEWEST report per ``provider_label``.

    Optionally filter to a single ``condition`` (fairness-off|default). Newest is by
    ``run_utc`` (ISO-8601, lexicographically sortable). Malformed files are skipped.
    Returns ``{provider_label: report}``. Read-only.
    """
    newest: dict[str, dict] = {}
    if not out_dir.is_dir():
        return newest
    for path in sorted(out_dir.glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        label = report.get("provider_label")
        if not isinstance(label, str):
            continue
        if condition is not None and report.get("condition") != condition:
            continue
        prev = newest.get(label)
        if prev is None or str(report.get("run_utc", "")) >= str(prev.get("run_utc", "")):
            newest[label] = report
    return newest


def provider_status(report: dict) -> dict:
    """Per-provider gate inputs (RESULT.md §2): viability + p95-vs-2.5s.

    ``has_samples`` is False when every call errored (no ``summary_s``) — that is a
    100%-missed, non-viable provider, NOT a pass with "no data".
    """
    summary = report.get("summary_s")
    has_samples = isinstance(summary, dict) and "p95" in summary
    p95_s = summary["p95"] if has_samples else None
    miss = float(report.get("missed_cycle_rate", 1.0))
    viable = (miss <= MAX_MISS_RATE) and has_samples
    p95_ok = has_samples and p95_s is not None and p95_s <= IN_CYCLE_TIMEOUT_S
    return {
        "provider": report.get("provider_label"),
        "has_samples": has_samples,
        "p95_s": p95_s,
        "missed_cycle_rate": miss,
        "viable": viable,  # missed ≤ threshold AND has samples
        "p95_ok": p95_ok,  # p95 ≤ 2.5s
    }


def verdict(reports: dict[str, dict]) -> dict:
    """Apply the deterministic RESULT.md §2 rule across the commander group.

    HOLD (3s) ⇔ every measured commander provider is viable (missed ≤ threshold) AND
    the worst-case p95 across them is ≤ 2.5s. Otherwise EXTEND (4-5s / suspect
    provider) — and the cycle_total pick is left to the operator (wait term is a
    design choice, §2 step4), so ``cycle_total``/``blocked_timeout`` stay None here.
    """
    measured = {p: reports[p] for p in COMMANDER_PROVIDERS if p in reports}
    statuses = {p: provider_status(r) for p, r in measured.items()}
    missing = [p for p in COMMANDER_PROVIDERS if p not in measured]

    p95s = [s["p95_s"] for s in statuses.values() if s["p95_s"] is not None]
    worst_p95_s = max(p95s) if p95s else None
    worst_provider = None
    if worst_p95_s is not None:
        worst_provider = next(p for p, s in statuses.items() if s["p95_s"] == worst_p95_s)

    all_viable = bool(statuses) and all(s["viable"] for s in statuses.values())
    complete = not missing
    holds = (
        all_viable and complete and worst_p95_s is not None and worst_p95_s <= IN_CYCLE_TIMEOUT_S
    )

    return {
        "measured": list(measured),
        "missing": missing,  # commander providers with no report (sweep incomplete)
        "deferred": dict(DEFERRED_PROVIDERS),  # Grok etc. — explicit, not silent
        "statuses": statuses,
        "worst_p95_s": worst_p95_s,
        "worst_provider": worst_provider,
        "all_viable": all_viable,
        "complete_sweep": complete,
        "holds": holds,
        "decision": "HOLD 3s cycle" if holds else "EXTEND to 4-5s / suspect provider",
        "cycle_total_s": 3.0 if holds else None,
        "blocked_timeout_s": blocked_timeout_for(3.0) if holds else None,
    }


def format_section1(reports: dict[str, dict]) -> str:
    """RESULT.md §1 table rows (ms), one per provider; absent provider → '—'."""
    header = (
        "| provider | model | p50 | p95 | p99 | mean | max | err | missed% | floor | viability |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|"
    )
    lines = [header]
    for prov, label in PROVIDER_LABELS.items():
        model = EXPECTED_MODELS.get(prov, "—")
        report = reports.get(prov)
        if report is None:
            note = "DEFERRED" if prov in DEFERRED_PROVIDERS else "not measured"
            lines.append(f"| {label} | {model} | — | — | — | — | — | — | — | — | {note} |")
            continue
        s = report.get("summary_s") or {}
        st = provider_status(report)
        miss_pct = f"{st['missed_cycle_rate'] * 100:.1f}%"
        floor = _ms(report.get("gateway_floor_s"))
        viab = "ok" if st["viable"] and st["p95_ok"] else "FAIL"
        lines.append(
            f"| {label} | {model} "
            f"| {_ms(s.get('p50')) if s else '—'} "
            f"| {_ms(s.get('p95')) if s else '—'} "
            f"| {_ms(s.get('p99')) if s else '—'} "
            f"| {_ms(s.get('mean')) if s else '—'} "
            f"| {_ms(s.get('max')) if s else '—'} "
            f"| {report.get('n_err', '—')} "
            f"| {miss_pct} "
            f"| {floor if floor is not None else '—'} "
            f"| {viab} |"
        )
    return "\n".join(lines)


def format_section3_table(cycles: tuple[float, ...] = (3.0, 4.0, 5.0)) -> str:
    """RESULT.md §3 reference table: cycle_total → max(10, 3×cycle)."""
    lines = ["| cycle_total (s) | 3×cycle | max(10, 3×cycle) |", "|---|---|---|"]
    for c in cycles:
        lines.append(f"| {c:.1f} | {3.0 * c:.1f} | **{blocked_timeout_for(c):.1f}** |")
    return "\n".join(lines)


def render(reports: dict[str, dict]) -> str:
    """Full operator-facing report: §1 table + §2 verdict + §3 timeout."""
    v = verdict(reports)
    out = ["## §1 results (ms) — derived from out/*.json", "", format_section1(reports), ""]

    out.append("## §2 cycle-length verdict (doc06:104 / doc08:140 / RESULT.md §2)")
    if not v["measured"]:
        out.append("- NO commander reports found in out/ — run the sweep first (README §4).")
    else:
        out.append(f"- measured: {', '.join(v['measured'])}")
        if v["missing"]:
            out.append(
                f"- ⚠ INCOMPLETE sweep — missing: {', '.join(v['missing'])} "
                "(verdict is provisional until all 3 commander providers are measured)"
            )
        if v["deferred"]:
            for p, why in v["deferred"].items():
                out.append(f"- ⏸ {PROVIDER_LABELS.get(p, p)} DEFERRED ({why}) — not a silent drop")
        for p, s in v["statuses"].items():
            tag = "viable" if s["viable"] else "NOT-viable"
            p95 = f"{_ms(s['p95_s'])}ms" if s["p95_s"] is not None else "no samples"
            out.append(
                f"  - {p}: missed={s['missed_cycle_rate'] * 100:.1f}% "
                f"(≤{MAX_MISS_RATE * 100:.0f}%? {tag}), p95={p95} "
                f"({'≤' if s['p95_ok'] else '>'}2.5s)"
            )
        if v["worst_p95_s"] is not None:
            out.append(f"- worst-case p95 = {_ms(v['worst_p95_s'])}ms ({v['worst_provider']})")
        out.append(f"- **DECISION: {v['decision']}**")
        if v["holds"]:
            out.append(
                "  - all viable AND worst-case p95 ≤ 2.5s → 3s Mode-A cycle holds (doc06:104)"
            )
        else:
            out.append(
                "  - viability gate or worst-case p95 failed → cycle 4-5s / suspect provider; "
                "operator sets cycle_total per §2 step4 (wait + p95 response)"
            )

    out += [
        "",
        "## §3 BLOCKED_TIMEOUT = max(10, 3×cycle) (R-46 / doc07:256)",
        "",
        format_section3_table(),
    ]
    if not v["measured"]:
        out.append(
            "\n→ no commander reports yet — run the sweep (README §4) to derive the verdict."
        )
    elif v["holds"]:
        out.append(
            f"\n→ HOLD: cycle_total=3.0s → blocked_timeout = **{v['blocked_timeout_s']:.1f}s** (unchanged)."
        )
    else:
        out.append(
            "\n→ EXTEND: pick cycle_total from §2, then read blocked_timeout off the table above."
        )
    out.append(
        "\n(apply = follow-up, owned by llm-bridge [cycle] / safety-state·bringup [blocked_timeout] — RESULT.md §7)"
    )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Derive RESULT.md §1/§2/§3 from measure.py out/*.json (read-only)."
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent / "out"),
        help="directory of measure.py JSON reports (default spike/latency/out)",
    )
    parser.add_argument(
        "--condition",
        choices=["fairness-off", "default", "unknown"],
        default=None,
        help="filter to one measurement condition (default: any)",
    )
    args = parser.parse_args(argv)
    reports = load_reports(Path(args.out), args.condition)
    print(render(reports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
