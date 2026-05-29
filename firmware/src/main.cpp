// ESP32 micro-ROS firmware (skeleton) — Yahboom MicroROS Car.
//
// Responsibilities (doc02 / doc03 / doc12 Layer 0):
//   Pub : /<ns>/odom (nav_msgs/Odometry), /<ns>/scan (sensor_msgs/LaserScan),
//         /<ns>/battery (sensor_msgs/BatteryState)
//   Sub : /<ns>/cmd_vel (geometry_msgs/Twist)
//   SAFETY (Layer 0): clamp commanded velocity to <= MAX_LINEAR_VELOCITY in the
//   MCU, regardless of the value sent from ROS 2. This is the final defense line.
//
// NOTE: 雛形（実機未検証）。micro-ROS の executor/timer/QoS と各ドライバは
//       実機到着後に確定する（doc17 Step 1）。ロジックの骨格と安全クランプを示す。
#include <Arduino.h>

#include "config.h"

// --- Layer 0 safety: velocity clamp (pure, unit-testable logic) -------------
// ROS 側の指令値に関わらず、MCU 内で物理上限にクランプする。
float clampLinear(float v) {
  if (v > MAX_LINEAR_VELOCITY) return MAX_LINEAR_VELOCITY;
  if (v < -MAX_LINEAR_VELOCITY) return -MAX_LINEAR_VELOCITY;
  return v;
}

float clampAngular(float w) {
  if (w > MAX_ANGULAR_VELOCITY) return MAX_ANGULAR_VELOCITY;
  if (w < -MAX_ANGULAR_VELOCITY) return -MAX_ANGULAR_VELOCITY;
  return w;
}

// --- /cmd_vel callback (skeleton) ------------------------------------------
// micro-ROS の subscription から呼ばれる想定。受信した Twist をクランプして
// モータへ渡す。ここが Layer 0 の速度強制点。
void onCmdVel(float linear_x, float angular_z) {
  const float v = clampLinear(linear_x);
  const float w = clampAngular(angular_z);
  // TODO(Phase 1): setMotorVelocity(v, w);  // 4輪モータPWMへ反映
  (void)v;
  (void)w;
}

// --- 近接停止（Layer 0、通信非依存）----------------------------------------
// TODO(Phase 1): 近接センサ/バンパ検知でモータ enable OFF（ROS/OS 非依存）。

void setup() {
  Serial.begin(115200);
  // TODO(Phase 1): WiFi 接続 → micro-ROS transport(UDP, AGENT_IP:AGENT_PORT) 設定
  // TODO(Phase 1): node "esp32_" ROS_NAMESPACE / pub(odom,scan,battery) / sub(cmd_vel)
  // TODO(Phase 1): MS200 を Serial1.begin(MS200_BAUD, SERIAL_8N1, MS200_RX_PIN, MS200_TX_PIN)
  // TODO(Phase 1): 再接続ロジック（Agent 切断時に rclc_support 再初期化）
}

void loop() {
  // TODO(Phase 1): rclc_executor_spin_some(...) / odom・scan・battery を publish
  // 雛形のため空。安全クランプは onCmdVel() に実装済み。
  delay(10);
}
