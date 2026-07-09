# L2 Contract / Governance / Traffic Box（実行許可・交通管理）

作成日: 2026-07-07

> **状態**: 設計整理（**稼働実装の商用 box 化整理**。[02](02-l4-robotics-bridge-box.md) / [03](03-l3-planning-core-box.md) の「設計提案」と異なり、L2 の 3 box は実装・テスト済みの稼働コードが主対象）。ここでは新しい config key、ROS topic、REST API、`warehouse_interfaces` frozen contract は追加しない。図解: [layer-l2-detail.html](layer-l2-detail.html)。

## 目的

L2 は、L3 が作った command 候補に**実行許可を与える最後の判定境界**と、複数台の**交通調整**を担う層である（[01-commercial-box-map.md:25,76](01-commercial-box-map.md)）。box は 3 つ。

```
L3 Command candidate
  -> action_map（L4 所有 seam・gen_id / idempotency_key を Bridge が注入）
  -> Governance Box（gen -> idempotency -> Policy Gate -> accepted-motion gate）
  -> Traffic Box（traffic_mode: none | simple | open-rmf ／ profile: X-lite | X-rmf）
  -> L1 Navigation Box（position goal のみ渡す）
      Contract Box = warehouse_interfaces（L2–L0 が一方向依存する凍結契約ハブ †）
```

Governance が accept しなければ **0 actuation**。L2 の出口は position goal だけであり、`cmd_vel`・velocity・trajectory は生成しない（[01-commercial-box-map.md:86-88](01-commercial-box-map.md) 境界の原則 3・5）。

## 結論（2026-07-07）

- **L2 = Contract † / Governance / Traffic の 3 box** を [01-commercial-box-map.md:65-67](01-commercial-box-map.md) のまま採用し、本書を L2 層の商用 box 設計の正本とする。† は「L2 の凍結契約ハブだが L2–L0 が一方向に横断依存する」の意（[01:80](01-commercial-box-map.md)）。
- **本書のレイヤ番号は商用 box map（L0–L4）体系**（[01:73-80](01-commercial-box-map.md)）を使う。安全レイヤー 4 層・時間 3 層（[../architecture/12-infrastructure-common.md](../architecture/12-infrastructure-common.md)）とは**軸が異なる**ため、§レイヤ番号の対応 の読み替え表を正とし、本書内で他体系の番号を裸で書かない。
- **L2 の中心不変条件 = accepted-motion gate**: motion tool の結果が `status=="ok"` かつ forwarder 配線時（Mode A/B。X-ER は config gate `mode_x_er.dispatch.forward_to_nav2`・§Governance）のみ Nav2 Bridge REST へ forward する（`warehouse_mcp_server/tools.py` の `_maybe_forward`・symbol 参照）。Mode C は forwarder=None で **0 actuation**（R-26 unit `tests/unit/test_modec_noactuation.py` で固定）。
- **Traffic の Mode C（open-rmf）live 経路は R-38 memory gate（#187）待ちのまま**とし、本書は新しい判断をしない（[../mode-c/11c-traffic-mode-c.md:437-467](../mode-c/11c-traffic-mode-c.md)）。offline core（location 解決・single-writer）は実装済み。
- Acceptance gate family として **L2-G0〜G8** を新設する（§Acceptance Gates）。L4-G / L3-G / N-G / H-G / E-G（[02:268-277](02-l4-robotics-bridge-box.md) / [03:207-217](03-l3-planning-core-box.md) / [08](08-navigation-hardware-eval-gates.md)）と衝突しない未予約帯。`reason_code` / gate は **proposal catalog であり product contract ではない**（[README.md §読み方](README.md) と同じ免責）。

## レイヤ番号の対応（商用 L0–L4 / 安全レイヤー 4 層 / 時間 3 層）

リポジトリには目的の異なる 3 つの「層」体系が併存する。**本書は常に商用 box map（L0–L4）で番号を書く**。他体系を引用するときは体系名を添える。

| 対象 | 商用 box map（本書・[01:73-80](01-commercial-box-map.md)） | 安全レイヤー 4 層（[12:72-93](../architecture/12-infrastructure-common.md)） | 時間 3 層（[12:43-49](../architecture/12-infrastructure-common.md)） |
|---|---|---|---|
| ESP32 firmware（速度 clamp・近接停止） | **L0** Hardware Box | Layer 0 | 即時（µs–ms） |
| Emergency Guardian | **L1** Safety Box | Layer 1 | Hard-RT（50ms 目標） |
| Nav2 / collision_monitor / twist_mux | **L1** Navigation / Safety Box | （Layer 1 と連携する物理停止トポロジ） | Hard-RT（Nav2 制御 50ms） |
| 交通管理（Open-RMF / SimpleTraffic / none） | **L2** Traffic Box | Layer 2（交通管理のみ・[12:86-88,110](../architecture/12-infrastructure-common.md)） | Soft-RT（event-driven） |
| Warehouse MCP Server / Policy Gate | **L2** Governance Box | Layer 3 側に併記（早わかり表・[12:101](../architecture/12-infrastructure-common.md)） | Non-RT（数十 ms・[12:481](../architecture/12-infrastructure-common.md)） |
| State Cache | box 帰属未定（F3・[01:171](01-commercial-box-map.md)・暫定 Safety） | 安全レイヤー外（[12:110](../architecture/12-infrastructure-common.md)） | Soft-RT（100ms 書出） |
| LLM Bridge / Hermes Gateway | **L4** Robotics Bridge Super-Box | Layer 3 | Non-RT（Mode A 3s / Mode C 5s） |

