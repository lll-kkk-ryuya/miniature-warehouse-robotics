#!/usr/bin/env bash
# =============================================================================
# run-hlf-g0.sh — wrapper that runs hlf_g0_probe.py with an ISOLATED langfuse
#                 and the gateway's creds sourced (never printed).
# =============================================================================
# RUN BY A HUMAN / MAIN SESSION ONLY. Makes REAL calls to a running plugin-ON
# forked ER gateway AND to the Langfuse API. Needs HERMES_LANGFUSE_* +
# API_SERVER_KEY (the user adds them to the gateway HERMES_HOME/.env).
#
# WHAT IT DOES
#   1. Installs `langfuse` into an ISOLATED dir ($LANGFUSE_LIBS) with
#        pip install --target $LANGFUSE_LIBS 'langfuse>=4.9,<5'
#      NEVER into the personal venv (~/.hermes/hermes-agent/venv) and NEVER into
#      ~/.hermes. (langfuse 4.9 = the line the Bridge pins and the version whose
#      v3+ API the Hermes plugin uses: create_trace_id / propagate_attributes /
#      start_as_current_observation.)
#   2. Sources the gateway's HERMES_HOME/.env (default ~/.hermes-mwr-er-lean/.env)
#      to export HERMES_LANGFUSE_* + API_SERVER_KEY — values are NEVER echoed.
#   3. Runs hlf_g0_probe.py under an isolated python with $LANGFUSE_LIBS prepended
#      to PYTHONPATH, pointed at the running gateway base URL.
#
# PREREQUISITE (THE USER MUST DO THIS — see README-hlf-g0.md):
#   - The plugin-ON forked gateway must be RUNNING (see README; the personal-venv
#     gateway needs langfuse on ITS PYTHONPATH + the plugin enabled, else the
#     plugin is inert and NO trace is written).
#   - Add HERMES_LANGFUSE_PUBLIC_KEY / _SECRET_KEY / (optional _BASE_URL) to the
#     gateway's HERMES_HOME/.env (~/.hermes-mwr-er-lean/.env). API_SERVER_KEY is
#     already there from the ER gateway setup.
#
# ABSOLUTE RULES (mirror run-er-gateway.sh)
#   - NEVER pip-install into / import from the personal venv or ~/.hermes.
#   - REFUSE if HERMES_HOME resolves to the personal home (~/.hermes).
#   - NEVER print secret values.
#
# USAGE
#   ./run-hlf-g0.sh                       # base from $HERMES_HOME/.env (host:port)
#   HLF_G0_BASE=http://127.0.0.1:8644 ./run-hlf-g0.sh
#   ./run-hlf-g0.sh --wav /path/to/clip.wav
#   ./run-hlf-g0.sh --help                # forwards probe --help
#
# PARAMETERS (env; sane defaults)
#   HERMES_HOME    ISOLATED gateway home  (default ~/.hermes-mwr-er-lean)
#   LANGFUSE_LIBS  isolated langfuse dir  (default /tmp/mwr-hlf-g0-langfuse-libs)
#   PY             python>=3.8 interpreter for the probe (default: python3.12,
#                  else python3 — must NOT be the personal venv python)
#   HLF_G0_BASE    gateway base URL       (default http://$API_SERVER_HOST:$API_SERVER_PORT)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROBE="$SCRIPT_DIR/hlf_g0_probe.py"

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes-mwr-er-lean}"
LANGFUSE_LIBS="${LANGFUSE_LIBS:-/tmp/mwr-hlf-g0-langfuse-libs}"
PERSONAL_HOME="$HOME/.hermes"
PERSONAL_VENV="$HOME/.hermes/hermes-agent/venv"

log() { printf '[hlf-g0] %s\n' "$*" >&2; }
die() { printf '[hlf-g0] ERROR: %s\n' "$*" >&2; exit 1; }

