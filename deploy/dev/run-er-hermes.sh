#!/usr/bin/env bash
# Start a DEDICATED, LEAN Hermes gateway whose active model is Gemini Robotics-ER.
#
# Isolated via HERMES_HOME — it NEVER touches the personal ~/.hermes (the user's openai-codex
# daily driver). The ER text+image leg of Mode X-ER routes through this gateway's OpenAI-compatible
# /v1/chat/completions; the audio leg goes DIRECT to ER (Hermes can't carry audio). See
# docs/mode-x-er/06-unfrozen-contract-resolutions.md §5.
#
# Usage:
#   export GOOGLE_API_KEY=...            # (or GEMINI_API_KEY) your Gemini key — never printed
#   deploy/dev/run-er-hermes.sh          # foreground; Ctrl-C to stop
# Env knobs:
#   MWR_ER_HERMES_HOME  isolated config home (default: ~/.hermes-mwr-er-lean)
#   MWR_ER_HERMES_PORT  API server port      (default: 8643)
#
# After start, the Bridge points at http://127.0.0.1:$PORT/v1 with the API_SERVER_KEY:
#   grep API_SERVER_KEY "$MWR_ER_HERMES_HOME/.env"   # value not printed here
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${MWR_ER_HERMES_HOME:-$HOME/.hermes-mwr-er-lean}"
PORT="${MWR_ER_HERMES_PORT:-8643}"

GKEY="${GOOGLE_API_KEY:-${GEMINI_API_KEY:-}}"
if [ -z "$GKEY" ]; then
  echo "ERROR: export GOOGLE_API_KEY (or GEMINI_API_KEY) with your Gemini key first." >&2
  exit 2
fi
if ! command -v hermes >/dev/null 2>&1; then
  echo "ERROR: 'hermes' CLI not on PATH (try: export PATH=\"\$HOME/.local/bin:\$PATH\")." >&2
  exit 2
fi

mkdir -p "$HERMES_HOME"
cp "$SCRIPT_DIR/hermes-er/config.lean.yaml" "$HERMES_HOME/config.yaml"

# Write the instance .env once (secrets stay outside the repo). Keep an existing API_SERVER_KEY.
if [ -f "$HERMES_HOME/.env" ] && grep -q '^API_SERVER_KEY=.\+' "$HERMES_HOME/.env"; then
  AKEY="$(grep -E '^API_SERVER_KEY=' "$HERMES_HOME/.env" | head -1 | cut -d= -f2-)"
else
  AKEY="$(openssl rand -hex 32)"
fi
umask 077
{
  echo "API_SERVER_ENABLED=true"
  echo "API_SERVER_HOST=127.0.0.1"
  echo "API_SERVER_PORT=${PORT}"
  echo "API_SERVER_KEY=${AKEY}"
  echo "GOOGLE_API_KEY=${GKEY}"
} > "$HERMES_HOME/.env"

echo "Dedicated LEAN ER Hermes: HERMES_HOME=$HERMES_HOME  port=$PORT  (personal ~/.hermes untouched)"
echo "Bridge auth token: grep API_SERVER_KEY \"$HERMES_HOME/.env\"   (value not printed)"
echo "Starting 'hermes gateway run' ..."
exec env HERMES_HOME="$HERMES_HOME" hermes gateway run
