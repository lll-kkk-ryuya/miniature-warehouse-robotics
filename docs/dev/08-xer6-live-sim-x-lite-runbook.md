# 08 — Mode X-ER XER6 live X-lite sim runbook（plan → frozen Command → Nav2 の赤箱/青箱デモ）

> **位置づけ**: これは「**どう動かすか**（運用手順）」の operator runbook であり、`docs/dev/02-operator-runbook.md`（汎用オペ手順）／ `docs/dev/07-mode-x-er-live-e2e-runbook.md`（live ER→L3 の gateway 起動）の **下流** = 「L3 が出した frozen `Command` を既存 L2 実行経路（action_map → MCP → Policy Gate → Nav2 Bridge REST → Nav2）に流して sim の2台を動かす」までを扱う XER6（X-lite）専用版。
> 設計の正本は [`docs/mode-x-er/01-architecture-and-flow.md`](../mode-x-er/01-architecture-and-flow.md)（L2 以降の実行経路＝`docs/mode-x-er/01-architecture-and-flow.md:184-206`）と [`docs/architecture/03-software-architecture.md`](../architecture/03-software-architecture.md)（トピック契約）。Docker/Nav2 の bring-up 正本は [`deploy/dev/run-mode-a-live.sh`](../../deploy/dev/run-mode-a-live.sh) と [`docs/architecture/17-development-workflow.md`](../architecture/17-development-workflow.md) §4。安全境界の正本は [`.claude/rules/safety.md`](../../.claude/rules/safety.md) / [`docs/architecture/12-infrastructure-common.md`](../architecture/12-infrastructure-common.md)。本書は **新しい契約・しきい値・トピックを発明しない**（docs-first）。
>
> **これは HUMAN-GATED**（オペレーターの Docker/Nav2 マシン）。sim スタック起動は課金しないが、live ER leg を混ぜる場合は provider call が課金される（§7）。本書の主線（§2〜§5）は **offline で組んだ frozen `Command` を sim に流す**ところに集中し、live ER は §7 の任意 leg として分ける。

---

## 0. 何を証明するデモか（期待値を正直に揃える）

「人の順序付き指示 → ER 認識 → L3 変換 → 既存 Nav2 で2台が順番に動く」のうち、**XER6 が繋ぐのは L3 の frozen `Command` から先**（L2 → Nav2）である。上流（ER 実走）は §7 の任意 leg。

| leg | 何を証明するか | 状態 | gate |
|---|---|---|---|
| **A. plan → frozen `Command`（offline）** | `compile_raw_output` が accept 済 plan を frozen `warehouse_interfaces.schemas.Command` にする（`pipeline.py:90-187`） | **DONE・main(208eb76) マージ済（#381）**。unit `tests/unit/test_l3_pipeline.py:197-317` | autonomous（network 無し） |
| **B. frozen `Command` → Nav2（live sim）** | `Command` を action_map → MCP → Policy Gate → Nav2 Bridge REST に流して sim の bot1/bot2 が動く（`docs/mode-x-er/01-architecture-and-flow.md:188-197`） | **EXISTS**（Mode-A live スタックが同経路を回す。`run-mode-a-live.sh:288`） | human-gate（オペレーターの Docker/Nav2） |
| **C. live ER → plan（課金）** | 実 `gemini-robotics-er` を呼んで raw plan を得る | **EXISTS・env-gated**。§7 で任意接続 | human-gate（課金 provider call・doc07 §3） |

> **一本線（音声 → ER → Langfuse → Nav2）はまだ一本に繋がっていない**。本書は「B（frozen `Command` → Nav2）」を主線化し、A は offline で組み、C は任意で前置きする。ER leg の Bridge-side tracer（`observability.py` `LangfuseTranscriptTracer.record_transcript`）は fail-open span を発火する配線が入った（Lane A / PR #382）が、**live 実 trace の着地検証は human gate #88** のまま（doc07 §5 honest limit 2 = `docs/dev/07-mode-x-er-live-e2e-runbook.md:189`）——観測の live 検証は本 runbook の scope 外。

---

## 1. L3 → L2 の seam（frozen `Command` はどこで L2 に入るか）

正本フロー（`docs/mode-x-er/01-architecture-and-flow.md:66-71,184-197`）:

```
Command candidate
  -> action_map                    Bridge が gen_id + idempotency_key を注入
  -> ToolCall(gen_id, idempotency_key)
  -> Warehouse MCP Server
  -> Policy Gate                   stale / duplicate / battery / emergency / location を拒否
  -> accepted motion only          -> Nav2 Bridge REST (POST /api/v1/navigate|wait|stop)
  -> Nav2 (/bot1, /bot2)
```

