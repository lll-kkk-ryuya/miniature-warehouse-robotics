#!/usr/bin/env bash
# XER6 X-lite live sim runbook — DRY scaffold (PRINTS steps + PREFLIGHT checks only).
#
# This script does NOT run the sim, does NOT run pytest, and NEVER makes a provider
# call. It only:
#   1) preflight-checks the local tooling (docker / venv python / repo layout),
#   2) prints the ordered operator steps (bring-up -> offline Command -> Nav2 dispatch),
#   3) points at the runbook + the real launchers to run by hand.
#
# The actual sim bring-up is deploy/dev/run-sim-cockpit.sh + deploy/dev/run-mode-a-live.sh
# (human-gated, operator's Docker/Nav2 machine). The offline Command is built by
# tests/unit/test_l3_pipeline.py (compile_raw_output). Full procedure + safety GO/No-Go:
#   docs/dev/08-xer6-live-sim-x-lite-runbook.md
#
# Secrets: this scaffold reads no .env and prints no secret values.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_PY="/Users/kawaguchiryuya/Developer/miniature-warehouse-robotics/.venv/bin/python"
RUNBOOK="docs/dev/08-xer6-live-sim-x-lite-runbook.md"

ok()   { printf 'OK     %s\n' "$*"; }
warn() { printf 'WARN   %s\n' "$*"; }
info() { printf 'INFO   %s\n' "$*"; }

echo "=== XER6 X-lite live sim — DRY scaffold (no sim run, no pytest, no provider call) ==="
echo

# --- 1) preflight (read-only; never actuates) --------------------------------------------
echo "[1/3] preflight"

if command -v docker >/dev/null 2>&1; then
  ok "docker present ($(command -v docker))"
else
  warn "docker NOT found — sim bring-up (Step A/B) needs Docker on the operator machine."
fi

if [ -x "$VENV_PY" ]; then
  ok "venv python present ($VENV_PY)"
else
  warn "venv python NOT found at $VENV_PY — offline Command build (Step 3) needs it."
fi

for f in \
  deploy/dev/run-sim-cockpit.sh \
  deploy/dev/run-mode-a-live.sh \
  deploy/dev/install-nav2-e2e.sh \
  tests/unit/test_l3_pipeline.py \
  ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/pipeline.py \
  ws/src/warehouse_llm_bridge/warehouse_llm_bridge/action_map.py \
  "$RUNBOOK"; do
  if [ -e "$REPO_ROOT/$f" ]; then
    ok "found $f"
  else
    warn "MISSING $f"
  fi
done
echo

# --- 2) ordered operator steps (printed only — run by hand) -------------------------------
echo "[2/3] ordered steps (run these by hand — this scaffold does NOT run them)"
cat <<'EOF'

  A. sim cockpit (Gazebo + Nav2 container):
       deploy/dev/run-sim-cockpit.sh
       docker exec mwr-sim bash /ws/deploy/dev/install-nav2-e2e.sh

  B. full stack (sim + Nav2 + Bridge + Nav2 Bridge), X-lite / no司令官 Hermes:
       TRAFFIC_MODE=none SCENARIO=default deploy/dev/run-mode-a-live.sh --no-restart
       # noVNC: http://127.0.0.1:6082  (confirm /bot1 /bot2 Nav2 are active)

  3. offline frozen Command (cycle 1 = bot1 -> shelf_1; network-free, no charge):
       <venv-python> -m pytest tests/unit/test_l3_pipeline.py -q
       # compile_raw_output: red_box -> shelf_1 (bot1). t2(bot2) is `after t1` -> next cycle.

  4. dispatch to Nav2 (the EXISTING L2 path — NO new actuation here):
       Command -> action_map.command_to_tool_calls(cmd, gen_id)
               -> dispatch_task(robot, dropoff, gen_id, idempotency_key)
               -> Warehouse MCP -> Policy Gate -> POST /api/v1/navigate -> Nav2
       cycle1: bot1 -> shelf_1 ; wait GET /api/v1/status/bot1 (t1.completed) ;
       cycle2: re-compile -> bot2 -> shelf_2.

  5. safety GO/No-Go (must all hold; see runbook §5/§6):
       - 0-dispatch (R-26): non-accept plan -> empty Command.
       - Policy Gate: destination ∈ KNOWN_LOCATIONS only.
       - L0 firmware clamp NOT bypassed: cmd_vel <= 0.3 m/s.
       - collision_monitor / twist_mux / Emergency Guardian live.

EOF

# --- 3) pointers -------------------------------------------------------------------------
echo "[3/3] pointers"
info "Full procedure + Go/No-Go transcription table: $RUNBOOK"
info "Live ER leg (optional, PAID, human-gate): docs/dev/07-mode-x-er-live-e2e-runbook.md §3-4.5"
info "This scaffold ran no sim, no pytest, and no provider call."
