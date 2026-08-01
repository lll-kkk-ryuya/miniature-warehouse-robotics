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
    # fastapi + uvicorn serve the observe-only gateway (doc22 §10/§15); websockets backs the
    # /ws fan-out. Imported lazily in app.py / at module load in web_bridge_node.py, so the
    # pure-core unit tests import without them (same pattern as warehouse_nav2_bridge).
    install_requires=["setuptools", "fastapi>=0.110,<1", "uvicorn>=0.27,<1", "websockets>=12,<14"],
    zip_safe=True,
    maintainer="kawaguchiryuya",
    maintainer_email="ryu3124ruyu@gmail.com",
    description="Web Observability gateway (observe-only, doc22): ObsEvent + events.jsonl (S1); "
    "rclpy subscriber + FastAPI/WebSocket (S2).",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        # Executable name unchanged; cli:main preflights the heavy deps (#283) before importing
        # web_bridge_node (whose module-load rclpy/uvicorn import is what fails first).
        "console_scripts": ["web_bridge = warehouse_web_bridge.cli:main"],
    },
)
