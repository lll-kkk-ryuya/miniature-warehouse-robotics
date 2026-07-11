# Commercial Box Map

作成日: 2026-06-22

> **状態**: 設計提案。ここでいう box は商用再利用の保管単位であり、現時点の ROS package 境界と完全一致するとは限らない。ここでは新しい config key、ROS topic、REST API、`warehouse_interfaces` frozen contract は追加しない。

## 全体 map

```
[入力] Operator / WMS / API / Voice
══ L4 入力・知覚/オーケストレーション ══ Non-RT / external API ══════════
  -> L4 Robotics Bridge Super-Box
     -> Input Context Box                  [sub-box]
     -> Run Orchestration Box              [demoted → Super-Box cycle]
     -> Hermes-managed Transport Box       [demoted → Model Adapter transport]
     -> Model Adapter Box                  [sub-box ・transport: hermes|direct|worker]
        -> ER Adapter                      [provider_type:er ・optional]
        -> VLA Adapter                     [provider_type:vla ・optional]
        -> STT Adapter                     [provider_type:stt ・optional]
     -> Fusion Box                         [sub-box ・optional]
     -> L3 Handoff Box                     [seam → L3 Core 配下]
     -> Trace / Audit                      [demoted → trace root + Eval Box]
══ L3 司令・検証 ══ Non-RT / Python ════════════════════════════════════
  -> L3 Robotics Planning Core Box
══ L2 実行許可・交通管理 ══ Soft-RT / Python + optional RMF ═════════════
  -> Contract Box
  -> Governance Box
  -> Traffic Box
══ L1 自律走行・安全 ══ Hard-RT ════════════════════════════════════════
  -> Navigation Box
  -> Safety Box
══ L0 物理安全 ══ MCU / 即時 ═══════════════════════════════════════════
  -> Hardware Box

[横断] Eval / Observability Box は全 box（L4–L0）を横断して raw event / trace / KPI を集約する。
```

## Box 種別と分類規則（taxonomy の正本）

map の各ノードは「再利用保管単位の box」とは限らない。種別を次の規則で一貫判定する。**この節を box taxonomy の正本**とし、後続の表（Box 一覧 / Robotics Bridge Super-Box 内の分担）はこの種別に従って読む。

| 種別 | 定義 | 例 |
|---|---|---|
| **box** | 1つの安定した produces/consumes 契約（凍結 file:line＝`warehouse_interfaces` schemas/stores/paths/locations か doc03 topic に解決）を持ち、その契約を変えず**丸ごと入替/省略 or variant 縮退**できる再利用保管単位（04 §保管単位）。実体は ROS package と1:1とは限らない（本書冒頭の注記）。 | L4 Super-Box、L3 Planning Core、Contract、Governance、Traffic、Navigation、Safety、Hardware、Eval |
| **sub-box** | 親 box の produces/consumes の一部を担う内部ステージ。親 interface を越えて単独 consume されず、親の swap 境界を割らない。 | Input Context、Model Adapter、Fusion |
| **seam** | 2 box 境界の写像/変換。**独立した produces/consumes を持たず**（新 schema を産まない）、出力が隣接 box の入力契約そのもので、かつ常時随伴で入替/省略できない。所有 box 配下へ降格。複数 box 跨ぎは multi-owner seam。 | action_map、MCP dispatch、L3 Handoff |
| **plugin** | box の interface を変えず中身の実装差を吸収する差替点（Hermes 拡張機構 / L3 Core+Plugin の site/visual/compiler/store）。 | Provider Routing、L3 の各 Plugin |
| **demoted** | (a) Hermes 機能分類の表行で box=保管単位でない（Hermes-vs-direct は `transport: hermes|direct|worker` の裏＝箱を貫く線でなく実装選択）、(b) map のみで設計実体ゼロのノード、(c) seam を box 行に列挙したもの。所有 box の transport 属性 or 既存責務へ吸収。 | Run Orchestration、Trace/Audit、Hermes-managed Transport、Model Transport、API Server 等 |

