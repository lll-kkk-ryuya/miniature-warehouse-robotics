#!/usr/bin/env bash
# apply-fork.sh — idempotent applier for the ER native-audio Hermes overlay patch.
#
# WHAT: Applies 0001-input_audio-passthrough.patch (2-file transport-only fork) to a TARGET
#       hermes-agent v0.15.1 checkout so its OpenAI-compatible /v1/chat/completions accepts
#       OpenAI `input_audio` content parts and maps them to Gemini native inlineData
#       (mimeType: audio/wav). This adds an INPUT MODALITY only — it does NOT touch
#       orchestration, the Policy Gate, action_map idempotency, 0-dispatch-on-timeout, or
#       eval_sdk outcome scores. See ./README.md and docs/mode-x-er/06 §5 補遺.
#
# WHY:  Unforked Hermes rejects audio with HTTP 400 unsupported_content_type (PROBE-2,
#       measured 2026-06-27). This overlay is the productionization seam that lets the ER
#       audio leg ride the same uniform Hermes transport instead of going direct.
#
# ABSOLUTE SAFETY RULE: never patch/checkout-branch the PERSONAL clone (~/.hermes/hermes-agent)
#       in place. That is the user's daily-driver gateway (port 8642, memory ON). This script
#       REFUSES if HERMES_SRC resolves to it. The intended TARGET is an ISOLATED git worktree
#       of that clone (see README "How to apply"), or a separate clone.
#
# USAGE:
#   HERMES_SRC=/path/to/isolated/worktree  ./apply-fork.sh            # apply (idempotent)
#   HERMES_SRC=/path/to/isolated/worktree  ./apply-fork.sh --check    # dry-run only, no writes
#   HERMES_SRC=/path/to/isolated/worktree  ./apply-fork.sh --revert   # reverse the patch
#
# ENV:
#   HERMES_SRC   REQUIRED. Absolute path to the TARGET hermes-agent checkout to patch.
#                Must NOT equal the personal clone (~/.hermes/hermes-agent).
#   PATCH_FILE   Override the patch path (default: <this dir>/0001-input_audio-passthrough.patch).
#
# EXIT CODES: 0 ok / no-op  |  2 misuse (missing/forbidden HERMES_SRC, bad target)  |  3 patch failure
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_FILE="${PATCH_FILE:-$SCRIPT_DIR/0001-input_audio-passthrough.patch}"

# The personal daily-driver clone that must NEVER be touched in place.
PERSONAL_CLONE="$HOME/.hermes/hermes-agent"

# Files the patch edits (relative to the target checkout root). Used for shape verification.
F_API="gateway/platforms/api_server.py"
F_ADAPTER="agent/gemini_native_adapter.py"

# Marker the patch introduces — its presence means "already applied".
APPLIED_MARKER="_AUDIO_PART_TYPES"

MODE="apply"
case "${1:-}" in
  --check)  MODE="check" ;;
  --revert) MODE="revert" ;;
  "")       MODE="apply" ;;
  *) echo "ERROR: unknown argument '$1' (expected --check | --revert | none)." >&2; exit 2 ;;
esac

# --- HERMES_SRC required ------------------------------------------------------
if [ -z "${HERMES_SRC:-}" ]; then
  echo "ERROR: HERMES_SRC is REQUIRED (absolute path to the TARGET hermes-agent checkout)." >&2
  echo "       Create an ISOLATED worktree of the personal clone first, e.g.:" >&2
  echo "         git -C \"$PERSONAL_CLONE\" worktree add /tmp/hermes-er-fork -b mwr-er-audio HEAD" >&2
  echo "       then: HERMES_SRC=/tmp/hermes-er-fork $0" >&2
  exit 2
fi

# --- resolve to a canonical absolute path (handles symlinks/relative input) ---
canon() {
  # Prefer realpath; fall back to a cd-based resolver (macOS bash 3.2 has no realpath -m).
  if command -v realpath >/dev/null 2>&1; then realpath "$1" 2>/dev/null && return 0; fi
  ( cd "$1" 2>/dev/null && pwd -P )
}
SRC_ABS="$(canon "$HERMES_SRC" || true)"
if [ -z "$SRC_ABS" ] || [ ! -d "$SRC_ABS" ]; then
  echo "ERROR: HERMES_SRC='$HERMES_SRC' is not an existing directory." >&2
  exit 2
fi
PERSONAL_ABS="$(canon "$PERSONAL_CLONE" || echo "$PERSONAL_CLONE")"

