#!/usr/bin/env bash
# =============================================================================
# probe-hlf-g0.sh — DESIGN-ONLY harness for the Hermes Langfuse plugin gate
#                   (HLF-G0 inbound trace_id honor + HLF-G2/G3/G4/G5).
# =============================================================================
# WHAT THIS DECIDES
#   Pattern B (drop the Bridge `from langfuse.openai import AsyncOpenAI` wrapper,
#   turn the Hermes built-in Langfuse plugin ON) is CLEAN iff HLF-G0 PASSES:
#   "does the Hermes Langfuse plugin honor an INBOUND trace_id passed in request
#    metadata, so #6 (Warehouse Orchestrator) can attach outcome scores to the
#    SAME deterministic-seed trace?" (doc13:561 §7.7.1 cond.1 / doc13:520 ①).
#   See WRAPPER-REMOVAL-PLAN.md §2/§6.
#
# STATUS: this is a SCAFFOLD (design-only). The live POST/Langfuse-read steps are
#   intentionally left as TODO blocks (RUN_LIVE=1 -> die placeholder).
#
#   ⚠️ THE REAL PROBE IS THE SIBLING `run-hlf-g0.sh` + `hlf_g0_probe.py` IN THIS DIR.
#   For an actual HLF-G0 PASS/FAIL/INCONCLUSIVE verdict, run `./run-hlf-g0.sh`
#   against a running plugin-ON gateway — NOT this file. This scaffold only refuses
#   unsafe paths, sets up isolation, and prints the canonical gate checklist
#   (doc02:190-195). It exists as the early design stub; the working harness
#   superseded it. (Kept for the gate checklist; do not extend the placeholder.)
#
# SAFETY (mirrors deploy/hermes/er-audio-fork/README.md:65-71,123-127):
#   - NEVER touch personal ~/.hermes or its venv. Refuse if paths point there.
#   - Install langfuse via `pip install --target "$ISOLATED_DIR"` + PYTHONPATH
#     prepend. NEVER pip-install into the Hermes venv.
#   - HERMES_HOME defaults to an ISOLATED home (~/.hermes-mwr-er-lean), never ~/.hermes.
#   - SOURCE HERMES_HOME/.env for secrets; NEVER echo/print secret values.
# =============================================================================
set -euo pipefail

# ---- config (all overridable by env; safe defaults) -------------------------
PERSONAL_HOME="${HOME}/.hermes"
HERMES_HOME="${HERMES_HOME:-${HOME}/.hermes-mwr-er-lean}"
ISOLATED_DIR="${ISOLATED_DIR:-/tmp/mwr-hlf-g0-langfuse}"     # pip --target sink (gitignored)
LANGFUSE_SPEC="${LANGFUSE_SPEC:-langfuse>=4.9,<5}"          # match the sibling real probe (run-hlf-g0.sh) — plugin uses the v4 surface (create_trace_id / propagate_attributes)
GATEWAY_BASE_URL="${GATEWAY_BASE_URL:-http://127.0.0.1:8644/v1}"  # lean ER gateway (README:122)
RUN_LIVE="${RUN_LIVE:-0}"                                    # 1 = attempt live probe (MAIN SESSION only)

die() { printf 'probe-hlf-g0: ERROR: %s\n' "$1" >&2; exit 2; }
note() { printf 'probe-hlf-g0: %s\n' "$1" >&2; }

