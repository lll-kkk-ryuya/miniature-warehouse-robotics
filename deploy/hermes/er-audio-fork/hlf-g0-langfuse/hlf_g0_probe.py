#!/usr/bin/env python3
# =============================================================================
# hlf_g0_probe.py — HLF-G0 live gate: does the Hermes built-in Langfuse plugin
#                   honor an INBOUND trace_id supplied in the request?
# =============================================================================
# RUN BY A HUMAN / MAIN SESSION ONLY.  This script makes REAL network calls to
# (a) a running plugin-ON forked ER gateway and (b) the Langfuse API.  It needs
# HERMES_LANGFUSE_* + API_SERVER_KEY creds in the environment.  An agent/subagent
# MUST NOT run it (no creds, no live gateway/Langfuse).  Build-only; the live run
# is sequential and done after the user supplies HERMES_LANGFUSE_* creds.
#
# -----------------------------------------------------------------------------
# WHY THIS GATE EXISTS (Pattern B instinct)
# -----------------------------------------------------------------------------
# Goal: decide whether the LLM Bridge can DROP its `from langfuse.openai import
# AsyncOpenAI` wrapper and instead turn the Hermes built-in Langfuse plugin ON.
# The deciding factor is HLF-G0:
#
#   Does the Hermes Langfuse plugin honor an INBOUND trace_id (passed by the
#   Bridge in the request) so the Warehouse Orchestrator (#6) can later attach
#   outcome scores (SR / SPL / collision) to the SAME trace?
#
#     PASS  -> plugin-ON + no-wrapper is clean (Bridge controls the trace_id).
#     FAIL  -> the plugin mints its own trace_id; a fork tweak or the wrapper is
#              needed (OR the Orchestrator must re-derive the plugin's trace_id;
#              see the "DETERMINISTIC SEED" finding below).
#
# -----------------------------------------------------------------------------
# WHAT THE PLUGIN SOURCE ACTUALLY DOES  (verified this session — read the source)
#   ~/.hermes/hermes-agent/plugins/observability/langfuse/__init__.py
# -----------------------------------------------------------------------------
# The plugin's trace_id is ALWAYS minted server-side. It is NEVER read from the
# request. The only place a trace_id is created is `_start_root_trace` (l.544):
#
#     trace_id = client.create_trace_id(
#         seed=f"{session_id or 'sessionless'}::{task_id or task_key}")
#
# There is NO code path in the plugin that reads `trace_id`, `metadata.trace_id`,
# `langfuse_trace_id`, or any inbound correlation id from the request body or
# headers. The hooks receive only: task_id, session_id, platform, provider,
# model, api_mode, messages, usage, etc. — all gateway-derived (NOT a request
# `metadata` field). Likewise the gateway `/v1/chat/completions` handler
# (~/.hermes/hermes-agent/gateway/platforms/api_server.py:1671-1785) reads ONLY:
# messages, model, stream, body.id/body.session_id, and the headers
# X-Hermes-Session-Id / X-Hermes-Session-Key. It does NOT read any request
# `metadata` object. So a Langfuse/OpenAI-style {"metadata":{"trace_id":...}}
# field in the body is silently ignored.
#
#   ==> STRUCTURAL VERDICT (from source, before any live run): stock HLF-G0 FAIL.
#       The plugin will mint its own trace_id; the inbound one is dropped.
#
# This script STILL runs live because it (1) proves audio still works with the
# plugin ON (HTTP 200), (2) EMPIRICALLY confirms the FAIL on the real stack,
# (3) checks whether usage/cost got attached (Issue #42306), and (4) tests the
# one genuine workaround knob below.
#
# -----------------------------------------------------------------------------
# THE ONE WORKAROUND KNOB — DETERMINISTIC SEED via X-Hermes-Session-Id
# -----------------------------------------------------------------------------
# `create_trace_id(seed)` is DETERMINISTIC (verified in langfuse 4.9.0 source,
# _client/client.py:1759):  sha256(seed.encode("utf-8")).digest()[:16].hex()
#
# For /v1/chat/completions the gateway derives (VERIFIED by source trace,
# workflow w3ub33nlh / claim C4 — supersedes the earlier task_id="" guess):
#     session_id = X-Hermes-Session-Id  (if the caller sends it; else a content
#                                         fingerprint, api_server.py:1743-1764)
#     effective_task_id = session_id or uuid4  (api_server.py:3464) -> task_id=H
# Because task_id is NON-empty (== session_id == H), the plugin's _trace_key
# "session:" fallback (__init__.py:226) is NEVER reached, so the plugin's seed
# (__init__.py:544, f"{session_id or 'sessionless'}::{task_id or task_key}")
# becomes:
#     seed = f"{session_id}::{session_id}"   (== "H::H"; the older
#            "H::session:H" hypothesis assumed task_id="" and is WRONG here)
#
# => If the Bridge sends a stable X-Hermes-Session-Id, the Orchestrator can
#    RE-DERIVE the plugin's trace_id OFFLINE (no inbound trace_id needed):
#       expected = create_trace_id(seed=f"{sid}::{sid}")
#    This is NOT "honoring an inbound trace_id" (so HLF-G0 strictly = FAIL), but
#    it is a viable correlation path for #6. This script reports whether the
#    observed trace_id matches this re-derivation (WORKAROUND-VIABLE) so the
#    main session can decide: fork tweak vs. wrapper vs. deterministic-seed.
#
#    Caveat (honest): this seed couples to the plugin's INTERNAL seed recipe
#    (session_id + task_key shape). That recipe is plugin-version-specific and
#    could change upstream. Treat WORKAROUND-VIABLE as "works on THIS clone",
#    not a stable contract.
#
# -----------------------------------------------------------------------------
# ISOLATION (ABSOLUTE — mirrors run-er-gateway.sh)
# -----------------------------------------------------------------------------
#  - NEVER import langfuse from / install into the personal Hermes venv
#    (~/.hermes/hermes-agent/venv) or touch ~/.hermes. This script only READS
#    plugin source there; it imports langfuse from an ISOLATED dir on sys.path
#    (the wrapper run-hlf-g0.sh prepends $LANGFUSE_LIBS via PYTHONPATH).
#  - Creds (HERMES_LANGFUSE_* / API_SERVER_KEY) come from the ENVIRONMENT only
#    (the wrapper `source`s the gateway's HERMES_HOME/.env). This script NEVER
#    prints any secret value.
# =============================================================================
"""HLF-G0 live probe. Human/main-session only. See module docstring."""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any

