#!/usr/bin/env bash
# =============================================================================
# slice3_inject_swap.sh - drive the head-on coordinate swap via the Nav2 Bridge REST API.
#
# The capstone's ≥0.15m head-on demo (doc mode-a/11a:431-466 §9) needs the two bots to
# swap ends through the SAME 200mm aisle-A pinch, which admits one bot at a time. This
# script is the LIVE operator wrapper for that swap (the unit-tested in-process path is
# warehouse_nav2_bridge.head_on_injector.HeadOnInjector):
#
#   1. derive the swap goals from the sim's documented ``head_on_goals`` DATA export
#      (warehouse_sim.scenarios — the sanctioned capstone hand-off, NOT a package import;
#      scenarios.py:18-21 exports the coords "so the capstone can read the coords");
#   2. POST bot1's coordinate goal to ``/api/v1/navigate`` (``{"robot":..,"goal":[x,y]}``);
#   3. WAIT for bot1 to clear the pinch (poll ``/api/v1/status`` until it is no longer
#      "navigating" — operational serialization = the second bot never enters while the
#      first is inside, so they stay ≥0.15m apart, 11a:446);
#   4. POST bot2's coordinate goal.
#
# The goals are inline pinch-aligned coordinates (11a:455), NOT KNOWN_LOCATIONS names, so
# they use the bridge's additive coordinate ``goal=`` path (core.py). The Nav2 Bridge
# :8645 must already be up (bringup.launch.py in-process composes it; do NOT start a second
# one — tests/e2e/README step 0 / nav2_bridge.py:41 DEFAULT_PORT=8645 double-bind).
#
# DRY_RUN=1 derives the goals and prints the planned requests WITHOUT calling the API or
# needing ros2/curl — pure python, runs on host (mirrors slice3_seed_initialpose.sh).
#
# Env:
#   BRIDGE_URL   Nav2 Bridge base (default http://127.0.0.1:8645)
#   POLL_TIMEOUT seconds to wait for bot1 to clear the pinch (default 60)
#   POLL_PERIOD  status poll period seconds (default 1)
#   PYTHON_BIN / WAREHOUSE_CONFIG_DIR / WAREHOUSE_ENV — as slice3_seed_initialpose.sh
# =============================================================================
set -euo pipefail

BRIDGE_URL="${BRIDGE_URL:-http://127.0.0.1:8645}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"
POLL_PERIOD="${POLL_PERIOD:-1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

pick_python() {
  # Prefer python3.12; else a python3 new enough to import warehouse_sim (PEP 604 / 3.10+).
  if [[ -n "${PYTHON_BIN:-}" ]]; then printf '%s\n' "${PYTHON_BIN}"; return 0; fi
  if command -v python3.12 >/dev/null 2>&1; then printf '%s\n' "python3.12"; return 0; fi
  if command -v python3 >/dev/null 2>&1 &&
    python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    printf '%s\n' "python3"; return 0
  fi
  return 1
}

derive_goals() {
  # Read the sim's documented head_on_goals DATA export and print, per principal bot,
  # "<bot> <x> <y>" (one line each). Pure python (no ROS/gz), so it runs anywhere the
  # workspace is importable. PYTHONPATH covers only the read-only shared/sim packages.
  local py
  py="$(pick_python)" || return 1
  PYTHONPATH="${REPO_ROOT}/ws/src/warehouse_sim:${REPO_ROOT}/ws/src/warehouse_interfaces:${REPO_ROOT}/ws/src/warehouse_description:${PYTHONPATH:-}" \
    WAREHOUSE_CONFIG_DIR="${WAREHOUSE_CONFIG_DIR:-${REPO_ROOT}/config}" \
    WAREHOUSE_ENV="${WAREHOUSE_ENV:-dev}" \
    "${py}" - <<'PY'
from warehouse_sim.scenarios import head_on_goals

goals = head_on_goals()
for bot in list(goals)[:2]:
    x, y, _yaw = goals[bot]  # yaw dropped: the bridge goal is (x, y) (backend.Pose)
    print(f"{bot} {x:.6f} {y:.6f}")
PY
}

# -- Resolve the two swap goals --------------------------------------------------
if ! _goals_out="$(derive_goals)" || [[ -z "${_goals_out}" ]]; then
  echo "ERROR: could not derive head_on_goals from warehouse_sim.scenarios." \
    "Fix WAREHOUSE_CONFIG_DIR / PYTHON_BIN / PYTHONPATH (warehouse_sim must be importable)." >&2
  exit 2
