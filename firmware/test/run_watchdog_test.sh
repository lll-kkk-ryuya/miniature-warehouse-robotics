#!/usr/bin/env bash
# Host (no ESP32 / no PlatformIO) unit test for the pure command-stream (heartbeat)
# watchdog (firmware/include/command_watchdog.h): the comms-loss deadman decision.
#
# Compiles firmware/test/test_watchdog/test_watchdog.cpp with the bundled minimal
# Unity shim so the SAME test source runs under plain g++/clang when PlatformIO is
# absent. Mirrors the test_watchdog suite of `pio test -e native`. Separate from the
# Layer-0 clamp R-26 gate (run_host_test.sh) so that safety gate stays pristine.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"  # firmware/test
fw="$(dirname "$here")"                               # firmware
out="$(mktemp -d)/test_watchdog"

"${CXX:-c++}" -std=c++17 -Wall -Wextra \
  -I "$fw/include" \
  -I "$here/support/unity_shim" \
  "$here/test_watchdog/test_watchdog.cpp" \
  -o "$out"

"$out"
echo "PASS: host watchdog unit test (comms-loss heartbeat deadman)"