# --- Markers used in the final verdict line (greppable by the wrapper/README) --
PASS = "HLF-G0 PASS"  # plugin honored an INBOUND trace_id
FAIL = "HLF-G0 FAIL"  # plugin minted its own trace_id (expected, from source)
INCONCLUSIVE = "HLF-G0 INCONCLUSIVE"  # no generation found / could not decide

SPIKE_SESSION_ID = "hlf-g0-spike"
SPIKE_TAG = "hlf-g0-spike"


def log(msg: str) -> None:
    print(f"[hlf-g0] {msg}", file=sys.stderr, flush=True)


def die(msg: str, code: int = 1) -> None:
    log(f"ERROR: {msg}")
    sys.exit(code)


# --------------------------------------------------------------------------- #
# Inbound trace_id construction                                               #
# --------------------------------------------------------------------------- #
def inbound_trace_id(run_id: str, gen_id: str) -> str:
    """Deterministic 32-hex inbound trace_id from run_id:gen_id.

    This is the id the Bridge WOULD pass and the Orchestrator WOULD score
    against. Matches the langfuse convention (32 lowercase hex / 16 bytes) so a
    PASS would be unambiguous. We mirror langfuse.create_trace_id(seed=...):
    sha256(seed)[:16].hex() — so this id is itself reproducible offline.
    """
    seed = f"{run_id}:{gen_id}"
    return hashlib.sha256(seed.encode("utf-8")).digest()[:16].hex()


