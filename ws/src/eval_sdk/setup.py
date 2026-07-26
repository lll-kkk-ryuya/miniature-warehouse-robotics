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
    # Pure stdlib core (no hard runtime deps). Two OPTIONAL extras, both lazy-imported so the
    # package builds, imports and unit-tests with them absent (doc21 §4 背骨 / §12.3):
    #   * langfuse — tracer/sink seam only (fail-open). Pinned >=4.9,<5 to match the v4 OTEL
    #     surface the seam targets (doc21 §12.4 / warehouse_llm_bridge/setup.py).
    #   * stats    — numpy for eval_sdk.stats.sparc (FFT). SR/SPL/SoftSPL/jerk/ldlj/n_movement_units
    #     are pure stdlib; only sparc needs numpy and lazy-imports it inside the function, so
    #     `import eval_sdk.stats` works without numpy (doc21 §12.1/:277 "SPARC は numpy のみで足りる"
    #     / §14:406 "numpy FFT・scipy lazy"). Not an install_requires → rosdep unaffected.
    install_requires=["setuptools"],
    extras_require={
        "langfuse": ["langfuse>=4.9,<5"],
        "stats": ["numpy>=1.24"],
    },
    zip_safe=True,
    maintainer="kawaguchiryuya",
    maintainer_email="ryu3124ruyu@gmail.com",
    description="Domain-independent embodied-AI evaluation core: seed/tracer/sink/stats/cost.",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={"console_scripts": []},
)
