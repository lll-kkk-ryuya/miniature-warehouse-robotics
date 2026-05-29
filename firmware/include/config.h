// Hardware / network configuration for the ESP32 micro-ROS car.
// ⚠️ 認証情報（WiFi パス等）はコミットしない。実値は config_secret.h（.gitignore）に置く。
// ピン・寸法は実機到着後に実測で確定する（TODO: Phase 1）。
#pragma once

// ── Safety (Layer 0, safety.md) ─────────────────────────────────────
// MCU 内で強制する速度上限。ROS 側 /cmd_vel の値に関わらずこの値にクランプする。
// build_flags の MAX_LINEAR_VELOCITY_MMPS から導出（0.3 m/s）。
static constexpr float MAX_LINEAR_VELOCITY = MAX_LINEAR_VELOCITY_MMPS / 1000.0f;  // m/s
static constexpr float MAX_ANGULAR_VELOCITY = 2.0f;  // rad/s（TODO: Phase 1 実測）

// ── micro-ROS namespace（/bot1 or /bot2、build_flags BOT_ID）─────────
#if BOT_ID == 1
  #define ROS_NAMESPACE "bot1"
#else
  #define ROS_NAMESPACE "bot2"
#endif

// ── MS200 LiDAR (UART, doc02: Yahboom 配線 GPIO17/18 @ 230400) ───────
static constexpr int MS200_RX_PIN = 18;   // TODO: 実機配線で確定
static constexpr int MS200_TX_PIN = 17;
static constexpr int MS200_BAUD = 230400;

// ── 通信 ───────────────────────────────────────────────────────────
// WiFi SSID/PASS と Agent IP は config_secret.h で定義（コミット禁止）。
// 例: #define WIFI_SSID "..."  #define WIFI_PASS "..."  #define AGENT_IP "192.168.x.x"  #define AGENT_PORT 8888
#if __has_include("config_secret.h")
  #include "config_secret.h"
#else
  #define WIFI_SSID ""
  #define WIFI_PASS ""
  #define AGENT_IP ""
  #define AGENT_PORT 8888
#endif