原則:

- **transport override は box を割らない**: 同一 box の interface 裏で `hermes|direct|worker` を選ぶのは実装選択であって、機能を Hermes列/Bridge列に縦割りした表（本書 §Robotics Bridge Super-Box 内の分担 / `02` §Hermes に寄せる範囲）は **box 境界ではなく transport 実装ノート**として読む。
- **安全境界・lifecycle 単一所有は box の真ん中を貫けない**: `MAX_LINEAR_VELOCITY=0.3`（凍結契約 `warehouse_interfaces/safety.py:18`・`warehouse_safety` が消費）/ `gen_id`・`idempotency_key` mint（`warehouse_llm_bridge/action_map.py`＝Bridge mint・LLM 由来不可）/ `KNOWN_LOCATIONS` 9 キー（`warehouse_interfaces/locations.py`）/ B-3 stale→C replay（`GenChecker.check`）は上位 model 非依存（本書 §境界の原則6）。安全 gate を持つ box の `transport` は **n/a**（`hermes` と書くと motion gate が Hermes 線で貫かれる category error）。
- **観測 taxonomy は別レイヤ**: `decision_event` の `box=` 軸（`05`/`06`）・funnel・gate family（L4C/L4A/L4F/L3H/N-G/H-G/E-G）は**集計軸**であって保管単位でない。sub-box/seam へ降格しても `box=` literal と gate ID は据え置き、gate は所有 box の acceptance-gates へ帰属させる。

## Box 一覧

| Layer | Box | repo 内の主な実体 | 案件で差し替えるもの | 保管したい artifact |
|---|---|---|---|---|
| **L4** | L4 Input Context Box（Super-Box 内 sub-box） | `warehouse_llm_bridge/situation.py`、Mode X-ER request skeleton | audio、STT、camera、state source、calibration id、site profile | context schema、fixture input、site adapter |
| **L4** | L4 Robotics Bridge Super-Box（**親 box**・下記 sub-box/seam を配下に持つ・実装あり） | `warehouse_llm_bridge` 1:1（`situation.py`/`action_map.py`/`tracing.py`/`executor.py` の `DispatchToolExecutor`）。`action_map`・MCP dispatch は Governance への **seam** | provider、model routing、timeout、trace tag、run id、transport: hermes/direct/worker | bridge interface、adapter registry、audit policy |
| **L4** | L4 Model Adapter Box（Super-Box 内 **sub-box**・**未実装 skeleton/candidate＝proposal**） | Mode X-ER `GeminiErAdapter` skeleton、Mode X-ER-VLA `VlaAdapter` candidate（ws/src に実体なし）。ER/VLA/STT は `provider_type` registry エントリ・transport: hermes\|direct\|worker | Gemini ER、OpenVLA、STT、runtime、GPU/API、auth | provider adapter、offline fixture、raw output recorder |
| **L4** | L4 Fusion Box（Super-Box 内 **sub-box**・**optional**・**未実装 candidate＝proposal**） | Mode X-ER-VLA Fusion Validator / Safety Compiler candidate（単一 model では不使用） | ER/VLA disagreement policy、confidence fusion、operator clarification | fusion policy、golden disagreement fixture |
| **L3** | L3 Robotics Planning Core Box | Validator、Visual Resolver、Task Graph Executor、Command Compiler | site policy、calibration、task graph rule、compiler backend | rules, calibration replay, task fixture, compiler plugin |
| **L2** † | Contract Box | `warehouse_interfaces` | location、robot、schema、安全上限、store backend | frozen schema、migration note、contract tests |
| **L2** | Governance Box | `warehouse_mcp_server`、Policy Gate、gen / idempotency stores | policy、権限、業務ルール、rate limit、audit sink | policy profile、reject reason catalog、accepted-motion gate |
| **L2** | Traffic Box | `warehouse_traffic`、`warehouse_rmf_adapter` | X-lite / X-rmf、fleet adapter、route graph、traffic rule | traffic profile、RMF graph、fallback plan |
| **L1** | Navigation Box | `warehouse_nav2_bridge`、Nav2 params、URDF/map | map、URDF、sensor、controller、localization | robot nav profile、map bundle、Nav2 params |
| **L1** | Safety Box | `warehouse_safety`、collision_monitor、twist_mux | distance threshold、sensor source、stop topology | safety profile、R-26 tests、event catalog |
| **L0** | Hardware Box | `firmware/`、micro-ROS Agent | motor driver、encoder、battery、MCU、client_key | board profile、driver shim、host safety tests |
| **横断** | Eval / Observability Box | `eval_sdk`、`warehouse_orchestrator`、Langfuse score sink | KPI vocabulary、report、trace sink、customer report、decision event aggregation | metric manifest、decision event manifest、run export、dashboard preset |

