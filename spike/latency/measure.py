#!/usr/bin/env python3
"""measure.py — Hermes Gateway commander-call latency spike (R-07 / R-46).

Measures the **end-to-end** latency of the commander LLM call through the Hermes
Gateway (``model="hermes-agent"``), mirroring the transport in
``warehouse_llm_bridge/hermes_client.py`` (OpenAI Chat-Completions compatible,
``{base_url}/v1/chat/completions``, doc13:24-25 / hermes_client.py:3,105-106), so
the measured number reflects what the real bridge cycle pays. The collected
distribution decides two suspended designs:

* **Cycle length** — doc06:103-104: record p50/p95/p99 over ~120 calls; if
  ``p95 > 2.5s`` the Mode-A total cycle (~3s = 1s wait + response, doc08:121-128)
  must move to 4-5s. The 2.5s figure is the in-cycle timeout (doc08:140).
* **BLOCKED_TIMEOUT coupling** — R-46 (doc07:256): ``BLOCKED_TIMEOUT`` (currently
  flat ``10.0`` in config/warehouse.base.yaml:17, owned by safety-state/bringup)
  assumes a 3-cycle (9s) margin (doc08a:372); a longer cycle breaks it. The
  measured cycle feeds the re-derivation ``max(10, 3*cycle)`` — **(b) docs
  example** (doc07:256), not a frozen value.

Scope: this script **measures and labels one provider per run** (the gateway's
current ``active_provider``, doc13:175). It does NOT switch providers, restart the
gateway, or edit any config/scheduler constant — see README.md for the 4-provider
sweep and RESULT.md for the judgment.

Safety (safety.md / environments.md): **dev-only**. Refuses ``WAREHOUSE_ENV=prod``
and any non-loopback gateway (override with ``--allow-remote``, dev use only).
Reads ONLY ``API_SERVER_KEY`` (the Bridge<->Gateway auth, config/dev/.env); never
the provider secret keys (Hermes holds those in ~/.hermes/.env). The key is never
printed or written to an output file.
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from stats import summarize

DEFAULT_BASE_URL = "http://127.0.0.1:8642"
HERMES_MODEL = "hermes-agent"  # Hermes routes to active_provider (doc13:175)
DEFAULT_N = 120  # doc06:103 "約120回呼出し"
DEFAULT_WARMUP = 3  # discarded: excludes cold connection setup from the distribution
DEFAULT_TIMEOUT = 60.0  # capture the tail — NOT the 2.5s decision cutoff (doc08:140)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# A response slower than this is a MISSED cycle: the bridge keeps the previous
# command and advances (doc08:140). Errors/timeouts are likewise missed cycles.
IN_CYCLE_TIMEOUT_S = 2.5
# Viability gate on the missed-cycle rate (n_err + responses > IN_CYCLE_TIMEOUT_S).
# Survivor p95 alone hides a high failure fraction (e.g. HTTP 429 on a 120-call
# burst): an errored call does NOT fit in the cycle (R-07). This threshold is a
# **spike-local operating assumption** — there is NO doc value for it, so per
# docs-first ("発明しない") it is surfaced as an explicit assumption, not a frozen
# contract (RESULT.md §2/§6: confirm/cite when live data lands).
MAX_MISS_RATE = 0.05

# Fidelity copy of warehouse_llm_bridge/hermes_client.py:44-52 SYSTEM_PROMPT so the
# token count matches the real bridge call. This is a measurement fixture, NOT a
# contract — the authoritative prompt lives in hermes_client.py (do not import it;
# the spike stays independent of the ROS package, parallel-workflow.md §2.1).
SYSTEM_PROMPT = (
    "あなたは倉庫ロボット2台の司令官AIです。状況JSONを読み、安全性を効率性より優先して"
    "（衝突回避を最優先に）2台分の指示を決定してください。バッテリー方針: 10%以下は新規"
    "タスク禁止（充電へ）、20%以下は新規割当を控える。\n"
    "必ず次のJSON形式のみで返答してください（前後に文章を付けない）:\n"
    '{"reasoning": "判断理由", "commands": [{"bot": "bot1", "action": '
    '"navigate|wait|stop|yield|charge", "destination": "場所名", "duration": 秒数, '
    '"via": "経由ルート", "retreat_to": "退避先"}], "priority_explanation": "優先順位の説明"}'
)

# Representative Situation payload mirroring the Mode-A shape that
# SituationBuilder emits (doc08a Situation: per-bot position/velocity/heading/
# predicted_position_3s/obstacle_ahead/battery + current_task + history). Sized to
# a realistic prompt, not a trivial "PING", so the measured latency is valid
# (kickoff step 1). The exact values are illustrative; the SHAPE/size is the point.
REPRESENTATIVE_SITUATION: dict = {
    "timestamp": "2026-06-06T12:00:00Z",
    "traffic_mode": "simple",
    "robots": [
        {
            "bot": "bot1",
            "position": {"x": 0.42, "y": 0.31, "theta": 1.57},
            "velocity": {"linear": 0.18, "angular": 0.05},
            "heading": 1.57,
            "predicted_position_3s": {"x": 0.45, "y": 0.85, "theta": 1.62},
            "obstacle_ahead": False,
            "battery": 0.74,
            "status": "moving",
            "current_task": "shelf_b",
        },
        {
            "bot": "bot2",
            "position": {"x": 1.21, "y": 0.66, "theta": -1.57},
            "velocity": {"linear": 0.0, "angular": 0.0},
            "heading": -1.57,
            "predicted_position_3s": {"x": 1.21, "y": 0.66, "theta": -1.57},
            "obstacle_ahead": True,
            "battery": 0.19,
            "status": "idle",
            "current_task": None,
        },
    ],
    "history": [
        "bot1 navigate shelf_b -> ok",
        "bot2 wait -> ok",
        "bot1 navigate shelf_a -> ok",
        "bot2 yield retreat_B -> ok",
        "bot1 navigate shelf_b -> blocked",
    ],
    "locations": ["shelf_a", "shelf_b", "pickup", "dropoff", "charging_station"],
}


class Sample(NamedTuple):
    """One measured call: wall-clock seconds, success flag, error, total tokens."""

    latency_s: float
    ok: bool
    error: str | None
    tokens: int | None


def _parse_env_file(path: Path, key: str) -> str:
    """Return the value of *key* in a ``.env`` file, or "" if absent.

    Minimal ``KEY=VALUE`` parser (strips surrounding quotes). The value is a
    secret — the caller must never log it.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def load_api_server_key(env_file: Path | None) -> str:
    """Resolve ``API_SERVER_KEY``: environment first, then *env_file* (config/dev/.env).

    Returns "" if unset. Never logs the value.
    """
    from_env = os.environ.get("API_SERVER_KEY")
    if from_env:
        return from_env
    if env_file is not None:
        return _parse_env_file(env_file, "API_SERVER_KEY")
    return ""


