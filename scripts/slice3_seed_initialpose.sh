#!/usr/bin/env bash
# =============================================================================
# slice3_seed_initialpose.sh - seed AMCL initial poses after a slice3 sim launch.
#
# Publishes /bot{n}/initialpose once per bot so late subscribers (State Cache, RViz)
# see both robots AND AMCL localizes to the ACTUAL Gazebo spawn.
#
# CRITICAL for the head-on demo: the spawn poses DIFFER per launch scenario. The
# default (berth) sim spawns the two bots at berth_A/berth_B; ``scenario:=head_on``
# spawns them on the aisle-A centreline facing each other. Seeding the WRONG poses
# makes AMCL mislocalize and the recorded demo is garbage (the bots' map pose drifts
# from their real pinch pose). So SCENARIO MUST match the bringup launch:
#
#   SCENARIO=default  (default) seeds berth_A / berth_B, both facing south.
#   SCENARIO=head_on  derives the exact aisle-centreline face-off poses (incl. the
#                     opposing yaws) from the sim's documented ``head_on_spawn_poses``
#                     DATA export (warehouse_sim.scenarios — the sanctioned capstone
#                     hand-off, NOT a package import; scenarios.py says the coords are
#                     exported "so the capstone can read the coords"). The seed thus
#                     always MATCHES the Gazebo spawn and tracks a layout re-survey.
#
# Explicit per-bot overrides take precedence over SCENARIO:
#   BOT{1,2}_X  BOT{1,2}_Y  BOT{1,2}_YAW_Z  BOT{1,2}_YAW_W
# DRY_RUN=1 prints the resolved poses and exits (no ros2 needed) — used by the host
# regression test tests/e2e/test_slice3_initialpose_seed.py.
# =============================================================================
set -euo pipefail

SCENARIO="${SCENARIO:-default}"
PUB_TIMEOUT="${PUB_TIMEOUT:-10}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# -- Default (berth) scenario poses ------------------------------------------------
# (x, y) mirror config/warehouse.base.yaml locations berth_A / berth_B; both face
# south (-pi/2). Kept as the prior fixed defaults for back-compat with SCENARIO=default.
DEF_BOT1_X="0.2"; DEF_BOT1_Y="0.8"
DEF_BOT2_X="0.7"; DEF_BOT2_Y="0.8"
DEF_BOT1_YAW_Z="-0.707106771713121"; DEF_BOT1_YAW_W="0.707106790659974"
DEF_BOT2_YAW_Z="-0.707106771713121"; DEF_BOT2_YAW_W="0.707106790659974"

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

derive_head_on() {
  # Read the sim's documented head_on_spawn_poses DATA export and print, per principal bot,
  # "x y yaw_z yaw_w" (one line each). Pure python (no ROS/gz), so it runs anywhere the
  # workspace is importable. PYTHONPATH covers only the read-only shared/sim packages.
  local py
  py="$(pick_python)" || return 1
  PYTHONPATH="${REPO_ROOT}/ws/src/warehouse_sim:${REPO_ROOT}/ws/src/warehouse_interfaces:${REPO_ROOT}/ws/src/warehouse_description:${PYTHONPATH:-}" \
    WAREHOUSE_CONFIG_DIR="${WAREHOUSE_CONFIG_DIR:-${REPO_ROOT}/config}" \
    WAREHOUSE_ENV="${WAREHOUSE_ENV:-dev}" \
    "${py}" - <<'PY'
import math

from warehouse_sim.scenarios import head_on_spawn_poses

poses = head_on_spawn_poses()
for bot in list(poses)[:2]:
    x, y, _z, yaw = poses[bot]
    print(f"{x:.6f} {y:.6f} {math.sin(yaw / 2):.12f} {math.cos(yaw / 2):.12f}")
PY
}

# -- Resolve per-bot poses from SCENARIO -------------------------------------------
if [[ "${SCENARIO}" == "head_on" ]]; then
  _h1=""
  _h2=""
  if _ho_out="$(derive_head_on)" && [[ -n "${_ho_out}" ]]; then
    { IFS= read -r _h1 || true; IFS= read -r _h2 || true; } <<EOF
