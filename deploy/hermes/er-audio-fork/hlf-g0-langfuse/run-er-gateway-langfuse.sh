#!/usr/bin/env bash
# =============================================================================
# run-er-gateway-langfuse.sh — HLF-G0 launcher: the LEAN ER (Gemini Robotics-ER)
#   input_audio fork gateway WITH the Hermes built-in Langfuse plugin turned ON.
# =============================================================================
# WHAT THIS IS FOR (the experiment — read this first)
#   This is the test bed for the user's "Pattern B" instinct: instead of the
#   LLM Bridge wrapping the OpenAI client with `from langfuse.openai import
#   AsyncOpenAI` (Bridge-side tracing), drop that wrapper and turn the Hermes
#   server-side Langfuse plugin ON. Then ONE question decides the design:
#
#     HLF-G0: does the Hermes Langfuse plugin honor an INBOUND trace_id
#             (a trace_id we pass in request metadata), so the Warehouse
#             Orchestrator (#6) can attach outcome scores (SR / SPL / collision)
#             to the SAME trace the LLM call produced?
#       YES -> plugin-ON + no-wrapper is clean. Bridge stops owning the trace.
#       NO  -> we need a fork tweak (read inbound trace_id) OR keep the wrapper.
#
#   ⚠️ HONEST STATUS — read before drawing a conclusion:
#     From the plugin source (~/.hermes/hermes-agent/plugins/observability/
#     langfuse/__init__.py:542-603) the trace_id is MINTED, not read inbound:
#         trace_id = client.create_trace_id(
#                       seed=f"{session_id or 'sessionless'}::{task_id or task_key}")
#     The hooks receive `task_id` / `session_id` (Hermes-internal turn ids) and
#     derive the trace_id deterministically from them. There is NO code path
#     that reads a caller-supplied `trace_id` out of request metadata. So the
#     LITERAL "honor an inbound trace_id field" answer is, on this clone,
#     **NO** (static reading — NOT yet confirmed by a live run).
#       - The *escape hatch* is that the seed is deterministic: if #6 can learn
#         the (session_id, task_id) Hermes used for a turn, it can RECONSTRUCT
#         the same trace_id via create_trace_id(seed=...) and score that trace.
#         Whether (session_id, task_id) are observable/controllable from the
#         Bridge→Hermes request is the thing the LIVE run must establish.
#     This launcher exists to make that live run cheap and ISOLATED. It does
#     NOT decide HLF-G0 by itself — a human runs it with real creds and reads
#     the resulting Langfuse trace. See HLF-G0 NOTES at the bottom.
#
#   SIBLING TOOLS in this dir (this launcher is the GATEWAY half):
#     - run-hlf-g0.sh + hlf_g0_probe.py — the PROBE half: drives one request at
#       the running gateway, then queries the Langfuse API to emit the actual
#       PASS / FAIL / WORKAROUND-VIABLE / INCONCLUSIVE verdict.
#     - README-hlf-g0.md / PLUGIN-TRACEID-ANALYSIS.md — the written analysis
#       (incl. the deterministic-seed workaround via X-Hermes-Session-Id) and
#       RESULT.md — where the human records the live outcome.
#     Sequence: (1) THIS script boots the plugin-ON gateway → (2) run-hlf-g0.sh
#     probes it → (3) human records RESULT.md. Both halves share one isolated
#     langfuse install (default /tmp/mwr-hlf-g0-langfuse-libs).
#
# WHAT IT DOES (delta over run-er-gateway.sh)
#   It SOURCES ../run-er-gateway.sh's worktree/patch/guard/stop/env logic (so the
#   input_audio fork prep is byte-identical and never duplicated), then adds:
#     1. ISOLATED langfuse install:
#          $HERMES_SRC/venv/bin/pip install --target $LANGFUSE_LIBS 'langfuse>=4.9,<5'
#        installed INTO a throwaway dir (default /tmp/mwr-hlf-g0-langfuse-libs,
#        SHARED with the sibling probe), NOT into the personal venv. Idempotent:
#        skips if `import langfuse` already resolves from $LANGFUSE_LIBS with the
#        plugin's required API surface. The range is the SAME one the sibling
#        probe verified live (langfuse 4.9.0) — see VERSION RANGE below.
#     2. ENABLE the plugin against the ISOLATED home:
#          HERMES_HOME=$HERMES_HOME PYTHONPATH=$LANGFUSE_LIBS:$WORKTREE_DIR \
#            $VENV_PY -m hermes_cli.main plugins enable observability/langfuse
#        Idempotent: skips if config.yaml already lists it under plugins.enabled.
#     3. LAUNCH the forked gateway with
#          PYTHONPATH=$LANGFUSE_LIBS:$WORKTREE_DIR
#        so BOTH the input_audio patch (worktree) AND langfuse (isolated libs)
#        import, with NEITHER the personal venv nor ~/.hermes touched.
#
# WHY THE LAYERING WORKS (verified by reading the clone, 2026-06-27)
#   - The Langfuse plugin is BUNDLED in the Hermes source tree
#     (plugins/observability/langfuse/). Hermes resolves bundled plugins from the
#     hermes_cli package location (config.py:6197-6200,
#     `Path(__file__).resolve().parents[1] / "plugins"`). Because we launch with
#     PYTHONPATH=...:$WORKTREE_DIR, hermes_cli (and therefore its bundled plugin)
#     loads FROM THE PATCHED WORKTREE — the plugin travels with the fork. The
#     ONLY missing piece is the `langfuse` SDK, which we supply on PYTHONPATH
#     ahead of the worktree via $LANGFUSE_LIBS. (Confirmed: `langfuse` is NOT in
#     ~/.hermes/hermes-agent/venv — that is the documented blocker.)
#   - plugins.enabled is persisted in $HERMES_HOME/config.yaml (plugins.py:198-225
#     reads `plugins.enabled` via load_config(), which is HERMES_HOME-relative).
#     Enabling against the ISOLATED home never edits the personal ~/.hermes.
#
# VERSION RANGE (decided by reading the plugin AND the sibling probe's verified pin)
#   The plugin imports and calls the MODERN top-level SDK surface (NOT the legacy
#   v2 `langfuse.Langfuse().trace()/.generation()` surface):
#       from langfuse import Langfuse, propagate_attributes   (__init__.py:37)
#       client.create_trace_id(seed=...)                      (:544)
#       client.start_as_current_observation(trace_context=…)  (:567,577,587)
#       root_span.start_observation(...)                      (:609)
#   That surface (propagate_attributes / create_trace_id / start_as_current_
#   observation) is the v3+ line that continues through v4. The SIBLING probe in
#   this dir (hlf_g0_probe.py / run-hlf-g0.sh) VERIFIED this surface against
#   **langfuse 4.9.0** (the same line the LLM Bridge pins), so we pin the SAME
#   range: `langfuse>=4.9,<5`. (A narrower `>=3,<4` guess would NOT match what was
#   verified live.) Override with LANGFUSE_SPEC if a future plugin revision changes
#   the surface — re-check the imports + the sibling probe pin before bumping.
#
# ABSOLUTE ISOLATION RULES (same as the base launcher, plus pip)
#   - NEVER `pip install` into / modify the personal venv
#     (~/.hermes/hermes-agent/venv) or the personal home/source (~/.hermes).
#     langfuse goes into $LANGFUSE_LIBS via `pip install --target` ONLY.
#   - NEVER patch/checkout-branch the personal clone in place — only the isolated
#     worktree (inherited from run-er-gateway.sh's guard_paths()).
#   - HERMES_HOME must be an ISOLATED home (default ~/.hermes-mwr-er-lean); this
#     script REFUSES ~/.hermes. $LANGFUSE_LIBS must NOT be the personal venv or
#     home. Refusals below + inherited guard_paths().
#   - NEVER print secret values. HERMES_LANGFUSE_* (and GOOGLE_API_KEY /
#     API_SERVER_KEY) are sourced from $HERMES_HOME/.env and never echoed.
#
# SECRETS (user-supplied; this script and the agent NEVER read or print them)
#   The Langfuse credentials live in $HERMES_HOME/.env — the SAME .env the base
#   gateway already auto-loads (gateway/run.py:851-852) and that source_env()
#   sources. The user adds these keys themselves; see .env.example here:
#       HERMES_LANGFUSE_PUBLIC_KEY=pk-lf-...     (required)
#       HERMES_LANGFUSE_SECRET_KEY=sk-lf-...     (required)
#       HERMES_LANGFUSE_BASE_URL=https://cloud.langfuse.com   (or self-hosted)
#       HERMES_LANGFUSE_ENV / _RELEASE / _SAMPLE_RATE / _DEBUG / _MAX_CHARS (opt)
#   Without real keys the plugin fails open (hooks no-op) — the gateway still
#   serves input_audio, but no traces are emitted (plugin README + __init__.py
#   _validate_langfuse_key). So for a meaningful HLF-G0 run the user MUST put
#   real pk-lf-/sk-lf- keys in $HERMES_HOME/.env first.
#
# USAGE
#   ./run-er-gateway-langfuse.sh           # prep worktree+patch, install langfuse
#                                          #   (isolated), enable plugin, run (fg)
#   ./run-er-gateway-langfuse.sh --probe   # same prep, run (bg), wait healthy,
#                                          #   POST input_audio, assert HTTP 200,
#                                          #   report whether langfuse loaded +
#                                          #   plugin is enabled, leave running.
#                                          #   (Does NOT verify a trace landed in
#                                          #    Langfuse — that is human-gated.)
#   ./run-er-gateway-langfuse.sh --stop    # stop gateway + remove worktree
#                                          #   (delegates to the base --stop).
#                                          #   Leaves $LANGFUSE_LIBS in place
#                                          #   (cheap reuse); --purge to drop it.
#   ./run-er-gateway-langfuse.sh --purge   # --stop AND remove $LANGFUSE_LIBS.
#   ./run-er-gateway-langfuse.sh --help
#
# PARAMETERS (env vars; sane defaults — base launcher's vars also apply)
#   PORT, HERMES_HOME, HERMES_SRC, WORKTREE_DIR, BRANCH, PATCH_FILE
#                          inherited from run-er-gateway.sh (same defaults)
#   LANGFUSE_LIBS  isolated --target install dir   (default /tmp/mwr-hlf-g0-langfuse-libs;
#                                                   SHARED with the sibling probe)
#   LANGFUSE_SPEC  pip spec for the SDK            (default 'langfuse>=4.9,<5')
#   BASE_LAUNCHER  path to run-er-gateway.sh       (default ../run-er-gateway.sh)
# =============================================================================
set -euo pipefail

