#!/usr/bin/env bash
# Memory-gate spike driver — Phase 0.5 段階1 (Mac Docker approximation), NO hardware.
#
# Boots the FULL Phase 0.5 LLM-in-Gazebo stack (bringup.launch.py: sim + Nav2x2 + AMCL +
# State Cache + Emergency Guardian + nav2_bridge + LLM Bridge, plus an external Hermes Gateway
# daemon and the in-process Warehouse MCP tools) inside ONE tiryoh/ros2-desktop-vnc:jazzy
# container capped at `--memory=6g --memory-swap=6g`, then samples memory + checks for OOM.
#
# WHY 6GiB: Jetson Orin Nano 8GB minus JetPack(OS+CUDA+desktop) ~2-2.5GB leaves ~5.5-6GB for
# the app (--memory form doc06:92; 6GiB-usable rationale doc06:93). This is the 段階1 EARLY
# SMOKE — "a design that dies here will certainly die on the real robot" (doc06:94). It de-risks
# R-02 (07:153) and feeds the R-38 Open-RMF Go/No-Go gate (07:243): if free headroom < 500MB,
# Open-RMF (Mode C) is No-Go-leaning and must be reconsidered (doc06:98 / 07:212).
#
# IMPORTANT — what 段階1 CANNOT show (do NOT read closure into a GO here):
#   * Jetson unified CPU/GPU memory contention + true JetPack overhead are NOT reproducible on
#     Mac (doc06:99-101). Final numbers require 段階2 = real Jetson `free -h` 30s x 10min
#     (doc06:96). 段階1 is an early-warning smoke, not the verdict.
#   * `free -h` INSIDE a --memory-capped container reports the HOST's RAM, not the cgroup limit
#     (well-known Docker gotcha). So for 段階1 the AUTHORITATIVE 残RAM signal is the cgroup
#     accounting (memory.current / memory.max / memory.peak) and `docker stats` against the 6g
#     limit. `free -h` is logged only for reference (it is the doc06:96 form used at 段階2).
#   * OOM: a cgroup OOM-kill of a *child* process (nav2, gz, ...) does NOT flip
#     `docker inspect .State.OOMKilled` (that only tracks PID 1 = `sleep infinity`). The robust
#     段階1 signal is the cgroup `memory.events` `oom_kill` counter (cgroup v2; incremented for
#     ANY process in the cgroup). On cgroup v1 that counter is unreliable -> reported as UNKNOWN;
#     we also record .State.OOMKilled and dmesg as secondaries. Docker Desktop on Mac uses v2.
#
# FIDELITY (so the measured stack is the configured one, not silent defaults):
#   * Nodes resolve config from $WAREHOUSE_CONFIG_DIR (relative "config" by default,
#     paths.py:56). The repo is mounted ro at /repo, so setup sets -e WAREHOUSE_CONFIG_DIR=
#     /repo/config -e WAREHOUSE_ENV=dev on `docker run` (every exec inherits it). Without this,
#     load_config() returns {} and emergency_guardian.py:53 KeyErrors at startup (safety node
#     silently absent + under-measured).
#   * fastapi/uvicorn (nav2_bridge eager import) and langfuse/openai (llm_bridge) are NOT apt/
#     rosdep deps and colcon does not pip-install setup.py install_requires -> setup pip-installs
#     them, else those nodes crash and are silently missing from the footprint.
#   * Hermes is installed the documented way (git install.sh -> ~/.local/bin/hermes, NOT pip;
#     deploy/gcp/README.md:73,86) and its liveness on :8642 is asserted; if absent the run
#     degrades to ROS-only and report annotates "Hermes NOT counted".
#
# Usage:
#   ./run.sh setup     # pull image, create 6g container, install ROS+py deps, build ws, install Hermes
#   ./run.sh run       # launch bringup.launch.py sim:=true llm:=true (+ Hermes daemon if available)
#   ./run.sh measure   # poll liveness, then sample cgroup + docker stats + free -h every 30s; OOM check
#   ./run.sh report    # summarise logs/ into a table (peak, headroom vs 500MB, OOM, node presence)
#   ./run.sh all       # setup -> run -> measure -> report
#   ./run.sh selftest  # OFFLINE branch self-test (no docker/network): verdict awk + FLOOR notes
#   ./run.sh clean     # remove the container
#
# Tunables (env): MEMGATE_SAMPLES (default 21 = ~10min @30s), MEMGATE_INTERVAL (default 30s),
#                 MEMGATE_SETTLE (default 120s liveness-poll timeout), MEMGATE_MEM (default 6g),
#                 MEMGATE_REQUIRE_HERMES=1 (hard-fail `run` if Hermes absent, vs a FLOOR run).
set -uo pipefail

