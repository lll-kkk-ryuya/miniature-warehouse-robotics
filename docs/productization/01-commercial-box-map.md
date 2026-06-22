# Commercial Box Map

作成日: 2026-06-22

> **状態**: 設計提案。ここでいう box は商用再利用の保管単位であり、現時点の ROS package 境界と完全一致するとは限らない。

## 全体 map

```
Operator / WMS / API / Voice
  -> L4 Robotics Bridge Super-Box
     -> Input Context Box
     -> Run Orchestration Box
     -> Hermes-managed Transport Box
     -> Model Adapter Box
        -> ER Adapter
        -> VLA Adapter
        -> STT Adapter
     -> Fusion Box
     -> L3 Handoff Box
     -> Trace / Audit
  -> L3 Robotics Planning Core Box
  -> Contract Box
  -> Governance Box
  -> Traffic Box
  -> Navigation Box
  -> Safety Box
  -> Hardware Box

Eval / Observability Box は全 box を横断して raw event / trace / KPI を集約する。
```

## Box 一覧

| Box | repo 内の主な実体 | 案件で差し替えるもの | 保管したい artifact |
|---|---|---|---|
| L4 Input Context Box（Super-Box 内 sub-box） | `warehouse_llm_bridge/situation.py`、Mode X-ER request skeleton | audio、STT、camera、state source、calibration id、site profile | context schema、fixture input、site adapter |
| L4 Robotics Bridge Super-Box | `warehouse_llm_bridge`、`hermes_client`、`llm_bridge`、`action_map`、tracing seam | provider、model routing、timeout、trace tag、run id、Hermes/direct adapter | bridge interface、adapter registry、audit policy |
| L4 Model Adapter Box | Mode X-ER `GeminiErAdapter` skeleton、Mode X-ER-VLA `VlaAdapter` candidate | Gemini ER、OpenVLA、STT、runtime、GPU/API、auth | provider adapter、offline fixture、raw output recorder |
| L4 Fusion Box | Mode X-ER-VLA Fusion Validator / Safety Compiler candidate | ER/VLA disagreement policy、confidence fusion、operator clarification | fusion policy、golden disagreement fixture |
| L3 Robotics Planning Core Box | Validator、Visual Resolver、Task Graph Executor、Command Compiler | site policy、calibration、task graph rule、compiler backend | rules, calibration replay, task fixture, compiler plugin |
| Contract Box | `warehouse_interfaces` | location、robot、schema、安全上限、store backend | frozen schema、migration note、contract tests |
| Governance Box | `warehouse_mcp_server`、Policy Gate、gen / idempotency stores | policy、権限、業務ルール、rate limit、audit sink | policy profile、reject reason catalog、accepted-motion gate |
| Traffic Box | `warehouse_traffic`、`warehouse_rmf_adapter` | X-lite / X-rmf、fleet adapter、route graph、traffic rule | traffic profile、RMF graph、fallback plan |
| Navigation Box | `warehouse_nav2_bridge`、Nav2 params、URDF/map | map、URDF、sensor、controller、localization | robot nav profile、map bundle、Nav2 params |
| Safety Box | `warehouse_safety`、collision_monitor、twist_mux | distance threshold、sensor source、stop topology | safety profile、R-26 tests、event catalog |
| Hardware Box | `firmware/`、micro-ROS Agent | motor driver、encoder、battery、MCU、client_key | board profile、driver shim、host safety tests |
| Eval / Observability Box | `eval_sdk`、`warehouse_orchestrator`、Langfuse score sink | KPI vocabulary、report、trace sink、customer report | metric manifest、run export、dashboard preset |

## Box 境界の原則

1. L4 は model / provider / trace / timeout を所有する。
2. L3 は model output を実行候補へ変換するが、実行許可はしない。
3. Governance は accepted motion だけを下流へ出す。
4. Traffic は複数台の調整を担当する。必要がなければ X-lite に縮退する。
5. Navigation は position goal を処理し、速度 policy を直接作らない。
6. Safety と Hardware は上位 model に依存しない。
7. Eval は domain-free core と domain KPI を分ける。

`action_map` は既存コード上 `warehouse_llm_bridge` にあり、L4 Robotics Bridge Super-Box と Governance の境界 seam である。商用 box としては L4 Robotics Bridge Super-Box が `Command` から tool call への変換を所有し、Governance Box は MCP / Policy Gate 側の検証と accepted-motion gate を所有する。

L4 Model Adapter Box と L4 Fusion Box は、商用保管上は L4 Robotics Bridge Super-Box の配下に置く。独立 repo / 独立 service として分離する場合でも、trace、timeout、audit、L3 handoff、secret 境界は Robotics Bridge の所有物として扱う。

## Robotics Bridge Super-Box 内の分担

Hermes Agent に寄せられるものと、robotics bridge が所有し続けるものを分ける。ここでは新しい config key、ROS topic、REST API、`warehouse_interfaces` contract は追加しない。

| Sub-Box | Hermes Agent で扱う範囲 | Bridge 側に残す範囲 |
|---|---|---|
| Model Transport Box | provider 切替、custom endpoint、provider fallback、OpenAI 互換 endpoint | robotics request id、cycle、timeout 後の 0 dispatch 判定 |
| STT Adapter Box | Local Whisper / API STT / custom command / plugin provider | transcript を state / image / calibration と束ねる Input Context |
| Basic Vision Transport Box | vision-capable model への image input、汎用 vision analysis | overhead camera id、calibration id、map frame、known location との対応 |
| MCP Tool Connection Box | stdio / HTTP MCP 接続、tool include/exclude、resources/prompts wrapper の制御 | motion tool の採用経路、Bridge mint の `idempotency_key`、accepted-motion gate |
| Plugin Extension Box | model provider、STT/TTS、custom tool、hook、MCP 連携の plugin 化 | robotics 固有の safety policy、L3 handoff contract、audit / Eval join |
| ER / VLA Adapter Box | transport が合う場合の Gemini / custom endpoint / plugin provider | ER/VLA input/output contract、raw output recorder、offline fixture |
| Fusion Box | なし。Hermes tool として置く場合も推論補助まで | disagreement policy、confidence fusion、operator clarification、Safety Compiler |
| L3 Handoff Box | なし | `RoboticsPlan draft` / `VlaGroundingReport` から L3 入力へ正規化 |
| Action Map Seam | なし | `Command` から MCP tool call への写像、`gen_id` / `idempotency_key` 注入 |

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
