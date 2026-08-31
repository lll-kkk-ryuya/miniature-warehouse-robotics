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
    # typing_extensions: `Self` on py3.10 (Jetson/Humble, ADR-0008 / #563) — pydantic v2
    # already depends on >=4.x, so this pins an existing transitive dep (no new weight).
    install_requires=[
        "setuptools",
        "langfuse>=4.9,<5",
        "openai>=1.0,<2",
        "pluggy>=1,<2",
        "typing_extensions>=4",
    ],
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
            # Mode X-ER visual-task commander node (docs/mode-x-er/08 §2, XER6). Composed by
            # bringup.launch.py IFF mode_x_er.enabled (mutually exclusive with llm_bridge).
            "x_er_bridge = warehouse_llm_bridge.x_er_bridge:main",
            # L4 Operator Feedback Box runtime subscriber (mode-x-er/05 §8.10 item 4):
            # subscribes /operator/notice + /emergency/event and drives the offline box
            # (render -> sink). SUBSCRIBE-ONLY = 0 actuation (R-26 / L4OF-G1). Launch
            # composition is a follow-up (bringup.launch.py is nav-traffic-owned).
            "operator_feedback = warehouse_llm_bridge.operator_feedback.notice_node:main",
        ],
    },
)
