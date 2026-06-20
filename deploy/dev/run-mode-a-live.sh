#!/usr/bin/env bash
# Start the browser-viewable Gazebo/RViz Mode-A live stack with Hermes Bridge wiring.
#
# One-command operator path:
#   1. start/reuse the noVNC sim container,
#   2. preflight Hermes health + authenticated token,
#   3. inject the Bridge token into the ROS launch,
#   4. restart the full stack so env changes are actually picked up,
#   5. seed the head_on AMCL poses.
#
# Secret values are loaded from config/<env>/.env and passed to docker exec via
# --env VAR (not embedded in the shell command string or printed).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

WAREHOUSE_ENV_VALUE="${WAREHOUSE_ENV:-dev}"
ENV_FILE="${MWR_HERMES_ENV_FILE:-${REPO_ROOT}/config/${WAREHOUSE_ENV_VALUE}/.env}"
CONTAINER="${MWR_SIM_CONTAINER:-mwr-mode-a-live}"
PORT="${MWR_SIM_PORT:-6082}"
BIND="${MWR_SIM_BIND:-127.0.0.1}"
HOST_HERMES_URL="${HERMES_BASE_URL:-http://127.0.0.1:8642}"
CONTAINER_HERMES_URL="${WAREHOUSE__HERMES__BASE_URL:-http://host.docker.internal:8642}"
TRAFFIC_MODE="${TRAFFIC_MODE:-none}"
SCENARIO="${SCENARIO:-head_on}"
RVIZ_CONFIG="${RVIZ_CONFIG:-record}"
LOG_FILE="${MWR_LIVE_LOG_FILE:-/tmp/mwr_mode_a_live.log}"
HERMES_LOG_FILE="${MWR_HERMES_LOG_FILE:-/tmp/mwr_hermes_gateway.log}"
RESTART_STACK="${MWR_RESTART_STACK:-1}"
SKIP_BUILD="${MWR_SKIP_BUILD:-auto}"
SKIP_SEED="${MWR_SKIP_SEED:-0}"
RUN_CHAT_CHECK="${MWR_LIVE_CHAT:-0}"
START_HERMES="${MWR_START_HERMES:-0}"
TASKS_DEFAULT='[{"id":"task_1","from":"berth_A","to":"shelf_1"},{"id":"task_2","from":"berth_B","to":"shelf_3"}]'
WAREHOUSE_TASKS="${WAREHOUSE_TASKS:-${TASKS_DEFAULT}}"

usage() {
  cat <<'EOF'
Usage:
  deploy/dev/run-mode-a-live.sh [options]

Options:
  --container NAME     Docker container name. Default: mwr-mode-a-live
  --port PORT          noVNC host port. Default: 6082
  --env-file PATH      Bridge-side env file. Default: config/$WAREHOUSE_ENV/.env
  --hermes-url URL     Host Hermes URL. Default: http://127.0.0.1:8642
  --tasks JSON         WAREHOUSE_TASKS seed JSON.
  --start-hermes       Start "API_SERVER_ENABLED=true hermes gateway" in the background if down.
  --no-restart         Do not stop an existing warehouse_bringup launch first.
  --skip-build         Do not auto-build when /ws/ws/install/setup.bash is missing.
  --force-build        Run colcon build even if /ws/ws/install/setup.bash exists.
  --skip-seed          Do not run scripts/slice3_seed_initialpose.sh after launch.
  --chat               Include a paid/provider chat smoke in the Hermes preflight.
  -h, --help           Show this help.

Common overrides:
  MWR_SIM_CONTAINER=mwr-sim-v1 MWR_SIM_PORT=6081 deploy/dev/run-mode-a-live.sh

Prerequisite:
  API_SERVER_ENABLED=true hermes gateway
  config/dev/.env must contain the same API_SERVER_KEY as ~/.hermes/.env.

Fully one-command dev mode:
  deploy/dev/run-mode-a-live.sh --start-hermes
EOF
}

fail() { printf 'FAIL   %s\n' "$*" >&2; exit 1; }
info() { printf 'INFO   %s\n' "$*"; }

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --container)
      [[ "$#" -ge 2 ]] || fail "--container requires a name"
      CONTAINER="$2"; shift 2 ;;
    --port)
      [[ "$#" -ge 2 ]] || fail "--port requires a value"
      PORT="$2"; shift 2 ;;
    --env-file)
      [[ "$#" -ge 2 ]] || fail "--env-file requires a path"
      ENV_FILE="$2"; shift 2 ;;
    --hermes-url)
      [[ "$#" -ge 2 ]] || fail "--hermes-url requires a URL"
      HOST_HERMES_URL="$2"; shift 2 ;;
    --tasks)
      [[ "$#" -ge 2 ]] || fail "--tasks requires JSON"
      WAREHOUSE_TASKS="$2"; shift 2 ;;
    --start-hermes)
      START_HERMES=1; shift ;;
    --no-restart)
      RESTART_STACK=0; shift ;;
    --skip-build)
      SKIP_BUILD=1; shift ;;
    --force-build)
      SKIP_BUILD=0; shift ;;
    --skip-seed)
      SKIP_SEED=1; shift ;;
    --chat)
      RUN_CHAT_CHECK=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      fail "unknown argument: $1" ;;
  esac
