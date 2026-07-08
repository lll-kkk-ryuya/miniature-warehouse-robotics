#!/usr/bin/env bash
# Runner for the XER6 live matrix harness (sibling of deploy/dev/run-live-er-chain.sh:43-80 —
# same gate discipline). Default mode is PAID: it arms WAREHOUSE_LIVE_ER=1 internally, which is
# only legitimate AFTER the operator approved the batch cost (doc07 §4.5; this round: <=12 calls
# approved 2026-07-08). '--check' and '--offline' NEVER make a provider call.
#
# Secrets: never printed. The Gemini key comes from the operator's env (~/.zshenv). The gateway
# bearer (API_SERVER_KEY) is sourced from the FORK gateway home .env INSIDE this script when not
# already exported — the agent process itself never reads a .env (environments.md:24-25).
#
# Usage:
#   spike/xer6-live-matrix/run-live-matrix.sh --check              # safe: keys + gateway health only
#   spike/xer6-live-matrix/run-live-matrix.sh --offline [args...]  # safe: 0-charge fixture replay
#   spike/xer6-live-matrix/run-live-matrix.sh [args...]            # PAID: live matrix (budget 12)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# The shared dev venv lives in the primary checkout (precedent run-live-er-chain.sh:27);
# worktrees reuse it. Override with MWR_PYTHON.
PYTHON="${MWR_PYTHON:-/Users/kawaguchiryuya/Developer/miniature-warehouse-robotics/.venv/bin/python}"
GATEWAY_URL="${MWR_ER_GATEWAY_URL:-http://127.0.0.1:8644}"
FORK_HOME="${MWR_ER_FORK_HOME:-$HOME/.hermes-mwr-er-fork}"

MODE="live"
case "${1:-}" in
  --check)   MODE="check";   shift ;;
  --offline) MODE="offline"; shift ;;
esac

if [ ! -x "$PYTHON" ]; then
  echo "ERROR: python not found at $PYTHON (set MWR_PYTHON)" >&2
  exit 2
fi

# Offline path needs no keys and cannot bill (WAREHOUSE_LIVE_ER stays unset;
# HttpErTransportSender.send would refuse anyway, gemini_er.py:287-291).
if [ "$MODE" = "offline" ]; then
  exec "$PYTHON" "$SCRIPT_DIR/harness.py" --mode offline "$@"
fi

# 1) Gemini key must be present (value NEVER printed) — run-live-er-chain.sh:32-39 precedent.
if [ -z "${GEMINI_API_KEY:-}" ] && [ -z "${GOOGLE_API_KEY:-}" ]; then
  cat >&2 <<'EOF'
ERROR: no Gemini key in env (GEMINI_API_KEY or GOOGLE_API_KEY).
The operator provisions it via ~/.zshenv — see docs/dev/07-mode-x-er-live-e2e-runbook.md §4.5.
EOF
  exit 2
fi

# 2) Gateway bearer: source the fork home .env inside this script if not already in env
#    (never echoed; set -a exports what the file defines — run-er-gateway.sh:146-153 precedent).
if [ -z "${HERMES_API_KEY:-}" ] && [ -z "${API_SERVER_KEY:-}" ]; then
  if [ -f "$FORK_HOME/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$FORK_HOME/.env"
    set +a
  fi
fi
if [ -z "${HERMES_API_KEY:-}" ] && [ -z "${API_SERVER_KEY:-}" ]; then
  echo "ERROR: no gateway bearer (HERMES_API_KEY / API_SERVER_KEY) in env and no $FORK_HOME/.env" >&2
  exit 2
fi

# 3) Gateway health (unauthenticated /health; no provider call, no charge).
if command -v curl >/dev/null 2>&1; then
  if ! curl -fsS --max-time 5 "$GATEWAY_URL/health" >/dev/null 2>&1; then
    echo "ERROR: ER gateway not healthy at $GATEWAY_URL — start deploy/hermes/er-audio-fork/run-er-gateway.sh first" >&2
    exit 2
  fi
fi

if [ "$MODE" = "check" ]; then
  echo "PASS   Gemini key present in env (value hidden)"
  echo "PASS   gateway bearer present in env (value hidden)"
  echo "PASS   ER gateway healthy at $GATEWAY_URL"
  echo "[gate] default mode is a PAID Gemini Robotics-ER batch (operator-approved, <=12 sends)"
  echo "would run: WAREHOUSE_LIVE_ER=1 $PYTHON $SCRIPT_DIR/harness.py --mode live --budget 12 --gateway $GATEWAY_URL"
  exit 0
fi

echo "[gate] paid Gemini Robotics-ER matrix (operator-authorized batch, hard cap 12 sends)"
# --mode/--budget are pinned AFTER "$@" (argparse last-wins), so pass-through args can NOT
# change them at all; the harness additionally refuses any budget above its APPROVED_CAP.
exec env WAREHOUSE_LIVE_ER=1 "$PYTHON" "$SCRIPT_DIR/harness.py" \
  --gateway "$GATEWAY_URL" "$@" --mode live --budget 12
