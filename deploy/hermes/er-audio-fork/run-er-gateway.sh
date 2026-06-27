#!/usr/bin/env bash
# =============================================================================
# run-er-gateway.sh — production launcher for the LEAN ER (Gemini Robotics-ER)
#                     Hermes gateway WITH the input_audio passthrough fork.
# =============================================================================
# WHAT IT DOES
#   1. Creates an ISOLATED git worktree of the personal Hermes clone
#      (`git -C $HERMES_SRC worktree add $WORKTREE_DIR -b $BRANCH HEAD`) — a
#      throwaway source tree with NO venv/node_modules (gitignored), so the
#      personal clone is NEVER patched/checked-out/branch-switched in place.
#   2. Applies 0001-input_audio-passthrough.patch into that worktree
#      (idempotent: skips if already applied).
#   3. Launches the lean ER gateway with:
#        PYTHONPATH=$WORKTREE_DIR  (overrides the editable PEP660 finder, so the
#                                   PATCHED modules load from the worktree)
#        $HERMES_SRC/venv/bin/python -m hermes_cli.main gateway run --accept-hooks
#                                   (reuses the personal venv's deps WITHOUT
#                                    touching personal source)
#        HERMES_HOME=$HERMES_HOME  (ISOLATED home, default ~/.hermes-mwr-er-lean —
#                                   NEVER ~/.hermes, the personal daily driver)
#
# WHY (design正本 — verified live 2026-06-27):
#   - The fork is TRANSPORT-ONLY: it accepts OpenAI `input_audio` content parts
#     and maps them to Gemini native inlineData{mimeType:"audio/<fmt>"}. It adds
#     an INPUT MODALITY; it does NOT touch orchestration/safety. Never changed:
#     action_map idempotency mint, Policy Gate, 0-dispatch-on-timeout, eval_sdk
#     outcome scores (result/SR/SPL/collision/deadlock). See
#     docs/mode-x-er/06-unfrozen-contract-resolutions.md §5 (+ §5 補遺),
#     PR #355, issue #356.
#   - LIVE RESULT (2026-06-27): POST input_audio -> HTTP 200; ER understood NATIVE
#     audio; lean latency median 3.69s vs direct 4.24s (n=4, comparable, ER-
#     thinking confound); +~408 prompt tokens/call. Hermes is a SINGLE server-side
#     active model (no per-request provider routing), so a 4-provider comparison
#     needs per-provider gateways (config + restart).
#   - "default=Hermes for audio" is the TARGET this productionization enables;
#     until deployed the current audio leg = DIRECT (permanent fallback).
#
# ABSOLUTE RULES
#   - NEVER patch/modify/checkout-branch the personal clone ($HERMES_SRC) in
#     place — only isolated worktrees. This script REFUSES if HERMES_HOME is the
#     personal home, or if WORKTREE_DIR is the personal clone/home.
#   - NEVER print secret values (GOOGLE_API_KEY / API_SERVER_KEY). They are
#     sourced, used, and never echoed.
#
# USAGE
#   ./run-er-gateway.sh            # prepare worktree + patch, then run gateway (fg)
#   ./run-er-gateway.sh --probe    # run gateway (bg), wait healthy, POST an
#                                  #   input_audio test (generated wav via
#                                  #   say+afconvert, else SKIP), assert HTTP 200,
#                                  #   then leave it running
#   ./run-er-gateway.sh --stop     # kill the gateway by API_SERVER_PORT and
#                                  #   remove the isolated worktree (cleanup)
#   ./run-er-gateway.sh --help
#
# PARAMETERS (env vars; sane defaults)
#   PORT          API server port               (default 8644)
#   HERMES_HOME   ISOLATED gateway home         (default ~/.hermes-mwr-er-lean)
#   HERMES_SRC    personal Hermes clone         (default ~/.hermes/hermes-agent)
#   WORKTREE_DIR  isolated patched source tree  (default /tmp/mwr-er-fork-worktree)
#   BRANCH        worktree branch name          (default mwr-er-audio-prod)
#   PATCH_FILE    fork patch                    (default <script dir>/0001-input_audio-passthrough.patch)
#
# Secrets live in $HERMES_HOME/.env (GOOGLE_API_KEY + API_SERVER_KEY +
# API_SERVER_HOST/PORT). Copy .env.example there and fill it in. The gateway
# also auto-loads $HERMES_HOME/.env (gateway/run.py:851-852); this script sources
# it too so the host-side health/probe curls can authenticate.
# =============================================================================
set -euo pipefail

