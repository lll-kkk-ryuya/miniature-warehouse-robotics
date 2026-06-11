// Minimal Unity shim for HOST execution (g++/clang) when PlatformIO + Unity is
// unavailable. Implements ONLY the subset of the Unity API used by test_clamp.cpp,
// so a SINGLE test source runs under both:
//   pio test -e native               -> real ThrowTheSwitch Unity (framework)
//   firmware/test/run_host_test.sh   -> this shim (added via -I)
//
// Why this does not shadow the framework's <unity.h> under `pio test -e native`:
// PlatformIO puts the running suite folder (test_clamp/) and the Unity framework on
// the include path, but support/unity_shim/ has no `test` name prefix -- it is
// treated as shared code, not a suite, and is not added recursively to CPPPATH -- so
// the suite's `#include <unity.h>` resolves to real ThrowTheSwitch Unity
// (test_framework = unity, platformio.ini:24). `test_filter = test_clamp` only
// selects which suite RUNS; it is not what hides this shim.
#pragma once

#include <cstdio>
#include <cmath>

static int unity_failures = 0;
static int unity_tests = 0;
static const char* unity_current = "";

inline void unity_fail(const char* file, int line, const char* what) {
  std::printf("  FAIL %s:%d in %s -- %s\n", file, line, unity_current, what);
  ++unity_failures;
}

#define TEST_ASSERT_TRUE(cond) \
  do { if (!(cond)) unity_fail(__FILE__, __LINE__, "TEST_ASSERT_TRUE(" #cond ")"); } while (0)

// Mirror real Unity's UnityFloatsWithin: the comparison is "within" only when the
// difference is FINITE and within delta. A non-finite diff -- NaN (from a NaN
// operand) or ±inf (from an inf operand) -- is NEVER within and FAILs. Without the
// !isfinite() guard, `fabs(NaN) > delta` is false, so a NaN `actual` would silently
// PASS (a false GREEN that hides a regression of the clamp's non-finite fail-safe).
// ONE divergence from real Unity: real Unity special-cases two SAME-SIGN infinities
// as "within" (PASS); here the diff ∞−∞ = NaN, so an EXPECTED ±inf FAILs. Harmless
// for this suite (every `expected` is finite -- 0.0/0.3/MAX_*); do NOT use this
// assertion to compare an expected ±inf.
#define TEST_ASSERT_FLOAT_WITHIN(delta, expected, actual)                       \
  do {                                                                          \
    const float _uw_diff = (float)(actual) - (float)(expected);                 \
    if (!std::isfinite(_uw_diff) || std::fabs(_uw_diff) > (float)(delta))       \
      unity_fail(__FILE__, __LINE__,                                            \
                 "TEST_ASSERT_FLOAT_WITHIN(" #expected ", " #actual ")");       \
  } while (0)

// Real Unity uses a relative tolerance (~1e-5); for our small fixed values an
// absolute epsilon is deterministic and sufficient.
#define TEST_ASSERT_EQUAL_FLOAT(expected, actual) \
  TEST_ASSERT_FLOAT_WITHIN(1e-5f, (expected), (actual))

#define UNITY_BEGIN() do { unity_failures = 0; unity_tests = 0; } while (0)

// Mirror Unity's per-test lifecycle: setUp() / test / tearDown().
#define RUN_TEST(func) \
  do { unity_current = #func; ++unity_tests; setUp(); func(); tearDown(); } while (0)

// Print a Unity-style summary line and yield the exit code. Real Unity returns the
// raw failure count (and main() returns it as the process status); a count that is a
// multiple of 256 would wrap to 0 under POSIX 8-bit exit codes = a false GREEN. The
// shim saturates to 0/1 so ANY failure yields a non-zero exit.
#define UNITY_END() \
  (std::printf("\n%d Tests %d Failures 0 Ignored\n", unity_tests, unity_failures), \
   unity_failures ? 1 : 0)
