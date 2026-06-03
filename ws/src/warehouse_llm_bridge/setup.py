from setuptools import find_packages, setup

package_name = "warehouse_llm_bridge"

setup(
    name=package_name,
    version="0.0.0",
    packages=find_packages(exclude=["test", "test.*"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
    ],
    # langfuse + openai are lazy-imported (Bridge-owned trace via langfuse.openai,
    # doc08:354-356 / doc13 §7.5); pinned here (the pip source of truth) and absent
    # from CI's pytest env, so the cycle stays testable with fakes. openai brings
    # its own httpx (the Hermes OpenAI-compatible transport).
    install_requires=["setuptools", "langfuse>=4.7,<5", "openai>=1.0"],
    zip_safe=True,
    maintainer="kawaguchiryuya",
    maintainer_email="ryu3124ruyu@gmail.com",
    description="LLM Bridge: commander cycle, exclusivity control, character LLM.",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": ["llm_bridge = warehouse_llm_bridge.llm_bridge:main"],
    },
)