done

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck source=/dev/null
  source "${ENV_FILE}"
else
  if [[ -z "${API_SERVER_KEY:-}" && -z "${HERMES_API_KEY:-}" ]]; then
    fail "Bridge env file missing: ${ENV_FILE}. Copy config/${WAREHOUSE_ENV_VALUE}/.env.example and set API_SERVER_KEY, or export API_SERVER_KEY/HERMES_API_KEY."
  fi
  info "Bridge env file not found; using API_SERVER_KEY/HERMES_API_KEY from the current environment"
fi

if [[ -z "${API_SERVER_KEY:-}" && -n "${HERMES_API_KEY:-}" ]]; then
  API_SERVER_KEY="${HERMES_API_KEY}"
fi
if [[ -z "${HERMES_API_KEY:-}" && -n "${API_SERVER_KEY:-}" ]]; then
  HERMES_API_KEY="${API_SERVER_KEY}"
fi
if [[ -z "${API_SERVER_KEY:-}" ]]; then
  fail "API_SERVER_KEY/HERMES_API_KEY is empty in ${ENV_FILE}"
fi

export API_SERVER_KEY HERMES_API_KEY WAREHOUSE_TASKS
export LANGFUSE_PUBLIC_KEY="${LANGFUSE_PUBLIC_KEY:-}"
export LANGFUSE_SECRET_KEY="${LANGFUSE_SECRET_KEY:-}"
export LANGFUSE_HOST="${LANGFUSE_HOST:-}"
export HERMES_LANGFUSE_PUBLIC_KEY="${HERMES_LANGFUSE_PUBLIC_KEY:-}"
export HERMES_LANGFUSE_SECRET_KEY="${HERMES_LANGFUSE_SECRET_KEY:-}"
export HERMES_LANGFUSE_HOST="${HERMES_LANGFUSE_HOST:-}"
export HERMES_BASE_URL="${HOST_HERMES_URL}"
export WAREHOUSE__HERMES__BASE_URL="${CONTAINER_HERMES_URL}"
export WAREHOUSE_ENV="${WAREHOUSE_ENV_VALUE}"

if ! command -v curl >/dev/null 2>&1; then
  fail "curl is required"
fi
if ! command -v docker >/dev/null 2>&1; then
  fail "docker is required"
fi

ensure_hermes() {
  if curl -fsS --max-time 4 "${HOST_HERMES_URL%/}/health" >/dev/null 2>&1; then
    return 0
  fi
  if [[ "${START_HERMES}" -ne 1 ]]; then
    return 0
  fi
  if ! command -v hermes >/dev/null 2>&1; then
    fail "Hermes is down and hermes command is not on PATH"
  fi
  info "Hermes is down; starting Gateway service (log=${HERMES_LOG_FILE})"
  if [[ "$(uname -s)" == "Darwin" ]] && command -v launchctl >/dev/null 2>&1; then
    # launchd services do not inherit this one-shot shell assignment; publish it
    # into the user launchd environment before starting the Hermes service.
    launchctl setenv API_SERVER_ENABLED true
  fi
  API_SERVER_ENABLED=true hermes gateway start > "${HERMES_LOG_FILE}" 2>&1 || {
    cat "${HERMES_LOG_FILE}" >&2 || true
    fail "Hermes Gateway service start failed"
  }
  for _ in $(seq 1 45); do
    if curl -fsS --max-time 4 "${HOST_HERMES_URL%/}/health" >/dev/null 2>&1; then
      info "Hermes Gateway is reachable"
      return 0
    fi
    sleep 1
  done
  tail -80 "${HERMES_LOG_FILE}" || true
  fail "Hermes did not become reachable at ${HOST_HERMES_URL}; inspect ${HERMES_LOG_FILE}"
}

ensure_hermes

preflight_args=(--env-file "${ENV_FILE}" --base-url "${HOST_HERMES_URL}" --skip-container)
if [[ "${RUN_CHAT_CHECK}" -eq 1 ]]; then
  preflight_args+=(--chat)
fi
"${REPO_ROOT}/deploy/dev/check-hermes-live.sh" "${preflight_args[@]}"

info "starting/reusing sim cockpit container ${CONTAINER} on ${BIND}:${PORT}"
MWR_SIM_CONTAINER="${CONTAINER}" MWR_SIM_PORT="${PORT}" MWR_SIM_BIND="${BIND}" \
  "${REPO_ROOT}/deploy/dev/run-sim-cockpit.sh"

mounted_ws="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/ws"}}{{.Source}}{{end}}{{end}}' "${CONTAINER}" 2>/dev/null || true)"
if [[ -n "${mounted_ws}" && "${mounted_ws}" != "${REPO_ROOT}" ]]; then
  fail "container ${CONTAINER} mounts ${mounted_ws} at /ws, not ${REPO_ROOT}. Use another MWR_SIM_CONTAINER or recreate it."
