cmake_minimum_required(VERSION 3.5)

project(minicar_client LANGUAGES C)

# Deps mirror the stock configuration_example/configured_publisher demo (jazzy). Crucially
# this package depends ONLY on rcl/rclc/std_msgs (+ the microxrcedds rmw), NOT on any
# service package — so `colcon build --packages-up-to minicar_client` skips std_srvs /
# example_interfaces, whose rosidl_typesupport_microxrcedds_c fails to link on Jazzy
# (undefined service_msgs__msg__ServiceEventInfo — the service-introspection breakage).
find_package(ament_cmake REQUIRED)
find_package(rcl REQUIRED)
find_package(rclc REQUIRED)
find_package(std_msgs REQUIRED)
find_package(microxrcedds_client REQUIRED)
find_package(rmw_microxrcedds REQUIRED)

add_executable(${PROJECT_NAME} main.c)

target_link_libraries(${PROJECT_NAME}
  microxrcedds_client
)

ament_target_dependencies(${PROJECT_NAME}
  rcl
  rclc
  std_msgs
  microxrcedds_client
  rmw_microxrcedds
)

# lib/${PROJECT_NAME} so `ros2 run minicar_client minicar_client ...` resolves it.
install(TARGETS ${PROJECT_NAME}
  DESTINATION lib/${PROJECT_NAME}
)

ament_package()
