#!/usr/bin/env bash
# Preflight the live Hermes Gateway path used by the local Gazebo/Mode-A stack.
#
# This script intentionally never prints secret values. It reads the Bridge-side
# env file, verifies that a Gateway token is present, checks Hermes health, and
# checks an authenticated OpenAI-compatible endpoint before a paid/live run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

WAREHOUSE_ENV_VALUE="${WAREHOUSE_ENV:-dev}"
ENV_FILE="${MWR_HERMES_ENV_FILE:-${REPO_ROOT}/config/${WAREHOUSE_ENV_VALUE}/.env}"
HERMES_BASE_URL_EXPLICIT=0
CONTAINER_HERMES_URL_EXPLICIT=0
if [[ -n "${HERMES_BASE_URL:-}" ]]; then
  HERMES_BASE_URL_EXPLICIT=1
fi
if [[ -n "${WAREHOUSE__HERMES__BASE_URL:-}" ]]; then
  CONTAINER_HERMES_URL_EXPLICIT=1
fi
HERMES_BASE_URL="${HERMES_BASE_URL:-http://127.0.0.1:8642}"
CONTAINER_HERMES_URL="${WAREHOUSE__HERMES__BASE_URL:-}"
CONTAINER="${MWR_SIM_CONTAINER:-}"
CHECK_CONTAINER=1
CHAT_CHECK=0

usage() {
  cat <<'EOF'
Usage:
  deploy/dev/check-hermes-live.sh [options]

Options:
  --env-file PATH      Bridge-side env file. Default: config/$WAREHOUSE_ENV/.env
  --base-url URL       Hermes URL from the host. Default: http://127.0.0.1:8642
  --container NAME     Also verify the container can reach Hermes through host.docker.internal.
  --skip-container     Do not run the container reachability check.
  --chat               Also run a minimal /v1/chat/completions smoke. This can spend provider quota.
  -h, --help           Show this help.

Environment:
  WAREHOUSE_ENV        dev/stg/prod selector for the default env file. Default: dev
  MWR_HERMES_ENV_FILE  Same as --env-file.
  HERMES_BASE_URL      Same as --base-url.
  WAREHOUSE__HERMES__BASE_URL  Container-side Hermes URL override.
  MWR_SIM_CONTAINER    Same as --container.

Exit codes:
  0 = usable, non-zero = categorized preflight failure.
EOF
}

pass() { printf 'PASS   %s\n' "$*"; }
fail() { printf 'FAIL   %s\n' "$*" >&2; exit 1; }
info() { printf 'INFO   %s\n' "$*"; }

container_hermes_url_for_host() {
  local url="${1%/}"
  local scheme authority suffix
  if [[ "${url}" =~ ^([A-Za-z][A-Za-z0-9+.-]*://)([^/?#]*)(.*)$ ]]; then
    scheme="${BASH_REMATCH[1]}"
    authority="${BASH_REMATCH[2]}"
    suffix="${BASH_REMATCH[3]}"
    case "${authority}" in
      localhost|127.0.0.1|0.0.0.0)
        authority="host.docker.internal"
        ;;
      localhost:*|127.0.0.1:*|0.0.0.0:*)
        authority="host.docker.internal:${authority##*:}"
        ;;
    esac
    printf '%s%s%s\n' "${scheme}" "${authority}" "${suffix}"
    return 0
  fi
  printf '%s\n' "${url}"
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --env-file)
      [[ "$#" -ge 2 ]] || fail "--env-file requires a path"
      ENV_FILE="$2"
      shift 2
      ;;
    --base-url)
      [[ "$#" -ge 2 ]] || fail "--base-url requires a URL"
      HERMES_BASE_URL="$2"
      HERMES_BASE_URL_EXPLICIT=1
      shift 2
      ;;
    --container)
      [[ "$#" -ge 2 ]] || fail "--container requires a container name"
      CONTAINER="$2"
      CHECK_CONTAINER=1
      shift 2
      ;;
    --skip-container)
      CHECK_CONTAINER=0
      shift
      ;;
    --chat)
      CHAT_CHECK=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

REQUESTED_HERMES_BASE_URL="${HERMES_BASE_URL}"
REQUESTED_CONTAINER_HERMES_URL="${CONTAINER_HERMES_URL}"

if [[ -f "${ENV_FILE}" ]]; then
  pass "Bridge env file exists: ${ENV_FILE}"
  # shellcheck source=/dev/null
  source "${ENV_FILE}"
else
  if [[ -z "${API_SERVER_KEY:-}" && -z "${HERMES_API_KEY:-}" ]]; then
    fail "Bridge env file missing: ${ENV_FILE}. Copy config/${WAREHOUSE_ENV_VALUE}/.env.example and set API_SERVER_KEY, or export API_SERVER_KEY/HERMES_API_KEY."
  fi
  info "Bridge env file not found; using API_SERVER_KEY/HERMES_API_KEY from the current environment"
