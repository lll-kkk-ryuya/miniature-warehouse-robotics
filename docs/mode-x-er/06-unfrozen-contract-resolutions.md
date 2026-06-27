# Mode X-ER 未凍結契約の解決方針（contract PR 前の決定ドラフト）

作成日: 2026-06-25

> **状態**: 設計提案 / 決定ドラフト。本書は [`README.md`](README.md):94-104 と [`../productization/02-l4-robotics-bridge-box.md`](../productization/02-l4-robotics-bridge-box.md):279-284 が列挙する Mode X-ER の**未凍結ブロッカー**を、実装着手前に **FREEZE-NOW（今 凍結してよい）/ DEFER（ゲートまで延期）/ NEEDS-PROBE（実測が要る）** に判定し、提案形・additive 互換・contract PR 手順を **たどれる file:line** 根拠で固める。
>
> **本書自体は config / ROS topic / REST API / `warehouse_interfaces` frozen contract を一切凍結しない**（docs-first: 凍結は別 `docs/*` PR / `contract` PR で行う。[`.claude/rules/docs-first.md`](../../.claude/rules/docs-first.md) / [`parallel-workflow.md` §4](../../.claude/rules/parallel-workflow.md)）。提案形の値は**既存 docs 案の統合**であり、docs に literal が無いもの（pydantic クラス名・config ブロック名・file パス等）は `(発明/要確定)` と明記する。
>
> **対象範囲（7件）と対象外**: 本書が判定するのは **L4/L3 実装に直結する contract/config/topic ブロッカー 7件**（§0）。次の3件は**本書の対象外**（観測 / 別モードの関心事で、L4/L3 実装の凍結経路とは独立した別ゲートで扱う）: ① Hermes Langfuse plugin を trace owner にできるか（[`README.md`](README.md):102・HLF-G0〜G5 [`../productization/02-l4-robotics-bridge-box.md`](../productization/02-l4-robotics-bridge-box.md):177-199 の live spike gate）/ ② Langfuse trace taxonomy の Mode X-ER 固有 tag（`../productization/02`:284）/ ③ Mode X-ER-VLA の OpenVLA runtime 配置（`../productization/02`:282）。現状の trace owner は Bridge-owned 継続（`tracing.py`）で、L4/L3 実装は trace-owner 決定を待たない。

## 凡例 / 判定の意味

| 判定 | 意味 |
|---|---|
| **FREEZE-NOW** | docs 全体で方針が確定済みで、additive かつ既存契約を壊さず今 凍結（または「MVP 方針として確定」）できる |
| **DEFER(gate)** | docs が「未凍結・optional・実装後再評価」を明言、または内部形が上流フェーズ通過まで安定しない。指定ゲート通過後に凍結 |
| **NEEDS-PROBE** | 凍結を妨げる不確実性が外部 API 依存で、live probe による実測（HTTP status / 応答）が先 |

正本: [README](README.md) / [01-architecture-and-flow](01-architecture-and-flow.md) / [02-l3-planning-core](02-l3-planning-core.md) / [03-er-adapter-skeleton](03-er-adapter-skeleton.md) / [04-er-input-modalities-and-stt](04-er-input-modalities-and-stt.md) / [05-operator-feedback-and-voice-response](05-operator-feedback-and-voice-response.md) / [productization/02](../productization/02-l4-robotics-bridge-box.md)。コード慣習: [`ws/src/warehouse_interfaces/warehouse_interfaces/schemas.py`](../../ws/src/warehouse_interfaces/warehouse_interfaces/schemas.py)。

---

## 0. サマリ（7ブロッカーの判定）

| # | ブロッカー | 判定 | 解錠ゲート / 先決 | 提案の核 |
|---|---|---|---|---|
| 1 | RoboticsPlan / ValidationReport / ErTaskRequest の `warehouse_interfaces` 昇格 | **DEFER(gate)** | XER1-2 で内部形が安定後（RoboticsPlan/ValidationReport）。ErTaskRequest は**昇格不要**（bridge-local） | 02:278「最初から凍結する＝避けること」。昇格時は additive optional（`start_negotiation` 先例） |
| 2 | `transport: hermes\|direct\|worker` / `provider_type: llm\|er\|vla\|stt` の code enum | **DEFER(gate)** | Model Adapter Box の adapter skeleton 着地時。**`warehouse_interfaces` には入れない**＝bridge-local | observation-only（Langfuse tag）。実行分岐に使わない（03:75 原則） |
| 3 | Mode X-ER config key（x_lite/x_rmf・calibration_id）＋ calibration artifact 配置/形式 | **DEFER(gate)** | XER0 docs 確定 → XER3 Visual Resolver 着手前 | `mode_x_er:` ブロック（traffic_mode と直交）+ `config/<env>/calibration/<id>.yaml` |
| 4 | visual target を coordinate goal として MCP / Policy Gate へ通す正式契約 | **FREEZE-NOW**（MVP=known location 限定）＋**DEFER**（coordinate goal 契約） | coordinate 契約は具体ユースケース確定後（〜XER7） | MVP は既存契約のまま additive ゼロ。座標版は MCP 引数 or CommandItem variant の2案 |
| 5 | Hermes 経由で ER の audio / image API を扱えるか | **NEEDS-PROBE → RESOLVED**（2026-06-26〜27・PROBE-1/2/3 実測済） | （解錠済）live probe 3本（PROBE-1〜3）実測完了 → §5 実測結果 | 音声=**direct ER 確定**（Hermes は `input_audio` 400・透過不可）/ text+image=**lean Hermes gateway** or direct（image_url passthrough・観測一元化） |
| 6 | X-rmf の temporary waypoint / task submission seam | **DEFER(gate)** | #187 → Mode C Fleet Adapter live/sim → XER6 → XER7 | **schema を発明しない**（rmf_adapter CLAUDE.md:16）。命名のみ |
| 7 | OperatorNotice schema と `/operator/notice` topic（型/QoS） | **DEFER(gate)**（**案A 採用方針**のみ確定・型/QoS/topic名/schema_version は未凍結 draft） | XER-OF1 offline fixture で payload 実証後 | doc05 §8 の **contract draft（未凍結）** = `std_msgs/String`(JSON)・`operator_notice.v0`・RELIABLE/KEEP_LAST depth=20(暫定)/VOLATILE（§8.8 に topic名/depth/publisher/emergency/schema_version の未決） |

