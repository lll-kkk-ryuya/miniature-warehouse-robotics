#!/usr/bin/env bash
# Host (no ESP32 / no PlatformIO) COMPILE gate for the firmware skeleton.
#
# Compiles firmware/src/main.cpp against a minimal Arduino shim
# (test/support/arduino_shim) so the stub structure — motor sink, sensor publisher
# stubs, MS200 UART init, /cmd_vel → clamp → motor path — is type-checked on the
# host. micro-ROS (rclc/rcl) calls are Phase-1 TODO comments, so no ROS headers are
# needed. Compiles to an object only (-c, no link): the ESP32 Arduino main() is
# irrelevant on the host. Complements run_host_test.sh (the RUNNABLE R-26 clamp unit).
#
# Build flags mirror platformio.ini [env:esp32dev]: BOT_ID + the MCU velocity ceiling
# MAX_LINEAR_VELOCITY_MMPS=300 (= 0.3 m/s, safety.md / doc12:77).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"  # firmware/test
fw="$(dirname "$here")"                               # firmware
obj="$(mktemp -d)/main.o"

"${CXX:-c++}" -std=c++17 -Wall -Wextra -c \
  -I "$fw/include" \
  -I "$here/support/arduino_shim" \
  -D BOT_ID=1 \
  -D MAX_LINEAR_VELOCITY_MMPS=300 \
  "$fw/src/main.cpp" \
  -o "$obj"

echo "PASS: host compile of firmware skeleton (main.cpp)"