def plugin_rederived_trace_id(session_id: str) -> str:
    """Re-derive the trace_id the stock plugin mints for this session.

    Verified by source trace (workflow w3ub33nlh, claim C4): the
    /v1/chat/completions path sets effective_task_id = session_id or uuid4
    (api_server.py:3464), so with a NON-empty X-Hermes-Session-Id=H the plugin
    sees task_id=H and the _trace_key "session:" fallback (__init__.py:226) is
    NEVER reached. Plugin seed (__init__.py:544,
    f"{session_id or 'sessionless'}::{task_id or task_key}") == f"{H}::{H}".
    The earlier "H::session:H" recipe assumed task_id=="" and is WRONG for this
    path (it produced a false-negative WORKAROUND verdict).
      trace_id == sha256(seed)[:16].hex()   (langfuse create_trace_id)
    """
    h = session_id or "sessionless"
    seed = f"{h}::{h}"
    return hashlib.sha256(seed.encode("utf-8")).digest()[:16].hex()


# --------------------------------------------------------------------------- #
# WAV input                                                                    #
# --------------------------------------------------------------------------- #
def build_wav(path_arg: str | None, tmpdir: str) -> str | None:
    """Return base64 wav. Prefer an explicit --wav path; else say+afconvert.

    Returns None if neither is available (audio leg is then SKIPPED — but the
    HLF-G0 trace check can still proceed with a text-only request, which is
    enough to answer the inbound-trace_id question).
    """
    if path_arg:
        if not os.path.isfile(path_arg):
            die(f"--wav path does not exist: {path_arg}")
        with open(path_arg, "rb") as fh:
            return base64.b64encode(fh.read()).decode("ascii")

    say = _which("say")
    afconvert = _which("afconvert")
    if not (say and afconvert):
        log("say/afconvert unavailable and no --wav given; audio leg will be SKIPPED.")
        return None

    aiff = os.path.join(tmpdir, "say.aiff")
    wav = os.path.join(tmpdir, "probe.wav")
    try:
        subprocess.run(
            [say, "-o", aiff, "Move the red box to the loading dock."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [afconvert, "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", aiff, wav],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        log(f"WAV generation failed ({exc}); audio leg will be SKIPPED.")
        return None
    with open(wav, "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


def _which(name: str) -> str | None:
    from shutil import which

    return which(name)


# --------------------------------------------------------------------------- #
# Gateway POST                                                                 #
# --------------------------------------------------------------------------- #
def post_chat(
    base: str, api_key: str, wav_b64: str | None, session_id: str, inbound_tid: str, timeout: float
) -> tuple[int, dict]:
    """POST input_audio (or text) to /v1/chat/completions on the plugin-ON gateway.

    We attach the inbound trace_id THREE ways to maximize the chance the plugin
    or gateway picks it up, AND to make the negative result airtight:
      1. body.metadata.trace_id           (Langfuse/OpenAI metadata convention)
      2. body.metadata.langfuse_session_id + body.metadata.tags (spike grouping)
      3. X-Hermes-Session-Id header        (the ONE field the gateway DOES read;
                                            drives the plugin's deterministic seed)
    Per source review, (1)/(2) are ignored by the stock gateway/plugin; only (3)
    influences the minted trace_id (via the seed). We send all three so the live
    result distinguishes: PASS (1/2 honored) vs WORKAROUND-VIABLE (3 seed match)
    vs hard FAIL (random id).
    """
    user_content: list[dict[str, Any]] = [
        {"type": "text", "text": "Transcribe and act on the spoken instruction."}
    ]
    if wav_b64 is not None:
        user_content.append(
            {"type": "input_audio", "input_audio": {"data": wav_b64, "format": "wav"}}
        )

    body = {
        "model": "er",
        "messages": [{"role": "user", "content": user_content}],
        "max_tokens": 64,
        # (1)+(2): documented OpenAI/Langfuse metadata conventions for an inbound
        # trace_id / session grouping. NOTE (verified by source review): the stock
        # Hermes gateway does NOT read request `metadata`; this is here so a PASS
        # would be meaningful and a FAIL is airtight.
        "metadata": {
            "trace_id": inbound_tid,
            "langfuse_trace_id": inbound_tid,
            "langfuse_session_id": SPIKE_SESSION_ID,
            "session_id": SPIKE_SESSION_ID,
            "tags": [SPIKE_TAG],
        },
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/v1/chat/completions",
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            # (3): the ONLY request field the gateway actually maps to the
            # plugin's session_id (api_server.py:1743). Drives the deterministic
            # seed -> lets the Orchestrator re-derive the trace_id offline.
            "X-Hermes-Session-Id": session_id,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.getcode()
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
        status = exc.code
    except urllib.error.URLError as exc:
        die(f"could not reach gateway at {base}: {exc.reason}")
        return 0, {}  # unreachable (die exits)
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {"_raw_head": raw[:400]}
    return status, parsed


# --------------------------------------------------------------------------- #
# Langfuse query                                                               #
# --------------------------------------------------------------------------- #
def import_langfuse():
    try:
        from langfuse import Langfuse  # noqa: WPS433 (isolated import by design)

        return Langfuse
    except Exception as exc:  # pragma: no cover - env-gated
        die(
            "could not import `langfuse` — it must be on PYTHONPATH from the "
            "ISOLATED libs dir (run via run-hlf-g0.sh, which prepends "
            f"$LANGFUSE_LIBS). Import error: {exc}"
        )


def make_langfuse_client(langfuse_cls):
    pub = os.environ.get("HERMES_LANGFUSE_PUBLIC_KEY", "").strip()
    sec = os.environ.get("HERMES_LANGFUSE_SECRET_KEY", "").strip()
    base_url = (
        os.environ.get("HERMES_LANGFUSE_BASE_URL", "").strip() or "https://cloud.langfuse.com"
    )
    if not (pub and sec):
        die(
            "HERMES_LANGFUSE_PUBLIC_KEY / HERMES_LANGFUSE_SECRET_KEY not set in "
            "env. Add them to the gateway's HERMES_HOME/.env (default "
            "~/.hermes-mwr-er-lean/.env) and source it (run-hlf-g0.sh does)."
        )
    # Construct exactly as the plugin would (same keys/base_url), so we read the
    # same project the plugin wrote to. Never print the values.
    return langfuse_cls(public_key=pub, secret_key=sec, base_url=base_url)


def fetch_spike_traces(client, *, since_ms: int) -> list:
    """Find traces the plugin wrote for this spike.

    Strategy (most-specific first):
      1. by session_id == hlf-g0-spike (set via X-Hermes-Session-Id)
      2. by tag        == hlf-g0-spike (if the plugin propagated request tags)
      3. by recency    (created after the POST) as a last resort
    Returns a list of trace summary objects (id, sessionId, tags, timestamp).
    """
    found: list = []
    api = client.api.trace

    # (1) session_id
    try:
        page = api.list(session_id=SPIKE_SESSION_ID, limit=20)
        found = list(getattr(page, "data", []) or [])
        if found:
            log(f"Langfuse: {len(found)} trace(s) matched session_id={SPIKE_SESSION_ID!r}")
            return found
    except Exception as exc:  # pragma: no cover - live
        log(f"trace.list(session_id) failed: {exc}")

    # (2) tag
    try:
        page = api.list(tags=[SPIKE_TAG], limit=20)
        found = list(getattr(page, "data", []) or [])
        if found:
            log(f"Langfuse: {len(found)} trace(s) matched tag={SPIKE_TAG!r}")
            return found
    except Exception as exc:  # pragma: no cover - live
        log(f"trace.list(tags) failed: {exc}")

    # (3) recency fallback — only surfaces traces created during this run window.
    try:
        page = api.list(limit=20)
        recent = []
        for t in getattr(page, "data", []) or []:
            ts = getattr(t, "timestamp", None)
            ms = _to_ms(ts)
            if ms is None or ms >= since_ms - 5000:
                recent.append(t)
        if recent:
            log(
                f"Langfuse: {len(recent)} recent trace(s) (no session/tag match — "
                "plugin did not propagate spike session/tag; inspecting by recency)"
            )
        return recent
    except Exception as exc:  # pragma: no cover - live
        log(f"trace.list(recent) failed: {exc}")
        return []


def _to_ms(ts: Any) -> int | None:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return int(ts * (1000 if ts < 1e12 else 1))
    # datetime
    epoch = getattr(ts, "timestamp", None)
    if callable(epoch):
        try:
            return int(ts.timestamp() * 1000)
        except Exception:
            return None
    return None


def trace_full(client, trace_id: str):
    try:
        return client.api.trace.get(trace_id)
    except Exception as exc:  # pragma: no cover - live
        log(f"trace.get({trace_id[:8]}...) failed: {exc}")
        return None


def usage_cost_present(full_trace) -> tuple[bool, str]:
    """Return (present, detail) for usage/cost on the trace's generations.

    Issue #42306 context: a generation must carry usage_details / cost_details
    for the dashboard to roll up tokens & cost. We check both trace-level
    totalCost and per-observation usage/cost.
    """
    if full_trace is None:
        return False, "no trace detail"
    total_cost = getattr(full_trace, "total_cost", None)
    obs = getattr(full_trace, "observations", None) or []
    gen_with_usage = 0
    gen_with_cost = 0
    for o in obs:
        otype = (getattr(o, "type", "") or "").upper()
        if otype != "GENERATION":
            continue
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
    present = bool(gen_with_usage or (total_cost and total_cost > 0))
    detail = (
        f"trace.totalCost={total_cost!r}; generations_with_usage={gen_with_usage}; "
        f"generations_with_cost={gen_with_cost}"
    )
    return present, detail


# --------------------------------------------------------------------------- #
# Verdict                                                                      #
# --------------------------------------------------------------------------- #
def decide_verdict(traces, inbound_tid: str, rederived_tid: str) -> tuple[str, str]:
    """Classify the observed trace_id(s) against the inbound and re-derived ids."""
    if not traces:
        return INCONCLUSIVE, (
            "No generation/trace appeared for this spike. Either the plugin is "
            "inert (langfuse missing in the GATEWAY interpreter, or plugin not "
            "enabled), creds point at a different project, or flush lagged. "
            "Re-check: gateway launched with langfuse on PYTHONPATH + plugin "
            "enabled; same HERMES_LANGFUSE_* project."
        )
    observed_ids = [getattr(t, "id", "") for t in traces]
    if inbound_tid in observed_ids:
        return PASS, (
            f"A trace with the INBOUND trace_id ({inbound_tid}) exists -> the "
            "plugin HONORED the inbound trace_id. Pattern B (plugin-ON + "
            "no-wrapper) is clean: the Orchestrator can score the same trace."
        )
    if rederived_tid in observed_ids:
        return FAIL, (
            f"The plugin minted its OWN trace_id ({rederived_tid}), NOT the "
            f"inbound one ({inbound_tid}). HLF-G0 strictly = FAIL.  BUT the "
            "minted id == create_trace_id(seed=f'{sid}::{sid}') for "
            f"sid={SPIKE_SESSION_ID!r} -> WORKAROUND-VIABLE: the Orchestrator "
            "can RE-DERIVE the plugin's trace_id offline from a stable "
            "X-Hermes-Session-Id (no inbound trace_id, no wrapper). Caveat: "
            "this couples to the plugin's internal seed recipe (version-specific)."
        )
    return FAIL, (
        f"A trace appeared but with an id that is NEITHER the inbound "
        f"({inbound_tid}) NOR the session-seed re-derivation ({rederived_tid}). "
        f"Observed: {observed_ids}. HLF-G0 = FAIL and the deterministic-seed "
        "workaround does NOT hold for this plugin/session shape -> a fork tweak "
        "or the langfuse.openai wrapper is required for #6 correlation."
    )


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(
        description="HLF-G0 live probe (HUMAN/main-session only; needs creds + "
        "running plugin-ON gateway + Langfuse).",
    )
    ap.add_argument(
        "--base",
        default=os.environ.get("HLF_G0_BASE", ""),
        help="Gateway base URL, e.g. http://127.0.0.1:8644 (default $HLF_G0_BASE).",
    )
    ap.add_argument(
        "--wav",
        default=None,
        help="Path to a wav file. Default: generate via say+afconvert "
        "(macOS); if unavailable the audio leg is SKIPPED.",
    )
    ap.add_argument(
        "--run-id",
        default=os.environ.get("HLF_G0_RUN_ID", "hlfg0run"),
        help="run_id half of the inbound trace seed (deterministic).",
    )
    ap.add_argument(
        "--gen-id",
        default=os.environ.get("HLF_G0_GEN_ID", "gen0001"),
        help="gen_id half of the inbound trace seed (deterministic).",
    )
    ap.add_argument(
        "--session-id",
        default=SPIKE_SESSION_ID,
        help=f"X-Hermes-Session-Id to send (default {SPIKE_SESSION_ID!r}).",
    )
    ap.add_argument(
        "--settle",
        type=float,
        default=8.0,
        help="Seconds to wait for the plugin's async flush before querying Langfuse (default 8).",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        help="HTTP timeout for the gateway POST (default 90).",
    )
    args = ap.parse_args()

    if not args.base:
        die(
            "missing --base (or $HLF_G0_BASE): the plugin-ON gateway base URL, "
            "e.g. http://127.0.0.1:8644"
        )
    base = args.base.rstrip("/")

    api_key = os.environ.get("API_SERVER_KEY", "").strip()
    if not api_key:
        die(
            "API_SERVER_KEY not set in env (the gateway bearer token). Source the "
            "gateway HERMES_HOME/.env (run-hlf-g0.sh does)."
        )

    inbound_tid = inbound_trace_id(args.run_id, args.gen_id)
    rederived_tid = plugin_rederived_trace_id(args.session_id)
    log(f"inbound trace_id (run:gen seed) = {inbound_tid}")
    log(f"plugin-seed re-derived trace_id (sid={args.session_id!r}) = {rederived_tid}")

    # --- import langfuse FIRST (fail fast if libs not isolated) ---------------
    langfuse_cls = import_langfuse()
    client = make_langfuse_client(langfuse_cls)

    # --- (1) POST + assert 200 (audio still works with plugin ON) -------------
    with tempfile.TemporaryDirectory(prefix="hlf-g0.") as tmpdir:
        wav_b64 = build_wav(args.wav, tmpdir)
        audio_leg = "AUDIO" if wav_b64 is not None else "TEXT-ONLY (audio skipped)"
        log(f"POST /v1/chat/completions ({audio_leg}) to plugin-ON gateway ...")
        post_ms = int(time.time() * 1000)
        status, resp = post_chat(base, api_key, wav_b64, args.session_id, inbound_tid, args.timeout)

    if status == 200:
        log(
            f"PASS: chat/completions -> HTTP 200 (audio/transport works with plugin ON). "
            f"leg={audio_leg}"
        )
    else:
        head = json.dumps(resp)[:400]
        die(
            f"chat/completions -> HTTP {status} (expected 200). Plugin ON may have "
            f"broken the request path. Body head: {head}",
            code=2,
        )

    # --- (2) wait for async flush, then query Langfuse ------------------------
    log(f"waiting {args.settle}s for the plugin's background flush ...")
    time.sleep(args.settle)

    traces = fetch_spike_traces(client, since_ms=post_ms)

    # Enrich: pull full detail for matched/observed traces for usage/cost.
    observed_ids = [getattr(t, "id", "") for t in traces]
    log(f"observed trace ids: {observed_ids or '(none)'}")

    # usage/cost (#42306) — inspect whichever observed trace matches our ids,
    # else the first recent one.
    target_id = None
    for cand in (inbound_tid, rederived_tid):
        if cand in observed_ids:
            target_id = cand
            break
    if target_id is None and observed_ids:
        target_id = observed_ids[0]

    usage_present, usage_detail = (False, "no observed trace to inspect")
    if target_id:
        full = trace_full(client, target_id)
        usage_present, usage_detail = usage_cost_present(full)

    # --- (3) verdict ----------------------------------------------------------
    verdict, rationale = decide_verdict(traces, inbound_tid, rederived_tid)

    # Flush our own read client (no-op for reads, but tidy).
    with contextlib.suppress(Exception):
        client.flush()

    print("")
    print("=" * 78)
    print("HLF-G0 RESULT")
    print("=" * 78)
    print(f"gateway POST status      : {status} (expected 200)")
    print(f"audio leg                : {audio_leg}")
    print(f"inbound trace_id sent     : {inbound_tid}")
    print(f"plugin-seed re-derivation : {rederived_tid}  (sid={args.session_id})")
    print(f"observed trace ids        : {observed_ids or '(none)'}")
    print(f"usage/cost attached (#42306): {usage_present}  [{usage_detail}]")
    print("-" * 78)
    print(f"VERDICT: {verdict}")
    print(rationale)
    print("=" * 78)

    # Exit codes: 0 PASS, 3 FAIL, 4 INCONCLUSIVE (200-but-undecided).
    if verdict == PASS:
        return 0
    if verdict == INCONCLUSIVE:
        return 4
    return 3


if __name__ == "__main__":
    sys.exit(main())
