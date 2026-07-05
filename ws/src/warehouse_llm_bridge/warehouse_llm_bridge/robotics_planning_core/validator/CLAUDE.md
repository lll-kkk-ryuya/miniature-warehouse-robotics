# robotics_planning_core/validator — Mode X-ER L3 Validator (XER2/G1)

- **担当トラック / ブランチ**: track:mode-x-er / `feat/mode-x-er`（epic #338, XER2）
- **Phase**: XER2（README:88・03:93 G1）
- **編集境界**: この `validator/` サブツリーと同 pkg の `tests/unit/test_plan_*`・`test_validation_report_vocab`・`test_validator_*` のみ。`warehouse_interfaces` / doc03 / config / `scheduler.py` / `llm_bridge.py` / 他 pkg は触らない（bridge-local・doc06 §1 昇格は #343 DEFER）。

## 責務（一行）
ER の `RoboticsPlanDraft` が「実行候補か」を actuation 前に決定論で判定し、`status != accepted` を **0 dispatch** にする L3 安全ゲート（doc02:39-107,248）。最終実行許可は L2 MCP / Policy Gate（doc02:19）。

## 提供 (produce) — 他段（XER3+）/ 呼び出し側が消費しうる公開 IF
- `PlanValidator.validate(raw: dict, context: PlanningContext) -> ValidationReport`（doc02:248。返り型 alias `ValidationResult = ValidationReport`）。parse/schema 失敗は `PlanValidationError` を raise（doc02:92・コード無し）。
- `ValidationReport`（`status / errors[] / warnings[] / normalized_plan`・doc02:60-66,314）。`permits_dispatch`（=status==accepted）・`command_candidates`（accepted 時のみ非空・doc02:68 0-dispatch チョークポイント）。`from_rules(rules, normalized_plan)` で集約（doc02:291,304）。
- `RuleResult{code, severity, field_path, message_for_operator, dispatch_effect, debug_detail}`（doc02:95,310-315）。
- 凍結語彙 enum（doc02:280-346）: `ValidationStatus`{accepted,rejected,needs_clarification,emergency_stop} / `Severity`{error,warning} / `DispatchEffect`{block,needs_clarification,emergency_stop,none} / `ValidationCode`（全9: 8 reject + `OPERATOR_CLARIFICATION_REQUESTED`）。
- `PlanPolicy` / `PlanPolicyOverlay` / `merge_policy(base, *overlays)` / `warehouse_reference_policy(**overrides)`（doc02:94,97,98）。threshold は注入（hardcode せず）。`PlanPolicy.emergency_stop_allowlist: frozenset[str]`（既定 empty）＝**L4 plugin composition の emergency_stop 権威 ceiling**（ADR-0003 item 6 / doc09:388-390）。L4 `PluginDispatchPolicy.derive_from_base` が narrow-only（除去のみ・追加不可）で consume する。値の consume は L4→L3 一方向で、L3 Validator 自体は本 field を使わない（plugin dispatch は L4 の責務）。
- `PlanningContext{policy, runtime}` + `profile_id`/`policy_version` プロパティ・`from_store(policy, store)`。`RuntimeSafetyState{emergency_active, state_age_s}`。`RuntimeStateSource`(Protocol)/`InMemoryRuntimeStateSource`（State Cache 直読み回避・brief step 7・凍結 `warehouse_interfaces.stores.StateStore` 名と衝突回避＝別名）。
- seam（IF のみ・default in-memory・XER3/XER4 で消費）: `Calibration`(doc02:149 5 field)/`CalibrationLoader`/`InMemoryCalibrationLoader`（XER3）・`TaskGraphStore`/`InMemoryTaskGraphStore`（doc02:198・XER4）。

## 消費 (consume)
- 凍結契約: `warehouse_interfaces.locations.KNOWN_LOCATIONS`（locations.py:23）・`warehouse_interfaces.schemas.CommandAction`（schemas.py:135）。**読むだけ・新 location/action を定義しない**（brief step 5）。
- bridge-local: `robotics_planning_core.models`（`RoboticsPlanDraft`/`Detection`/`TaskNode`・`_BridgeModel`）。L3 Handoff（handoff.py）が parse/schema_version/forbidden を先に通す前提。

## 設計判断（docs 接地・要レビュー / DEFER）
- **parse/schema は pydantic 層**（doc02:92）= `RoboticsPlanDraft.model_validate` で、失敗は raise（凍結 9 code に parse/schema code が無いため・vocab 優先）。9 code は semantic check 専用。
- **fail-closed**: freshness gate 設定時に `state_age_s=None`（齢不明）は stale 扱い／confidence gate 設定時に `confidence=None` は低信頼扱い（handoff.py:62-65 と同規律）。docs 明記値でない設計判断＝レビュー対象。
- **target 検証は `target is not None` のときのみ**（doc02:78）。per-action の target 要否は docs 未定義ゆえ発明しない。
- **DAG 検査は stdlib DFS**（NetworkX は XER4 Executor の候補に留め依存を足さない・doc02:196）。
- **`normalized_plan` は意図的 DEFER stub**（型未確定・下流 XER3/4 が確定・doc02:346）。accepted 時は `draft.model_dump()`。
- 返り型名: doc02:248 は `ValidationResult`、凍結 vocab 節と doc06 §1:50 は `ValidationReport`。後者を正とし `ValidationResult` は alias。

## テスト（offline・no ROS / no network）
- `tests/unit/test_plan_validator.py`（各カテゴリ→code・accepted・parse/schema raise）
- `tests/unit/test_validation_report_vocab.py`（語彙 literal・code↔effect↔status 表・集約優先）
- `tests/unit/test_plan_policy.py`（注入・overlay・warehouse reference・同一 raw×policy 差→判定差）
- `tests/unit/test_validator_zero_dispatch.py`（**R-26 safety**: 0-dispatch 不変条件）
- `tests/unit/test_validator_seams.py`（provider 非依存 grep+挙動・seam in-memory・RuntimeStateSource 注入）

## 設計ドキュメント
- docs/mode-x-er/02-l3-planning-core.md:39-107,248,280-346（§1 Validator・IF skeleton・凍結語彙）
- docs/mode-x-er/03-er-adapter-skeleton.md:71,75,93（clarification field・source_model audit-only・G1）
- docs/mode-x-er/06-unfrozen-contract-resolutions.md §1（昇格 DEFER・語彙確定）