読み替えの注意:

- **矛盾ではなく軸違い**。doc12 自身が「時間は 3 層・安全は 4 層で軸が異なる」と明記する（[12:107-108](../architecture/12-infrastructure-common.md)）。商用 box map の「L2 = Soft-RT」は**層の性格付け**（[01:76](01-commercial-box-map.md)）であり、component 単位の時間クラス（Policy Gate = Non-RT 数十 ms）と両立する。本書はどちらかへ「裁定」しない。
- **旧番号の in-code ラベル**に注意。`warehouse_interfaces/schemas.py` の docstring には `StateSnapshot` を「L2(producer) ↔ L1(consumer)」と呼ぶ**本書と逆向きの旧体系**が残る（symbol: `schemas.py` `StateSnapshot` 前後コメント）。読み替えは本表で吸収し、docstring 是正は contract PR（[parallel-workflow.md §4](../../.claude/rules/parallel-workflow.md)）の別件（§未凍結事項）。

## 責務（持つ / 持たない）

L2 が持つもの:

- **実行許可（accepted-motion gate）**: accepted motion だけを下流へ出す。「L3 の command candidate が Traffic / Navigation / Safety / Hardware 側へ進む前の最後の L2 判定境界」（[01:86](01-commercial-box-map.md) 原則 3）。
- **複数台の交通調整**: 必要がなければ X-lite に縮退する（[01:87](01-commercial-box-map.md) 原則 4）。
- **凍結契約ハブ**: schemas / 安全上限 / location / store IF / path の単一ソース（Contract Box）。
- **reject reason catalog + JSONL audit**: accept / reject / error を `gen_id` join key 付きで記録し、decision event 集計（[05](05-decision-observability-and-tooling.md)）へ渡せる形にする。

L2 が持たないもの:

- **戦略判断・model 依存**: provider / trace / timeout は L4（[01:84](01-commercial-box-map.md) 原則 1）、提案の正規化・検証は L3（[01:85](01-commercial-box-map.md) 原則 2）。Governance は pure Python で rclpy も LLM も import しない。
- **`cmd_vel` / velocity / trajectory の生成**: `/bot{n}/cmd_vel` の producer は L1 の Nav2 controller と Emergency Guardian のみ（[12:151-159](../architecture/12-infrastructure-common.md)）。Navigation は position goal を処理し velocity policy を作らない（[01:88](01-commercial-box-map.md) 原則 5）。RMF の velocity も planning 入力にとどめ、下流 clamp を上書きしない（[../mode-c/11c-traffic-mode-c.md:499-523](../mode-c/11c-traffic-mode-c.md)）。
- **物理停止の最終保証**: twist_mux 優先度（emergency=100 > nav2=10）と L0 clamp が担う。「真の強制は L2/L1/L0 に残す」における L2 は許可の入口であり、物理の最終防衛線は L1/L0（[../adr/0003-bridge-local-manifest-composition.md:31](../adr/0003-bridge-local-manifest-composition.md)・[09:281-282](09-run-manifest-and-plugin-composition.md)）。

## Box の保管場所

| Box | repo 実体 | 案件で差し替えるもの / 保管したい artifact |
|---|---|---|
| Contract † | `ws/src/warehouse_interfaces`（`schemas.py` / `safety.py` / `locations.py` / `stores.py` / `paths.py`） | [01:65](01-commercial-box-map.md) の表のとおり（location・robot・schema・安全上限・store backend ／ frozen schema・migration note・contract tests） |
| Governance | `ws/src/warehouse_mcp_server`（`server.py` / `tools.py` / `policy_gate.py` / `gen_check.py` / `audit.py` / `nav2_client.py`） | [01:66](01-commercial-box-map.md)（policy・権限・業務ルール・rate limit・audit sink ／ policy profile・reject reason catalog・accepted-motion gate） |
| Traffic | `ws/src/warehouse_traffic`（`traffic_manager.py` / `traffic_logic.py` / `virtual_scan*.py`）+ `ws/src/warehouse_rmf_adapter`（`fleet_adapter.py` / `fleet.py` / `nav2_router.py` / `robot_driver.py`） | [01:67](01-commercial-box-map.md)（X-lite / X-rmf・fleet adapter・route graph・traffic rule ／ traffic profile・RMF graph・fallback plan） |

