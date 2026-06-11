// Minimal Unity shim for HOST execution (g++/clang) when PlatformIO + Unity is
// unavailable. Implements ONLY the subset of the Unity API used by test_clamp.cpp,
// so a SINGLE test source runs under both:
//   pio test -e native               -> real ThrowTheSwitch Unity (framework)
//   firmware/test/run_host_test.sh   -> this shim (added via -I)
//
// Under `pio test`, this folder is NOT on the include path and is excluded by
// `test_filter = test_clamp` in platformio.ini, so the real <unity.h> is used.
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

#define TEST_ASSERT_FALSE(cond) \
  do { if (cond) unity_fail(__FILE__, __LINE__, "TEST_ASSERT_FALSE(" #cond ")"); } while (0)

#define TEST_ASSERT_FLOAT_WITHIN(delta, expected, actual)                       \
  do { if (std::fabs((float)(expected) - (float)(actual)) > (float)(delta))     \
         unity_fail(__FILE__, __LINE__,                                          \
                    "TEST_ASSERT_FLOAT_WITHIN(" #expected ", " #actual ")"); } while (0)

// Real Unity uses a relative tolerance (~1e-5); for our small fixed values an
// absolute epsilon is deterministic and sufficient.
#define TEST_ASSERT_EQUAL_FLOAT(expected, actual) \
  TEST_ASSERT_FLOAT_WITHIN(1e-5f, (expected), (actual))

#define UNITY_BEGIN() do { unity_failures = 0; unity_tests = 0; } while (0)

// Mirror Unity's per-test lifecycle: setUp() / test / tearDown().
#define RUN_TEST(func) \
  do { unity_current = #func; ++unity_tests; setUp(); func(); tearDown(); } while (0)

// Print a Unity-style summary line and yield the failure count as the exit code.
#define UNITY_END() \
  (std::printf("\n%d Tests %d Failures 0 Ignored\n", unity_tests, unity_failures), unity_failures)
