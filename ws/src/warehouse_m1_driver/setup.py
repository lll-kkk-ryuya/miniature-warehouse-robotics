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
    # No console_scripts yet: this slice ships the pure L0' clamp only. The serial
    # driver node entry point lands with the FUNC_MOTION framing slice.
    entry_points={
        "console_scripts": [],
    },
)
