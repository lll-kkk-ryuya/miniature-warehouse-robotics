#!/usr/bin/env bash
# =============================================================================
# slice3_live_precheck.sh - preflight for the slice3 live Hermes/RViz demo.
#
# Read-only helper for tests/e2e/README.md slice3 runbook. It validates the host
# integration harness and the WAREHOUSE_TASKS demo seed before starting a live
# ROS/Gazebo run. With --live it also checks external daemon health endpoints.
# =============================================================================
set -u -o pipefail

MODE="offline"
RUN_TESTS=1
STRICT=0
TASKS_JSON="${WAREHOUSE_TASKS:-}"
HERMES_BASE_URL="${HERMES_BASE_URL:-http://127.0.0.1:8642}"
NAV2_BRIDGE_BASE_URL="${NAV2_BRIDGE_BASE_URL:-http://127.0.0.1:8645}"

DEFAULT_TASKS='[{"id":"task_1","from":"berth_A","to":"shelf_1"},{"id":"task_2","from":"berth_B","to":"shelf_3"}]'

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
SKIP_COUNT=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

usage() {
  cat <<'EOF'
Usage:
  scripts/slice3_live_precheck.sh [--offline|--live] [--tasks JSON] [--skip-tests] [--strict]

Modes:
  --offline    Validate host e2e harness and WAREHOUSE_TASKS seed. Default.
  --live       Offline checks plus Hermes/Nav2 Bridge /health checks.

Options:
  --tasks JSON    Demo WAREHOUSE_TASKS seed. Defaults to a known-location two-task seed.
  --skip-tests    Skip pytest tests/e2e/ execution.
  --strict        Treat WARN/SKIP as failure.

Environment:
  WAREHOUSE_TASKS          Used when --tasks is not provided.
  HERMES_BASE_URL          Default: http://127.0.0.1:8642
  NAV2_BRIDGE_BASE_URL     Default: http://127.0.0.1:8645

Output:
  Prints the exact export/launch commands to use after the precheck passes.
EOF
}

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  printf 'PASS   %s\n' "$*"
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  printf 'FAIL   %s\n' "$*"
}

warn() {
  WARN_COUNT=$((WARN_COUNT + 1))
  printf 'WARN   %s\n' "$*"
}