# ----------------------------- locate base launcher --------------------------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
BASE_LAUNCHER="${BASE_LAUNCHER:-$SCRIPT_DIR/../run-er-gateway.sh}"
BASE_DIR="$(cd -- "$(dirname -- "$BASE_LAUNCHER")" >/dev/null 2>&1 && pwd 2>/dev/null || dirname -- "$BASE_LAUNCHER")"

# The input_audio patch lives NEXT TO the base launcher (parent dir). We export
# PATCH_FILE BEFORE sourcing the base so the base's `${PATCH_FILE:-$SCRIPT_DIR/…}`
# default does NOT mis-resolve to our temp source file's dir during _load_base.
# (The base derives PATCH_FILE from ${BASH_SOURCE[0]}, which would point at the
# extracted temp file when sourced — so we must pin it here.)
export PATCH_FILE="${PATCH_FILE:-$BASE_DIR/0001-input_audio-passthrough.patch}"

# HERMES_HOME: this Option-D spike keeps its ESTABLISHED lean home (~/.hermes-mwr-er-lean),
# NOT the base launcher's new default fork home (~/.hermes-mwr-er-fork, 2026-07-03). Pin it
# BEFORE sourcing the base so the base's `${HERMES_HOME:-…}` respects this value. This keeps the
# whole hlf-g0-langfuse + spike/ toolchain (whose scripts + docs all reference the lean home)
# internally coherent and SEPARATE from the shipped fork gateway on ~/.hermes-mwr-er-fork.
# (Override with HERMES_HOME=… as before.)
export HERMES_HOME="${HERMES_HOME:-$HOME/.hermes-mwr-er-lean}"

