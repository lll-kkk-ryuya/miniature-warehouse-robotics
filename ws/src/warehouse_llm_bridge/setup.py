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
    # from the default CI pytest env, so the cycle stays testable with fakes. openai brings
    # its own httpx (the Hermes OpenAI-compatible transport).
    # langfuse >=4.9: tracing.py uses the 4.9 OTEL API (client.create_trace_id /
    # start_as_current_observation / propagate_attributes) — 4.7.x exposed a different shape
    # that failed at runtime (verified at 4.9.0, #88). openai <2: guard a major bump
    # that could break the langfuse.openai wrapper / the chat.completions kwargs.
    # pluggy: the composition validate_plan hook backbone (robotics/composition/plugins.py) —
    # a HARD import at module load (not lazy). Required once the composition seam is wired into a
    # runtime node; pip-only like langfuse/openai (not a rosdep key). Today the composition
    # subtree is spike-isolated (no entry_point imports it), so it only runs under pytest where
    # pluggy is a transitive dep — pinned here so a pytest-less runtime install does not ImportError.
    install_requires=["setuptools", "langfuse>=4.9,<5", "openai>=1.0,<2", "pluggy>=1,<2"],
    zip_safe=True,
    maintainer="kawaguchiryuya",
    maintainer_email="ryu3124ruyu@gmail.com",
    description="LLM Bridge: commander cycle, exclusivity control, character LLM.",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "llm_bridge = warehouse_llm_bridge.llm_bridge:main",
            # Bot1/Bot2 character-LLM negotiation layer (doc14, Slice 2). Mode A/B node,
            # composed by bringup.launch.py when traffic_mode != open-rmf.
            "character_llm = warehouse_llm_bridge.character_node:main",
            # Seed the commander prompts into Langfuse Prompt Management (idempotent upsert;
            # default dry-run). doc08 §Langfuse Prompt Management 方針.
            "seed_prompts = warehouse_llm_bridge.seed_prompts:main",
        ],
    },
)