skip() {
  SKIP_COUNT=$((SKIP_COUNT + 1))
  printf 'SKIP   %s\n' "$*"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

python_cmd() {
  if have python3.12; then
    printf '%s\n' "python3.12"
  elif have python3; then
    if python3 - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
    then
      printf '%s\n' "python3"
    else
      return 1
    fi
  else
    return 1
  fi
}

py_path() {
  printf '%s:%s:%s:%s\n' \
    "${REPO_ROOT}/ws/src/warehouse_interfaces" \
    "${REPO_ROOT}/ws/src/warehouse_llm_bridge" \
    "${REPO_ROOT}/ws/src/warehouse_mcp_server" \
    "${REPO_ROOT}/ws/src/warehouse_nav2_bridge"
}

repo_pythonpath() {
  if [[ -n "${PYTHONPATH:-}" ]]; then
    printf '%s:%s\n' "$(py_path)" "${PYTHONPATH}"
  else
    py_path
  fi
}

validate_tasks() {
  local py="$1"
  local raw="$2"
  PYTHONPATH="$(repo_pythonpath)" WAREHOUSE_TASKS_RAW="${raw}" "${py}" - <<'PY'
import json
import os
import sys

from warehouse_interfaces.locations import KNOWN_LOCATIONS
from warehouse_llm_bridge.scheduler import parse_seed_tasks

raw = os.environ["WAREHOUSE_TASKS_RAW"]
try:
    tasks = parse_seed_tasks(raw)
except Exception as exc:
    print(f"invalid WAREHOUSE_TASKS: {exc}", file=sys.stderr)
    raise SystemExit(1)

if len(tasks) < 2:
    print("slice3 expects at least two pending tasks for a two-bot head-on demo", file=sys.stderr)
    raise SystemExit(1)

bad = []
for task in tasks:
    for key in ("from", "to"):
        value = task.get(key)
        if value not in KNOWN_LOCATIONS:
            bad.append(f"{task.get('id', '<no-id>')}.{key}={value!r}")
if bad:
    print(
        "WAREHOUSE_TASKS contains location(s) outside KNOWN_LOCATIONS: " + ", ".join(bad),
        file=sys.stderr,
    )
    raise SystemExit(1)

print(json.dumps(tasks, ensure_ascii=False, separators=(",", ":")))
PY
}

check_http_health() {
  local py="$1"
  local label="$2"
  local url="$3"
  HEALTH_URL="${url%/}/health" HEALTH_LABEL="${label}" "${py}" - <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request

url = os.environ["HEALTH_URL"]
label = os.environ["HEALTH_LABEL"]
try:
    with urllib.request.urlopen(url, timeout=5) as response:
        body = response.read(4096).decode("utf-8", errors="replace")
        if response.status < 200 or response.status >= 300:
            print(f"{label} {url} returned HTTP {response.status}", file=sys.stderr)
            raise SystemExit(1)
        try:
            parsed = json.loads(body)
            print(json.dumps(parsed, ensure_ascii=False, separators=(",", ":")))
        except json.JSONDecodeError:
            print(body[:200])
except (urllib.error.URLError, TimeoutError, OSError) as exc:
    print(f"{label} {url} unreachable: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
}

run_static_checks() {
  printf '== slice3 offline precheck ==\n'
  [[ -f "${REPO_ROOT}/tests/e2e/README.md" ]] && pass "tests/e2e runbook exists" || fail "tests/e2e runbook missing"
  [[ -f "${REPO_ROOT}/tests/e2e/test_slice2_yield_forward.py" ]] && pass "slice2 e2e harness exists" || fail "slice2 e2e harness missing"
  [[ -f "${REPO_ROOT}/ws/src/warehouse_llm_bridge/warehouse_llm_bridge/scheduler.py" ]] && pass "llm scheduler exists" || fail "llm scheduler missing"
  [[ -f "${REPO_ROOT}/ws/src/warehouse_bringup/launch/bringup.launch.py" ]] && pass "bringup.launch.py exists" || fail "bringup.launch.py missing"

  local py
  if ! py="$(python_cmd)"; then
    fail "python3.12 or python>=3.11 is required"
    return
  fi
  pass "python selected: ${py}"

  local tasks="${TASKS_JSON:-${DEFAULT_TASKS}}"
  local normalized
  if normalized="$(validate_tasks "${py}" "${tasks}")"; then
    pass "WAREHOUSE_TASKS seed validates against PendingTask + KNOWN_LOCATIONS"
    TASKS_JSON="${normalized}"
  else
    fail "WAREHOUSE_TASKS seed validation"
  fi

  if [[ "${RUN_TESTS}" -eq 1 ]]; then
    if PYTHONPATH="$(repo_pythonpath)" "${py}" -m pytest -p no:cacheprovider "${REPO_ROOT}/tests/e2e/" -q; then
      pass "host e2e harness"
    else
      fail "host e2e harness"
    fi
  else
    skip "host e2e harness skipped by --skip-tests"
  fi

  if have ros2; then
    pass "ros2 command available"
  else
    skip "ros2 command not available on this host; run launch commands inside tiryoh ROS container"
  fi
}

run_live_checks() {
  printf '== slice3 live daemon precheck ==\n'
  local py
  if ! py="$(python_cmd)"; then
    fail "python3.12 or python>=3.11 is required for live health checks"
    return
  fi

  local output
  if output="$(check_http_health "${py}" "Hermes Gateway" "${HERMES_BASE_URL}" 2>&1)"; then
    pass "Hermes Gateway /health reachable at ${HERMES_BASE_URL}"
  else
    fail "Hermes Gateway /health unreachable at ${HERMES_BASE_URL}: ${output}"
  fi

  if output="$(check_http_health "${py}" "Nav2 Bridge" "${NAV2_BRIDGE_BASE_URL}" 2>&1)"; then
    pass "Nav2 Bridge /health reachable at ${NAV2_BRIDGE_BASE_URL}"
  else
    fail "Nav2 Bridge /health unreachable at ${NAV2_BRIDGE_BASE_URL}: ${output}"
  fi
}

print_next_steps() {
  printf '\n== launch commands ==\n'
  printf "export WAREHOUSE_TASKS='%s'\n" "${TASKS_JSON:-${DEFAULT_TASKS}}"
  cat <<'EOF'
export WAREHOUSE_CONFIG_DIR=/ws/config
export WAREHOUSE_ENV=dev
# sim idle only: AMCL may publish initial pose once, so avoid false pose_stale while recording.
export WAREHOUSE__SAFETY__POSE_FRESHNESS_TIMEOUT=999

# slice1 health (Hermes not required)
ros2 launch warehouse_bringup bringup.launch.py llm:=false sim:=true
# In another ROS-sourced shell after both Nav2 lifecycle managers report active:
cd /ws && scripts/slice3_seed_initialpose.sh

# slice2/3 full stack (Hermes Gateway :8642 and Nav2 Bridge :8645 already running)
ros2 launch warehouse_bringup bringup.launch.py sim:=true llm:=true traffic_mode:=none rviz:=true
# Repeat initialpose seeding after full-stack launch reaches active lifecycle.
cd /ws && scripts/slice3_seed_initialpose.sh
EOF
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --offline)
      MODE="offline"
      shift
      ;;
    --live)
      MODE="live"
      shift
      ;;
    --tasks)
      if [[ "$#" -lt 2 ]]; then
        echo "--tasks requires a JSON value" >&2
        exit 2
      fi
      TASKS_JSON="$2"
      shift 2
      ;;
    --skip-tests)
      RUN_TESTS=0
      shift
      ;;
    --strict)
      STRICT=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

cd "${REPO_ROOT}" || exit 1
run_static_checks
if [[ "${MODE}" == "live" ]]; then
  run_live_checks
fi

STRICT_BLOCK=0
if [[ "${STRICT}" -eq 1 && $((WARN_COUNT + SKIP_COUNT)) -gt 0 ]]; then
  STRICT_BLOCK=1
fi

if [[ "${FAIL_COUNT}" -eq 0 && "${STRICT_BLOCK}" -eq 0 ]]; then
  print_next_steps
else
  printf '\n== launch commands ==\n'
  if [[ "${STRICT_BLOCK}" -eq 1 ]]; then
    printf 'SKIP   launch commands suppressed because --strict treats WARN/SKIP as failure\n'
  else
    printf 'SKIP   launch commands suppressed because the precheck did not pass cleanly\n'
  fi
fi

printf '\n== summary ==\n'
printf 'PASS=%s FAIL=%s WARN=%s SKIP=%s\n' "${PASS_COUNT}" "${FAIL_COUNT}" "${WARN_COUNT}" "${SKIP_COUNT}"

if [[ "${STRICT_BLOCK}" -eq 1 ]]; then
  exit 1
fi
if [[ "${FAIL_COUNT}" -gt 0 ]]; then
  exit 1
fi
exit 0