# ----------------------------- our parameters --------------------------------
# Default isolated install dir is SHARED with the sibling probe (run-hlf-g0.sh /
# hlf_g0_probe.py use the same /tmp/mwr-hlf-g0-langfuse-libs), so the gateway and
# the probe import the SAME langfuse build — one --target install serves both.
LANGFUSE_LIBS="${LANGFUSE_LIBS:-/tmp/mwr-hlf-g0-langfuse-libs}"
# Version range: the sibling probe VERIFIED the plugin's API surface against
# langfuse 4.9.0 (create_trace_id / propagate_attributes /
# start_as_current_observation), which is also the line the Bridge pins. So pin
# the SAME range here — NOT a narrower v3 guess. (See VERSION RANGE in the header
# + README-hlf-g0.md "Verified build-time".)
LANGFUSE_SPEC="${LANGFUSE_SPEC:-langfuse>=4.9,<5}"
PLUGIN_KEY="observability/langfuse"

# ----------------------------- arg parse -------------------------------------
MODE="run"
case "${1:-}" in
  --probe) MODE="probe" ;;
  --stop)  MODE="stop" ;;
  --purge) MODE="purge" ;;
  --help|-h)
    # Print the whole header banner (line 2 .. the LAST "# ===…===" closer),
    # stripping the leading "# ". Using the last closer (not the first) so the
    # title sub-banner inside the header does not truncate the help output.
    _hdr_end="$(grep -nE '^# ={70,}$' "${BASH_SOURCE[0]}" | tail -1 | cut -d: -f1)"
    sed -n "2,${_hdr_end:-2}p" "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  "" ) MODE="run" ;;
  * )
    echo "[er-langfuse] unknown argument: $1 (use --probe | --stop | --purge | --help)" >&2
    exit 2
    ;;
