// Differential-drive (skid-steer) kinematics — PURE, Arduino-independent,
// host-unit-testable. Implements the math behind the doc02:14 control model
// ("4輪スキッドステアリング、左右2チャンネル制御") so the motor mix and the
// dead-reckon odometry can be exercised on the host (no ESP32) the same way the
// Layer-0 clamp lives in safety_clamp.h. Tested by firmware/test/test_kinematics.
//
// What stays in firmware (Phase 1, NOT here): the measured TRACK_WIDTH, the
// track-speed -> PWM duty curve, the encoder tick -> distance scale, and dt. Those
// are real-chassis values (config.h TODO) and are passed in as PARAMETERS here, so
// no hardware constant is invented in this header.
//
// Free of <Arduino.h>; uses only <cmath>. Inputs are assumed finite — the upstream
// Layer-0 clamp (safety_clamp.h) already maps non-finite cmd_vel to stop.
#pragma once

#include <cmath>  // std::cos / std::sin

// --- Inverse kinematics: body twist -> per-track speed (skid-steer mix) -------
struct TrackSpeeds {
  float left;   // m/s, left track
  float right;  // m/s, right track
};

// Map body linear v (m/s) + angular w (rad/s, +CCW) to left/right track speeds.
// track_width = distance between the left/right track centers (m). PRECONDITION:
// track_width >= 0 (measured on the real chassis -> config.h Phase 1; not invented).
//   left  = v - w * (track_width / 2)
//   right = v + w * (track_width / 2)
// w > 0 (turn left) makes right > left; straight (w == 0) gives left == right == v;
// spin-in-place (v == 0) gives left == -right.
inline TrackSpeeds mixSkidSteer(float v, float w, float track_width) {
  const float half = track_width * 0.5f;
  return TrackSpeeds{v - w * half, v + w * half};
}

// --- Forward kinematics: dead-reckon pose integration -------------------------
struct Pose2D {
  float x;      // m
  float y;      // m
  float theta;  // rad
};

// Integrate a body twist (v, w) over dt into a planar pose (unicycle model,
// 2nd-order midpoint heading: exact for straight (w == 0) and spin (v == 0) moves;
// a close approximation for curved moves, refined in Phase 1 if needed).
// PRECONDITION: v, w, dt finite (upstream clamp guarantees finite v/w; dt is the
// firmware loop period).
inline Pose2D integrateOdom(Pose2D p, float v, float w, float dt) {
  const float theta_mid = p.theta + 0.5f * w * dt;
  return Pose2D{
      p.x + v * std::cos(theta_mid) * dt,
      p.y + v * std::sin(theta_mid) * dt,
      p.theta + w * dt,
  };
}
