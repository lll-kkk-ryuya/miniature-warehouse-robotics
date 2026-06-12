#!/usr/bin/env bash
# Host (no ESP32 / no PlatformIO) unit test for the pure differential-drive
# kinematics (firmware/include/kinematics.h): skid-steer mix + dead-reckon odom.
#
# Compiles firmware/test/test_kinematics/test_kinematics.cpp with the bundled
# minimal Unity shim so the SAME test source runs under plain g++/clang when
# PlatformIO is absent. Mirrors `pio test -e native`. Separate from the Layer-0
# clamp R-26 gate (run_host_test.sh) so the safety gate stays pristine.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"  # firmware/test
fw="$(dirname "$here")"                               # firmware
out="$(mktemp -d)/test_kinematics"

"${CXX:-c++}" -std=c++17 -Wall -Wextra \
  -I "$fw/include" \
  -I "$here/support/unity_shim" \
  "$here/test_kinematics/test_kinematics.cpp" \
  -o "$out"

"$out"
echo "PASS: host kinematics unit test (skid-steer mix + dead-reckon odom)"
