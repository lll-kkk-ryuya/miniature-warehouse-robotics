#!/usr/bin/env python3
"""verify.py — Langfuse Phase-3 real-trace verification harness (doc13:520 ①〜⑤).

Phase 4 compares 4 providers × 3 traffic modes, which presumes a verifiable Langfuse
observability layer: **a single Bridge-owned trace per cycle (no double generation),
a deterministic ``trace_id`` that #4 and #6 derive identically, and a non-zero cost for
all four providers (incl. xAI Grok)**. doc20 §8.4.3 says these can be confirmed ONLY
against a real Langfuse (4.7.x) + Hermes + 4 provider keys — a paid, human-gated run.

This module is the **turnkey harness** for that run, in two halves (mirroring
``spike/latency`` and ``spike/memory-gate``: an offline-testable pure core + a live
driver behind a human gate):

* **Offline core (autonomous, CI-green)** — the verification *logic* as pure predicates
  over a normalized "trace readback" structure: trace_id determinism / normalization,
  the Grok offline-cost arithmetic, and the ①〜⑤ assertions. These are unit-tested with
  a **fake Langfuse client** (``test_verify.py``) and need no SDK, no network, no keys.
* **Live driver (human gate)** — ``run_live`` calls Hermes through the Bridge-owned
  ``langfuse.openai`` path (doc08:517), flushes, reads the trace back, and feeds the real
  readback to the same predicates. Requires real keys + Hermes; **dev-only, fail-closed**.

Boundary (kickoff / parallel-workflow §2.1): this spike is **independent of the ROS
packages**. It does NOT import ``warehouse_orchestrator`` (``trace_id.py`` / ``grok_cost.py``);
it re-implements the *documented SDK contract* (``langfuse.create_trace_id``, 32-hex-no-dash)
so the harness verifies that an independent implementation satisfies the same contract.
Per doc08:508 the harness fixes **no** Grok price or literal model string in code — prices
are *injected* (offline) or recorded as expected values in ``CHECKLIST.md`` (live).

Safety (safety.md / environments.md): the live driver refuses ``WAREHOUSE_ENV=prod`` and a
non-loopback gateway, reads only ``API_SERVER_KEY`` + the two ``HERMES_LANGFUSE_*`` keys,
and never prints or writes a secret value.
"""

import argparse
import json
import os
import re
import sys
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

# ── frozen contract constants (docs-cited; NOT invented) ──────────────────────────────────
# Langfuse trace ids are 32 lowercase hex, no dashes (W3C trace-context, doc13:516).
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
# SDK pin under test (doc13:514 "v4 (4.7.1, OTEL ベース)"): accept >=4.7,<5 (⑤ smoke).
SDK_MIN: tuple[int, int] = (4, 7)
SDK_MAX_EXCL: tuple[int, int] = (5, 0)
# Defensive token-key aliases for a Langfuse ``usage_details`` mapping. The live key shape is
# UNVERIFIED (doc08:508), so parse across documented v4 / OpenAI-compatible aliases instead of
# fixing one unconfirmed key (same defensive stance as wo grok_cost.py, which this does NOT import).
_INPUT_TOKEN_KEYS = ("input", "input_tokens", "prompt_tokens")
_OUTPUT_TOKEN_KEYS = ("output", "output_tokens", "completion_tokens")
# Candidate keys for a managed-prompt link on a generation (④). v4 attaches the linked prompt
# under one of these; the exact field is live-confirmed (doc13:520④ / doc14:320), so check defensively.
_PROMPT_LINK_KEYS = ("prompt", "prompt_name", "promptName")

