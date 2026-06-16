#!/usr/bin/env bash
# Build (if needed) and run the reusable `mwr-sim` Gazebo+Nav2 cockpit container.
#
# Idempotent — re-runnable any day:
#   * builds the mwr-sim:jazzy image if it is missing (deploy/dev/Dockerfile),
#   * creates the container if it is missing (repo mounted rw at /ws, noVNC port published),
#   * starts it if it exists but is stopped,
#   * then prints the noVNC URL.
# Recordings / generated files land on the host through the /ws bind mount.
#
# Why a persistent named container: the ROS workspace build (ws/build, ws/install) is
# reused from the host (symlink-install is pinned to /ws), so no colcon rebuild is needed
# on each run. `docker start mwr-sim` resumes the exact same environment.
#
# Env overrides: MWR_SIM_IMAGE / MWR_SIM_CONTAINER / MWR_SIM_PORT / MWR_SIM_MEM / MWR_SIM_SHM
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE="${MWR_SIM_IMAGE:-mwr-sim:jazzy}"
CONTAINER="${MWR_SIM_CONTAINER:-mwr-sim}"
HOST_PORT="${MWR_SIM_PORT:-6080}"
MEM="${MWR_SIM_MEM:-6g}"
SHM="${MWR_SIM_SHM:-1g}"

# 1) image -------------------------------------------------------------------
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "[cockpit] building $IMAGE (one-time; downloads Nav2 ~hundreds of MB) ..."
  docker build -t "$IMAGE" -f "$REPO_ROOT/deploy/dev/Dockerfile" "$REPO_ROOT/deploy/dev"
fi

# 2) container ---------------------------------------------------------------
if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER")" = "true" ]; then
    echo "[cockpit] $CONTAINER already running"
  else
    echo "[cockpit] starting existing $CONTAINER"
    docker start "$CONTAINER" >/dev/null
  fi
else
  echo "[cockpit] creating $CONTAINER (repo -> /ws rw, noVNC :$HOST_PORT, --memory=$MEM)"
  docker run -d --name "$CONTAINER" \
    --memory="$MEM" --memory-swap="$MEM" --shm-size="$SHM" \
    -p "${HOST_PORT}:80" \
    -e LIBGL_ALWAYS_SOFTWARE=1 -e GALLIUM_DRIVER=llvmpipe \
    -v "$REPO_ROOT:/ws" \
    "$IMAGE" >/dev/null
fi

echo "[cockpit] noVNC desktop : http://localhost:${HOST_PORT}   (login: ubuntu / ubuntu)"
echo "[cockpit] workspace      : /ws  (build reused from /ws/ws/install)"
echo "[cockpit] next: see deploy/dev/README.md for the launch + record commands."