> **Layer 凡例**（正本: [docs/mode-x-er/01-architecture-and-flow.md](../mode-x-er/01-architecture-and-flow.md) のレイヤ図）。レイヤ（L4–L0 の RT クラス）は §Box 種別と分類規則の**種別と直交する別軸**（box が「どの RT 層か」と「box/sub-box/seam のどれか」は独立）:
> - **L4** 入力・知覚/オーケストレーション（Non-RT / external API）— 音声・画像・state を束ね、ER/VLA を呼び、提案と trace を作る（直接 actuation しない）。
> - **L3** 司令・検証（Non-RT / Python）— 提案を検証・target 解決・依存管理し、既存 `Command` 候補へ変換する（実行許可は持たない）。
> - **L2** 実行許可・交通管理（Soft-RT / Python + optional RMF）— accepted motion だけを下流へ通し、X-lite / X-rmf を選ぶ。
> - **L1** 自律走行・安全（Hard-RT）— Nav2 の実走と collision_monitor / twist_mux / Emergency Guardian の物理停止。
> - **L0** 物理安全（MCU / 即時）— ESP32 firmware の速度 clamp ≤0.3 m/s と即時停止。
> - **横断** Eval / Observability は L4–L0 全 box を横断して trace / KPI を集約する。
> - † **Contract Box** は L2 の凍結契約ハブだが、`warehouse_interfaces` は L2–L0 が一方向に横断依存する（schema・安全上限・location の単一ソース）。

## Box 境界の原則

1. L4 は model / provider / trace / timeout を所有する。
2. L3 は model output を実行候補へ変換するが、実行許可はしない。
3. Governance は accepted motion だけを下流へ出す。ここが motion dispatch gate であり、L3 の command candidate が Traffic / Navigation / Safety / Hardware 側へ進む前の最後の L2 判定境界になる。
4. Traffic は複数台の調整を担当する。必要がなければ X-lite に縮退する。
5. Navigation は position goal を処理し、速度 policy を直接作らない。
6. Safety と Hardware は上位 model に依存しない。
7. Eval は `eval_sdk` の domain-free core と warehouse-specific KPI / report を分ける。

`action_map` は既存コード上 `warehouse_llm_bridge` にあり、L4 Robotics Bridge Super-Box と Governance の境界 seam である。商用 box としては L4 Robotics Bridge Super-Box が `Command` から tool call への変換を所有し、Governance Box は MCP / Policy Gate 側の検証と accepted-motion gate を所有する。

L4 Model Adapter Box と L4 Fusion Box は、商用保管上は L4 Robotics Bridge Super-Box の配下に置く。独立 repo / 独立 service として分離する場合でも、trace、timeout、audit、L3 handoff、secret 境界は Robotics Bridge の所有物として扱う。

## Robotics Bridge Super-Box の transport 実装ノートと seam（旧「内の分担」表）