def assert_dev_only(base_url: str, allow_remote: bool) -> None:
    """Fail-closed dev guard (safety.md / environments.md): refuse prod / non-loopback."""
    env = os.environ.get("WAREHOUSE_ENV", "dev")
    if env == "prod":
        sys.exit("REFUSED: WAREHOUSE_ENV=prod — this spike is dev-only (safety.md).")
    host = urllib.parse.urlparse(base_url).hostname or ""
    if host not in LOOPBACK_HOSTS:
        if not allow_remote:
            sys.exit(
                f"REFUSED: non-loopback gateway host {host!r}. "
                "Use a loopback dev gateway, or pass --allow-remote (dev only)."
            )
        # allow_remote intentionally bypasses the loopback guard; warn LOUDLY since
        # the env guard and host guard are independent (a non-prod env + a prod host
        # is permitted). Operator must keep this dev-only (safety.md/environments.md).
        print(
            f"WARNING: --allow-remote → connecting to NON-loopback host {host!r}. "
            "Use DEV keys/gateway ONLY — never a prod/GCP gateway (safety.md/environments.md).",
            file=sys.stderr,
        )


def make_caller(base_url: str, api_key: str, timeout: float) -> Callable[[], Sample]:
    """Build a zero-arg ``call() -> Sample`` that times one chat-completions call.

    Mirrors hermes_client.py's transport (plain ``openai`` SDK, not the langfuse
    wrapper — tracing overhead is local and out of scope for the latency floor).
    ``openai`` is imported lazily so ``--dry-run`` and the unit tests need no SDK.
    """
    import openai  # lazy: pip extra, not needed for --dry-run / tests

    # The OpenAI SDK appends "/chat/completions" to base_url itself (hermes_client.py:105).
    client = openai.OpenAI(base_url=base_url.rstrip("/") + "/v1", api_key=api_key or "no-key")
    user_content = json.dumps(REPRESENTATIVE_SITUATION, ensure_ascii=False)

    def call() -> Sample:
        t0 = time.perf_counter()
        try:
            completion = client.chat.completions.create(
                model=HERMES_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                timeout=timeout,
            )
            dt = time.perf_counter() - t0
            usage = getattr(completion, "usage", None)
            tokens = getattr(usage, "total_tokens", None)
            return Sample(dt, True, None, tokens)
        except Exception as exc:  # noqa: BLE001 — record any transport/API error, keep measuring
            dt = time.perf_counter() - t0
            # Cap length: bounds any accidental payload echo (the openai SDK redacts
            # auth, so the key is not present, but keep error dumps tidy & bounded).
            return Sample(dt, False, f"{type(exc).__name__}: {exc}"[:300], None)

    return call


