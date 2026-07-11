# 08 x_er_bridge node 仕様（XER6/G5 の背骨・X-ER commander node 契約）

> **状態**: 設計正本（XER6 実装の前提 doc）。[07-implementation-status](07-implementation-status.md):55 `next_wire` が示す「背骨 1 本」＝新規 `warehouse_llm_bridge/x_er_bridge.py` の **node 契約**をここに確定する（07 は現状記録・本書が設計正本）。生成 2026-07-07・全 file:line は `origin/main cdb34e0`（現 main `eda2513` の祖先・差分は #416 の追加 2 ファイルのみ）で検証済み。#416 関連（§3 確定・§4 step6）は `eda2513` で追検証。
> **スコープ**: XER6（X-lite E2E・[#342]・[README](README.md):91）。live 手順は [dev/08-xer6-live-sim-x-lite-runbook](../dev/08-xer6-live-sim-x-lite-runbook.md)（operator 用）・[dev/07 live runbook](../dev/07-mode-x-er-live-e2e-runbook.md)（課金 gate）。X-rmf は対象外（`NotImplementedError` fail-closed・#346）。
> **凍結契約**: 本書は `warehouse_interfaces` を一切変更しない（bridge-local のみ）。

## 1. 位置づけ（何を閉じるか）

[07 §トップダウン連結](07-implementation-status.md):44-53 の connectivity hops のうち、**⓪（稼働 cycle）③（adapter→pipeline 受け渡し）④（L3 chain running 到達）⑤（Command→L2 dispatch・X-ER 経路）を 1 node で一括で閉じる**。Mode A の稼働 commander（`llm_bridge.py` → `scheduler.py` dispatch）と同型の常駐 rclpy node を X-ER 用に 1 本立てる。hop ⓪は doc07 上「稼働 cycle」と「mic → audio capture」の 2 要素が同居しており（[07:46](07-implementation-status.md)）、本 node が閉じるのは**稼働 cycle 部分のみ**＝**実マイク capture 部分は対象外**（別 follow-up）。hop ②（live-send）は機構 land 済（#389）で、本 node からは offline=fixture / live=env-gate 越しに呼ぶ。

## 2. node 契約（形）

| 項目 | 契約 | 根拠 |
|---|---|---|
| module | `warehouse_llm_bridge/x_er_bridge.py`（`main()` + `rclpy.Node`） | [07:55](07-implementation-status.md)・landed（#419・offline Slice A・main `949ae7f`） |
| console_scripts | `x_er_bridge = warehouse_llm_bridge.x_er_bridge:main`（`llm_bridge` と同型） | `setup.py` 既存形 |
| 起動 gate | `bringup.launch.py` が **`llm:=true` かつ `mode_x_er.enabled==true` のときのみ** x_er_bridge を追加（`traffic_mode=='x-er'` 値は**発明しない**＝[06 §3 追補](06-unfrozen-contract-resolutions.md)）。`llm` 既定 true ゆえ通常の overlay opt-in（`mode_x_er.enabled`）挙動は不変。**Slice B で `llm` を「司令 node 一切なし」の operator kill-switch へ一般化**（§2.1） | [06:29](06-unfrozen-contract-resolutions.md) RESOLVED・§2.1 |
| 相互排他 | `mode_x_er.enabled == true` の bringup は **Mode A commander（`llm_bridge`）を起動しない**（司令 node は常に 1 本＝gen 発番・排他制御 B-3 の一意 owner を保つ。どちらの commander かは `mode_x_er.enabled` が決める） | [01:184-197](01-architecture-and-flow.md)（gen_id は Bridge 発番）・[#342] DoD「#4 と mode 分岐を調整」 |
| 依存 | 凍結契約 `warehouse_interfaces`＋自 package 内（`robotics/`・`robotics_planning_core/`・`robotics/composition/`）のみ。他トラック内部 import なし | [parallel-workflow.md §2.1](../../.claude/rules/parallel-workflow.md) |
| actuation | node 自身は **0 actuation**。motion は既存 L2 経路（MCP tool → Policy Gate → Nav2 Bridge REST）のみ。L3 は R-26 reject で**空 Command・store 無接触** | [02:360](02-l3-planning-core.md)・`pipeline.py:108,171-175` |

### 2.1 起動 gate と operator kill-switch（Slice B 確定・`llm:=false` × `mode_x_er.enabled`）

Slice A は x_er_bridge を **`mode_x_er.enabled` の単独 gate**で起動し、`llm` launch arg（既定 true・「Start the LLM commander stack」）とは独立にした（`bringup.launch.py` の KNOWN residual＝「`llm:=false` でも x_er_bridge が起動しうる」）。Slice B で以下に**確定**する:

- **契約**: x_er_bridge は **`llm:=true` かつ `mode_x_er.enabled==true`** のときのみ起動する。`llm:=false`（＝nav2 / safety-only bring-up）は **Mode A・X-ER どちらの commander も起動しない**単一の operator kill-switch とする（`llm` の文書化された意味「commander stack を止める」を X-ER にも一般化）。どの commander を選ぶか（相互排他）は従来どおり `mode_x_er.enabled` が決める。
- **理由（2 点）**: ①`llm:=false` を「commander を一切立てない」と読める一貫した kill-switch にし、「commander が出るのに `llm:=false`」という驚きを消す。②x_er_bridge の Slice B actuation（`dispatch.forward_to_nav2: true`）は **nav2_bridge（REST :8645）**へ REST する（§5 step6）。その nav2_bridge は `llm:=true`∧`traffic_mode∈{none,simple}` で gate されるため（`bringup.launch.py` nav2_bridge Node 条件が第一義・allowlist は `llm_bridge.py:94` `NAV2_BRIDGE_MODES` を mirror）、commander と endpoint を **co-gate** しないと「commander は起動・endpoint は不在」の壊れ config を許してしまう。`llm` 既定 true ゆえ overlay で `mode_x_er.enabled` を立てる通常運用の挙動は不変。
- **委譲**: launch 側の実変更（x_er_bridge 条件に `and llm=='true'` を追加＝現行 `IfCondition(str(mode_x_er_enabled).lower())` の拡張）は**実装レーンが行う**（本 docs PR は契約のみ確定・`bringup.launch.py` の residual 注記が「doc08 amendment 後に条件変更」を明記）。

## 3. `mode_x_er:` config key（凍結形）

[06 §3](06-unfrozen-contract-resolutions.md):29,99-104 の DEFER を解除し（追補は 06 末尾）、以下を凍結する。**`traffic_mode` と直交**・完全 additive（`load_config` は未知 top-level key を deep-merge 素通し＝06:96）・`warehouse_interfaces` 不触:

```yaml
mode_x_er:                       # 新規 top-level（base は全て安全側 OFF/空）
  enabled: false                 # bringup が x_er_bridge を起動するか（既定 OFF・本 PR で凍結）
  execution_profile: x_lite      # x_lite | x_rmf（値は 01:203-204 由来。x_rmf は NotImplementedError）
  calibration_id: ""             # config/<env>/calibration/<id>.yaml の stem（06:105 の 5 field YAML）
  visual:                        # Visual Resolver 閾値（コード定数禁止＝02:98。値は env overlay で確定）
    snap_radius_m: 0.25          # 例示値（visual_resolver/policy.py:65 の code default と同値）。location_coords は config `locations`（doc13 §3.3・base.yaml 既存 block）から導出＝新規座標 key は発明しない
  run_manifest: ""               # run_manifest.v1 YAML への path（空＝composition 起動拒否＝fail-closed）
  plugin_manifests: []           # plugin.yaml path の list（run manifest 宣言と全一致要・§4）
  site_profile:                  # 安全クリティカル profile gate（§4 step6）の解決子
    base_dir: ""                 # site_profiles/ ルート（productization/04 §Site Profile）
    customer: ""
    site: ""
  # ── Slice B 追加凍結（2026-07-07・§9 残件①②解消。base ship は全て safe-OFF/空）──
  dispatch:                      # L2 forwarder 解決。Mode A（llm_bridge.py:160）と同型＝endpoint は既存 `nav2_bridge.base_url`（base.yaml 既存 block・doc15）を再利用し**新規 endpoint key を発明しない**
    forward_to_nav2: false       # 既定 safe-OFF＝`nav2_forwarder=None`（tools.py:92-115＝受理・記帳のみ・0 actuation＝Slice A 挙動）。true＝`Nav2RestForwarder(<nav2_bridge.base_url>)`（sim/real motion・MCP→Policy Gate→Nav2 Bridge REST）
  request_fixture: ""            # v0 dev-only（PROVISIONAL）＝`ErTaskRequest` JSON への path。空＝node は idle（request 源未設定）。実 mic/audio capture 配線＋`/nav2_bridge/goal_result`→`mark_succeeded` 完了信号が入る後続 slice で superseded。set-but-malformed は起動時 raise（fail-closed・x_er_bridge.py:80）
  # ── G5 準備 追加凍結（2026-07-11・無償 offline-replay 経路。base ship は空=無効）──
  er_offline_payload: ""         # dev/検証用: 録画済み ER transport envelope（JSON object）への path。空＝無効＝従来 live 経路。非空＝build_er_adapter が GeminiErAdapter(offline_payload=...) を構築（replay・provider call 不可能）。不在 path / malformed / 非 object は起動拒否（fail-closed）
```

- `enabled` / `run_manifest` / `plugin_manifests` / `site_profile.*` は XER6 docs（#418）での追加凍結（06 提案形は `execution_profile`/`calibration_id` の 2 key。manifest ingestion の**取得元未定義**＝[productization/09](../productization/09-run-manifest-and-plugin-composition.md):402-416 RESIDUAL をここで解消する）。
- **`dispatch.forward_to_nav2` / `request_fixture` は Slice B（本 PR）での追加凍結**（§9 残件①②の解消）。①`dispatch.forward_to_nav2`＝§5 step6 の offline(None)/sim(`Nav2RestForwarder`) flip を表す**単一 boolean**（`traffic_mode` は mode_x_er と直交＝§3 冒頭ゆえ Mode A の `traffic_mode∈{none,simple}` gate を再利用できず、base ship の `nav2_bridge.base_url` が非空ゆえ「空 base_url⇒None」でも表せない → 明示 boolean が最小の非発明形）。②`request_fixture`＝実装済み runtime-consumed key（x_er_bridge.py:161・未設定なら node は idle）ゆえ**未文書化契約を避けるため §3 に昇格**（廃止は代替入力源が無く不可＝v0 の唯一の入力）。
- calibration artifact は 06:105 のとおり **`config/<env>/calibration/<id>.yaml`**（`camera_id / map_frame / homography(3x3) / reprojection_error / valid_polygon`＝`02:149` 逐語 5 field・コード定数埋込禁止 `02:277`）。
- **base.yaml への実追加は docs PR ではしない**: `config/warehouse.base.yaml` は bringup/skeleton 所有 → 実装レーンが**所有 Issue へ予告 → 合意 → 末尾追記**の additive PR で行う（[06:110](06-unfrozen-contract-resolutions.md) contract PR 手順どおり）。Slice-A keys（`enabled`/`execution_profile`/`calibration_id`/`visual`/`run_manifest`/`plugin_manifests`/`site_profile`）は #419 で land 済（safe-OFF/空）。**Slice-B keys（`dispatch.forward_to_nav2: false`・`request_fixture: ""`）は実装レーンの base.yaml additive step で safe-OFF 既定で足す**（本 docs PR は形のみ凍結）。
- 確定（2026-07-07・#416 merge 済＝main `eda2513`）: governed 経路（§4 step6→§5 step4）は **`mode_x_er.calibration_id` をそのまま `camera_id`** として `resolve_governed_calibration(profile, camera_id=<calibration_id>)` に渡す（識別子は 1 本＝artifact file stem と同一。二重 ID を発明しない）。実装は witness 埋込のため loader 変種 `resolve_governed_calibration_with_loader`（calibration_source.py:116）を用いる（同一 fail-closed 契約）。
- **`er_offline_payload` は G5（#342）準備での追加凍結**（2026-07-11・本 PR）。semantics（安全姿勢を凍結する）:
  1. **dev/検証用途であり本番 live 経路の既定は不変**（base ship は空＝無効。live send の operator/cost gate `WAREHOUSE_LIVE_ER` も不変＝[dev/07 §4.5](../dev/07-mode-x-er-live-e2e-runbook.md)。**`WAREHOUSE_LIVE_ER` が不要になるのは replay 時のみ**＝replay adapter は HTTP sender を一切持たず provider call が構造的に不可能）。
  2. 非空なら `build_er_adapter`（adapter_factory.py）が path の JSON（録画済み transport envelope＝`RawModelOutput.payload` になる形。direct=`candidates[...]` / hermes=`choices[...]`・handoff は envelope の key 形で正規化）を読み `GeminiErAdapter(offline_payload=...)` を構築する。config replay は注入 sender より優先（無償側優先）。
  3. **fail-closed**: 存在しない path・malformed JSON・非 object・非 string 値はすべて起動拒否（§6 起動時と同族）。
  4. transport 監査タグは従来どおり `resolve_audio_transport` の解決値（observation-only・doc03:75。replay payload の実形と tag のズレは handoff 正規化に影響しない）。
  5. base.yaml への実追加は本 PR の実装 commit が additive 末尾追記で行う（bringup/skeleton 所有＝PR 本文で開示。上の Slice-A/B 前例と同手順）。G5 の operator 手順は [dev/08 追補 2](../dev/08-xer6-live-sim-x-lite-runbook.md)。

## 4. composition 起動シーケンス（fail-closed・起動時 1 回）

すべて **raise ⇒ 起動拒否 ⇒ 0 dispatch**（部分的に立ち上がらない）。順序と根拠 file:line（`robotics/composition/`）:

1. `load_run_manifest(mode_x_er.run_manifest)` — [loader.py:37](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/loader.py)。unknown `schema_version` 含め malformed は reject。
2. `load_plugin_manifests(...)` → `build_plugin_code_registry(run_manifest, plugin_manifests)` — [plugin_manifest.py:205,301](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/plugin_manifest.py)。run 宣言 plugin に manifest 無し＝raise。
3. `PluginDispatchPolicy.derive_from_base(base=PlanPolicy の emergency_stop allowlist, requested=...)` — [plugin_results.py:272](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/plugin_results.py)（narrow-only・base ceiling は #410）。
4. `PluginComposition(...)` を構築し、各 hookimpl を **manifest の `plugin_id` 名で** `register(impl, plugin_id)` — [plugins.py:123,150](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/plugins.py)。
5. `preflight_composition(manifest, composition)` — [preflight.py:57](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/preflight.py)（`==` 集合等価＝#410）。**reconciliation（step2）と preflight（step5）は別物**であり、`run-declared == registered hookimpls == plugin-manifest-present` の**三重突合を回すのは x_er_bridge の責務**（[plugin_manifest.py:22-27](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/plugin_manifest.py) が XER6 へ明示的に委譲）。
6. site profile gate: `load_site_profile(base_dir, customer, site)` → `compute_content_hash` → `load_approved_record` → `verify_against_approved(...).assert_verified()` → `build_calibration_loader(profile)` — [profile.py:161,287,197,320,148](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/profile.py)・[calibration_gate.py:291](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/calibration_gate.py)。**実 caller は §3:governed 経路の `resolve_governed_calibration_with_loader`（calibration_source.py:116）** を用いる（generic builder ではなく governance witness 埋込のため loader 変種・同一 fail-closed 契約）。
7. `build_effective_composition(...)` → `write_run_artifacts(...)`（`out/runs/<run_id>/`） — [record.py:139,242](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/record.py)。**enabled な全 box に `ConstructedBox` entry が必須**（in-process で構築しない box は `stage=None` の entry を明示列挙・[record.py:181-183](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/record.py) の missing raise）。
8. ER adapter 構築: `build_er_adapter(cfg)` — [adapter_factory.py:77](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/adapter_factory.py)（config→transport 解決・shipped 既定 DIRECT fail-safe＝[ADR-0002](../adr/0002-er-in-hermes-standard.md):43）。**offline test は factory を経由せず** `GeminiErAdapter(offline_payload=...)` を注入する（§8）。加えて `mode_x_er.er_offline_payload`（§3 G5 追加凍結）が非空のときは **factory 自身が replay 構築**（`GeminiErAdapter(offline_payload=...)`・sender 無し＝live 能力ゼロ）に切替わる（無償 G5 経路＝[dev/08 追補 2](../dev/08-xer6-live-sim-x-lite-runbook.md)）。

> **manifest は record であって config source ではない**（[composition/fixtures.py](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/fixtures.py):20-32 の F2/F4）。stage オブジェクトは caller（本 node）が warehouse config から自前構築し、step7 が「recorded==ran」を事後突合する。

## 5. cycle 設計（毎 cycle・fail-closed）

async 境界: `propose_plan` は async・L3/composition は sync のため、Mode A と同型の **background thread + 専用 event loop**（[llm_bridge.py:254-255,297](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/llm_bridge.py)）で回す。

1. **入力**: `ErTaskRequest`（[er_task.py:31](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/er_task.py)・`known_locations ⊆ KNOWN_LOCATIONS`）。v0 の入力源は fixture / 事前録音 ref（実マイク capture＝07 hop ⓪の mic 要素は対象外）。
2. **ER**: `raw = await adapter.propose_plan(req)`（offline=fixture 即答／live=`WAREHOUSE_LIVE_ER` gate 越し・**agent は gate を立てない**＝[dev/07 §4.5](../dev/07-mode-x-er-live-e2e-runbook.md)）。
3. **plugin 合成 validate（F1 判断＝二重 validate を採用）**: `validate_with_plugins(PlanValidator(), draft, ctx, composition)` — [plugins.py:409](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/plugins.py) を先に呼び、`ComposedValidationReport.permits_dispatch`（[plugins.py:370](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/plugins.py)）が false なら **その cycle は 0 dispatch・store 無接触で終了**。理由: L3 入口 `compile_raw_output` は `PlanValidator()` を内部構築し plugin composition を受けない（[pipeline.py](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/pipeline.py):171 相当・plugin 経路は別関数）＝**この guard 無しでは manifest 宣言 plugin が reject しても Command が出る**。代替案（resolve→execute→compile の tail 再組立）は pipeline 内部の複製＝drift 源として不採用。コスト＝core validator が cycle あたり 2 回走る（offline 決定論・許容）。
4. **L3**: `cmd: Command = compile_raw_output(raw, calibration=<§4 step6 の governed 経路>, resolver_policy=VisualPolicy(...), executor=<長命 executor>)` — [pipeline.py:90,98-99](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/pipeline.py)。executor は node が保持する **長命 `TaskGraphExecutor`**（[executor.py:70](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/task_graph_executor/executor.py)）を毎回同一注入（plan ごと・cycle ごと live handle は 1 つ＝[02:361](02-l3-planning-core.md) STALE-HANDLE 契約）。
5. **gen 発番**: node が `FileGenStore` で mint（[llm_bridge.py:152](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/llm_bridge.py) と同型・LLM/ER 由来値は使わない＝[01:184-197](01-architecture-and-flow.md)）。
6. **dispatch**: `command_to_tool_calls(cmd, gen)`（[action_map.py:92](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/action_map.py)）→ `DispatchToolExecutor`（[llm_bridge.py:238](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/llm_bridge.py) と同型）→ `WarehouseTools.dispatch`（[tools.py:124](../../ws/src/warehouse_mcp_server/warehouse_mcp_server/tools.py)）。**offline slice は `nav2_forwarder=None`**（[tools.py:92-115](../../ws/src/warehouse_mcp_server/warehouse_mcp_server/tools.py)＝受理・記帳のみ・0 actuation）→ sim の `Nav2RestForwarder`（`/api/v1/{navigate,wait,stop}`＝[nav2_client.py:45](../../ws/src/warehouse_mcp_server/warehouse_mcp_server/nav2_client.py)）への切替は **Slice B で `mode_x_er.dispatch.forward_to_nav2` による config flip**（§3・既定 `false`＝`None`＝Slice A 挙動／`true`＝`Nav2RestForwarder(<既存 `nav2_bridge.base_url`>)`）。endpoint は Mode A（[llm_bridge.py:160](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/llm_bridge.py)＝`Nav2RestForwarder(nav2_base_url) if traffic_mode∈{none,simple} else None`）と同一の `nav2_bridge.base_url` を再利用し**新規 endpoint key を発明しない**。Slice A 実装は `None` 固定＝本 key 未配線（node/launch 配線は §9 残件①解消の実装 PR。actuating 経路は nav2_bridge 稼働＝`llm:=true` を要する＝§2.1）。
7. **progression**: dispatch 受理後の lifecycle（`mark_running` → 完了確認 → `mark_succeeded`）は **caller loop＝本 node が所有**（[02:359](02-l3-planning-core.md) 三分割所有）。`compile_raw_output` は one-shot ready-tasks-only のため、**red→blue の順序 demo は t1 完了後に次 cycle で再呼び**して t2 を ready 化する（[dev/08](../dev/08-xer6-live-sim-x-lite-runbook.md) §4 手動手順の自動化）。完了信号（`/nav2_bridge/goal_result`・payload `{robot, task_id, result}`＝[doc03:110](../architecture/03-software-architecture.md)）→`mark_succeeded` の変換は本 node の実装範囲。**完了相関は robot 経由**で行う: navigate REST body は `{robot, destination|goal}` のみで dispatch/Policy-Gate task_id も plan node id も nav2 へ round-trip せず、nav2 は自前 `nav_NNN` を `goal_result.task_id` に載せる（[core.py:190,296](../../ws/src/warehouse_nav2_bridge/warehouse_nav2_bridge/core.py)）。本 node は 1 plan・after-gating で **robot あたり in-flight ≤1** のため robot で一意に相関できる（task_id 粒度の相関は不要）。richer な task_id 相関が要る場合は round-trip 契約の追加が別途必要＝§9 残件。

## 6. エラー方針（すべて安全側）

- **起動時**: §4 のどの step の raise でも node は cycle を開始しない（0 dispatch）。
- **cycle 中**: adapter 失敗（network/gate/parse）＝その cycle を skip・store 無接触。calibration 拒否＝`GovernedCalibrationUnavailableError`（#416 seam・None を黙って通さない）＝0 dispatch。plugin reject（§5 step3）＝0 dispatch・store 無接触。L3 reject（R-26）＝空 Command・store 無接触（[pipeline.py:108,171-175](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/pipeline.py)）。
- **例外を握り潰して dispatch を続行しない**（fail-open 禁止）。L2 Policy Gate / L1 collision_monitor・Guardian / L0 clamp は一切迂回しない（[dev/08](../dev/08-xer6-live-sim-x-lite-runbook.md) §GO/No-Go）。

## 7. 依存 PR / issue（実装順序）

1. **#416**（calibration 橋・§4 step6→§5 step4）: ✅ **merge 済**（2026-07-07・main `eda2513`）。実装が消費する実体は `resolve_governed_calibration_with_loader`（governance witness 埋込のため loader 変種を使用・挙動は同一 fail-closed）。
2. **#342 の ready 化**: ✅ **完了**（2026-07-07・#339/#340/#341 を DoD 監査で close → `blocked`→`ready` 反転済）。
3. base.yaml additive: Slice-A block（`enabled`/`execution_profile`/`calibration_id`/`visual`/`run_manifest`/`plugin_manifests`/`site_profile`）は ✅ **#419 で land 済**（`config/warehouse.base.yaml:104-115`・safe-OFF/空）。**Slice-B keys（`dispatch.forward_to_nav2`/`request_fixture`）の base.yaml 追加は実装レーンで pending**（§3・bringup/skeleton 所有 Issue へ予告 → additive 末尾追記）。

## 8. テスト（3 層・R-26）

| 層 | 内容 | gate |
|---|---|---|
| ① offline（必須・CI） | `GeminiErAdapter(offline_payload=red_blue_sequence の envelope)` 注入（factory 非経由＝[gemini_er.py:9,18,180](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/adapters/gemini_er.py)・fixture 正本 [red_blue_sequence.py:23](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/fixtures/red_blue_sequence.py) `INNER_PLAN`）＋ **X-ER 用 run manifest fixture（新規作成・現存 fixture は Mode A probe のみ）**＋ `nav2_forwarder=None`。R-26 unit: 起動 fail-closed 各 step／plugin reject⇒0 dispatch／calibration 拒否⇒0 dispatch／gen 発番が ER 出力に非依存／t1→t2 進行 | なし（無料・決定論） |
| ② sim（XER6 受入=G5） | `Nav2RestForwarder` flip・Gazebo で red→blue ordered demo が MCP/Policy Gate/Nav2 Bridge を通る | human（docker） |
| ③ live ER | [dev/08 §7](../dev/08-xer6-live-sim-x-lite-runbook.md) の optional live leg | human・課金（`WAREHOUSE_LIVE_ER`・agent は立てない） |

## 9. 残件（本 doc の未決・隠さない）

- ~~`camera_id`↔`calibration_id` 突合 semantics~~ → §3 で確定済（calibration_id ≡ camera_id・2026-07-07）。
- ~~**forwarder 解決 key**（§5 step6 の sim flip 用）が §3 凍結 set に無い~~ → **Slice B で確定**（2026-07-07）: §3 に `mode_x_er.dispatch.forward_to_nav2`（単一 boolean・既定 safe-OFF）を凍結、endpoint は既存 `nav2_bridge.base_url` を再利用（Mode A `llm_bridge.py:160` と同型・新規 endpoint key なし）。node/launch の実配線は実装レーンへ委譲。
- ~~**`mode_x_er.request_fixture`**（dev-only 暫定 key・§3 凍結 set 外）→「§3 昇格 or 廃止」を確定~~ → **Slice B で §3 へ昇格**（2026-07-07・v0 dev-only PROVISIONAL 注記付き）。runtime-consumed（x_er_bridge.py:161）ゆえ未文書化契約を避ける／廃止は代替入力源が無く不可。base ship は空（idle）。
- **gate 意味論（`llm:=true` co-gate）**＝§2.1 で契約確定（Slice B）。launch 条件 `and llm=='true'` の実追加は実装レーン（lead-review-419）へ委譲（doc は契約のみ・`bringup.launch.py` の residual 注記が「doc08 amendment 後に変更」を明記）。
- **完了相関の task_id round-trip 契約は未定義**（nav2 は自前 `nav_NNN` を発番・navigate に呼び出し側 task_id が入らない＝[core.py:190,296](../../ws/src/warehouse_nav2_bridge/warehouse_nav2_bridge/core.py)）。X-ER は robot 経由相関で十分（node 1 plan・in-flight ≤1・§5 step7）。multi-plan / task_id 粒度の相関が要るなら doc12a の navigate 契約へ task_id round-trip を additive 追加する follow-up（本 slice は不要）。
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