**全体結論**: いま `warehouse_interfaces` / config / topic に手を入れて「凍結」すべき項目は**実質ゼロ**。#4 の MVP 方針（known location 限定）は docs 上**確定済み**、#7 は**案A の採用方針のみ確定**（topic 名・型・depth・schema_version・emergency 扱いは doc05 §8.8 で未凍結）。いずれもコード凍結は依然として別 `contract`/`docs` PR とフェーズ通過を要する。

---

## 1. RoboticsPlan / ValidationReport / ErTaskRequest 昇格 — DEFER(gate)

**現状（docs）**: 3 schema いずれも「未凍結の内部案」と明言。
- RoboticsPlan: [`02`](02-l3-planning-core.md):278 の「避けること」に **「`RoboticsPlan` schema を最初から `warehouse_interfaces` に凍結する」** を列挙＝**最初から凍結しない**のが設計意図（adapter 出力形が変わっても L3 以降を守る seam）。`02`:5 / `03`:5「contract はまだ凍結しない」。最小形は `03`:55-73。
- ValidationReport: [`README`](README.md):97 未凍結。`02`:95 に rule result フィールド、`02`:96 に stable code 列挙はあるが `02`:5 内部案・`02`:98「threshold は docs/config/contract 確定まで hardcode しない」。
- ErTaskRequest: `03`:35-51 案。`03`:53「ER へ送る情報の上限」＝L4 内部 request で、`README`:94-104 / `productization/02`:279-284 の昇格対象リストに**載っていない**。

**判定 / 根拠**: **DEFER**。`02`:278 が「最初から凍結」を明示的に避けるべき行為とし、3 schema とも threshold/confidence が `02`:98 で未確定。内部形は **XER1（offline fixture で RoboticsPlan draft 安定・`README`:86）→ XER2（Validator が 0 dispatch + ValidationReport・`README`:87）** を通るまで安定しない。よって昇格は XER1-2 後をゲートとする。**ErTaskRequest は昇格不要**＝Bridge package ローカル（adapter seam）に置く（topic/contract 境界を跨がない内部 request）。

**提案形（既存 docs 案の統合のみ・昇格時の下書き）**:
- **RoboticsPlan draft**（出所 `03`:55-73 最小形 ＋ `02`:46-54 Validator 入力）: `schema_version: str="robotics_plan_draft.v0"`(`03`:59) / `plan_id: str`(`03`:60) / `source_model: str`（audit 用・**下流分岐に使わない** `03`:75）/ `input_refs:{audio,image,state}`(`03`:62-66) / `transcript: str\|None`(`03`:67) / `interpreted_intent: str\|None`(`03`:68) / `detections: list[Detection]`(`03`:69, Detection={id, pixel:list[int], confidence:float} `02`:117) / `task_graph: list[TaskNode]`(`03`:70, TaskNode={id, robot, action, target, after:str\|None}・after は `"t1.completed"` 形 `02`:171-173) / `operator_clarification_required: bool=False`(`03`:71)。
- **ValidationReport**（出所 `02`:60-66 ＋ `02`:95）: `status`（literal は docs 上 `accepted`(`02`:61) と `needs_clarification`(`02`:79,84) のみ確定。`02`:68 は「status != accepted は 0 dispatch」＝**reject 系 status 文字列は要確定**）/ `errors: list[RuleResult]` / `warnings: list[RuleResult]` / `normalized_plan: dict`。RuleResult={`code, severity, field_path, message_for_operator, debug_detail, dispatch_effect`}(`02`:95)。stable `code`(`02`:96・全8): `UNKNOWN_ROBOT`/`UNKNOWN_ACTION`/`UNKNOWN_TARGET`/`LOW_CONFIDENCE_TARGET`/`INVALID_AFTER_REFERENCE`/`TASK_GRAPH_CYCLE`/`CYCLE_STATE_STALE`/`EMERGENCY_ACTIVE`（clarification 系 code は docs 未列挙＝**要確定**）。
- **ErTaskRequest**（`03`:37-50・昇格しない方針だが形の記録）: `request_id, mode, instruction_audio_ref, transcript, overhead_image_ref, state_snapshot_ref, calibration_id, known_robots, known_locations, allowed_actions=[navigate,wait,stop,yield,charge], output_contract="robotics_plan_draft.v0"`。
- **慣習統合（schemas.py に倣う・発明でない）**: 全 model は `_Model(BaseModel, ConfigDict(extra="ignore"))` を継承（[`schemas.py`](../../ws/src/warehouse_interfaces/warehouse_interfaces/schemas.py):24-25）。enum は `StrEnum`（schemas.py:17,135）。`known_locations` 検証は `warehouse_interfaces.locations.KNOWN_LOCATIONS` 再利用（**新 location を定義しない**）。threshold 数値は schema に hardcode せず `PlanPolicy`/config 注入（`02`:94,98）。
- `(発明/要確定)`: ValidationReport の reject 系 `status` 文字列、clarification 系 `code` 語彙、`severity`/`dispatch_effect` の許容値集合、Detection/TaskNode を独立 model にするか inline か。

**additive 互換**: 昇格時は additive-first で安全。新 model を追加するだけ（既存 `Situation`/`Command`/`Proposal` のフィールド削除・改名・型変更なし）。全 model が `extra="ignore"`（schemas.py:13,25）で未知 field に hard-fail しない（前方互換）。先例 `Command.start_negotiation: StartNegotiation | None = None`（schemas.py:194・既定 None・後方互換）と同型の additive optional。RoboticsPlan は L4→L3 内部表現で既存 `Command`/Nav2 wire 契約を変えない（`02`:229「出力は既存 Command に通す」）。

**contract PR 手順**: いま PR は出さない（DEFER）。昇格ゲート＝XER1→XER2 後。手順（[parallel-workflow.md §4](../../.claude/rules/parallel-workflow.md) / [issue-and-pr-authoring.md §4](../../.claude/rules/issue-and-pr-authoring.md)）: ① `docs/*` で docs 確定 → ② `feat/*` で `warehouse_interfaces` 追加 PR（`contract` ラベル必須 ＋ track ラベル・**X-ER epic 起票が先**）→ ③ マージ前に依存トラックへ予告: **llm-bridge**（Command compile `02`:229）・**safety-state**（Policy Gate / `EMERGENCY_ACTIVE` `02`:96）・**skeleton**（`warehouse_interfaces` owner #1・合意必須）→ ④ マージ順 docs PR → contract PR（凍結）→ L3 実装。ErTaskRequest は contract PR 不要（bridge-local）。

---

## 2. transport / provider_type の code enum — DEFER(gate)・warehouse_interfaces に入れない

