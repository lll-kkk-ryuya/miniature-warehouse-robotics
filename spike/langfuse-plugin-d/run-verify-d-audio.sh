#!/usr/bin/env bash
# =============================================================================
# run-verify-d-audio.sh — wrapper for the OPTION D AUDIO live verify.
# =============================================================================
# RUN BY A HUMAN / MAIN SESSION ONLY (after creds are in place).  It:
#   1. STARTS the input_audio-forked + Langfuse-plugin-ON LEAN ER gateway via the
#      single-sourced launcher
#        ../../../mwr-hermes-er-fork/deploy/hermes/er-audio-fork/hlf-g0-langfuse/run-er-gateway-langfuse.sh
#      (that launcher applies 0001-input_audio-passthrough.patch AND enables the
#      Hermes observability/langfuse plugin in an ISOLATED env — it NEVER touches
#      personal ~/.hermes, and installs langfuse via pip --target + PYTHONPATH).
#      We background it (its `run` mode `exec`s the gateway in the FOREGROUND).
#   2. WAITS for the gateway /health.
#   3. SOURCES creds from $HERMES_HOME/.env (set -a; values are NEVER echoed) and
#      prepends the ISOLATED langfuse libs to PYTHONPATH.
#   4. RUNS verify_d_audio.py (build-only here; this is its driver).
#   5. STOPS the gateway via the launcher's --stop (kills by port; removes the
#      isolated worktree), regardless of the verify outcome.
#
# An agent/subagent MUST NOT run this (no creds, no live gateway/Langfuse).
#
# PREREQUISITE (see README-verify-d.md): the Langfuse plugin keys must already be
# in the ISOLATED home's .env:
#     ~/.hermes-mwr-er-lean/.env :  HERMES_LANGFUSE_PUBLIC_KEY / _SECRET_KEY /
#                                   _BASE_URL   (plus API_SERVER_KEY for the gateway)
#
# EXIT CODES (propagated from verify_d_audio.py):
#   0 PASS   1 FAIL   2 INCONCLUSIVE   (other non-zero = setup/teardown error)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

# --- locate the single-sourced gateway launcher (audio fork + plugin ON) ------
GATEWAY_LAUNCHER="${GATEWAY_LAUNCHER:-/Users/kawaguchiryuya/Developer/mwr-hermes-er-fork/deploy/hermes/er-audio-fork/hlf-g0-langfuse/run-er-gateway-langfuse.sh}"

# --- isolated environment knobs (MUST match the launcher's defaults) ----------
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes-mwr-er-lean}"     # isolated ER home (NOT ~/.hermes)
HERMES_SRC="${HERMES_SRC:-$HOME/.hermes/hermes-agent}"      # personal clone (read-only: venv/pip)
LANGFUSE_LIBS="${LANGFUSE_LIBS:-/tmp/mwr-hlf-g0-langfuse-libs}"  # isolated --target install
PORT="${PORT:-8644}"                                        # base launcher default
HOST="${API_SERVER_HOST:-127.0.0.1}"
BASE="http://${HOST}:${PORT}"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-90}"

VENV_PY="${VENV_PY:-$HERMES_SRC/venv/bin/python}"
[ -x "$VENV_PY" ] || VENV_PY="$(command -v python3.12 || command -v python3 || command -v python)"

log() { printf '[run-verify-d-audio] %s\n' "$*" >&2; }
die() { printf '[run-verify-d-audio] ERROR: %s\n' "$*" >&2; exit 1; }

# --- refuse to operate on the personal home (defense in depth) -----------------
_rp_home="$(cd "$HERMES_HOME" 2>/dev/null && pwd || echo "$HERMES_HOME")"
_rp_personal="$(cd "$HOME/.hermes" 2>/dev/null && pwd || echo "$HOME/.hermes")"
[ "$_rp_home" = "$_rp_personal" ] && die "HERMES_HOME is the personal ~/.hermes — refusing. Use an isolated home."

[ -x "$GATEWAY_LAUNCHER" ] || die "gateway launcher not found/executable: $GATEWAY_LAUNCHER"
[ -f "$HERMES_HOME/.env" ] || die "missing $HERMES_HOME/.env — add HERMES_LANGFUSE_* + API_SERVER_KEY (see README-verify-d.md)."