- **XER6 の産物 = frozen `Command`**（`compile_raw_output`、`pipeline.py:97,105-106`）。この関数は「**No actuation happens here — the ``Command`` is handed to the downstream Bridge -> MCP -> Policy Gate path**」と明記する（`pipeline.py:105-106`）。つまり XER6 は **actuation しない**。動かすのは既存 L2。
- L2 の入口 = **`action_map`**（`ws/src/warehouse_llm_bridge/warehouse_llm_bridge/action_map.py`）。`command_to_tool_calls(command, gen_id)`（`action_map.py:92-98`）が各 `CommandItem` を MCP `ToolCall` に落とす。`navigate` → `dispatch_task(robot, dropoff[, via])`（`action_map.py:46-55`）。`gen_id` と per-call `idempotency_key`（UUID）は **Bridge 側が注入**し、LLM 出力は信用しない（`action_map.py:5-9`、`docs/mode-x-er/01-architecture-and-flow.md:197`）。
- 出口 = **Nav2 Bridge REST**。`POST /api/v1/navigate`（`ws/src/warehouse_nav2_bridge/warehouse_nav2_bridge/core.py:168`）が named `destination` を Nav2 goal に解決し、Mode A/B では **Warehouse MCP Server 内部が `/bot{n}/goal_pose` を発行**する（`docs/architecture/03-software-architecture.md:97`）。fire-and-forget＝即 `accepted`、完了は poll で返る（`core.py:177-178`）。
- **X-lite に固定**: `compile_raw_output` の `profile` 既定は `ExecutionProfile.X_LITE`（`pipeline.py:97`）。`x_rmf` は `WarehouseNavCompiler` が `NotImplementedError`（`pipeline.py:138,160-161`、`docs/mode-x-er/01-architecture-and-flow.md:199-206`）。RMF 経路（Open-RMF Task API）は本 runbook の対象外。

---

## 2. Docker / Nav2 スタックの bring-up（オペレーターの1コマンド）

正本は [`deploy/dev/run-mode-a-live.sh`](../../deploy/dev/run-mode-a-live.sh)。手作業で `ros2 launch` しない（[`.claude/rules/environments.md`](../../.claude/rules/environments.md) §「dev live Hermes / LLM Bridge 起動」）。

### Step A. sim cockpit（Gazebo + Nav2）を立てる

XER6 sim は Hermes 司令官 LLM を要しない（plan は §3 で offline に組む）。まず Nav2 込みの container を用意する:

```bash
deploy/dev/run-sim-cockpit.sh        # mwr-sim:jazzy を build（初回のみ）→ noVNC URL を表示
```

- 名前付き永続 container `mwr-sim`、repo を `/ws` に rw マウント、noVNC は `127.0.0.1:6080`（`run-sim-cockpit.sh:20-29`）。ws の build（`ws/build` / `ws/install`）を host から再利用するため毎回の colcon rebuild は不要（`run-sim-cockpit.sh:10-13`）。
- base image `tiryoh/ros2-desktop-vnc:jazzy` は **Nav2 を含まない**。2-bot E2E に必要な Nav2 / twist_mux / SLAM を container に足すのは [`deploy/dev/install-nav2-e2e.sh`](../../deploy/dev/install-nav2-e2e.sh)（`install-nav2-e2e.sh:1-8,17-24`。base 8.3GB の上に数百 MB）:

```bash
docker exec mwr-sim bash /ws/deploy/dev/install-nav2-e2e.sh
```

### Step B. full スタック（sim + Nav2 + Bridge + Nav2 Bridge）を起動

Mode-A live launcher が同じ L2 経路（action_map → MCP → Policy Gate → Nav2 Bridge → Nav2）を起こす。XER6 デモではこのスタックを **traffic_mode=none** で使う（X-lite は単純2台制御、`docs/mode-x-er/01-architecture-and-flow.md:203`）:

```bash
# Hermes 司令官を使わない XER6 sim では llm レーンは任意。
# Nav2 + Nav2 Bridge + MCP を確実に起こす最小形:
TRAFFIC_MODE=none SCENARIO=default \
  deploy/dev/run-mode-a-live.sh --no-restart
```

- 内部 launch = `ros2 launch warehouse_bringup bringup.launch.py sim:=true llm:=true traffic_mode:=none rviz:=true scenario:=default ...`（`run-mode-a-live.sh:288`）。`sim:=true` が Gazebo を含め、`llm:=true` が `llm_bridge + nav2_bridge` を起こす（`bringup.launch.py:162-173`）。`traffic_mode` 既定は `config/warehouse.base.yaml`（`bringup.launch.py:136-141`）。
- noVNC は既定 `127.0.0.1:6082`（`run-mode-a-live.sh:21`）。ブラウザで開き、RViz/Gazebo で2台（`/bot1`, `/bot2`）の Nav2 が上がっていることを確認する。
- **secret はファイルから agent に読ませない**。launcher は `config/<env>/.env` を `docker exec --env` で渡し、値を印字しない（`run-mode-a-live.sh:11-12`）。Hermes を使わない XER6 では provider key 不要だが、Bridge↔MCP の `API_SERVER_KEY` 経路は Mode-A と共通のため §7 の secret 規約に従う。

> **deploy 正本**: worktree と clone の使い分け・環境昇格は [`docs/architecture/17-development-workflow.md`](../architecture/17-development-workflow.md) §4（`:73` 「worktree 実行ランブック」／`:75` §4.0）。prod（実機）投入は Emergency Guardian / 0.3 m/s テスト通過後のみ（`.claude/rules/environments.md` §「prod の扱い」）——本 runbook は **dev sim 限定**。