IMAGE="tiryoh/ros2-desktop-vnc:jazzy"
CONTAINER="mwr-memgate-spike"
SPIKE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SPIKE_DIR/../.." && pwd)"   # repo root (this spike lives at spike/memory-gate/)
HERMES_DIR_HOST="${HOME}/.hermes"            # provider keys (project_api_keys_dev_setup)

MEM="${MEMGATE_MEM:-6g}"                      # --memory form doc06:92 (6GiB ~= Jetson 8GB usable, doc06:93)
SAMPLES="${MEMGATE_SAMPLES:-21}"             # 21 samples @30s ~= 10 min (doc06:96 window)
INTERVAL="${MEMGATE_INTERVAL:-30}"          # doc06:96 30s cadence
SETTLE="${MEMGATE_SETTLE:-120}"             # max wait for Nav2 lifecycle + gz + Hermes to register
HEADROOM_FLOOR_MB=500                        # doc06:98 / 07:212 — 残RAM<500MB => Open-RMF 再検討 (decimal MB)

# In-container ws copy (built here, NOT in the mounted repo, to keep the host worktree clean —
# mirrors firmware/spike which builds in container-internal /root/*_ws).
WS_C="/root/mwr_ws"
SRC_ROS='source /opt/ros/jazzy/setup.bash'
SRC_WS="$SRC_ROS; source $WS_C/install/setup.bash"
# Software GL so headless gz (gpu_lidar/ogre2) renders under llvmpipe (warehouse_sim/spike).
GLENV='export LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe'
PATH_LOCAL='export PATH="$HOME/.local/bin:$PATH"'   # Hermes git install lands ~/.local/bin/hermes
# Core nodes whose presence means the stack is actually up (liveness gate).
CORE_NODES=(state_cache controller_server llm_bridge)

dex()  { docker exec    "$CONTAINER" bash -lc "$*"; }   # login shell (ROS sourcing convenience)
dexd() { docker exec -d "$CONTAINER" bash -lc "$*"; }   # detached, persists in container
dexq() { docker exec    "$CONTAINER" bash -c  "$*"; }   # NON-login: clean stdout for machine parse

# Validate numeric tunables (an empty/garbage override would silently corrupt the loop/verdict).
_posint() { [[ "$1" =~ ^[1-9][0-9]*$ ]]; }
_posint   "$SAMPLES"  || { echo "MEMGATE_SAMPLES must be a positive int (got '$SAMPLES')"  >&2; exit 2; }
_posint   "$INTERVAL" || { echo "MEMGATE_INTERVAL must be a positive int (got '$INTERVAL')" >&2; exit 2; }
[[ "$SETTLE" =~ ^[0-9]+$ ]]      || { echo "MEMGATE_SETTLE must be an int (got '$SETTLE')" >&2; exit 2; }
[[ "$MEM" =~ ^[0-9]+[mMgG]?$ ]]  || { echo "MEMGATE_MEM must look like 6g/6144m (got '$MEM')" >&2; exit 2; }

ensure_up() {
  docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}\$" || {
    echo "container '${CONTAINER}' not running — run: $0 setup" >&2; exit 1; }
  dexq "test -d $WS_C/install" || {
    echo "ws not built in container — run: $0 setup" >&2; exit 1; }
}

# Clean, sentinel-tagged single line: "CGSNAP <current> <limit> <peak> <oom_kill>" (-1 if N/A).
# Non-login exec + sentinel + tail -1 defends against login-shell/profile stdout contamination.
cgroup_snapshot() {
  dexq '
    if [ -f /sys/fs/cgroup/memory.current ]; then           # cgroup v2
      cur=$(cat /sys/fs/cgroup/memory.current 2>/dev/null || echo -1)
      lim=$(cat /sys/fs/cgroup/memory.max 2>/dev/null || echo -1)
      pk=$(cat /sys/fs/cgroup/memory.peak 2>/dev/null || echo -1)
      ok=$(awk "/^oom_kill /{print \$2}" /sys/fs/cgroup/memory.events 2>/dev/null); ok=${ok:--1}
    else                                                     # cgroup v1 (oom_kill counter unreliable)
      cur=$(cat /sys/fs/cgroup/memory/memory.usage_in_bytes 2>/dev/null || echo -1)
      lim=$(cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null || echo -1)
      pk=$(cat /sys/fs/cgroup/memory/memory.max_usage_in_bytes 2>/dev/null || echo -1)
      ok=-1
    fi
    echo "CGSNAP ${cur:--1} ${lim:--1} ${pk:--1} ${ok:--1}"
  ' | awk '/^CGSNAP/{print $2, $3, $4, $5}' | tail -n1
}

