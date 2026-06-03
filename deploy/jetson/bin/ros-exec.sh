#!/usr/bin/env bash
# =============================================================================
# ros-exec.sh — source ROS 2 + the warehouse colcon workspace, then exec a node.
#
# systemd services have no ROS environment; every warehouse-*.service runs its
# node through this wrapper so the underlay (/opt/ros/$ROS_DISTRO) and the repo
# overlay (${WAREHOUSE_WS}/install) are sourced identically. Env comes from
# /etc/warehouse/warehouse.env (systemd EnvironmentFile); see env/warehouse.env.example.
#
# Usage (from a unit): ExecStart=/opt/warehouse/deploy/jetson/bin/ros-exec.sh ros2 run <pkg> <exe>
#
# Source of truth: docs/architecture/19-environments-and-config.md,
# docs/setup/jetson-deploy.md.
# =============================================================================
set -euo pipefail

ROS_DISTRO="${ROS_DISTRO:-jazzy}"
ros_setup="/opt/ros/${ROS_DISTRO}/setup.bash"

if [[ ! -f "${ros_setup}" ]]; then
  echo "ros-exec: ROS 2 underlay not found at ${ros_setup} (is ROS ${ROS_DISTRO} installed?)" >&2
  exit 1
fi
# shellcheck source=/dev/null
source "${ros_setup}"

# Workspace overlay is optional only so this wrapper stays runnable before the
# first colcon build; in prod the build is a deploy prerequisite (jetson-deploy.md).
ws_setup="${WAREHOUSE_WS:-/opt/warehouse/ws}/install/setup.bash"
if [[ -f "${ws_setup}" ]]; then
  # shellcheck source=/dev/null
  source "${ws_setup}"
else
  echo "ros-exec: warning — workspace overlay ${ws_setup} missing; running with underlay only" >&2
fi

if [[ "$#" -eq 0 ]]; then
  echo "ros-exec: no command given" >&2
  exit 64
fi

exec "$@"
