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
