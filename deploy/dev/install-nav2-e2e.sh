#!/usr/bin/env bash
# Provision the Nav2 + twist_mux (+ SLAM) stack into the dev/sim container for the
# #8 nav-traffic 2-bot Gazebo E2E (#67). The base image `tiryoh/ros2-desktop-vnc:jazzy`
# ships ros_gz only (the #7 sim spike installed gz-sim; see
# ws/src/warehouse_sim/spike/run_spike.sh) and has NO Nav2 — `ros2 pkg prefix
# nav2_common` fails there. This script adds exactly what `nav2_bringup.launch.py`
# launch-time exec_depends require (ws/src/warehouse_bringup/package.xml).
#
# Re-runnable. Run INSIDE a ROS 2 Jazzy container, or via the docker-exec wrapper:
#   docker exec <ctr> bash /ws/deploy/dev/install-nav2-e2e.sh
# (mount the repo at /ws, like the sim spike). See deploy/dev/README.md.
#
# NOTE: ~hundreds-of-MB on top of the 8.3GB base. E2E run + map gen still need the
# sim-side /clock + a world map — tracked by #76 (sim). This only provisions packages.
set -euo pipefail

PACKAGES=(
  # Nav2 stack — mirrors warehouse_bringup/package.xml exec_depend (the metapackage
  # ros-jazzy-navigation2 pulls amcl/controller/planner/behaviors/bt_navigator/
  # map_server/lifecycle_manager/nav2_common + nav2_mppi_controller).
  ros-jazzy-navigation2
  ros-jazzy-nav2-bringup        # reference launches (optional but handy for E2E)
  ros-jazzy-twist-mux           # the muxer node (twist_mux.yaml)
  ros-jazzy-slam-toolbox        # Phase 2a map generation (doc09:117-121); optional for a pre-made map
)

echo "[install-nav2-e2e] ROS_DISTRO=${ROS_DISTRO:-<unset>}"
if [[ "${ROS_DISTRO:-}" != "jazzy" ]]; then
  echo "[install-nav2-e2e] WARNING: expected ROS_DISTRO=jazzy; continuing anyway." >&2
fi

SUDO=""
[[ "$(id -u)" -ne 0 ]] && SUDO="sudo"

$SUDO apt-get update
$SUDO apt-get install -y --no-install-recommends "${PACKAGES[@]}"

echo "[install-nav2-e2e] verifying key packages resolve..."
# shellcheck disable=SC1090
source "/opt/ros/${ROS_DISTRO:-jazzy}/setup.bash"
for p in nav2_common nav2_mppi_controller nav2_amcl twist_mux; do
  if ros2 pkg prefix "$p" >/dev/null 2>&1; then
    echo "  ok  $p"
  else
    echo "  MISSING  $p" >&2
    exit 1
  fi
done
echo "[install-nav2-e2e] done. Build the workspace next: colcon build (in /ws/ws)."
