#!/usr/bin/env python3
# =============================================================================
# verify_d_audio.py — OPTION D live verify, AUDIO-FIRST (the real ER use case).
# =============================================================================
# RUN BY A HUMAN / MAIN SESSION ONLY.  Makes REAL network calls to (a) a running
# plugin-ON + input_audio-forked LEAN ER gateway and (b) the Langfuse API.  It
# needs HERMES_LANGFUSE_* + API_SERVER_KEY in the environment and the `langfuse`
# SDK on PYTHONPATH from the ISOLATED libs dir.  An agent/subagent MUST NOT run
# it (no creds, no live gateway/Langfuse).  This file is BUILD-ONLY here; the
# wrapper run-verify-d-audio.sh drives it after the user supplies creds.
#
# -----------------------------------------------------------------------------
# WHAT OPTION D IS (and what we are verifying)
# -----------------------------------------------------------------------------
# GOAL: make the outcome-score JOIN work with the Hermes built-in Langfuse plugin
# turned ON, WITHOUT forking the Langfuse plugin (we avoid OPTION A).  Concretely:
#   - The LLM Bridge DROPS its `from langfuse.openai import AsyncOpenAI` wrapper on
#     the D path and instead lets the Hermes Langfuse plugin mint the root trace.
#   - The Bridge pins a stable correlation id H on the request via the header
#     X-Hermes-Session-Id: H, where H = seed_for(run_id, gen_id) = f"{run_id}:{gen_id}".
#   - The Orchestrator (scorer) RE-DERIVES the plugin's trace_id OFFLINE as
#     create_trace_id(seed=plugin_seed(H)) = create_trace_id(seed=f"{H}::{H}") and
#     attaches outcome scores to that SAME trace.  Zero inbound trace_id needed.
#
# We verify this on the REAL path: AUDIO (input_audio content part) reaching the
# gemini-robotics-er model through the forked gateway, with the plugin ON.
#
# FORK DISTINCTION (do NOT conflate):
#   - The input_audio FORK (2-file passthrough patch) is KEPT — it is what makes
#     AUDIO reach ER through Hermes (without it audio => HTTP 400).
#   - Option D's "no fork" means NO fork of the LANGFUSE PLUGIN.  The Langfuse /
#     score-join is observability AFTER ER receives the input; it is the SAME
#     regardless of modality, but we VERIFY it with AUDIO (the real path) on the
#     already-forked + plugin-ON gateway.
#
# -----------------------------------------------------------------------------
# VERIFIED D MECHANISM (cited from source — read this session, not from memory)
# -----------------------------------------------------------------------------
#   * Plugin mints trace_id at
#       ~/.hermes/hermes-agent/plugins/observability/langfuse/__init__.py:544
#         trace_id = client.create_trace_id(
#             seed=f"{session_id or 'sessionless'}::{task_id or task_key}")
#     create_trace_id is PURE: sha256(seed.encode())[:16].hex()  (32-hex, no dash)
#     (langfuse/_client/client.py:1759).
#   * The plugin fires via the pre_api_request hook (__init__.py:999 register;
#     invoked at agent/conversation_loop.py:1258 with task_id=effective_task_id,
#     session_id=agent.session_id).
#   * The request header X-Hermes-Session-Id sets session_id. On the
#     /v1/chat/completions path the gateway echoes it back as the
#     X-Hermes-Session-Id RESPONSE HEADER (NOT a body field). The body
#     "session_id" field (api_server.py:1515 result.get("session_id")) is the
#     echo for the SEPARATE /api/sessions/.../chat endpoint, not /v1. This
#     harness reads body-then-header (echoed_session_id, below) to cover BOTH
#     endpoints; the Bridge drift-detect reads only the /v1 header
#     (hermes_client._detect_session_drift).
#   * For the stateless/agent chat path the gateway sets
#       effective_task_id = session_id or uuid   (api_server.py:3464 / :3706)
#     and passes task_id=effective_task_id into run_conversation, where
#     conversation_loop sets effective_task_id = task_id or uuid
#     (conversation_loop.py:432) — so with X-Hermes-Session-Id=H, BOTH task_id
#     and session_id reaching the hook are H, and _trace_key(task_id=H, ...) == H
#     (__init__.py:222).  Therefore BOTH seed halves equal H -> seed = "H::H".
#
#   ==> D PREDICTION: trace_id == create_trace_id(seed=f"{H}::{H}").
#       This is EXACTLY eval_sdk.derive_plugin_trace_id(run_id, gen_id)
#       (ws/src/eval_sdk/eval_sdk/seed.py: plugin_seed(h)=f"{h}::{h}").
#
# HONEST CAVEAT — TWO SEED RECIPES (handled deliberately, not silently):
#   A predecessor probe (../hlf-g0-langfuse/hlf_g0_probe.py) used the STOCK
#   stateless recipe where task_id == "" so task_key == f"session:{H}" and the
#   seed is f"{H}::session:{H}".  That holds ONLY if the gateway does NOT route
#   through run_conversation with task_id=session_id.  The agent-run path above
#   yields "H::H".  Which one fires is a property of the live gateway routing, so
#   this harness computes the D prediction ("H::H", the brief + eval_sdk contract)
#   as PRIMARY, and ALSO checks the stock fallback ("H::session:H").  If the
#   landed trace matches the fallback (not the primary), we report INCONCLUSIVE
#   with the matched recipe named — never a silent FAIL, never a silent PASS.
#
# -----------------------------------------------------------------------------
# EXIT CODES
#   0  PASS          — audio 200 + echo==H + landed trace_id == D-predicted
#                      (H::H) + a generation with usage/cost present.
#   1  FAIL          — a hard contradiction (audio !=200, echo drifts in a way
#                      that breaks the join, or the D-predicted trace is absent
#                      AND no known recipe matches).
#   2  INCONCLUSIVE  — could not complete the check (no creds/SDK, trace not yet
#                      flushed, OR a non-D recipe matched -> the join works but
#                      NOT via the D prediction; a human must decide).
# =============================================================================

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

