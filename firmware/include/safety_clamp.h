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

#include <cmath>     // std::isfinite — fail-safe guard for non-finite cmd_vel
#include "config.h"  // MAX_LINEAR_VELOCITY / MAX_ANGULAR_VELOCITY (build-flag derived)

// Compile-time tripwire: -ffast-math (-> -ffinite-math-only, __FINITE_MATH_ONLY__)
// lets the compiler ASSUME no NaN/Inf and constant-fold std::isfinite(v) to `true`,
// silently deleting the fail-safe below. A static_assert can't catch it (the folded
// value is still a constant expression), so fail the build loudly instead. The ESP32
// Arduino / ESP-IDF default build does NOT set this; the guard just fences off a
// future build-flag/LTO recipe from defeating Layer-0 safety without warning.
#if defined(__FINITE_MATH_ONLY__) && __FINITE_MATH_ONLY__
#  error "safety_clamp.h: -ffinite-math-only/-ffast-math defeats the std::isfinite NaN/Inf fail-safe"
#endif

// Clamp `v` into the symmetric interval [-limit, +limit].
// PRECONDITION: limit >= 0. A negative limit would invert the comparisons and turn
// this into a runaway amplifier (firmware analogue of the clamp_velocity negative-cap
// fail-open). Callers pass MAX_*_VELOCITY, which the unit tests assert are > 0.
inline float clamp_symmetric(float v, float limit) {
  // Fail-safe: a non-finite request (NaN / ±inf) is unknown -> STOP (0.0), never a
  // silent leak nor a snap to ±limit (= full speed). Without this guard, IEEE-754
  // makes `NaN > limit` and `NaN < -limit` both false, so a NaN would fall through
  // to `return v` and reach the motor duty calc once main.cpp:26 (setMotorVelocity)
  // is wired. Mirrors the canonical clamp_velocity (safety.py:31-32) and the State
  // Cache's non-finite drop (doc12:293) — unknown input fails to stop, not to motion.
  if (!std::isfinite(v)) return 0.0f;
  if (v > limit) return limit;
  if (v < -limit) return -limit;
  return v;
}

// Thin wrappers applying the MCU-enforced ceilings (Layer 0 enforcement point).
inline float clampLinear(float v) { return clamp_symmetric(v, MAX_LINEAR_VELOCITY); }
inline float clampAngular(float w) { return clamp_symmetric(w, MAX_ANGULAR_VELOCITY); }
