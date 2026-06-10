#!/usr/bin/env bash
# Seed AMCL poses after slice3 sim launch so late subscribers see both robots.
set -euo pipefail

BOT1_X="${BOT1_X:-0.2}"
BOT1_Y="${BOT1_Y:-0.8}"
BOT2_X="${BOT2_X:-0.7}"
BOT2_Y="${BOT2_Y:-0.8}"
YAW_Z="${YAW_Z:--0.707106771713121}"
YAW_W="${YAW_W:-0.707106790659974}"
PUB_TIMEOUT="${PUB_TIMEOUT:-10}"

if ! command -v ros2 >/dev/null 2>&1; then
  echo "ros2 command not found; source /opt/ros/jazzy/setup.bash and ws/install/setup.bash first" >&2
  exit 1
fi

publish_pose() {
  local bot="$1"
  local x="$2"
  local y="$3"

  timeout "${PUB_TIMEOUT}" ros2 topic pub --once "/${bot}/initialpose" \
    geometry_msgs/msg/PoseWithCovarianceStamped \
    "{header: {frame_id: map}, pose: {pose: {position: {x: ${x}, y: ${y}, z: 0.0}, orientation: {x: 0.0, y: 0.0, z: ${YAW_Z}, w: ${YAW_W}}}, covariance: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}}"
}

publish_pose bot1 "${BOT1_X}" "${BOT1_Y}"
publish_pose bot2 "${BOT2_X}" "${BOT2_Y}"