## Contract Box（凍結契約ハブ・†）

`warehouse_interfaces` は判断を持たない**純データ契約の箱**であり、L2–L0 の全 box が一方向に依存する（[01:80](01-commercial-box-map.md)）。rclpy 非依存（pure Python + pydantic）なので、Governance のような非 ROS プロセスからも import できる。

| 凍結 artifact | 内容 | anchor（symbol） |
|---|---|---|
| `Command` / `CommandItem` / `CommandAction` | 司令官の実行指示。action は `navigate\|wait\|stop\|yield\|charge` の StrEnum。`destination`/`retreat_to` は `KNOWN_LOCATIONS` 検証、`idempotency_key` は UUID 検証（optional・Bridge mint） | `warehouse_interfaces/schemas.py` `Command` / `CommandAction` |
| `Situation` / `StateSnapshot` | LLM 入力（`gen_id` を運ぶ）／ State Cache が書き出す集約状態 | `schemas.py` `Situation` / `StateSnapshot` |
| 安全上限 | `MAX_LINEAR_VELOCITY = 0.3` m/s、battery 10（critical）/ 20（low）/（charging 不要判定は Governance 側 80）、`clamp_velocity`（非有限→0.0） | `warehouse_interfaces/safety.py:18` ほか（Policy Gate と Emergency Guardian の両方が消費） |
| `KNOWN_LOCATIONS` | 既知 location 名 9 キーの frozenset（座標は config 側・非凍結） | `warehouse_interfaces/locations.py:23` |
| Store IF | `StateStore` / `GenStore` / `IdempotencyStore`（file 実装は atomic write・idempotency は 8 世代 window で eviction） | `warehouse_interfaces/stores.py` `IdempotencyStore` / `IDEMPOTENCY_WINDOW_GENS` 定数（`stores.py:23`） |
| 共有 path | `/tmp/warehouse/{state.json, gen_store, idempotency_store, audit.jsonl}`（env で上書き可・prod は `/run/warehouse`） | `warehouse_interfaces/paths.py` |

機械 validation で守れる範囲・「判断しない箱」としての性格づけは [05 §Contract Box](05-decision-observability-and-tooling.md) が正本（本書では重複させない）。凍結契約と docs 例示がズレたら**凍結契約が優先**（[docs-first.md](../../.claude/rules/docs-first.md)）。

## Governance Box（実行許可）

`warehouse_mcp_server` は 7 tool の MCP server + Policy Gate + gen / idempotency 検証 + JSONL audit + Nav2 REST forward で構成される。tool 仕様の正本は [../architecture/15-mcp-platform.md](../architecture/15-mcp-platform.md)（tool catalog `:133-192`・motion→endpoint 表 `:198-205`・Policy Gate 参照実装 `:263-342`）。「dispatch gate の本質」「機械化しやすさ（OPA/Cedar は候補・最終 dispatch seam の丸投げは不採用）」は [05 §Dispatch Gate と Governance の本質](05-decision-observability-and-tooling.md)・[07:56](07-layer-tool-decision-matrix.md) を正本とする。

### guard 順序（全 tool 共通）

```
① gen check（B-3: gen_id < current_gen → "stale_generation"）
② idempotency check（C: 同一 idempotency_key の replay → "duplicate_command"）
③ Policy Gate（下表の reason code・motion tool のみ）
④ audit（executed / rejected / error を gen_id 付きで JSONL 記録）
⑤ accepted-motion gate（_maybe_forward: status=="ok" のみ forward）
```

- ①② は `gen_check.py` `GenChecker.check`（idempotency は非 stale のときだけ登録・window は Contract Box の 8 世代）。`gen_id` / `idempotency_key` は **Bridge が mint** し、LLM / L3 / model 由来の値を信頼しない（§境界）。
- ③〜⑤ の validate→register は**単一 `asyncio.Lock` 内で atomic**（2 台同時 dispatch の race を閉じる・[15:507-534](../architecture/15-mcp-platform.md)、symbol: `policy_gate.py` `validate_and_register_dispatch`）。
- ⑤ `_maybe_forward`（symbol: `tools.py`）は forwarder 注入時（Mode A/B）かつ `status=="ok"` のときだけ Nav2 Bridge REST `POST /api/v1/{navigate,wait,stop}`（`:8645`・#86）へ forward する。**Mode C は forwarder=None＝全 motion tool が 0 actuation**（R-26 unit で固定）。**Mode X-ER も同 gate を config `mode_x_er.dispatch.forward_to_nav2`（既定 None＝0 actuation・#421/#423）で駆動する**（symbol: `x_er_bridge.py` `resolve_nav2_forwarder`・safe-OFF 既定 None）。forward 自体は fail-open（Nav2 Bridge 停止でもサイクルを殺さず audit に残す）だが、**gate 判定は fail-closed**（`ok` 以外は絶対に forward しない）。

