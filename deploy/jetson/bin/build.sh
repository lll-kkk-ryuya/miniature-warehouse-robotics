#!/usr/bin/env bash
# =============================================================================
# build.sh — build the workspace on the board, with a record of what was built.
#
# Thin wrapper: source the ROS 2 underlay, then hand every argument to
# mwr_build.py, which does the real work (safety guard -> git facts -> rosdep
# check -> colcon build -> build-info JSON). Use this instead of typing
# `colcon build` by hand, so every build leaves a record behind.
#
# It installs nothing and it NEVER enables/starts/restarts a unit: build is not
# deploy and not run — the switch-over is a separate step
# (docs/setup/jetson-deploy.md §3 install / §8 switch-over).
#
#   deploy/jetson/bin/build.sh                 # dev: colcon build --symlink-install
#   deploy/jetson/bin/build.sh --profile prod  # prod: plain build, clean + tagged only
#   deploy/jetson/bin/build.sh --dry-run       # print the record, build nothing
#
# Source of truth: docs/jetson/03-build-deploy-run-and-run-records.md.
# =============================================================================
# NOTE: `-u` is deliberately absent. The upstream ROS/colcon setup scripts read
# unset variables (AMENT_TRACE_SETUP_FILES, _colcon_prefix_chain_*), so a shell
# with `set -u` dies inside `source /opt/ros/<distro>/setup.bash`.
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROS_SETUP="/opt/ros/${ROS_DISTRO:-humble}/setup.bash"

if [[ -f "${ROS_SETUP}" ]]; then
  # shellcheck disable=SC1090
  source "${ROS_SETUP}"
else
  echo "build.sh: WARNING no ROS underlay at ${ROS_SETUP} (continuing; colcon may be missing)" >&2
fi

exec python3 "${SCRIPT_DIR}/mwr_build.py" "$@"
