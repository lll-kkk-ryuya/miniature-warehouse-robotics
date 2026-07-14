// ESP32 micro-ROS firmware (skeleton) — Yahboom MicroROS Car.
//
// Responsibilities (doc02 / doc03 / doc12 Layer 0):
//   Pub : /<ns>/odom (nav_msgs/Odometry), /<ns>/scan (sensor_msgs/LaserScan),
//         /<ns>/battery (sensor_msgs/BatteryState)
//   Sub : /<ns>/cmd_vel (geometry_msgs/Twist)
//   SAFETY (Layer 0): clamp commanded velocity to <= MAX_LINEAR_VELOCITY in the
//   MCU, regardless of the value sent from ROS 2. This is the final defense line.
//
// NOTE: 雛形（実機未検証）。micro-ROS の executor/timer/QoS と各ドライバ実体は
//       実機到着後に確定する（doc17 Step 1 / doc06 Phase 1）。本ファイルは
//       ロジックの骨格・安全クランプ・各 publisher/driver の stub IF を示す。
//       実際の rclc publisher 登録・rcl_publish・UART フレームパースは Phase 1。
//       host での構文確認は firmware/test/run_host_compile.sh（Arduino shim）。
#include <Arduino.h>

#include "config.h"
// Layer 0 safety: velocity clamp (pure, Arduino-independent, host-unit-tested R-26).
// clampLinear / clampAngular live here so the same logic is pinned by
// firmware/test/test_clamp (`pio test -e native` / run_host_test.sh). 呼ぶだけ・不変。
#include "safety_clamp.h"
// Pure differential-drive kinematics (skid-steer mix + dead-reckon odom), host-unit-
// tested in firmware/test/test_kinematics. Hardware values (TRACK_WIDTH / PWM duty /
// encoder scale / dt) stay Phase-1 TODO and are passed in as parameters.
#include "kinematics.h"

// =============================================================================
//  Motor output — Layer 0 enforcement SINK (doc02:14, doc12:77)
// =============================================================================
// setMotorVelocity — drive the 4-wheel SKID-STEER base from a CLAMPED (v, w).
// doc02:14 — 310 encoder motor ×4 = LEFT / RIGHT 2-channel control. Differential
// mix maps body twist to two track speeds:
//     left_track  = v - w * (TRACK_WIDTH / 2)
//     right_track = v + w * (TRACK_WIDTH / 2)
// then per-track PWM duty = dutyFromSpeed(track_speed). TRACK_WIDTH and the
// speed->duty curve are measured on the real chassis (config.h: TODO Phase 1), so
// the mix constants are NOT invented here — only the call contract is fixed.
// PRECONDITION: v, w are ALREADY clamped by clampLinear/clampAngular (Layer 0):
// this stub must never re-introduce a value above the MCU ceiling.
void setMotorVelocity(float v, float w) {
  // The skid-steer mix (v,w -> left/right track speed) is host-tested in kinematics.h;
  // only the measured TRACK_WIDTH and the track-speed -> PWM duty curve are Phase 1.
  // TODO(Phase 1): const TrackSpeeds ts = mixSkidSteer(v, w, TRACK_WIDTH);  // measured (config.h)
  //                ledcWrite(L_PWM_CH, dutyFromSpeed(ts.left));   // ESP32 LEDC PWM
  //                ledcWrite(R_PWM_CH, dutyFromSpeed(ts.right));  // duty curve = Phase 1
  (void)v;
  (void)w;
}

// --- /cmd_vel callback — Layer 0 enforcement POINT (doc12:77) ----------------
// micro-ROS の subscription から呼ばれる想定。受信 Twist をクランプしてモータへ。
// ここが Layer 0 の速度強制点（上位の値に関わらず MCU 内で <= 0.3 m/s に丸める）。
void onCmdVel(float linear_x, float angular_z) {
  const float v = clampLinear(linear_x);   // <= MAX_LINEAR_VELOCITY (0.3 m/s)
  const float w = clampAngular(angular_z);  // <= MAX_ANGULAR_VELOCITY
  setMotorVelocity(v, w);                    // clamped command -> motors
}