### 7 tool（motion 3 + read-only 2 + advisory 2）

| tool | 種別 | Nav2 endpoint（Mode A/B） |
|---|---|---|
| `dispatch_task` | **motion** | wait→`/api/v1/wait`・deliver/yield→`/api/v1/navigate` |
| `cancel_task` | **motion** | `/api/v1/stop` |
| `send_to_charging` | **motion** | `/api/v1/navigate`（dropoff=`charging_station`） |
| `get_fleet_status` / `get_task_queue` | read-only | なし（forward 対象外） |
| `escalation_response` / `start_negotiation` | advisory（稟議・非 motion） | なし（forward 対象外） |

### Policy Gate reject reason catalog（実装済み語彙の転記・新設しない）

しきい値の出所: battery は Contract Box（`safety.py`）、鮮度・rate は Governance 定数（symbol: `policy_gate.py` `STALE_AFTER_S`/`UNAVAILABLE_AFTER_S`/`RATE_LIMIT_S`/`CHARGING_NOT_NEEDED_ABOVE`、doc の stale 判定表は [12:344-370](../architecture/12-infrastructure-common.md)）。

| 系統 | reason code | 条件 |
|---|---|---|
| location | `missing_location` / `unknown_location` / `same_location` | 名前なし／`KNOWN_LOCATIONS` 外／pickup==dropoff |
| robot 状態 | `unknown_robot` / `robot_stale` / `robot_unavailable` / `robot_in_emergency` | snapshot なし／age > 0.5s（dispatch 拒否）／age > 2.0s（全拒否）／emergency 中 |
| fail-closed | `state_timestamp_corrupt` | timestamp があるのに parse 不能 → **拒否**（安全側） |
| battery | `battery_critical` / `battery_low` / `charging_not_needed` | ≤10／≤20（新規タスク不可）／>80（charging のみ） |
| 頻度・重複 | `rate_limited` / `duplicate_destination` | 同一 robot へ 0.5s 以内の連続 cmd／別 robot が同じ dropoff を実行中 |
| action 固有 | `wait_requires_robot` | wait に robot 指定なし |

- charging 経路は「充電させない理由になる battery 低下」を**意図的に免除**する（reject は unknown/stale/emergency/corrupt/battery>80 のみ。symbol: `policy_gate.py` `validate_and_register_charging`）。
- dispatch 配線層（Policy Gate 前）の reject: `unknown_tool` / `missing_gen_id` / `bad_arguments` / `no_active_task` / `unknown_action` / `unknown_escalation_id` / `already_resolved` / `unknown_starter`（symbol: `tools.py` `dispatch` ほか）。
- catalog は**実装済み語彙の転記**であり、本書は code の追加・改名・renumber を一切しない。

## Traffic Box（交通管理）

切替は config 1 key `traffic_mode`（`config/warehouse.base.yaml:6`）。registry は LLM Bridge 側が配線する（symbol: `warehouse_traffic/traffic_logic.py` `make_traffic_manager`・[../mode-a/11a-traffic-mode-a.md:47-54](../mode-a/11a-traffic-mode-a.md)）。挙動の正本は [11a](../mode-a/11a-traffic-mode-a.md)（Mode A/B）と [11c](../mode-c/11c-traffic-mode-c.md)（Mode C）、OSS 再利用の小設計は [06 §Traffic Box](06-oss-reuse-and-box-small-designs.md)（本書では重複させない）。

| `traffic_mode` | 実装 | 要点 |
|---|---|---|
| `none`（Mode A） | `NoTrafficManager` | 排他ロジックなし。司令官 LLM が全判断。`submit_task` は dropoff のみを Nav2 Bridge へ（[11a:59-85](../mode-a/11a-traffic-mode-a.md)） |
| `simple`（Mode B） | `SimpleTrafficManager` | 通路（aisle）排他ロック。占有中は `waiting`、解放 trigger は Nav2 goal SUCCEEDED（主）+ lock 経過 timeout（副・暫定 30s・非凍結 `# TODO(Phase 3)`）（[11a:89-133,457-466](../mode-a/11a-traffic-mode-a.md)） |
| `open-rmf`（Mode C） | `RMFTrafficManager`（rmf-adapter track 所有） | RMF Traffic Schedule + Conflict Negotiation。Task Dispatcher は無効（task 割当は LLM 所有）。escalation は retry 3 回超のみ LLM へ（[11c:59-61,82-116,197-199](../mode-c/11c-traffic-mode-c.md)） |