${_ho_out}
EOF
  fi
  if [[ -n "${_h1}" && -n "${_h2}" ]]; then
    read -r DEF_BOT1_X DEF_BOT1_Y DEF_BOT1_YAW_Z DEF_BOT1_YAW_W <<<"${_h1}"
    read -r DEF_BOT2_X DEF_BOT2_Y DEF_BOT2_YAW_Z DEF_BOT2_YAW_W <<<"${_h2}"
  elif [[ -n "${BOT1_X:-}" && -n "${BOT1_Y:-}" && -n "${BOT2_X:-}" && -n "${BOT2_Y:-}" ]]; then
    # Escape hatch: derivation failed but the operator pinned both bots' positions explicitly.
    # Honor them — but for head_on, BOT2 faces NORTH (opposite the berth-south default), so set
    # BOT2_YAW_Z/W too or bot2 localizes facing the wrong way.
    echo "WARN: head_on pose derivation failed; honoring explicit BOT{1,2}_{X,Y,...} overrides." \
      "Set BOT2_YAW_Z/W to the north-facing quat for the head-on standoff." >&2
  else
    # FAIL HARD (B1): silently seeding berth coords under head_on mislocalizes AMCL (~0.25m) — the
    # exact accident this script exists to prevent (the header calls it garbage). Refuse rather than
    # fall back, mirroring the loud `unknown SCENARIO` exit 2 (a silent failure here is far worse).
    echo "ERROR: SCENARIO=head_on could not derive spawn poses from warehouse_sim.scenarios and no" \
      "explicit BOT{1,2}_* override is set. Refusing to seed berth coords under head_on (would" \
      "mislocalize AMCL). Fix WAREHOUSE_CONFIG_DIR / PYTHON_BIN / PYTHONPATH (warehouse_sim must be" \
      "importable), or set BOT{1,2}_{X,Y,YAW_Z,YAW_W} explicitly to the head_on spawn." >&2
    exit 2
  fi
elif [[ "${SCENARIO}" != "default" ]]; then
  echo "unknown SCENARIO='${SCENARIO}' (expected 'default' or 'head_on')" >&2
  exit 2
fi

BOT1_X="${BOT1_X:-${DEF_BOT1_X}}"; BOT1_Y="${BOT1_Y:-${DEF_BOT1_Y}}"
BOT2_X="${BOT2_X:-${DEF_BOT2_X}}"; BOT2_Y="${BOT2_Y:-${DEF_BOT2_Y}}"
BOT1_YAW_Z="${BOT1_YAW_Z:-${DEF_BOT1_YAW_Z}}"; BOT1_YAW_W="${BOT1_YAW_W:-${DEF_BOT1_YAW_W}}"
BOT2_YAW_Z="${BOT2_YAW_Z:-${DEF_BOT2_YAW_Z}}"; BOT2_YAW_W="${BOT2_YAW_W:-${DEF_BOT2_YAW_W}}"

if [[ -n "${DRY_RUN:-}" ]]; then
  printf 'SCENARIO=%s\n' "${SCENARIO}"
  printf 'bot1 x=%s y=%s yaw_z=%s yaw_w=%s\n' "${BOT1_X}" "${BOT1_Y}" "${BOT1_YAW_Z}" "${BOT1_YAW_W}"
  printf 'bot2 x=%s y=%s yaw_z=%s yaw_w=%s\n' "${BOT2_X}" "${BOT2_Y}" "${BOT2_YAW_Z}" "${BOT2_YAW_W}"
  exit 0
fi

if ! command -v ros2 >/dev/null 2>&1; then
  echo "ros2 command not found; source /opt/ros/jazzy/setup.bash and ws/install/setup.bash first" >&2
  exit 1
fi

publish_pose() {
  local bot="$1"
  local x="$2"
  local y="$3"
  local yaw_z="$4"
  local yaw_w="$5"

  timeout "${PUB_TIMEOUT}" ros2 topic pub --once "/${bot}/initialpose" \
    geometry_msgs/msg/PoseWithCovarianceStamped \
    "{header: {frame_id: map}, pose: {pose: {position: {x: ${x}, y: ${y}, z: 0.0}, orientation: {x: 0.0, y: 0.0, z: ${yaw_z}, w: ${yaw_w}}}, covariance: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}}"
}

publish_pose bot1 "${BOT1_X}" "${BOT1_Y}" "${BOT1_YAW_Z}" "${BOT1_YAW_W}"
publish_pose bot2 "${BOT2_X}" "${BOT2_Y}" "${BOT2_YAW_Z}" "${BOT2_YAW_W}"
