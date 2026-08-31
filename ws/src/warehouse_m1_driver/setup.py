from setuptools import find_packages, setup

package_name = "warehouse_m1_driver"

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
        "ROSMASTER M1 host-side serial driver: L0' velocity clamp before the FUNC_MOTION frame."
    ),
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            # G-l: the executable that wires clamp_body_velocity into the
            # dispatch path (docs/mode-m1/02 §2).
            "m1_driver = warehouse_m1_driver.driver_node:main",
            # Read-only on-robot probe (docs/mode-m1/03 §2). No motion.
            "m1_probe = warehouse_m1_driver.probe:main",
        ],
    },
)