# ----------------------------- parameters ------------------------------------
HERMES_SRC="${HERMES_SRC:-$HOME/.hermes/hermes-agent}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes-mwr-er-lean}"
WORKTREE_DIR="${WORKTREE_DIR:-/tmp/mwr-er-fork-worktree}"
BRANCH="${BRANCH:-mwr-er-audio-prod}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PATCH_FILE="${PATCH_FILE:-$SCRIPT_DIR/0001-input_audio-passthrough.patch}"

# Personal daily-driver paths we must NEVER touch in place.
PERSONAL_HOME="$HOME/.hermes"
VENV_PY="$HERMES_SRC/venv/bin/python"

MODE="run"
case "${1:-}" in
  --probe) MODE="probe" ;;
  --stop)  MODE="stop" ;;
  --help|-h)
    # Print the header banner (line 2 .. the LAST "# ===…===" closer), stripping
    # the leading "# ". Using the dynamically-found closer (not a hardcoded line
    # number) so edits to the header never spill raw source into --help output.
    _hdr_end="$(grep -nE '^# ={70,}$' "${BASH_SOURCE[0]}" | tail -1 | cut -d: -f1)"
    sed -n "2,${_hdr_end:-67}p" "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  "" ) MODE="run" ;;
  * )
    echo "[er-gateway] unknown argument: $1 (use --probe | --stop | --help)" >&2
    exit 2
    ;;
esac

# ----------------------------- helpers ---------------------------------------
log()  { printf '[er-gateway] %s\n' "$*" >&2; }
die()  { printf '[er-gateway] ERROR: %s\n' "$*" >&2; exit 1; }

# Refuse-to-touch guards (run for every mode).
guard_paths() {
  local rp_home rp_wt rp_personal rp_src
  rp_personal="$(cd "$PERSONAL_HOME" 2>/dev/null && pwd || echo "$PERSONAL_HOME")"
  rp_src="$(cd "$HERMES_SRC" 2>/dev/null && pwd || echo "$HERMES_SRC")"
  rp_home="$HERMES_HOME"; [ -d "$rp_home" ] && rp_home="$(cd "$rp_home" && pwd)"
  rp_wt="$WORKTREE_DIR";  [ -d "$rp_wt" ]   && rp_wt="$(cd "$rp_wt" && pwd)"

  [ "$rp_home" = "$rp_personal" ] && \
    die "HERMES_HOME ($rp_home) is the personal daily-driver. Use an isolated home (e.g. ~/.hermes-mwr-er-lean)."
  [ "$rp_wt" = "$rp_src" ] && \
    die "WORKTREE_DIR ($rp_wt) is the personal clone. The worktree MUST be a separate path."
  [ "$rp_wt" = "$rp_personal" ] && \
    die "WORKTREE_DIR ($rp_wt) is the personal home. Refusing."
  # Defense in depth: the gateway HOME must not be the patched source tree.
  [ "$rp_home" = "$rp_src" ] && \
    die "HERMES_HOME ($rp_home) equals HERMES_SRC. The gateway home and the patched source MUST be separate paths."
  [ "$rp_home" = "$rp_wt" ] && \
    die "HERMES_HOME ($rp_home) equals WORKTREE_DIR. The gateway home and the worktree MUST be separate paths."
  return 0
}

