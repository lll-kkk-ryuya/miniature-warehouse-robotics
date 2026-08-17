// Layer 0 safety: command-stream (heartbeat) watchdog — pure, Arduino-independent,
// host-unit-testable. Comms-loss deadman.
//
// If /cmd_vel stops arriving (ESP32<->Jetson link dropped, upstream crashed), the MCU
// must stop the motors ON ITS OWN: a dead link means the off-MCU Emergency Guardian
// (Layer 1) cannot push a stop through it, so the last accepted command would keep the
// robot rolling. The comms loss itself is the stop condition (deadman).
//
// Layer / doctrine: docs/architecture/12-infrastructure-common.md §安全レイヤー Layer 0
// (heartbeat/watchdog bullet + note :112) and H-G6 `heartbeat_lost`
// (docs/productization/08-navigation-hardware-eval-gates.md:105). This is DISTINCT from
// the L1 blocked-timeout watchdog + battery 3-stage policy (Emergency Guardian,
// doc12:82) — that runs off-MCU; this is the MCU-internal, communication-independent cut.
//
// Kept free of <Arduino.h>/millis() so the decision compiles and is host-testable
// (`pio test -e native` / firmware/test/run_watchdog_test.sh) without an ESP32. The
// timeout is a PARAMETER, not baked in: its real value is Phase-1 measured (like the
// kinematics hardware constants), so this header invents no threshold.
#pragma once

#include <cstdint>

// True when the last accepted /cmd_vel is older than `timeout_ms` — the caller must
// then fail-stop the motors (setMotorVelocity(0, 0)).
//
// Uses UNSIGNED subtraction so it is correct across the millis() uint32 rollover
// (~every 49.7 days): (now - last) wraps to the TRUE elapsed as long as the real gap
// is < 2^32 ms, so a rollover with a small real gap does NOT falsely trip the stop.
// Boundary: elapsed == timeout is still FRESH; the gap must strictly EXCEED the timeout
// to be stale, so an exactly-on-time command is not cut. Direction is fail-safe:
// no fresh command (or a backwards clock) → large/rolled elapsed → stale → stop.
inline bool command_is_stale(uint32_t last_cmd_ms, uint32_t now_ms, uint32_t timeout_ms) {
  return static_cast<uint32_t>(now_ms - last_cmd_ms) > timeout_ms;
}