---

## 3. plan を frozen `Command` に落とす（offline・network 無し）

`compile_raw_output` に ER の raw envelope（direct=Gemini `generateContent` / hermes=OpenAI 互換、どちらでも同一 `Command`）を渡すと frozen `Command` が返る（`pipeline.py:90-187`）。canonical fixture は **赤箱/青箱**（`docs/mode-x-er/01-architecture-and-flow.md:134-151`）:

- 指示: 「bot1は赤い箱へ。到達したらbot2は青い箱へ」（`docs/mode-x-er/01-architecture-and-flow.md:139`）。
- detections: `red_box` pixel [420,310] / `blue_box` pixel [810,280]（`docs/mode-x-er/01-architecture-and-flow.md:142-143`）。
- task_graph: `t1`(bot1→red_box) / `t2`(bot2→blue_box, `after t1.completed`)（`docs/mode-x-er/01-architecture-and-flow.md:146-147`）。

Visual Resolver が pixel を KNOWN_LOCATION に snap する（red_box→`shelf_1`、blue_box→`shelf_2`、`tests/unit/test_l3_chain.py:12-20,60-73`）。site 依存の homography / location 座標 / snap 閾値は **caller が注入**し、code 定数に埋めない（`pipeline.py:127-134`、`docs/mode-x-er/01-architecture-and-flow.md` の未凍結注記 `:5`）。

offline で `Command` を確認する（sim 起動不要・課金なし）:

```bash
/Users/kawaguchiryuya/Developer/miniature-warehouse-robotics/.venv/bin/python \
  -m pytest tests/unit/test_l3_pipeline.py -q
```

- accept 経路: **1台目だけが ready**（one-shot）。`compile_raw_output` は `bot1 → NAVIGATE → shelf_1` を出し、`t2`(bot2) は `after t1` ゲートで **この cycle には出さない**（`test_l3_pipeline.py:197-208,242-249`、`pipeline.py:112-114`）。
- `destination` は必ず `KNOWN_LOCATIONS` の要素（`test_l3_pipeline.py:208`、`schemas.py:157-162`）。座標 goal / velocity / model 昇格は **やらない**（`pipeline.py:22-23`）。
- **R-26 0-dispatch を end-to-end で固定**: 非 accept な `ValidationReport`（unknown robot / emergency / needs_clarification）は **空 `Command`** を返し、resolve/compile に一切進まない（`test_l3_pipeline.py:252-298`、`pipeline.py:172-179`）。forbidden envelope は Handoff で `ValueError`（`test_l3_pipeline.py:301-307`、`pipeline.py:169`）。

> **順序付き移動（赤→青）の実現**: `compile_raw_output` は「今 ready なタスクだけ」をコンパイルする one-shot（`pipeline.py:112-114`）。bot1 が `shelf_1` 到達＝`t1.completed` 後に **次 cycle で再度**呼び、`t2`(bot2→`shelf_2`) を出す。cycle 間の running/completed 進行は caller のループ（live 経路）の責務で、この offline entry は状態を持たない（`pipeline.py:112-114`）。デモでは「bot1 到達を確認 → 2回目の `Command` を投入」を人手で行う（§4 手順）。

---

## 4. 赤箱/青箱の順序付きデモ（frozen `Command` → Nav2）

§2 でスタックが上がり、§3 で `Command`（1回目 = bot1→`shelf_1`）が得られている前提。frozen `Command` を L2 に投入して2台を順に動かす。

### 手順（順序を守る＝X-lite の本質）

1. **cycle 1（bot1 → 赤箱=`shelf_1`）**: §3 で得た `Command`（`bot1 NAVIGATE shelf_1`）を Bridge の action_map に渡す＝`command_to_tool_calls(command, gen_id)`（`action_map.py:92`）→ `dispatch_task(robot="bot1", dropoff="shelf_1", gen_id, idempotency_key)`（`action_map.py:46-55`）→ MCP → Policy Gate → `POST /api/v1/navigate`（`core.py:168`）。RViz で bot1 が `shelf_1` へ向かうのを確認。
2. **到達待ち（`t1.completed`）**: bot1 の nav 完了を Nav2 Bridge status で確認（`GET /api/v1/status/{robot}`、`core.py:244`）。fire-and-forget ゆえ完了は poll で返る（`core.py:177-178`）。
3. **cycle 2（bot2 → 青箱=`shelf_2`）**: 同じ raw plan を再度 `compile_raw_output` に流す。`t1` 完了を caller ループが state に反映した上で呼べば `t2`(bot2→`shelf_2`) が ready になる（`pipeline.py:112-114`）。得た `Command` を同経路（action_map → … → `/api/v1/navigate`）で投入。RViz で bot2 が `shelf_2` へ向かうのを確認。

> **順序保証はどこにあるか**: task_graph の `after t1.completed`（`docs/mode-x-er/01-architecture-and-flow.md:147`）を **Task Graph Executor**（XER4）が保持し、ready task だけを compile する（`pipeline.py:11,112-114`）。「bot1 が着く前に bot2 が動く」ことは L3 が構造的に防ぐ——L2/Nav2 側で順序を作らない。