# ---- refuse personal-path footguns -----------------------------------------
case "$HERMES_HOME" in
  "$PERSONAL_HOME"|"$PERSONAL_HOME"/*)
    die "HERMES_HOME ($HERMES_HOME) is the PERSONAL home; refusing. Use an isolated home." ;;
esac
case "$ISOLATED_DIR" in
  "$PERSONAL_HOME"|"$PERSONAL_HOME"/*)
    die "ISOLATED_DIR ($ISOLATED_DIR) is inside the personal home; refusing." ;;
esac
[ -d "${PERSONAL_HOME}/hermes-agent/venv" ] && \
  note "note: personal venv exists at ${PERSONAL_HOME}/hermes-agent/venv — will NOT be modified."

# ---- isolated langfuse install (no personal venv mutation) ------------------
setup_isolation() {
  mkdir -p "$ISOLATED_DIR"
  note "installing '$LANGFUSE_SPEC' into ISOLATED_DIR=$ISOLATED_DIR (pip --target)"
  python3 -m pip install --quiet --target "$ISOLATED_DIR" "$LANGFUSE_SPEC" \
    || die "isolated langfuse install failed; pin LANGFUSE_SPEC from the plugin imports."
  export PYTHONPATH="${ISOLATED_DIR}:${PYTHONPATH:-}"
  note "PYTHONPATH prepended with ISOLATED_DIR (langfuse resolves from isolated dir)."
}

# ---- secrets: source only, never echo --------------------------------------
load_secrets() {
  local envf="${HERMES_HOME}/.env"
  [ -f "$envf" ] || die "missing $envf — add HERMES_LANGFUSE_* there (placeholders in .env.example). Never commit it."
  set -a; # shellcheck disable=SC1090
  . "$envf"; set +a
  # presence-only assertions (NEVER print the values)
  : "${HERMES_LANGFUSE_PUBLIC_KEY:?HERMES_LANGFUSE_PUBLIC_KEY unset in $envf}"
  : "${HERMES_LANGFUSE_SECRET_KEY:?HERMES_LANGFUSE_SECRET_KEY unset in $envf}"
  : "${HERMES_LANGFUSE_BASE_URL:?HERMES_LANGFUSE_BASE_URL unset in $envf}"
  note "HERMES_LANGFUSE_{PUBLIC,SECRET}_KEY + _BASE_URL present (values not shown)."
}

# ---- gate checklist (always printed; authoritative gate = doc02:190-195) ------
print_gate_checklist() {
  cat >&2 <<'EOF'
HLF gate checklist (canonical = docs/productization/02-l4-robotics-bridge-box.md:190-195
                    = docs/architecture/13-hermes-setup.md:561-566 §7.7.1 conditions 1-6):
  [ ] HLF-G0  trace id passthrough: inbound trace_id (or equiv correlation id) honored -> same trace
  [ ] HLF-G1  metadata: gen_id/run_id/provider/mode/env/prompt land in trace metadata/tags
  [ ] HLF-G2  score join: the (Bridge-external) Warehouse Orchestrator can create_score on that trace
  [ ] HLF-G3  span shape: MCP tool span + model generation join the SAME trace
  [ ] HLF-G4  fail-open: plugin/Langfuse failure does not break 0-dispatch / fail-open robot control
  [ ] HLF-G5  no double generation: wrapper dropped + plugin ON => generation recorded EXACTLY ONCE
Record PASS/FAIL into RESULT.md. HLF-G0 PASS (+G5) is the gate to start the Bridge-code PR.
(managed-prompt link is NOT a separate doc02 gate; it is an HLF-G1 / Pattern-A edge — see README-hlf-g0.md.)
EOF
}

# ---- live probe (PLACEHOLDER — main session implements + runs) --------------
run_live_probe() {
  note "RUN_LIVE=1 requested."
  setup_isolation
  load_secrets
  # TODO(main session, live & sequential): with HERMES_LANGFUSE_* loaded and the
  # lean ER gateway running (run-er-gateway.sh), POST a chat/completion to
  # GATEWAY_BASE_URL carrying a deterministic inbound trace_id
  #   seed     = "${WAREHOUSE_RUN_ID}:${gen_id}"
  #   trace_id = langfuse create_trace_id(seed)  (normalize: 32-hex, no dash)
  # in the metadata channel the plugin reads (extra_body/metadata/header — confirm
  # by reading the plugin source's `metadata`/`trace_context`/`trace_id` refs).
  # Then read Langfuse for that trace_id and assert HLF-G0..G5. Print ONLY PASS/FAIL.
  die "live probe is a placeholder; implement + run in the MAIN SESSION (design-only here)."
}

main() {
  note "design-only HLF-G0 probe scaffold (Pattern B decider). See WRAPPER-REMOVAL-PLAN.md."
  print_gate_checklist
  if [ "$RUN_LIVE" = "1" ]; then
    run_live_probe
  else
    note "dry scaffold: isolation/secrets/live steps NOT executed (RUN_LIVE=1 to attempt; main session only)."
    note "no live gateway hit, no Langfuse hit — by design."
  fi
}

main "$@"