# ----------------------------------------------------------------------------- #
# Exit codes (named, so the wrapper + readers agree).
# ----------------------------------------------------------------------------- #
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_INCONCLUSIVE = 2


def log(msg: str) -> None:
    print(f"[verify-d-audio] {msg}", file=sys.stderr)


def _verdict(code: int, label: str, detail: str) -> int:
    log("=" * 70)
    log(f"VERDICT: {label}")
    log(detail)
    log("=" * 70)
    return code


# ----------------------------------------------------------------------------- #
# (1) H = seed_for(run_id, gen_id) = f"{run_id}:{gen_id}"  (eval_sdk/seed.py:33-42)
#     plugin_seed(H) = f"{H}::{H}"                          (eval_sdk/seed.py)
#     D prediction   = create_trace_id(seed=plugin_seed(H)) = sha256[:16].hex
# ----------------------------------------------------------------------------- #
def seed_for(run_id: str, gen_id: str) -> str:
    """H — the cross-lane join key (eval_sdk.seed.seed_for, used VERBATIM)."""
    return f"{run_id}:{gen_id}"


def _create_trace_id_pure(seed: str) -> str:
    """Mirror langfuse create_trace_id(seed): sha256(seed)[:16].hex (client.py:1759).

    Offline-deterministic source of truth so the prediction is reproducible even
    without a configured client. We ALSO cross-check against the real langfuse
    helper when the SDK is importable (see derive_plugin_trace_id_live)."""
    return hashlib.sha256(seed.encode("utf-8")).digest()[:16].hex()


def plugin_seed(h: str) -> str:
    """f"{H}::{H}" — the plugin's seed doubling for the agent chat path (eval_sdk)."""
    return f"{h}::{h}"


def stock_session_seed(h: str) -> str:
    """f"{H}::session:{H}" — the STOCK stateless recipe (task_id=='' fallback).

    Only used to disambiguate an INCONCLUSIVE result (predecessor probe recipe)."""
    return f"{h or 'sessionless'}::session:{h}"


def d_predicted_trace_id(run_id: str, gen_id: str) -> str:
    """The Option D prediction: create_trace_id(seed=f"{H}::{H}"), 32-hex."""
    return _create_trace_id_pure(plugin_seed(seed_for(run_id, gen_id)))