### scaffold（steps を印字するだけ・実 sim は起こさない）

本 runbook の手順を1画面で確認する thin scaffold を用意した（**実行はしない・preflight を印字するだけ**）:

```bash
deploy/dev/run-xer6-sim.sh           # 各 step と preflight チェック項目を印字（no sim run）
```

- この scaffold は **sim も pytest も provider call も走らせない**。`docker` / venv / repo の存在を確認し、§2〜§5 の実コマンドを表示するだけ（実行はオペレーターが行う）。live ER・課金・実 Nav2 dispatch は含めない。

---

## 5. 安全境界の GO/No-Go（迂回していないことを確認）

XER6 は **安全機構を一切迂回しない**。以下が生きていることを毎回確認する（`docs/mode-x-er/01-architecture-and-flow.md:58-82` の L2/L1/L0 スタック）。

| 層 | 機構 | GO 条件 | 根拠 |
|---|---|---|---|
| **L3** | 0-dispatch（R-26） | 非 accept plan は空 `Command`＝dispatch しない | `pipeline.py:172-179`、`test_l3_pipeline.py:252-298` |
| **L2** | Policy Gate | stale / duplicate / battery / emergency / location を拒否。`destination` は `KNOWN_LOCATIONS` のみ | `docs/mode-x-er/01-architecture-and-flow.md:61`、`schemas.py:157-162` |
| **L2** | action_map の gen_id / idempotency_key | Bridge が注入（LLM 出力を信用しない）。replay は MCP で idempotent reject | `action_map.py:5-9,43` |
| **L1** | collision_monitor / twist_mux / Emergency Guardian | 上流の cmd_vel より優先して停止できる | `docs/mode-x-er/01-architecture-and-flow.md:73`、`docs/architecture/12-infrastructure-common.md` |
| **L0** | ESP32 firmware Layer-0 クランプ | `clampLinear <= 0.3 m/s` / proximity stop / 非有限 `cmd_vel`→stop | `docs/mode-x-er/01-architecture-and-flow.md:81`、`.claude/rules/safety.md:4`、R-26 unit `firmware/test/test_clamp`（`docs/architecture/16-repository-and-conventions.md:221`） |

- **速度上限 = 0.3 m/s**（ミニチュアスケール最終防衛線）。これは **firmware Layer-0 が強制**（`.claude/rules/safety.md:4`、`docs/mode-x-er/01-architecture-and-flow.md:81`）＝上流（L3/L2）が壊れても 0.3 m/s を超えない。R-26 で host unit 化済（`docs/architecture/20-dev-quality-and-testing.md:35`、`firmware-safety` CI job `docs/architecture/20-dev-quality-and-testing.md:48`）。
- **No-Go（デモを止める）**: ①`Command` に `KNOWN_LOCATIONS` 外の destination（schema が `ValueError`、`schemas.py:160-161`）②Policy Gate が accept しない（stale/battery/emergency）③L0 クランプ / collision_monitor が起動していない ④sim で 0.3 m/s 超の cmd_vel が観測される（＝L0 迂回のサイン）。いずれも **actuation 前に止める**（fail-closed）。
- **sim は迂回ではない**: dev sim は実機ではないが、同じ Nav2 / twist_mux / collision_monitor スタックを回す（`install-nav2-e2e.sh:17-24`）。prod（実機）投入は別ゲート（Emergency Guardian / 0.3 m/s テスト通過後のみ、`.claude/rules/environments.md` §「prod の扱い」）。

---

## 6. Go/No-Go 転記表（human gate・デモのたびに埋める）

オペレーターが実走のたびに手で埋める（値・secret は書かない）。

| # | チェック項目 | 期待 | 実測 | GO/No-Go |
|---|---|---|---|---|
| 1 | sim cockpit + Nav2 起動（noVNC で `/bot1` `/bot2` 可視） | 2台の Nav2 が active | | |
| 2 | offline `Command`（cycle1）= `bot1 NAVIGATE shelf_1`・1件のみ | `len(commands)==1`, dest ∈ KNOWN_LOCATIONS | | |
| 3 | cycle1 dispatch → bot1 が `shelf_1` へ移動 | RViz で bot1 到達 | | |
| 4 | `t1.completed` 確認（`GET /api/v1/status/bot1`） | nav 完了 | | |
| 5 | offline `Command`（cycle2）= `bot2 NAVIGATE shelf_2` | bot2 が ready になる | | |
| 6 | cycle2 dispatch → bot2 が `shelf_2` へ移動 | RViz で bot2 到達 | | |
| 7 | 順序: bot2 は bot1 到達**後**にのみ動いた | 順序保持（`after t1`） | | |
| 8 | 安全: sim の cmd_vel が全 leg で ≤ 0.3 m/s | L0 迂回なし | | |
| 9 | 安全: 非 accept plan（emergency/unknown robot）で空 `Command`＝0 dispatch | R-26 保持 | | |
| 10 | 安全: `KNOWN_LOCATIONS` 外 destination が schema で reject | fail-closed | | |