**現状（docs）**: 両 enum は一貫して「docs 例示・未凍結・proposal」。
- [`productization/02`](../productization/02-l4-robotics-bridge-box.md):264「`transport: hermes\|direct\|worker` はコード enum 未実装＝未凍結」。:110-116 で `provider_type: llm\|er\|vla\|stt` と `transport` は同じ **`model_call observation`** レイヤのフィールド。
- [`productization/04`](../productization/04-box-storage-and-reuse-guidelines.md):63 が **`transport` enum を名指しで「未凍結契約・frozen 扱いで出さない」**。:64「transport は box interface 裏の実装選択」。
- [`productization/01`](../productization/01-commercial-box-map.md):52-53「安全 gate を持つ box の `transport` は n/a（`hermes` と書くと motion gate が Hermes 線で貫かれる category error）」。:62 Model Adapter Box は「未実装 skeleton＝proposal（ws/src に実体なし）」。
- [`03`](03-er-adapter-skeleton.md):75「`source_model` は audit 用・**下流の実行分岐に使わない**」＝観測フィールドを実行分岐に使わない原則の正本。
- grep: `ws/src` に `ProviderType`/`Transport` enum・`hermes|direct|worker` enum は**ゼロ**（既存 `CommandAction(StrEnum)` schemas.py:135-141 のみ）。

**判定 / 根拠**: **DEFER**。(1) `productization/04`:63 が `transport` enum を「未凍結・frozen 扱いで出さない」と名指し＝今 `warehouse_interfaces` に凍結するのは docs に無い契約の発明（docs-first 違反）。(2) Model Adapter Box が proposal（ws/src 実体ゼロ）で enum を consume する実装が無い＝凍結対象（producer/consumer）が存在しない。(3) 両 enum は **observation tag（Langfuse）** であり `03`:75 の「audit 用＝実行分岐に使わない」原則の同類＝安全契約でも wire 契約でもなく、全トラックが import する `warehouse_interfaces`（凍結契約ハブ）に置く根拠が無い。凍結は adapter 実装と同じ **bridge-local** スコープで、registry skeleton 着地ゲートまで DEFER。

**提案形（既存 docs 案の統合のみ）**:
```python
# bridge-local（adapter registry と同居・warehouse_interfaces には入れない）
class ProviderType(StrEnum):  # observation/audit tag only — NOT an execution branch key
    LLM = "llm"; ER = "er"; VLA = "vla"; STT = "stt"     # 値の出所: productization/02:111, 01:17-19,62
class Transport(StrEnum):     # box interface 裏の実装選択（01:52）— 安全 gate box は n/a
    HERMES = "hermes"; DIRECT = "direct"; WORKER = "worker"  # 値の出所: productization/02:112,264, 01:16
```
- 用途: **観測専用（Langfuse `model_call observation` の tag）**。実行分岐キーにしない（`03`:75 原則）。`CommandAction(StrEnum)` と同じ StrEnum パターン（schemas.py:135）だが**配置層が異なる**（bridge-local）。
- `(発明/要確定)`: クラス名 `ProviderType`/`Transport`（docs に literal なし・値リテラルは docs 由来固定）。安全 gate box の `transport=n/a` は enum 値でなく Optional/未設定で表現（`productization/04`:34,64）。Mode X-ER 単体では `worker` を未使用（`01`:163 で worker は Mode X-ER-VLA 側）だが docs の3値は保持。

**additive 互換**: `warehouse_interfaces` を触らないため全トラック波及ゼロ。observation tag は Langfuse metadata で既存購読者（web_bridge observe-only・WO KPI）が未知 tag を無視できる（additive・前方互換）。実行分岐に使わない限り値の増減が motion 経路/Policy Gate を壊さない（observation-only に閉じる最大の互換利点）。

**contract PR 手順**: **`contract` ラベル不要**（`warehouse_interfaces` 不触）。DEFER ゲート＝Model Adapter Box の最初の adapter skeleton を `ws/src` に着地させる PR（track:llm-bridge）。その実装 PR 内で bridge-local enum を定義し Langfuse observation tag にのみ使う（PR 本文に「observation-only・実行分岐に使わない」を明記）。将来 `warehouse_interfaces` 昇格判断（`productization/02`:283 で未決）が出た時に限り `contract` PR + 予告。

---

## 3. Mode X-ER config key ＋ calibration artifact — DEFER(gate)

**現状（docs）**: [`README`](README.md):98「Mode X-ER config key」/ :99「calibration artifact の配置と形式」を**未凍結リスト**に列挙。`README`:5「最初の実装に入る前に確定し、別 PR で凍結」。
- execution profile: `README`:76-77 で X-lite=MVP profile / X-rmf=optional profile。[`01`](01-architecture-and-flow.md):201-206 表が key を `x_lite`(MVP 採用)/`x_rmf`(再評価候補) と表記。`02`:234「X-lite/X-rmf 差分は `ExecutionProfile` で分け、RoboticsPlan draft schema は共通に保つ」。
- calibration artifact: `02`:148-149「pixel→map 変換は calibration artifact の `homography` 版に紐付ける。artifact は **camera id / map frame / homography matrix / reprojection error / valid polygon** を持つ」。:156「**file として version 管理**・現場ごと差し替え」。`02`:277（避けること）「**camera calibration をコード定数に埋め込む**」。
- calibration_id 参照: `02`:117-121 / `01`:124 の例 `"calibration_id": "calib-YYYYMMDD"`（例示）。

**判定 / 根拠**: **DEFER**。profile/artifact は「方向性」確定だが「凍結形」が docs 未確定。① config 表現（traffic_mode と同列 top-level か `x_er` ネストか）が docs 未記載＝発明領域。② calibration の file 形式（YAML/JSON）・配置パス・version 命名が未確定。③ `calib-YYYYMMDD` は「例示（illustrative）」。実装が無い現状で base.yaml を先に汚すと、profile 選択配線（launch/bridge 両所読み）と calibration ローダ設計が固まる前に key を確定してしまう。XER0/XER1 で request shape が固まり XER3 で Visual Resolver が calibration を実消費する段で凍結するのが docs-first ゲート（`README`:85-92）。なお `load_config`（[`config.py`](../../ws/src/warehouse_interfaces/warehouse_interfaces/config.py):114-131）は未知 top-level key を deep-merge で素通し、schema 制約は `safety.*` のみ（config.py:69-111）＝追加は非破壊で、凍結を急ぐ必然性なし。

