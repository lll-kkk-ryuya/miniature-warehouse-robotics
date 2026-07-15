#!/usr/bin/env bash
# Host (no ESP32 / no PlatformIO) unit test for the pure XRCE client_key derivation
# (firmware/include/client_key.h): the deterministic, distinct-per-board key that keeps
# two boards from colliding on one micro_ros_agent (R-37 first-line fix).
#
# Compiles firmware/test/test_client_key/test_client_key.cpp with the bundled minimal
# Unity shim so the SAME test source runs under plain g++/clang when PlatformIO is
# absent. Mirrors the test_client_key suite of `pio test -e native`. Separate from the
# Layer-0 clamp R-26 gate (run_host_test.sh) so that safety gate stays pristine.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"  # firmware/test
fw="$(dirname "$here")"                               # firmware
out="$(mktemp -d)/test_client_key"

"${CXX:-c++}" -std=c++17 -Wall -Wextra \
  -I "$fw/include" \
  -I "$here/support/unity_shim" \
  "$here/test_client_key/test_client_key.cpp" \
  -o "$out"

"$out"
echo "PASS: host client_key unit test (deterministic distinct XRCE key, R-37)"