# --- REFUSE to touch the personal clone in place -----------------------------
if [ "$SRC_ABS" = "$PERSONAL_ABS" ]; then
  echo "REFUSING: HERMES_SRC resolves to the PERSONAL clone ($PERSONAL_ABS)." >&2
  echo "          Never patch the daily-driver in place. Use an ISOLATED worktree instead:" >&2
  echo "            git -C \"$PERSONAL_CLONE\" worktree add /tmp/hermes-er-fork -b mwr-er-audio HEAD" >&2
  exit 2
fi

# --- patch file present ------------------------------------------------------
if [ ! -f "$PATCH_FILE" ]; then
  echo "ERROR: patch file not found: $PATCH_FILE" >&2
  exit 2
fi

# --- verify the target IS hermes-agent v0.15.1 (the version this patch is cut against) -------
# Primary signal: pyproject name+version. Secondary: the two target files exist in pre-patch shape.
PYPROJECT="$SRC_ABS/pyproject.toml"
verify_version() {
  if [ -f "$PYPROJECT" ]; then
    local name ver
    name="$(grep -m1 -E '^name[[:space:]]*=' "$PYPROJECT" | sed -E 's/.*"([^"]+)".*/\1/' || true)"
    ver="$(grep -m1 -E '^version[[:space:]]*=' "$PYPROJECT" | sed -E 's/.*"([^"]+)".*/\1/' || true)"
    if [ "$name" = "hermes-agent" ] && [ "$ver" = "0.15.1" ]; then
      return 0
    fi
    echo "WARN: $PYPROJECT reports name='$name' version='$ver' (expected hermes-agent 0.15.1)." >&2
  else
    echo "WARN: no pyproject.toml at target root ($PYPROJECT)." >&2
  fi
  # Fall back to structural shape: both target files must exist.
  if [ -f "$SRC_ABS/$F_API" ] && [ -f "$SRC_ABS/$F_ADAPTER" ]; then
    echo "INFO: falling back to file-shape verification (both patch targets present)." >&2
    return 0
  fi
  echo "ERROR: target does not look like hermes-agent v0.15.1 (version mismatch AND missing patch-target files)." >&2
  echo "       Missing one of: $F_API / $F_ADAPTER under $SRC_ABS" >&2
  return 1
}
verify_version || exit 2

# --- idempotency probe: is the patch already applied? ------------------------
already_applied() {
  grep -q "$APPLIED_MARKER" "$SRC_ABS/$F_API" 2>/dev/null
}

# git apply runs relative to the repo; the patch uses a/ b/ prefixes => -p1 from target root.
git_apply() { git -C "$SRC_ABS" apply "$@" "$PATCH_FILE"; }

case "$MODE" in
  check)
    if already_applied; then
      echo "ALREADY APPLIED: '$APPLIED_MARKER' present in $SRC_ABS/$F_API — apply would be a no-op."
      exit 0
    fi
    echo "DRY-RUN: checking whether the patch applies cleanly to $SRC_ABS ..."
    if git_apply --check; then
      echo "OK: patch applies cleanly (no changes written; --check)."
      exit 0
    else
      echo "FAIL: patch does NOT apply cleanly to this target." >&2
      exit 3
    fi
    ;;

  revert)
    if ! already_applied; then
      echo "NO-OP: patch not present ('$APPLIED_MARKER' absent in $F_API) — nothing to revert."
      exit 0
    fi
    echo "Reverting the fork on $SRC_ABS ..."
    if ! git_apply --reverse --check; then
      echo "FAIL: reverse --check failed (local edits drifted from the patch?). Refusing to revert." >&2
      exit 3
    fi
    git_apply --reverse
    echo "REVERTED: removed the ER audio passthrough from $SRC_ABS."
    exit 0
    ;;

  apply)
    if already_applied; then
      echo "ALREADY APPLIED: '$APPLIED_MARKER' present in $SRC_ABS/$F_API — no-op."
      exit 0
    fi
    echo "Dry-run check before applying to $SRC_ABS ..."
    if ! git_apply --check; then
      echo "FAIL: patch does NOT apply cleanly to this target (upstream drift?)." >&2
      echo "      See README MAINTENANCE: re-cut the patch against the new hermes-agent version." >&2
      exit 3
    fi
    git_apply
    echo "APPLIED: ER native-audio passthrough is now in:"
    echo "  - $SRC_ABS/$F_API"
    echo "  - $SRC_ABS/$F_ADAPTER"
    echo "Run the forked modules WITHOUT touching the personal source via PYTHONPATH override:"
    echo "  PYTHONPATH=$SRC_ABS $PERSONAL_CLONE/venv/bin/python -m hermes_cli.main gateway run --accept-hooks"
    echo "(personal clone $PERSONAL_CLONE was NOT modified.)"
    exit 0
    ;;
esac