# Live transport defaults (mirror spike/latency — the Bridge talks OpenAI-compatible to Hermes).
DEFAULT_BASE_URL = "http://127.0.0.1:8642"
HERMES_MODEL = "hermes-agent"  # Hermes routes to its active_provider (doc13:175)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_ENV_API_KEY = "API_SERVER_KEY"  # Bridge<->Gateway auth (config/dev/.env)
_ENV_LF_PUBLIC = "HERMES_LANGFUSE_PUBLIC_KEY"  # langfuse_sink.py:58
_ENV_LF_SECRET = "HERMES_LANGFUSE_SECRET_KEY"  # langfuse_sink.py:59
_ENV_RUN_ID = "WAREHOUSE_RUN_ID"  # per-run id shared by #4/#6 (doc13:519 / trace_id.py:31)

# A representative inbound situation, sized like the real Mode-A bridge call (not a "PING"), so the
# live trace/generation looks like production. Values are illustrative; the SHAPE is the point.
REPRESENTATIVE_SITUATION: dict = {
    "timestamp": "2026-06-11T12:00:00Z",
    "traffic_mode": "simple",
    "robots": [
        {"bot": "bot1", "position": {"x": 0.42, "y": 0.31}, "battery": 0.74, "status": "moving"},
        {"bot": "bot2", "position": {"x": 1.21, "y": 0.66}, "battery": 0.19, "status": "idle"},
    ],
    "locations": ["shelf_a", "shelf_b", "pickup", "dropoff", "charging_station"],
}
SYSTEM_PROMPT = (
    "あなたは倉庫ロボット2台の司令官AIです。状況JSONを読み、安全性を効率性より優先して"
    "2台分の指示をJSONで返してください。"
)


# ══════════════════════════════════════════════════════════════════════════════════════════
# OFFLINE CORE — pure predicates (no SDK, no network). Unit-tested in test_verify.py.
# ══════════════════════════════════════════════════════════════════════════════════════════


def normalize_trace_id(value: str) -> str:
    """Return a Langfuse-valid 32-hex-no-dash trace id (doc13:516); raise on a malformed id.

    Strips dashes + lowercases (a 32-hex UUID, dashed or not, normalizes cleanly). A dashed
    UUID string is rejected by v4 and orphans the score, so we fail at the boundary rather than
    silently produce an orphan. Independent re-impl of the documented contract (NOT a wo import).
    """
    cleaned = value.replace("-", "").strip().lower()
    if not _HEX32.match(cleaned):
        raise ValueError(f"trace_id must be 32 hex chars (no dash); got {value!r}")
    return cleaned


def seed_for(run_id: str, gen_id: int) -> str:
    """The deterministic trace seed both lanes hash: ``f"{run_id}:{gen_id}"`` (doc13:516,519)."""
    return f"{run_id}:{gen_id}"


def derive_trace_id(seed: str, create_fn: Callable[..., str]) -> str | None:
    """Derive a 32-hex trace id from ``seed`` via ``langfuse.create_trace_id`` (doc13:519b).

    ``create_fn`` is injected (the langfuse static helper in production, a fake in tests) so the
    derivation is testable without the SDK. Returns ``None`` (fail-open) when the call or the
    normalization fails — the live caller no-ops, the offline test asserts the happy path.
    """
    try:
        derived = create_fn(seed=seed)
        return normalize_trace_id(derived) if derived else None
    except Exception:  # noqa: BLE001 — fail-open: a derivation error must not break the caller
        return None


def trace_ids_match(leg_a: str | None, leg_b: str | None) -> bool:
    """True iff both legs derived a non-empty, identical trace id (#4 ↔ #6 determinism, doc13:519).

    This is the offline proof that the two lanes attach to the SAME Langfuse trace: feeding the
    same ``f"{run_id}:{gen_id}"`` seed to ``create_trace_id`` must yield byte-identical ids.
    """
    return bool(leg_a) and bool(leg_b) and leg_a == leg_b


def check_inbound_trace_id(observed: str | None, expected_seed_id: str | None) -> bool:
    """① The trace we read back carries the seed-derived id (Bridge owns it; Hermes respected it).

    doc13:520① — if Hermes honours the inbound ``metadata.trace_id`` (Bridge-owned, Pattern A),
    the generation lands under the seed-derived trace id. Normalizes both sides before compare so
    a dashed/uppercase variant does not false-negative.
    """
    if not observed or not expected_seed_id:
        return False
    try:
        return normalize_trace_id(observed) == normalize_trace_id(expected_seed_id)
    except ValueError:
        return False


