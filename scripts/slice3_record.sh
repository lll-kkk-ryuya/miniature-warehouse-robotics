#!/usr/bin/env bash
# =============================================================================
# slice3_record.sh - wrap noVNC / X screen capture for the slice3 demo recording.
#
# Wraps the start/stop of an ffmpeg x11grab capture of the (noVNC) X display so the
# #156 slice3 head-on demo can be recorded for the first public/sales clip (3-stage
# release, round 2026-06-06). ACTUAL capture is a HUMAN-gated step (a human runs this
# script); Claude never starts a recording. If ffmpeg is unavailable the script prints
# the manual capture steps instead of failing — it is a convenience wrapper, not a
# dependency of the demo.
#
#   slice3_record.sh start [OUTPUT.mp4]   begin capture (writes a PID file)
#   slice3_record.sh stop                 finish capture cleanly (SIGINT -> finalize mp4)
#   slice3_record.sh status               report whether a capture is running
#
# Env:
#   RECORD_DISPLAY   X display to grab. Default: $DISPLAY or :1.0 (tiryoh noVNC).
#   RECORD_SIZE      WxH override. Default: autodetected via xdpyinfo, else 1280x720.
#   RECORD_FPS       frames/sec. Default: 25.
#   RECORD_DIR       output + pidfile + log dir. Default: $PWD.
# =============================================================================
set -u -o pipefail

RECORD_DISPLAY="${RECORD_DISPLAY:-${DISPLAY:-:1.0}}"
RECORD_FPS="${RECORD_FPS:-25}"
RECORD_DIR="${RECORD_DIR:-$PWD}"
PID_FILE="${RECORD_DIR}/.slice3_record.pid"
LOG_FILE="${RECORD_DIR}/.slice3_record.log"

have() {
  command -v "$1" >/dev/null 2>&1
}

usage() {
  cat <<'EOF'
Usage:
  scripts/slice3_record.sh start [OUTPUT.mp4]   begin noVNC/X capture (writes a PID file)
  scripts/slice3_record.sh stop                 stop capture cleanly (SIGINT -> finalize mp4)
  scripts/slice3_record.sh status               report whether a capture is running

Env: RECORD_DISPLAY (default $DISPLAY or :1.0), RECORD_SIZE (WxH), RECORD_FPS (25),
     RECORD_DIR (default $PWD). Actual recording is a human-gated step.
EOF
}

detect_size() {
  if [[ -n "${RECORD_SIZE:-}" ]]; then
    printf '%s\n' "${RECORD_SIZE}"
    return
  fi
  if have xdpyinfo; then
    local dims
    dims="$(DISPLAY="${RECORD_DISPLAY}" xdpyinfo 2>/dev/null | awk '/dimensions:/ {print $2; exit}')"
    if [[ -n "${dims}" ]]; then
      printf '%s\n' "${dims}"
      return
    fi
  fi
  printf '%s\n' "1280x720"
}

manual_instructions() {
  cat <<EOF
ffmpeg not found — record the noVNC window manually:
  * Use the browser/OS screen recorder on the noVNC tab, or install ffmpeg and rerun.
  * Equivalent command this wrapper would run:
      ffmpeg -y -f x11grab -framerate ${RECORD_FPS} -video_size $(detect_size) \\
        -i ${RECORD_DISPLAY} -c:v libx264 -preset veryfast -pix_fmt yuv420p OUTPUT.mp4
EOF
}

recorder_running() {
  [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}" 2>/dev/null)" 2>/dev/null
}

cmd_start() {
  local output="${1:-${RECORD_DIR}/warehouse_slice3_$(date +%Y%m%d_%H%M%S).mp4}"
  if recorder_running; then
    echo "already recording (pid $(cat "${PID_FILE}")); run 'stop' first" >&2
    return 1
  fi
  if ! have ffmpeg; then
    manual_instructions
    return 0
  fi
  local size
  size="$(detect_size)"
  echo "recording ${RECORD_DISPLAY} (${size} @ ${RECORD_FPS}fps) -> ${output}"
  ffmpeg -y -f x11grab -framerate "${RECORD_FPS}" -video_size "${size}" \
    -i "${RECORD_DISPLAY}" -c:v libx264 -preset veryfast -pix_fmt yuv420p "${output}" \
    >"${LOG_FILE}" 2>&1 &
  echo "$!" >"${PID_FILE}"
  echo "started (pid $!). Stop with: $0 stop"
}

cmd_stop() {
  if [[ ! -f "${PID_FILE}" ]]; then
    echo "no recording in progress (no ${PID_FILE})" >&2
    return 1
  fi
  local pid
  pid="$(cat "${PID_FILE}" 2>/dev/null)"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
    # SIGINT lets ffmpeg finalize the mp4 moov atom cleanly (SIGKILL truncates the file).
    kill -INT "${pid}" 2>/dev/null || true
    echo "sent stop to pid ${pid}; mp4 is finalizing"
  else
    echo "recorder pid '${pid}' not running; clearing stale pidfile" >&2
  fi
  rm -f "${PID_FILE}"
}

cmd_status() {
  if recorder_running; then
    echo "recording (pid $(cat "${PID_FILE}"))"
  else
    echo "idle"
  fi
}

main() {
  local sub="${1:-}"
  case "${sub}" in
    start)
      shift
      cmd_start "$@"
      ;;
    stop)
      cmd_stop
      ;;
    status)
      cmd_status
      ;;
    -h | --help | "")
      usage
      ;;
    *)
      echo "unknown subcommand: ${sub}" >&2
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