esac

# ----------------------------- helpers ---------------------------------------
log() { printf '[er-langfuse] %s\n' "$*" >&2; }
die() { printf '[er-langfuse] ERROR: %s\n' "$*" >&2; exit 1; }

[ -f "$BASE_LAUNCHER" ] || die "base launcher not found: $BASE_LAUNCHER (set BASE_LAUNCHER=…)"

# -----------------------------------------------------------------------------
# REUSE the base launcher's logic without re-running its dispatch. We extract
# just its function/parameter section (see _load_base for how/why) and source
# THAT, obtaining: guard_paths, resolve_port, source_env, pid_on_port,
# prepare_worktree, plus the resolved HERMES_SRC / HERMES_HOME / WORKTREE_DIR /
# BRANCH / VENV_PY vars — so the worktree/patch/guard behavior is the SAME code
# on disk (no copy that can drift from the base launcher).
# -----------------------------------------------------------------------------
_load_base() {
  # The CURRENT base (run-er-gateway.sh) is NOT source-only-aware: sourcing it
  # whole would run its dispatch AND its top-level arg-parse `case "${1:-}"`
  # (which sets a MODE var and can `exit`). So we EXTRACT only the pure
  # function/parameter definitions: everything EXCEPT (a) the final
  # "# ---- dispatch ----" section and (b) the top-level arg-parse block
  # (`MODE="run"` … its closing `esac`). What remains is var defaults + helper
  # functions + worktree prep — sourced verbatim from the base file on disk, so
  # the worktree/patch/guard logic is single-sourced (no copy drift).
  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/er-langfuse-base.XXXXXX.sh")"
  awk '
    # Drop the top-level arg-parse case block: from a line that is exactly
    # MODE="run" (start of the parser) through its matching esac.
    /^MODE="run"$/      { in_argparse=1; next }
    in_argparse==1      { if ($0 ~ /^esac$/) { in_argparse=0 }; next }
    # Drop everything from the final dispatch banner onward.
    /^# -+ dispatch -+$/ { stop=1 }
    stop==1             { next }
    { print }
  ' "$BASE_LAUNCHER" > "$tmp"
  # Guard: ensure we actually captured the worktree prep (fail loud on drift).
  grep -q 'prepare_worktree()' "$tmp" \
    || { rm -f "$tmp"; die "could not extract prepare_worktree() from $BASE_LAUNCHER (format drift?). Inspect the base launcher's '----- dispatch -----' banner."; }
  grep -q 'guard_paths()' "$tmp" \
    || { rm -f "$tmp"; die "could not extract guard_paths() from $BASE_LAUNCHER (format drift?)."; }
  # Belt-and-suspenders: assert the arg-parse case was actually excised so a
  # base-format change can't silently clobber our MODE/exit on the unknown-arg
  # branch.
  if grep -qE '^MODE="run"$' "$tmp"; then
    rm -f "$tmp"; die "failed to excise the base arg-parse block from $BASE_LAUNCHER (format drift?). Refusing to source it."
  fi
  # shellcheck disable=SC1090
  source "$tmp"
  rm -f "$tmp"
}
# Preserve OUR MODE across the source (defensive — the excised block shouldn't
# touch it, but the base may define other vars; MODE is ours alone).
_MWR_MODE="$MODE"
_load_base
MODE="$_MWR_MODE"; unset _MWR_MODE

# After sourcing, these are defined by the base: HERMES_SRC, HERMES_HOME,
# WORKTREE_DIR, BRANCH, VENV_PY, SCRIPT_DIR(base), and the functions. We keep
# OUR SCRIPT_DIR/log/die (re-assert ours, since the base defines its own).
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
log() { printf '[er-langfuse] %s\n' "$*" >&2; }
die() { printf '[er-langfuse] ERROR: %s\n' "$*" >&2; exit 1; }