**総合判定**: 1〜10 がすべて GO のときのみ「XER6 X-lite sim デモ = GREEN」と宣言する。1つでも No-Go なら **actuation を止め**、原因（該当 §5 行）を残す。

---

## 7. live ER leg を前置きする（任意・課金・human-gate）

§3 は offline fixture で `Command` を組むが、**実 ER で raw plan を取得**したい場合は doc07 の live 経路を前置きする。**これは有料 provider call**（doc07 §3）。

```bash
# 恒久プロビジョン済みなら（doc07 §4.5）:
deploy/dev/run-live-er-smoke.sh --check   # 安全: 鍵存在 + gate banner のみ・provider call なし
deploy/dev/run-live-er-smoke.sh           # 課金: tests/live/test_er_handoff_live.py を実走
```

- live ER は **`RoboticsPlanDraft`（handoff）で止まる**（doc07 §T-LIVE ER→Handoff、`test_er_handoff_live.py`）。その draft を offline で `compile_raw_output` に流せば §3 と同じ frozen `Command` まで到達できる（network 不要）。**「live で Validator/Compiler まで一本」は依然 XER6 のループ側の仕事**（doc07 §5-1）。
- gate / scoped 承認文言 / secret 非表示は **doc07 §3〜§4 が正本**（`.claude/rules/llm-observability-testing.md` / `.claude/rules/environments.md`）。本 runbook は sim tail を追加するだけで、live gate を緩めない。CI（autonomous）に `WAREHOUSE_LIVE_ER` を入れない（doc07 §4.5 境界）。

---

## 8. Honest limits（隠さない・デモ前に必ず読む）

1. **`compile_raw_output` は actuation しない**。frozen `Command` を返すだけで、Nav2 dispatch は L2（action_map → MCP → Policy Gate → Nav2 Bridge）の仕事（`pipeline.py:105-106`）。本 runbook はその L2 投入を **オペレーター手順**として記述する——production の自動ループ（cycle 間 state 進行）は未配線（`pipeline.py:112-114`）。
2. **順序（赤→青）は2 cycle の手動投入**で実現する。`compile_raw_output` は one-shot（ready task のみ）ゆえ、bot1 到達後に再度呼ぶ必要がある（§3 末尾）。連続自動化は caller ループの責務で本 runbook の scope 外。
3. **観測（Langfuse）の live 着地は human gate**。ER leg の Bridge-side tracer は fail-open span を発火する配線済（Lane A / PR #382、`observability.py` `LangfuseTranscriptTracer.record_transcript`）だが、**live 実 trace の着地検証は #88 gate**（doc07 §5 honest limit 2 = `docs/dev/07-mode-x-er-live-e2e-runbook.md:189`）。デモの観測 live 検証は本 runbook では扱わない。
4. **座標 goal / velocity / RMF は対象外**。`compile_raw_output` は KNOWN_LOCATION named destination のみを出し（`pipeline.py:22-23`）、`x_rmf` は `NotImplementedError`（`pipeline.py:138,160-161`）。座標 variant は Nav2 Bridge 側にあるが（`core.py:168-175`）、XER6 X-lite では使わない。
5. **これは dev sim**。実機 prod は別ゲート（Emergency Guardian / 0.3 m/s テスト通過後、`.claude/rules/environments.md` §「prod の扱い」）。sim GREEN は実機 GO を意味しない。

---

## 参照（たどれる file:line を一次ソースに）

- L3 → frozen `Command`: `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/pipeline.py:90-187`（`compile_raw_output`・main #381）/ unit `tests/unit/test_l3_pipeline.py:197-317`（compile）/ `tests/unit/test_l3_chain.py:12-20`（red/blue fixture）
- L2 実行経路: `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/action_map.py:46-55,92-98`（action_map → ToolCall）/ `ws/src/warehouse_nav2_bridge/warehouse_nav2_bridge/core.py:168,199-200,232,244`（Nav2 Bridge REST）
- フロー正本: [`docs/mode-x-er/01-architecture-and-flow.md`](../mode-x-er/01-architecture-and-flow.md):58-82（L2〜L0 スタック）/ :134-151（赤箱/青箱 fixture）/ :184-206（L2 以降・X-lite/X-rmf）
- トピック契約: [`docs/architecture/03-software-architecture.md`](../architecture/03-software-architecture.md):97（`/bot{n}/goal_pose` = Mode A/B は MCP 内部発行）
- bring-up: `deploy/dev/run-mode-a-live.sh:288`（launch）/ `deploy/dev/run-sim-cockpit.sh:20-29`（cockpit）/ `deploy/dev/install-nav2-e2e.sh:17-24`（Nav2 provision）/ `ws/src/warehouse_bringup/launch/bringup.launch.py:136-173`（launch args）
- 安全: [`.claude/rules/safety.md`](../../.claude/rules/safety.md):4（≤0.3 m/s）/ [`docs/architecture/16-repository-and-conventions.md`](../architecture/16-repository-and-conventions.md):221（R-26 Layer-0 unit）/ [`docs/architecture/20-dev-quality-and-testing.md`](../architecture/20-dev-quality-and-testing.md):35,48（安全 unit / `firmware-safety` CI）
- deploy / 環境: [`docs/architecture/17-development-workflow.md`](../architecture/17-development-workflow.md):73-75（§4）/ [`.claude/rules/environments.md`](../../.claude/rules/environments.md)（dev live / prod gate）
- 上流 live ER: [`docs/dev/07-mode-x-er-live-e2e-runbook.md`](07-mode-x-er-live-e2e-runbook.md) §3〜§4.5 / `deploy/dev/run-live-er-smoke.sh`