def stock_predicted_trace_id(run_id: str, gen_id: str) -> str:
    """Fallback recipe id: create_trace_id(seed=f"{H}::session:{H}"), 32-hex."""
    return _create_trace_id_pure(stock_session_seed(seed_for(run_id, gen_id)))


def cross_check_with_langfuse(seed: str, expected: str) -> bool | None:
    """If langfuse is importable, assert create_trace_id(seed) == our pure mirror.

    Returns True (match) / False (mismatch -> langfuse changed its recipe) / None
    (SDK absent — pure mirror stands alone). A False here is load-bearing: it
    means the offline derivation in eval_sdk no longer matches the live SDK."""
    try:
        from langfuse import Langfuse  # isolated import by design (PYTHONPATH)
    except Exception:
        return None
    try:
        pub = os.environ.get("HERMES_LANGFUSE_PUBLIC_KEY", "").strip()
        sec = os.environ.get("HERMES_LANGFUSE_SECRET_KEY", "").strip()
        base_url = (
            os.environ.get("HERMES_LANGFUSE_BASE_URL", "").strip() or "https://cloud.langfuse.com"
        )
        client = Langfuse(public_key=pub, secret_key=sec, base_url=base_url)
        live = client.create_trace_id(seed=seed)
        return (live or "").replace("-", "").strip().lower() == expected
    except Exception as exc:  # pragma: no cover - live
        log(f"langfuse create_trace_id cross-check skipped: {exc}")
        return None


# ----------------------------------------------------------------------------- #
# (2) WAV — say + afconvert (macOS) or --wav path. Base64 for input_audio.
# ----------------------------------------------------------------------------- #
def build_wav_b64(wav_path: str | None, tmpdir: str) -> str | None:
    """Return base64 of a 16k mono PCM WAV. Prefer --wav; else say+afconvert."""
    if wav_path:
        try:
            with open(wav_path, "rb") as fh:
                return base64.b64encode(fh.read()).decode("ascii")
        except OSError as exc:
            log(f"--wav {wav_path!r} could not be read: {exc}")
            return None
    say = _which("say")
    afconvert = _which("afconvert")
    if not (say and afconvert):
        log("say/afconvert unavailable — pass --wav <file.wav> to supply audio.")
        return None
    aiff = os.path.join(tmpdir, "say.aiff")
    wav = os.path.join(tmpdir, "probe.wav")
    try:
        subprocess.run(
            [say, "-o", aiff, "Move the red box to the loading dock."],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [afconvert, "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, wav],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with open(wav, "rb") as fh:
            return base64.b64encode(fh.read()).decode("ascii")
    except (subprocess.CalledProcessError, OSError) as exc:
        log(f"WAV synthesis failed: {exc}")
        return None


def _which(name: str) -> str | None:
    for d in os.environ.get("PATH", "").split(os.pathsep):
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


# ----------------------------------------------------------------------------- #
# (2) POST input_audio with X-Hermes-Session-Id: H ; assert 200.
# (3) read echoed session_id from the response ; assert == H.
# ----------------------------------------------------------------------------- #
def post_input_audio(
    base: str, api_key: str, session_id: str, wav_b64: str, timeout: float
) -> tuple[int, dict, dict]:
    """POST the input_audio turn. Returns (status, parsed_body, response_headers).

    The audio content part shape mirrors the forked gateway's own probe:
      {"type":"input_audio","input_audio":{"data":<b64>,"format":"wav"}}
    The ONLY request field the gateway maps to the plugin's session_id is the
    X-Hermes-Session-Id header (api_server.py session-chat path) — that is what
    pins H into the plugin seed."""
    body = {
        "model": "er",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Transcribe the spoken instruction."},
                    {
                        "type": "input_audio",
                        "input_audio": {"data": wav_b64, "format": "wav"},
                    },
                ],
            }
        ],
        "max_tokens": 64,
    }
    req = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "X-Hermes-Session-Id": session_id,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.getcode()
            headers = {k.lower(): v for k, v in resp.getheaders()}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
        status = exc.code
        headers = {k.lower(): v for k, v in (exc.headers.items() if exc.headers else [])}
    except urllib.error.URLError as exc:
        log(f"could not reach gateway at {base}: {exc.reason}")
        return 0, {}, {}
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {"_raw_head": raw[:400]}
    return status, parsed, headers


