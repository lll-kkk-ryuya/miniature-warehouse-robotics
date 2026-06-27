#!/usr/bin/env bash
# =============================================================================
# run-bridge-live.sh — drive the PRODUCTION Bridge HermesClient (Option D, owner
# = hermes_plugin) against the forked + plugin-ON lean ER gateway and confirm the
# Langfuse trace lands at the Bridge-derived id. RUN BY A HUMAN / MAIN SESSION.
#
# Same gateway lifecycle as run-verify-d-audio.sh (start in bg -> /health ->
# source creds -> run -> --stop). Difference: it runs bridge_live_test.py with the
# warehouse_llm_bridge / eval_sdk / warehouse_interfaces packages on PYTHONPATH so
# it imports the REAL Bridge code (not a hand-built POST).
#
# EXIT: 0 PASS / 1 FAIL / 2 INCONCLUSIVE (from bridge_live_test.py) ; other = setup error.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
WT_ROOT="$(cd -- "$SCRIPT_DIR/../.." >/dev/null 2>&1 && pwd)"   # mwr-langfuse-plugin-d
WS="$WT_ROOT/ws/src"

GATEWAY_LAUNCHER="${GATEWAY_LAUNCHER:-/Users/kawaguchiryuya/Developer/mwr-hermes-er-fork/deploy/hermes/er-audio-fork/hlf-g0-langfuse/run-er-gateway-langfuse.sh}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes-mwr-er-lean}"
HERMES_SRC="${HERMES_SRC:-$HOME/.hermes/hermes-agent}"
LANGFUSE_LIBS="${LANGFUSE_LIBS:-/tmp/mwr-hlf-g0-langfuse-libs}"
PORT="${PORT:-8644}"
HOST="${API_SERVER_HOST:-127.0.0.1}"
BASE="http://${HOST}:${PORT}"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-90}"

# Prefer the .venv (py3.12) two levels up from this worktree; fall back to a system py3.12/3
# (do NOT hard-code a personal absolute path — the sibling run-verify-d-audio.sh derives it too).
VENV_PY="${VENV_PY:-$WT_ROOT/../miniature-warehouse-robotics/.venv/bin/python}"
[ -x "$VENV_PY" ] || VENV_PY="$(command -v python3.12 || command -v python3)"

log() { printf '[run-bridge-live] %s\n' "$*" >&2; }
die() { printf '[run-bridge-live] ERROR: %s\n' "$*" >&2; exit 1; }

[ -x "$GATEWAY_LAUNCHER" ] || die "gateway launcher not found: $GATEWAY_LAUNCHER"
[ -f "$HERMES_HOME/.env" ] || die "missing $HERMES_HOME/.env (HERMES_LANGFUSE_* + API_SERVER_KEY)."

_rp_home="$(cd "$HERMES_HOME" 2>/dev/null && pwd || echo "$HERMES_HOME")"
[ "$_rp_home" = "$(cd "$HOME/.hermes" 2>/dev/null && pwd || echo x)" ] && die "HERMES_HOME is personal ~/.hermes — refusing."

_stopped=0
stop_gateway() {
  [ "$_stopped" = 1 ] && return 0
  _stopped=1
  log "stopping gateway via launcher --stop (port $PORT)"
  HERMES_HOME="$HERMES_HOME" PORT="$PORT" "$GATEWAY_LAUNCHER" --stop >&2 || log "stop reported an issue (continuing)."
}
trap stop_gateway EXIT INT TERM

log "starting forked + plugin-ON lean ER gateway (home=$HERMES_HOME port=$PORT) in background"
HERMES_HOME="$HERMES_HOME" PORT="$PORT" "$GATEWAY_LAUNCHER" >"$HERMES_HOME/gw.bridge-live.log" 2>&1 &
GW_BG_PID=$!

log "waiting up to ${HEALTH_TIMEOUT_S}s for ${BASE}/health"
deadline=$(( $(date +%s) + HEALTH_TIMEOUT_S )); healthy=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  if ! kill -0 "$GW_BG_PID" 2>/dev/null; then
    log "gateway exited early — last 30 lines:"; tail -n 30 "$HERMES_HOME/gw.bridge-live.log" >&2 2>/dev/null || true
    die "gateway never came up."
  fi
  curl -fsS --max-time 3 "${BASE}/health" >/dev/null 2>&1 && { healthy=1; break; }
  sleep 1
done
[ "$healthy" = 1 ] || die "gateway never healthy at ${BASE}/health within ${HEALTH_TIMEOUT_S}s."
log "gateway healthy ✓"

log "sourcing creds from \$HERMES_HOME/.env (values never printed)"
set -a; . "$HERMES_HOME/.env"; set +a
[ -n "${API_SERVER_KEY:-}" ] || die "API_SERVER_KEY not set after sourcing .env."
# Distinguish "Langfuse not configured" (test returns INCONCLUSIVE=2) from a real setup error,
# so an empty key is not mistaken for a bare FAIL (parity with run-verify-d-audio.sh).
if [ -z "${HERMES_LANGFUSE_PUBLIC_KEY:-}" ] || [ -z "${HERMES_LANGFUSE_SECRET_KEY:-}" ]; then
  log "WARNING: HERMES_LANGFUSE_* not set — the trace lookup will be INCONCLUSIVE (plugin no-ops)."
fi
[ -d "$LANGFUSE_LIBS" ] || die "isolated langfuse libs missing at $LANGFUSE_LIBS."

# REAL Bridge code + its deps. Do NOT put LANGFUSE_LIBS on the TEST's path: it is a
# Python 3.11 build (the gateway venv) and would shadow the .venv 3.12
# pydantic_core/openai (ABI mismatch -> "No module named 'pydantic_core._pydantic_core'").
# The test side uses the .venv's own langfuse; create_trace_id is sha256[:16] in any
# langfuse 4.x, so the derived id still matches the gateway plugin's minted id.
export PYTHONPATH="${WS}/warehouse_llm_bridge:${WS}/eval_sdk:${WS}/warehouse_interfaces${PYTHONPATH:+:$PYTHONPATH}"

log "running bridge_live_test.py against ${BASE} (production HermesClient, owner=hermes_plugin)"
set +e
env \
  MWR_GATEWAY_BASE="$BASE" \
  API_SERVER_KEY="$API_SERVER_KEY" \
  HERMES_LANGFUSE_PUBLIC_KEY="${HERMES_LANGFUSE_PUBLIC_KEY:-}" \
  HERMES_LANGFUSE_SECRET_KEY="${HERMES_LANGFUSE_SECRET_KEY:-}" \
  HERMES_LANGFUSE_BASE_URL="${HERMES_LANGFUSE_BASE_URL:-${HERMES_LANGFUSE_HOST:-}}" \
  PYTHONPATH="$PYTHONPATH" \
  "$VENV_PY" "$SCRIPT_DIR/bridge_live_test.py" "$@"
RC=$?
set -e

log "bridge_live_test.py exit code: $RC (0=PASS 1=FAIL)"
stop_gateway
exit "$RC"