# Resolve API server port for stop/health WITHOUT echoing secrets.
# Order: explicit PORT env > $HERMES_HOME/.env API_SERVER_PORT > 8644.
resolve_port() {
  if [ -n "${PORT:-}" ]; then
    printf '%s' "$PORT"; return 0
  fi
  local p=""
  if [ -f "$HERMES_HOME/.env" ]; then
    p="$(grep -E '^[[:space:]]*API_SERVER_PORT=' "$HERMES_HOME/.env" 2>/dev/null \
          | tail -1 | sed -E 's/^[[:space:]]*API_SERVER_PORT=//; s/[[:space:]]*$//' || true)"
  fi
  printf '%s' "${p:-8644}"
}

# Source $HERMES_HOME/.env into THIS shell (export keys) — never print values.
source_env() {
  [ -f "$HERMES_HOME/.env" ] || die "missing $HERMES_HOME/.env — copy $SCRIPT_DIR/.env.example there and fill it in."
  set -a
  # shellcheck disable=SC1090
  . "$HERMES_HOME/.env"
  set +a
}

# Find the PID listening on a TCP port (macOS/Linux lsof).
pid_on_port() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | head -1 || true
}

# True iff $WORKTREE_DIR is a REGISTERED worktree of $HERMES_SRC. Compares the
# `worktree <path>` porcelain lines by exact, PHYSICAL-path equality (pwd -P, so
# the /tmp -> /private/tmp symlink macOS uses resolves the SAME on both sides) —
# NOT a substring grep, which would false-match a path that is merely a prefix
# of (or contained in) another registered worktree's path (e.g. /tmp/x vs
# /private/tmp/x or /tmp/x-suffix).
worktree_registered() {
  local want reg
  want="$WORKTREE_DIR"; [ -d "$want" ] && want="$(cd "$want" && pwd -P)"
  while IFS= read -r reg; do
    [ "$reg" = "${reg#worktree }" ] && continue   # only `worktree <path>` lines
    reg="${reg#worktree }"
    [ -d "$reg" ] && reg="$(cd "$reg" && pwd -P)"
    [ "$reg" = "$want" ] && return 0
  done < <(git -C "$HERMES_SRC" worktree list --porcelain 2>/dev/null)
  return 1
}

# ----------------------------- stop ------------------------------------------
do_stop() {
  guard_paths
  local port pid
  port="$(resolve_port)"
  pid="$(pid_on_port "$port")"
  if [ -n "$pid" ]; then
    log "stopping gateway on port $port (pid $pid)"
    kill "$pid" 2>/dev/null || true
    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
      pid="$(pid_on_port "$port")"; [ -z "$pid" ] && break
      sleep 0.5
    done
    pid="$(pid_on_port "$port")"
    if [ -n "$pid" ]; then
      log "force-killing pid $pid"
      kill -9 "$pid" 2>/dev/null || true
    fi
  else
    log "no gateway listening on port $port"
  fi

  # Remove the isolated worktree (idempotent).
  if git -C "$HERMES_SRC" rev-parse --git-dir >/dev/null 2>&1; then
    if worktree_registered; then
      log "removing isolated worktree $WORKTREE_DIR"
      git -C "$HERMES_SRC" worktree remove --force "$WORKTREE_DIR" 2>/dev/null \
        || log "worktree remove reported an issue (continuing)"
    fi
    git -C "$HERMES_SRC" worktree prune 2>/dev/null || true
    # Drop the throwaway branch if present and no longer checked out.
    if git -C "$HERMES_SRC" show-ref --verify --quiet "refs/heads/$BRANCH"; then
      git -C "$HERMES_SRC" branch -D "$BRANCH" 2>/dev/null || true
    fi
  fi
  log "stop complete."
}