# --- pure, offline-testable helpers (no docker / no network) — exercised by `selftest` ---

# R-38 verdict from a measure timeseries TSV. $1=TSV path, $2=headroom floor (decimal MB).
# Kept awk-only (no container) so the Go/No-Go branch logic is unit-testable with synthetic
# input (#187 DoD "verdict awk 分岐 unit-test"). Emits the summary + the "VERDICT (R-38)" line.
compute_verdict_awk() {
  awk -F'\t' -v floor="$2" '
    NR>1 {
      c=$3; l=$4; p=$5; o=$6; n++;
      if (c ~ /^[0-9]+$/) { valid++; if (c+0>maxc) maxc=c+0 }
      if (p ~ /^[0-9]+$/ && p+0>maxp) maxp=p+0;
      if (l ~ /^[0-9]+$/ && l+0>maxl) maxl=l+0;   # max non-negative limit (static; -1 on a flaky read)
      if (o ~ /^[0-9]+$/) { if (o+0>maxo) maxo=o+0 } else oomunknown=1;
    }
    END {
      if (n==0) { print "no samples — run measure"; exit }
      peak=(maxp>maxc)?maxp:maxc; lim=maxl; MB=1000000;   # decimal MB to match doc06:98 "500MB"
      printf "samples           : %d (valid cgroup rows: %d)\n", n, valid+0;
      if (lim>0 && peak>0) {
        headroom = lim - peak;
        printf "cgroup limit      : %.0f MB\n", lim/MB;
        printf "peak usage        : %.0f MB  (cgroup memory.peak/max_usage if present, else sampled current max)\n", peak/MB;
        printf "headroom @peak    : %.0f MB  (limit - peak; floor = %d MB, doc06:98/07:212)\n", headroom/MB, floor;
      }
      if (oomunknown && maxo==0) print "cgroup oom_kill   : UNKNOWN (counter unreadable on some samples; confirm via measure_oom.txt)";
      else printf "cgroup oom_kill   : %d\n", maxo;
      # OOM is the definitive FAIL — report it even if headroom accounting is partial.
      if (maxo>0) { print "VERDICT (R-38)    : OOM OBSERVED => 段階1 FAIL (design dies on Jetson too, doc06:94)"; exit }
      if (valid+0==0 || lim<=0 || peak<=0) {
        print "VERDICT (R-38)    : INVALID — cgroup accounting unavailable/garbage; re-run measure (check cgroup path/exec noise)."; exit }
      verdict = (oomunknown) ? "OOM UNKNOWN (cgroup counter unavailable) — confirm measure_oom.txt before trusting a GO" \
              : (headroom/MB < floor) ? "headroom < 500MB => Open-RMF (Mode C) No-Go-leaning (doc06:98 / 07:212); R-38 gate trips" \
              : "no OOM and headroom >= 500MB => 段階1 GO-leaning; Open-RMF still UNMEASURED -> 段階2 required";
      printf "VERDICT (R-38)    : %s\n", verdict;
    }' "$1"
}

# FLOOR caveats — loud, offline-testable. $1=hermes_present (yes/no), $2=stack_live (yes/no).
# A Hermes-less or not-fully-live run UNDER-measures Mode A/B, so the result is a FLOOR and must
# not be read as a GO. Returns 0 (silent) only when both signals are "yes".
floor_notes() {
  [ "$1" != yes ] && echo "⚠️  FLOOR: Hermes daemon NOT counted in peak — Mode A/B under-measured by its resident footprint; do NOT read a GO."
  [ "$2" != yes ] && echo "⚠️  FLOOR: core stack was not fully live — treat the headroom/verdict as a FLOOR, re-run after fixing startup."
  return 0
}

# Top-line verdict tag — pure/offline (selftest). $1=hermes_present (yes/no/unknown). When Hermes
# is NOT counted the run is a FLOOR (Mode A/B resident footprint missing), so a reader scanning the
# VERDICT line ALONE must see FLOOR/NOT-a-GO, not the underlying compute_verdict_awk "GO-leaning"
# (which is printed below it for the headroom detail). Hermes counted => no tag (empty), and the
# compute_verdict_awk "VERDICT (R-38)" line stands as the verdict.
verdict_line() {
  [ "${1:-}" != yes ] && echo "VERDICT: FLOOR — NOT a GO (Hermes 未計上 — 常駐分が欠落)"
  return 0
}