> **読み方（重要）**: 下表は **box の縦割りではない**。各機能を「Hermes-managed＝`transport: hermes` の実装」と「Bridge-owned＝所有する box / seam の責務」へ分解した**実装ノート**である（§Box 種別と分類規則の「transport override は box を割らない」）。左列の名称は**確定種別**で読む（旧「Sub-Box」列名は誤解を招くため改めた）。ここでは新しい config key、ROS topic、REST API、`warehouse_interfaces` frozen contract は追加しない。

| 機能（確定種別） | Hermes-managed（`transport: hermes` 実装） | Bridge-owned（所有 box / seam の責務） |
|---|---|---|
| Model Transport（demoted → Model Adapter transport） | provider 切替、custom endpoint、provider fallback、OpenAI 互換 endpoint | robotics request id、cycle、timeout 後の 0 dispatch 判定（Super-Box） |
| STT（sub-box・Model Adapter `provider_type:stt`） | Local Whisper / API STT / custom command / plugin provider | transcript を state / image / calibration と束ねる Input Context |
| Basic Vision（demoted → Model Adapter transport + Input Context） | vision-capable model への image input、汎用 vision analysis | overhead camera id、calibration id、map frame、known location との対応（Input Context / L3 Visual Resolver） |
| MCP Tool Connection（seam → MCP dispatch / Governance） | stdio / HTTP MCP 接続、tool include/exclude、resources/prompts wrapper の制御 | motion tool の採用経路、Bridge mint の `idempotency_key`、accepted-motion gate（Governance） |
| Plugin Extension（plugin・Hermes 拡張機構） | model provider、STT/TTS、custom tool、hook、MCP 連携の plugin 化 | robotics 固有の safety policy、L3 handoff contract、audit / Eval join（Safety / L3 / Eval） |
| ER / VLA（sub-box・Model Adapter `provider_type:er/vla`・optional） | transport が合う場合の Gemini / custom endpoint / plugin provider | ER/VLA input/output contract、raw output recorder、offline fixture |
| Fusion（sub-box・optional・Hermes 列=なし） | なし。Hermes tool として置く場合も推論補助まで | disagreement policy、confidence fusion、operator clarification、Safety Compiler |
| L3 Handoff（seam → L3 Core 配下） | なし | `RoboticsPlan draft` / `VlaGroundingReport` から L3 入力へ正規化 |
| action_map（seam → Governance） | なし | `Command` から MCP tool call への写像、`gen_id` / `idempotency_key` 注入（Bridge mint・LLM 由来不可） |

Hermes Agent の server-side tool execution は、read-only status tool や operator-facing workflow には使える。ただし robot motion の採用経路では、Bridge が final model output を受けて L3 / Governance へ渡す。これにより、Hermes 側 transport を使っても、motion 実行は L3 Planning Core、MCP / Policy Gate、Traffic、Navigation、Safety、Hardware の順に通る。

## 商用案件での組み合わせ例

### 小規模 2台搬送 PoC

```
L4 Robotics Bridge Super-Box
  -> L3 Planning Core
  -> Governance
  -> X-lite Traffic
  -> Nav2 Bridge
  -> Safety
  -> ESP32
  -> Eval
```

Open-RMF は使わず、known location と Nav2 Bridge で完結させる。

### 複数台 fleet 案件

```
L4 Robotics Bridge Super-Box
  -> L3 Planning Core
  -> Governance
  -> X-rmf Traffic
  -> Open-RMF Fleet Adapter
  -> Nav2
  -> Safety
  -> robot hardware
  -> Eval
```

Fleet Adapter と RMF graph を site ごとに差し替える。

### ドッキング / 把持を含む案件

```
L4 ER Adapter
  + L4 VLA Adapter
  -> Fusion Box
  -> L3 Planning Core
  -> Governance
  -> Navigation + Manipulation compiler
  -> Safety / Hardware
```