def gateway_floor(base_url: str, api_key: str, timeout: float = 5.0) -> float | None:
    """Best-effort control-plane floor: round-trip of ``GET /v1/models`` (no LLM).

    A lower bound on gateway/transport cost that does NOT exercise the upstream
    provider — it is *not* a true Hermes-overhead decomposition (see RESULT.md §5).
    Returns seconds, or None if the endpoint is unavailable.
    """
    url = base_url.rstrip("/") + "/v1/models"
    req = urllib.request.Request(url)  # noqa: S310 — loopback dev gateway only
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout):  # noqa: S310
            return time.perf_counter() - t0
    except Exception:  # noqa: BLE001 — floor probe is best-effort; absence is fine
        return None


def run_measurement(
    call: Callable[[], Sample],
    n: int,
    warmup: int = 0,
    progress: Callable[[int, int, Sample], None] | None = None,
) -> dict:
    """Drive *n* measured calls (after *warmup* discarded ones) through *call*.

    Pure loop (no I/O of its own) so it is unit-testable with a fake ``call``.
    Successful latencies feed the distribution; errors are counted separately
    (an error/timeout is NOT a response-time sample — see RESULT.md §0).
    """
    for _ in range(max(0, warmup)):
        call()
    latencies: list[float] = []
    errors: list[str] = []
    tokens: list[int] = []
    for i in range(n):
        s = call()
        if progress is not None:
            progress(i, n, s)
        if s.ok:
            latencies.append(s.latency_s)
            if s.tokens is not None:
                tokens.append(s.tokens)
        elif s.error is not None:
            errors.append(s.error)
    return {
        "latencies": latencies,
        "errors": errors,
        "tokens": tokens,
        "n_requested": n,
        "warmup": max(0, warmup),
    }


def _print_progress(i: int, n: int, s: Sample) -> None:
    mark = f"{s.latency_s * 1000:7.0f}ms" if s.ok else f"ERR {s.error}"
    print(f"  [{i + 1:>4}/{n}] {mark}", file=sys.stderr)