def _token_count(usage_details: Mapping[str, object], keys: tuple[str, ...]) -> float:
    """First numeric value among ``keys`` in ``usage_details``, else ``0.0`` (defensive parse).

    ``bool`` is excluded though it subclasses ``int`` — a stray ``True`` must not count as a token.
    """
    for key in keys:
        value = usage_details.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return 0.0


def grok_cost_usd(
    usage_details: Mapping[str, object],
    input_usd_per_token: float,
    output_usd_per_token: float,
) -> float:
    """Offline Grok cost: ``in_tokens*in_price + out_tokens*out_price`` (doc08:505).

    Prices are **injected**, never baked in (doc08:508 — the live unit prices / field shape are
    confirmed in CHECKLIST.md, not fixed in code). This is the arithmetic that unlocks the Grok
    comparison when Langfuse has no built-in xAI price; an independent re-impl of wo's contract.
    """
    in_tokens = _token_count(usage_details, _INPUT_TOKEN_KEYS)
    out_tokens = _token_count(usage_details, _OUTPUT_TOKEN_KEYS)
    return in_tokens * input_usd_per_token + out_tokens * output_usd_per_token


def generation_cost(
    generation: Mapping[str, object], *, grok_prices: tuple[float, float] | None = None
) -> float | None:
    """The effective cost of a generation: native ``cost`` if present, else the Grok offline fallback.

    doc08:502-506 — a registered custom model price gives a native ``cost``; absent that, xAI Grok
    falls back to ``usage_details × static price`` (``grok_prices=(in, out)``). Returns ``None`` when
    no cost can be determined (so the caller distinguishes "unpriceable" from a real zero).
    """
    native = generation.get("cost")
    if isinstance(native, (int, float)) and not isinstance(native, bool):
        return float(native)
    usage = generation.get("usage_details")
    if grok_prices is not None and isinstance(usage, Mapping):
        return grok_cost_usd(usage, grok_prices[0], grok_prices[1])
    return None


def cost_is_nonzero(
    generation: Mapping[str, object], *, grok_prices: tuple[float, float] | None = None
) -> bool:
    """② The generation has a strictly positive cost (natively or via the Grok offline fallback).

    doc13:520② / doc08:506 — all four providers must report ``cost > 0`` or the comparison breaks.
    """
    cost = generation_cost(generation, grok_prices=grok_prices)
    return cost is not None and cost > 0.0


