// Layer 0 safety: host-runnable unit tests for the command-stream (heartbeat)
// watchdog (R-26). Pins the comms-loss deadman decision so a regression -- loosening
// `>` to `>=`, or a signed/naive comparison that mishandles the millis() rollover --
// fails here. Doctrine: doc12 §安全レイヤー Layer 0 heartbeat/watchdog + H-G6
// `heartbeat_lost` (docs/productization/08-navigation-hardware-eval-gates.md:105).
// R-26 requires safety mechanisms to ship with unit tests (docs/architecture/16 §11,
// docs/architecture/20-dev-quality-and-testing.md:75).
//
// Runs two ways, both on the host (no ESP32 required):
//   pio test -e native                 (PlatformIO + Unity, native test_filter)
//   firmware/test/run_watchdog_test.sh (g++/clang + bundled minimal Unity shim)
//
// Oracle is independent: expected staleness is computed by hand (elapsed vs timeout),
// not by re-deriving it from command_is_stale, so the tests go red under mutation.
#include <unity.h>

#include <cstdint>

#include "command_watchdog.h"  // command_is_stale

void setUp(void) {}
void tearDown(void) {}

// fresh: elapsed < timeout -> not stale (keep driving); zero elapsed is fresh too.
void test_fresh_command_is_not_stale(void) {
  TEST_ASSERT_TRUE(!command_is_stale(1000u, 1100u, 500u));  // elapsed 100 < 500
  TEST_ASSERT_TRUE(!command_is_stale(1000u, 1000u, 500u));  // elapsed 0 (just received)
}

// stale: elapsed > timeout -> stale (caller fail-stops).
void test_old_command_is_stale(void) {
  TEST_ASSERT_TRUE(command_is_stale(1000u, 2000u, 500u));  // elapsed 1000 > 500
}

// boundary: elapsed == timeout is still FRESH; timeout+1 is stale. Pins `>` (not `>=`),
// so a mutation to the operator flips one of these and the suite goes red.
void test_boundary_exact_timeout_is_fresh(void) {
  TEST_ASSERT_TRUE(!command_is_stale(1000u, 1500u, 500u));  // elapsed 500 == timeout -> fresh
  TEST_ASSERT_TRUE(command_is_stale(1000u, 1501u, 500u));   // elapsed 501 >  timeout -> stale
}

// rollover-safe: millis() (uint32) wraps ~every 49.7 days. Unsigned subtraction must
// yield the TRUE elapsed, so a wrap with a small real gap stays FRESH (no false stop).
// last near UINT32_MAX, now wrapped past 0 -> true elapsed = 0x50 + 0x100 = 0x150 = 336 ms.
void test_rollover_small_gap_is_fresh(void) {
  TEST_ASSERT_TRUE(!command_is_stale(0xFFFFFF00u, 0x00000050u, 500u));  // 336 < 500
}

// rollover with a real gap beyond the timeout must still be detected as stale
// (a signed or naive `now < last -> not stale` comparison would wrongly keep driving).
void test_rollover_large_gap_is_stale(void) {
  TEST_ASSERT_TRUE(command_is_stale(0xFFFFFF00u, 0x00000050u, 100u));  // 336 > 100
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_fresh_command_is_not_stale);
  RUN_TEST(test_old_command_is_stale);
  RUN_TEST(test_boundary_exact_timeout_is_fresh);
  RUN_TEST(test_rollover_small_gap_is_fresh);
  RUN_TEST(test_rollover_large_gap_is_stale);
  return UNITY_END();
}