# ----------------------- prepare isolated worktree ---------------------------
prepare_worktree() {
  guard_paths
  [ -x "$VENV_PY" ] || die "personal venv python not found/executable: $VENV_PY"
  [ -f "$PATCH_FILE" ] || die "patch not found: $PATCH_FILE"
  git -C "$HERMES_SRC" rev-parse --git-dir >/dev/null 2>&1 \
    || die "HERMES_SRC is not a git repo: $HERMES_SRC"

  # Create the worktree if absent (idempotent). Pin to HEAD of the personal clone.
  if worktree_registered; then
    log "reusing existing isolated worktree: $WORKTREE_DIR"
  else
    [ -e "$WORKTREE_DIR" ] && die "WORKTREE_DIR exists but is not a registered worktree: $WORKTREE_DIR"
    log "creating isolated worktree: $WORKTREE_DIR (branch $BRANCH @ $HERMES_SRC HEAD)"
    if git -C "$HERMES_SRC" show-ref --verify --quiet "refs/heads/$BRANCH"; then
      # Branch left over from a prior run — attach the worktree to it.
      git -C "$HERMES_SRC" worktree add "$WORKTREE_DIR" "$BRANCH"
    else
      git -C "$HERMES_SRC" worktree add "$WORKTREE_DIR" -b "$BRANCH" HEAD
    fi
  fi

  # Apply the patch idempotently. If it reverse-applies cleanly it's already in.
  if git -C "$WORKTREE_DIR" apply --reverse --check "$PATCH_FILE" >/dev/null 2>&1; then
    log "patch already applied in worktree — skipping."
  elif git -C "$WORKTREE_DIR" apply --check "$PATCH_FILE" >/dev/null 2>&1; then
    log "applying input_audio passthrough patch."
    git -C "$WORKTREE_DIR" apply "$PATCH_FILE"
  else
    die "patch does not apply cleanly to $WORKTREE_DIR (HEAD drift?). Inspect $PATCH_FILE."
  fi

  # Sanity: confirm the patched marker is present (transport seam, not safety).
  grep -q "_AUDIO_PART_TYPES" "$WORKTREE_DIR/gateway/platforms/api_server.py" 2>/dev/null \
    || die "patched marker _AUDIO_PART_TYPES missing after apply — refusing to launch."
  log "worktree ready (patched, isolated)."
}

# ----------------------------- launch ----------------------------------------
launch_gateway() {
  source_env
  # Ensure the lean ER home actually carries the lean config (fail loud if not).
  # This package does NOT auto-seed config.yaml; initialize the lean home once
  # (README "一気通貫 > 初期化"):  mkdir -p "$HERMES_HOME";
  #   cp <repo>/deploy/dev/hermes-er/config.lean.yaml "$HERMES_HOME/config.yaml"
  #   cp "$SCRIPT_DIR/.env.example" "$HERMES_HOME/.env"   # then fill it in
  # (or run the existing deploy/dev/run-er-hermes.sh once, which seeds the same home).
  [ -f "$HERMES_HOME/config.yaml" ] || \
    die "missing $HERMES_HOME/config.yaml — initialize the lean ER home first: cp <repo>/deploy/dev/hermes-er/config.lean.yaml $HERMES_HOME/config.yaml (provider google, ER model, api_server: [] zero tools). See README '一気通貫 > 初期化'."

  # Explicit PORT overrides; otherwise .env is the source of truth.
  if [ -n "${PORT:-}" ]; then export API_SERVER_PORT="$PORT"; fi
  export API_SERVER_ENABLED="${API_SERVER_ENABLED:-true}"
  export HERMES_HOME
  export PYTHONPATH="$WORKTREE_DIR${PYTHONPATH:+:$PYTHONPATH}"

  local port; port="$(resolve_port)"
  log "launching lean ER gateway: home=$HERMES_HOME port=$port (PYTHONPATH override -> patched modules)"
  log "model=gemini-robotics-er-1.6-preview provider=google tools=[] memory=off (lean transport)"
  log "(secrets sourced from $HERMES_HOME/.env — not printed)"
  # --replace: cleanly take over a stale instance on THIS isolated home.
  exec env \
    HERMES_HOME="$HERMES_HOME" \
    PYTHONPATH="$PYTHONPATH" \
    "$VENV_PY" -m hermes_cli.main gateway run --accept-hooks --replace
}

