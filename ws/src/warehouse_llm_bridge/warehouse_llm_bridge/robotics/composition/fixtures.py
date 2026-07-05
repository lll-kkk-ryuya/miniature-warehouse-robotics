"""Mode A expressibility fixture — the open-question Q4 probe (doc09 open questions).

This fixture writes the ALREADY-WIRED Mode A stack (bringup.launch.py:205-253: state_cache /
emergency_guardian / nav2_bridge / llm_bridge / character_llm, plus the in-process
WarehouseTools + Policy Gate governance path, llm_bridge.py S2-PR2) as a ``run_manifest.v1``
document, to measure whether the schema can express the live wiring without distortion.

Measured result (kept in sync with the fixture below; asserted by
tests/unit/test_mode_a_composition_fixture.py):

- **Expressible without schema changes.** Every running Mode A component maps onto a doc01
  box (l4_bridge / l2_governance / traffic / navigation / safety / hardware /
  eval_observability); the absent L3 Validator box is expressed by OMISSION, which doc09:124
  defines as "not used in this run" — no ``enabled: false`` stanza needed.

Expressibility findings (taxonomy-level, NOT schema-level gaps — none required a v1 field):

- **F1 (character_llm has no taxonomy home)**: the negotiation layer (persona/negotiation/
  character_node, doc14) is part of the L4 Super-Box implementation but is neither a doc01
  sub-box nor a doc09-cataloged plugin. It IS expressible as a free-form L4 plugin entry
  (``l4.character_llm.scripted_persona`` below) because plugin ids are open strings — but no
  plugin manifest (doc09:183-219) exists for it. Gap belongs to the doc01/doc09 catalogs.
- **F2 (traffic profile <-> traffic_mode translation owner undefined)**: Mode A runs
  ``traffic_mode: none|simple`` (config/warehouse.base.yaml, bringup.launch.py:116-120). The
  box profile string carries it (``x_lite_simple``), but WHICH component translates
  ``boxes.traffic.profile`` into the ``traffic_mode`` config key is undefined — manifests are
  currently a RECORD of what was enabled, not a config SOURCE (doc09:44).
- **F3 (State Cache has no box home)**: ``warehouse_state/state_cache`` (a running Mode A
  node) appears in no doc01 box row. Here it is subsumed under ``safety`` (the safety-state
  track owns it), which is a judgment call the taxonomy does not make for us.
- **F4 (mode itself is not first-class)**: "this is Mode A" is only encoded in profile
  naming (``mode_a_commander``). Sufficient for now-scope; a first-class ``mode``/``scenario``
  field would be a v2 discussion, not a blocker.

Kept as a Python string (mirroring ``robotics_planning_core/fixtures/red_blue_sequence.py``)
so no package_data plumbing is needed and the loader path is exercised end-to-end.
"""

# All plugin/profile NAMES below follow doc09 conventions (doc09:65 `l4.model_adapter.hermes`);
# free-form ids introduced here (F1) are fixture-local and NOT a frozen vocabulary.
MODE_A_RUN_MANIFEST_YAML: str = """\
schema_version: run_manifest.v1
run_id: mode_a_expressibility_probe

boxes:
  # L4 Robotics Bridge Super-Box (doc01:61): llm_bridge commander cycle (3s), Hermes
  # gateway :8642 transport, Bridge-owned Langfuse trace (Pattern A).
  l4_bridge:
    enabled: true
    profile: mode_a_commander
    plugins:
      # HermesClient(LLMClient) — the doc09:65 canonical model-adapter plugin id.
      - id: l4.model_adapter.hermes
        version: 0.1.0
        profile: default
      # Finding F1: negotiation layer (character_llm node, ScriptedPersona offline) has no
      # doc01 sub-box / doc09 catalog entry; expressed as a free-form L4 plugin id.
      - id: l4.character_llm.scripted_persona
        version: 0.1.0
        profile: mode_a_default

  # NOTE: no l3_validator stanza — Mode A has no L3 Planning Core in the loop; omission
  # means "not used in this run" (doc09:124).

  # Governance Box (doc01:66): in-process WarehouseTools dispatch + Policy Gate +
  # gen/idempotency stores (B-3 + C exclusivity layers).
  l2_governance:
    enabled: true
    profile: mini_warehouse_default

  # Traffic Box (doc01:67): Mode A is LLM-only traffic; finding F2 — profile string carries
  # traffic_mode (none|simple) by naming convention only.
  traffic:
    enabled: true
    profile: x_lite_simple

  # Navigation Box (doc01:68): nav2_bridge REST :8645 -> BasicNavigator.
  navigation:
    enabled: true
    profile: nav2_mini_warehouse

  # Safety Box (doc01:69): emergency_guardian 50ms reflex + twist_mux + collision_monitor.
  # Finding F3: state_cache (warehouse_state) has no doc01 box row; subsumed here.
  safety:
    enabled: true
    profile: mini_warehouse_default

  # Hardware Box (doc01:70): Yahboom micro-ROS ESP32 (or Gazebo sim stand-in in dev).
  hardware:
    enabled: true
    profile: yahboom_micro_ros

  # Eval / Observability Box (doc01:71): eval_sdk tracer + WO KPI + Langfuse score sink.
  eval_observability:
    enabled: true
    profile: default

expected_emitters:
  - l4_bridge
  - l2_governance
  - traffic
  - navigation
  - safety
  - hardware
  - eval_observability

score_specs:
  - result
  - task_completion_time
  - efficiency
"""

# The plugin ids the fixture declares (what an S4 registry must register for this run).
MODE_A_PLUGIN_IDS: frozenset[str] = frozenset(
    {"l4.model_adapter.hermes", "l4.character_llm.scripted_persona"}
)
