#!/usr/bin/env bash
# Reusable runner for the OPT-IN live Gemini Robotics-ER FULL-CHAIN test, so every future
# worktree/session can run the wired XER6 chain (RawModelOutput -> compile_raw_output -> Command)
# against the live ER model with the operator's env-provisioned key WITHOUT re-setting up the gate.
#
# Sibling of deploy/dev/run-live-er-smoke.sh (same structure/flags): where that runner defaults to
# the live ER Handoff probe (tests/live/test_er_handoff_live.py), this one defaults to the live
# FULL-CHAIN forerunner (tests/live/test_xer_full_chain_live.py) — the wired pipeline seam to a
# frozen Command. Both module-skip unless WAREHOUSE_LIVE_ER=1 and each needs a Gemini key in env
# (GEMINI_API_KEY or GOOGLE_API_KEY). The operator provisions the key via ~/.zshenv (see
# docs/dev/07-mode-x-er-live-e2e-runbook.md §4). This wrapper asserts the key is present, prints
# the paid-call gate banner, and runs pytest with WAREHOUSE_LIVE_ER=1 — these calls bill the
# operator's Gemini account, so the default mode is gated and a safe '--check' mode exists that
# NEVER calls the provider.
#
# This script NEVER prints secret values (the key is read from env, never echoed).
#
# Usage:
#   deploy/dev/run-live-er-chain.sh --check                    # safe: assert key + print gate + show cmd, NO call
#   deploy/dev/run-live-er-chain.sh                            # PAID: runs tests/live/test_xer_full_chain_live.py
#   deploy/dev/run-live-er-chain.sh tests/live/test_xer3_chain_live.py -s   # PAID: pass-through pytest args
#
# Exit codes:
#   0 = ran (or --check succeeded), 2 = no Gemini key in env (setup pointer printed).
set -euo pipefail

PYTHON="/Users/kawaguchiryuya/Developer/miniature-warehouse-robotics/.venv/bin/python"
DEFAULT_TEST="tests/live/test_xer_full_chain_live.py"

# 1) Assert a Gemini key is present (value NEVER printed). Either name is accepted, matching
#    run-live-er-smoke.sh:27 and test_er_handoff_live.py:46.
if [ -z "${GEMINI_API_KEY:-}" ] && [ -z "${GOOGLE_API_KEY:-}" ]; then
  cat >&2 <<'EOF'
ERROR: no Gemini key in env (GEMINI_API_KEY or GOOGLE_API_KEY).
The operator provisions it via ~/.zshenv — see docs/dev/07-mode-x-er-live-e2e-runbook.md §4.
Without it, the live ER tests self-skip; do not hardcode the key.
EOF
  exit 2
fi

# 2) Parse a leading --check (safe verification path) without consuming pytest pass-through args.
CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then
  CHECK_ONLY=1
  shift
fi

# Pytest target(s): pass-through args, or the default live ER full-chain forerunner.
if [ "$#" -gt 0 ]; then
  PYTEST_ARGS=("$@")
else
  PYTEST_ARGS=("${DEFAULT_TEST}")
fi

# Always run with -s (per-test summary: token counts / command items). Append it only if the caller
# did not already pass -s, so the previewed and executed commands are identical and never show `-s -s`.
HAS_S=0
for arg in "${PYTEST_ARGS[@]}"; do
  if [ "${arg}" = "-s" ]; then
    HAS_S=1
    break
  fi
done
if [ "${HAS_S}" -eq 0 ]; then
  PYTEST_ARGS+=("-s")
fi

GATE_BANNER='[gate] paid Gemini Robotics-ER call (operator-authorized)'

if [ "${CHECK_ONLY}" -eq 1 ]; then
  echo "PASS   Gemini key present in env (value hidden)"
  echo "${GATE_BANNER}"
  echo "Would run (NOT executed under --check):"
  echo "  WAREHOUSE_LIVE_ER=1 ${PYTHON} -m pytest ${PYTEST_ARGS[*]}"
  exit 0
fi

# 3) Default mode: announce the paid call, then run the live tests (PYTEST_ARGS already carries -s).
echo "${GATE_BANNER}"
exec env WAREHOUSE_LIVE_ER=1 "${PYTHON}" -m pytest "${PYTEST_ARGS[@]}"
