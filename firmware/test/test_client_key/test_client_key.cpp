// Layer 0 safety (R-37 enabler): host-runnable unit tests for the deterministic XRCE
// client_key derivation (firmware/include/client_key.h). Pins the "two boards get
// DISTINCT, DETERMINISTIC, non-weak-RNG keys" decision so a regression -- dropping the
// BOT_ID mix, zeroing the MAC contribution, or a shift/OR/XOR slip -- fails HERE.
//
// Why this is a safety unit: without a distinct client_key per board, a single
// micro_ros_agent tears down one client's XRCE session ("session re-established") and
// one direction of pub/sub silently drops (R-37: firmware/spike/RESULT.md:29). The
// derivation is the FIRST-LINE fix (docs/shared/07-research-notes.md:242). R-26 requires
// safety mechanisms to ship with mutation-adequate unit tests -- independent oracle +
// mutation sensitivity (docs/architecture/20-dev-quality-and-testing.md:139-140 §9;
// seam heuristics docs/architecture/16 §11). Phase 0.5 is where these R-26 units are
// onboarded into CI (docs/architecture/20-dev-quality-and-testing.md:75).
//
// Runs two ways, both on the host (no ESP32 required):
//   pio test -e native                     (PlatformIO + Unity, native test_filter)
//   firmware/test/run_client_key_test.sh   (g++/clang + bundled minimal Unity shim)
//
// INDEPENDENT ORACLE (hand-computed, NOT re-derived from derive_xrce_client_key):
//   key = (bot_id << 24) | ((oui24 ^ nic24) & 0x00FFFFFF)
//   FIX_MAC = {AA,BB,CC,DD,EE,FF}: oui24=0xAABBCC, nic24=0xDDEEFF,
//     fold24 = 0xAABBCC ^ 0xDDEEFF = 0x775533   (AA^DD=77, BB^EE=55, CC^FF=33)
//     => bot1 = 0x01000000 | 0x775533 = 0x01775533 ;  bot2 = 0x02775533
//   MAC_A = {24,6F,28,00,00,01}: fold24 = 0x246F28 ^ 0x000001 = 0x246F29 => bot1 = 0x01246F29
//   MAC_B = {24,6F,28,00,00,02}: fold24 = 0x246F28 ^ 0x000002 = 0x246F2A => bot1 = 0x01246F2A
//   MAC_ZERO = {00,00,00,00,00,00}: fold24 = 0 => bot1 = 0x01000000 (non-zero), bot2 = 0x02000000
#include <unity.h>

#include <cstdint>

#include "client_key.h"  // derive_xrce_client_key, fold_mac24

void setUp(void) {}
void tearDown(void) {}

// Fixtures (const so the arrays decay to const uint8_t* as the signature requires).
static const uint8_t FIX_MAC[6]  = {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF};
static const uint8_t MAC_A[6]    = {0x24, 0x6F, 0x28, 0x00, 0x00, 0x01};
static const uint8_t MAC_B[6]    = {0x24, 0x6F, 0x28, 0x00, 0x00, 0x02};
static const uint8_t MAC_ZERO[6] = {0x00, 0x00, 0x00, 0x00, 0x00, 0x00};

// PRIMARY (R-37 load-bearing): the two robots (BOT_ID=1 vs 2) MUST get different keys
// even on the SAME MAC, and the high byte MUST equal bot_id. Independent oracle: the
// >>24 byte is the literal bot id (1 / 2), computed by hand, not from the impl.
// Mutation: drop bot_id from the mix (e.g. `key = fold24(mac)`) -> same-MAC bot1==bot2
// AND (key>>24)==0 -> both asserts go red.
void test_primary_distinct_by_bot_id(void) {
  const uint32_t k1 = derive_xrce_client_key(1, FIX_MAC);
  const uint32_t k2 = derive_xrce_client_key(2, FIX_MAC);
  TEST_ASSERT_TRUE(k1 != k2);            // distinct across the two boards on one MAC
  TEST_ASSERT_TRUE((k1 >> 24) == 1u);   // high byte == BOT_ID (hand-computed oracle)
  TEST_ASSERT_TRUE((k2 >> 24) == 2u);
}

// Defense-in-depth: with a FIXED bot_id, two boards whose MACs differ only in the NIC
// bytes still fold to distinct keys -- this guards the degenerate case where both were
// mistakenly flashed with the same BOT_ID. Mutation: zero out the MAC term
// (`key = bot_id << 24`) -> derive(1,MAC_A) == derive(1,MAC_B) -> red.
void test_distinct_by_mac(void) {
  TEST_ASSERT_TRUE(derive_xrce_client_key(1, MAC_A) != derive_xrce_client_key(1, MAC_B));
  // Pin the exact folded low-24 bits so a broken fold (bad shift/XOR) is caught.
  TEST_ASSERT_TRUE(derive_xrce_client_key(1, MAC_A) == 0x01246F29u);
  TEST_ASSERT_TRUE(derive_xrce_client_key(1, MAC_B) == 0x01246F2Au);
}

// Deterministic/stable + exact bit-layout pin. Same (bot_id, mac) called twice yields
// the same key (no RNG/clock). The exact literals pin the shift/OR/XOR placement:
// any single mutation of the layout breaks one literal -> red.
void test_deterministic_and_exact_layout(void) {
  TEST_ASSERT_TRUE(derive_xrce_client_key(1, FIX_MAC) == 0x01775533u);
  TEST_ASSERT_TRUE(derive_xrce_client_key(2, FIX_MAC) == 0x02775533u);
  // fold helper pinned independently (oracle 0x775533) so a fold-only regression shows.
  TEST_ASSERT_TRUE(fold_mac24(FIX_MAC) == 0x00775533u);
  // called twice -> identical (determinism / non-weak-RNG regression guard).
  TEST_ASSERT_TRUE(derive_xrce_client_key(1, FIX_MAC) == derive_xrce_client_key(1, FIX_MAC));
}

// Format valid: bot_id >= 1 => key != 0, so it never collapses to the XRCE all-zero
// CLIENTKEY_INVALID even when the MAC folds to 0. Mutation: mask the bot_id high byte
// (`key = fold24(mac) & 0x00FFFFFF`) -> derive(1, MAC_ZERO) == 0 -> red.
void test_format_valid_nonzero(void) {
  TEST_ASSERT_TRUE(derive_xrce_client_key(1, MAC_ZERO) != 0u);
  TEST_ASSERT_TRUE(derive_xrce_client_key(1, MAC_ZERO) == 0x01000000u);  // high byte carries it
  TEST_ASSERT_TRUE(derive_xrce_client_key(2, MAC_ZERO) == 0x02000000u);
}

int main(void) {
  UNITY_BEGIN();
  RUN_TEST(test_primary_distinct_by_bot_id);
  RUN_TEST(test_distinct_by_mac);
  RUN_TEST(test_deterministic_and_exact_layout);
  RUN_TEST(test_format_valid_nonzero);
  return UNITY_END();
}
