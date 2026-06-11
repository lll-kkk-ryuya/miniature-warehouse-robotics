#!/usr/bin/env bash
# Langfuse Phase-3 verify-harness driver (doc13:520 ①〜⑤). Two halves, mirroring
# spike/latency + spike/memory-gate:
#   * OFFLINE selftest — autonomous, CI-green: ruff + pytest of verify.py's pure predicates
#     (trace_id determinism / Grok cost arithmetic / ①〜⑤ logic with a FAKE Langfuse client).
#     NO SDK, NO network, NO keys. This is the DoD coverage gate.
#   * LIVE verify — a PAID human gate: drives the gateway's CURRENT active_provider through the
#     Bridge-owned langfuse.openai path, reads the trace back, and asserts ①〜⑤. Requires real
#     Langfuse(4.7.x) + Hermes(:8642) + provider keys. dev-only, fail-closed (verify.py guards).
#
# WHY a harness (not ad-hoc): Phase 4's 4-provider × 3-mode comparison presumes a verifiable
# observability layer — 1 Bridge-owned trace/cycle (no double generation), a deterministic
# trace_id #4/#6 both derive, and cost≠0 for all four (incl. Grok). doc20 §8.4 item3 says these are
# confirmable ONLY against real Langfuse. Turnkey-ing the run keeps Phase 3→4 off the critical path.
#
# Usage:
#   ./run.sh selftest            # OFFLINE: ruff + pytest of the pure core (autonomous gate)
#   ./run.sh setup               # pip install 'langfuse>=4.7,<5' 'openai>=1.0' (for live verify)
#   ./run.sh verify  <provider>  # LIVE (paid, human gate): verify the gateway's active_provider
#   ./run.sh report              # summarise out/*.json into a table for RESULT.md transcription
#   ./run.sh clean               # remove out/ (raw per-run dumps)
#
# 4-provider sweep (live): for each of anthropic|openai|google|xai, set Hermes active_provider
# (doc13:175) -> restart gateway -> `./run.sh verify <provider>`. Same window as spike/latency's
# live run (both drive Hermes 4-provider). See README.md.
#
# Env: PY (default python3.12 — host python3 is 3.7), HERMES_BASE_URL (default 127.0.0.1:8642),
#      WAREHOUSE_RUN_ID (per-run id seeding the trace; required for live), LANGFUSE_GROK_PRICES
#      ("IN,OUT" USD/token from CHECKLIST.md — injected, NOT baked into code per doc08:508).
set -uo pipefail

SPIKE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SPIKE_DIR/../.." && pwd)"   # repo root (this spike lives at spike/langfuse/)
PY="${PY:-python3.12}"
BASE_URL="${HERMES_BASE_URL:-http://127.0.0.1:8642}"
ENV_FILE="${LANGFUSE_ENV_FILE:-$REPO_DIR/config/dev/.env}"
PROVIDERS=(anthropic openai google xai)

case "${1:-}" in
  selftest)
    # The autonomous DoD gate — pure offline. Clear stale bytecode first (a same-second ruff-format
    # rewrite can leave a stale .pyc that false-fails pytest — see project memory / #230 gotcha).
    find "$SPIKE_DIR" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
    echo "=== ruff check ==="
    "$PY" -m ruff check "$SPIKE_DIR" || { echo "ruff check FAILED"; exit 1; }
    echo "=== ruff format --check ==="
    "$PY" -m ruff format --check "$SPIKE_DIR" || { echo "ruff format FAILED"; exit 1; }
    echo "=== pytest (offline core: fake Langfuse client, no SDK/keys) ==="
    "$PY" -m pytest "$SPIKE_DIR/test_verify.py" -q || { echo "pytest FAILED"; exit 1; }
    echo "selftest: PASS (offline core green)" ;;

  setup)
    # Live verify needs the v4 SDK (doc13:514) + openai (Bridge-owned langfuse.openai path, doc13:517).
    # Offline selftest needs NEITHER — this is only for the human-gated live run.
    echo "=== pip install langfuse + openai (for LIVE verify only) ==="
    "$PY" -m pip install --quiet 'langfuse>=4.7,<5' 'openai>=1.0' \
      || { echo "pip install FAILED — install manually: $PY -m pip install 'langfuse>=4.7,<5' 'openai>=1.0'"; exit 1; }
    "$PY" -c "import langfuse, openai; print('langfuse', langfuse.__version__, '/ openai', openai.__version__)"
    echo "setup done. Next: ./run.sh verify <provider>  (requires Hermes + keys — see README.md)" ;;

  verify)
    prov="${2:-${WAREHOUSE_PROVIDER:-}}"
    if [ -z "$prov" ]; then
      echo "usage: ./run.sh verify <anthropic|openai|google|xai>  (the gateway's CURRENT active_provider)"; exit 2
    fi
    cat <<'BANNER'
