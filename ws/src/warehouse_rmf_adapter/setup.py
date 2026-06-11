from setuptools import find_packages, setup

package_name = "warehouse_rmf_adapter"

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
        "GATE-前 design scaffold for the Mode C 案A EasyFullControl Fleet Adapter "
        "(self-built, drives namespaced /bot{n} Nav2 directly, no zenoh). DESIGN ONLY — "
        "implementation BLOCKED on the R-38 memory gate (#187). 設計正本: docs/mode-c/11c §3.5."
    ),
    license="Apache-2.0",
    tests_require=["pytest"],
    # console_scripts は GATE 後に実装ノードが確定するまで意図的に空（GATE 前は runnable node なし）。
    entry_points={
        "console_scripts": [],
    },
)