def _build_report(
    label: str, condition: str, base_url: str, result: dict, floor_s: float | None, timeout: float
) -> dict:
    """Assemble the JSON report (no secrets) from a measurement result."""
    latencies = result["latencies"]
    tokens = result["tokens"]
    n_req = result["n_requested"]
    n_err = len(result["errors"])
    # A successful-but-slow response (> 2.5s) is ALSO a missed cycle (doc08:140).
    n_over = sum(1 for x in latencies if x > IN_CYCLE_TIMEOUT_S)
    miss_rate = (n_err + n_over) / n_req if n_req else 0.0
    host = urllib.parse.urlparse(base_url).hostname or ""
    report: dict = {
        "provider_label": label,
        "condition": condition,  # fairness-off | default | unknown (doc08:307-313 / R-36)
        "model": HERMES_MODEL,
        "gateway_host": host,
        "run_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "n_requested": n_req,
        "n_ok": len(latencies),
        "n_err": n_err,
        "n_over_in_cycle_timeout": n_over,  # ok but > 2.5s = missed cycle (doc08:140)
        "missed_cycle_rate": miss_rate,  # (n_err + n_over) / n_requested — VIABILITY input
        "warmup_discarded": result["warmup"],
        "transport_timeout_s": timeout,
        "gateway_floor_s": floor_s,
        "prompt_tokens_note": "see usage.total_tokens samples",
        "total_tokens_samples": tokens[:5],
        "errors_sample": result["errors"][:5],
    }
    if latencies:
        report["summary_s"] = summarize(latencies)
    return report