fi
{ IFS= read -r _g1 || true; IFS= read -r _g2 || true; } <<EOF
${_goals_out}
EOF
read -r BOT1 BOT1_X BOT1_Y <<<"${_g1}"
read -r BOT2 BOT2_X BOT2_Y <<<"${_g2}"
if [[ -z "${BOT1:-}" || -z "${BOT2:-}" ]]; then
  echo "ERROR: expected two principal bot goals from head_on_goals; got:" >&2
  printf '%s\n' "${_goals_out}" >&2
  exit 2
fi

goal_body() { printf '{"robot":"%s","goal":[%s,%s]}' "$1" "$2" "$3"; }

if [[ -n "${DRY_RUN:-}" ]]; then
  echo "BRIDGE_URL=${BRIDGE_URL}"
  echo "swap: ${BOT1} -> (${BOT1_X}, ${BOT1_Y}) then (after ${BOT1} clears) ${BOT2} -> (${BOT2_X}, ${BOT2_Y})"
  echo "POST ${BRIDGE_URL}/api/v1/navigate $(goal_body "${BOT1}" "${BOT1_X}" "${BOT1_Y}")"
  echo "POST ${BRIDGE_URL}/api/v1/navigate $(goal_body "${BOT2}" "${BOT2_X}" "${BOT2_Y}")"
  exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl not found (needed to call the Nav2 Bridge REST API)" >&2
  exit 1
fi

post_goal() {
  local robot="$1" x="$2" y="$3"
  echo "navigate ${robot} -> (${x}, ${y})"
  curl -fsS -X POST "${BRIDGE_URL}/api/v1/navigate" \
    -H 'Content-Type: application/json' \
    -d "$(goal_body "${robot}" "${x}" "${y}")"
  echo
}

nav_status() {
  # Echo the robot's nav_status field, or empty on any error (treated as not-yet-ready).
  curl -fsS "${BRIDGE_URL}/api/v1/status/$1" 2>/dev/null |
    "$(pick_python)" -c 'import json,sys;print(json.load(sys.stdin).get("nav_status",""))' 2>/dev/null || true
}

wait_until_clear() {
  # Wait for ${1} to clear the pinch, distinguishing the terminal outcomes (nav2_bridge.py:103-107
  # emits exactly "succeeded"/"failed"):
  #   return 0  — goal SUCCEEDED ⇒ ${1} reached the south staging ⇒ it left the pinch (safe to swap).
  #   return 2  — goal FAILED. A Nav2 abort (the #144 head-on stall is exactly this) can leave ${1}
  #               stalled INSIDE the 200mm pinch, so "failed" is NOT a clear — dispatching the waiter
  #               then would co-occupy the channel. Fail hard (the script header's fail-closed claim /
  #               #218 B1), do not treat a failed goal as an exit.
  #   return 1  — POLL_TIMEOUT (still "navigating"/"waiting"/idle/unreported ⇒ never confirmed clear).
  local robot="$1" waited=0 st
  while (( waited < POLL_TIMEOUT )); do
    st="$(nav_status "${robot}")"
    case "${st}" in
      succeeded)
        echo "${robot} cleared the aisle (nav_status=succeeded)"
        return 0
        ;;
      failed)
        echo "ERROR: ${robot} goal FAILED (nav_status=failed) — a Nav2 abort can leave it stalled" \
          "inside the 200mm pinch; refusing to dispatch the waiter into a possible co-occupancy." >&2
        return 2
        ;;
    esac
    sleep "${POLL_PERIOD}"
    waited=$(( waited + POLL_PERIOD ))
  done
  return 1
}

# -- Drive the serialized swap ---------------------------------------------------
post_goal "${BOT1}" "${BOT1_X}" "${BOT1_Y}"     # first bot acquires the pinch
# FAIL CLOSED: only dispatch ${BOT2} once ${BOT1}'s goal SUCCEEDED (it reached the south staging ⇒
# exited the pinch). A POLL_TIMEOUT or a FAILED goal can leave ${BOT1} inside the 200mm pinch —
# dispatching ${BOT2} then is the head-on co-occupancy this serialization exists to prevent (11a:446).
# Mirror slice3_seed_initialpose.sh:94-105 (#218 B1) and the in-process HeadOnInjector, which never
# dispatch the waiter until the lock is genuinely free — refuse rather than fall open.
wait_until_clear "${BOT1}" || {
  rc=$?
  if (( rc == 1 )); then
    echo "ERROR: ${BOT1} did not clear aisle-A within ${POLL_TIMEOUT}s; refusing to dispatch" \
      "${BOT2} into the pinch (would co-occupy the 200mm channel). Investigate ${BOT1}" \
      "(stuck / replanning) or raise POLL_TIMEOUT, then re-run." >&2
  fi
  exit 2  # rc==2 (FAILED) already printed its own diagnostic in wait_until_clear
}
post_goal "${BOT2}" "${BOT2_X}" "${BOT2_Y}"     # then swaps through (the mouth is now clear)