---

## 追補 — G5 live 前提条件（live-matrix ラウンド由来・2026-07-08）

live ER を前置きした full G5（#342）には、offline X-lite（§3〜§6）に無い前提が2つある。live-matrix ハーネス（`spike/xer6-live-matrix/run-live-matrix.sh`＋`REPORT.md`＝branch `feat/mode-x-er-live-matrix`）が live で実測した:

1. **State Cache（10Hz）が dispatch と並行して稼働していること**。Policy Gate の鮮度上限は既定 `UNAVAILABLE_AFTER_S = 2.0`（`ws/src/warehouse_mcp_server/warehouse_mcp_server/policy_gate.py`）だが、live ER 1 サイクルは 4–6s（median 4.68s）。ER 呼び出し**前**に取った `StateSnapshot` は dispatch 時点で必ず失効し `robot_unavailable` で reject される（batch2 実測）。G5/本番は `warehouse_state` の 10Hz State Cache writer（[`docs/architecture/12-infrastructure-common.md`](../architecture/12-infrastructure-common.md)）を並行更新し、state が ER 呼び出しを跨がないことが dispatch 成立の前提。
2. **detection が camera 由来であること**。画像無しの text-only live ER はモデルが pixel を発明し、Visual Resolver の snap（既定 `_DEFAULT_SNAP_RADIUS_M = 0.25`、`ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/visual_resolver/policy.py`）にまず解決せず、accepted でも空 `Command`（fail-closed が正しく作動）。実運用は camera detections が前提。ハーネスは暫定 `--pixel-hints` で full-chain を閉じ、image 添付 ER call（`overhead_image_ref`）での自然 resolve は follow-up。

> 本ラウンドは **RUNNING node ではなく harness が node と同一の backbone 関数列を駆動**した結果（OFFLINE-WIRED≠RUNNING）。稼働 rclpy node での G5 sim ゲートは #342 で継続。live 一本線の位置づけは [`docs/dev/07-mode-x-er-live-e2e-runbook.md`](07-mode-x-er-live-e2e-runbook.md) の live-matrix 追補。

---

## 追補 2 — G5 無償 RUNNING-node offline-replay 経路（2026-07-11・#342 準備）

§2〜§6 は「offline で組んだ frozen `Command` を**人手で** L2 に投入する」手順、追補 1 は live ER を前置きした full G5 の前提だった。G5（#342）の受入は本来 **稼働 rclpy node（`x_er_bridge`）が自走で red→blue を回す**ことだが、従来この node の plan 供給源は実 paid ER 呼び出しのみ（`build_er_adapter` は live sender を構築し、実 send は `WAREHOUSE_LIVE_ER=1` 課金 gate 越し）で、**RUNNING-node 受入を無償で回す経路が無かった**。それを `mode_x_er.er_offline_payload`（[docs/mode-x-er/08 §3 G5 追加凍結](../mode-x-er/08-x-er-bridge-node-spec.md)）で埋める: 非空なら factory が録画済み ER envelope を replay する `GeminiErAdapter(offline_payload=...)` を構築（**HTTP sender を持たない＝provider call が構造的に不可能・課金ゼロ・`WAREHOUSE_LIVE_ER` 不要**）。live gate 自体は不変（[dev/07 §4.5](07-mode-x-er-live-e2e-runbook.md)）。

### G5 実行 artifact（repo commit 済・`deploy/dev/xer6/`）

| ファイル | 役割 |
|---|---|
| `er_request.red_blue.json` | `ErTaskRequest` fixture（§3 の canonical 赤箱/青箱指示の転写。`mode_x_er.request_fixture` に指す） |
| `er_offline_payload.direct.json` | 録画済み ER 応答 envelope（`robotics_planning_core/fixtures/red_blue_sequence.py` `direct_envelope()` と同一形。`mode_x_er.er_offline_payload` に指す） |
| `run_manifest.yaml` | plugin-less な `run_manifest.v1`（`production_plugin_factories()` は今日空＝plugin 宣言があると起動拒否のため、G5 は zero-plugin baseline＝`spike/xer6-live-matrix/manifests/variant_a.yaml` 踏襲） |
| `site_profiles/customer_a/site_01/` | `APPROVED.yaml` 付き site profile bundle（doc mode-x-er/08 §4 step6 gate 用。**dev-sim 専用承認**であり実 site 承認ではない） |
| `warehouse.dev-overlay.example.yaml` | `config/dev/warehouse.yaml` へ operator が手動 merge する `mode_x_er` overlay の例（path は container 内 `/ws/...`） |

