#!/usr/bin/env bash
# R-37 spike driver — micro-ROS Agent multi-client (UDP) reproduction, NO hardware.
#
# Reproduces/characterizes R-37 (docs/shared/07-research-notes.md:242: "1 Agent に複数
# ボードを UDP 接続すると pub/sub の片方しか通らない") by standing up, inside a single
# tiryoh/ros2-desktop-vnc:jazzy container, ONE `micro_ros_agent udp4` plus TWO software
# micro-ROS clients (bot1/bot2 = minicar_client, rclc + rmw_microxrcedds). The two knobs
# the risk turns on — the XRCE-DDS session id (client_key) and the agent topology — are
# varied across four scenarios. This is a THROWAWAY probe, not the real firmware.
#
# IMPORTANT: a host no-repro does NOT close R-37. Default host client_key is rand()
# (rmw_init.c:114-118) so two host clients get DISTINCT keys naturally — the collision must
# be FORCED. Loopback also cannot exercise the WiFi-loss/MTU half (R-43). Final closure is
# Phase 1 on real ESP32 over WiFi. See RESULT.md.
#
# Usage:
#   ./run_spike.sh setup        # container + agent ws (uros_ws) + client ws (client_ws) + minicar_client
#   ./run_spike.sh baseline     # 1 agent + 1 client  (sanity: graph/pub/sub work at all)
#   ./run_spike.sh repro        # 1 agent + 2 clients, IDENTICAL client_key (force the collision)
#   ./run_spike.sh fixA         # 1 agent + 2 clients, DISTINCT client_key + ns (the documented fix)
#   ./run_spike.sh fixB         # 2 agents on separate ports + 2 clients (the doc's "別ポート" idea)
#   ./run_spike.sh all          # baseline -> repro -> fixA -> fixB
#   ./run_spike.sh report       # summarise logs/ into a table
#   ./run_spike.sh clean        # remove the container
set -uo pipefail

IMAGE="tiryoh/ros2-desktop-vnc:jazzy"
CONTAINER="mwr-uros-spike"
SPIKE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP_BR="jazzy"                 # micro_ros_setup branch
PORT_A=8888                      # frozen prod port (warehouse-microros-agent.service:21)
PORT_B=8889                      # second agent (fixB only)
KEY1=0xB0A71001                  # bot1 distinct key
KEY2=0xB0A71002                  # bot2 distinct key
KEY_SAME=0xB0A71001              # both bots share this in `repro`

SRC_ROS='source /opt/ros/jazzy/setup.bash'
SRC_AGENT="$SRC_ROS; source /root/uros_ws/install/local_setup.bash"
# RMW_IMPLEMENTATION=rmw_microxrcedds is MANDATORY or the client uses default DDS and never reaches the agent.
SRC_CLIENT="$SRC_ROS; source /root/client_ws/install/local_setup.bash; export RMW_IMPLEMENTATION=rmw_microxrcedds"

dex()  { docker exec    "$CONTAINER" bash -lc "$*"; }
dexd() { docker exec -d "$CONTAINER" bash -lc "$*"; }

kill_all() {
  dex 'pkill -f micro_ros_agent 2>/dev/null; pkill -f minicar_client 2>/dev/null; pkill -f "ros2 topic" 2>/dev/null; sleep 1' || true
}

start_agent() { # <scenario> <port>
  dexd "$SRC_AGENT; exec ros2 run micro_ros_agent micro_ros_agent udp4 --port $2 -v6 > /spike/logs/$1_agent_$2.log 2>&1"
}
start_client() { # <scenario> <ns> <port> <key>
  dexd "$SRC_CLIENT; exec ros2 run minicar_client minicar_client 127.0.0.1 $3 $2 $4 0 > /spike/logs/$1_client_$2.log 2>&1"
}

