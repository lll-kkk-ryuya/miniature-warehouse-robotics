#!/usr/bin/env bash
# =============================================================================
# preflight.sh — Jetson arrival/runbook preflight for G0-G7.
#
# Read-only by design: this script never enables, starts, restarts, or stops
# systemd units. It checks static deploy invariants before the Jetson arrives and
# read-only readiness after arrival. Hardware gates that require measurement are
# reported as MANUAL instead of being silently guessed.
#
# Source of truth:
# - docs/setup/jetson-deploy.md
# - docs/jetson/01-fidelity-and-validation.md
# - docs/architecture/19-environments-and-config.md
# =============================================================================
set -u -o pipefail

MODE="offline"
GATES=""
STRICT=0

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
SKIP_COUNT=0
MANUAL_COUNT=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
JETSON_DIR="${REPO_ROOT}/deploy/jetson"
SYSTEMD_DIR="${JETSON_DIR}/systemd"
ENV_EXAMPLE="${JETSON_DIR}/env/warehouse.env.example"
ENV_FILE_DEFAULT="${WAREHOUSE_ENV_FILE:-/etc/warehouse/warehouse.env}"

usage() {
  cat <<'EOF'
Usage:
  deploy/jetson/bin/preflight.sh [--offline|--arrival] [--gates G0,G1,G7|all] [--strict]

Modes:
  --offline   Static checks that can run before the Jetson arrives. Default.
  --arrival   Offline checks plus read-only Jetson arrival readiness checks.

Gate checks:
  --gates G0,G1,G7   Run selected gate helpers.
  --gates all        Run all implemented helpers: G0,G1,G7.

Notes:
  - This script is read-only. It never runs systemctl enable/start/restart/stop.
  - Secrets are not read. config/prod/.env and ~/.hermes/.env are intentionally ignored.
  - WAREHOUSE_ENV_FILE may point --arrival at a copied warehouse.env for local rehearsal.
  - Layer 0 clamp/e-stop and real robot behavior still require manual measurement.
EOF
}