**提案形（`(発明/要確定)` を明記）**:
- ① execution profile（traffic_mode と直交）: `traffic_mode`(none/simple/open-rmf) は Mode A/B/C 軸。X-lite/X-rmf は「視覚司令を Nav2 REST に流すか RMF に流すか」の実行先で別軸＝**直交**が docs 整合（`README`:76-77）。`(発明/要確定)` 形:
  ```yaml
  mode_x_er:                       # 新規 top-level（docs に config 構造記載なし＝発明）
    execution_profile: x_lite      # x_lite | x_rmf（値は 01:203-204 由来）
    calibration_id: ""             # 既定空＝環境 overlay で指定
  ```
- ② calibration artifact: file に `camera_id / map_frame / homography(3x3) / reprojection_error / valid_polygon`（**5 field は `02`:149 逐語**）。`(発明/要確定)` 形式=YAML（[code-style.md](../../.claude/rules/code-style.md) の 2-space YAML 慣習・既存 config が YAML 主体）、配置= **`config/<env>/calibration/<id>.yaml`**（[environments.md](../../.claude/rules/environments.md) の env-overlay 原則・site 固有 `02`:158）。**コード定数に埋めない**（`02`:277）。座標/行列のみで secret でない＝`.env` 不要。
- ③ calibration_id 参照: config `mode_x_er.calibration_id`（②の file 名 stem と一致）を L4 が input bundle に載せ、Visual Resolver が id→file 解決（`02`:117-121,251-252）。`calib-YYYYMMDD` は例示どまり＝凍結時に厳密形確定。multi-site は `calib-<site>-YYYYMMDD` 拡張余地（`02`:158）。

**additive 互換**: 完全 additive。`load_config` は base+overlay を deep-merge するだけで未知 top-level key に schema 制約なし（`_validate_safety` は `safety.*` のみ）。既存購読者（traffic_logic/situation/launch）は新 key を読まず無視。`warehouse_interfaces` 不触。`WAREHOUSE__MODE_X_ER__EXECUTION_PROFILE` の env override も自動で効く（config.py:48-66）。calibration file は新規ファイル追加のみ。

**contract PR 手順**: `config/warehouse.base.yaml` は **bringup/skeleton 所有**（[parallel-workflow.md §7.1](../../.claude/rules/parallel-workflow.md)）。① 先に `docs/*` PR（track:docs）で README:98-99 を凍結形へ落とす → ② base.yaml 追加は所有 Issue へ予告→合意→**末尾追記**（既存ブロック後）→ ③ 依存予告先: Mode X-ER 実装レーン・nav-traffic（x_rmf↔`rmf.enabled` base.yaml:73-75）・web（観測 mode 表示）→ ④ マージ順 docs 凍結 PR → base.yaml additive PR（XER0 完了後・XER3 着手前）。env overlay calibration は dev=sim placeholder・prod=Phase 2 実測（locations と同運用）。**RoboticsPlan input に calibration_id が現れるため #1 と同時凍結が安全寄り**。

---

## 4. visual target → coordinate goal の正式契約 — FREEZE-NOW(MVP) ＋ DEFER(coordinate)

**現状（docs）**: 「MVP は known location snap 時のみ compile、coordinate goal は未凍結＝compile しない」を5箇所で一致明記。
- [`02`](02-l3-planning-core.md):25「`CommandItem.destination`/`retreat_to` は known location だけを許す → MVP は snap できた場合だけ既存 `Command` に compile」。:37「coordinate goal は `warehouse_nav2_bridge` 側に additive variant があるが、MCP/Policy Gate 経由の**全経路 contract は未凍結**＝MVP の Command Compiler は coordinate goal を compile しない」。:152 / :231 も同旨。
- [`README`](README.md):100 未凍結事項に「visual target を coordinate goal として MCP/Policy Gate へ通す正式契約」。
- 凍結契約側: [`schemas.py`](../../ws/src/warehouse_interfaces/warehouse_interfaces/schemas.py):143-162 `CommandItem.destination` は `str|None`（KNOWN_LOCATIONS 検証のみ・座標フィールドなし）。座標 variant は [`nav2_bridge/core.py`](../../ws/src/warehouse_nav2_bridge/warehouse_nav2_bridge/core.py):39,106,124-148（GoalCoord/`_resolve_goal` XOR・yaw は :107,122 で drop）に**実在するが自パッケージ REST API の additive optional 引数**で凍結契約でも MCP tool でもない（[`nav2_bridge/CLAUDE.md`](../../ws/src/warehouse_nav2_bridge/CLAUDE.md):9）。MCP 側は座標 tool 未追加（[`mcp_server/CLAUDE.md`](../../ws/src/warehouse_mcp_server/CLAUDE.md):11）。

**判定 / 根拠**: **二分**。
- **FREEZE-NOW（MVP=known location 限定）**: docs 5箇所＋README 未凍結表が完全一致＝新規発明不要。既存契約（`CommandItem.destination=str|None` + KNOWN_LOCATIONS 検証 schemas.py:157-162）と Policy Gate の `check_location_known`（[`policy_gate.py`](../../ws/src/warehouse_mcp_server/warehouse_mcp_server/policy_gate.py):47-52・unknown は `unknown_location` reject）に**何も足さず MVP が成立**＝additive ゼロで即「MVP 方針として凍結」可。
- **DEFER（coordinate goal 契約）**: coordinate を通すには (a) MCP tool 引数 / CommandItem に座標 variant 追加、(b) Policy Gate の location 検証を座標対応に拡張（現状 `destination=None` の座標は `missing_location` で fail-closed＝安全側に落ちる policy_gate.py:50）、(c) duplicate/same-location 検査の座標版意味論、が**未定義＝設計の空白で発明禁止**（`02`:37 が「全経路 contract 未凍結」明記）。MVP デモ（赤箱→shelf_1 snap `02`:27-35）は塞がないので DEFER で実害なし。

