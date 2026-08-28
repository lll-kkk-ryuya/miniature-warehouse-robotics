#!/usr/bin/env bash
# Send one shell command to the Jetson through the jetson-link agent and wait
# for its output. Canonical design: docs/jetson/02-remote-access-and-dev-link.md
#
# Usage: deploy/dev/jetson-link/send.sh 'free -h'
#        deploy/dev/jetson-link/send.sh --timeout 120 'colcon build'
set -euo pipefail

STATE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.state"
TIMEOUT=60

while [[ "${1:-}" == --* ]]; do
  case "$1" in
    --timeout) TIMEOUT="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ $# -lt 1 ]]; then
  echo "usage: send.sh [--timeout SEC] '<shell command>'" >&2
  exit 2
fi

mkdir -p "$STATE"
ID="cmd-$(date +%s)"
printf '%s\n%s\n' "$ID" "$1" > "$STATE/cmd.txt"
echo "[sent] $ID (agent polls every 3s, timeout ${TIMEOUT}s)"

for _ in $(seq 1 "$TIMEOUT"); do
  if [[ -f "$STATE/last.txt" ]] && head -1 "$STATE/last.txt" | grep -qF "### $ID"; then
    tail -n +2 "$STATE/last.txt"
    exit 0
  fi
  sleep 1
done

echo "[timeout] no reply for $ID - is mwr-agent running on the Jetson?" >&2
exit 1
