#!/usr/bin/env bash
# Host (no ESP32) gate for the Layer-0 velocity clamp R-26 unit test.
#
# Compiles firmware/test/test_clamp/test_clamp.cpp with a bundled minimal Unity
# shim so the SAME test source runs under plain g++/clang when PlatformIO is not
# installed. This is the pristine R-26 clamp gate (test_clamp only); it mirrors the
# test_clamp suite of `pio test -e native` — both paths must be green (R-26 unit).
#
# Build flags mirror platformio.ini [env:native]: BOT_ID + the MCU velocity
# ceiling MAX_LINEAR_VELOCITY_MMPS=300 (= 0.3 m/s, safety.md / doc12:77).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"  # firmware/test
fw="$(dirname "$here")"                               # firmware
out="$(mktemp -d)/test_clamp"

"${CXX:-c++}" -std=c++17 -Wall -Wextra \
  -I "$fw/include" \
  -I "$here/support/unity_shim" \
  -D BOT_ID=1 \
  -D MAX_LINEAR_VELOCITY_MMPS=300 \
  "$here/test_clamp/test_clamp.cpp" \
  -o "$out"

"$out"
echo "PASS: host clamp unit test (R-26)"