measure() { # <scenario> <ns...>
  local sc="$1"; shift; local bots=("$@"); local L="/spike/logs/${sc}"
  echo "  [measure] settling 8s for XRCE sessions..."; sleep 8
  dex "$SRC_ROS; ros2 node list"  > "$SPIKE_DIR/logs/${sc}_nodes.txt"  2>&1
  dex "$SRC_ROS; ros2 topic list" > "$SPIKE_DIR/logs/${sc}_topics.txt" 2>&1
  for ns in "${bots[@]}"; do
    echo "  [measure] $ns PUB: ros2 topic hz /$ns/hb (6s)"
    dex "$SRC_ROS; timeout 6 ros2 topic hz /$ns/hb" > "$SPIKE_DIR/logs/${sc}_hz_${ns}.txt" 2>&1 || true
  done
  for ns in "${bots[@]}"; do
    local val="1${ns#bot}11"   # bot1->111, bot2->211 (recognizable)
    echo "  [measure] $ns SUB: publish /$ns/cmd data=$val (4s) then grep client log"
    dex "$SRC_ROS; timeout 4 ros2 topic pub /$ns/cmd std_msgs/msg/Int32 '{data: $val}' -r 10 >/dev/null 2>&1" || true
  done
  sleep 1
}

ensure_up() {
  docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}\$" || {
    echo "container '${CONTAINER}' not running — run: $0 setup" >&2; exit 1; }
  dex 'test -x /root/client_ws/install/minicar_client/lib/minicar_client/minicar_client' || {
    echo "minicar_client not built — run: $0 setup" >&2; exit 1; }
}