def generations_of(trace: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
    """The generation observations of a single-cycle trace (empty list if none/malformed)."""
    gens = trace.get("generations")
    if isinstance(gens, Sequence) and not isinstance(gens, (str, bytes)):
        return [g for g in gens if isinstance(g, Mapping)]
    return []


def single_generation(trace: Mapping[str, object]) -> bool:
    """③ Exactly one generation per cycle trace — i.e. NO double generation (doc13:520③).

    A double generation (e.g. Hermes's own Langfuse plugin left enabled on top of the Bridge-owned
    trace, doc08:517) would double-count cost/latency and corrupt the comparison.
    """
    return len(generations_of(trace)) == 1


def managed_prompt_linked(generation: Mapping[str, object]) -> bool:
    """④ The generation carries a Langfuse managed-prompt link (doc13:520④).

    The exact field is live-confirmed (doc14:320 — out of doc14 scope, #88 owns), so accept any of
    the documented candidate keys with a truthy value.
    """
    return any(generation.get(k) for k in _PROMPT_LINK_KEYS)


def parse_sdk_version(version: str) -> tuple[int, int] | None:
    """Parse ``major.minor`` from a version string, or ``None`` if unparseable."""
    m = re.match(r"^\s*(\d+)\.(\d+)", version)
    return (int(m.group(1)), int(m.group(2))) if m else None


def sdk_version_ok(version: str) -> bool:
    """⑤ The installed langfuse SDK is v4 in range ``[4.7, 5.0)`` (doc13:514 — 4.7.1 OTEL base)."""
    parsed = parse_sdk_version(version)
    return parsed is not None and SDK_MIN <= parsed < SDK_MAX_EXCL


def evaluate_trace(
    trace: Mapping[str, object],
    *,
    expected_trace_id: str | None,
    grok_prices: tuple[float, float] | None = None,
) -> dict[str, object]:
    """Run the ①〜④ trace-level checks over one normalized single-cycle readback.

    Returns a verdict dict (booleans + the per-generation cost/prompt detail). ⑤ (SDK version) is
    process-global, not per-trace, so the live driver records it separately. A ``None`` value means
    "not determinable from this readback" (e.g. ④ when no managed prompt was used) — surfaced, not
    silently passed (docs-first: don't hide unknowns).
    """
    gens = generations_of(trace)
    observed_id = trace.get("trace_id")
    per_gen = [
        {
            "provider": g.get("provider"),
            "model": g.get("model"),
            "cost": generation_cost(g, grok_prices=grok_prices),
            "cost_nonzero": cost_is_nonzero(g, grok_prices=grok_prices),
            "managed_prompt": managed_prompt_linked(g),
        }
        for g in gens
    ]
    return {
        "observed_trace_id": observed_id,
        "check1_inbound_trace_id": check_inbound_trace_id(
            observed_id if isinstance(observed_id, str) else None, expected_trace_id
        ),
        "check3_single_generation": single_generation(trace),
        "check2_all_costs_nonzero": bool(per_gen) and all(g["cost_nonzero"] for g in per_gen),
        "check4_any_managed_prompt": bool(per_gen) and any(g["managed_prompt"] for g in per_gen),
        "generations": per_gen,
    }


# ══════════════════════════════════════════════════════════════════════════════════════════
# LIVE DRIVER — human gate (real keys + Hermes + Langfuse). Lazy SDK import, dev-only fail-closed.
# ══════════════════════════════════════════════════════════════════════════════════════════


def _parse_env_file(path: Path, key: str) -> str:
    """Value of ``key`` in a ``.env`` file, or "" if absent. The value is a secret — never logged."""
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


def load_secret(name: str, env_file: Path | None) -> str:
    """Resolve a secret: environment first, then *env_file* (config/dev/.env). Never logs the value."""
    from_env = os.environ.get(name)
    if from_env:
        return from_env
    if env_file is not None:
        return _parse_env_file(env_file, name)
    return ""


def assert_dev_only(base_url: str, allow_remote: bool) -> None:
    """Fail-closed dev guard (safety.md / environments.md): refuse prod / non-loopback gateway."""
    if os.environ.get("WAREHOUSE_ENV", "dev") == "prod":
        sys.exit("REFUSED: WAREHOUSE_ENV=prod — this spike is dev-only (safety.md).")
    host = urllib.parse.urlparse(base_url).hostname or ""
    if host not in LOOPBACK_HOSTS:
        if not allow_remote:
            sys.exit(
                f"REFUSED: non-loopback gateway host {host!r}. "
                "Use a loopback dev gateway, or pass --allow-remote (dev only)."
            )
        print(
            f"WARNING: --allow-remote → NON-loopback host {host!r}. Use DEV keys/gateway ONLY "
            "(safety.md/environments.md).",
            file=sys.stderr,
        )


def sdk_version() -> str | None:
    """The installed langfuse ``__version__``, or ``None`` if the SDK is absent (lazy import)."""
    try:
        import langfuse  # lazy/optional (pip extra)
    except ImportError:
        return None
    return getattr(langfuse, "__version__", None)


def make_traced_call(
    base_url: str, api_key: str, public_key: str, secret_key: str
) -> Callable[[str], dict]:
    """Build ``call(seed) -> readback`` that makes ONE Bridge-owned traced Hermes call (doc08:517).

    Mirrors the production trace ownership: a ``langfuse.openai`` OpenAI client (so the SDK owns the
    generation) pointed at Hermes (``base_url/v1``), with the trace id seeded from ``f"{run_id}:{gen_id}"``
    so the read-back trace must carry the deterministic id (①). The langfuse SDK + ``openai`` are lazily
    imported so ``--dry-run`` and the unit tests need neither. Returns a best-effort normalized readback;
    the exact fetch field shapes are UNVERIFIED (doc08:508), so ``fetch_trace`` parses defensively.
    """
    from langfuse import get_client  # lazy/optional
    from langfuse.openai import OpenAI  # lazy: Bridge-owned generation wrapper (doc08:517)

    # langfuse reads its own creds from env; set them for this process if passed explicitly.
    os.environ.setdefault(_ENV_LF_PUBLIC, public_key)
    os.environ.setdefault(_ENV_LF_SECRET, secret_key)
    client = get_client()
    oai = OpenAI(base_url=base_url.rstrip("/") + "/v1", api_key=api_key or "no-key")
    user_content = json.dumps(REPRESENTATIVE_SITUATION, ensure_ascii=False)

    def call(seed: str) -> dict:
        trace_id = client.create_trace_id(seed=seed)
        # Bind the cycle span to the seed-derived trace id (v4 trace_context) so the Bridge OWNS the
        # trace (Pattern A, doc13:517) — this is exactly what ① tests: the langfuse.openai generation
        # must land under THIS id (not a Hermes-created one). The exact binding API is UNVERIFIED
        # (doc08:508 / doc14:318) → honestly flagged; the human confirms in the Langfuse UI.
        with client.start_as_current_span(
            name="phase3_verify_cycle", trace_context={"trace_id": trace_id}
        ):
            client.update_current_trace(metadata={"trace_id": trace_id, "seed": seed})
            oai.chat.completions.create(
                model=HERMES_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
            )
        client.flush()
        return fetch_trace(client, trace_id)

    return call


def fetch_trace(client: object, trace_id: str) -> dict:
    """Best-effort read-back of a trace into the normalized structure ``evaluate_trace`` consumes.

    The v4 fetch API field paths are UNVERIFIED (doc08:508 / doc20 §8.4) — the human confirms in the
    Langfuse UI. We parse defensively across candidate shapes and record the raw payload so a wrong
    guess here never silently passes a check. Returns ``{"trace_id", "generations", "raw", "fetch_ok"}``.
    """
    try:
        normalized = normalize_trace_id(trace_id)
    except ValueError:
        normalized = trace_id
    out: dict = {"trace_id": normalized, "generations": [], "raw": None, "fetch_ok": False}
    api = getattr(client, "api", None)
    fetched = None
    for getter in ("get_trace", "trace"):
        fn = getattr(api, getter, None) if api is not None else None
        if callable(fn):
            try:
                fetched = fn(normalized)
                break
            except Exception as exc:  # noqa: BLE001 — fetch is best-effort; record and move on
                out["raw"] = f"fetch error via api.{getter}: {type(exc).__name__}: {exc}"[:300]
    if fetched is None:
        return out
    out["fetch_ok"] = True
    obs = getattr(fetched, "observations", None) or []
    for o in obs:
        otype = (getattr(o, "type", "") or "").upper()
        if otype != "GENERATION":
            continue
        usage = getattr(o, "usage_details", None) or getattr(o, "usage", None)
        out["generations"].append(
            {
                "provider": getattr(o, "model", None),  # provider inferred from model; UNVERIFIED
                "model": getattr(o, "model", None),
                "cost": _safe_cost(o),
                "usage_details": dict(usage) if isinstance(usage, Mapping) else None,
                "prompt": getattr(o, "prompt_name", None) or getattr(o, "prompt", None),
            }
        )
    return out


def _safe_cost(observation: object) -> object:
    """Pull a native cost off a fetched generation defensively (``cost`` / ``calculated_total_cost``)."""
    for attr in ("calculated_total_cost", "total_cost", "cost"):
        value = getattr(observation, attr, None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    cost_details = getattr(observation, "cost_details", None)
    if isinstance(cost_details, Mapping):
        total = cost_details.get("total")
        if isinstance(total, (int, float)) and not isinstance(total, bool):
            return total
    return None


def run_live(
    *,
    provider_label: str,
    base_url: str,
    api_key: str,
    public_key: str,
    secret_key: str,
    run_id: str,
    gen_id: int,
    grok_prices: tuple[float, float] | None,
) -> dict:
    """Drive one live provider verification cycle and return a secret-free report dict.

    One run = the gateway's CURRENT ``active_provider`` (doc13:175) — like spike/latency, the driver
    does NOT switch providers; the human sweeps all four (README). ⑤ SDK smoke + the seed-derived id
    are recorded even if the read-back fails, so a partial run still produces an honest RESULT row.
    """
    version = sdk_version()
    seed = seed_for(run_id, gen_id)
    expected_id = derive_trace_id(seed, _require_create_fn())
    report: dict = {
        "provider_label": provider_label,
        "gateway_host": urllib.parse.urlparse(base_url).hostname or "",
        "model": HERMES_MODEL,
        "run_id_present": bool(run_id),
        "seed": seed,
        "expected_trace_id": expected_id,
        "sdk_version": version,
        "check5_sdk_version_ok": bool(version) and sdk_version_ok(version),
        "grok_prices_injected": grok_prices is not None,
    }
    call = make_traced_call(base_url, api_key, public_key, secret_key)
    readback = call(seed)
    report["fetch_ok"] = readback.get("fetch_ok", False)
    report["raw_note"] = readback.get("raw")
    report["evaluation"] = evaluate_trace(
        readback, expected_trace_id=expected_id, grok_prices=grok_prices
    )
    return report


def _require_create_fn() -> Callable[..., str]:
    """The langfuse ``create_trace_id`` static helper (live only; raises if the SDK is absent)."""
    from langfuse import Langfuse  # lazy/optional

    return Langfuse.create_trace_id


def _grok_prices_arg(value: str | None) -> tuple[float, float] | None:
    """Parse ``--grok-prices in,out`` (USD per token) — injected, never baked (doc08:508)."""
    if not value:
        return None
    try:
        a, b = (float(x) for x in value.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("expected 'IN,OUT' USD-per-token floats") from None
    return (a, b)


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    default_env = repo_root / "config" / "dev" / ".env"
    parser = argparse.ArgumentParser(
        description="Langfuse Phase-3 real-trace verifier (doc13:520)."
    )
    parser.add_argument(
        "-p",
        "--provider",
        required=True,
        help="label of the gateway's CURRENT active_provider (anthropic|openai|google|xai)",
    )
    parser.add_argument("--base-url", default=os.environ.get("HERMES_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument(
        "--run-id",
        default=os.environ.get(_ENV_RUN_ID, ""),
        help=f"per-run id (default ${_ENV_RUN_ID}); seeds the deterministic trace id",
    )
    parser.add_argument("--gen-id", type=int, default=1, help="generation id for the trace seed")
    parser.add_argument(
        "--grok-prices",
        type=_grok_prices_arg,
        default=None,
        help="xAI offline-fallback prices 'IN,OUT' USD/token (see CHECKLIST.md; NOT baked in)",
    )
    parser.add_argument("--env-file", default=str(default_env))
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "out"))
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="permit a non-loopback gateway (dev only; off by default)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate config + print the plan WITHOUT any paid API call",
    )
    args = parser.parse_args(argv)

    assert_dev_only(args.base_url, args.allow_remote)
    env_file = Path(args.env_file) if args.env_file else None
    api_key = load_secret(_ENV_API_KEY, env_file)
    public_key = load_secret(_ENV_LF_PUBLIC, env_file)
    secret_key = load_secret(_ENV_LF_SECRET, env_file)
    version = sdk_version()

    if args.dry_run:
        print("=== DRY RUN (no API calls) ===")
        print(f"base_url        : {args.base_url}")
        print(f"provider_label  : {args.provider}")
        print(f"run_id          : {'present' if args.run_id else 'ABSENT'} (seeds trace id)")
        print(f"gen_id          : {args.gen_id}")
        print(f"API_SERVER_KEY  : {'present' if api_key else 'ABSENT'} (value never shown)")
        print(
            f"LANGFUSE keys   : pub={'present' if public_key else 'ABSENT'} "
            f"sec={'present' if secret_key else 'ABSENT'} (values never shown)"
        )
        print(
            f"langfuse SDK    : {version or 'NOT INSTALLED'}  "
            f"(⑤ ok={bool(version) and sdk_version_ok(version)})"
        )
        print(
            f"grok_prices     : {'injected' if args.grok_prices else 'NONE (Grok cost gate skipped)'}"
        )
        print(f"out_dir         : {args.out}")
        return 0

    missing = [
        n
        for n, v in (
            (_ENV_API_KEY, api_key),
            (_ENV_LF_PUBLIC, public_key),
            (_ENV_LF_SECRET, secret_key),
        )
        if not v
    ]
    if missing:
        sys.exit(
            f"REFUSED: missing secrets {missing} (env or --env-file) — live verify needs them."
        )
    if not args.run_id:
        sys.exit(f"REFUSED: no run id (${_ENV_RUN_ID} or --run-id) — cannot seed the trace id.")
    if version is None:
        sys.exit("REFUSED: langfuse SDK not installed — run: ./run.sh setup")

    report = run_live(
        provider_label=args.provider,
        base_url=args.base_url,
        api_key=api_key,
        public_key=public_key,
        secret_key=secret_key,
        run_id=args.run_id,
        gen_id=args.gen_id,
        grok_prices=args.grok_prices,
    )
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.provider}_{args.run_id}_{args.gen_id}.json"
    dup = 1
    while out_path.exists():
        out_path = out_dir / f"{args.provider}_{args.run_id}_{args.gen_id}_{dup}.json"
        dup += 1
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_live_summary(report)
    print(f"\nwrote {out_path}")
    print(
        "NOTE: confirm ①〜⑤ in the Langfuse UI and transcribe into RESULT.md "
        "(read-back field shapes are UNVERIFIED — doc08:508 / doc20 §8.4)."
    )
    return 0