# ----------------------------- our extra guards ------------------------------
# Refuse a LANGFUSE_LIBS that is (or is inside) the personal venv / home.
guard_langfuse_libs() {
  local rp_libs rp_venv rp_personal
  rp_personal="$(cd "$HOME/.hermes" 2>/dev/null && pwd || echo "$HOME/.hermes")"
  rp_venv="$(cd "$HERMES_SRC/venv" 2>/dev/null && pwd || echo "$HERMES_SRC/venv")"
  # Resolve LANGFUSE_LIBS's parent (it may not exist yet) + the dir if it does.
  if [ -d "$LANGFUSE_LIBS" ]; then
    rp_libs="$(cd "$LANGFUSE_LIBS" && pwd)"
  else
    rp_libs="$LANGFUSE_LIBS"
  fi
  case "$rp_libs/" in
    "$rp_venv/"*|"$rp_venv/") die "LANGFUSE_LIBS ($rp_libs) is inside the personal venv. Refusing — use an isolated dir (e.g. /tmp/mwr-hlf-g0-langfuse-libs)." ;;
    "$rp_personal/"*|"$rp_personal/") die "LANGFUSE_LIBS ($rp_libs) is inside the personal home ~/.hermes. Refusing." ;;
  esac
  [ "$rp_libs" = "$rp_personal" ] && die "LANGFUSE_LIBS is the personal home. Refusing."
  [ "$rp_libs" = "$rp_venv" ] && die "LANGFUSE_LIBS is the personal venv. Refusing."
  return 0
}

# Path used for EVERY hermes/python invocation: isolated libs AHEAD of worktree.
langfuse_pythonpath() {
  printf '%s:%s' "$LANGFUSE_LIBS" "$WORKTREE_DIR"
}

# ----------------------------- isolated langfuse install ---------------------
ensure_langfuse_installed() {
  guard_paths
  guard_langfuse_libs
  local pip_bin="$HERMES_SRC/venv/bin/pip"
  [ -x "$pip_bin" ] || die "personal venv pip not found/executable: $pip_bin"

  # Idempotent: if `import langfuse` already resolves from $LANGFUSE_LIBS AND it
  # exposes the MODERN API surface the plugin needs (top-level propagate_attributes
  # + Langfuse.create_trace_id + start_as_current_observation), skip the install.
  if [ -d "$LANGFUSE_LIBS" ] && \
     PYTHONPATH="$LANGFUSE_LIBS" "$VENV_PY" - <<'PY' >/dev/null 2>&1
import importlib, sys
importlib.import_module("langfuse")
# Plugin's required surface (verified live against langfuse 4.9.0 by the sibling
# probe): top-level Langfuse + propagate_attributes + create_trace_id.
from langfuse import Langfuse, propagate_attributes  # noqa: F401
assert hasattr(Langfuse, "create_trace_id"), "no create_trace_id (wrong/old langfuse)"
assert hasattr(Langfuse, "start_as_current_observation"), "no start_as_current_observation"
sys.exit(0)
PY
  then
    log "langfuse already present in $LANGFUSE_LIBS (plugin API surface OK) — skipping install."
    return 0
  fi

  mkdir -p "$LANGFUSE_LIBS"
  log "installing '$LANGFUSE_SPEC' into ISOLATED dir $LANGFUSE_LIBS (personal venv untouched)."
  # --target keeps it out of the venv; --upgrade so a partial/old dir is fixed.
  "$pip_bin" install --target "$LANGFUSE_LIBS" --upgrade "$LANGFUSE_SPEC"

  # Verify the API surface the plugin needs actually imports now.
  PYTHONPATH="$LANGFUSE_LIBS" "$VENV_PY" - <<'PY' \
    || die "langfuse installed into $LANGFUSE_LIBS but the API surface the plugin needs did not import (create_trace_id / propagate_attributes / start_as_current_observation). Re-check the plugin imports vs LANGFUSE_SPEC."
import sys
from langfuse import Langfuse, propagate_attributes  # noqa: F401
assert hasattr(Langfuse, "create_trace_id")
assert hasattr(Langfuse, "start_as_current_observation")
print("langfuse plugin API surface present", file=sys.stderr)
PY
  log "langfuse install verified (plugin API surface present)."
}

