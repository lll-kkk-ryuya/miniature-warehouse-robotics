#!/usr/bin/env bash
# Live test of the Emergency Guardian scan_stale path on ROS 2 Humble (no Gazebo).
# Runs INSIDE the ros:humble-ros-base container: repo at /ws (read-only), colcon
# install at /opt/mwr/install, logs to /scratch/live/*.log (host-visible).
#
# Phases (wall-clock markers in harness.log correlate with recorder.log):
#   P0  guardian + /bot1/odom only (no scan)  -> expect scan_stale (never received + odom witness)
#   P1  start /bot1/scan @10Hz                -> expect clear (stop_requested false, no zero Twist)
#   P2  hold 5s                               -> expect no events
#   P3  kill the scan publisher               -> expect scan_stale ~1.0s later (detail scan_age ~1.0x)
#   P4  restart the scan publisher            -> expect clear
set -o pipefail
source /opt/ros/humble/setup.bash
source /opt/mwr/install/setup.bash
export WAREHOUSE_CONFIG_DIR=/ws/config WAREHOUSE_ENV=dev ROS_DOMAIN_ID=77
export PYTHONUNBUFFERED=1
LOG=/scratch/live
mkdir -p "$LOG"
rm -f "$LOG"/harness.log "$LOG"/recorder.log "$LOG"/guardian.log "$LOG"/pub_*.log

ts() { date +%s.%N | cut -c1-14; }
mark() { echo "$(ts) $*" | tee -a "$LOG/harness.log"; }

SENSOR_QOS="--qos-reliability best_effort --qos-durability volatile"
start_scan() {
  ros2 topic pub -r 10 $SENSOR_QOS /bot1/scan sensor_msgs/msg/LaserScan \
    '{header: {frame_id: laser}, angle_min: -3.14, angle_max: 3.14, angle_increment: 0.0175, time_increment: 0.0, scan_time: 0.1, range_min: 0.05, range_max: 12.0, ranges: [1.0, 1.0, 1.0, 1.0]}' \
    > "$LOG/pub_scan.log" 2>&1 &
  SCAN=$!
}

python3 /scratch/live/recorder.py > "$LOG/recorder.log" 2>&1 &
REC=$!
sleep 2

mark "P0 start guardian (no odom, no scan)"
ros2 run warehouse_safety emergency_guardian > "$LOG/guardian.log" 2>&1 &
GUARD=$!
sleep 3
if ! kill -0 "$GUARD" 2>/dev/null; then mark "FATAL guardian exited early"; cat "$LOG/guardian.log"; exit 1; fi

mark "P0 start /bot1/odom publisher only (expect scan_stale: never received + odom witness)"
ros2 topic pub -r 20 $SENSOR_QOS /bot1/odom nav_msgs/msg/Odometry \
  '{header: {frame_id: odom}, child_frame_id: base_link, pose: {pose: {orientation: {w: 1.0}}}}' \
  > "$LOG/pub_odom.log" 2>&1 &
ODOM=$!
sleep 4

mark "P1 start /bot1/scan publisher @10Hz (expect clear)"
start_scan
sleep 5

mark "P2 hold 5s (expect no events)"
sleep 5

mark "P3 kill scan publisher (expect scan_stale after ~1.0s)"
kill "$SCAN" 2>/dev/null; wait "$SCAN" 2>/dev/null
mark "P3 scan publisher stopped"
sleep 5

mark "P4 restart scan publisher (expect clear)"
start_scan
sleep 5

mark "END"
kill "$SCAN" "$ODOM" 2>/dev/null
kill -INT "$GUARD" 2>/dev/null; sleep 1
kill -INT "$REC" 2>/dev/null; sleep 1
kill "$GUARD" "$REC" 2>/dev/null
wait 2>/dev/null
echo "--- guardian.log ---"; cat "$LOG/guardian.log"
echo "--- recorder.log ---"; cat "$LOG/recorder.log"