fi

container_preflight=(--env-file "${ENV_FILE}" --base-url "${HOST_HERMES_URL}" --container "${CONTAINER}")
if [[ "${RUN_CHAT_CHECK}" -eq 1 ]]; then
  container_preflight+=(--chat)
fi
"${REPO_ROOT}/deploy/dev/check-hermes-live.sh" "${container_preflight[@]}"

if [[ "${SKIP_BUILD}" == "0" ]] ||
  { [[ "${SKIP_BUILD}" == "auto" ]] && ! docker exec "${CONTAINER}" test -f /ws/ws/install/setup.bash; }; then
  info "building ROS workspace inside ${CONTAINER}"
  docker exec "${CONTAINER}" bash -lc \
    'set -euo pipefail; source /opt/ros/jazzy/setup.bash; cd /ws/ws; colcon build --symlink-install'
else
  info "ROS workspace build skipped (MWR_SKIP_BUILD=${SKIP_BUILD})"
fi

if [[ "${RESTART_STACK}" -eq 1 ]]; then
  info "stopping any existing warehouse full-stack launch in ${CONTAINER}"
  docker exec "${CONTAINER}" bash -lc \
    'pkill -f "ros2 launch warehouse_bringup bringup.launch.py" || true; pkill -f "/warehouse_llm_bridge/.*/llm_bridge" || true; sleep 2'
fi

info "launching Mode-A live stack; log=${LOG_FILE}"
docker exec -d \
  -e DISPLAY=:1 \
  -e XAUTHORITY=/home/ubuntu/.Xauthority \
  -e LIBGL_ALWAYS_SOFTWARE=1 \
  -e GALLIUM_DRIVER=llvmpipe \
  -e API_SERVER_KEY \
  -e HERMES_API_KEY \
  -e LANGFUSE_PUBLIC_KEY \
  -e LANGFUSE_SECRET_KEY \
  -e LANGFUSE_HOST \
  -e HERMES_LANGFUSE_PUBLIC_KEY \
  -e HERMES_LANGFUSE_SECRET_KEY \
  -e HERMES_LANGFUSE_HOST \
  -e WAREHOUSE_TASKS \
  -e WAREHOUSE_ENV="${WAREHOUSE_ENV_VALUE}" \
  -e WAREHOUSE_CONFIG_DIR=/ws/config \
  -e WAREHOUSE__HERMES__BASE_URL="${CONTAINER_HERMES_URL}" \
  -e HERMES_BASE_URL="${CONTAINER_HERMES_URL}" \
  -e WAREHOUSE__SAFETY__POSE_FRESHNESS_TIMEOUT=999 \
  -e WAREHOUSE_SCENARIO="${SCENARIO}" \
  "${CONTAINER}" bash -lc \
  "set -euo pipefail; \
   source /opt/ros/jazzy/setup.bash; \
   source /ws/ws/install/setup.bash; \
   ros2 launch warehouse_bringup bringup.launch.py sim:=true llm:=true traffic_mode:=${TRAFFIC_MODE} rviz:=true scenario:=${SCENARIO} rviz_config:=${RVIZ_CONFIG} > '${LOG_FILE}' 2>&1"

info "waiting for Nav2 Bridge (:8645) to become ready"
ready=0
for _ in $(seq 1 90); do
  if docker exec "${CONTAINER}" curl -fsS --max-time 4 http://localhost:8645/health >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [[ "${ready}" -ne 1 ]]; then
  docker exec "${CONTAINER}" tail -120 "${LOG_FILE}" || true
  fail "Nav2 Bridge did not become ready; inspect ${LOG_FILE} inside ${CONTAINER}"
fi

if [[ "${SKIP_SEED}" -ne 1 ]]; then
  info "seeding AMCL initial poses for ${SCENARIO}"
  docker exec \
    -e SCENARIO="${SCENARIO}" \
    -e WAREHOUSE_CONFIG_DIR=/ws/config \
    -e WAREHOUSE_ENV="${WAREHOUSE_ENV_VALUE}" \
    "${CONTAINER}" bash -lc \
    'set -euo pipefail; source /opt/ros/jazzy/setup.bash; source /ws/ws/install/setup.bash; cd /ws; scripts/slice3_seed_initialpose.sh'
fi

url_host="${BIND}"
if [[ "${BIND}" == "127.0.0.1" || "${BIND}" == "0.0.0.0" ]]; then
  url_host="localhost"
fi

cat <<EOF
PASS   Mode-A live stack launched
INFO   noVNC/RViz: http://${url_host}:${PORT}  (login: ubuntu / ubuntu)
INFO   log inside container: ${LOG_FILE}
INFO   Nav2 Bridge health: docker exec ${CONTAINER} curl -fsS http://localhost:8645/health
INFO   Hermes from container: ${CONTAINER_HERMES_URL}
EOF