CI 側の裏取り: `tests/unit/test_xer6_g5_replay_artifacts.py` が **commit 済 artifacts そのもの**で node cycle 相当（red→blue・goal_result 駆動）を `WAREHOUSE_LIVE_ER` 無しで完走することを固定する（artifact が腐ればユニットが赤くなる）。

### 手順（無償・provider call ゼロ・human-gated は sim 起動のみ）

1. **overlay を手動 merge**: `deploy/dev/xer6/warehouse.dev-overlay.example.yaml` の `mode_x_er` block を `config/dev/warehouse.yaml` に足す。`enabled: true` は **Mode A commander（`llm_bridge`）と相互排他の意識的 flip**（doc mode-x-er/08 §2）なので commit しない（G5 実走のときだけローカルで立てる）。
2. **bring-up は §2 と同一**（`run-sim-cockpit.sh` → `install-nav2-e2e.sh` → `TRAFFIC_MODE=none deploy/dev/run-mode-a-live.sh --no-restart`）。`mode_x_er.enabled: true` の bringup は `x_er_bridge` を compose し `llm_bridge` を起動しない。State Cache（10Hz）は同 stack 内で稼働（追補 1 の前提 1 は replay では ER レイテンシ 0 のため更に緩いが、Policy Gate 鮮度窓 2.0s 自体は不変＝state writer 必須）。
3. **起動ログ確認**: `x_er_bridge ready (... er_source=offline_replay, request_source=fixture ...)`。`er_source=live` のままなら overlay が読まれていない（No-Go）。
4. **node が自走で red→blue**: cycle 1 = `bot1 NAVIGATE shelf_1` → Nav2 完了 → `/nav2_bridge/goal_result` → node が `mark_succeeded` → cycle 2 = `bot2 NAVIGATE shelf_2`（§4 の手動 2-cycle 投入は不要。doc mode-x-er/08 §5 step7）。
5. **actuation の 2 段階**: まず `dispatch.forward_to_nav2: false`（既定）で dry run（受理・記帳のみ 0 actuation・tools.py:92-115）→ ログ/audit を確認してから `true` に flip して実 sim motion（MCP → Policy Gate → Nav2 Bridge REST → Nav2）。安全境界は §5 / Go/No-Go 転記は §6 の表をそのまま使う（項目 3/6 の dispatch 主体が「人手」から「node」に変わるだけ）。

### 安全・cost 姿勢（不変条件）

- **replay adapter は live 能力ゼロ**（sender 無し）。`WAREHOUSE_LIVE_ER` は立てない・不要（課金 gate は不変。paid live ER leg は従来どおり §7 の optional・human-gate）。
- **fail-closed**: `er_offline_payload` の不在 path / malformed JSON / 非 object は起動拒否（0 cycle・0 dispatch。doc mode-x-er/08 §6）。
- 安全機構（L3 R-26 / L2 Policy Gate / L1 / L0）は一切迂回しない（§5 の表がそのまま適用）。

---

## 追補 3 — G5 本番デモ振り付け v2（t1–t5 完了依存 DAG・2026-07-11 裁定適用）

追補 2 までの赤箱/青箱 2 タスク形（t1/t2）は **機構検証ベースラインとしてそのまま残す**（v1 kit・CI oracle とも不変）。本追補は #342 G5 の**本番デモ振り付け v2**＝2 台・5 タスクの完了依存 DAG を確定する。規範は [doc02「クロスロボット依存トリガーの語彙（2026-07-11 裁定）」](../mode-x-er/02-l3-planning-core.md)（`docs/mode-x-er/02-l3-planning-core.md:364-408`・v1 規範 `:383-391`・早見表 `:397-404`）であり、本追補はその適用（運転仕様）。

### 振り付け（t1–t5）

| task | robot | 内容 | target（detections 内の id） | 到達先（snap） | after |
|---|---|---|---|---|---|
| t1 | bot1 | 赤箱へ | `red_box` pixel [420,310] | `shelf_1` | —（初回 cycle で dispatch） |
| t2 | bot1 | 帰還 | `berth_A_marker` pixel [420,1060] | `berth_A` | `t1.completed` |
| t3 | bot2 | 青箱へ | `blue_box` pixel [810,280] | `shelf_2` | —（t1 と並走） |
| t4 | bot2 | 赤箱地点へ | `red_box`（t1 と同一 detection を再利用） | `shelf_1` | **`t2.completed`** |
| t5 | bot2 | 帰還 | `berth_B_marker` pixel [810,1060] | `berth_B` | `t4.completed` |

