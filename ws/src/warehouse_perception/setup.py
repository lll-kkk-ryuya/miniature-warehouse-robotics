from setuptools import find_packages, setup

package_name = "warehouse_perception"

setup(
    name=package_name,
    version="0.0.0",
    packages=find_packages(exclude=["test", "test.*"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="kawaguchiryuya",
    maintainer_email="ryu3124ruyu@gmail.com",
    description=(
        "L4 perception home (gesture_detector lands here): speed band publisher "
        "for the runtime speed limiter (ADR-0012)."
    ),
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            # Runtime speed limiter (2): band -> nav2_msgs/SpeedLimit at 20 Hz
            # (docs/mode-m1/04, ADR-0012 Decisions 3-5). Safe-OFF by default.
            "speed_band_publisher = warehouse_perception.speed_band_node:main",
        ],
    },
)
