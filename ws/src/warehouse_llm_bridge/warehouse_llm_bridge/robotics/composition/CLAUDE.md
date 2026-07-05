# robotics/composition — L3 plugin composition seam (S4 spike: pluggy + typed validate_plan)

- **担当トラック / ブランチ**: productization spike S4（pluggy PluginManager / typed hookspec /
  namespaced plugin codes / trust & fail-closed granularity）
- **位置づけ**: design-fork 比較実装（Grill Q1 / open Q5・Q6）。**未凍結・bridge-local**。
  `warehouse_interfaces` と凍結 Validator 語彙（`robotics_planning_core/validator/report.py:69-88`）
  は一切編集しない（兄弟実装で成立することが検証対象）。
- **編集境界**: この `composition/` サブツリーと `tests/unit/test_plugin_composition.py` のみ。

## 提供 (produce)
- `PluginComposition`（pluggy wrap）: `register(plugin, plugin_id)` / `run_validate_plan(plan, context)`
  / `registered_plugin_ids()` / `missing_declared()` / `preflight()`（S2 の manifest 宣言 ⊆ 登録集合
  fail-closed preflight 用 seam）。
- typed hookspec `validate_plan(plan, context)`（引数名は doc09:246 literal・改名禁止 canary あり）。
- `NamespacedPluginRuleResult`（変種A: 単一 `code`="<plugin_id>:<reason_code>"）/
  `StructuredPluginRuleResult`（変種B: `plugin_id`+`reason_code` 分離・**推奨**）。
- `PluginDispatchPolicy` + `clamp_finding`（plugin は effect を「要求」・policy が上限 clamp・
  `emergency_stop` は allowlist のみ・clamp は `clamped_from` に記録）。
- `PluginCodeRegistry`（manifest `emits.reason_codes` 由来・未宣言 code → needs_clarification へ
  fail-closed 変換）。`from_manifest_dicts` が S2 manifest との統合点。
- `ComposedValidationReport` + `compose_report`（凍結 `from_rules` 無編集の兄弟集約・
  most-severe-wins 格子を core+plugin へ均一適用・0-dispatch は composed status がゲート）。
- `validate_with_plugins(validator, raw, context, composition)`（core 検証 → plugin の順・
  parse 失敗は plugin 実行前に raise）。

## 消費 (consume)
- L3 `robotics_planning_core.validator`（`report.py` 凍結語彙・`_EFFECT_TO_STATUS`/`_STATUS_PRIORITY`
  格子を read-only import・`PlanningContext`・`PlanValidator`）。L4→L3 の一方向依存のみ。
- pip: `pluggy>=1`（pytest 依存で既に環境に存在）。

## 設計判断（docs 接地）
- plugin code は凍結 9 code と構造的に非交差（小文字+`:` 必須 vs 大文字コロン無し・
  doc09:204 / report.py:79-87）。`RuleResult` への密輸は pydantic enum 検証で不可（test 固定）。
- fail-closed: 未宣言 code / spoof / malformed → needs_clarification（doc10:394-395）・
  crash → blocking reject（isolate mode）または composition 全体 refuse（refuse_run mode）。
  fail-closed 変換と crash 結果は clamp 対象外（policy で無効化できない）。
- trust model: in-proc hookimpl の enforce は幻想（test で実証）→ advisory（manifest 自己申告
  doc09:216-218 + human review + registry preflight + 例外 fail-closed）。

## テスト
- `tests/unit/test_plugin_composition.py`（64 tests・host-runnable・ROS/network 不要・
  安全不変条件は `@pytest.mark.safety`）。