# --- teardown trap: always stop the gateway via the launcher (kills by port) ---
_stopped=0
stop_gateway() {
  [ "$_stopped" = 1 ] && return 0
  _stopped=1
  log "stopping gateway via launcher --stop (kills by port $PORT; removes isolated worktree)"
  HERMES_HOME="$HERMES_HOME" PORT="$PORT" "$GATEWAY_LAUNCHER" --stop >&2 2>&1 || \
    log "launcher --stop reported an issue (continuing)."
}
trap stop_gateway EXIT INT TERM

# --- 1. start the gateway in the BACKGROUND (its run mode execs in foreground) -
log "starting forked+plugin-ON LEAN ER gateway (home=$HERMES_HOME port=$PORT) in background"
log "  launcher applies input_audio patch + enables observability/langfuse in an isolated env"
HERMES_HOME="$HERMES_HOME" PORT="$PORT" "$GATEWAY_LAUNCHER" \
  >"$HERMES_HOME/gw.verify-d-audio.log" 2>&1 &
GW_BG_PID=$!

# --- 2. wait for /health ------------------------------------------------------
log "waiting up to ${HEALTH_TIMEOUT_S}s for ${BASE}/health"
healthy=0
deadline=$(( $(date +%s) + HEALTH_TIMEOUT_S ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if ! kill -0 "$GW_BG_PID" 2>/dev/null; then
    log "gateway launcher process exited early — see $HERMES_HOME/gw.verify-d-audio.log"
    log "--- last 30 lines ---"
    tail -n 30 "$HERMES_HOME/gw.verify-d-audio.log" >&2 2>/dev/null || true
    die "gateway never came up."
  fi
  if curl -fsS --max-time 3 "${BASE}/health" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 1
done
[ "$healthy" = 1 ] || die "gateway never became healthy at ${BASE}/health within ${HEALTH_TIMEOUT_S}s."
log "gateway healthy at ${BASE}/health ✓"

# --- 3. source creds (NEVER echo) + isolated langfuse PYTHONPATH --------------
log "sourcing creds from \$HERMES_HOME/.env (values never printed)"
set -a
# shellcheck disable=SC1090
. "$HERMES_HOME/.env"
set +a

[ -n "${API_SERVER_KEY:-}" ] || die "API_SERVER_KEY not present after sourcing $HERMES_HOME/.env."
if [ -z "${HERMES_LANGFUSE_PUBLIC_KEY:-}" ] || [ -z "${HERMES_LANGFUSE_SECRET_KEY:-}" ]; then
  log "WARNING: HERMES_LANGFUSE_* not set — the trace/usage checks will be INCONCLUSIVE (plugin no-ops)."
fi

[ -d "$LANGFUSE_LIBS" ] || die "isolated langfuse libs not found at $LANGFUSE_LIBS (the launcher installs them; re-run it)."
export PYTHONPATH="${LANGFUSE_LIBS}${PYTHONPATH:+:$PYTHONPATH}"
log "PYTHONPATH prepended with isolated langfuse libs ($LANGFUSE_LIBS)"

# --- 4. run the verify --------------------------------------------------------
log "running verify_d_audio.py against ${BASE}"
set +e
env \
  MWR_GATEWAY_BASE="$BASE" \
  API_SERVER_KEY="$API_SERVER_KEY" \
  HERMES_LANGFUSE_PUBLIC_KEY="${HERMES_LANGFUSE_PUBLIC_KEY:-}" \
  HERMES_LANGFUSE_SECRET_KEY="${HERMES_LANGFUSE_SECRET_KEY:-}" \
  HERMES_LANGFUSE_BASE_URL="${HERMES_LANGFUSE_BASE_URL:-}" \
  PYTHONPATH="$PYTHONPATH" \
  "$VENV_PY" "$SCRIPT_DIR/verify_d_audio.py" "$@"
VERIFY_RC=$?
set -e

log "verify_d_audio.py exit code: $VERIFY_RC (0=PASS 1=FAIL 2=INCONCLUSIVE)"

# --- 5. stop (also runs via trap) + propagate the verify exit code ------------
stop_gateway
exit "$VERIFY_RC"
