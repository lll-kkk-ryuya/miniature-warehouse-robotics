# deploy/dev — dev/sim container provisioning

Helpers to provision the local Docker dev/sim environment (`tiryoh/ros2-desktop-vnc:jazzy`,
ARM64 on the M4 Mac) beyond the base image. The base ships `ros_gz` only; the #7 sim spike
(`ws/src/warehouse_sim/spike/run_spike.sh`) adds gz-sim. This dir adds the rest needed for the
**#8 nav-traffic 2-bot Gazebo Nav2 E2E (#67)**.

## `install-nav2-e2e.sh`

Installs the Nav2 + twist_mux (+ slam-toolbox) stack required by
`ws/src/warehouse_bringup/launch/nav2_bringup.launch.py` (matches
`warehouse_bringup/package.xml` exec_depend). Re-runnable; verifies the key packages resolve.

```bash
# mount the repo at /ws (same convention as the sim spike), then:
docker exec <container> bash /ws/deploy/dev/install-nav2-e2e.sh
# or run it inside an interactive container shell.
```

## E2E prerequisites NOT covered here (tracked elsewhere)

This script only installs packages. The full E2E (#67) also needs:
- **sim `/clock` + sim_time** and a **world occupancy map** — sim-owned, tracked by **#76**.
- **`bringup.launch.py` compose** of `nav2_bringup.launch.py` — skeleton-owned, **#75**
  (optional; sim + nav2 can run as two `ros2 launch` invocations).
- **AMCL initialpose** — nav-traffic-owned, in `nav2_params.yaml` (seeded from the berth spawn poses).

See #67 for the consolidated prerequisite chain and run/validate steps.

## sim cockpit — `Dockerfile` + `run-sim-cockpit.sh` (reusable viewable env)

A **persistent, browser-viewable** Gazebo+Nav2 environment so you can *watch* the 2 robots
drive (RViz on the noVNC desktop) and record clips — without rebuilding anything each time.

- **`Dockerfile`** bakes the Nav2 stack + window tools (`wmctrl`/`xdotool`) + `ffmpeg`,
  plus Python runtime deps (`pydantic`, `pyyaml`, FastAPI/Uvicorn, `httpx`, Langfuse/OpenAI
  SDKs) on top of `tiryoh/ros2-desktop-vnc:jazzy` (which already carries ROS 2 Jazzy,
  Gazebo Harmonic `gz`, ros_gz, and the noVNC desktop). The ROS workspace is **not**
  baked — mount the repo at `/ws`; the colcon symlink-install build is reused from
  `/ws/ws/install` (host build is pinned to `/ws`).
- **`run-sim-cockpit.sh`** is idempotent: builds the image if missing, creates the `mwr-sim`
  container if missing (repo → `/ws` rw, noVNC on `127.0.0.1:6080`, `--memory=6g`),
  else `docker start`s it. LAN exposure is opt-in with `MWR_SIM_BIND=0.0.0.0`; existing
  containers keep their original Docker publish and the helper warns if it differs.

```bash
deploy/dev/run-sim-cockpit.sh                    # build/create/start; prints the noVNC URL
# open http://localhost:6080  (login ubuntu / ubuntu)  ->  desktop with RViz
# optional LAN exposure on a trusted network:
MWR_SIM_BIND=0.0.0.0 deploy/dev/run-sim-cockpit.sh
# recreate if an older mwr-sim has the old publish/deps:
docker rm -f mwr-sim && deploy/dev/run-sim-cockpit.sh
docker start mwr-sim                             # resume any later day
docker stop  mwr-sim                             # free RAM when done
```

### Watch the 2 robots navigate (Nav2-only — no API keys)

Run **inside** the container (`docker exec mwr-sim bash -lc '…'`). Set the X env so RViz can
draw on the noVNC display, source ROS, then launch:

```bash
export DISPLAY=:1 XAUTHORITY=/home/ubuntu/.Xauthority      # else RViz: "could not connect to display :1"
export LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe      # software GL (no GPU in Docker-on-Mac)
source /opt/ros/jazzy/setup.bash && source /ws/ws/install/setup.bash
export WAREHOUSE_CONFIG_DIR=/ws/config WAREHOUSE_ENV=dev
export WAREHOUSE__SAFETY__POSE_FRESHNESS_TIMEOUT=999        # sim: AMCL only republishes on motion
ros2 launch warehouse_bringup bringup.launch.py sim:=true llm:=false traffic_mode:=none rviz:=true rviz_config:=record
# then (separate exec, after lifecycle active): seed AMCL to the spawn, drive with Nav2 goals:
cd /ws && SCENARIO=default scripts/slice3_seed_initialpose.sh
ros2 action send_goal /bot1/navigate_to_pose nav2_msgs/action/NavigateToPose \
  "{pose: {header: {frame_id: map}, pose: {position: {x: 1.5, y: 0.67}, orientation: {w: 1.0}}}}"
# record:  scripts/slice3_record.sh start /tmp/out.mp4   (ffmpeg x11grab of :1)  ->  ... stop
```

### One-command Mode A live stack (Hermes + LLM Bridge)

Use this path when the goal is to watch the LLM Bridge in the browser-viewable
Gazebo/RViz environment without re-debugging Hermes auth every time.

One-time setup:

```bash
cp config/dev/.env.example config/dev/.env
# Edit config/dev/.env:
#   API_SERVER_KEY must be the same value as ~/.hermes/.env API_SERVER_KEY.
```

Start Hermes in a separate terminal:

```bash
API_SERVER_ENABLED=true hermes gateway
```

Then launch the sim, Bridge, RViz, and head-on seed from the repo:

```bash
deploy/dev/run-mode-a-live.sh
# opens noVNC/RViz at http://localhost:6082  (login ubuntu / ubuntu)
```

For a fully one-command dev path, let the launcher start Hermes in the background
when it is down:

```bash
deploy/dev/run-mode-a-live.sh --start-hermes
# Hermes service-start log: /tmp/mwr_hermes_gateway.log
```

The launcher intentionally uses a separate default container
(`mwr-mode-a-live`, port `6082`) so it does not accidentally reuse an older
`mwr-sim` container mounted to a different worktree. Override when needed:

```bash
MWR_SIM_CONTAINER=mwr-sim-v1 MWR_SIM_PORT=6081 deploy/dev/run-mode-a-live.sh
```

Agents or shells that are not allowed to read `config/dev/.env` can pass the
Bridge token through the process environment instead:

```bash
export API_SERVER_KEY='<same value as ~/.hermes/.env>'
MWR_HERMES_ENV_FILE=/nonexistent deploy/dev/run-mode-a-live.sh --start-hermes
```

What the launcher does:

- runs `deploy/dev/check-hermes-live.sh` before ROS starts;
- verifies Hermes `/health` and authenticated `/v1/models`;
- verifies the sim container can reach Hermes through `host.docker.internal`;
- injects only Bridge-side auth (`API_SERVER_KEY` / `HERMES_API_KEY`) into
  `docker exec`; provider keys remain owned by Hermes;
- restarts the full-stack launch so changed env values are actually picked up;
- exports `WAREHOUSE__HERMES__BASE_URL=http://host.docker.internal:8642`;
- launches `warehouse_bringup` with `llm:=true`, `traffic_mode:=none`,
  `scenario:=head_on`, `rviz_config:=record`;
- runs `scripts/slice3_seed_initialpose.sh` with `SCENARIO=head_on`.

For diagnosis without launching ROS:

```bash
deploy/dev/check-hermes-live.sh
deploy/dev/check-hermes-live.sh --container mwr-mode-a-live
deploy/dev/check-hermes-live.sh --chat        # optional provider call
```

### Gotchas learned the hard way (read before debugging "it won't move")

- **RViz X-auth**: the desktop runs as `ubuntu`; `docker exec` is `root`. Export
  `XAUTHORITY=/home/ubuntu/.Xauthority` or RViz dies with `qt.qpa.xcb: could not connect`.
- **AMCL seed timing**: `slice3_seed_initialpose.sh` publishes `initialpose` once per bot; if a
  bot's AMCL hasn't subscribed yet it stays unlocalized — just re-run the seed (idempotent).
- **Tight map (R-42)** — this is real, not a bug. `maps/map.pgm` is 180×90 @ 1cm; with robot
  footprint + costmap inflation the only wide free lane is the **east-west corridor at y≈0.57–0.77**
  (x≈0.14–1.68). Two facts follow:
  - **Berth goals (y=0.8) and shelf goals (y=0.3) fail to plan** — too close to the top wall
    (`worldToMap failed … size 180,90`) or inside a shelf block. Aim goals into the free corridor.
  - **`emergency_guardian` e-stops at 0.3 m** (`emergency_min_distance`, [safety.md](../../.claude/rules/safety.md)).
    Two robots cannot pass within 0.3 m in this miniature map, so a **head-on cross is stopped by design** —
    exactly why the project needs the **LLM commander to coordinate yielding** (a robot retreats,
    the other passes; doc08a). For a raw Nav2-only *viewing* clip you can relax it with
    `export WAREHOUSE__SAFETY__EMERGENCY_MIN_DISTANCE=0.10` (sim only; real demo keeps 0.3 m).
- **Python deps** are baked in the image now; if you run an *older* `mwr-sim` image/container,
  rebuild or recreate it so FastAPI/Uvicorn, `httpx`, Langfuse/OpenAI, `pydantic`, and `pyyaml`
  are all present.

> This cockpit is a dev/sim convenience. It does **not** replace the on-Jetson validation gates
> (`docs/jetson/01-fidelity-and-validation.md`): GPU/CUDA, real-time jitter, micro-ROS-over-WiFi,
> real sensor accuracy and the 8 GB memory budget are **not** reproducible here.
