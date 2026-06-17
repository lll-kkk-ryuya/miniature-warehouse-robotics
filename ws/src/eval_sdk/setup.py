from setuptools import find_packages, setup

package_name = "eval_sdk"

setup(
    name=package_name,
    version="0.0.0",
    packages=find_packages(exclude=["test", "test.*"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
    ],
    # Pure stdlib core (no hard runtime deps). langfuse is an OPTIONAL extra used only
    # by the tracer/sink seam: it is lazy-imported and fail-open, so the package builds,
    # imports and unit-tests with langfuse absent (doc21 §4 背骨 / §12.3). Pinned
    # >=4.9,<5 to match the v4 OTEL surface the seam targets (doc21 §12.4 /
    # warehouse_llm_bridge/setup.py).
    install_requires=["setuptools"],
    extras_require={"langfuse": ["langfuse>=4.9,<5"]},
    zip_safe=True,
    maintainer="kawaguchiryuya",
    maintainer_email="ryu3124ruyu@gmail.com",
    description="Domain-independent embodied-AI evaluation core: seed/tracer/sink/stats/cost.",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={"console_scripts": []},
)