def _print_live_summary(report: dict) -> None:
    ev = report.get("evaluation", {})
    print("\n=== Langfuse Phase-3 verify (one provider) ===")
    print(f"provider        : {report['provider_label']}   gateway: {report['gateway_host']}")
    print(f"expected trace  : {report['expected_trace_id']} (seed {report['seed']})")
    print(f"observed trace  : {ev.get('observed_trace_id')}   fetch_ok={report.get('fetch_ok')}")
    print(f"① inbound id    : {ev.get('check1_inbound_trace_id')}")
    print(
        f"② cost ≠0 (all) : {ev.get('check2_all_costs_nonzero')}  "
        f"{'(grok prices injected)' if report.get('grok_prices_injected') else ''}"
    )
    print(f"③ single gen    : {ev.get('check3_single_generation')}")
    print(f"④ managed prompt: {ev.get('check4_any_managed_prompt')}")
    print(f"⑤ SDK {report.get('sdk_version')}    : {report.get('check5_sdk_version_ok')}")
    if not report.get("fetch_ok"):
        print(
            "WARN: trace read-back FAILED/empty — ①②③④ are UNVERIFIED from the API; "
            f"confirm in the Langfuse UI (raw note: {report.get('raw_note')})."
        )


if __name__ == "__main__":
    raise SystemExit(main())
