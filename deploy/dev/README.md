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