// =============================================================================
//  Sensor publishers (micro-ROS) — doc03 FROZEN topic contract
// =============================================================================
// All publish wiring (rclc publisher init + rcl_publish from a timer/executor) is
// Phase 1. These stubs pin the doc03 topic name + type each will carry and leave
// the fill/publish as TODO. 新トピック/型は産まない（doc03 をそのまま consume）。

// /<ns>/scan : sensor_msgs/LaserScan — ORBBEC MS200 360° dToF (doc03:78, doc02:15,26).
// Fixed-value mock: real MS200 UART frame parse is Phase 1; the fake scan documents
// the shape only — 360° sweep, range 0.03..12 m, 0.4° resolution (doc02:15).
// NOTE(R-43, doc07:253): 360°/0.4° ≈ 900 pts × float ≈ 3.6 KB/scan over UDP MTU 512B
//   needs downsample / fragmentation handling — measured Phase 1 (host spike未検証).
void publishScan() {
  // TODO(Phase 1): parse Serial1 MS200 frames -> ranges[]; build
  //   sensor_msgs__msg__LaserScan{angle_min=-pi, angle_max=+pi, range_min=0.03f,
  //   range_max=12.0f, ranges[...]} and rcl_publish(&scan_pub_, &msg, NULL).
}

// /<ns>/odom : nav_msgs/Odometry — encoder dead-reckon (doc03:77, doc02:28).
// Dead-reckon stub: pose starts at [0,0,0] and would integrate the last commanded
// (v, w) over dt from encoder counts. Real encoder integration is Phase 1
// (doc06:129 /odom receipt check). Hook point for setMotorVelocity feedback later.
void publishOdom() {
  // Dead-reckon pose accumulates here; the integration math is host-tested
  // (kinematics.h integrateOdom). Encoder-tick read, tick->distance scale and dt
  // are Phase 1 (doc06:129 /odom receipt check).
  static Pose2D s_pose = {0.0f, 0.0f, 0.0f};  // starts at origin
  // TODO(Phase 1): s_pose = integrateOdom(s_pose, v_meas, w_meas, dt);  // from encoders
  //   build nav_msgs__msg__Odometry{header.frame_id="odom", child_frame_id="base_link",
  //   pose=s_pose, twist=(v_meas,w_meas)} and rcl_publish(&odom_pub_, &msg, NULL).
  (void)s_pose;
}

// /<ns>/battery : sensor_msgs/BatteryState — 7.4V LiPo (doc03:79, doc02:17).
// 実機は micro-ROS firmware が battery を供給する (doc03:79, Phase 1+).
void publishBattery() {
  // TODO(Phase 1): read pack voltage via ADC (nominal 7.4 V, doc02:17); set
  //   `percentage` on the SAME scale as config safety.battery_percentage_scale so
  //   State Cache / Emergency Guardian don't split-brain (doc03:79). Build
  //   sensor_msgs__msg__BatteryState and rcl_publish(&batt_pub_, &msg, NULL).
}

// /<ns>/imu : 6-axis IMU — *** doc03-contract-EXTERNAL — DO NOT PUBLISH ***
// doc03 does NOT register /bot{n}/imu and sim does not bridge it (doc03:81,
// doc02:27 「※要確認 / sim 未橋渡し」). This stub is a placeholder ONLY: it is
// deliberately NOT registered as a publisher and NOT called from loop(). Wiring it
// would ADD a new topic to the frozen doc03 contract, which is a contract-PR +
// announce on epic #3 (implementation-and-dependencies.md §3), NOT a firmware-local
// decision. Left here so the IMU read path has a named home in Phase 1.
void publishImu() {
  // TODO(contract): /bot{n}/imu is doc03-external (:81). Do NOT enable until a
  //   contract-PR adds it to doc03. Intentionally unwired (no publisher, no call).
}

