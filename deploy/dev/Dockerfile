# syntax=docker/dockerfile:1
# Reusable Gazebo Harmonic + Nav2 "sim cockpit" image for miniature-warehouse-robotics.
#
# Base: tiryoh/ros2-desktop-vnc:jazzy
#   - ARM64-native, ROS 2 Jazzy, Gazebo Harmonic (`gz`) + ros_gz already inside,
#     and a noVNC desktop (Xtigervnc :1 -> websockify on :80) so RViz / Gazebo GUIs
#     are viewable in a browser at http://localhost:<host-port>.
#   - Default ENTRYPOINT (/entrypoint.sh) brings up the desktop; we inherit it unchanged.
#
# This image bakes ONLY the Nav2 launch-time deps that warehouse_bringup requires
# (mirrors deploy/dev/install-nav2-e2e.sh:17-25). The ROS 2 workspace itself is NOT baked:
# mount the repo at /ws at runtime; the colcon symlink-install build is reused from
# /ws/ws/install (the host build is pinned to the /ws path). See deploy/dev/README.md.
#
#   Build:  docker build -t mwr-sim:jazzy -f deploy/dev/Dockerfile deploy/dev
#   Run:    deploy/dev/run-sim-cockpit.sh        # idempotent: build / create / start
FROM tiryoh/ros2-desktop-vnc:jazzy

ARG ROS_DISTRO=jazzy

# Nav2 stack + twist_mux (+ slam_toolbox for Phase 2a map generation). The base image
# ships ros_gz / Gazebo Harmonic already (the #7 sim spike confirmed `gz` is present),
# so only the Nav2 launch-time exec_depends from warehouse_bringup/package.xml are added.
# Also: window tools (so RViz can be maximised on the noVNC desktop) + ffmpeg (screen
# capture for the slice3 recording wrapper, scripts/slice3_record.sh).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ros-${ROS_DISTRO}-navigation2 \
      ros-${ROS_DISTRO}-nav2-bringup \
      ros-${ROS_DISTRO}-twist-mux \
      ros-${ROS_DISTRO}-slam-toolbox \
      wmctrl xdotool x11-utils ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# Python runtime deps for the warehouse ROS nodes that the bringup launches:
#   pydantic + pyyaml -> warehouse_interfaces schemas/config loader
#   fastapi + uvicorn -> warehouse_nav2_bridge REST API (llm:=true path)
#   httpx              -> MCP -> Nav2 Bridge REST forwarder
#   langfuse + openai  -> warehouse_llm_bridge Hermes client / Bridge-owned trace
# The base image's system Python is externally-managed (PEP 668), so --break-system-packages.
RUN pip3 install --no-cache-dir --break-system-packages \
      "pydantic>=2" \
      pyyaml \
      "fastapi>=0.110" \
      "uvicorn>=0.27" \
      "httpx>=0.27" \
      "langfuse>=4.7,<5" \
      "openai>=1.0"

# ENTRYPOINT / CMD are inherited from the base image (noVNC desktop). Do not override.