case "${1:-}" in
  --help|-h)
    sed -n '2,60p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    echo
    echo "---- probe --help ----"
    "${PY:-python3}" "$PROBE" --help || true
    exit 0
    ;;
esac

# --- isolation guards --------------------------------------------------------
rp_home="$HERMES_HOME"; [ -d "$rp_home" ] && rp_home="$(cd "$rp_home" && pwd)"
rp_personal="$(cd "$PERSONAL_HOME" 2>/dev/null && pwd || echo "$PERSONAL_HOME")"
[ "$rp_home" = "$rp_personal" ] && \
  die "HERMES_HOME ($rp_home) is the personal daily-driver. Use an isolated home (e.g. ~/.hermes-mwr-er-lean)."

rp_libs="$LANGFUSE_LIBS"; [ -d "$rp_libs" ] && rp_libs="$(cd "$rp_libs" && pwd)"
case "$rp_libs" in
  "$PERSONAL_VENV"*|"$rp_personal"*)
    die "LANGFUSE_LIBS ($rp_libs) is inside the personal venv/home. Refusing." ;;
esac

[ -f "$PROBE" ] || die "probe not found: $PROBE"

# --- pick an isolated python (NOT the personal venv) -------------------------
PY="${PY:-}"
if [ -z "$PY" ]; then
  if command -v python3.12 >/dev/null 2>&1; then PY="python3.12"
  elif command -v python3 >/dev/null 2>&1; then PY="python3"
  else die "no python3 found"; fi
fi
rp_py="$(command -v "$PY" || true)"
[ -n "$rp_py" ] || die "python interpreter not found: $PY"
case "$rp_py" in
  "$PERSONAL_VENV"*) die "PY ($rp_py) is the personal venv python. Use an isolated interpreter." ;;
esac
# Refuse Python < 3.8 (urllib f-strings / typing). The macOS system python3 may
# be 3.7 — fail loudly rather than emit confusing tracebacks.
"$PY" - <<'PYEOF' || die "$PY is too old (need >=3.8). Set PY=python3.12 (e.g. a project .venv)."
import sys
raise SystemExit(0 if sys.version_info[:2] >= (3, 8) else 1)
PYEOF
log "using python: $rp_py"

# --- install langfuse into the ISOLATED dir ----------------------------------
if [ -d "$LANGFUSE_LIBS/langfuse" ]; then
  log "reusing isolated langfuse at $LANGFUSE_LIBS"
else
  log "installing langfuse>=4.9,<5 into isolated dir: $LANGFUSE_LIBS (never the personal venv)"
  "$PY" -m pip install --quiet --target "$LANGFUSE_LIBS" 'langfuse>=4.9,<5' \
    || die "pip install langfuse into $LANGFUSE_LIBS failed."
fi

# --- source the gateway creds (NEVER printed) --------------------------------
[ -f "$HERMES_HOME/.env" ] || \
  die "missing $HERMES_HOME/.env — add HERMES_LANGFUSE_* (+ existing API_SERVER_KEY) there. See README-hlf-g0.md."
set -a
# shellcheck disable=SC1090
. "$HERMES_HOME/.env"
set +a

# --- resolve base URL (host:port from .env unless HLF_G0_BASE given) ----------
if [ -z "${HLF_G0_BASE:-}" ]; then
  host="${API_SERVER_HOST:-127.0.0.1}"
  port="${API_SERVER_PORT:-8644}"
  HLF_G0_BASE="http://${host}:${port}"
fi
export HLF_G0_BASE
log "gateway base: $HLF_G0_BASE (secrets sourced from $HERMES_HOME/.env — not printed)"

# --- run the probe with isolated langfuse ahead on PYTHONPATH ----------------
export PYTHONPATH="$LANGFUSE_LIBS${PYTHONPATH:+:$PYTHONPATH}"
log "running HLF-G0 probe (HUMAN/main-session live gate) ..."
exec "$PY" "$PROBE" --base "$HLF_G0_BASE" "$@"
