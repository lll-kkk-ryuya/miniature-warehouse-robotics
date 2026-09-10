#!/usr/bin/env bash
# =============================================================================
# record-run.sh — record a real run: bag + node/topic/param snapshot + provenance.
#
# Thin wrapper: source the ROS 2 underlay and the workspace overlay, then hand
# every argument to mwr_run_record.py. Observe-only — neither this script nor the
# Python it execs launches the stack, publishes anything, or touches a systemd
# unit. Start the stack in another terminal first, then record it here.
#
#   deploy/jetson/bin/record-run.sh --operator ryu --purpose "M1 first drive"
#   deploy/jetson/bin/record-run.sh --duration 120 --slug joystick
#   deploy/jetson/bin/record-run.sh --dry-run     # print the record, touch no ROS
#
# The overlay to source follows --ws (or $WAREHOUSE_WS, else /opt/warehouse/ws)
# so the recorder reads the build-info of the workspace it is sourcing.
#
# Source of truth: docs/jetson/03-build-deploy-run-and-run-records.md;
# run context: docs/setup/jetson-deploy.md §3/§8.
# =============================================================================
# NOTE: `-u` is deliberately absent — ROS/colcon setup scripts read unset vars.
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROS_SETUP="/opt/ros/${ROS_DISTRO:-humble}/setup.bash"

# Minimal scan for --ws (both `--ws PATH` and `--ws=PATH`); mwr_run_record.py
# parses the arguments for real.
WS="${WAREHOUSE_WS:-/opt/warehouse/ws}"
prev=""
for arg in "$@"; do
  case "${arg}" in
    --ws=*) WS="${arg#--ws=}" ;;
  esac
  if [[ "${prev}" == "--ws" ]]; then
    WS="${arg}"
  fi
  prev="${arg}"
done

if [[ -f "${ROS_SETUP}" ]]; then
  # shellcheck disable=SC1090
  source "${ROS_SETUP}"
else
  echo "record-run.sh: WARNING no ROS underlay at ${ROS_SETUP} (continuing)" >&2
fi

if [[ -f "${WS}/install/setup.bash" ]]; then
  # shellcheck disable=SC1091
  source "${WS}/install/setup.bash"
else
  echo "record-run.sh: WARNING no overlay at ${WS}/install/setup.bash (build it first)" >&2
fi

exec python3 "${SCRIPT_DIR}/mwr_run_record.py" "$@"