移動だけでは VLA を必須にしない。把持、配置、ドッキング、近接位置合わせなど、Nav2 だけでは表現できない subtask で VLA box を追加する。

## 未凍結事項

- Productization 用の実ディレクトリ名。
- `RoboticsPlan` / `ValidationReport` を product contract に昇格するか。
- site profile のファイル形式。
- commercial report の KPI schema。
- ER/VLA adapter を Hermes 経由に固定するか、Bridge-managed direct adapter を許可するか。
- **Mode A manifest 表現で判明した taxonomy gap（run_manifest.v1 で実測・[productization/09](09-run-manifest-and-plugin-composition.md)）**:
  - **character_llm の box home 不在（F1）**: negotiation 層（persona / negotiation / character_node・[architecture/14](../architecture/14-character-llm-negotiation.md)）は L4 Super-Box 実装だが doc01 sub-box でも doc09 catalog plugin でもない（free-form L4 plugin id で表現は可・plugin manifest 未整備）。
  - **profile ↔ traffic_mode 翻訳 owner 未定義（F2）**: box profile 文字列が `traffic_mode`（`none|simple`）を命名規約で運ぶが、それを config key へ翻訳する component が未定義（manifest は現状 record であって config source ではない・[doc09:44](09-run-manifest-and-plugin-composition.md)）。
  - **State Cache の box 帰属（F3）**: `warehouse_state/state_cache`（稼働 Mode A node）が doc01 の box 行に無い（暫定 `safety` box に subsume＝taxonomy 判断は未確定）。
  - **mode 自体が first-class でない（F4）**: 「これは Mode A」が profile 命名にしか encode されない。first-class な `mode`/`scenario` field は v2 議論（now-scope では blocker でない）。

## レイヤ annotation 対応表（L0–L4 → 責務 → package / 主要ファイル）

追記日: 2026-07-11

> **用途**: 回答・plan・実装・PR/Issue 本文・レビューで「対象コード/設計が **どの layer に属するか**」を明記する（`.claude/rules/layer-annotation.md` の参照先）ための**正準対応表**。レイヤの意味の正本は §Box 一覧 直下の Layer 凡例と [mode-x-er/01-architecture-and-flow.md:22-78](../mode-x-er/01-architecture-and-flow.md) のレイヤ図（用語集は [GLOSSARY §3](../GLOSSARY.md)）、box 単位の割付は §Box 一覧。本節は box taxonomy・責務定義を**変更せず**、annotation に必要な **file-level anchor** を足すだけ。他の「層」体系（安全レイヤー 4 層・時間 3 層）との読み替えは [11 §レイヤ番号の対応](11-l2-contract-governance-traffic-box.md) を正とし、本表の番号は商用 box map（L0–L4）体系。

