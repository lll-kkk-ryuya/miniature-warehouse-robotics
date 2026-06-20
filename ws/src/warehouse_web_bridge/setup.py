from setuptools import find_packages, setup

package_name = "warehouse_web_bridge"

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
    description="Web Observability gateway (observe-only, doc22): ObsEvent + events.jsonl (S1); "
    "rclpy subscriber + FastAPI (S2).",
    license="Apache-2.0",
    tests_require=["pytest"],
    # S1 ships no console_scripts: the rclpy node entry point (web_bridge = ...:main) is added
    # in S2 (doc22 §13). The offline core is import-only and host-testable without ROS.
    entry_points={"console_scripts": []},
)