def _print_summary(report: dict) -> None:
    s = report.get("summary_s")
    miss_rate = report.get("missed_cycle_rate", 0.0)
    n_over = report.get("n_over_in_cycle_timeout", 0)
    print("\n=== latency summary (Hermes Gateway end-to-end) ===")
    print(f"provider_label : {report['provider_label']}")
    print(f"condition      : {report['condition']}  (fairness-off|default|unknown)")
    print(f"gateway_host   : {report['gateway_host']}   model: {report['model']}")
    print(
        f"n_ok / n_req   : {report['n_ok']} / {report['n_requested']}   errors: {report['n_err']}"
    )
    print(
        f"missed cycles  : {miss_rate * 100:.1f}%  (n_err={report['n_err']} + ok>2.5s={n_over})"
        f" / {report['n_requested']}  [gate ≤{MAX_MISS_RATE * 100:.0f}% spike-local; doc08:140]"
    )
    floor = report.get("gateway_floor_s")
    if floor is not None:
        print(f"gateway_floor  : {floor * 1000:.0f}ms  (GET /v1/models, no LLM — best-effort)")
    if not s:
        print("NO successful samples — cannot compute percentiles. Cycle NOT viable (100% missed).")
        return
    print(f"{'':15}{'ms':>10}")
    for k in ("p50", "p95", "p99", "mean", "min", "max", "stdev"):
        print(f"{k:15}{s[k] * 1000:>10.0f}")
    p95_ms = s["p95"] * 1000
    p95_over = s["p95"] > IN_CYCLE_TIMEOUT_S
    miss_over = miss_rate > MAX_MISS_RATE
    if p95_over or miss_over:
        why = []
        if p95_over:
            why.append(f"p95={p95_ms:.0f}ms > 2.5s")
        if miss_over:
            why.append(f"missed {miss_rate * 100:.1f}% > {MAX_MISS_RATE * 100:.0f}%")
        verdict = (
            f"cycle does NOT hold ({'; '.join(why)}) → cycle 4-5s / suspect provider (doc06:104)"
        )
    else:
        verdict = (
            f"p95={p95_ms:.0f}ms ≤ 2.5s AND missed {miss_rate * 100:.1f}%"
            f" ≤ {MAX_MISS_RATE * 100:.0f}% → 3s Mode-A cycle holds (doc06:104)"
        )
    print(f"\njudgment hint  : {verdict}")
    if miss_over:
        print(
            "WARN: high missed-cycle rate — survivor p95 alone is NOT sufficient;"
            " an errored/timed-out call IS a missed cycle (doc08:140)."
        )
    print(
        "(decision figures = p50/p95 + missed-cycle rate; p99 at n~120 barely estimable — RESULT.md §0/§2)"
    )


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    default_env = repo_root / "config" / "dev" / ".env"
    parser = argparse.ArgumentParser(description="Hermes Gateway latency spike (R-07/R-46).")
    parser.add_argument(
        "-p",
        "--provider",
        required=True,
        help="label of the gateway's CURRENT active_provider "
        "(anthropic|openai|google|xai) — for output labelling only",
    )
    parser.add_argument(
        "--condition",
        choices=["fairness-off", "default", "unknown"],
        default="unknown",
        help="measurement condition: Hermes memory/skills/session_search "
        "OFF (R-36/doc08:307-313) vs default. Declare it (transparency).",
    )
    parser.add_argument(
        "-n",
        "--n",
        type=int,
        default=DEFAULT_N,
        help=f"measured samples (default {DEFAULT_N}, doc06:103)",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP,
        help=f"discarded warmup calls (default {DEFAULT_WARMUP})",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("HERMES_BASE_URL", DEFAULT_BASE_URL),
        help=f"gateway base url (default {DEFAULT_BASE_URL} or $HERMES_BASE_URL)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"transport timeout s (default {DEFAULT_TIMEOUT}; captures the tail)",
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent / "out"),
        help="output directory for the JSON report",
    )
    parser.add_argument(
        "--env-file",
        default=str(default_env),
        help="env file to read API_SERVER_KEY from (default config/dev/.env)",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="permit a non-loopback gateway (dev only; off by default)",
    )
    parser.add_argument(
        "--no-floor", action="store_true", help="skip the GET /v1/models control-plane floor probe"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate config and print the plan WITHOUT any paid API call",
    )
    args = parser.parse_args(argv)

    assert_dev_only(args.base_url, args.allow_remote)
    env_file = Path(args.env_file) if args.env_file else None
    api_key = load_api_server_key(env_file)

    if args.dry_run:
        print("=== DRY RUN (no API calls) ===")
        print(f"base_url        : {args.base_url}")
        print(f"model           : {HERMES_MODEL}")
        print(f"provider_label  : {args.provider}")
        print(f"condition       : {args.condition}")
        print(f"n / warmup      : {args.n} / {args.warmup}")
        print(f"timeout_s       : {args.timeout}")
        print(f"API_SERVER_KEY  : {'present' if api_key else 'ABSENT'} (value never shown)")
        print(f"out_dir         : {args.out}")
        sys_prompt_chars = len(SYSTEM_PROMPT)
        user_chars = len(json.dumps(REPRESENTATIVE_SITUATION, ensure_ascii=False))
        print(
            f"prompt chars    : system={sys_prompt_chars} user={user_chars} "
            "(representative situation, not PING)"
        )
        return 0

    if not api_key:
        sys.exit(
            "REFUSED: API_SERVER_KEY not found (env or --env-file). "
            "A gateway with API_SERVER_ENABLED=true requires it."
        )

    floor_s = None if args.no_floor else gateway_floor(args.base_url, api_key)
    call = make_caller(args.base_url, api_key, args.timeout)
    print(
        f"measuring {args.n} samples (+{args.warmup} warmup) against {args.base_url} ...",
        file=sys.stderr,
    )
    result = run_measurement(call, args.n, args.warmup, progress=_print_progress)
    report = _build_report(
        args.provider, args.condition, args.base_url, result, floor_s, args.timeout
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = report["run_utc"].replace(":", "").replace("-", "")
    base = f"{args.provider}_{args.condition}_{args.n}_{stamp}"
    out_path = out_dir / f"{base}.json"
    dup = 1
    while out_path.exists():  # never silently overwrite a same-second run
        out_path = out_dir / f"{base}_{dup}.json"
        dup += 1
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary(report)
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
