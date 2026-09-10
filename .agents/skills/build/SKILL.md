---
name: build
description: Build and test the ROS 2 workspace with colcon. Use when asked to build, test, verify ROS packages, or run the project build gate.
---

# Build And Test

Run the ROS 2 workspace build and test flow.

## Steps

1. Run `colcon build --symlink-install`.
2. Source `install/setup.bash`.
3. Run `colcon test`.
4. Run `colcon test-result --verbose`.

If dependencies are missing, report the missing dependency and the command that
failed before installing anything.

## On the robot (`/opt/warehouse`)

- Do not run bare `colcon build`. Use `deploy/jetson/bin/build.sh`: it records the
  build, refuses while the motion stack runs, and never restarts systemd units.
- `colcon test` is optional until doc20 Phase 3 — the merge gate is `pytest tests/unit`.
- Rules: `.claude/rules/build-deploy-run.md` (Build != Deploy != Run; physical runs
  are recorded with `deploy/jetson/bin/record-run.sh`).
