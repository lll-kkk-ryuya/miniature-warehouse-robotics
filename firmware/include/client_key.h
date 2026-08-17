// Layer 0 safety enabler: deterministic per-board XRCE client_key derivation
// (pure, Arduino/rmw-independent, host-unit-testable).
//
// R-37: connecting two ESP32 boards to a single micro_ros_agent (udp4 :8888) breaks
// unless each board presents a DISTINCT XRCE-DDS client_key. A shared OR weak-RNG key
// makes the Agent treat the second client as a re-connect of the SAME session
// ("session re-established") and it tears down the first client's entities, so one
// direction of pub/sub silently drops (R-37). The host spike reproduced this and
// proved distinct keys fix it on a single Agent (firmware/spike/RESULT.md:29,44).
//
// The failure ROOT is the rmw_microxrcedds default, which seeds a WEAK RNG at boot:
//   srand(uxr_nanos()); client_key = rand();   (rmw_init.c, firmware/spike/RESULT.md:54)
// Two boards booting near-simultaneously can correlate/duplicate that seed -> same key
// -> session collision. This function replaces that with a fully DETERMINISTIC key
// derived from board identity (BOT_ID build flag + the per-board MAC), so the key is
// stable across reboots and provably distinct between the two boards.
//
// Doctrine / adoption: a distinct client_key (BOT_ID/MAC-derived) fed to
// rmw_uros_options_set_client_key() is the FIRST-LINE R-37 fix
// (docs/shared/07-research-notes.md:242, firmware/spike/RESULT.md:63). The runtime reads
// the per-board MAC (WiFi.macAddress() / esp_efuse_mac_get_default) and feeds this key to
// rmw_uros_options_set_client_key() (firmware/spike/uros_app/minicar_client/main.c:81,97).
//
// Kept free of <Arduino.h>/rmw so the derivation compiles and is host-testable
// (firmware/test/run_client_key_test.sh) without an ESP32. bot_id and mac are
// PARAMETERS (like command_watchdog.h's timeout), so this header invents no runtime
// value and stays pure. The bit layout below is a DERIVATION METHOD, not a frozen
// threshold; the spike's 0xB0A71001-style constants are test CLI literals, not baked here.
#pragma once

#include <cstdint>

// Fold the 6 MAC bytes into 24 bits: XOR the high 3 bytes (OUI) with the low 3 bytes
// (per-chip NIC id). The NIC id carries the per-board uniqueness, so two boards sharing
// an Espressif OUI still fold to distinct values. Byte shifts are computed on uint32_t
// (not the promoted uint8_t) to keep the intent explicit and free of promotion surprises.
inline uint32_t fold_mac24(const uint8_t mac[6]) {
  const uint32_t hi = (static_cast<uint32_t>(mac[0]) << 16) |
                      (static_cast<uint32_t>(mac[1]) << 8) |
                      static_cast<uint32_t>(mac[2]);
  const uint32_t lo = (static_cast<uint32_t>(mac[3]) << 16) |
                      (static_cast<uint32_t>(mac[4]) << 8) |
                      static_cast<uint32_t>(mac[5]);
  return hi ^ lo;
}

// Derive the DISTINCT, DETERMINISTIC XRCE client_key for one board:
//   key = (bot_id << 24) | (fold_mac24(mac) & 0x00FFFFFF)
//
// Properties (all STRUCTURAL — no magic threshold is invented):
//   * DETERMINISTIC: pure bit ops only; the same (bot_id, mac) always yields the same
//     key, so re-deriving on every boot is stable (firmware/CLAUDE.md:12). No rand()/
//     clock is used, which is exactly the weak-RNG path this function exists to avoid.
//   * DISTINCT (primary, R-37): the two robots are flashed BOT_ID=1 vs BOT_ID=2
//     (firmware/platformio.ini:15 / firmware/include/config.h:13), so the high byte is
//     0x01 vs 0x02 and the keys differ even if the MACs were identical
//     (docs/shared/07-research-notes.md:242).
//   * DISTINCT (defense-in-depth): if both boards were wrongly flashed with the SAME
//     BOT_ID, the per-chip MAC still separates them -- for the real case of two boards
//     sharing an Espressif OUI the fold differs exactly when the NIC bytes differ. NOTE
//     the 48->24 fold is lossy, so arbitrary cross-OUI MACs could in principle collide;
//     this is only a backstop -- the BOT_ID high byte above is the load-bearing guarantee.
//   * NON-ZERO: bot_id >= 1 (BOT_ID in {1,2}) => key >= 0x01000000 != 0, so it can never
//     collapse to the XRCE all-zero CLIENTKEY_INVALID. This follows from the bit layout,
//     not from an invented threshold.
//   * The `& 0x00FFFFFF` mask on the fold is belt-and-suspenders: fold_mac24 is already
//     <=24 bits by construction, so the mask is an equivalent no-op on today's fold, but
//     it guarantees the low term can never bleed into and corrupt the bot_id high byte if
//     fold_mac24 ever changed. Kept for layout safety; deliberately NOT pinned by a test
//     (asserting a no-op mask would be tautological / impl-coupled, which R-26 forbids).
inline uint32_t derive_xrce_client_key(uint8_t bot_id, const uint8_t mac[6]) {
  return (static_cast<uint32_t>(bot_id) << 24) | (fold_mac24(mac) & 0x00FFFFFFu);
}