**提案形**:
- **FREEZE-NOW（additive ゼロ）**: `CommandItem` 不変。Command Compiler は `resolution=="known_location"` の ResolvedTarget のみ compile（`02`:211,231）、unresolved/coordinate は 0 dispatch（`02`:68,152）。Policy Gate は現状の `check_location_known` で十分。
- **DEFER（将来 coordinate を通す時の候補・どれも `(発明/要確定)`）**: 案① **additive MCP tool 引数**＝`dispatch_task` に optional `goal:[x,y]|[x,y,yaw]`（destination と XOR）。nav2_bridge 既存 GoalCoord/`_resolve_goal`（core.py:39,124-148 の XOR=INVALID_GOAL）を上流へ延伸する形で REST 既存 variant と整合（第一候補）。案② **CommandItem variant**＝`CommandItem` に optional `goal`（destination と排他）を additive 追加（`idempotency_key` と同じ optional/extra=ignore パターン schemas.py:143-169）。Policy Gate（両案共通）: 座標は KNOWN_LOCATIONS に無いため `check_location_known` を「destination XOR goal」を受ける形へ拡張し、座標は calibration の valid polygon（`02`:149-151）内・reprojection error 内で検証。duplicate は座標距離しきい値（**数値は docs/config 未定＝hardcode 禁止 `02`:98**）。yaw は nav2_bridge が drop（core.py:107,122）。

**additive 互換**: FREEZE-NOW は契約変更ゼロ＝完全後方互換。DEFER 両案とも additive-first（optional field 追加・既存 destination 経路不変・`extra="ignore"` schemas.py:25 で旧購読者は新 goal を無視）。先例 `idempotency_key`/`start_negotiation`（additive optional・既定 None）。Policy Gate 拡張は `destination=None`+goal 経路を新設し既存 named 経路の挙動を変えないこと（拡張前は `missing_location` で安全に reject される＝fail-closed）。

**contract PR 手順**: FREEZE-NOW＝`docs/*` PR（track:docs）で `02`/`README` の MVP 方針を「案」→「MVP 凍結方針」に格上げ（コード変更不要・`warehouse_interfaces` 不触）。DEFER＝(1) `docs/*` で案①②と Policy Gate 座標検証の設計を `02`/`01` に追記（未確定なら『要確定』表記を保つ）→ (2) 確定後に `contract` ラベル PR ＋ 依存トラック（bridge: nav2_bridge + mcp_server・両 CLAUDE.md が「実装前に docs/contract を先に更新」明記）へ予告。gate=coordinate goal を要する具体デモ要件 or XER7（`README`:92）。

---

## 5. Hermes 経由で ER の audio / image API を扱えるか — NEEDS-PROBE → RESOLVED（2026-06-26〜27・PROBE-1/2/3 実測済）

**実測前の状況（probe 動機・2026-06-25 時点）**:
- [`productization/02`](../productization/02-l4-robotics-bridge-box.md):281 が未凍結事項「Hermes が Gemini Robotics-ER の audio/image API を扱えるか」。L4-G4(`02`:276)「Hermes 経由/direct の両方で同じ L3 interface に渡せる」。
- [`04`](04-er-input-modalities-and-stt.md):9「**Gemini Robotics-ER 1.6 Preview は音声を直接入力できる（一次情報）**」。但し当時は :27「audio は input 一覧に明記されるが robotics command としての実証は未確認」とし audio probe 自体が未実施だった（probe 後の現在 :28 は「✅ 疎通＋parseable plan 生成を実証」）（text/image probe は HTTP 200 実測済 [`vla-access-and-runtime-spike.md`](../dev/vla-access-and-runtime-spike.md):23-28、ただし :33 これは**Gemini API 直接 call で Hermes 経由ではない**）。
- web 一次情報（取得 2026-06-25）: Gemini API `gemini-robotics-er-1.6-preview` の Inputs = Text/images/video/audio（`https://ai.google.dev/gemini-api/docs/robotics-overview`）。Hermes の OpenAI 互換 `/v1/chat/completions` は `image_url`（http(s) と `data:image/...`）を明示サポートする一方、**非 image の `data:` URL / uploaded file は `400 unsupported_content_type`**（`https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server`）。OpenAI 互換 `input_audio` は別 content part のため、当時は Hermes が audio content part を透過しない公算が高いと推定していた（**PROBE-2 で `400` を実測確定。下記「実測結果」§5**）。

**判定 / 根拠**: **NEEDS-PROBE → RESOLVED**（2026-06-26〜27・PROBE-1/2/3 実測済。詳細は下記「実測結果」）。元の不確実性は外部 API 依存で実測が要るものだった: (1) ER の robotics audio 応答品質は Google も作例なし、(2) Hermes 透過は web 上「audio=400 公算・image=対応」。**実測で両方確定**: 音声は **direct ER**（Hermes は `input_audio` 400・透過不可）、text+image は **Hermes 経由でも direct でも**同 L3 interface に渡せる（image_url passthrough）。`02`:281 の凍結方針はこの実測で固まった。

**提案形（probe 手順・外部 API 既存仕様の採用で発明でない）**: 既存 spike と同方式で3 probe（各 HTTP status と modelVersion/usage のみ記録・値や thoughtSignature は記録しない）。
- **PROBE-1（ER audio direct・最優先）**: Gemini REST `POST .../models/gemini-robotics-er-1.6-preview:generateContent` に `parts=[{"text":"<指示>"},{"inline_data":{"mime_type":"audio/wav","data":"<base64 ≤20MB>"}}]` → HTTP 200 と task 理解応答を確認（`04`:84 の robotics 実証）。
- **PROBE-2（Hermes 透過 audio・否定仮説検証）**: Hermes `/v1/chat/completions`（dev は container→host `http://host.docker.internal:8642`・[environments.md](../../.claude/rules/environments.md)）に `content=[{"type":"text",...},{"type":"input_audio","input_audio":{"data":"<base64>","format":"wav"}}]` → status 実測。web 予測は **400**。200 なら透過可、400 なら「Hermes は audio 非透過＝direct 固定」を `02`:281 に確定。
- **PROBE-3（Hermes 透過 image・L4-G4 の Hermes 側）**: 同 endpoint に `image_url:{url:"data:image/png;base64,..."}` → 200 と画像認識応答（image は Hermes/direct どちらでも同 L3 interface に渡せることを裏付け）。注意: Hermes は model server-side 固定＝vision-capable provider が必要。
- 凍結方針（probe 後・additive）: transport を `direct`(audio/image 一次) + `hermes`(image・OpenAI 互換 image_url) として固める。content part 形（`input_audio`/`inline_data`）は外部 API 仕様で当方の発明ではない。

**additive 互換**: probe は読み取り検証で frozen contract/topic/config を追加しない（`04`:5 / `02`:264）。将来凍結する transport は新 enum 値追加＝additive で既存 direct image 経路を壊さない。