# Refuse to proceed on a Hermes-less FLOOR when the operator demanded Hermes be counted.
# $1 = hermes_present (yes|no|unknown). Pure/offline — exercised by selftest. Applies to every
# entry point (run/measure/report), not just run, so a direct `./run.sh measure|report` (or a run
# against a stale logs/hermes_present.txt) cannot emit a GO-leaning verdict for an uncounted FLOOR.
require_hermes_or_refuse() {
  if [ "${MEMGATE_REQUIRE_HERMES:-0}" = 1 ] && [ "${1:-}" != yes ]; then
    echo "  REFUSED: MEMGATE_REQUIRE_HERMES=1 but Hermes is ${1:-unknown} (not counted) — refusing to measure/report a FLOOR. Fix setup first." >&2
    exit 3
  fi
}

case "${1:-}" in
  clean)
    docker rm -f "$CONTAINER" 2>/dev/null || true ;;

  setup)
    mkdir -p "$SPIKE_DIR/logs"
    docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull "$IMAGE"
    docker rm -f "$CONTAINER" 2>/dev/null || true
    # Mount: spike rw (logs), repo ro (source + config), ~/.hermes ro (copied to a writable
    # container path in setup). WAREHOUSE_CONFIG_DIR makes nodes read the REAL config (not {}).
    HERMES_MOUNT=()
    if [ -d "$HERMES_DIR_HOST" ]; then HERMES_MOUNT=(-v "$HERMES_DIR_HOST:/host-hermes:ro"); fi
    docker run -d --name "$CONTAINER" \
      --memory="$MEM" --memory-swap="$MEM" \
      -e LIBGL_ALWAYS_SOFTWARE=1 -e GALLIUM_DRIVER=llvmpipe \
      -e WAREHOUSE_CONFIG_DIR=/repo/config -e WAREHOUSE_ENV=dev \
      -v "$SPIKE_DIR:/spike:rw" \
      -v "$REPO_DIR:/repo:ro" \
      "${HERMES_MOUNT[@]}" \
      "$IMAGE" sleep infinity
    echo "=== apt deps (Nav2 + ros_gz + twist_mux + colcon/rosdep + curl/git/venv + mesa) ==="
    dex "set -e; DEBIAN_FRONTEND=noninteractive apt-get update -qq && apt-get install -y -qq \
           ros-jazzy-navigation2 ros-jazzy-nav2-bringup ros-jazzy-twist-mux \
           ros-jazzy-ros-gz ros-jazzy-ros-gz-bridge ros-jazzy-ros-gz-sim \
           python3-colcon-common-extensions python3-rosdep python3-pip python3-venv \
           curl git mesa-utils libgl1-mesa-dri \
         > /spike/logs/setup_apt.log 2>&1 || { tail -40 /spike/logs/setup_apt.log; exit 1; }"
    echo "=== python deps NOT covered by apt/rosdep/colcon (nav2_bridge + llm_bridge runtime) ==="
    # nav2_bridge.py:26 eager 'import uvicorn'; setup.py install_requires fastapi/uvicorn (colcon
    # does not pip-install these). llm_bridge needs langfuse/openai (openai brings httpx).
    dex "pip install --quiet --break-system-packages 'fastapi>=0.110' 'uvicorn>=0.27' 'langfuse>=4.7,<5' 'openai>=1.0' \
         > /spike/logs/setup_pydeps.log 2>&1 || { tail -40 /spike/logs/setup_pydeps.log; exit 1; }"
    echo "=== copy repo ws -> $WS_C (container-internal build; host worktree stays clean) ==="
    dex "set -e; rm -rf $WS_C; mkdir -p $WS_C/src; cp -r /repo/ws/src/. $WS_C/src/"
    echo "=== rosdep + colcon build (the warehouse_* packages) ==="
    dex "$SRC_ROS; rosdep init >/dev/null 2>&1 || true; rosdep update >/dev/null 2>&1 || true; \
         cd $WS_C; rosdep install --from-paths src --ignore-src -y > /spike/logs/setup_rosdep.log 2>&1 \
           || echo 'WARN: rosdep install non-zero (see logs/setup_rosdep.log) — colcon may still build'; \
         colcon build --symlink-install > /spike/logs/setup_build.log 2>&1 \
           || { echo 'colcon build FAILED:'; tail -60 /spike/logs/setup_build.log; exit 1; }"
    echo "=== Hermes Gateway via official git installer (deploy/gcp/README.md:73 — NOT pip) ==="
    # The mounted host ~/.hermes/hermes-agent is a *macOS* install (Darwin venv); reusing it makes
    # the Linux installer report "Existing install detected — keeping legacy layout" and SKIP, so no
    # Linux launcher is ever created (root cause of the prior `hermes: command not found` FLOOR run —
    # logs/setup_versions.txt). Fix: do a CLEAN Linux install FIRST, THEN inject only the host
    # provider keys/config (.env/config.yaml) so they are not clobbered by installer defaults. The
    # memory gate needs Hermes only RESIDENT (it makes no LLM call), so a minimal config suffices.
    dex "rm -rf /root/.hermes /root/.local/bin/hermes"
    dex "$PATH_LOCAL; curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh \
           | bash > /spike/logs/setup_hermes.log 2>&1; echo \"installer exit: \$?\" >> /spike/logs/setup_hermes.log"
    dex 'if [ -d /host-hermes ]; then mkdir -p /root/.hermes; \
           for f in .env config.yaml; do [ -e "/host-hermes/$f" ] && cp "/host-hermes/$f" "/root/.hermes/$f"; done; \
           chmod -R u+w /root/.hermes 2>/dev/null || true; fi'
    # Robust launcher resolution (fresh install -> ~/.local/bin/hermes; else scan known locations &
    # symlink) + a definitive marker (logs/hermes_install.txt) + a LOUD FLOOR banner if Hermes is
    # still unresolved, so a Hermes-less footprint is never silently misread as a real Mode A/B figure.
    dex "$PATH_LOCAL
      if ! command -v hermes >/dev/null 2>&1; then
        for cand in /root/.local/bin/hermes /usr/local/bin/hermes \
                    /usr/local/lib/hermes-agent/hermes /root/.hermes/hermes-agent/hermes; do
          [ -x \"\$cand\" ] && { mkdir -p /root/.local/bin; ln -sf \"\$cand\" /root/.local/bin/hermes; break; }
        done
      fi
      if command -v hermes >/dev/null 2>&1 && hermes --version >> /spike/logs/setup_hermes.log 2>&1; then
        echo installed > /spike/logs/hermes_install.txt
        echo \"  hermes installed OK (\$(command -v hermes))\"
      else
        echo FAILED > /spike/logs/hermes_install.txt
        printf '  %s\n' \
          '############################################################' \
          '## (!) HERMES NOT INSTALLED -> measure will be a FLOOR run ##' \
          '## Hermes Gateway resident footprint is NOT counted, so    ##' \
          '## the Mode A/B peak is UNDER-measured. Do NOT read a GO.   ##' \
          '## See logs/setup_hermes.log. To refuse measuring a FLOOR, ##' \
          '## re-run: MEMGATE_REQUIRE_HERMES=1 ./run.sh run            ##' \
          '############################################################'
      fi"
    echo "=== versions ==="
    dex "$PATH_LOCAL; $SRC_WS; { cat /etc/os-release | grep PRETTY_NAME; \
         echo \"gz: \$(gz sim --version 2>&1 | head -1)\"; \
         echo \"nav2: \$(ros2 pkg xml nav2_bringup 2>/dev/null | grep -m1 -oE '<version>[^<]+' | cut -d'>' -f2)\"; \
         echo \"hermes: \$(hermes --version 2>&1 | head -1 || echo NONE)\"; \
         echo \"config_dir: \$WAREHOUSE_CONFIG_DIR (env \$WAREHOUSE_ENV)\"; \
         python3 --version; } | tee /spike/logs/setup_versions.txt"
    echo "SETUP done. Next: $0 run" ;;

  run)
    ensure_up
    dex 'pkill -f "ros2 launch" 2>/dev/null; pkill -f "gz sim" 2>/dev/null; pkill -f hermes 2>/dev/null; sleep 1' || true
    echo "=== start Hermes Gateway daemon (:8642) if available ==="
    HERMES_PRESENT=no
    if dex "$PATH_LOCAL; command -v hermes >/dev/null 2>&1"; then
      # Independent service (doc12a:409 "独立"); HTTP API on :8642 (doc15:20-46). Reads ~/.hermes/.env.
      dexd "$PATH_LOCAL; exec hermes gateway > /spike/logs/run_hermes.log 2>&1"
      sleep 6
      # Liveness MUST be a POSITIVE probe: an HTTP response on :8642 or a listening socket. A log
      # grep ("listen"/"started") false-positives on bind-FAILURE lines (e.g. "could not start
      # listener"), which would bypass the FLOOR refusal. If BOTH curl and ss are unavailable the
      # check conservatively reports NOT-counted -> loud FLOOR (the SAFE direction).
      if dex 'curl -sf http://localhost:8642/ >/dev/null 2>&1 || (command -v ss >/dev/null && ss -ltn 2>/dev/null | grep -q :8642)'; then
        HERMES_PRESENT=yes; echo "  hermes daemon LIVE on :8642"
      else
        echo "  WARN: hermes launched but :8642 not confirmed (see logs/run_hermes.log) — treating as NOT counted"
      fi
    else
      echo "  HERMES ABSENT — launching ROS stack only; llm_bridge degrades to Nav2-only (doc08:291)."
    fi
    echo "$HERMES_PRESENT" > "$SPIKE_DIR/logs/hermes_present.txt"
    if [ "$HERMES_PRESENT" != yes ]; then
      printf '  %s\n' \
        '⚠️  FLOOR RUN — Hermes Gateway is NOT counted in this measurement.' \
        '    The Mode A/B peak will be UNDER-measured by the Hermes resident footprint;' \
        '    do NOT read a GO from a Hermes-less run. Re-run setup to fix the install.'
      require_hermes_or_refuse "$HERMES_PRESENT"
    fi
    echo "=== launch full stack: bringup.launch.py sim:=true llm:=true ==="
    # config resolves via WAREHOUSE_CONFIG_DIR (set on docker run). MCP = in-process inside
    # llm_bridge (doc15:50); Hermes = external daemon above; no micro-ROS — the sim layer stands
    # in for the real-robot base bridge (composition note, bringup.launch.py:48-49 / doc06 Phase0.5;
    # systemd order doc12a:403). Defaults: map=warehouse_sim bundled, traffic_mode=config.
    dexd "$GLENV; $SRC_WS; exec ros2 launch warehouse_bringup bringup.launch.py sim:=true llm:=true \
            > /spike/logs/run_bringup.log 2>&1"
    echo "  bringup launched. Next: $0 measure  (logs/run_bringup.log)" ;;

  measure)
    ensure_up
    # Honour MEMGATE_REQUIRE_HERMES at THIS entry point too (not only in run): a direct
    # `./run.sh measure`, or a run against a stale logs/hermes_present.txt, must not measure a FLOOR.
    require_hermes_or_refuse "$(cat "$SPIKE_DIR/logs/hermes_present.txt" 2>/dev/null || echo unknown)"
    TS="$SPIKE_DIR/logs/measure_timeseries.tsv"
    : > "$SPIKE_DIR/logs/measure_free.log"
    echo -e "sample\tt_s\tcgroup_current_b\tcgroup_limit_b\tcgroup_peak_b\toom_kill\tdocker_stats_memusage" > "$TS"
    echo "=== liveness poll: wait up to ${SETTLE}s for core nodes (${CORE_NODES[*]}) ==="
    STACK_LIVE=no; waited=0
    while [ "$waited" -lt "$SETTLE" ]; do
      nl="$(dex "$SRC_WS; timeout 15 ros2 node list" 2>/dev/null)"
      missing=0; for n in "${CORE_NODES[@]}"; do echo "$nl" | grep -qE "/${n}\$" || missing=1; done
      if [ "$missing" -eq 0 ]; then STACK_LIVE=yes; echo "  core nodes up after ${waited}s"; break; fi
      sleep 10; waited=$((waited + 10))
    done
    [ "$STACK_LIVE" = no ] && echo "  WARN: core nodes NOT all up after ${SETTLE}s — measuring anyway; report will flag."
    echo "$STACK_LIVE" > "$SPIKE_DIR/logs/stack_live.txt"
    echo "=== snapshot node/topic list (timeout-wrapped) ==="
    dex "$SRC_WS; timeout 20 ros2 node list"  > "$SPIKE_DIR/logs/measure_nodes.txt"  2>&1 || true
    dex "$SRC_WS; timeout 20 ros2 topic list" > "$SPIKE_DIR/logs/measure_topics.txt" 2>&1 || true
    echo "=== sample loop: ${SAMPLES} x ${INTERVAL}s ==="
    for i in $(seq 1 "$SAMPLES"); do
      t=$(( (i-1) * INTERVAL ))
      read -r cur lim pk ok <<<"$(cgroup_snapshot)"
      [[ "$cur" =~ ^-?[0-9]+$ ]] || { cur=-1; lim=-1; pk=-1; ok=-1; echo "  WARN sample $i: cgroup read garbage"; }
      ds=$(docker stats --no-stream --format '{{.MemUsage}}' "$CONTAINER" 2>/dev/null | tr -d ' ')
      echo -e "${i}\t${t}\t${cur}\t${lim}\t${pk}\t${ok}\t${ds:-NA}" | tee -a "$TS"
      { echo "---- sample $i (t=${t}s) ----"; dex 'free -h'; } >> "$SPIKE_DIR/logs/measure_free.log" 2>&1
      [ "$i" -lt "$SAMPLES" ] && sleep "$INTERVAL"
    done
    echo "=== re-snapshot nodes at end (late-registering lifecycle nodes) ==="
    dex "$SRC_WS; timeout 20 ros2 node list" > "$SPIKE_DIR/logs/measure_nodes_end.txt" 2>&1 || true
    echo "=== OOM signals (cgroup oom_kill above is primary; record secondaries) ==="
    {
      echo "docker_inspect_OOMKilled: $(docker inspect --format '{{.State.OOMKilled}}' "$CONTAINER" 2>/dev/null)"
      echo "--- dmesg tail (host VM; may be empty inside Docker Desktop) ---"
      dex 'dmesg 2>/dev/null | grep -iE "killed process|oom" | tail -20 || echo "(dmesg unavailable)"'
    } | tee "$SPIKE_DIR/logs/measure_oom.txt"
    echo "measure done. Next: $0 report" ;;

  report)
    TS="$SPIKE_DIR/logs/measure_timeseries.tsv"
    [ -f "$TS" ] || { echo "no timeseries — run: $0 measure" >&2; exit 1; }
    NODES="$SPIKE_DIR/logs/measure_nodes_end.txt"; [ -f "$NODES" ] || NODES="$SPIKE_DIR/logs/measure_nodes.txt"
    HERMES_PRESENT="$(cat "$SPIKE_DIR/logs/hermes_present.txt" 2>/dev/null || echo unknown)"
    STACK_LIVE="$(cat "$SPIKE_DIR/logs/stack_live.txt" 2>/dev/null || echo unknown)"
    # Refuse BEFORE printing any verdict: a stale/absent hermes_present.txt must not yield a
    # GO-leaning report when the operator demanded Hermes be counted (reuses HERMES_PRESENT above).
    require_hermes_or_refuse "$HERMES_PRESENT"
    echo "=== memory-gate report (段階1, --memory=$MEM) ==="
    echo "hermes daemon counted: $HERMES_PRESENT   |   core stack live: $STACK_LIVE"
    # Surface a crashed/incomplete launch so a low-memory reading is not misread as a benign GO.
    if grep -qiE "Traceback|ModuleNotFoundError|process has died|has died|No module named" "$SPIKE_DIR/logs/run_bringup.log" 2>/dev/null; then
      echo "⚠️  run_bringup.log shows a NODE FAILURE — the measured stack is INCOMPLETE:"
      grep -niE "Traceback|ModuleNotFoundError|process has died|has died|No module named" "$SPIKE_DIR/logs/run_bringup.log" | head -10
    fi
    # On a Hermes-less FLOOR, tag the TOP VERDICT line unambiguously so a reader scanning it alone
    # cannot misread the compute_verdict_awk "段階1 GO-leaning" (printed below) as a real GO.
    verdict_line "$HERMES_PRESENT"
    compute_verdict_awk "$TS" "$HEADROOM_FLOOR_MB"
    floor_notes "$HERMES_PRESENT" "$STACK_LIVE"
    echo "--- full-stack node presence (per-bot nav2 expects 2; core expects 1; src: $(basename "$NODES")) ---"
    if [ -f "$NODES" ]; then
      for pair in controller_server:2 planner_server:2 bt_navigator:2 amcl:2 \
                  state_cache:1 emergency_guardian:1 nav2_bridge:1 llm_bridge:1; do
        name="${pair%%:*}"; exp="${pair##*:}"
        c=$(grep -cE "/${name}\$" "$NODES" 2>/dev/null)
        flag=""; [ "$c" -lt "$exp" ] && flag="  <-- SHORT (expected ${exp})"
        echo "  ${name} : ${c}/${exp}${flag}"
      done
    fi
    echo "NOTE: 段階1 != closure. Final numbers = 段階2 real Jetson (doc06:96-101). Transcribe into RESULT.md." ;;

  all)
    SELF="$SPIKE_DIR/$(basename "${BASH_SOURCE[0]}")"
    bash "$SELF" setup && bash "$SELF" run && bash "$SELF" measure && bash "$SELF" report ;;

  selftest)
    # Offline self-test (NO docker, NO network): drives compute_verdict_awk through every R-38
    # branch with synthetic cgroup timeseries, floor_notes through its FLOOR-annotation branches,
    # and require_hermes_or_refuse through its refuse/pass branches (in subshells so its exit 3
    # does not kill selftest). Pair with `bash -n run.sh`. Exits non-zero on any failed assertion.
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    pass=0; fail=0
    HDR='sample\tt_s\tcgroup_current_b\tcgroup_limit_b\tcgroup_peak_b\toom_kill\tdocker_stats_memusage'
    LIM=6442450944   # 6 GiB cgroup limit (decimal bytes); awk MB divisor = 1e6, floor = 500 MB
    _v() {  # $1=name $2=expected-substr ; reads the TSV at $tmp/ts.tsv
      local name="$1" want="$2" out
      out="$(compute_verdict_awk "$tmp/ts.tsv" "$HEADROOM_FLOOR_MB")"
      if printf '%s' "$out" | grep -qF "$want"; then pass=$((pass+1)); echo "  PASS  $name"
      else fail=$((fail+1)); echo "  FAIL  $name (want substr: $want)"; printf '%s\n' "$out" | sed 's/^/        got: /'; fi
    }
    _chk() {  # $1=name $2=expected-substr ($3 empty => assert silent) $4=actual
      local name="$1" want="$2" actual="$3"
      if [ -z "$want" ]; then
        [ -z "$actual" ] && { pass=$((pass+1)); echo "  PASS  $name"; } || { fail=$((fail+1)); echo "  FAIL  $name (expected silence, got: $actual)"; }
      elif printf '%s' "$actual" | grep -qF "$want"; then pass=$((pass+1)); echo "  PASS  $name"
      else fail=$((fail+1)); echo "  FAIL  $name (want substr: $want)"; fi
    }
    # OOM observed (oom_kill>0) => 段階1 FAIL (takes precedence over headroom)
    { printf "$HDR\n"; printf "1\t0\t6000000000\t$LIM\t6000000000\t2\tNA\n"; } > "$tmp/ts.tsv"
    _v "OOM => FAIL" "OOM OBSERVED"
    # no OOM, headroom 442MB < 500MB floor => No-Go-leaning
    { printf "$HDR\n"; printf "1\t0\t6000000000\t$LIM\t6000000000\t0\tNA\n"; } > "$tmp/ts.tsv"
    _v "headroom<floor => No-Go" "No-Go-leaning"
    # no OOM, headroom 1442MB >= 500MB floor => GO-leaning
    { printf "$HDR\n"; printf "1\t0\t5000000000\t$LIM\t5000000000\t0\tNA\n"; } > "$tmp/ts.tsv"
    _v "headroom>=floor => GO" "GO-leaning"
    # garbage cgroup (all -1) => INVALID
    { printf "$HDR\n"; printf "1\t0\t-1\t-1\t-1\t-1\tNA\n"; } > "$tmp/ts.tsv"
    _v "garbage cgroup => INVALID" "INVALID"
    # cgroup v1 (oom_kill counter -1, numeric mem) => OOM UNKNOWN
    { printf "$HDR\n"; printf "1\t0\t5000000000\t$LIM\t5000000000\t-1\tNA\n"; } > "$tmp/ts.tsv"
    _v "v1 oom counter => UNKNOWN" "OOM UNKNOWN"
    # FLOOR-annotation branches
    _chk "floor: hermes-absent note" "Hermes daemon NOT counted" "$(floor_notes no yes)"
    _chk "floor: stack-not-live note" "core stack was not fully live" "$(floor_notes yes no)"
    _chk "floor: all-live => silent"  "" "$(floor_notes yes yes)"
    # verdict_line: Hermes-less => top VERDICT line tagged FLOOR/NOT-a-GO; Hermes counted => silent.
    _chk "verdict: hermes-absent => FLOOR tag" "FLOOR — NOT a GO" "$(verdict_line no)"
    _chk "verdict: hermes-unknown => FLOOR tag" "FLOOR — NOT a GO" "$(verdict_line unknown)"
    _chk "verdict: hermes-counted => silent"  "" "$(verdict_line yes)"
    # require_hermes_or_refuse: exercise refuse/pass in a SUBSHELL so its `exit 3` cannot kill us.
    _rc() {  # $1=name $2=expected-exit-code $3=actual-exit-code
      local name="$1" want="$2" got="$3"
      if [ "$got" -eq "$want" ]; then pass=$((pass+1)); echo "  PASS  $name"
      else fail=$((fail+1)); echo "  FAIL  $name (want exit $want, got $got)"; fi
    }
    ( MEMGATE_REQUIRE_HERMES=1; require_hermes_or_refuse no ) >/dev/null 2>&1; _rc "require: on + absent => refuse(3)" 3 "$?"
    ( MEMGATE_REQUIRE_HERMES=1; require_hermes_or_refuse yes ) >/dev/null 2>&1; _rc "require: on + present => pass(0)" 0 "$?"
    ( MEMGATE_REQUIRE_HERMES=0; require_hermes_or_refuse no ) >/dev/null 2>&1; _rc "require: off + absent => pass(0)" 0 "$?"
    echo "selftest: $pass passed, $fail failed"
    [ "$fail" -eq 0 ] || exit 1 ;;

  *)
    echo "usage: $0 {setup|run|measure|report|all|selftest|clean}"; exit 2 ;;
esac