############################################################
## (!) LIVE VERIFY — PAID, HUMAN GATE                     ##
## Drives ONE provider call through Hermes + Langfuse.    ##
## Needs real Langfuse(4.7.x) + Hermes(:8642) + provider  ##
## keys. dev-only / fail-closed. Sweep all 4 by switching ##
## Hermes active_provider (doc13:175) between runs.       ##
############################################################
BANNER
    # Best-effort Hermes liveness hint (verify.py still fail-closes on missing keys/SDK).
    if ! curl -sf "$BASE_URL/v1/models" >/dev/null 2>&1; then
      echo "  WARN: $BASE_URL not responding to GET /v1/models — start Hermes Gateway first (README.md)."
    fi
    GP_ARG=()
    [ -n "${LANGFUSE_GROK_PRICES:-}" ] && GP_ARG=(--grok-prices "$LANGFUSE_GROK_PRICES")
    echo "  verifying active_provider='$prov' against $BASE_URL ..."
    # NOTE: "${GP_ARG[@]+"${GP_ARG[@]}"}" — guard the empty-array expansion. Under `set -u`, macOS
    # bash 3.2 (the dev machine, CLAUDE.md) treats "${GP_ARG[@]}" on an EMPTY array as an unbound
    # variable and aborts (exit 127) BEFORE verify.py runs. LANGFUSE_GROK_PRICES is optional/xAI-only
    # (README §LIVE), so anthropic|openai|google leave it unset — this guard keeps them runnable.
    "$PY" "$SPIKE_DIR/verify.py" -p "$prov" --base-url "$BASE_URL" --env-file "$ENV_FILE" "${GP_ARG[@]+"${GP_ARG[@]}"}"
    echo "  done. Repeat for the other providers, then: ./run.sh report" ;;

  report)
    OUT_DIR="$SPIKE_DIR/out"
    [ -d "$OUT_DIR" ] || { echo "no out/ — run: ./run.sh verify <provider>" >&2; exit 1; }
    # Summarise the secret-free per-run JSON dumps into a ①〜⑤ matrix for RESULT.md transcription.
    "$PY" - "$OUT_DIR" <<'PYREPORT'
import json, sys
from pathlib import Path

out_dir = Path(sys.argv[1])
files = sorted(out_dir.glob("*.json"))
if not files:
    print("no out/*.json yet — run ./run.sh verify <provider>"); raise SystemExit(0)
print("=== Langfuse Phase-3 verify report (out/*.json) ===")
hdr = f"{'provider':10} {'①id':>4} {'②cost':>6} {'③1gen':>6} {'④prmpt':>7} {'⑤sdk':>5} {'fetch':>6}"
print(hdr); print("-" * len(hdr))
for f in files:
    try:
        r = json.loads(f.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"{f.name}: unreadable ({exc})"); continue
    ev = r.get("evaluation", {})
    def m(v): return "?" if v is None else ("Y" if v else "N")
    print(f"{r.get('provider_label',''):10} {m(ev.get('check1_inbound_trace_id')):>4} "
          f"{m(ev.get('check2_all_costs_nonzero')):>6} {m(ev.get('check3_single_generation')):>6} "
          f"{m(ev.get('check4_any_managed_prompt')):>7} {m(r.get('check5_sdk_version_ok')):>5} "
          f"{m(r.get('fetch_ok')):>6}   sdk={r.get('sdk_version')}")
print("\nlegend: Y=pass N=fail ?=unverifiable from API (confirm in Langfuse UI).")
print("Transcribe into RESULT.md. fetch=N → ①②③④ are UI-confirmed only (doc08:508 / doc20 §8.4).")
PYREPORT
    ;;

  clean)
    rm -rf "$SPIKE_DIR/out"
    echo "removed out/" ;;

  *)
    echo "usage: $0 {selftest|setup|verify <provider>|report|clean}"
    echo "  providers: ${PROVIDERS[*]}"
    exit 2 ;;
esac