- **virtual scan（Mode A/B のみ・安全レイヤー外）**: 相手 robot を自 Nav2 の `obstacle_layer` へ幻影 LaserScan として注入する Soft-RT 補助（10Hz・±15°・max 2.0m・>1.0m は抑制・`ROBOT_RADIUS 0.075` は `warehouse_description` 単一ソース。symbol: `virtual_scan_logic.py`、[11a:158-177,305-321](../mode-a/11a-traffic-mode-a.md)）。Mode C では node ごと停止する。安全の担保ではない（安全は L1/L0。[12:110](../architecture/12-infrastructure-common.md)）。
- **X-lite / X-rmf（Mode X-ER の実行 profile・[../mode-x-er/01-architecture-and-flow.md:199-206](../mode-x-er/01-architecture-and-flow.md)）**: X-lite（MVP 採用）= MCP / Policy Gate → Nav2 Bridge REST。X-rmf（optional・再評価候補）= MCP / Policy Gate → RMF Task API → Fleet Adapter。商用の組み合わせ例は [01:114-145](01-commercial-box-map.md)。「必要がなければ X-lite に縮退」（[01:87](01-commercial-box-map.md)）。
- **Mode C の実装状態**: EasyFullControl 直結（案A）が第一候補（[11c:203-271](../mode-c/11c-traffic-mode-c.md)）。**RMF-free offline core は実装済み** — `LocationResolver` が `KNOWN_LOCATIONS` 9 キーを検証し config 座標から `Nav2Goal` を解決（未知名・座標欠落は raise ＝ fail-closed・0 actuation）、1 プロセス 2 namespace・**Fleet Adapter が唯一の Nav2 writer**（single-writer 不変条件・[11c:63](../mode-c/11c-traffic-mode-c.md)、symbol: `warehouse_rmf_adapter/nav2_router.py` `LocationResolver` / `robot_driver.py` / `fleet.py`）。**RMF/rclpy 配線と live は R-38 memory gate（#187）待ち**（[11c:437-467](../mode-c/11c-traffic-mode-c.md)）。

## 境界（L4/L3 → L2 → L1/L0）

### 入口: L4 / L3 → L2

```
Command candidate（L3・実行許可ではない）
  -> action_map（L4 所有 seam）: Command -> MCP ToolCall へ写像・gen_id / idempotency_key を Bridge が mint
  -> Warehouse MCP Server（Governance）: gen -> idempotency -> Policy Gate
  -> accepted motion のみ下流へ
```

- **所有分割**: 「L4 Robotics Bridge Super-Box が `Command` から tool call への変換を所有し、Governance Box は MCP / Policy Gate 側の検証と accepted-motion gate を所有する」（[01:92](01-commercial-box-map.md)）。
- **mint の非対称性**: `gen_id` / `idempotency_key` は model output ではなく Bridge / `action_map` 側が注入する（[../mode-x-er/01-architecture-and-flow.md:197](../mode-x-er/01-architecture-and-flow.md)、symbol: `warehouse_llm_bridge/action_map.py`）。L3 Command Compiler も生成禁止（[03:169](03-l3-planning-core-box.md)）。
- **L3 は実行許可を持たない**（[../mode-x-er/01-architecture-and-flow.md:182](../mode-x-er/01-architecture-and-flow.md)・[03:21](03-l3-planning-core-box.md)）。L4 も motion を直接 dispatch しない（Nav2 action 直発行・ROS topic publish・server-side 即時実行・velocity 採用を持たない・[02:81-87](02-l4-robotics-bridge-box.md)）。
- **coordinate goal は未凍結**のため L3 は compile せず、L2 はこの経路を持たない。凍結するときは L3 Handoff + Governance + Nav2 Bridge の multi-owner gate（N-G1・[08:52,64-65](08-navigation-hardware-eval-gates.md)）として扱う。

### 出口: L2 → L1（position goal のみ）

- Mode A/B（X-lite）: Governance の forward が Nav2 Bridge REST（`:8645` `/api/v1/{navigate,wait,stop}`・位置ゴールのみ扱い速度を持たない）へ。`warehouse_nav2_bridge` 自体は **L1 Navigation Box**（[01:68](01-commercial-box-map.md)）。
- Mode C（X-rmf）: Fleet Adapter が唯一の Nav2 writer として per-namespace `NavigateToPose` action goal を送る（[11c:252](../mode-c/11c-traffic-mode-c.md)）。
- どちらの経路でも**渡るのは position goal だけ**。velocity・trajectory・`cmd_vel` は L2 から出ない。

### bypass 不可の安全チェーン（L1/L0・凍結）

L2 の許可が通っても、物理はこのチェーンが最終決定する。L2 のどの box もこれを迂回してはならない。