# ----------------------------- enable plugin (isolated home) -----------------
plugin_already_enabled() {
  # True if $HERMES_HOME/config.yaml lists the plugin under plugins.enabled.
  # Cheap textual check (avoids a second python spin); the enable command is
  # itself idempotent so this is just to skip noise / be loud about state.
  local cfg="$HERMES_HOME/config.yaml"
  [ -f "$cfg" ] || return 1
  # Strip comment lines FIRST so an instructional comment that mentions the key
  # (e.g. "# hermes plugins enable observability/langfuse") is NOT a false match
  # — that false positive made the real `plugins enable` step be skipped, so the
  # plugin was never loaded and minted no traces (root cause, 2026-06-27).
  grep -vE '^[[:space:]]*#' "$cfg" 2>/dev/null \
    | grep -qE "(^|[[:space:]\"'-])$PLUGIN_KEY([[:space:]\"']|$)"
}

ensure_plugin_enabled() {
  guard_paths
  [ -f "$HERMES_HOME/config.yaml" ] || \
    die "missing $HERMES_HOME/config.yaml — the isolated ER home is not initialized (run the base run-er-gateway.sh once, or init the lean home first)."

  if plugin_already_enabled; then
    log "plugin $PLUGIN_KEY already enabled in $HERMES_HOME/config.yaml — skipping enable."
    return 0
  fi

  log "enabling Hermes plugin '$PLUGIN_KEY' against ISOLATED home $HERMES_HOME (personal ~/.hermes untouched)."
  # Run the enable command with langfuse + worktree on PYTHONPATH so the plugin
  # (bundled in the worktree) + its SDK both resolve during the enable's load.
  if env \
       HERMES_HOME="$HERMES_HOME" \
       PYTHONPATH="$(langfuse_pythonpath)${PYTHONPATH:+:$PYTHONPATH}" \
       "$VENV_PY" -m hermes_cli.main plugins enable "$PLUGIN_KEY" >&2; then
    log "plugin enable command completed."
  else
    # Treat an "already enabled" exit as success; otherwise fail loud.
    if plugin_already_enabled; then
      log "plugin enable returned non-zero but config now lists it as enabled — treating as idempotent success."
    else
      die "plugins enable $PLUGIN_KEY failed and config.yaml does not list it. Inspect: env HERMES_HOME=$HERMES_HOME PYTHONPATH=$(langfuse_pythonpath) $VENV_PY -m hermes_cli.main plugins enable $PLUGIN_KEY"
    fi
  fi

  plugin_already_enabled || \
    die "post-enable check: $PLUGIN_KEY still not present in $HERMES_HOME/config.yaml plugins.enabled."
}

# ----------------------------- launch (fg) -----------------------------------
launch_gateway_langfuse() {
  source_env   # from base: sources $HERMES_HOME/.env (never prints), fails if absent
  [ -f "$HERMES_HOME/config.yaml" ] || \
    die "missing $HERMES_HOME/config.yaml — isolated ER home not initialized."

  # Warn (do NOT print values) if the Langfuse creds are absent → plugin no-ops.
  if [ -z "${HERMES_LANGFUSE_PUBLIC_KEY:-}" ] || [ -z "${HERMES_LANGFUSE_SECRET_KEY:-}" ]; then
    log "WARNING: HERMES_LANGFUSE_PUBLIC_KEY / _SECRET_KEY not set in \$HERMES_HOME/.env."
    log "         The plugin will load but FAIL OPEN (hooks no-op) — NO traces emitted."
    log "         Add real pk-lf-/sk-lf- keys to $HERMES_HOME/.env for a meaningful HLF-G0 run."
  fi

  if [ -n "${PORT:-}" ]; then export API_SERVER_PORT="$PORT"; fi
  export API_SERVER_ENABLED="${API_SERVER_ENABLED:-true}"
  export HERMES_HOME
  export PYTHONPATH="$(langfuse_pythonpath)${PYTHONPATH:+:$PYTHONPATH}"

  local port; port="$(resolve_port)"
  log "launching lean ER gateway + Langfuse plugin: home=$HERMES_HOME port=$port"
  log "PYTHONPATH = <langfuse libs>:<patched worktree> (input_audio patch AND langfuse both load)"
  log "model=gemini-robotics-er-1.6-preview provider=google tools=[] memory=off; plugin=$PLUGIN_KEY ON"
  log "(secrets incl. HERMES_LANGFUSE_* sourced from \$HERMES_HOME/.env — never printed)"
  exec env \
    HERMES_HOME="$HERMES_HOME" \
    PYTHONPATH="$PYTHONPATH" \
    "$VENV_PY" -m hermes_cli.main gateway run --accept-hooks --replace
}

