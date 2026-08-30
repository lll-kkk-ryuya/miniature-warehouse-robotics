# warehouse_runtime_control — runtime constraint control-plane (L1)

- **Layer**: L1 runtime constraint control-plane. It affects Nav2 controller
  constraints but has **no actuation authority of its own** and publishes no
  `cmd_vel`.
- **Purpose**: keep runtime speed-band translation separate from L4 perception,
  L2 motion authorization, Nav2 goal execution, and L0' hardware dispatch.
- **Node**: `runtime_speed_limit`.
- **Input**: relative `speed_band` (`std_msgs/String`, `slow|stable|fast`).
- **Output**: relative `speed_limit` (`nav2_msgs/SpeedLimit`, absolute m/s only).
- **Hard ceiling**: every output is `min(band, config operating cap,
  MAX_LINEAR_VELOCITY)` and must be finite and strictly positive. `0.0` is never
  emitted because Nav2 interprets it as `NO_SPEED_LIMIT`.
- **Default**: `enabled=false`; speed-band values are not invented here and must
  be supplied after S-SPEED / OQ-T1/T2 are resolved.
- **R4 limitation**: periodic republish improves operational consistency but is
  **not a hard safety guarantee** on Humble MPPI. An MPPI reset may clear the
  runtime limit inside the first controller call after inactivity before the
  next republish. L0' remains the only hard runtime speed envelope in Mode M1.
- **Recovery**: `behavior_server` is intentionally out of scope; runtime bands
  constrain controller plugins, not recoveries.
- **Tests**: `tests/unit/test_runtime_speed_control.py` is ROS-free and pins the
  positive-value rule, double cap, band ordering, absolute mode, and the
  no-`cmd_vel` invariant.

The package is deliberately small. Do not add task/goal policy, gesture
recognition, emergency-stop logic, or serial-driver behavior here.