# ---------------------- probe (run + assert input_audio 200) -----------------
do_probe() {
  prepare_worktree
  source_env

  local host port base auth_hdr
  port="$(resolve_port)"
  host="${API_SERVER_HOST:-127.0.0.1}"
  base="http://${host}:${port}"
  # API_SERVER_KEY is sourced (never printed); build the header without logging it.
  auth_hdr="Authorization: Bearer ${API_SERVER_KEY:-}"

  if [ -n "${PORT:-}" ]; then export API_SERVER_PORT="$PORT"; fi
  export API_SERVER_ENABLED="${API_SERVER_ENABLED:-true}"
  export HERMES_HOME
  export PYTHONPATH="$WORKTREE_DIR${PYTHONPATH:+:$PYTHONPATH}"

  log "starting gateway in background for probe (home=$HERMES_HOME port=$port)"
  env HERMES_HOME="$HERMES_HOME" PYTHONPATH="$PYTHONPATH" \
    "$VENV_PY" -m hermes_cli.main gateway run --accept-hooks --replace \
    >"$HERMES_HOME/gw.probe.log" 2>&1 &
  local gw_pid=$!

  # Wait for /health (no auth). Up to ~60s.
  local healthy=0 i
  for i in $(seq 1 120); do
    if ! kill -0 "$gw_pid" 2>/dev/null; then
      die "gateway process exited during startup — see $HERMES_HOME/gw.probe.log"
    fi
    if curl -fsS --max-time 3 "${base}/health" >/dev/null 2>&1; then
      healthy=1; break
    fi
    sleep 0.5
  done
  [ "$healthy" = 1 ] || { kill "$gw_pid" 2>/dev/null || true; die "gateway never became healthy at ${base}/health"; }
  log "gateway healthy at ${base}/health"

  # Authenticated reachability (matches the Bridge token path).
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 \
            -H "$auth_hdr" "${base}/v1/models" || true)"
  case "$code" in
    2*) log "authenticated /v1/models accepted the token (HTTP $code)" ;;
    401|403) kill "$gw_pid" 2>/dev/null || true; die "/v1/models HTTP $code — API_SERVER_KEY mismatch between gateway and probe." ;;
    *) kill "$gw_pid" 2>/dev/null || true; die "/v1/models HTTP $code at $base" ;;
  esac

  # Build a tiny WAV via say + afconvert (macOS). If unavailable -> SKIP audio probe.
  local tmpdir wav_b64 have_audio=0
  tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/er-probe.XXXXXX")"
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
    log "SKIP input_audio probe: say/afconvert unavailable (NOT verified on this host)."
    log "gateway is running (pid $gw_pid). Stop with: $0 --stop"
    return 0
  fi

  # POST input_audio to /v1/chat/completions and assert HTTP 200.
  local body acode
  body="$(printf '{"model":"er","messages":[{"role":"user","content":[{"type":"text","text":"Transcribe the spoken instruction."},{"type":"input_audio","input_audio":{"data":"%s","format":"wav"}}]}],"max_tokens":64}' "$wav_b64")"
  acode="$(printf '%s' "$body" | curl -s -o "$tmpdir/resp.json" -w '%{http_code}' --max-time 60 \
            -H "$auth_hdr" -H 'Content-Type: application/json' \
            -X POST "${base}/v1/chat/completions" --data-binary @- || true)"

  if [ "$acode" = "200" ]; then
    log "PASS: input_audio POST -> HTTP 200 (native-audio transport seam live)."
    log "(response body in $tmpdir/resp.json — removed on exit; gateway pid $gw_pid still running)"
    log "stop with: $0 --stop"
    return 0
  else
    log "response: $(head -c 400 "$tmpdir/resp.json" 2>/dev/null || true)"
    kill "$gw_pid" 2>/dev/null || true
    die "input_audio POST -> HTTP $acode (expected 200). Gateway stopped."
  fi
}

# ----------------------------- dispatch --------------------------------------
case "$MODE" in
  stop)  do_stop ;;
  probe) do_probe ;;
  run)   prepare_worktree; launch_gateway ;;
  *)     die "internal: unknown MODE=$MODE" ;;
esac
