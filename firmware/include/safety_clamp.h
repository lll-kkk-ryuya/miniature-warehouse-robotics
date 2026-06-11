// Layer 0 safety: velocity clamp (pure, Arduino-independent, host-unit-testable).
//
// This is the firmware's FINAL defense line: regardless of the /cmd_vel value sent
// from ROS 2, the MCU clamps commanded velocity to the physical ceiling here.
// Ceiling source: .claude/rules/safety.md (<=0.3 m/s enforced in code) and
// docs/architecture/12-infrastructure-common.md:77,112 (Layer 0, final defense).
// The numeric value is NOT invented here -- it is derived from the build flag
// MAX_LINEAR_VELOCITY_MMPS (platformio.ini) via config.h.
//
// Kept free of <Arduino.h> so the clamp logic compiles and is testable on the host
// (`pio test -e native` / firmware/test/run_host_test.sh) without an ESP32.
#pragma once

#include "config.h"  // MAX_LINEAR_VELOCITY / MAX_ANGULAR_VELOCITY (build-flag derived)

// Clamp `v` into the symmetric interval [-limit, +limit].
// PRECONDITION: limit >= 0. A negative limit would invert the comparisons and turn
// this into a runaway amplifier (firmware analogue of the clamp_velocity negative-cap
// fail-open). Callers pass MAX_*_VELOCITY, which the unit tests assert are > 0.
inline float clamp_symmetric(float v, float limit) {
  if (v > limit) return limit;
  if (v < -limit) return -limit;
  return v;
}

// Thin wrappers applying the MCU-enforced ceilings (Layer 0 enforcement point).
inline float clampLinear(float v) { return clamp_symmetric(v, MAX_LINEAR_VELOCITY); }
inline float clampAngular(float w) { return clamp_symmetric(w, MAX_ANGULAR_VELOCITY); }