fi

if [[ "${HERMES_BASE_URL_EXPLICIT}" -eq 1 ]]; then
  HERMES_BASE_URL="${REQUESTED_HERMES_BASE_URL}"
fi
if [[ "${CONTAINER_HERMES_URL_EXPLICIT}" -eq 1 ]]; then
  CONTAINER_HERMES_URL="${REQUESTED_CONTAINER_HERMES_URL}"
elif [[ -n "${WAREHOUSE__HERMES__BASE_URL:-}" ]]; then
  CONTAINER_HERMES_URL="${WAREHOUSE__HERMES__BASE_URL}"
fi
if [[ -z "${CONTAINER_HERMES_URL}" ]]; then
  CONTAINER_HERMES_URL="$(container_hermes_url_for_host "${HERMES_BASE_URL}")"
fi

if [[ -z "${API_SERVER_KEY:-}" && -n "${HERMES_API_KEY:-}" ]]; then
  API_SERVER_KEY="${HERMES_API_KEY}"
fi
if [[ -z "${HERMES_API_KEY:-}" && -n "${API_SERVER_KEY:-}" ]]; then
  HERMES_API_KEY="${API_SERVER_KEY}"
fi
export API_SERVER_KEY HERMES_API_KEY

if [[ -z "${API_SERVER_KEY:-}" ]]; then
  fail "API_SERVER_KEY/HERMES_API_KEY is empty in ${ENV_FILE}. It must match Hermes Gateway's API_SERVER_KEY."
fi
pass "Bridge API token is present (value hidden)"

if ! command -v curl >/dev/null 2>&1; then
  fail "curl is required"
fi

tmp_body="$(mktemp "${TMPDIR:-/tmp}/mwr-hermes-preflight.XXXXXX")"
cleanup() { rm -f "${tmp_body}"; }
trap cleanup EXIT

http_code() {
  local method="$1" url="$2"
  shift 2
  curl -sS -o "${tmp_body}" -w '%{http_code}' --max-time 12 -X "${method}" "$@" "${url}" || true
}

base="${HERMES_BASE_URL%/}"
code="$(http_code GET "${base}/health")"
case "${code}" in
  2*) pass "Hermes /health reachable at ${base}" ;;
  000) fail "Hermes /health unreachable at ${base}. Start it with: API_SERVER_ENABLED=true hermes gateway" ;;
  *) fail "Hermes /health returned HTTP ${code} at ${base}" ;;
esac

code="$(http_code GET "${base}/v1/models" -H "Authorization: Bearer ${API_SERVER_KEY}")"
case "${code}" in
  2*) pass "Hermes authenticated /v1/models accepted the Bridge token" ;;
  401|403) fail "Hermes authenticated /v1/models returned HTTP ${code}. API_SERVER_KEY mismatch between Hermes and Bridge env." ;;
  000) fail "Hermes authenticated /v1/models unreachable at ${base}" ;;
  *) fail "Hermes authenticated /v1/models returned HTTP ${code}" ;;
esac

if [[ "${CHAT_CHECK}" -eq 1 ]]; then
  payload='{"model":"hermes-agent","messages":[{"role":"user","content":"Return exactly: ok"}],"max_tokens":8}'
  code="$(http_code POST "${base}/v1/chat/completions" \
    -H "Authorization: Bearer ${API_SERVER_KEY}" \
    -H "Content-Type: application/json" \
    -d "${payload}")"
  case "${code}" in
    2*) pass "Hermes /v1/chat/completions smoke succeeded" ;;
    401|403) fail "Hermes chat smoke returned HTTP ${code}. API_SERVER_KEY mismatch." ;;
    000) fail "Hermes chat smoke unreachable at ${base}" ;;
    *) fail "Hermes chat smoke returned HTTP ${code}. Provider/model config may need attention." ;;
  esac
else
  info "chat smoke skipped (use --chat when you intentionally want a provider call)"
fi

container_base="${CONTAINER_HERMES_URL}"
if [[ "${CHECK_CONTAINER}" -eq 1 && -n "${CONTAINER}" ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    fail "docker is required for --container"
  fi
  running="$(docker inspect -f '{{.State.Running}}' "${CONTAINER}" 2>/dev/null || true)"
  if [[ "${running}" != "true" ]]; then
    fail "container ${CONTAINER} is not running"
  fi
  if docker exec "${CONTAINER}" curl -fsS --max-time 8 "${container_base%/}/health" >/dev/null; then
    pass "container ${CONTAINER} can reach Hermes at ${container_base%/}"
  else
    fail "container ${CONTAINER} cannot reach Hermes at ${container_base%/}. Use host.docker.internal from Docker-on-Mac."
  fi
fi

pass "Hermes live preflight complete"
