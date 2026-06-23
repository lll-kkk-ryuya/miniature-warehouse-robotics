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
  --offline    Validate host e2e harness and WAREHOUSE_TASKS seed. Default. Inside a container
               with a loopback HERMES_BASE_URL it WARNs to use host.docker.internal.
  --live       Offline checks plus Hermes/Nav2 Bridge /health, a live state.json check (both
               bots present + snapshot freshness), and stack process liveness.

Options:
  --tasks JSON    Demo WAREHOUSE_TASKS seed. Defaults to a known-location two-task seed.
  --skip-tests    Skip pytest tests/e2e/ execution.
  --strict        Treat WARN/SKIP as failure.

Environment:
  WAREHOUSE_TASKS            Used when --tasks is not provided.
  HERMES_BASE_URL            Default: http://127.0.0.1:8642 (in a container use
                             http://host.docker.internal:8642 to reach the host Hermes).
  NAV2_BRIDGE_BASE_URL       Default: http://127.0.0.1:8645
  STATE_FRESHNESS_LIMIT_SEC  --live state.json max age in seconds. Default: 5

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

ros2_probe() {
  if have ros2; then
    printf '%s\n' "PATH"
    return 0
  fi
  local setup="${ROS_SETUP:-/opt/ros/jazzy/setup.bash}"
  if [[ -f "${setup}" ]] &&
    bash -lc "source '${setup}' >/dev/null 2>&1 && command -v ros2 >/dev/null 2>&1"; then
    printf '%s\n' "${setup}"
    return 0
  fi
  return 1
}

in_container() {
  # tiryoh ROS image runs in Docker; the host Hermes is NOT reachable via loopback from here.
  [[ -f /.dockerenv ]] || grep -qaE '(docker|containerd|kubepods)' /proc/1/cgroup 2>/dev/null
}

url_host() {
  # Extract the host from http(s)://host[:port][/path] using parameter expansion (no sed).
  local rest="${1#*://}"
  printf '%s\n' "${rest%%[:/]*}"
}