# ----------------------------- probe (build-confidence, not Langfuse-verify) -
do_probe_langfuse() {
  prepare_worktree           # base: isolated worktree + input_audio patch
  ensure_langfuse_installed  # isolated SDK
  ensure_plugin_enabled      # isolated-home enable
  source_env

  local host port base auth_hdr
  port="$(resolve_port)"
  host="${API_SERVER_HOST:-127.0.0.1}"
  base="http://${host}:${port}"
  auth_hdr="Authorization: Bearer ${API_SERVER_KEY:-}"

  if [ -z "${HERMES_LANGFUSE_PUBLIC_KEY:-}" ] || [ -z "${HERMES_LANGFUSE_SECRET_KEY:-}" ]; then
    log "NOTE: HERMES_LANGFUSE_* not set — gateway will run but emit NO traces (plugin fails open)."
    log "      A real HLF-G0 trace check needs the user's pk-lf-/sk-lf- keys in \$HERMES_HOME/.env."
  fi

  if [ -n "${PORT:-}" ]; then export API_SERVER_PORT="$PORT"; fi
  export API_SERVER_ENABLED="${API_SERVER_ENABLED:-true}"
  export HERMES_HOME
  export PYTHONPATH="$(langfuse_pythonpath)${PYTHONPATH:+:$PYTHONPATH}"

  log "starting gateway in background for probe (home=$HERMES_HOME port=$port, plugin ON)"
  env HERMES_HOME="$HERMES_HOME" PYTHONPATH="$PYTHONPATH" \
    "$VENV_PY" -m hermes_cli.main gateway run --accept-hooks --replace \
    >"$HERMES_HOME/gw.langfuse-probe.log" 2>&1 &
  local gw_pid=$!

  # Wait for /health (no auth). Up to ~60s.
  local healthy=0 i
  for i in $(seq 1 120); do
    if ! kill -0 "$gw_pid" 2>/dev/null; then
      die "gateway process exited during startup — see $HERMES_HOME/gw.langfuse-probe.log"
    fi
    if curl -fsS --max-time 3 "${base}/health" >/dev/null 2>&1; then
      healthy=1; break
    fi
    sleep 0.5
  done
  [ "$healthy" = 1 ] || { kill "$gw_pid" 2>/dev/null || true; die "gateway never became healthy at ${base}/health"; }
  log "gateway healthy at ${base}/health"

  # Confirm langfuse + plugin loaded WITHOUT a crash (greppable signal in log).
  if grep -qiE 'langfuse' "$HERMES_HOME/gw.langfuse-probe.log" 2>/dev/null; then
    log "startup log references langfuse (plugin load path exercised). See $HERMES_HOME/gw.langfuse-probe.log"
  else
    log "NOTE: no 'langfuse' string in startup log yet — plugin may load lazily on first turn."
  fi

  # Authenticated reachability.
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 \
            -H "$auth_hdr" "${base}/v1/models" || true)"
  case "$code" in
    2*) log "authenticated /v1/models accepted the token (HTTP $code)" ;;
    401|403) kill "$gw_pid" 2>/dev/null || true; die "/v1/models HTTP $code — API_SERVER_KEY mismatch between gateway and probe." ;;
    *) kill "$gw_pid" 2>/dev/null || true; die "/v1/models HTTP $code at $base" ;;
  esac

  # Build a tiny WAV via say + afconvert (macOS). If unavailable -> SKIP audio.
  local tmpdir wav_b64 have_audio=0
  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/er-lf-probe.XXXXXX")"
  # EXIT (not RETURN): the non-200 path below calls die->exit, which would skip a
  # RETURN trap and leak $tmpdir. EXIT fires on every path (return, exit, die).
  trap 'rm -rf "$tmpdir"' EXIT
  if command -v say >/dev/null 2>&1 && command -v afconvert >/dev/null 2>&1; then
    if say -o "$tmpdir/say.aiff" "Move the red box to the loading dock." >/dev/null 2>&1 \
       && afconvert -f WAVE -d LEI16@16000 -c 1 "$tmpdir/say.aiff" "$tmpdir/probe.wav" >/dev/null 2>&1; then
      wav_b64="$(base64 < "$tmpdir/probe.wav" | tr -d '\n')"
      have_audio=1
    fi
  fi

  if [ "$have_audio" != 1 ]; then
    log "SKIP input_audio probe: say/afconvert unavailable (audio leg NOT verified on this host)."
    log "gateway is running (pid $gw_pid) with the Langfuse plugin enabled."
    _probe_epilogue "$gw_pid"
    return 0
  fi

  # POST input_audio and assert HTTP 200 (transport seam still works with plugin).
  local body acode
  body="$(printf '{"model":"er","messages":[{"role":"user","content":[{"type":"text","text":"Transcribe the spoken instruction."},{"type":"input_audio","input_audio":{"data":"%s","format":"wav"}}]}],"max_tokens":64}' "$wav_b64")"
  acode="$(printf '%s' "$body" | curl -s -o "$tmpdir/resp.json" -w '%{http_code}' --max-time 60 \
            -H "$auth_hdr" -H 'Content-Type: application/json' \
            -X POST "${base}/v1/chat/completions" --data-binary @- || true)"

  if [ "$acode" = "200" ]; then
    log "PASS: input_audio POST -> HTTP 200 with the Langfuse plugin enabled."
    _probe_epilogue "$gw_pid"
    return 0
  else
    log "response: $(head -c 400 "$tmpdir/resp.json" 2>/dev/null || true)"
    kill "$gw_pid" 2>/dev/null || true
    die "input_audio POST -> HTTP $acode (expected 200). Gateway stopped."
  fi
}

