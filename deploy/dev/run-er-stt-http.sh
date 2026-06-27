#!/usr/bin/env bash
# Serve ONLY the Hermes STT HTTP endpoint (/api/audio/transcribe) for the Mode X-ER out-of-band
# transcript lane — WITHOUT building the dashboard web UI.
#
# The endpoint lives on the Hermes web app. ``hermes dashboard`` needs an npm UI build, but the API
# routes are served by bare ``uvicorn hermes_cli.web_server:app``. On loopback there is no OAuth gate
# (web_server.should_require_auth), but /api/ still requires the dashboard session token
# (``X-Hermes-Session-Token``), pinned here via HERMES_DASHBOARD_SESSION_TOKEN. The Mode X-ER
# ``HermesTranscriber`` passes that same token. Isolated HERMES_HOME — never touches the personal
# ~/.hermes config/secrets (it does reuse the installed hermes-agent code + venv). See
# docs/mode-x-er/06 §5; consumed by warehouse_llm_bridge.robotics.HermesTranscriber.
#
# Usage:
#   HERMES_DASHBOARD_SESSION_TOKEN=my-token deploy/dev/run-er-stt-http.sh   # foreground
# Env knobs:
#   MWR_ER_STT_PORT      port (default 9119)          MWR_ER_HERMES_HOME  isolated home
#   MWR_HERMES_VENV_PY   Hermes venv python           MWR_HERMES_AGENT_DIR Hermes install dir
set -euo pipefail

HERMES_HOME="${MWR_ER_HERMES_HOME:-$HOME/.hermes-mwr-er-lean}"
PORT="${MWR_ER_STT_PORT:-9119}"
TOKEN="${HERMES_DASHBOARD_SESSION_TOKEN:-$(openssl rand -hex 24)}"
HVPY="${MWR_HERMES_VENV_PY:-$HOME/.hermes/hermes-agent/venv/bin/python3}"
HA="${MWR_HERMES_AGENT_DIR:-$HOME/.hermes/hermes-agent}"

if [ ! -x "$HVPY" ]; then
  echo "ERROR: Hermes venv python not found at $HVPY (set MWR_HERMES_VENV_PY)." >&2
  exit 2
fi
if [ ! -f "$HA/hermes_cli/web_server.py" ]; then
  echo "ERROR: Hermes install not found at $HA (set MWR_HERMES_AGENT_DIR)." >&2
  exit 2
fi

echo "Hermes STT HTTP: http://127.0.0.1:${PORT}/api/audio/transcribe  (loopback, no UI build)"
echo "X-Hermes-Session-Token: ${TOKEN}    (ephemeral loopback token — pass this to HermesTranscriber)"
cd "$HA"
exec env HERMES_HOME="$HERMES_HOME" HERMES_DASHBOARD_SESSION_TOKEN="$TOKEN" \
  "$HVPY" -m uvicorn hermes_cli.web_server:app --host 127.0.0.1 --port "$PORT" --log-level warning
