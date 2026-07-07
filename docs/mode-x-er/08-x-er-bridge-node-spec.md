# 08 x_er_bridge node 仕様（XER6/G5 の背骨・X-ER commander node 契約）

> **状態**: 設計正本（XER6 実装の前提 doc）。[07-implementation-status](07-implementation-status.md):55 `next_wire` が示す「背骨 1 本」＝新規 `warehouse_llm_bridge/x_er_bridge.py` の **node 契約**をここに確定する（07 は現状記録・本書が設計正本）。生成 2026-07-07・全 file:line は `origin/main cdb34e0` で検証済み。
> **スコープ**: XER6（X-lite E2E・[#342]・[README](README.md):91）。live 手順は [dev/08-xer6-live-sim-x-lite-runbook](../dev/08-xer6-live-sim-x-lite-runbook.md)（operator 用）・[dev/07 live runbook](../dev/07-mode-x-er-live-e2e-runbook.md)（課金 gate）。X-rmf は対象外（`NotImplementedError` fail-closed・#346）。
> **凍結契約**: 本書は `warehouse_interfaces` を一切変更しない（bridge-local のみ）。

## 1. 位置づけ（何を閉じるか）

[07 §トップダウン連結](07-implementation-status.md):44-53 の connectivity hops のうち、**⓪（稼働 cycle）③（adapter→pipeline 受け渡し）④（L3 chain running 到達）⑤（Command→L2 dispatch・X-ER 経路）を 1 node で一括で閉じる**。Mode A の稼働 commander（`llm_bridge.py` → `scheduler.py` dispatch）と同型の常駐 rclpy node を X-ER 用に 1 本立てる。hop ⓪は doc07 上「稼働 cycle」と「mic → audio capture」の 2 要素が同居しており（[07:46](07-implementation-status.md)）、本 node が閉じるのは**稼働 cycle 部分のみ**＝**実マイク capture 部分は対象外**（別 follow-up）。hop ②（live-send）は機構 land 済（#389）で、本 node からは offline=fixture / live=env-gate 越しに呼ぶ。

## 2. node 契約（形）

| 項目 | 契約 | 根拠 |
|---|---|---|
| module | `warehouse_llm_bridge/x_er_bridge.py`（`main()` + `rclpy.Node`） | [07:55](07-implementation-status.md)・greenfield（`origin/main` に不存在） |
| console_scripts | `x_er_bridge = warehouse_llm_bridge.x_er_bridge:main`（`llm_bridge` と同型） | `setup.py` 既存形 |
| 起動 gate | `bringup.launch.py` が **`mode_x_er.enabled == true` のときのみ** node を追加（§3。`traffic_mode=='x-er'` 値は**発明しない**＝[06 §3 追補](06-unfrozen-contract-resolutions.md)） | [06:29](06-unfrozen-contract-resolutions.md) RESOLVED |
| 相互排他 | `mode_x_er.enabled == true` の bringup は **Mode A commander（`llm_bridge`）を起動しない**（司令 node は常に 1 本＝gen 発番・排他制御 B-3 の一意 owner を保つ） | [01:184-197](01-architecture-and-flow.md)（gen_id は Bridge 発番）・[#342] DoD「#4 と mode 分岐を調整」 |
| 依存 | 凍結契約 `warehouse_interfaces`＋自 package 内（`robotics/`・`robotics_planning_core/`・`robotics/composition/`）のみ。他トラック内部 import なし | [parallel-workflow.md §2.1](../../.claude/rules/parallel-workflow.md) |
| actuation | node 自身は **0 actuation**。motion は既存 L2 経路（MCP tool → Policy Gate → Nav2 Bridge REST）のみ。L3 は R-26 reject で**空 Command・store 無接触** | [02:360](02-l3-planning-core.md)・`pipeline.py:108,171-175` |

## 3. `mode_x_er:` config key（凍結形）

[06 §3](06-unfrozen-contract-resolutions.md):29,99-104 の DEFER を解除し（追補は 06 末尾）、以下を凍結する。**`traffic_mode` と直交**・完全 additive（`load_config` は未知 top-level key を deep-merge 素通し＝06:96）・`warehouse_interfaces` 不触:

```yaml
mode_x_er:                       # 新規 top-level（base は全て安全側 OFF/空）
  enabled: false                 # bringup が x_er_bridge を起動するか（既定 OFF・本 PR で凍結）
  execution_profile: x_lite      # x_lite | x_rmf（値は 01:203-204 由来。x_rmf は NotImplementedError）
  calibration_id: ""             # config/<env>/calibration/<id>.yaml の stem（06:105 の 5 field YAML）
  run_manifest: ""               # run_manifest.v1 YAML への path（空＝composition 起動拒否＝fail-closed）
  plugin_manifests: []           # plugin.yaml path の list（run manifest 宣言と全一致要・§4）
  site_profile:                  # 安全クリティカル profile gate（§4 step6）の解決子
    base_dir: ""                 # site_profiles/ ルート（productization/04 §Site Profile）
    customer: ""
    site: ""
```

- `enabled` / `run_manifest` / `plugin_manifests` / `site_profile.*` は本 PR での追加凍結（06 提案形は `execution_profile`/`calibration_id` の 2 key。manifest ingestion の**取得元未定義**＝[productization/09](../productization/09-run-manifest-and-plugin-composition.md):402-416 RESIDUAL をここで解消する）。
- calibration artifact は 06:105 のとおり **`config/<env>/calibration/<id>.yaml`**（`camera_id / map_frame / homography(3x3) / reprojection_error / valid_polygon`＝`02:149` 逐語 5 field・コード定数埋込禁止 `02:277`）。
- **base.yaml への実追加は本 PR ではしない**: `config/warehouse.base.yaml` は bringup/skeleton 所有 → XER6 実装レーンが**所有 Issue へ予告 → 合意 → 末尾追記**の additive PR で行う（[06:110](06-unfrozen-contract-resolutions.md) contract PR 手順どおり）。
- 未決（実装時に 1 行追記で確定）: governed 経路（§5）の `resolve_governed_calibration(profile, camera_id)` に渡す `camera_id` と `calibration_id` の突合 semantics（#416 merge 後に確定）。

## 4. composition 起動シーケンス（fail-closed・起動時 1 回）

すべて **raise ⇒ 起動拒否 ⇒ 0 dispatch**（部分的に立ち上がらない）。順序と根拠 file:line（`robotics/composition/`）:

1. `load_run_manifest(mode_x_er.run_manifest)` — [loader.py:37](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/loader.py)。unknown `schema_version` 含め malformed は reject。
2. `load_plugin_manifests(...)` → `build_plugin_code_registry(run_manifest, plugin_manifests)` — [plugin_manifest.py:205,301](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/plugin_manifest.py)。run 宣言 plugin に manifest 無し＝raise。
3. `PluginDispatchPolicy.derive_from_base(base=PlanPolicy の emergency_stop allowlist, requested=...)` — [plugin_results.py:272](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/plugin_results.py)（narrow-only・base ceiling は #410）。
4. `PluginComposition(...)` を構築し、各 hookimpl を **manifest の `plugin_id` 名で** `register(impl, plugin_id)` — [plugins.py:123,150](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/plugins.py)。
5. `preflight_composition(manifest, composition)` — [preflight.py:57](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/preflight.py)（`==` 集合等価＝#410）。**reconciliation（step2）と preflight（step5）は別物**であり、`run-declared == registered hookimpls == plugin-manifest-present` の**三重突合を回すのは x_er_bridge の責務**（[plugin_manifest.py:22-27](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/plugin_manifest.py) が XER6 へ明示的に委譲）。
6. site profile gate: `load_site_profile(base_dir, customer, site)` → `compute_content_hash` → `load_approved_record` → `verify_against_approved(...).assert_verified()` → `build_calibration_loader(profile)` — [profile.py:161,287,197,320,148](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/profile.py)・[calibration_gate.py:291](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/calibration_gate.py)。
7. `build_effective_composition(...)` → `write_run_artifacts(...)`（`out/runs/<run_id>/`） — [record.py:139,242](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/record.py)。**enabled な全 box に `ConstructedBox` entry が必須**（in-process で構築しない box は `stage=None` の entry を明示列挙・[record.py:181-183](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/record.py) の missing raise）。
8. ER adapter 構築: `build_er_adapter(cfg)` — [adapter_factory.py:77](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/adapter_factory.py)（config→transport 解決・shipped 既定 DIRECT fail-safe＝[ADR-0002](../adr/0002-er-in-hermes-standard.md):43）。**offline test は factory を経由せず** `GeminiErAdapter(offline_payload=...)` を注入する（§8）。

> **manifest は record であって config source ではない**（[composition/fixtures.py](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/fixtures.py):20-32 の F2/F4）。stage オブジェクトは caller（本 node）が warehouse config から自前構築し、step7 が「recorded==ran」を事後突合する。

## 5. cycle 設計（毎 cycle・fail-closed）

async 境界: `propose_plan` は async・L3/composition は sync のため、Mode A と同型の **background thread + 専用 event loop**（[llm_bridge.py:254-255,297](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/llm_bridge.py)）で回す。

1. **入力**: `ErTaskRequest`（[er_task.py:31](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/er_task.py)・`known_locations ⊆ KNOWN_LOCATIONS`）。v0 の入力源は fixture / 事前録音 ref（実マイク capture＝07 hop ⓪の mic 要素は対象外）。
2. **ER**: `raw = await adapter.propose_plan(req)`（offline=fixture 即答／live=`WAREHOUSE_LIVE_ER` gate 越し・**agent は gate を立てない**＝[dev/07 §4.5](../dev/07-mode-x-er-live-e2e-runbook.md)）。
3. **plugin 合成 validate（F1 判断＝二重 validate を採用）**: `validate_with_plugins(PlanValidator(), draft, ctx, composition)` — [plugins.py:409](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/plugins.py) を先に呼び、`ComposedValidationReport.permits_dispatch`（[plugins.py:370](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/plugins.py)）が false なら **その cycle は 0 dispatch・store 無接触で終了**。理由: L3 入口 `compile_raw_output` は `PlanValidator()` を内部構築し plugin composition を受けない（[pipeline.py](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/pipeline.py):171 相当・plugin 経路は別関数）＝**この guard 無しでは manifest 宣言 plugin が reject しても Command が出る**。代替案（resolve→execute→compile の tail 再組立）は pipeline 内部の複製＝drift 源として不採用。コスト＝core validator が cycle あたり 2 回走る（offline 決定論・許容）。
4. **L3**: `cmd: Command = compile_raw_output(raw, calibration=<§4 step6 の governed 経路>, resolver_policy=VisualPolicy(...), executor=<長命 executor>)` — [pipeline.py:90,98-99](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/pipeline.py)。executor は node が保持する **長命 `TaskGraphExecutor`**（[executor.py:70](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/task_graph_executor/executor.py)）を毎回同一注入（plan ごと・cycle ごと live handle は 1 つ＝[02:361](02-l3-planning-core.md) STALE-HANDLE 契約）。
5. **gen 発番**: node が `FileGenStore` で mint（[llm_bridge.py:152](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/llm_bridge.py) と同型・LLM/ER 由来値は使わない＝[01:184-197](01-architecture-and-flow.md)）。
6. **dispatch**: `command_to_tool_calls(cmd, gen)`（[action_map.py:92](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/action_map.py)）→ `DispatchToolExecutor`（[llm_bridge.py:238](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/llm_bridge.py) と同型）→ `WarehouseTools.dispatch`（[tools.py:124](../../ws/src/warehouse_mcp_server/warehouse_mcp_server/tools.py)）。**offline slice は `nav2_forwarder=None`**（[tools.py:92-115](../../ws/src/warehouse_mcp_server/warehouse_mcp_server/tools.py)＝受理・記帳のみ・0 actuation）→ sim では `Nav2RestForwarder`（`/api/v1/{navigate,wait,stop}`＝[nav2_client.py:45](../../ws/src/warehouse_mcp_server/warehouse_mcp_server/nav2_client.py)）へ **config で** flip（コード変更ゼロ）。
7. **progression**: dispatch 受理後の lifecycle（`mark_running` → 完了確認 → `mark_succeeded`）は **caller loop＝本 node が所有**（[02:359](02-l3-planning-core.md) 三分割所有）。`compile_raw_output` は one-shot ready-tasks-only のため、**red→blue の順序 demo は t1 完了後に次 cycle で再呼び**して t2 を ready 化する（[dev/08](../dev/08-xer6-live-sim-x-lite-runbook.md) §4 手動手順の自動化）。完了信号（`/nav2_bridge/goal_result` 等）→`mark_succeeded` の変換は本 node の実装範囲。

## 6. エラー方針（すべて安全側）

- **起動時**: §4 のどの step の raise でも node は cycle を開始しない（0 dispatch）。
- **cycle 中**: adapter 失敗（network/gate/parse）＝その cycle を skip・store 無接触。calibration 拒否＝`GovernedCalibrationUnavailableError`（#416 seam・None を黙って通さない）＝0 dispatch。plugin reject（§5 step3）＝0 dispatch・store 無接触。L3 reject（R-26）＝空 Command・store 無接触（[pipeline.py:108,171-175](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/pipeline.py)）。
- **例外を握り潰して dispatch を続行しない**（fail-open 禁止）。L2 Policy Gate / L1 collision_monitor・Guardian / L0 clamp は一切迂回しない（[dev/08](../dev/08-xer6-live-sim-x-lite-runbook.md) §GO/No-Go）。

## 7. 依存 PR / issue（実装順序）

1. **#416**（`resolve_governed_calibration`・Draft）: §4 step6→§5 step4 の calibration 橋。**XER6 レーン着手前に merge するのが正順**（未 merge の場合、caller は None→refuse 変換を自前実装することになり同ロジックの二重レビューが発生）。
2. **#342 の ready 化**: #339/#340/#341 の DoD 監査（XER3-5 offline core land 済）→ close → `blocked` 解除が process 前提（[parallel-workflow.md §1](../../.claude/rules/parallel-workflow.md)）。
3. base.yaml additive PR（§3・bringup/skeleton 所有 Issue へ予告）。

## 8. テスト（3 層・R-26）

| 層 | 内容 | gate |
|---|---|---|
| ① offline（必須・CI） | `GeminiErAdapter(offline_payload=red_blue_sequence の envelope)` 注入（factory 非経由＝[gemini_er.py:9,18,180](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/adapters/gemini_er.py)・fixture 正本 [red_blue_sequence.py:23](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/fixtures/red_blue_sequence.py) `INNER_PLAN`）＋ **X-ER 用 run manifest fixture（新規作成・現存 fixture は Mode A probe のみ）**＋ `nav2_forwarder=None`。R-26 unit: 起動 fail-closed 各 step／plugin reject⇒0 dispatch／calibration 拒否⇒0 dispatch／gen 発番が ER 出力に非依存／t1→t2 進行 | なし（無料・決定論） |
| ② sim（XER6 受入=G5） | `Nav2RestForwarder` flip・Gazebo で red→blue ordered demo が MCP/Policy Gate/Nav2 Bridge を通る | human（docker） |
| ③ live ER | [dev/08 §7](../dev/08-xer6-live-sim-x-lite-runbook.md) の optional live leg | human・課金（`WAREHOUSE_LIVE_ER`・agent は立てない） |

## 9. 残件（本 doc の未決・隠さない）

- `camera_id`↔`calibration_id` 突合 semantics（#416 merge 後に §3 へ 1 行追記）。
- enabled box 集合（§4 step7 の `ConstructedBox` 列挙）＝X-ER run manifest fixture 作成時に確定。
- `out/runs/` の root `.gitignore` entry（[ADR-0003](../adr/0003-bridge-local-manifest-composition.md):63 follow-up・XER6 実装 PR に同梱可）。
- doc16 §9 ブランチ表に Mode X-ER 行が無い（現行は precedent＝`mwr-mode-x-er`/`feat/mode-x-er-*`。表追記は governance follow-up）。
- コード内 stale 注記の sweep（[07:71](07-implementation-status.md) 掲載分）＝XER6 実装 PR で同梱。

## References

- 判定履歴: [06-unfrozen-contract-resolutions §3＋追補](06-unfrozen-contract-resolutions.md) / 現状記録: [07-implementation-status](07-implementation-status.md)
- 設計正本: [01-architecture-and-flow](01-architecture-and-flow.md)（gen_id・L4 境界）/ [02-l3-planning-core](02-l3-planning-core.md)（三分割所有・store/executor seam）/ [03-er-adapter-skeleton](03-er-adapter-skeleton.md)（G0-G5）
- composition: [productization/09](../productization/09-run-manifest-and-plugin-composition.md) / [ADR-0003](../adr/0003-bridge-local-manifest-composition.md) / [ADR-0002](../adr/0002-er-in-hermes-standard.md)
- runbook: [dev/08-xer6-live-sim-x-lite-runbook](../dev/08-xer6-live-sim-x-lite-runbook.md) / [dev/07-mode-x-er-live-e2e-runbook](../dev/07-mode-x-er-live-e2e-runbook.md)
- 用語: [GLOSSARY](../GLOSSARY.md)（`x_er_bridge`・`mode_x_er:`・XER0–XER7・run manifest 群）

[#342]: https://github.com/lll-kkk-ryuya/miniature-warehouse-robotics/issues/342