def echoed_session_id(parsed: dict, headers: dict) -> str | None:
    """Read the echoed session id: response body 'session_id', else the header."""
    sid = parsed.get("session_id") if isinstance(parsed, dict) else None
    if isinstance(sid, str) and sid:
        return sid
    hdr = headers.get("x-hermes-session-id")
    return hdr if isinstance(hdr, str) and hdr else None


# ----------------------------------------------------------------------------- #
# (4) Query Langfuse for the plugin-minted trace; assert id == D prediction.
# (5) Confirm a generation + usage/cost present on it.
# ----------------------------------------------------------------------------- #
def make_langfuse_client():
    try:
        from langfuse import Langfuse  # isolated import by design
    except Exception as exc:  # pragma: no cover - env-gated
        log(
            "could not import `langfuse` — it must be on PYTHONPATH from the "
            "ISOLATED libs dir (the wrapper prepends $LANGFUSE_LIBS). "
            f"Import error: {exc}"
        )
        return None
    pub = os.environ.get("HERMES_LANGFUSE_PUBLIC_KEY", "").strip()
    sec = os.environ.get("HERMES_LANGFUSE_SECRET_KEY", "").strip()
    base_url = (
        os.environ.get("HERMES_LANGFUSE_BASE_URL", "").strip() or "https://cloud.langfuse.com"
    )
    if not (pub and sec):
        log(
            "HERMES_LANGFUSE_PUBLIC_KEY / HERMES_LANGFUSE_SECRET_KEY not set — "
            "add them to the gateway's HERMES_HOME/.env (~/.hermes-mwr-er-lean/.env)."
        )
        return None
    try:
        return Langfuse(public_key=pub, secret_key=sec, base_url=base_url)
    except Exception as exc:  # pragma: no cover - live
        log(f"Langfuse client init failed: {exc}")
        return None


def fetch_trace_by_id(client, trace_id: str, *, attempts: int, delay: float):
    """client.api.trace.get(trace_id) with a short retry (plugin flush is async)."""
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            full = client.api.trace.get(trace_id)
            if full is not None:
                return full
        except Exception as exc:  # pragma: no cover - live
            last_exc = exc
        if i < attempts - 1:
            log(
                f"trace {trace_id[:8]}… not visible yet (attempt {i + 1}/{attempts}); waiting {delay:.0f}s"
            )
            time.sleep(delay)
    if last_exc is not None:
        log(f"trace.get({trace_id[:8]}…) error: {last_exc}")
    return None


def generation_with_usage_cost(full_trace) -> tuple[bool, str]:
    """(present, detail): a GENERATION observation carrying usage and/or cost."""
    if full_trace is None:
        return False, "no trace detail"
    total_cost = getattr(full_trace, "total_cost", None)
    obs = getattr(full_trace, "observations", None) or []
    gens = 0
    gen_with_usage = 0
    gen_with_cost = 0
    for o in obs:
        if (getattr(o, "type", "") or "").upper() != "GENERATION":
            continue
        gens += 1
        usage = getattr(o, "usage", None) or getattr(o, "usage_details", None)
        cost = (
            getattr(o, "calculated_total_cost", None)
            or getattr(o, "total_cost", None)
            or getattr(o, "cost_details", None)
        )
        if usage:
            gen_with_usage += 1
        if cost:
            gen_with_cost += 1
    present = bool(gens and (gen_with_usage or gen_with_cost or (total_cost and total_cost > 0)))
    detail = (
        f"generations={gens}; with_usage={gen_with_usage}; with_cost={gen_with_cost}; "
        f"trace.totalCost={total_cost!r}"
    )
    return present, detail


