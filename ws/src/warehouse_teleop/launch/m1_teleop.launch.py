"""Mode M1 standalone bring-up: ``joy_node`` + ``teleop_joy`` with the joy_node
prerequisites PINNED IN CODE instead of in an operator's shell history.

Why this file exists
--------------------
``docs/mode-m1/03-joystick-teleop-bringup.md:52`` DECLARES two ``joy_node`` prerequisites
(``autorepeat_rate`` must not be 0, ``sticky_buttons`` must be false), and the canonical
command that satisfies them lives in that doc's §5-1 (added by PR #641, which lands before
this file). Until now nothing enforced them: forgetting either flag on the command line
silently removes the stop-authority prerequisites, and the symptom only shows up while
DRIVING ("it stops when I hold the stick", "the e-stop will not clear"). This launch is
that enforcement point (``warehouse_teleop/CLAUDE.md`` residual ⑧).

The four pinned ``joy_node`` params (values + rationale = 03 §5-1 table):

* ``autorepeat_rate = 20.0`` — NOT 0. At 0 a held button stops ``/joy`` publication, so
  ``teleop_joy``'s freshness dead-man (``joy_timeout_s``, default 0.6 s = 03:51) would judge
  NORMAL operation stale and drop the republish to a zero twist.
* ``sticky_buttons = False`` — true turns buttons into toggles, which breaks rising-edge
  detection and therefore the whole latch semantics (e-stop engage / clear chord / deadman
  re-arm are ALL edge-driven, docs/mode-m1/05 §5-§6).
* ``deadzone = 0.0`` — the neutral decision has exactly one home: ``teleop_joy``'s own
  ``deadzone`` (default 0.1 = ``joymap.DEFAULT_DEADZONE``), which the re-arm "all three axes
  neutral" observation also uses. ``joy_node``'s deadzone rescales the values it forwards, so
  a non-zero value here would shift the effective threshold the teleop side thinks it applies.
* ``device_id`` — launch arg (default 0): one receiver plugged in. Selection behaviour with
  several gamepads attached is unverified on hardware (03 §5-1 ``# TODO(実機)``).

What this launch deliberately does NOT start
--------------------------------------------
* ``m1_driver`` — it MOVES THE ROBOT. It may only be started after the §2 probes and the G-g
  watchdog test, with the wheels fully off the ground and a hand on the battery cutoff
  (W-4 = docs/mode-m1/02-m1-driver-and-watchdog.md:65 / :76 — with W-3 absent, W-4 is a
  REQUIRED layer, not an optional precaution). A launch file that brought the driver up with
  the gamepad would make "start the teleop stack" mean "energise the wheels", which is exactly
  the coupling W-4 forbids. Start it by hand, in its own terminal (03 §5-1 step ③).
* Nav2 / twist_mux — M0-M2 bring-up is standalone by design (03:50); ``teleop_joy`` publishes
  ``/<bot>/cmd_vel`` directly and ``m1_driver`` consumes it.

Button indices stay ros params (defaults imported from ``joymap`` so this file never becomes a
second source of truth). The real pad's indices are confirmed with ``/joy`` at the M1 gate and
overridden here on the command line if they differ (03 §5-2 step 6: the measurement wins).
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from warehouse_teleop.joymap import (
    DEFAULT_DEADMAN_BUTTON,
    DEFAULT_ESTOP_BUTTON,
    DEFAULT_ESTOP_BUTTON_ALT,
)


def generate_launch_description() -> LaunchDescription:
    """joy_node (params pinned) + teleop_joy. No driver, no Nav2, no mux."""
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "bot",
                default_value="bot1",
                description="Robot id; teleop publishes /<bot>/cmd_vel (doc03:87).",
            ),
            DeclareLaunchArgument(
                "device_id",
                default_value="0",
                description=(
                    "joy_node enumeration index (NOT guaranteed to equal the N of /dev/input/jsN)."
                ),
            ),
            # Index defaults come from joymap so the pad layout has ONE source of truth
            # (03 §5-2 expects these to be overridden here once /joy is measured).
            DeclareLaunchArgument(
                "deadman_button",
                default_value=str(DEFAULT_DEADMAN_BUTTON),
                description="Hold-to-drive button (L1 in PC/PCS mode).",
            ),
            DeclareLaunchArgument(
                "estop_button",
                default_value=str(DEFAULT_ESTOP_BUTTON),
                description="Deliberate e-stop (B). -1 disables the whole latch feature.",
            ),
            DeclareLaunchArgument(
                "estop_button_alt",
                default_value=str(DEFAULT_ESTOP_BUTTON_ALT),
                description="Reflex e-stop (R1), optional second button. -1 = none.",
            ),
            Node(
                package="joy",
                executable="joy_node",
                name="joy_node",
                output="screen",
                parameters=[
                    {
                        # Substitution params reach the node as STRINGS unless typed.
                        "device_id": ParameterValue(
                            LaunchConfiguration("device_id"), value_type=int
                        ),
                        # The two prerequisites declared in 03:52 — see module docstring.
                        "autorepeat_rate": 20.0,
                        "sticky_buttons": False,
                        # Neutral threshold lives in teleop_joy only.
                        "deadzone": 0.0,
                    }
                ],
            ),
            Node(
                package="warehouse_teleop",
                executable="teleop_joy",
                name="teleop_joy",
                output="screen",
                parameters=[
                    {
                        "bot": LaunchConfiguration("bot"),
                        "deadman_button": ParameterValue(
                            LaunchConfiguration("deadman_button"), value_type=int
                        ),
                        "estop_button": ParameterValue(
                            LaunchConfiguration("estop_button"), value_type=int
                        ),
                        "estop_button_alt": ParameterValue(
                            LaunchConfiguration("estop_button_alt"), value_type=int
                        ),
                    }
                ],
            ),
            # estop_clear_buttons (int array) is NOT exposed as a launch arg: array param type
            # inference on the real pad is unverified (CLAUDE.md residual S-3), so the node's
            # own default ([10, 11]) stays the only path until that is measured.
        ]
    )
