// Minimal host shim of the Arduino core API — TEST/CI ONLY (not flashed).
//
// Lets firmware/src/main.cpp be compiled (syntax + type checked) on the host with
// plain g++/clang, with no ESP32 toolchain, mirroring the unity_shim used by the
// Layer-0 clamp R-26 gate (firmware/test/support/unity_shim/unity.h). It provides
// ONLY the no-op primitives the skeleton references; real behavior comes from the
// ESP32 Arduino core under `pio run -e esp32dev`. micro-ROS (rclc/rcl) is NOT shimmed
// — the skeleton keeps all micro-ROS calls as Phase-1 TODO comments, so the host
// compile needs no ROS headers. Used by firmware/test/run_host_compile.sh.
#pragma once

#include <cstdint>
#include <cstddef>

// UART config constant (value irrelevant on host; real one is an ESP32 enum).
static constexpr int SERIAL_8N1 = 0x800001c;

// Timing primitives (no-ops on host).
static inline uint32_t millis() { return 0; }
static inline uint32_t micros() { return 0; }
static inline void delay(uint32_t) {}
static inline void delayMicroseconds(uint32_t) {}

// HardwareSerial stand-in. The single begin() overload (with defaults) accepts both
// Serial.begin(baud) and Serial1.begin(baud, config, rxPin, txPin).
struct HostHardwareSerial {
  void begin(unsigned long, int = SERIAL_8N1, int = -1, int = -1) {}
  void end() {}
  int available() { return 0; }
  int read() { return -1; }
  size_t write(uint8_t) { return 0; }
  template <typename T> size_t print(const T&) { return 0; }
  template <typename T> size_t println(const T&) { return 0; }
  size_t println() { return 0; }
};

static HostHardwareSerial Serial;
static HostHardwareSerial Serial1;
static HostHardwareSerial Serial2;