_probe_epilogue() {
  local gw_pid="$1"
  log "------------------------------------------------------------------"
  log "BUILD-CONFIDENCE PROBE COMPLETE. What is and is NOT verified:"
  log "  [verified] gateway boots with langfuse importable + plugin enabled."
  log "  [verified] input_audio transport seam still returns 200 (if audio ran)."
  log "  [NOT verified — HUMAN GATE] that a trace actually LANDED in Langfuse,"
  log "      and the HLF-G0 verdict (inbound trace_id honored / minted / workaround)."
  log "  This launcher does NOT decide HLF-G0. Run the SIBLING PROBE against this"
  log "  running gateway to get the actual verdict:"
  log "      ./run-hlf-g0.sh          # POST -> query Langfuse -> PASS/FAIL/WORKAROUND"
  log "  Prereqs for a meaningful verdict:"
  log "      1) real HERMES_LANGFUSE_* keys in \$HERMES_HOME/.env (NOT placeholders),"
  log "      2) this gateway up with the plugin enabled (it is — pid $gw_pid),"
  log "      3) read README-hlf-g0.md; record the outcome in RESULT.md."
  log "  (Per plugin source the trace_id is MINTED as"
  log "   create_trace_id(seed='<session_id>::<task_id>'), NOT read inbound — the"
  log "   probe confirms this live and tests the deterministic-seed workaround.)"
  log "  gateway still running (pid $gw_pid). Stop: $0 --stop"
  log "------------------------------------------------------------------"
}

# ----------------------------- purge (stop + drop libs) ----------------------
do_purge() {
  # Guard BEFORE doing anything destructive (refuse personal paths first).
  guard_paths
  guard_langfuse_libs
  # Delegate stop+worktree cleanup to the base launcher, then drop libs.
  log "stopping gateway + removing worktree via base launcher --stop."
  HERMES_HOME="$HERMES_HOME" HERMES_SRC="$HERMES_SRC" WORKTREE_DIR="$WORKTREE_DIR" \
    BRANCH="$BRANCH" PORT="${PORT:-}" "$BASE_LAUNCHER" --stop || \
      log "base --stop reported an issue (continuing to purge libs)."
  if [ -d "$LANGFUSE_LIBS" ]; then
    log "removing isolated langfuse libs: $LANGFUSE_LIBS"
    rm -rf "$LANGFUSE_LIBS"
  else
    log "no isolated langfuse libs at $LANGFUSE_LIBS"
  fi
  log "purge complete."
}

# ----------------------------- dispatch --------------------------------------
case "$MODE" in
  stop)
    # Delegate to the base launcher's --stop (single source for stop logic).
    # Keeps $LANGFUSE_LIBS (cheap reuse); use --purge to also drop it.
    log "delegating stop to base launcher (worktree cleanup); langfuse libs kept ($LANGFUSE_LIBS)."
    exec env \
      HERMES_HOME="$HERMES_HOME" HERMES_SRC="$HERMES_SRC" WORKTREE_DIR="$WORKTREE_DIR" \
      BRANCH="$BRANCH" PORT="${PORT:-}" "$BASE_LAUNCHER" --stop
    ;;
  purge)
    do_purge
    ;;
  probe)
    do_probe_langfuse
    ;;
  run)
    prepare_worktree           # base: isolated worktree + input_audio patch
    ensure_langfuse_installed  # isolated SDK (idempotent)
    ensure_plugin_enabled      # isolated-home enable (idempotent)
    launch_gateway_langfuse    # exec gateway with both on PYTHONPATH
    ;;
  *)
    die "internal: unknown MODE=$MODE"
    ;;
esac
