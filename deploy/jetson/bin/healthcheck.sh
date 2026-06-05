#!/usr/bin/env bash
# =============================================================================
# healthcheck.sh — monitoring scaffold for the warehouse stack on the Jetson.
#
# Reports systemd unit liveness, the shared runtime dir / state.json freshness,
# and (best-effort) GCP Hermes Gateway reachability. Read-only. Exit code is
# non-zero if any CORE unit is not active, so it doubles as a cron/health probe.
#
#   deploy/jetson/bin/healthcheck.sh            # human summary
#   STATE_MAX_AGE=5 deploy/jetson/bin/healthcheck.sh   # stricter staleness
#
# Source of truth: docs/setup/jetson-deploy.md, paths.py (runtime dir/state.json),
# docs/architecture/19-environments-and-config.md (prod Hermes 34.4.104.112).
# =============================================================================
set -uo pipefail

UNITS=(
  warehouse-microros-agent.service
  warehouse-state-cache.service
  warehouse-safety.service
  warehouse-nav2.service
  warehouse-bridge.service
)
RUNTIME_DIR="${WAREHOUSE_RUNTIME_DIR:-/run/warehouse}"
STATE_FILE="${RUNTIME_DIR}/state.json"
STATE_MAX_AGE="${STATE_MAX_AGE:-10}"        # seconds; state cache target ~100ms (doc12:391)
HERMES_HOST="${HERMES_HOST:-34.4.104.112}"  # prod GCP Hermes (doc19:18,86)
HERMES_PORT="${HERMES_PORT:-8642}"          # Hermes Gateway port

rc=0
echo "== warehouse stack health =="

echo "-- systemd units --"
for u in "${UNITS[@]}"; do
  state="$(systemctl is-active "${u}" 2>/dev/null || true)"
  printf '  %-34s %s\n' "${u}" "${state:-unknown}"
  [[ "${state}" == "active" ]] || rc=1
done

echo "-- runtime dir (${RUNTIME_DIR}) --"
if [[ -d "${RUNTIME_DIR}" ]]; then
  if [[ -f "${STATE_FILE}" ]]; then
    now="$(date +%s)"
    mtime="$(stat -c %Y "${STATE_FILE}" 2>/dev/null || echo 0)"
    age=$(( now - mtime ))
    if (( age <= STATE_MAX_AGE )); then
      echo "  state.json fresh (${age}s old)"
    else
      echo "  state.json STALE (${age}s > ${STATE_MAX_AGE}s) — State Cache stalled?"
      rc=1
    fi
  else
    echo "  state.json MISSING — State Cache not writing"
    rc=1
  fi
else
  echo "  ${RUNTIME_DIR} MISSING — no unit has created the runtime dir"
  rc=1
fi

echo "-- GCP Hermes Gateway (best-effort) --"
if command -v curl >/dev/null 2>&1; then
  if curl -fsS --max-time 5 "http://${HERMES_HOST}:${HERMES_PORT}/health" >/dev/null 2>&1; then
    echo "  reachable: ${HERMES_HOST}:${HERMES_PORT}"
  else
    echo "  NOT reachable: ${HERMES_HOST}:${HERMES_PORT} (network / Gateway down?) [non-fatal]"
  fi
else
  echo "  curl not installed — skipped"
fi

echo "== exit ${rc} =="
exit "${rc}"