**contract PR 手順**: probe 自体は契約 PR 不要。(1) probe script + 結果を `vla-access-and-runtime-spike.md` に追記する `docs/*` PR（track:docs・値非掲載）→ (2) 実測で `02`:281 / `04`:84 の TODO 解消 `docs/*` PR → (3) transport enum を昇格する段で初めて `contract`（#2 と一括）。**live probe は ~/.hermes/.env(GEMINI_API_KEY) / config/dev/.env を要する＝ユーザーから path+目的の明示スコープ承認が必要（値は非表示）**（environments.md）。

### 実測結果（2026-06-26〜27・PROBE 完了 → NEEDS-PROBE 解消）

ユーザー承認のもと live 実測（`~/.hermes/.env` の `GEMINI_API_KEY` を値非表示で使用）。PROBE-1/3 は 2026-06-26、PROBE-2 は 2026-06-27 に実測（全 3 本が実測済）。harness = `tests/live/test_er_handoff_live.py`（companion transport PR で着地）。

- **PROBE-1（ER audio direct）= ✅ 成功（疎通＋parseable plan）**。`generateContent` に `text`(schema) + `inline_data`(audio/wav) → HTTP 200・**ER が音声を直接理解**し transcript（"Bot1 goes to the red box. After Bot1 leaves, Bot2 goes to the blue box."）＋2-task plan を返却 → `to_robotics_plan_draft` で valid `RoboticsPlanDraft`（1803 tok）。`04`:27-28 の「audio→direct-ER の疎通＋parseable plan」を**実証** → **音声は direct ER で疎通する**（transport 確定・STT は critical path に入れない）。**robotics-grade command 品質は別 eval・未確認**（`04`:84）。
- **PROBE-2（Hermes 透過 audio）= ✅ 否定確定（実測）**。2026-06-27 に専用 lean Hermes gateway の `/v1/chat/completions` へ `input_audio` content part を直接 POST → **HTTP 400 `unsupported_content_type`** を実測（応答 message: `Unsupported content part type 'input_audio'. Only text and image_url/input_image parts are supported.`）。Hermes の OpenAI 互換 API server は **text + image_url/input_image のみ**で `input_audio` を透過しない（「公式 docs より推定」→ **実測で確定**）。→ **Hermes は音声を運べない＝音声 direct 固定**。
- **PROBE-3（ER via Hermes gateway）= ✅ 成功**。**専用 Hermes インスタンス**（`model.provider:"google"` native・active model=`gemini-robotics-er-1.6-preview`・別 `HERMES_HOME`・別ポート 8643・**個人 `~/.hermes` 不変**）の `/v1/chat/completions` → HTTP 200・ER plan → handoff → valid draft。**追加発見**: (a) Hermes API server は request `model` を無視し**単一 server-side active model**を使う（per-request routing 無し）→ ER 用は専用 gateway 必須。(b) full agent gateway は plan JSON を ` ```json ` fence で包む＋~20k token 過負荷 → handoff に fence 耐性を追加＋**lean 化 config**（`platform_toolsets.api_server:[]` / memory off / agent guidance off。`deploy/dev/hermes-er/config.lean.yaml` / `deploy/dev/run-er-hermes.sh`。いずれも companion transport PR で着地）。

**確定 transport（two-path）**: 音声 = **direct ER**（Hermes 不可）/ text+image = **lean Hermes gateway**（image_url passthrough・観測一元化）or direct。どちらも handoff で同一 `RoboticsPlanDraft` に正規化（transport 非依存）。観測（Langfuse / STT transcript）は**本線外・fail-open**で motion を止めない。**Langfuse は当面 Bridge 所有 trace を主**（音声 direct leg は Hermes 経路外＝Hermes plugin が観測できない）・Hermes 内蔵 plugin は HLF gate 後（[`productization/02`](../productization/02-l4-robotics-bridge-box.md):177-199）。STT は ER 経路に直列化せず out-of-band（Hermes `transcribe_audio` も利用可だが capture-side 機能・`04`:33,44）。

**STT transport（out-of-band・live 実測済）**: 上記 two-path（ER critical path）とは**別経路**。Hermes は STT を HTTP で `/api/audio/transcribe`（Hermes web app endpoint）に出し、dashboard UI build なしでも素の `uvicorn hermes_cli.web_server:app` を loopback で起動すれば叩ける。`/api/` は dashboard session token `X-Hermes-Session-Token`（`HERMES_DASHBOARD_SESSION_TOKEN` で固定）を要する。応答 = `{ok, transcript, provider}`。これは PROBE-2（`/v1/chat/completions` の ER audio = 400 実測）とは**別の lane**であり、音声→direct-ER の critical path と並行（**ER を一切ブロックしない**）に realtime-UI の transcript sink へ流れる＝provenance/audit（`04` §2・`04`:39）。harness/launcher = `tests/live/test_er_handoff_live.py` / `deploy/dev/run-er-stt-http.sh`（companion transport PR で着地）。

---

## 6. X-rmf の temporary waypoint / task submission seam — DEFER(gate)

**現状（docs）**: 3つの独立正本が同一方針で一致＝「未凍結・optional・GATE 後再評価・schema を発明しない」。
- [`README`](README.md):103 未凍結事項に「X-rmf の temporary waypoint / task submission seam」。:77 X-rmf=「optional 実行 profile」。:92 XER7=X-rmf eval（XER6 X-lite E2E の後）。
- [`01`](01-architecture-and-flow.md):204 採用状態=「再評価候補」。:206「X-rmf は本質でなく optional・まず X-lite」。
- [`02`](02-l3-planning-core.md):37「全経路 contract 未凍結」。:240 `RmfTaskCompiler` は将来 plugin 例。:278「RoboticsPlan を最初から凍結＝避けること」。
- [`rmf_adapter/CLAUDE.md`](../../ws/src/warehouse_rmf_adapter/CLAUDE.md):16「X-rmf は X-lite E2E と Mode C Fleet Adapter live/sim 検証の後に再評価。**GATE 前に RMF waypoint / temporary waypoint / task schema を発明しない**」。

**判定 / 根拠**: **DEFER**。docs が「未凍結・optional・XER7 再評価・GATE 前に発明禁止」を明言＝今 freeze する根拠 doc が存在しない（むしろ freeze 禁止が docs 方針）。NEEDS-PROBE でもない（不足情報でなく上流 gate の物理的未通過が原因）。

**提案形（schema は発明しない・既出案の統合のみ）**: visual target の解決先は (a) `known_location` snap=X-lite で既存 `Command.destination` に compile 可（`02`:25,34）、(b) `temporary_waypoint`=未登録の動的地点（rmf_adapter CLAUDE.md:16 が**命名のみ**・形は未定義）。X-rmf 経路（`01`:204）= `Bridge → MCP/Policy Gate → Open-RMF Task API → Fleet Adapter → Nav2`。Compiler 側は `ExecutionProfile` で分け `RmfTaskCompiler` を将来 plugin 追加（`02`:234,240）。`(発明/要確定)`: `temporary_waypoint` の field 構造・Open-RMF task submission envelope・座標 goal 全経路 contract は**一切提案しない**（XER7 後に別 contract PR）。

**additive 互換**: 凍結時は additive-first 前提。現方針は `warehouse_interfaces` を変更せず RMF Navigation Graph/waypoint/lane/task schema を契約外に保つ（rmf_adapter CLAUDE.md:49,60）。X-lite が既存 `Command`（known_location のみ）で動くため X-rmf seam は将来の additive 拡張で既存購読者が無視できる。座標は `KNOWN_LOCATIONS` に足さない（config 由来・暫定値）。

**contract PR 手順**: 今は PR を出さない（DEFER）。**解錠ゲート連鎖**: ① #187 R-38 メモリゲート（OPEN・Go/No-Go 未確定。[`shared/07-research-notes.md`](../shared/07-research-notes.md):243 / [`mode-c/11c`](../mode-c/11c-traffic-mode-c.md):273・466。No-Go なら Mode C 自体が Mode B 格下げ＝X-rmf seam も不要化 rmf_adapter CLAUDE.md:87-88）→ ② Mode C Fleet Adapter live/sim → ③ XER6 X-lite E2E（`README`:91）→ ④ XER7 評価（`README`:92）。価値ありなら `contract` ラベル PR ＋ 依存トラック（llm-bridge / nav-traffic #180 / safety-state）予告。

---

## 7. OperatorNotice schema ＋ /operator/notice topic — DEFER(gate)（案A 採用方針のみ確定）

**現状（docs）**: [`05`](05-operator-feedback-and-voice-response.md):5 が「設計提案・未凍結（ROS topic/REST/config/contract を凍結しない・別 contract PR で確定）」。本ブロッカーは**2つの別物**（doc05 §8.8/§8.9 が別物性を明記）:
- (1) box の**出力** `OperatorNotice`（喋った文面+音声 ref。fields `box, reason_code, locale, text, severity, source_decision_ref`＝`05`:279）。`05`:279「product contract に昇格するか未決・`warehouse_interfaces` にはまだ追加しない」。
- (2) box の**入力**を運ぶ別ノード event チャネル＝専用 topic `/operator/notice`（**案A・`05`:194 で確定**）。`05` §8 に doc03 契約ドラフト一式（型 `std_msgs/String`(JSON)・payload `operator_notice.v0` `05`:312-334・QoS RELIABLE/KEEP_LAST depth=20/VOLATILE `05`:336-345・pub/sub）。`05`:292「doc03 への行追加は別 contract PR（owner=skeleton/governance）」。
- [`doc03`](../architecture/03-software-architecture.md):112「doc03 は topic 名・型・一行責務のみ」。grep: `/operator/notice` は ws/src・config・doc03 に**未存在＝完全 net-new**。`decision` 固定語彙（[`productization/05`](../productization/05-decision-observability-and-tooling.md):69）は `accepted/rejected/warning/needs_clarification/emergency_stop` のみで **milestone(arrived/completed) を含まない**。

**判定 / 根拠**: **DEFER(gate=XER-OF1 + offline fixture)**。`05`:5 が凍結条件を「offline fixture と契約 PR で確定」と明示し、現状は実装ゼロ proposal（XER-OF0 のみ）で `05` §8.8(:369-376)に topic 名最終確定・depth 値・MVP publisher・emergency 二重化・schema_version の5未決が残る。今 FREEZE すると未検証の `depth=20`（:341「暫定・実装時調整」）を凍結契約に焼く。**ただし確定しているのは案A（専用 topic）の採用方針のみ**（`05`:194）。型（`std_msgs/String` JSON）・payload・QoS・topic 名・`schema_version`・emergency 扱いは `05` §8 の **contract draft＝未凍結**（`05` §8.8:369-376 に未決）。DEFER は方式の再議論でなく**未決値の充填と fixture 実証待ち**。

**提案形（既存 doc05 案の統合のみ）**:
- **topic**（doc03「Jetson 内部」表へ additive 1 行・`05`:299）: 型 `std_msgs/String`(JSON)（doc16 §3 の Phase 4 まで JSON 文字列方針・`05`:310）、一行責務「別ノード(L2/L1/L0)の operator 起因 reject/clarification/emergency 通知。L4 Operator Feedback Box が購読し音声化」。
- **payload `operator_notice.v0`**（`05`:312-334・既存 decision_event 形を消費・新語彙発明なし）: `schema_version, timestamp, run_id, gen_id, robot, box, stage, decision, reason_code, reason_detail, message_for_operator?`。`decision` は **`rejected/needs_clarification/emergency_stop` のみ**（accepted/warning は流さない・`productization/05`:69）＝**v0 は reject 級のみ**。
- **QoS**（`05`:336-345）: RELIABLE（lossless）/ KEEP_LAST depth=20（**暫定**）/ VOLATILE（再起動跨ぎの stale 再生防止）/ AUTOMATIC。
- **publish-only=no-actuation 不変条件**（`05`:269 L4OF-G1・§3 `05`:100,107）: feedback box は motion/tool dispatch を一切 emit しない（reject fixture で assert）。gate は publish して即継続＝TTS を待たない非ブロッキング・上位非依存。
- `(発明/要確定)`（doc05 §8.8 が未決と明記）: topic 名最終確定（`/operator/notice` vs `/operator/reject_event`・`05`:371）/ depth 値 / MVP 実 publisher / emergency を `/emergency/event` 相乗りか二重化か（`05`:374）/ `schema_version` 凍結。**milestone(arrived/completed) は現 `decision` 固定語彙に無い**ため v0 では運べず、喋るなら別 event 語彙/別チャネルが要る（`05`:376）＝v0 は reject 級に scope。

**additive 互換**: 完全 additive。新 topic は既存購読者ゼロ（grep 確認）＝無視可（`05`:380 additive チェック）。payload は既存 decision_event 形を消費するだけで新語彙発明なし。doc03 は1行追加のみで既存行不改変。VOLATILE で late-join/再起動後の古い reject 再生を回避。milestone を喋る決定をしても v0 を壊さず別語彙追加＝additive。

**contract PR 手順**: 2段の別 PR。**[本体]** doc03 トピック契約 PR（`contract` ラベル・track:skeleton/governance 所有・`05`:292）: doc03「Jetson 内部」表（doc03:90-110）に1行追加（payload/QoS/pub-sub 正本は `05` §8 へリンク）→ **マージ前に依存トラック safety-state/nav-traffic/wo/web へ予告し合意**（publisher 候補=mcp_server/traffic/rmf_adapter/nav2_bridge/safety/firmware、subscriber=llm_bridge feedback sub-box）→ OperatorNotice(出力) と本 topic(入力) の別物性を本文明記。**凍結前に XER-OF1 offline fixture で payload 実証**。**[別 PR]** `OperatorNotice`(box 出力 schema) の `warehouse_interfaces` 昇格は `05`:279「まだ追加しない」＝別 PR・別タイミング。taxonomy 正本登録（`productization/01` の box 一覧）は box-map owner 調整の別 PR。

---

## 8. contract PR ロードマップ（凍結タイミングと順序）

```
[現在 = XER0 docs]
   │
   ├─ #4 FREEZE-NOW(MVP=known location 限定)  ── docs/* PR（track:docs・コード不触）  ← 今すぐ可
   │
   ├─ #5 NEEDS-PROBE → RESOLVED（2026-06-26〜27）  ── PROBE-1/2/3 実測済 → §5 実測結果で確定（音声=direct ER / text+image=Hermes or direct）
   │
   ├─ XER1 (offline fixture: RoboticsPlan draft 安定)
   │     └─ XER2 (Validator + ValidationReport)
   │           └─ #1 DEFER 解錠 → contract PR（RoboticsPlan/ValidationReport 昇格・依存トラック予告）
   │
   ├─ Model Adapter skeleton 着地（track:llm-bridge）
   │     └─ #2 DEFER 解錠 → bridge-local enum 凍結（contract ラベル不要）
   │
   ├─ XER0 docs 確定 → XER3 着手前
   │     └─ #3 DEFER 解錠 → docs/* PR(凍結形) → base.yaml additive PR（bringup/skeleton 予告）
   │
   ├─ XER-OF1 (operator-feedback offline fixture)
   │     └─ #7 DEFER 解錠 → doc03 トピック contract PR（案A・全トラック予告）
   │
   └─ #187 R-38 → Mode C Fleet Adapter live → XER6 X-lite E2E → XER7 X-rmf eval
         └─ #6 DEFER 解錠（価値ありなら）→ contract PR（temporary_waypoint/task schema・発明は解錠後）