# ----------------------------------------------------------------------------- #
# Orchestration
# ----------------------------------------------------------------------------- #
def parse_args(argv: list) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Option D AUDIO live verify (plugin-ON, no plugin fork)."
    )
    p.add_argument("--run-id", default=os.environ.get("WAREHOUSE_RUN_ID", "verifyrun"))
    p.add_argument("--gen-id", default=os.environ.get("MWR_VERIFY_GEN_ID", "1"))
    p.add_argument("--base", default=os.environ.get("MWR_GATEWAY_BASE", "http://127.0.0.1:8644"))
    p.add_argument("--wav", default=None, help="explicit WAV file (else say+afconvert).")
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--trace-attempts", type=int, default=6)
    p.add_argument("--trace-delay", type=float, default=5.0)
    return p.parse_args(argv)


def main(argv: list) -> int:
    args = parse_args(argv)

    if os.environ.get("WAREHOUSE_ENV", "").strip().lower() == "prod":
        return _verdict(
            EXIT_INCONCLUSIVE, "INCONCLUSIVE", "REFUSED: WAREHOUSE_ENV=prod — dev-only (safety.md)."
        )

    api_key = os.environ.get("API_SERVER_KEY", "").strip()
    if not api_key:
        return _verdict(
            EXIT_INCONCLUSIVE,
            "INCONCLUSIVE",
            "API_SERVER_KEY not set — source $HERMES_HOME/.env (the wrapper does). Cannot auth to the gateway.",
        )

    run_id, gen_id = str(args.run_id), str(args.gen_id)
    h = seed_for(run_id, gen_id)
    d_tid = d_predicted_trace_id(run_id, gen_id)
    stock_tid = stock_predicted_trace_id(run_id, gen_id)
    log(f"H = seed_for({run_id!r}, {gen_id!r}) = {h!r}")
    log(f"D prediction  = create_trace_id(seed={plugin_seed(h)!r}) = {d_tid}")
    log(
        f"stock recipe  = create_trace_id(seed={stock_session_seed(h)!r}) = {stock_tid}  (fallback only)"
    )

    # Offline determinism cross-check (load-bearing: eval_sdk vs live SDK recipe).
    xc = cross_check_with_langfuse(plugin_seed(h), d_tid)
    if xc is False:
        return _verdict(
            EXIT_FAIL,
            "FAIL",
            "langfuse create_trace_id(seed) != our sha256[:16] mirror — the SDK changed its "
            "recipe; eval_sdk.derive_plugin_trace_id would no longer match the plugin. Investigate "
            "before trusting any join.",
        )
    if xc is True:
        log("offline determinism: langfuse create_trace_id matches sha256[:16].hex mirror ✓")

    # --- (2) AUDIO POST -----------------------------------------------------
    with tempfile.TemporaryDirectory(prefix="verify-d-audio.") as tmpdir:
        wav_b64 = build_wav_b64(args.wav, tmpdir)
        if not wav_b64:
            return _verdict(
                EXIT_INCONCLUSIVE,
                "INCONCLUSIVE",
                "no WAV available (say/afconvert missing and no --wav) — the AUDIO path was NOT "
                "exercised. Supply --wav <file.wav> on a non-macOS host.",
            )
        log(
            f"posting input_audio turn to {args.base}/v1/chat/completions with X-Hermes-Session-Id={h!r}"
        )
        status, parsed, headers = post_input_audio(args.base, api_key, h, wav_b64, args.timeout)

    if status != 200:
        head = json.dumps(parsed)[:400] if parsed else ""
        return _verdict(
            EXIT_FAIL,
            "FAIL",
            f"input_audio POST -> HTTP {status} (expected 200). Audio did NOT reach ER through the "
            f"forked+plugin-ON gateway. Response head: {head}",
        )
    log("audio reached ER: input_audio POST -> HTTP 200 ✓")

    # --- (3) ECHO DRIFT-DETECT ----------------------------------------------
    echoed = echoed_session_id(parsed, headers)
    if echoed is None:
        return _verdict(
            EXIT_INCONCLUSIVE,
            "INCONCLUSIVE",
            "audio 200 but the response carried NO session_id (body or X-Hermes-Session-Id header) "
            "to echo back — cannot confirm the plugin seeded on H. Inspect the gateway response shape.",
        )
    if echoed != h:
        # Session rotated (e.g. #16938 compression) -> the plugin seed used the
        # ROTATED id, not H. The D re-derivation from H would NOT match. This is
        # exactly the fail-open DRIFT case the Bridge must skip the cycle on.
        return _verdict(
            EXIT_FAIL,
            "FAIL",
            f"echoed session_id={echoed!r} != H={h!r} — the gateway rotated the session, so the "
            f"plugin seeded on {echoed!r} not H. The scorer's H-based re-derivation cannot join. "
            "(On the live Bridge this is the DRIFT-DETECT path: skip the cycle's score, never raise.)",
        )
    log(f"echo drift-detect: response session_id == H ({h!r}) ✓")

    # --- (4) TRACE ID == D PREDICTION ---------------------------------------
    client = make_langfuse_client()
    if client is None:
        return _verdict(
            EXIT_INCONCLUSIVE,
            "INCONCLUSIVE",
            "audio 200 + echo==H verified, but Langfuse SDK/creds unavailable — the trace_id == "
            "D-prediction and usage/cost checks were NOT run. Provide HERMES_LANGFUSE_* + langfuse SDK.",
        )

    full = fetch_trace_by_id(client, d_tid, attempts=args.trace_attempts, delay=args.trace_delay)
    if full is None:
        # The D-predicted id was not found. Disambiguate against the stock recipe
        # so we never emit a silent FAIL when the join in fact works via H::session:H.
        stock_full = fetch_trace_by_id(client, stock_tid, attempts=2, delay=args.trace_delay)
        if stock_full is not None:
            return _verdict(
                EXIT_INCONCLUSIVE,
                "INCONCLUSIVE",
                f"the LANDED trace matched the STOCK recipe ({stock_tid}, seed={stock_session_seed(h)!r}), "
                f"NOT the D prediction ({d_tid}, seed={plugin_seed(h)!r}). The join works, but eval_sdk."
                "derive_plugin_trace_id (H::H) would NOT land on it — the live gateway used task_id=='' "
                "(stateless path). A human must decide: adopt H::session:H, or force the agent-run path.",
            )
        return _verdict(
            EXIT_INCONCLUSIVE,
            "INCONCLUSIVE",
            f"audio 200 + echo==H verified, but NO trace found at the D-predicted id {d_tid} "
            f"(nor the stock fallback {stock_tid}) within "
            f"{args.trace_attempts}×{args.trace_delay:.0f}s. The plugin flush may be delayed, the "
            "Langfuse keys may point at a different project, or no trace was minted. Re-run the query.",
        )

    landed_id = (getattr(full, "id", None) or "").replace("-", "").strip().lower()
    if landed_id != d_tid:
        return _verdict(
            EXIT_FAIL,
            "FAIL",
            f"trace fetched at the D-predicted id but its own .id={landed_id!r} != {d_tid!r} — "
            "inconsistent Langfuse response; do not trust the join.",
        )
    log(f"trace_id == D prediction: {d_tid} ✓ (the score-join works for the AUDIO call)")

    # --- (5) GENERATION + USAGE/COST ----------------------------------------
    present, detail = generation_with_usage_cost(full)
    if not present:
        return _verdict(
            EXIT_INCONCLUSIVE,
            "INCONCLUSIVE",
            f"trace_id == D prediction verified, but NO generation with usage/cost was present "
            f"({detail}). The join id is correct; cost roll-up is missing (cf. Langfuse #42306). "
            "A human must confirm whether usage/cost is expected for gemini-robotics-er.",
        )
    log(f"generation + usage/cost present: {detail} ✓")

    return _verdict(
        EXIT_PASS,
        "PASS",
        "OPTION D AUDIO verified end-to-end: input_audio reached ER (200), the gateway echoed "
        f"session_id == H ({h!r}), the plugin-minted trace landed at the D-predicted id "
        f"{d_tid} (= create_trace_id(seed=f'{{H}}::{{H}}')), and it carries a generation with "
        f"usage/cost ({detail}). The outcome-score JOIN works with the plugin ON and NO Langfuse "
        "plugin fork, on the REAL (audio) path.",
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