| 段 | 内容 | anchor |
|---|---|---|
| twist_mux（L1） | `emergency`=**prio 100**（Emergency Guardian・timeout 0.5s）> `nav2`=**prio 10**（Nav2 + collision_monitor 出力）→ `/bot{n}/cmd_vel` に一本化。凍結 safety contract | `ws/src/warehouse_bringup/config/twist_mux.yaml:42-49`・[15:379-405](../architecture/15-mcp-platform.md) |
| collision_monitor（L1） | twist_mux **上流**（prio-10 側）で PolygonStop。prio-100 を迂回できない | [12:522-561](../architecture/12-infrastructure-common.md) |
| Emergency Guardian（L1） | 距離・battery・blocked 10s・pose 鮮度を独立監視し zero-Twist を prio-100 へ毎 tick 再送 | [12:181-242,506-513](../architecture/12-infrastructure-common.md)（pose 鮮度は :506-513） |
| firmware clamp（L0） | MCU 内で `≤ 0.3 m/s` を強制・非有限（NaN/Inf）→ 0.0（停止）。ROS/OS 非依存の最終防衛線 | `firmware/include/safety_clamp.h`・`warehouse_interfaces/safety.py:18` |

L2 が停止しても走行は縮退継続する（Nav2 単独の障害物回避 + Guardian の deadlock 検出・[12:65](../architecture/12-infrastructure-common.md)）。これが「真の強制は L2/L1/L0」（[../adr/0003-bridge-local-manifest-composition.md:31](../adr/0003-bridge-local-manifest-composition.md)）の実体である。

## 推奨 module 構成案（現状の写像）

L2 は実装済みのため、[02](02-l4-robotics-bridge-box.md)/[03](03-l3-planning-core-box.md) と違い**新ディレクトリを提案しない**。既存 module と box 責務の対応（＝構成案は現状の写像・差分ゼロ）:

```text
warehouse_interfaces/          # Contract Box †
  schemas.py locations.py safety.py stores.py paths.py
warehouse_mcp_server/          # Governance Box
  server.py                    #   MCP stdio wrapper（SDK は optional extra）
  tools.py                     #   7 tool dispatch + accepted-motion gate（_maybe_forward）
  policy_gate.py               #   reject catalog + atomic validate->register
  gen_check.py                 #   B-3 gen + C idempotency
  audit.py nav2_client.py      #   JSONL audit / Nav2 Bridge REST 写像
warehouse_traffic/             # Traffic Box（Mode A/B）
  traffic_logic.py             #   No/SimpleTrafficManager + registry
  traffic_manager.py           #   #125 yield demo node（NavigateToPose 直列化）
  virtual_scan(_logic).py      #   幻影 LaserScan 注入（Mode A/B のみ）
warehouse_rmf_adapter/         # Traffic Box（Mode C・offline core、live は R-38 #187 待ち）
  nav2_router.py fleet.py robot_driver.py fleet_adapter.py
```

## Acceptance Gates

| Gate | 内容 |
|---|---|
| L2-G0 | stale `gen_id`（B-3）の motion tool が `status!="ok"` で終わり forward されない |
| L2-G1 | duplicate `idempotency_key`（C replay）で二重 dispatch が起きない（8 世代 window 内） |
| L2-G2 | Policy Gate の reject reason catalog（§Governance の表）が fixture で決定論的に再現でき、実装語彙と 1:1 |
| L2-G3 | `status=="ok"` 以外は `_maybe_forward` が Nav2 Bridge へ POST しない（accepted-motion gate） |
| L2-G4 | forwarder=None（Mode C 構成）で全 motion tool が 0 actuation（R-26） |
| L2-G5 | validate→register が単一 lock 下で atomic（並行 dispatch fixture で race 0・duplicate_destination が決定論） |
| L2-G6 | accept / reject / error が JSONL audit に `gen_id` join key 付きで残り decision event へ集計できる |
| L2-G7 | `traffic_mode` 切替（none↔simple）で通路排他が fixture 再現・`open-rmf` は gate 待ちが明示 reject（NotImplemented）になる |
| L2-G8 | L2 の出口が position goal のみ（`cmd_vel` / velocity / trajectory を publish しない）を fixture / AST で検査 |

> 多くは既存 unit（Policy Gate / gen / idempotency / Mode C no-actuation / traffic host unit）で green の**実装済み層**であり、本表はそれを商用受け入れ観点で再掲した **proposal catalog**（product contract ではない・[README.md §読み方](README.md)）。

## 未凍結事項

