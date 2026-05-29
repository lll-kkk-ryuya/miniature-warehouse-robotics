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
    install_requires=["setuptools"],
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
