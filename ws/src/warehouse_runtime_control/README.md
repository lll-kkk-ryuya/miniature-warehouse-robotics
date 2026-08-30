# warehouse_runtime_control

Small ROS 2 control-plane adapters for runtime operational constraints.

`runtime_speed_limit` consumes a relative `speed_band` (`std_msgs/String`:
`slow|stable|fast`) and publishes a relative Nav2 `speed_limit`
(`nav2_msgs/SpeedLimit`). When namespaced as `/bot1`, these resolve to
`/bot1/speed_band` and `/bot1/speed_limit`.

The node is disabled by default. Band values must be injected explicitly; this
package does not invent production speed values. It publishes absolute m/s only
and clamps every value to both `safety.max_linear_velocity` and
`MAX_LINEAR_VELOCITY`.

This is not a velocity producer and does not publish `cmd_vel`. Runtime band
limits are operational constraints, not the hard physical safety boundary; the
M1 L0' clamp remains the final speed envelope.