| Layer | 責務（凡例の要約） | package / 主要ファイル（repo 実体） |
|---|---|---|
| **L4** 入力・知覚/オーケストレーション | 音声・画像・state を束ね、model（LLM/ER）を呼び、提案と trace を作る。直接 actuation しない | `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/`: `llm_bridge.py`（Mode A/C commander node）・`x_er_bridge.py`（X-ER commander node・契約正本 [mode-x-er/08](../mode-x-er/08-x-er-bridge-node-spec.md)）・`robotics/`（ER adapter / transport / composition）・`hermes_client.py`・`situation.py`・`executor.py`・`action_map.py`（→L2 への seam・`gen_id`/`idempotency_key` は Bridge mint） |
| **L3** 司令・検証（Planning Core） | model 提案を検証・target 解決・依存管理し、既存 `Command` 候補へ変換する。実行許可は持たない | `warehouse_llm_bridge/robotics_planning_core/`: `handoff.py`・`validator/validator.py`・`visual_resolver/resolver.py`・`task_graph_executor/executor.py`・`command_compiler/compiler.py`・`pipeline.py`（plugin 合成は `warehouse_llm_bridge/robotics/composition/`） |
| **L2** 実行許可・交通管理 | accepted motion だけを下流へ通す（Governance）＋複数台の交通調整（Traffic）＋凍結契約ハブ（Contract †） | Governance: `ws/src/warehouse_mcp_server/`（`policy_gate.py`・`tools.py`・`gen_check.py`・`audit.py`）／ Traffic: `ws/src/warehouse_traffic/`（`traffic_manager.py`・`virtual_scan*.py`）＋ `ws/src/warehouse_rmf_adapter/`（必要がなければ X-lite に縮退＝§Box 境界の原則 4・保管詳細 [11 §Box の保管場所](11-l2-contract-governance-traffic-box.md)）／ Contract †: `ws/src/warehouse_interfaces/`（L2–L0 が一方向横断依存） |
| **L1** 自律走行・安全 | Nav2 の実走と物理停止（collision_monitor / twist_mux / Emergency Guardian） | Navigation: `ws/src/warehouse_nav2_bridge/`（REST→Nav2 action）・`ws/src/warehouse_bringup/config/`（`nav2_params.yaml`・`collision_monitor.yaml`・`twist_mux.yaml`）／ Safety: `ws/src/warehouse_safety/`（`emergency_guardian.py`・`guard_logic.py`） |
| **L0** 物理安全 | ESP32 firmware の速度 clamp ≤0.3 m/s・近接/非常停止（MCU / 即時） | `firmware/src/main.cpp`・`firmware/include/safety_clamp.h`・`firmware/include/kinematics.h`（micro-ROS Agent 経由） |
| **横断** Eval / Observability（観測面） | 全 box（L4–L0）を横断して raw event / trace / KPI を集約する（motion 権限なし） | `ws/src/eval_sdk/`・`ws/src/warehouse_orchestrator/`・`ws/src/warehouse_web_bridge/`（observe-only consumer・[architecture/22](../architecture/22-web-observability.md)） |

annotation の注意（layer ≠ process / layer ≠ package）:

- **layer ≠ process**: L3 Planning Core のコードは L4 commander node（`llm_bridge.py` / `x_er_bridge.py` の Bridge プロセス）**の中で**動くが、レイヤ帰属は L3 のまま（実行許可・actuation 権限なし）。「L3 Validator / Resolver ＝ L4 と同一プロセス」の正本記述は [mode-x-er/05-operator-feedback-and-voice-response.md:179](../mode-x-er/05-operator-feedback-and-voice-response.md)。例: 「`executor.py`（L3 task_graph_executor・actuation 権限なし）」。
- **layer ≠ package**: `warehouse_llm_bridge` package は L4（node / adapter / composition）と L3（`robotics_planning_core/`）の両方を含む。annotation は package 名でなく本表の行（file 単位）で行う。同名 `executor.py` が L4（`warehouse_llm_bridge/executor.py`＝MCP dispatch）と L3（`robotics_planning_core/task_graph_executor/executor.py`）に併存する点に注意。
- **帰属未定の component は未定と書く**: State Cache（`ws/src/warehouse_state/`）は box 帰属未定（F3・§未凍結事項・暫定 `safety` box に subsume）。annotation では「State Cache（box 帰属未定 F3・暫定 Safety）」と書き、断定しない。
- **起動・記述・sim 系 package（`warehouse_bringup` / `warehouse_description` / `warehouse_sim` / `warehouse_teleop`）は単一 layer に帰属させない**（複数レイヤの起動/記述/開発基盤）。annotation は「扱っている config / launch がどの layer の component か」で書く（例: `collision_monitor.yaml`＝L1 Safety の config）。
- **他体系の番号を裸で書かない**: 安全レイヤー 4 層（Layer 0–3・[../architecture/12-infrastructure-common.md](../architecture/12-infrastructure-common.md)）・時間 3 層とは軸が異なる。読み替えは [11 §レイヤ番号の対応](11-l2-contract-governance-traffic-box.md) を正とする。
