#!/usr/bin/env bash
# Environment-spike driver (doc16 §10). Re-runnable, headless. Proves whether
# headless `gz sim` (Harmonic/gz-sim8) + a LiDAR sensor + `ros_gz_bridge` work on
# `tiryoh/ros2-desktop-vnc:jazzy` (ARM64) on the M4 Mac, producing /bot1/scan +
# /bot1/odom and accepting /bot1/cmd_vel. NOT the real feature — a throwaway probe.
#
# Usage:
#   ./run_spike.sh setup        # pull image, (re)create container, install gz+ros_gz, versions
#   ./run_spike.sh probe        # isolated headless render init (ogre2) — the decisive risk
#   ./run_spike.sh verify A     # run sim (ogre2 / min_lidar.sdf) + bridge, check the 3 topics
#   ./run_spike.sh verify B     # same via ogre1 fallback (min_lidar_cpu.sdf)
#   ./run_spike.sh clean        # remove the container
set -euo pipefail

IMAGE="tiryoh/ros2-desktop-vnc:jazzy"
CONTAINER="mwr-spike"
SPIKE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE="/spike/config/bridge.yaml"
RENV='export LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe; source /opt/ros/jazzy/setup.bash'

dex()  { docker exec "$CONTAINER" bash -lc "$*"; }
dexd() { docker exec -d "$CONTAINER" bash -lc "$*"; }   # detached, persists in container

case "${1:-setup}" in
  clean)
    docker rm -f "$CONTAINER" 2>/dev/null || true ;;

  setup)
    mkdir -p "$SPIKE_DIR/logs"
    docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull "$IMAGE"
    docker rm -f "$CONTAINER" 2>/dev/null || true
    # --memory cap folds in the doc06:92 memory smoke (Jetson 8GB ~= 6GB usable).
    docker run -d --name "$CONTAINER" \
      --memory=6g --memory-swap=6g \
      -e LIBGL_ALWAYS_SOFTWARE=1 -e GALLIUM_DRIVER=llvmpipe \
      -v "$SPIKE_DIR:/spike:rw" \
      "$IMAGE" sleep infinity
    dex 'set -e; apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
           ros-jazzy-ros-gz ros-jazzy-ros-gz-bridge ros-jazzy-ros-gz-sim mesa-utils libgl1-mesa-dri \
         > /spike/logs/apt.log 2>&1; source /opt/ros/jazzy/setup.bash; \
         echo "gz: $(gz sim --version 2>&1)"; echo "ros_gz_bridge prefix: $(ros2 pkg prefix ros_gz_bridge 2>&1)"'
    # generate the ogre1 fallback world (Path B) from the single ogre2 source
    dex 'sed "s|<render_engine>ogre2</render_engine>|<render_engine>ogre</render_engine>|" \
           /spike/worlds/min_lidar.sdf > /spike/worlds/min_lidar_cpu.sdf' ;;

  probe)
    # The decisive risk, isolated & fast: does ogre2+EGL render init under software GL?
    dex "$RENV; export GZ_VERBOSE=4; glxinfo -B 2>&1 | head -6 || true; \
         timeout 20 gz sim -s -r --headless-rendering -v4 /spike/worlds/min_lidar.sdf \
           > /spike/logs/render_probe_ogre2.log 2>&1 || true; tail -40 /spike/logs/render_probe_ogre2.log" ;;

  verify)
    path="${2:-A}"; world="/spike/worlds/min_lidar.sdf"
    [[ "$path" == "B" ]] && world="/spike/worlds/min_lidar_cpu.sdf"
    dex 'pkill -f "gz sim" 2>/dev/null || true; pkill -f parameter_bridge 2>/dev/null || true; sleep 1'
    dexd "$RENV; gz sim -s -r --headless-rendering -v1 $world > /spike/logs/sim_${path}.log 2>&1"
    sleep 8
    dexd "$RENV; ros2 run ros_gz_bridge parameter_bridge --ros-args -p config_file:=$BRIDGE \
            > /spike/logs/bridge_${path}.log 2>&1"
    sleep 5
    dex "$RENV; echo '--- topics ---'; ros2 topic list; \
         echo '--- scan hz ---';   timeout 8 ros2 topic hz /bot1/scan || true; \
         echo '--- scan sample ---'; timeout 6 ros2 topic echo /bot1/scan --once 2>/dev/null | head -c 500; echo; \
         echo '--- odom before ---'; timeout 6 ros2 topic echo /bot1/odom --once --field pose.pose.position 2>/dev/null || true; \
         echo '--- cmd_vel 0.2 m/s x3s (<=0.3 cap) ---'; timeout 3 ros2 topic pub /bot1/cmd_vel geometry_msgs/msg/Twist '{linear: {x: 0.2}}' -r 10 || true; \
         echo '--- odom after ---';  timeout 6 ros2 topic echo /bot1/odom --once --field pose.pose.position 2>/dev/null || true" ;;

  *)
    echo "usage: $0 {setup|probe|verify A|verify B|clean}"; exit 2 ;;
esac