// =============================================================================
//  Drivers / transport init (stubs) — doc02 / doc06 Phase 1
// =============================================================================
// initMS200 — bring up the ORBBEC MS200 dToF LiDAR UART (doc02:15, config.h:20-22).
void initMS200() {
  // GPIO RX=18 / TX=17 @ 230400 baud, 8N1 (config.h MS200_* / doc02:15 配線).
  Serial1.begin(MS200_BAUD, SERIAL_8N1, MS200_RX_PIN, MS200_TX_PIN);
  // TODO(Phase 1): drain + sync to the MS200 frame header before publishScan().
}

// --- 近接停止 (Layer 0, comms-independent) — doc12:76,78 ---------------------
// TODO(Phase 1): ToF/LiDAR 近接物体検出 → モータ PWM 停止 / motor enable OFF を MCU 内で
//   直接行う（ROS/OS 非依存）。これは速度クランプの「下」にある Layer-0 reflex で、
//   通信が全停止しても効く最終防衛線（doc12:76,78,112）。本ラウンドは stub/コメント。

void setup() {
  Serial.begin(115200);
  initMS200();
  // TODO(Phase 1): WiFi connect → micro-ROS UDP transport (AGENT_IP:AGENT_PORT, config.h).
  // TODO(Phase 1): rmw_uros_options_set_client_key() with a DISTINCT per-board key
  //   (BOT_ID/MAC). R-37: a shared/weak key collides the XRCE session and one
  //   direction of pub/sub drops (firmware/CLAUDE.md:12, doc07:242, spike/RESULT.md).
  // TODO(Phase 1): rclc node "esp32_" + ROS_NAMESPACE; register pub(odom,scan,battery)
  //   + sub(cmd_vel → onCmdVel). /bot{n}/imu は doc03:81 契約外＝登録しない。
  // TODO(Phase 1): reconnect — on Agent disconnect, re-init rclc_support (firmware/CLAUDE.md:11).
}

void loop() {
  // TODO(Phase 1): rclc_executor_spin_some(...) — drives onCmdVel from /cmd_vel.
  publishOdom();     // /<ns>/odom    nav_msgs/Odometry      (doc03:77)
  publishScan();     // /<ns>/scan    sensor_msgs/LaserScan  (doc03:78)
  publishBattery();  // /<ns>/battery sensor_msgs/BatteryState (doc03:79)
  // publishImu() intentionally NOT called — /bot{n}/imu is doc03-external (:81).

  // ---- SAFETY WATCHDOG (Layer 0 comms-loss deadman decided; wiring = Phase 1) -------
  // Layer 0 ACTIVELY enforces the velocity clamp (onCmdVel) + the proximity reflex.
  // The comms-loss heartbeat deadman is now a DECIDED Layer-0 mechanism (doc12 §安全
  // レイヤー Layer 0 heartbeat/watchdog + H-G6 heartbeat_lost, productization/08:105);
  // its pure decision lives in command_watchdog.h (command_is_stale, host-tested R-26),
  // kept distinct from the L1 blocked-timeout watchdog + battery 3-stage (Emergency
  // Guardian, doc12:82, OFF the MCU). Only the millis()/last_cmd_ms wiring is Phase 1:
  //   1) stale /cmd_vel : command_is_stale(last_cmd_ms, millis(), CMD_TIMEOUT) → setMotorVelocity(0,0). [L0]
  //   2) stale /scan    : if no MS200 frame for N ms → stop (lost obstacle sensing).  [L0, Phase 1]
  //   3) low battery    : MCU hard-cut floor only; the 3-stage policy is Layer 1
  //                       (doc12:82, Emergency Guardian), not the firmware.
  // Runtime gating (millis(), last_cmd_ms) is Phase 1; the decision + pure logic land now.
  delay(10);
}