```

| # | いつ凍結 | PR 種別 / ラベル | 所有・予告先 |
|---|---|---|---|
| 4(MVP) | 今すぐ | `docs/*`（track:docs・コード不触） | mode-x-er docs |
| 5 | **probe 実測済（2026-06-26〜27・RESOLVED）** | `docs/*`（probe 結果反映＝本 PR）→ 昇格時 `contract` | track:docs → llm-bridge |
| 1 | XER1-2 後 | `contract` + track | skeleton(#1 owner)・llm-bridge・safety-state |
| 2 | adapter skeleton 着地時 | **contract 不要**（bridge-local） | llm-bridge |
| 3 | XER0→XER3 前 | `docs/*` → base.yaml additive | bringup/skeleton・nav-traffic・web |
| 7 | XER-OF1 後 | `contract`（doc03 topic） | skeleton/governance・safety-state/nav-traffic/wo/web |
| 6 | #187→ModeC live→XER6→XER7 後 | `contract`（価値確認後） | nav-traffic(#180)・llm-bridge・safety-state |
| 4(coord) | coordinate ユースケース確定後 | `docs/*` → `contract` | bridge(nav2_bridge+mcp_server) |

---

## 9. docs-first 監査メモ（本書の自己検証）

本書の提案は隔離コンテキストの docs-first 監査（実 Read/grep で裏取り）を通した。要旨:
- **decision_disagreements / missing_additive: なし**。7項目の判定（DEFER×4 + FREEZE-NOW/DEFER 二分×1 + NEEDS-PROBE×1）は docs 方針と整合。**#5 の NEEDS-PROBE は 2026-06-26〜27 の PROBE-1/2/3 実測で RESOLVED 済（§0・§5・§8）**。
- 提案形の値は全て docs 由来。発明された**梱包**（pydantic クラス名 `ProviderType`/`Transport`・config ブロック名 `mode_x_er:`・calibration file パス/形式・calibration_id 命名拡張）は各所で `(発明/要確定)` と明記＝凍結 docs 値と発明を分離。
- 監査指摘の反映: ① `accepted` の正確位置は `02`:61（`02`:60 は開き `{`）に修正。② ValidationReport の `rejected` は docs に status literal が無い（docs は `accepted`(`02`:61)・`needs_clarification`(`02`:79,84) のみ・`02`:68 は「status != accepted」）→ 本書は「**reject 系 status 文字列は要確定**」と明記（§1）。

---

## 参照

- 正本: [README](README.md):94-104 / [01](01-architecture-and-flow.md) / [02](02-l3-planning-core.md) / [03](03-er-adapter-skeleton.md) / [04](04-er-input-modalities-and-stt.md) / [05](05-operator-feedback-and-voice-response.md) / [productization/02](../productization/02-l4-robotics-bridge-box.md):279-284
- コード慣習: [`schemas.py`](../../ws/src/warehouse_interfaces/warehouse_interfaces/schemas.py)（`_Model` extra=ignore:24-25・`CommandAction` StrEnum:135・`start_negotiation` additive:194）/ [`config.py`](../../ws/src/warehouse_interfaces/warehouse_interfaces/config.py):114-131 / [`nav2_bridge/core.py`](../../ws/src/warehouse_nav2_bridge/warehouse_nav2_bridge/core.py):39,124-148 / [`policy_gate.py`](../../ws/src/warehouse_mcp_server/warehouse_mcp_server/policy_gate.py):47-52
- ルール: [docs-first.md](../../.claude/rules/docs-first.md) / [parallel-workflow.md §4・§7](../../.claude/rules/parallel-workflow.md) / [issue-and-pr-authoring.md](../../.claude/rules/issue-and-pr-authoring.md) / [environments.md](../../.claude/rules/environments.md)
- web 一次情報（取得 2026-06-25）: `https://ai.google.dev/gemini-api/docs/robotics-overview`（ER 1.6 Inputs=Text/images/video/audio）/ `https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server`（非 image data: は 400）/ `https://hermes-agent.nousresearch.com/docs/user-guide/features/vision`