log() {
  printf '%s\n' "$*"
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

manual() {
  MANUAL_COUNT=$((MANUAL_COUNT + 1))
  printf 'MANUAL %s\n' "$*"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

require_file() {
  local path="$1"
  local label="$2"
  if [[ -f "${path}" ]]; then
    pass "${label}: ${path}"
  else
    fail "${label}: missing ${path}"
  fi
}

require_grep() {
  local pattern="$1"
  local path="$2"
  local label="$3"
  if grep -Eq "${pattern}" "${path}"; then
    pass "${label}"
  else
    fail "${label}"
  fi
}

env_value() {
  local key="$1"
  local path="$2"
  sed -n "s/^${key}=//p" "${path}" 2>/dev/null | tail -n 1
}

python_cmd() {
  if have python3.12; then
    printf '%s\n' "python3.12"
  elif have python3; then
    printf '%s\n' "python3"
  else
    return 1
  fi
}

run_offline_checks() {
  log "== offline preflight =="
  require_file "${JETSON_DIR}/bin/install.sh" "install script"
  require_file "${JETSON_DIR}/bin/healthcheck.sh" "healthcheck script"
  require_file "${JETSON_DIR}/bin/ros-exec.sh" "ros exec wrapper"
  require_file "${ENV_EXAMPLE}" "env example"
  require_file "${SYSTEMD_DIR}/warehouse-nav2.service" "nav2 unit"
  require_file "${SYSTEMD_DIR}/warehouse-safety.service" "safety unit"
  require_file "${SYSTEMD_DIR}/warehouse.target" "target unit"

  local sh_failed=0
  local script
  for script in "${JETSON_DIR}"/bin/*.sh; do
    if bash -n "${script}"; then
      :
    else
      sh_failed=1
    fi
  done
  if [[ "${sh_failed}" -eq 0 ]]; then
    pass "bash syntax for deploy/jetson/bin/*.sh"
  else
    fail "bash syntax for deploy/jetson/bin/*.sh"
  fi

  if have shellcheck; then
    if shellcheck "${JETSON_DIR}"/bin/*.sh; then
      pass "shellcheck deploy/jetson/bin/*.sh"
    else
      fail "shellcheck deploy/jetson/bin/*.sh"
    fi
  else
    skip "shellcheck not installed"
  fi

  if have systemd-analyze; then
    if systemd-analyze verify "${SYSTEMD_DIR}"/*.service "${SYSTEMD_DIR}"/*.target; then
      pass "systemd-analyze verify deploy/jetson/systemd"
    else
      fail "systemd-analyze verify deploy/jetson/systemd"
    fi
  else
    skip "systemd-analyze not installed on this host"
  fi

  require_grep '^BindsTo=warehouse-safety\.service$' \
    "${SYSTEMD_DIR}/warehouse-nav2.service" \
    "nav2 unit binds to safety"
  require_grep 'ExecStart=.*bringup\.launch\.py .*sim:=false .*llm:=false' \
    "${SYSTEMD_DIR}/warehouse-nav2.service" \
    "prod nav2 launch pins sim:=false and llm:=false"
  require_grep '^WAREHOUSE_ENV=prod$' "${ENV_EXAMPLE}" "env example pins WAREHOUSE_ENV=prod"
  require_grep '^WAREHOUSE_TRAFFIC_MODE=open-rmf$' \
    "${ENV_EXAMPLE}" \
    "env example pins prod traffic_mode open-rmf"
  require_grep '^WAREHOUSE_MAP=' "${ENV_EXAMPLE}" "env example declares WAREHOUSE_MAP"

  if grep -En '^[[:space:]]*systemctl[[:space:]]+(enable|start|restart|stop)' \
    "${JETSON_DIR}/bin/install.sh" >/dev/null; then
    fail "install.sh must not enable/start/restart/stop units"
  else
    pass "install.sh does not enable/start/restart/stop units"
  fi

  local py
  if py="$(python_cmd)"; then
    if WAREHOUSE_ENV=prod PYTHONPATH="${REPO_ROOT}/ws/src/warehouse_interfaces" "${py}" - <<'PY'
from warehouse_interfaces.paths import runtime_dir

actual = str(runtime_dir())
raise SystemExit(0 if actual == "/run/warehouse" else f"runtime_dir={actual}")
PY
    then
      pass "WAREHOUSE_ENV=prod resolves runtime_dir=/run/warehouse"
    else
      fail "WAREHOUSE_ENV=prod runtime_dir check"
    fi
  else
    skip "python3.12/python3 unavailable for paths.py runtime check"
  fi
}

check_env_file() {
  local env_file="${1:-${ENV_FILE_DEFAULT}}"
  log "-- env file (${env_file}) --"
  if [[ ! -f "${env_file}" ]]; then
    fail "${env_file} missing; run install.sh after cloning/building on Jetson"
    return
  fi

  local key
  local missing=0
  for key in \
    WAREHOUSE_ENV ROS_DISTRO WAREHOUSE_REPO WAREHOUSE_WS WAREHOUSE_CONFIG_DIR \
    MICROROS_PORT WAREHOUSE_TRAFFIC_MODE WAREHOUSE_MAP; do
    if grep -Eq "^${key}=" "${env_file}"; then
      pass "${key} is present"
    else
      fail "${key} is missing"
      missing=1
    fi
  done

  if [[ "${missing}" -eq 0 ]]; then
    local env_name
    env_name="$(env_value WAREHOUSE_ENV "${env_file}")"
    if [[ "${env_name}" == "prod" ]]; then
      pass "WAREHOUSE_ENV=prod in ${env_file}"
    else
      fail "WAREHOUSE_ENV must be prod in ${env_file}"
    fi
  fi
}

run_arrival_checks() {
  log "== arrival readiness (read-only) =="
  check_env_file "${ENV_FILE_DEFAULT}"

  local required_cmd
  for required_cmd in systemctl journalctl free uname; do
    if have "${required_cmd}"; then
      pass "command available: ${required_cmd}"
    else
      fail "command missing: ${required_cmd}"
    fi
  done

  local optional_cmd
  for optional_cmd in ros2 colcon nvpmodel tegrastats curl; do
    if have "${optional_cmd}"; then
      pass "command available: ${optional_cmd}"
    else
      warn "command missing: ${optional_cmd}"
    fi
  done

  local env_file="${ENV_FILE_DEFAULT}"
  if [[ -f "${env_file}" ]]; then
    local ros_distro repo ws config_dir map_path
    ros_distro="$(env_value ROS_DISTRO "${env_file}")"
    repo="$(env_value WAREHOUSE_REPO "${env_file}")"
    ws="$(env_value WAREHOUSE_WS "${env_file}")"
    config_dir="$(env_value WAREHOUSE_CONFIG_DIR "${env_file}")"
    map_path="$(env_value WAREHOUSE_MAP "${env_file}")"

    if [[ -n "${ros_distro}" && -f "/opt/ros/${ros_distro}/setup.bash" ]]; then
      pass "ROS underlay exists: /opt/ros/${ros_distro}/setup.bash"
    else
      fail "ROS underlay missing for ROS_DISTRO=${ros_distro:-unset}"
    fi
    [[ -d "${repo}" ]] && pass "WAREHOUSE_REPO exists" || warn "WAREHOUSE_REPO missing: ${repo:-unset}"
    [[ -d "${ws}" ]] && pass "WAREHOUSE_WS exists" || warn "WAREHOUSE_WS missing: ${ws:-unset}"
    [[ -f "${ws}/install/setup.bash" ]] && pass "workspace install/setup.bash exists" || warn "workspace not built yet"
    [[ -f "${config_dir}/warehouse.base.yaml" ]] && pass "warehouse.base.yaml exists" || fail "warehouse.base.yaml missing"
    [[ -f "${config_dir}/prod/warehouse.yaml" ]] && pass "prod overlay exists" || fail "prod overlay missing"
    [[ -f "${map_path}" ]] && pass "WAREHOUSE_MAP exists" || warn "WAREHOUSE_MAP not found yet: ${map_path:-unset}"
  fi

  manual "confirm Jetson Super mode: sudo nvpmodel -m 2 + sudo jetson_clocks"
  manual "do not run systemctl enable --now warehouse.target until G0 passes"
}

gate_g0() {
  log "== G0 safety gate =="
  manual "Layer 0: send >0.3 m/s test command and verify MCU clamps <=0.3 m/s"
  manual "Layer 0: verify physical/proximity e-stop stops the robot independent of ROS"

  local py
  if py="$(python_cmd)"; then
    if "${py}" -m pytest \
      tests/unit/test_safety.py \
      tests/unit/test_safety_contracts.py \
      tests/unit/test_emergency_guardian.py \
      tests/unit/test_nav2_params_safety.py \
      -q; then
      pass "G0 safety unit tests"
    else
      fail "G0 safety unit tests"
    fi
  else
    fail "python unavailable for G0 safety unit tests"
  fi
}

gate_g1() {
  log "== G1 memory gate =="
  if ! have free; then
    fail "free command unavailable for G1 memory check"
    return
  fi
  local available_mb
  available_mb="$(free -m | awk '/^Mem:/ {print $7}')"
  if [[ -z "${available_mb}" ]]; then
    fail "could not parse available memory from free -m"
    return
  fi
  if (( available_mb >= 500 )); then
    pass "available RAM ${available_mb}MB >= 500MB"
  else
    fail "available RAM ${available_mb}MB < 500MB"
  fi
  manual "G1 PASS is valid only with the intended bench stack running (Nav2x2 + State + Guardian + Bridge; Mode C adds Open-RMF)"
}

gate_g7() {
  log "== G7 Hermes reachability + E2E gate =="
  if [[ -x "${JETSON_DIR}/bin/healthcheck.sh" ]]; then
    if "${JETSON_DIR}/bin/healthcheck.sh"; then
      pass "healthcheck.sh reports core stack healthy"
    else
      fail "healthcheck.sh reports unhealthy stack"
    fi
  else
    fail "healthcheck.sh is not executable"
  fi

  if have curl; then
    if curl -fsS --max-time 5 "http://${HERMES_HOST:-34.4.104.112}:${HERMES_PORT:-8642}/health" >/dev/null; then
      pass "GCP Hermes /health reachable"
    else
      fail "GCP Hermes /health not reachable"
    fi
  else
    fail "curl unavailable for Hermes reachability"
  fi
  manual "Bridge authentication/commander cycle requires configured secrets; this script does not read .env files"
}

run_gate_checks() {
  local gates="$1"
  [[ -z "${gates}" ]] && return
  [[ "${gates}" == "all" ]] && gates="G0,G1,G7"

  local gate
  IFS=',' read -r -a gate_array <<<"${gates}"
  for gate in "${gate_array[@]}"; do
    case "${gate}" in
      G0|g0) gate_g0 ;;
      G1|g1) gate_g1 ;;
      G7|g7) gate_g7 ;;
      *) fail "unknown gate '${gate}' (implemented: G0,G1,G7,all)" ;;
    esac
  done
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --offline)
      MODE="offline"
      shift
      ;;
    --arrival)
      MODE="arrival"
      shift
      ;;
    --gates)
      if [[ "$#" -lt 2 ]]; then
        echo "--gates requires a value" >&2
        exit 2
      fi
      GATES="$2"
      shift 2
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

log "Repo: ${REPO_ROOT}"
run_offline_checks
if [[ "${MODE}" == "arrival" ]]; then
  run_arrival_checks
fi
run_gate_checks "${GATES}"

log "== summary =="
log "PASS=${PASS_COUNT} FAIL=${FAIL_COUNT} WARN=${WARN_COUNT} SKIP=${SKIP_COUNT} MANUAL=${MANUAL_COUNT}"

if [[ "${STRICT}" -eq 1 && "${WARN_COUNT}" -gt 0 ]]; then
  exit 1
fi
if [[ "${FAIL_COUNT}" -gt 0 ]]; then
  exit 1
fi
exit 0
