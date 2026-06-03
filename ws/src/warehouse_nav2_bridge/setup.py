from setuptools import find_packages, setup

package_name = "warehouse_nav2_bridge"

setup(
    name=package_name,
    version="0.0.0",
    packages=find_packages(exclude=["test", "test.*"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
    ],
    # fastapi + uvicorn serve the Nav2 Bridge REST API (doc12a:198-343); imported
    # lazily (app.py / nav2_bridge.py) so the pure-core unit tests run without them,
    # the same pattern warehouse_llm_bridge uses for langfuse.
    install_requires=["setuptools", "fastapi>=0.110", "uvicorn>=0.27"],
    zip_safe=True,
    maintainer="kawaguchiryuya",
    maintainer_email="ryu3124ruyu@gmail.com",
    description="REST -> Nav2 BasicNavigator executor for Mode A/B.",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": ["nav2_bridge = warehouse_nav2_bridge.nav2_bridge:main"],
    },
)
