// Host-runnable unit tests for the pure differential-drive kinematics
// (firmware/include/kinematics.h): the skid-steer mix (mixSkidSteer) and the
// dead-reckon odometry integration (integrateOdom). No ESP32 required.
//
// Runs two ways, both on the host:
//   pio test -e native                     (PlatformIO + Unity)
//   firmware/test/run_kinematics_test.sh   (g++/clang + bundled minimal Unity shim)
//
// All expected values are hand-computed and chosen on the unambiguous cases
// (straight / spin / rest), so a sign error in the mix or a bad integration step
// fails here regardless of the exact curved-move integration scheme.
#include <unity.h>

#include <cmath>  // M_PI

#include "kinematics.h"

void setUp(void) {}
void tearDown(void) {}

// --- mixSkidSteer: inverse kinematics ----------------------------------------
// track_width is an explicit TEST parameter (NOT a firmware constant): with
// L = 0.2 m, half = 0.1 m.
void test_mix_straight_drives_both_tracks_equally(void) {
  const TrackSpeeds t = mixSkidSteer(0.5f, 0.0f, 0.2f);  // w == 0
  TEST_ASSERT_EQUAL_FLOAT(0.5f, t.left);
  TEST_ASSERT_EQUAL_FLOAT(0.5f, t.right);
}

void test_mix_spin_in_place_is_antisymmetric(void) {
  const TrackSpeeds t = mixSkidSteer(0.0f, 1.0f, 0.2f);  // v == 0, half = 0.1
  TEST_ASSERT_EQUAL_FLOAT(-0.1f, t.left);   // 0 - 1*0.1
  TEST_ASSERT_EQUAL_FLOAT(0.1f, t.right);   // 0 + 1*0.1
}

void test_mix_combined_motion(void) {
  const TrackSpeeds t = mixSkidSteer(1.0f, 2.0f, 0.2f);  // half = 0.1
  TEST_ASSERT_EQUAL_FLOAT(0.8f, t.left);    // 1 - 2*0.1
  TEST_ASSERT_EQUAL_FLOAT(1.2f, t.right);   // 1 + 2*0.1
}

// Positive (CCW) w turns left: the right track must be faster than the left.
void test_mix_positive_w_turns_left(void) {
  const TrackSpeeds t = mixSkidSteer(0.5f, 0.5f, 0.2f);
  TEST_ASSERT_TRUE(t.right > t.left);
}

void test_mix_zero_is_zero(void) {
  const TrackSpeeds t = mixSkidSteer(0.0f, 0.0f, 0.2f);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, t.left);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, t.right);
}

// --- integrateOdom: forward kinematics (dead-reckon) -------------------------
void test_odom_rest_keeps_pose(void) {
  const Pose2D p = integrateOdom(Pose2D{0.0f, 0.0f, 0.0f}, 0.0f, 0.0f, 1.0f);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, p.x);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, p.y);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, p.theta);
}

void test_odom_straight_advances_along_x(void) {
  const Pose2D p = integrateOdom(Pose2D{0.0f, 0.0f, 0.0f}, 1.0f, 0.0f, 2.0f);
  TEST_ASSERT_EQUAL_FLOAT(2.0f, p.x);   // v*dt along theta=0
  TEST_ASSERT_EQUAL_FLOAT(0.0f, p.y);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, p.theta);
}

void test_odom_straight_follows_heading(void) {
  // facing +y (theta = pi/2), drive 1 m/s for 1 s -> y advances ~1, x ~0.
  const Pose2D p = integrateOdom(Pose2D{0.0f, 0.0f, (float)M_PI / 2.0f}, 1.0f, 0.0f, 1.0f);
  TEST_ASSERT_FLOAT_WITHIN(1e-5f, 0.0f, p.x);
  TEST_ASSERT_FLOAT_WITHIN(1e-5f, 1.0f, p.y);
}

void test_odom_spin_in_place_only_rotates(void) {
  const Pose2D p = integrateOdom(Pose2D{0.0f, 0.0f, 0.0f}, 0.0f, 1.0f, 1.0f);
  TEST_ASSERT_EQUAL_FLOAT(0.0f, p.x);     // v == 0 -> no translation
  TEST_ASSERT_EQUAL_FLOAT(0.0f, p.y);
  TEST_ASSERT_EQUAL_FLOAT(1.0f, p.theta);  // w*dt
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_mix_straight_drives_both_tracks_equally);
  RUN_TEST(test_mix_spin_in_place_is_antisymmetric);
  RUN_TEST(test_mix_combined_motion);
  RUN_TEST(test_mix_positive_w_turns_left);
  RUN_TEST(test_mix_zero_is_zero);
  RUN_TEST(test_odom_rest_keeps_pose);
  RUN_TEST(test_odom_straight_advances_along_x);
  RUN_TEST(test_odom_straight_follows_heading);
  RUN_TEST(test_odom_spin_in_place_only_rotates);
  return UNITY_END();
}
