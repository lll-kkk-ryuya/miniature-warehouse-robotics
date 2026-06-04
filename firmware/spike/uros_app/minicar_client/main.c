// R-37 spike — parameterized micro-ROS host client (one "minicar"), pub + sub.
//
// Models ONE Yahboom ESP32 minicar as a native Linux micro-ROS (rclc + rmw_microxrcedds)
// process over UDP, so two of these against ONE micro_ros_agent reproduce the surface of
// R-37 (docs/shared/07-research-notes.md:242) WITHOUT hardware. The two knobs the risk
// turns on — the XRCE-DDS session identity (client_key) and the ROS namespace — are taken
// from argv so run_spike.sh can FORCE a key collision (repro) or keep keys DISTINCT (fix).
//
//   pub: <ns>/hb   (std_msgs/Int32, 10 Hz heartbeat counter)   <- tests this bot's PUBLISH
//   sub: <ns>/cmd  (std_msgs/Int32)                            <- tests this bot's SUBSCRIBE
//
// std_msgs/Int32 is a deliberate minimal stand-in for the real /odom + /cmd_vel contract:
// the R-37 mechanism is a per-session XRCE concern, independent of message type. The
// LaserScan-over-UDP MTU/fragmentation half of the risk (R-43) is NOT covered by loopback
// and stays a Phase-1 on-hardware question — see RESULT.md.
//
// Init pattern copied verbatim from the stock, version-matched demo
// micro-ROS-demos/rclc/configuration_example/configured_publisher/main.c (jazzy):
//   rcl_init_options -> rmw_uros_options_set_udp_address(argv) -> set_client_key -> support_init_with_options.
#include <rcl/rcl.h>
#include <rcl/error_handling.h>
#include <rclc/rclc.h>
#include <rclc/executor.h>

#include <std_msgs/msg/int32.h>

#include <rmw_microros/rmw_microros.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define RCCHECK(fn) { rcl_ret_t temp_rc = fn; if((temp_rc != RCL_RET_OK)){ \
  printf("[FATAL] line %d: rc=%d. Aborting.\n", __LINE__, (int)temp_rc); return 1; } }
#define RCSOFTCHECK(fn) { rcl_ret_t temp_rc = fn; if((temp_rc != RCL_RET_OK)){ \
  printf("[WARN] line %d: rc=%d. Continuing.\n", __LINE__, (int)temp_rc); } }

static rcl_publisher_t publisher;
static rcl_subscription_t subscriber;
static std_msgs__msg__Int32 send_msg;
static std_msgs__msg__Int32 recv_msg;

static const char * g_ns = "?";
static unsigned long g_pub_count = 0;
static unsigned long g_recv_count = 0;

void timer_callback(rcl_timer_t * timer, int64_t last_call_time)
{
  (void) last_call_time;
  if (timer != NULL) {
    RCSOFTCHECK(rcl_publish(&publisher, &send_msg, NULL));
    g_pub_count++;
    if (g_pub_count % 10 == 0) {           // ~1 line/sec at 10 Hz
      printf("[%s] PUB hb=%d (sent %lu, recv %lu)\n",
             g_ns, send_msg.data, g_pub_count, g_recv_count);
      fflush(stdout);
    }
    send_msg.data++;
  }
}

void subscription_callback(const void * msgin)
{
  const std_msgs__msg__Int32 * m = (const std_msgs__msg__Int32 *)msgin;
  g_recv_count++;
  printf("[%s] SUB cmd=%d (recv %lu)\n", g_ns, m->data, g_recv_count);
  fflush(stdout);
}

int main(int argc, char * const argv[])
{
  if (argc < 5) {
    printf("Usage: minicar_client <agent_ip> <agent_port> <namespace> <client_key_hex> [domain_id]\n");
    printf("  e.g. minicar_client 127.0.0.1 8888 bot1 0xB0A71001 0\n");
    return 1;
  }
  const char * agent_ip   = argv[1];
  const char * agent_port = argv[2];
  const char * ns_in      = argv[3];
  uint32_t     client_key = (uint32_t) strtoul(argv[4], NULL, 0);  // accepts 0x.. hex
  size_t       domain_id  = (size_t)(argc >= 6 ? atoi(argv[5]) : 0);

  // rcl node namespace must be absolute ("/bot1"); accept "bot1" or "/bot1" on the CLI.
  char ns_abs[64];
  if (ns_in[0] == '/') snprintf(ns_abs, sizeof(ns_abs), "%s", ns_in);
  else                 snprintf(ns_abs, sizeof(ns_abs), "/%s", ns_in);
  g_ns = ns_in;

  rcl_allocator_t allocator = rcl_get_default_allocator();
  rclc_support_t support;
  rcl_init_options_t init_options = rcl_get_zero_initialized_init_options();

  RCCHECK(rcl_init_options_init(&init_options, allocator));
  rmw_init_options_t * rmw_options = rcl_init_options_get_rmw_init_options(&init_options);
  RCCHECK(rmw_uros_options_set_udp_address(agent_ip, agent_port, rmw_options));
  RCCHECK(rmw_uros_options_set_client_key(client_key, rmw_options));
  RCCHECK(rcl_init_options_set_domain_id(&init_options, domain_id));

  printf("[%s] connecting agent=%s:%s client_key=0x%08X domain=%zu ns=%s\n",
         g_ns, agent_ip, agent_port, client_key, domain_id, ns_abs);
  fflush(stdout);

  RCCHECK(rclc_support_init_with_options(&support, 0, NULL, &init_options, &allocator));

  rcl_node_t node;
  char node_name[64];
  snprintf(node_name, sizeof(node_name), "minicar_%s", g_ns);
  RCCHECK(rclc_node_init_default(&node, node_name, ns_abs, &support));

  RCCHECK(rclc_publisher_init_default(
    &publisher, &node, ROSIDL_GET_MSG_TYPE_SUPPORT(std_msgs, msg, Int32), "hb"));
  RCCHECK(rclc_subscription_init_default(
    &subscriber, &node, ROSIDL_GET_MSG_TYPE_SUPPORT(std_msgs, msg, Int32), "cmd"));

  rcl_timer_t timer;
  const unsigned int timer_timeout_ms = 100;   // 10 Hz
  RCCHECK(rclc_timer_init_default(&timer, &support, RCL_MS_TO_NS(timer_timeout_ms), timer_callback));

  rclc_executor_t executor = rclc_executor_get_zero_initialized_executor();
  RCCHECK(rclc_executor_init(&executor, &support.context, 2, &allocator));
  RCCHECK(rclc_executor_add_timer(&executor, &timer));
  RCCHECK(rclc_executor_add_subscription(
    &executor, &subscriber, &recv_msg, &subscription_callback, ON_NEW_DATA));

  send_msg.data = 0;
  printf("[%s] ready: pub %s/hb, sub %s/cmd\n", g_ns, ns_abs, ns_abs);
  fflush(stdout);

  rclc_executor_spin(&executor);

  RCCHECK(rcl_subscription_fini(&subscriber, &node));
  RCCHECK(rcl_publisher_fini(&publisher, &node));
  RCCHECK(rcl_node_fini(&node));
  return 0;
}
