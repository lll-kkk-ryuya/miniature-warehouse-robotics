# ROS 2 Guidance

Source reference: `.claude/rules/ros2.md`.

- Python nodes use `rclpy`; C++ nodes use `rclcpp`.
- Declare parameters explicitly with `declare_parameter()`.
- Use lifecycle nodes where state management requires it.
- Topic and service names use `snake_case`; robot-specific topics live under
  robot namespaces such as `/bot1/cmd_vel`.
- Package launch files live under package `launch/` directories and use
  `.launch.py`.
- Set QoS profiles explicitly.
- micro-ROS agents must include reconnection handling.