- **依存の根拠（doc02 裁定）**: `after` が表すのは**先行タスクの完了のみ**（`ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/task_graph_executor/executor.py:137-146` `_dependencies_met`・「完了」= `succeeded` のみ `.../task_graph_executor/states.py:46`）。オペレーター指示「bot1 が赤箱の場所から**離れたら** bot2 が入る」は空間述語であり現行語彙では表現できない → **t4 を t2（bot1 の帰還タスク）の完了に `after` させる v1 近似**で実現する（doc02 v1 規範 `docs/mode-x-er/02-l3-planning-core.md:383-391`）。t3（bot2 自身の先行タスク）の完了では t4 は解放されない＝依存は robot ではなく task に付く。t2 が `failed` なら t4/t5 は永遠に pending（fail-closed・`executor.py:179-196`）。
- **帰還先の写像（契約変更なし）**: ユーザー指示の「指定の箇所／所定の位置」は **start berth（`berth_A` / `berth_B`）に写像**する（`ws/src/warehouse_sim/warehouse_sim/layout.py:39-40` `SPAWN_LOCATIONS` = bot1→berth_A / bot2→berth_B と一致）。両名は凍結 `KNOWN_LOCATIONS` に既存（`ws/src/warehouse_interfaces/warehouse_interfaces/locations.py:11-23`）＝ **locations への追加は不要・`warehouse_interfaces` 非編集**。
- **帰還 navigate の実現形（honest）**: x_lite MVP compiler は **`ResolutionResult` に居る resolved visual target のみ** compile する（`ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/command_compiler/compiler.py:103-134`。detections に無い target は known-location 名でも skip = `compiler.py:123`。Validator は known-location 直 target を許す `.../validator/validator.py:219-240` が、その compile は x_lite MVP 未対応＝residual として明示）。よって帰還 navigate は canned envelope の **detections（berth marker pixel）経由**で resolver snap する: committed calibration（`deploy/dev/xer6/site_profiles/customer_a/site_01/calibration.json` の homography）の逆写像で berth_A(0.2, 0.8)→pixel [420,1060] / berth_B(0.7, 0.8)→[810,1060]（整数で厳密）。

### G5 v2 で実際に効く安全網（正確な列挙・過大に言わない）

1. **完了依存の構造保証（L3）**: bot2→shelf_1 の dispatch は t2（bot1 帰還）完了**後**にしか生成されない（executor の after gate・上記）。失敗は後続を解放しない（fail-closed）。
2. **L2 Policy Gate `duplicate_destination`**（`ws/src/warehouse_mcp_server/warehouse_mcp_server/policy_gate.py:222-235`）: **別 robot が現在向かっている** destination への dispatch を拒否。予約は robot ごとの**最新 dispatch 先**（`policy_gate.py:400-409` の上書き。タスク完了ではエントリは消えず、次の dispatch が上書きする）。v2 の t4（shelf_1 再訪）は、t4 dispatch 時点で bot1 の予約が t2 により `berth_A` へ移っているため**誤発火しない**——並走で 2 台が同一地点へ収束する dispatch は従来どおり拒否される。両方向とも CI unit（`tests/unit/test_xer6_g5_choreography_v2.py`）で実挙動を固定済み。
3. **L1 反射層 + L0**: collision_monitor / twist_mux / Emergency Guardian ＋ firmware ≤0.3 m/s クランプは §5 の表のまま常在（v1 と同一・迂回しない）。
4. **非アクティブなもの（正直に）**: 本デモは `TRAFFIC_MODE=none`（§2）ゆえ **traffic 層の隘路排他ロック（Mode A/B の ≥0.15m 最小分離を作る機構）は動いていない**（出所の正確な整理は `docs/mode-x-er/02-l3-planning-core.md:391`。L2 Policy Gate に 0.15m 距離チェックは存在しない）。metric な最小分離の最終防衛は L1/L0 側。
5. **rate limit 注記**: Policy Gate は robot あたり 0.5s の rate limit を持つ（`policy_gate.py:134,210-219`）。同一 robot の連続 dispatch（t3→t4→t5）は 0.5s 以上空く必要があるが、実 sim は nav 完了が十秒オーダーで自然に満たす。offline CI は fake clock を進めて再現する。

### v2 実行 artifact（repo commit 済・`deploy/dev/xer6/`・v1 kit は不変）

| ファイル | 役割 |
|---|---|
| `er_request.choreography_v2.json` | v2 指示文の `ErTaskRequest` fixture（`mode_x_er.request_fixture` に指す） |
| `er_offline_payload.choreography_v2.json` | t1–t5（after 付き）の録画済み ER envelope（`mode_x_er.er_offline_payload` に指す。形式は v1 `direct_envelope()`（`robotics_planning_core/fixtures/red_blue_sequence.py`）と同一の Gemini `generateContent` 形＝新形式を発明しない） |
| `run_manifest.yaml` | **v1 と共用**（run manifest は plan 内容を持たない zero-plugin baseline のため、v2 専用は作らない） |
| `warehouse.dev-overlay.example.yaml` | v1↔v2 の切替＝`request_fixture` / `er_offline_payload` の **2 key の path 差し替えのみ**（同ファイル内コメントに v2 例を記載） |

手順・安全境界・Go/No-Go は追補 2 / §5 / §6 をそのまま使う（自走 cycle が 2→最大 5 dispatch に増えるだけ。§6 の表の項目 2〜7 は t1–t5 の順序（t1/t3 並走 → t2 → t4 → t5）に読み替える）。CI oracle は `tests/unit/test_xer6_g5_choreography_v2.py`（committed v2 artifacts を実 factory で駆動し、並走 dispatch・t4 の解放エッジ（t3 完了では出ず t2 完了で出る）・t2 失敗時 fail-closed・duplicate_destination 相互作用・robot あたり in-flight ≤1 を pin）。