hermes_container_hint() {
  # Surface the #1 demo-day live-failure mode: inside the tiryoh container, a loopback
  # HERMES_BASE_URL cannot reach a host-side Hermes Gateway. A green --offline run must not
  # mask that. The fix is the config override mechanism (config.py:28,48-66): point the bridge
  # at host.docker.internal. WARN (non-fatal) so --offline still passes while flagging the gap.
  local host
  host="$(url_host "${HERMES_BASE_URL}")"
  if in_container && [[ "${host}" == "127.0.0.1" || "${host}" == "localhost" ]]; then
    warn "container detected + Hermes at ${host}: host Hermes is unreachable via loopback. \
For the full stack: export WAREHOUSE__HERMES__BASE_URL=http://host.docker.internal:8642 \
(and run --live with HERMES_BASE_URL=http://host.docker.internal:8642)."
  fi
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
  # eval_sdk is the domain-free core that warehouse_llm_bridge/tracing.py and the wo modules
  # now import (doc21 Phase 1); keep it on the offline PYTHONPATH alongside the warehouse
  # packages so this precheck's import smoke resolves the transitive dependency.
  printf '%s:%s:%s:%s:%s\n' \
    "${REPO_ROOT}/ws/src/eval_sdk" \
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

# Beyond location membership: the seed must set up a genuine TWO-BOT opposition — two distinct
# tasks sending two bots to two DIFFERENT goals (a single shared destination or a copy-pasted
# task never creates the head-on the demo records). This is a STRUCTURAL precondition, NOT a
# geometric channel-crossing test: the named tasks are PROXY keys (the live head-on is driven by
# COORDINATE goals + route_A/route_B locks, tests/e2e/README.md:46-49), and the documented default
# seed (berth_A->shelf_1 / berth_B->shelf_3) does not geometrically cross one aisle — so asserting
# geometry here would wrongly reject the default. We validate intent, not coordinates.
ids = [task.get("id") for task in tasks]
if len(set(ids)) != len(ids):
    print(f"WAREHOUSE_TASKS has duplicate task ids: {ids}", file=sys.stderr)
    raise SystemExit(1)

degenerate = [task.get("id") for task in tasks if task.get("from") == task.get("to")]
if degenerate:
    print(f"WAREHOUSE_TASKS has zero-length task(s) (from == to): {degenerate}", file=sys.stderr)
    raise SystemExit(1)

destinations = {task.get("to") for task in tasks}
if len(destinations) < 2:
    print(
        "WAREHOUSE_TASKS needs >=2 distinct destinations so two bots get different goals "
        f"(opposing-traffic precondition); got {sorted(destinations)}",
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

check_state_snapshot() {
  # Automates the slice3 live spot-check: read the live state.json via the SAME path the stack
  # writes (FileStateStore -> runtime_dir, doc12:262) and confirm BOTH configured bots are
  # present in a fresh snapshot. A MISSING bot = State Cache omitted it (doc12:293 — a bot whose
  # pose+velocity+battery are not all present is dropped, i.e. the pose_stale case); a STALE
  # top-level timestamp (doc12:288, datetime.now(UTC)) = the 100ms write loop (doc12:284) died.
  # Honors WAREHOUSE_ENV / WAREHOUSE_RUNTIME_DIR so it resolves the same state.json as the run.
  local py="$1"
  PYTHONPATH="$(repo_pythonpath)" STATE_FRESHNESS_LIMIT_SEC="${STATE_FRESHNESS_LIMIT_SEC:-5}" \
    "${py}" - <<'PY'
import os
import sys
from datetime import datetime

from warehouse_interfaces.config import load_config
from warehouse_interfaces.schemas import StateSnapshot
from warehouse_interfaces.stores import FileStateStore

try:
    raw = FileStateStore().read()
except Exception as exc:  # FileStateStore.read() only guards FileNotFoundError; a corrupt /
    print(f"state.json unreadable: {exc}", file=sys.stderr)  # truncated file raises JSONDecodeError
    raise SystemExit(1)
if raw is None:
    print("state.json not written yet (State Cache idle or not running)", file=sys.stderr)
    raise SystemExit(1)
try:
    snap = StateSnapshot.model_validate(raw)
except Exception as exc:
    print(f"state.json is not a valid StateSnapshot: {exc}", file=sys.stderr)
    raise SystemExit(1)

try:
    expected = {r["id"] for r in load_config().get("robots", [])}
except Exception:
    expected = set()
expected = expected or {"bot1", "bot2"}
present = set(snap.robots)
missing = expected - present
if missing:
    print(
        f"state.json missing bot(s) {sorted(missing)} (present={sorted(present)}): "
        "initialpose not seeded yet or pose_stale dropped a bot",
        file=sys.stderr,
    )
    raise SystemExit(1)

try:
    ts = datetime.fromisoformat(snap.timestamp)
except ValueError as exc:
    print(f"state.json timestamp unparseable: {exc}", file=sys.stderr)
    raise SystemExit(1)
now = datetime.now(ts.tzinfo) if ts.tzinfo else datetime.now()
age = (now - ts).total_seconds()
# 5s default is a deliberate liveness FLOOR (~50 missed 100ms cycles = State Cache clearly
# dead), intentionally looser than doc12:350-351's Policy Gate stale(0.5s)/unavailable(2.0s)
# bands, which gate command ACCEPTANCE, not process liveness. Override via env if needed.
limit = float(os.environ["STATE_FRESHNESS_LIMIT_SEC"])
if age > limit:
    print(
        f"state.json is stale by {age:.1f}s (> {limit:.0f}s): State Cache stopped writing?",
        file=sys.stderr,
    )
    raise SystemExit(1)
print(f"both bots present ({sorted(present)}), snapshot {age:.1f}s old")
PY
}

check_process_liveness() {
  # Supplementary to the state.json freshness check (which authoritatively proves State Cache
  # is alive): pgrep the stack's anchor processes. WARN (not fail) — the precheck may run in a
  # different shell/namespace than the launch, and process names vary by ROS distro.
  if ! have pgrep; then
    skip "process liveness (pgrep unavailable)"
    return
  fi
  local proc
  for proc in "gz sim" "state_cache"; do
    if pgrep -f "${proc}" >/dev/null 2>&1; then
      pass "process alive: ${proc}"
    else
      warn "process not found: ${proc} (stack not running in this namespace?)"
    fi
  done
}

run_static_checks() {
  printf '== slice3 offline precheck ==\n'
  [[ -f "${REPO_ROOT}/tests/e2e/README.md" ]] && pass "tests/e2e runbook exists" || fail "tests/e2e runbook missing"
  [[ -f "${REPO_ROOT}/tests/e2e/test_slice2_yield_forward.py" ]] && pass "slice2 e2e harness exists" || fail "slice2 e2e harness missing"
  [[ -f "${REPO_ROOT}/ws/src/warehouse_llm_bridge/warehouse_llm_bridge/scheduler.py" ]] && pass "llm scheduler exists" || fail "llm scheduler missing"
  [[ -f "${REPO_ROOT}/ws/src/warehouse_bringup/launch/bringup.launch.py" ]] && pass "bringup.launch.py exists" || fail "bringup.launch.py missing"

  # The full-stack record command depends on scenario/rviz_config reaching the sim include; assert
  # the pass-through forward is still wired (the demo-breaking gap #156/#204 closed). The unit
  # test test_bringup_launch.py:152-165 guards it in CI — this is the operator's last static gate
  # before a ~paid live run, where a silently-dropped forward records the berth spawn instead.
  local bringup="${REPO_ROOT}/ws/src/warehouse_bringup/launch/bringup.launch.py"
  if grep -q '"scenario": LaunchConfiguration' "${bringup}" &&
    grep -q '"rviz_config": LaunchConfiguration' "${bringup}"; then
    pass "bringup forwards scenario/rviz_config to sim (record knobs wired)"
  else
    fail "bringup does NOT forward scenario/rviz_config to sim — head_on/record would be dropped"
  fi

  # seed_initialpose and the --live state.json snapshot check must agree on the bot namespaces; a
  # rename in one that desyncs from the other would seed/assert the wrong bot while staying green.
  local seed="${REPO_ROOT}/scripts/slice3_seed_initialpose.sh"
  if grep -q 'publish_pose bot1' "${seed}" && grep -q 'publish_pose bot2' "${seed}"; then
    pass "seed_initialpose targets bot1/bot2 (matches state snapshot check)"
  else
    warn "seed_initialpose bot namespaces not bot1/bot2 as expected (snapshot check may desync)"
  fi

  local py
  if ! py="$(python_cmd)"; then
    fail "python3.12 or python>=3.11 is required"
    return
  fi
  pass "python selected: ${py}"

  local tasks="${TASKS_JSON:-${DEFAULT_TASKS}}"
  local normalized
  if normalized="$(validate_tasks "${py}" "${tasks}")"; then
    pass "WAREHOUSE_TASKS seed validates (PendingTask + KNOWN_LOCATIONS + two-bot opposition)"
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

  local ros2_source
  if ros2_source="$(ros2_probe)"; then
    if [[ "${ros2_source}" == "PATH" ]]; then
      pass "ros2 command available"
    else
      pass "ros2 command available after sourcing ${ros2_source}"
    fi
  else
    skip "ros2 command not available on this host; run launch commands inside tiryoh ROS container"
  fi

  hermes_container_hint
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
    # Containerized + loopback is the classic miss: probe host.docker.internal so the operator
    # learns Hermes IS up, just at the wrong address (the config override resolves it).
    local hhost
    hhost="$(url_host "${HERMES_BASE_URL}")"
    if in_container && [[ "${hhost}" == "127.0.0.1" || "${hhost}" == "localhost" ]] &&
      check_http_health "${py}" "Hermes" "http://host.docker.internal:8642" >/dev/null 2>&1; then
      warn "Hermes IS reachable at http://host.docker.internal:8642 — export \
WAREHOUSE__HERMES__BASE_URL=http://host.docker.internal:8642 (and rerun --live with \
HERMES_BASE_URL=http://host.docker.internal:8642)."
    fi
  fi

  if output="$(check_http_health "${py}" "Nav2 Bridge" "${NAV2_BRIDGE_BASE_URL}" 2>&1)"; then
    pass "Nav2 Bridge /health reachable at ${NAV2_BRIDGE_BASE_URL}"
  else
    fail "Nav2 Bridge /health unreachable at ${NAV2_BRIDGE_BASE_URL}: ${output}"
  fi

  # Stack health (requires the launch to be running + initialpose seeded): both bots in a
  # fresh state.json (automates the post-197 manual spot-check) + anchor-process liveness.
  if output="$(check_state_snapshot "${py}" 2>&1)"; then
    pass "state.json: ${output}"
  else
    fail "state.json check: ${output}"
  fi

  check_process_liveness
}

print_next_steps() {
  printf '\n== launch commands ==\n'
  printf "export WAREHOUSE_TASKS='%s'\n" "${TASKS_JSON:-${DEFAULT_TASKS}}"
  cat <<'EOF'
export WAREHOUSE_CONFIG_DIR=/ws/config
export WAREHOUSE_ENV=dev
# sim idle only: AMCL may publish initial pose once, so avoid false pose_stale while recording.
export WAREHOUSE__SAFETY__POSE_FRESHNESS_TIMEOUT=999
# Source ROS before every launch shell. The cockpit image has ROS installed but non-login shells
# do not put ros2 on PATH until this is sourced.
source /opt/ros/jazzy/setup.bash
if [ -f /ws/ws/install/setup.bash ]; then source /ws/ws/install/setup.bash; fi

# slice1 health (Hermes not required)
ros2 launch warehouse_bringup bringup.launch.py llm:=false sim:=true
# In another ROS-sourced shell after both Nav2 lifecycle managers report active:
cd /ws && scripts/slice3_seed_initialpose.sh

# slice2/3 full stack. ONLY Hermes Gateway :8642 is pre-started (step 0); Nav2 Bridge :8645 is
# composed IN-PROCESS by this launch (llm:=true + traffic_mode in {none,simple}) — do NOT pre-start
# it or :8645 double-binds and the launch fails. Inside tiryoh, reach the host Hermes (loopback
# won't) via the config override:
export WAREHOUSE__HERMES__BASE_URL=http://host.docker.internal:8642
# scenario:=head_on records the 200mm head-on standoff; rviz_config:=record selects the overview
# RViz cfg (bringup forwards both to the sim — slice3). Without them the recording shows berths.
ros2 launch warehouse_bringup bringup.launch.py sim:=true llm:=true traffic_mode:=none rviz:=true scenario:=head_on rviz_config:=record
# Re-seed after the full-stack launch reaches active lifecycle. head_on spawns on the aisle
# centreline (NOT the berths) — SCENARIO=head_on seeds the matching poses or AMCL mislocalizes.
cd /ws && SCENARIO=head_on scripts/slice3_seed_initialpose.sh
# Wrap the noVNC/screen capture (actual recording is human-gated):
scripts/slice3_record.sh start    # ... run the demo ... then: scripts/slice3_record.sh stop
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