- State Cache の box 帰属（F3・[01:171](01-commercial-box-map.md)）— 本書でも決めない（レイヤ対応表では「未定」と記載）。
- box profile ↔ `traffic_mode` の翻訳 owner（F2・[01:170](01-commercial-box-map.md)）。
- Mode C（open-rmf）の RMF/rclpy 配線と live 検証 — R-38 memory gate（#187）待ち。X-rmf の RMF Task API / endpoint 詳細も未凍結。
- `warehouse_interfaces/schemas.py` の旧レイヤ番号 docstring（「L2 producer / L1 consumer」）の是正 — contract PR 別件（[parallel-workflow.md §4](../../.claude/rules/parallel-workflow.md)）。
- `charging_station` の単一占有は現状どの層も強制しない（owner 未定義。Policy Gate は意図的に検査しない）。
- `escalation_response` / `start_negotiation` の下流実処理（registry は in-memory・実 forward なし）。
- traffic の状態 dict・`route_A`/`route_B`・aisle lock timeout 30s は**すべて非凍結**（doc 例示 or 暫定値）。凍結は Contract Box の artifact のみ。
- L2-G 表の product contract への昇格判断（昇格時は owner doc + contract PR に分ける）。
- **鮮度窓の loosen 許容（REVIEW POINT）**: in-flight レーン `feat/policy-gate-freshness-config` は窓を `MAX_FRESHNESS_S=10.0` ceiling まで広げる overlay を許す＝restrict-only（[../adr/0004-l2-restrict-only-policy-profile.md](../adr/0004-l2-restrict-only-policy-profile.md)）の厳密 tighten-only と不一致。「環境適合ノブ（天井付き）の明示例外」とするか tighten-only へ寄せるかは当該レーンの設計判断（§2026-07-09 補足 ②注記）。

## 参考URL

参照日 2026-07-07:

- Open-RMF: <https://www.open-rmf.org/>（Traffic Schedule / Conflict Negotiation / Fleet Adapter）
- Model Context Protocol: <https://modelcontextprotocol.io/>（MCP tool 仕様の一次情報）
- OPA（Open Policy Agent）: <https://www.openpolicyagent.org/> ／ Cedar: <https://www.cedarpolicy.com/>（Governance の候補 tool・採否は [07:56](07-layer-tool-decision-matrix.md)）
- twist_mux（ROS 2）: <https://github.com/ros-teleop/twist_mux>（優先度 mux の一次情報）

## 2026-07-09 補足: 二段ゲートと L2 policy profile（末尾追記・行参照非破壊）

本節は 2026-07-09 に承認された設計思想（[../adr/0004-l2-restrict-only-policy-profile.md](../adr/0004-l2-restrict-only-policy-profile.md)）を正準化する。#165 末尾追記原則に従う additive tail であり、本文（1–237 行）の節番号・下流 file:line 参照を動かさない。

### ① 二段ゲート（L3 Validator ↔ L2 Policy Gate）

似て見える 2 つの check は重複ではなく、**同一事故を別入力・別時刻で 2 回止める**二段防御である。

| 観点 | L3 Validator | L2 Policy Gate |
|---|---|---|
| 問い | この plan は Command 候補になってよいか？ | この具体 tool call を **今** 実行してよいか？ |
| 入力 | LLM・ER の plan draft（実行前・plan 時刻） | `dispatch_task`/`cancel_task`/`send_to_charging` の引数 + live `StateSnapshot`（dispatch 瞬間） |
| 失敗の結果 | tool call を組み立てない | Nav2 へ forward しない |
| 語彙 | frozen 9-code `ValidationCode`（`report.py:69-88`・UPPERCASE） | 14 policy_gate code（§Governance の表・119-129 行） |

**confusable pairs**（似て見えるが別レイヤ・別時刻）:

- `UNKNOWN_TARGET`（L3: target をどの location にも ground できない）vs `missing_location`/`unknown_location`（L2: 具体 dropoff 引数が空 / `KNOWN_LOCATIONS` 外）。
- `EMERGENCY_ACTIVE`（L3: emergency 中は plan するな＝plan 時刻の状態）vs `robot_in_emergency`（L2: dispatch 瞬間の状態）。
- `CYCLE_STATE_STALE`（L3: cycle 単位の鮮度）vs `robot_stale` 0.5s（L2: gate での per-robot 鮮度）。

**defense-in-depth**: L3 が stale/bug で漏らしても L2 が止める。強い L3 は L2 へ流れる garbage を減らす。**L3 は実行権限を持たず・L2 は planning 知能を持たない**（本書 §責務 / §境界と一貫・[01:85,86](01-commercial-box-map.md)・`report.py:69-88`・§Governance）。

### ② L2 restrict-only policy profile

L2 Governance は L3 のように自由 plugin 化**しない**。L2 の案件差分 = **data-only policy profile**（v1: L2 では in-proc code plugin を採らない）であり、**締める/止めるのみ・緩めない**（restrict-only。[../adr/0004-l2-restrict-only-policy-profile.md](../adr/0004-l2-restrict-only-policy-profile.md)）。

**floor**: 凍結値（battery 10/20 = `safety.py:21-22`、`MAX_LINEAR_VELOCITY` 0.3 = `safety.py:18`）は hard FLOOR。より緩い profile 値は **起動拒否（fail-closed）**＝[09 §startup fail-closed composition preflight:356](09-run-manifest-and-plugin-composition.md) を鏡写す。`safety.py:11-12`「config may lower the operational speed, never raise」前例に倣う。

**per-knob 安全方向**（方向は knob ごとに `policy_gate.py` の意味から決める＝一律コピーしない）:

| knob | 締める（可） | 緩める（不可＝起動拒否） |
|---|---|---|
| `battery_low`（既定 20） | 20→30（厳しく） | 20→15 |
| `battery_critical`（既定 10・floor） | 10→上げ可 | 10→下げ |
| robot 鮮度窓（stale/unavailable） | 窓を縮める（tighten）方向のみが理想 | **REVIEW POINT**（下記注記） |
| rate-limit 間隔 | 間隔を伸ばす（頻度を厳しく） | 間隔を縮める |

**注記（鮮度窓の REVIEW POINT）**: in-flight レーン `feat/policy-gate-freshness-config`（未 land・branch 名で引く・行番号で引かない）は Policy Gate の robot 鮮度窓 `policy_gate.stale_after_s`（既定 0.5）/`unavailable_after_s`（既定 2.0）を config 駆動・additive・既定不変・fail-closed（非有限・<=0・stale>unavailable・上限 `MAX_FRESHNESS_S=10.0` 超・非 mapping は起動拒否）にする。**ただし機構としては overlay が窓を 10.0s まで広げる（＝緩める）ことを許す**（hard ceiling 10.0s は defang 防止だが tighten-only ではない）＝restrict-only の厳密形とは**不一致**。§未凍結事項に列挙する（このレーンは本書から編集しない）。同レーンは restrict-only を config 駆動 fail-closed で具体化する **in-flight 最初の例**として引く。

**L2 で禁止（6 項）**: ① LLM raw 出力の再解釈 / ② motion tool の追加 / ③ accepted-motion gate の迂回・弱化 / ④ reject を accepted へ巻き戻す / ⑤ audit 無しで accept / ⑥ timeout・例外で accept。**合成 = AND**（どの reject も final）。

**data-only v1 の理由**: L2 は最後の許可境界。in-proc hookimpl の trust は ADVISORY で enforce 不可（[09:276-281](09-run-manifest-and-plugin-composition.md)・[../adr/0003-bridge-local-manifest-composition.md:31](../adr/0003-bridge-local-manifest-composition.md)）＝L2 では code plugin を v1 で採らない。真の強制は L2/L1/L0（本書 §境界）。

**profile の置き場所（F2 未解決）**: L2 profile は **data artifact** であり、run manifest は **record であって config source ではない**（[01:170](01-commercial-box-map.md) が [09:44](09-run-manifest-and-plugin-composition.md) を引く）。profile→config 翻訳の owner は F2 未定義（[01:170](01-commercial-box-map.md)）＝profile の loading owner も未確定。**manifest を config source に格上げしない**。

**層別 plugin 化強度**:

| 層 | plugin 化強度 |
|---|---|
| L4 Model Adapter | 強 |
| L3 Validator・Visual Resolver | 強 |
| L3 Command Compiler | 中（出力 contract 固定） |
| L2 Governance | 中〜弱（restrict-only data profile） |
| L1・L0 | 弱 |
| Eval | 強（実行に非関与） |

### ③ coordinate goal 6-gap（なぜ未凍結のままか）

本書 §境界（160 行・N-G1）を展開する。座標 goal を凍結するには 6 層すべてに差分が要る:

| # | 層 | 何が要るか | 現状 |
|---|---|---|---|
| 1 | Contract | `CommandItem` に goal variant | 未凍結 |
| 2 | L4 action_map | goal mapping | 未 |
| 3 | Governance `dispatch_task` | goal 引数 | 未 |
| 4 | Policy Gate | frame/finite/valid-polygon/forbidden-zone/calibration-approved/profile-allows（座標は 9 named location と違い**無限**） | 未 |
| 5 | audit/eval | x,y + `calibration_id` + source pixel を記録 | 未 |
| 6 | Nav2 Bridge（L1） | **additive goal=(x,y[,yaw]) API は既に存在** | 実装済（`core.py:106` `_coord_from_goal`・INVALID_GOAL・navigate `core.py:160-197`・yaw drop／[12a:463](../mode-a/12a-integration-mode-a.md) 補遺・#223 additive） |

- それでも action_map + MCP/Policy Gate は **bypass されない**。凍結は N-G1 multi-owner gate（L3 Handoff + Governance + Nav2 Bridge・[08:52](08-navigation-hardware-eval-gates.md)）経由。reject code は `invalid_coordinate_goal` / `coordinate_goal_unfrozen`（[08:52](08-navigation-hardware-eval-gates.md)）。座標キーは `warehouse_interfaces` に足さない＝contract ラベル不要。
- Visual Resolver の pixel→homography→map→known-location snap の **offline core は既に存在**（`visual_resolver/resolver.py` `VisualTaskResolver`・`Calibration` = `validator/seams.py:24`）。欠けているのは **downstream contract + live calibration**（[../mode-x-er/07-implementation-status.md](../mode-x-er/07-implementation-status.md)）。
