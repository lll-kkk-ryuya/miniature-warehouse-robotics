// Layer 0 safety: host-runnable unit tests for the velocity clamp (R-26).
//
// Pins the MCU-enforced velocity ceiling so a future build-flag regression that
// loosened the limit -- or a sign error inverting the clamp -- fails here. The
// clamp is the final defense line (.claude/rules/safety.md,
// docs/architecture/12-infrastructure-common.md:77,112); R-26 requires safety
// mechanisms to ship with unit tests (docs/architecture/16 §11,
// docs/architecture/20-dev-quality-and-testing.md:75 Phase 0.5 safety unit).
//
// Runs two ways, both on the host (no ESP32 required):
//   pio test -e native               (PlatformIO + Unity)
//   firmware/test/run_host_test.sh   (g++/clang + bundled minimal Unity shim)
#include <unity.h>

#include <cmath>  // NAN / INFINITY for the non-finite (fail-safe) tests

#include "config.h"        // MAX_LINEAR_VELOCITY / MAX_ANGULAR_VELOCITY (build-flag derived)
#include "safety_clamp.h"  // clamp_symmetric + clampLinear / clampAngular

void setUp(void) {}
void tearDown(void) {}

// --- pure clamp (clamp_symmetric), independent of config ---------------------
void test_clamp_symmetric_passes_values_in_range(void) {
  TEST_ASSERT_EQUAL_FLOAT(0.1f, clamp_symmetric(0.1f, 0.3f));
  TEST_ASSERT_EQUAL_FLOAT(-0.1f, clamp_symmetric(-0.1f, 0.3f));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, clamp_symmetric(0.0f, 0.3f));
}

void test_clamp_symmetric_caps_above_and_below(void) {
  TEST_ASSERT_EQUAL_FLOAT(0.3f, clamp_symmetric(0.5f, 0.3f));    // above  -> +limit
  TEST_ASSERT_EQUAL_FLOAT(-0.3f, clamp_symmetric(-0.5f, 0.3f));  // below  -> -limit
}

void test_clamp_symmetric_boundary_is_inclusive(void) {
  TEST_ASSERT_EQUAL_FLOAT(0.3f, clamp_symmetric(0.3f, 0.3f));    // exactly +limit
  TEST_ASSERT_EQUAL_FLOAT(-0.3f, clamp_symmetric(-0.3f, 0.3f));  // exactly -limit
}

// --- fail-safe: non-finite (NaN / ±inf) cmd_vel must STOP (#235) -------------
// IEEE-754: `NaN > limit` and `NaN < -limit` are both false, so without the
// explicit guard a NaN would leak straight through clamp_symmetric to the motor
// duty calc (main.cpp:26). ±inf would otherwise snap to ±limit (= full speed).
// Unknown input must fail-stop, matching clamp_velocity (safety.py:31-32).
void test_clamp_symmetric_nonfinite_returns_stop(void) {
  TEST_ASSERT_EQUAL_FLOAT(0.0f, clamp_symmetric(NAN, 0.3f));        // NaN  -> stop
  TEST_ASSERT_EQUAL_FLOAT(0.0f, clamp_symmetric(INFINITY, 0.3f));   // +inf -> stop
  TEST_ASSERT_EQUAL_FLOAT(0.0f, clamp_symmetric(-INFINITY, 0.3f));  // -inf -> stop
}

// The Layer-0 wrappers (what main.cpp:onCmdVel actually calls) inherit fail-safe.
void test_clamp_wrappers_nonfinite_returns_stop(void) {
  TEST_ASSERT_EQUAL_FLOAT(0.0f, clampLinear(NAN));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, clampLinear(INFINITY));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, clampLinear(-INFINITY));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, clampAngular(NAN));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, clampAngular(INFINITY));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, clampAngular(-INFINITY));
}

// --- linear wrapper applies the config ceiling ------------------------------
void test_clampLinear_enforces_max_linear_velocity(void) {
  TEST_ASSERT_EQUAL_FLOAT(MAX_LINEAR_VELOCITY, clampLinear(0.5f));    // over ceiling
  TEST_ASSERT_EQUAL_FLOAT(-MAX_LINEAR_VELOCITY, clampLinear(-0.5f));  // under -ceiling
  TEST_ASSERT_EQUAL_FLOAT(0.1f, clampLinear(0.1f));                   // passthrough
  TEST_ASSERT_EQUAL_FLOAT(0.0f, clampLinear(0.0f));                   // zero
}

// --- angular wrapper applies the config ceiling -----------------------------
// MAX_ANGULAR_VELOCITY=2.0 is a Phase 1 placeholder (firmware/include/config.h:10),
// so we pin only that the boundary is enforced, not that the value itself is final.
void test_clampAngular_enforces_max_angular_velocity(void) {
  TEST_ASSERT_EQUAL_FLOAT(MAX_ANGULAR_VELOCITY, clampAngular(MAX_ANGULAR_VELOCITY + 1.0f));
  TEST_ASSERT_EQUAL_FLOAT(-MAX_ANGULAR_VELOCITY, clampAngular(-(MAX_ANGULAR_VELOCITY + 1.0f)));
  TEST_ASSERT_EQUAL_FLOAT(0.5f, clampAngular(0.5f));  // passthrough
}

// --- SAFETY CONTRACT PIN: the MCU ceiling must be exactly 0.3 m/s ------------
// safety.md / doc12:77 freeze the Layer-0 ceiling at 0.3 m/s (= 300 mm/s / 1000).
// If a future build flag loosens MAX_LINEAR_VELOCITY_MMPS, this fails -- the point.
void test_max_linear_velocity_is_0_3_mps(void) {
  TEST_ASSERT_FLOAT_WITHIN(1e-6f, 0.3f, MAX_LINEAR_VELOCITY);
}

// Guard against a negative ceiling, which would invert clamp_symmetric into a
// runaway amplifier (firmware analogue of clamp_velocity negative-cap fail-open).
void test_velocity_limits_are_positive(void) {
  TEST_ASSERT_TRUE(MAX_LINEAR_VELOCITY > 0.0f);
  TEST_ASSERT_TRUE(MAX_ANGULAR_VELOCITY > 0.0f);
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_clamp_symmetric_passes_values_in_range);
  RUN_TEST(test_clamp_symmetric_caps_above_and_below);
  RUN_TEST(test_clamp_symmetric_boundary_is_inclusive);
  RUN_TEST(test_clamp_symmetric_nonfinite_returns_stop);
  RUN_TEST(test_clamp_wrappers_nonfinite_returns_stop);
  RUN_TEST(test_clampLinear_enforces_max_linear_velocity);
  RUN_TEST(test_clampAngular_enforces_max_angular_velocity);
  RUN_TEST(test_max_linear_velocity_is_0_3_mps);
  RUN_TEST(test_velocity_limits_are_positive);
  return UNITY_END();
}