case "${1:-}" in
  clean)
    docker rm -f "$CONTAINER" 2>/dev/null || true ;;

  setup)
    mkdir -p "$SPIKE_DIR/logs"
    docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull "$IMAGE"
    docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER}\$" || \
      docker run -d --name "$CONTAINER" --memory=6g --memory-swap=6g \
        -v "$SPIKE_DIR:/spike:rw" "$IMAGE" sleep infinity
    echo "=== baseline tooling ==="
    dex "DEBIAN_FRONTEND=noninteractive apt-get update -qq && apt-get install -y -qq \
         git python3-colcon-common-extensions python3-rosdep build-essential > /spike/logs/apt.log 2>&1; \
         rosdep update >/dev/null 2>&1 || true"
    echo "=== AGENT workspace (uros_ws): create_agent_ws + build_agent ==="
    dex "set -e; $SRC_ROS; mkdir -p /root/uros_ws/src; cd /root/uros_ws; \
         [ -d src/micro_ros_setup ] || git clone -b $SETUP_BR https://github.com/micro-ROS/micro_ros_setup.git src/micro_ros_setup; \
         rosdep install --from-paths src --ignore-src -y >/dev/null 2>&1 || true; \
         colcon build > /spike/logs/setup_agent_build.log 2>&1; \
         source install/local_setup.bash; \
         ros2 run micro_ros_setup create_agent_ws.sh >> /spike/logs/setup_agent_build.log 2>&1; \
         ros2 run micro_ros_setup build_agent.sh >> /spike/logs/setup_agent_build.log 2>&1"
    echo "=== CLIENT workspace (client_ws): create_firmware_ws host + minicar_client ==="
    # We do NOT run build_firmware.sh — on Jazzy/24.04 it fails two ways:
    #   (1) rmw_microxrcedds: cc1 'all warnings being treated as errors' (newer GCC + -Werror)
    #   (2) std_srvs/example_interfaces: undefined service_msgs__msg__ServiceEventInfo (service introspection)
    # Instead we replicate micro_ros_setup's host build.sh THREE-PHASE order (build the microxrcedds
    # typesupport generator -> SOURCE it -> build the message pkgs so they emit
    # *__rosidl_typesupport_microxrcedds_c.so), adding -w (defuse -Werror) and skipping the
    # service pkgs. Building messages BEFORE sourcing the generator yields only fastrtps/introspection
    # typesupport -> runtime "typesupport identifier (rosidl_typesupport_c) is not supported".
    dex "set -e; $SRC_ROS; mkdir -p /root/client_ws/src; cd /root/client_ws; \
         [ -d src/micro_ros_setup ] || git clone -b $SETUP_BR https://github.com/micro-ROS/micro_ros_setup.git src/micro_ros_setup; \
         colcon build --packages-select micro_ros_setup > /spike/logs/setup_client_tooling.log 2>&1; \
         source install/local_setup.bash; \
         [ -d firmware ] || ros2 run micro_ros_setup create_firmware_ws.sh host >> /spike/logs/setup_client_tooling.log 2>&1; \
         rm -rf src/minicar_client; cp -r /spike/uros_app/minicar_client src/minicar_client; \
         CMA='-DBUILD_TESTING=OFF -DBUILD_SHARED_LIBS=ON -DCMAKE_C_FLAGS=-w -DCMAKE_CXX_FLAGS=-w'; \
         colcon build --packages-up-to rosidl_typesupport_microxrcedds_c   --metas src/colcon.meta --cmake-args \$CMA  > /spike/logs/build_minicar.log 2>&1; \
         colcon build --packages-up-to rosidl_typesupport_microxrcedds_cpp --metas src/colcon.meta --cmake-args \$CMA >> /spike/logs/build_minicar.log 2>&1; \
         source install/local_setup.bash; \
         colcon build --packages-up-to minicar_client --metas src/colcon.meta \
           --packages-skip example_interfaces std_srvs lifecycle_msgs test_msgs --parallel-workers 4 --cmake-args \$CMA >> /spike/logs/build_minicar.log 2>&1"
    dex 'test -x /root/client_ws/install/minicar_client/lib/minicar_client/minicar_client' \
      && echo "SETUP OK: minicar_client built." || { echo "SETUP FAILED — see logs/build_minicar.log"; exit 1; } ;;

  baseline)
    ensure_up; echo "### scenario: baseline (1 agent + 1 client) ###"; kill_all
    start_agent baseline "$PORT_A"; sleep 3
    start_client baseline bot1 "$PORT_A" "$KEY1"
    measure baseline bot1; kill_all ;;

  repro)
    ensure_up; echo "### scenario: repro (1 agent + 2 clients, IDENTICAL key $KEY_SAME) ###"; kill_all
    start_agent repro "$PORT_A"; sleep 3
    start_client repro bot1 "$PORT_A" "$KEY_SAME"; sleep 2
    start_client repro bot2 "$PORT_A" "$KEY_SAME"
    measure repro bot1 bot2; kill_all ;;

  fixA)
    ensure_up; echo "### scenario: fixA (1 agent + 2 clients, DISTINCT keys $KEY1/$KEY2) ###"; kill_all
    start_agent fixA "$PORT_A"; sleep 3
    start_client fixA bot1 "$PORT_A" "$KEY1"; sleep 2
    start_client fixA bot2 "$PORT_A" "$KEY2"
    measure fixA bot1 bot2; kill_all ;;

  fixB)
    ensure_up; echo "### scenario: fixB (2 agents on $PORT_A/$PORT_B, distinct keys) ###"; kill_all
    start_agent fixB "$PORT_A"; start_agent fixB "$PORT_B"; sleep 3
    start_client fixB bot1 "$PORT_A" "$KEY1"; sleep 2
    start_client fixB bot2 "$PORT_B" "$KEY2"
    measure fixB bot1 bot2; kill_all ;;

  all)
    SELF="$SPIKE_DIR/$(basename "${BASH_SOURCE[0]}")"
    bash "$SELF" baseline; bash "$SELF" repro; bash "$SELF" fixA; bash "$SELF" fixB; bash "$SELF" report ;;

  report)
    echo "=== R-37 spike report ==="
    for sc in baseline repro fixA fixB; do
      [ -f "$SPIKE_DIR/logs/${sc}_nodes.txt" ] || continue
      echo "--- $sc ---"
      echo "nodes : $(tr '\n' ' ' < "$SPIKE_DIR/logs/${sc}_nodes.txt")"
      for ns in bot1 bot2; do
        hzf="$SPIKE_DIR/logs/${sc}_hz_${ns}.txt"; clf="$SPIKE_DIR/logs/${sc}_client_${ns}.log"
        [ -f "$hzf" ] || continue
        hz=$(grep -oE 'average rate: [0-9.]+' "$hzf" | head -1 | awk '{print $3}')
        subn=$(grep -c 'SUB cmd' "$clf" 2>/dev/null || echo 0)
        echo "  $ns  PUB(/hb hz)=${hz:-NONE}   SUB(cmd rx)=${subn}"
      done
    done
    echo "NOTE: host no-repro != R-37 closed (forced-key only; no WiFi/MTU). Final closure = Phase 1 hardware." ;;

  *)
    echo "usage: $0 {setup|baseline|repro|fixA|fixB|all|report|clean}"; exit 2 ;;
esac
