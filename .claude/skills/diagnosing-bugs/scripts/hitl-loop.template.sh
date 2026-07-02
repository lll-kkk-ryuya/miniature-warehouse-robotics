#!/usr/bin/env bash
# HITL (human-in-the-loop) reproduction-loop TEMPLATE for diagnosing-bugs Phase 1/4.
# Copy, fill in STEP/EXPECT, and run: it walks a human operator through one
# reproduction cycle and captures observations as KEY=VALUE lines the agent can
# grep back (our GO-sheet vocabulary: R-38 verdict / OOM / headroom / red|green).
#
# It observes only — it never mutates the repo or dispatches motion. For the
# live ER path use the runbook + cost/scoped-approval gate first
# (docs/dev/07-mode-x-er-live-e2e-runbook.md). Fill-in required; no live spend here.
set -u

OUT="${MWR_HITL_OUT:-/tmp/mwr-hitl-$(git rev-parse --short HEAD 2>/dev/null || echo run).log}"
note() { printf '%s\n' "$*" | tee -a "$OUT" >&2; }
capture() { # capture KEY "prompt"  -> reads operator input, appends KEY=VALUE
  local key="$1" prompt="$2" val
  read -r -p "[$key] $prompt: " val
  printf '%s=%s\n' "$key" "$val" >> "$OUT"
}

note "=== HITL loop -> $OUT (branch $(git rev-parse --abbrev-ref HEAD 2>/dev/null)) ==="

# --- STEP 1: state the deterministic reproduction command (Phase 1) -----------
# e.g. REPRO_CMD="deploy/dev/check-hermes-live.sh"   (never a paid live call without approval)
note "STEP 1: run the agreed reproduction command yourself, then record what you saw."
capture RED_OBSERVED "did it fail as expected? (red|green|other)"
capture EVIDENCE     "paste the one key line/error that proves the failure"

# --- STEP 2: single-variable observation (Phase 4) ----------------------------
# note "STEP 2: with only the [DEBUG-xxxx] change applied, re-run and observe."
# capture VERDICT     "R-38 style verdict for this cycle (GO|No-Go|inconclusive)"
# capture HEADROOM_MB "residual RAM headroom in MB if relevant (else -)"

note "=== captured -> $OUT ; feed these KEY=VALUE lines back to the agent ==="
