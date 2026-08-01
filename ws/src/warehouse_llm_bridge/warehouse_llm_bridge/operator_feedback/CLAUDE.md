# operator_feedback — L4 Operator Feedback Box (offline core + runtime node, XER-OF1/OF2/OF2.5)

> 拒否/要確認/緊急の `decision_event` を「どの箱の・どの理由か」人向け文面へ変換する
> **deterministic（model 不要）offline notice builder** ＋ それを駆動する **subscriber runtime
> node**（`notice_node.py`）。**0 actuation（R-26 / L4OF-G1）**＝gate 側は publish-only、node 側は
> subscribe-only。**実 TTS provider は XER-OF3 で未配線・DEFER**、launch 配線も follow-up。

- **担当トラック / ブランチ**: `feat/operator-feedback`（track #345・Part of epic #336）
- **Phase**: Mode X-ER / XER-OF1（doc05 §5.5 :249-260）
- **編集境界**: この `operator_feedback/` 配下＋`tests/unit/test_operator_feedback_*.py`。
  step②（contract PR #446）は追加で **doc05 §8.10（確定値）＋ doc03「Jetson 内部」表 1 行 additive**
  を触った（`contract` ラベル）。**step③（subscriber node）は追加で `setup.py` の console_scripts に
  1 行 additive**（entry_point `operator_feedback`）。`robotics_planning_core/`（Lane A）・
  `warehouse_interfaces`・`llm_bridge.py`・config・`productization/01` Box 表・
  `warehouse_bringup/launch/**`（launch 配線＝nav-traffic 所有）は触らない。
- **依存**: offline core（`models`/`notice_builder`/`templates_ja`/`scope_filter`/`feedback_box`/
  `sinks`/`publisher`）は**標準ライブラリのみ**（`dataclasses` / `typing`）。`notice_node.py` のみ
  rclpy / `std_msgs` を **import-guard 付き**（`x_er_bridge.py:72-101` と同型）で参照し、pure な
  driver / wiring seam は ROS 無し host で検証可能（doc16 §11）。pydantic / 他トラック内部モジュール
  は import しない。

## 設計ドキュメント（正本・file:line）

- `docs/mode-x-er/05-operator-feedback-and-voice-response.md` — proposal/未凍結 `:5,14` /
  「入力理由 vs 箱自身の失敗」`:109` / box manifest `:116` / 保管場所 §5.4 `:229-231` /
  fixtures §5.5 `:256-258` / gates §6 `:266-273`（L4OF-G1 0 actuation `:269`） /
  contract draft §8 `:292-345`（payload `:312-334`・`decision` 3 値 `:332`・attribution `:334`）/
  未凍結 §8.8 `:369-376`。
- `docs/productization/05-decision-observability-and-tooling.md:48-71`（decision_event 形・
  `decision` 固定語彙 `:69`・reason_detail `:71`）— **consume only・新語彙発明禁止**。
- `docs/mode-x-er/02-l3-planning-core.md:95-96,319-345`（RuleResult・stable 9 code・
  code→decision 早見表 `:338-343`）。
- `docs/mode-x-er/02-l3-planning-core.md:240-266` 系の保管単位案 →
  `docs/productization/02-l4-robotics-bridge-box.md:240-266`（module 配置の接地）。
- `docs/mode-x-er/06-unfrozen-contract-resolutions.md` §7 `:186-205`（#446 で **RESOLVED**・
  確定値の正本は doc05 §8.10・凍結成立は依存トラック合意後）。

## 消費する契約（consume）

- **gate `decision_event`**（read-only・`std_msgs/String` JSON を decode した dict 想定）。
  形は doc05 §8.4 draft `operator_notice.v0`（`schema_version, timestamp, run_id, gen_id, robot,
  box, stage, decision, reason_code, reason_detail, message_for_operator?`）。`extra=ignore`
  で未知キーは drop（`DecisionEvent.from_payload`）。**未凍結 draft**（doc05:5）。
- **consume する語彙**（発明しない）: `decision` ∈ `accepted/rejected/warning/needs_clarification/
  emergency_stop`（productization/05:69）/ L3 `code` 9 種（mode-x-er/02:319-328）/ box id・
  L2/L1/L0 reason_code（doc05 §1 :30-36, §8.6 :351-357）。

## 生産する契約 / IF（produce）

- **`OperatorNotice`**（offline 出力・doc05:279 の `box, reason_code, locale, text, severity,
  source_decision_ref`＋内部 `fallback` フラグ）。**`warehouse_interfaces` に追加しない・DEFER**
  （doc05:279「まだ追加しない」）。`text` のみが人向け文面、`source_decision_ref` は attribution
  参照（raw data を埋めない・doc05:334）。
- **`build_notice(decision_event) -> OperatorNotice | None`**（pure・deterministic・LLM 不使用）。
  reject 級 decision のみ notice 化、それ以外（accepted/warning/milestone）は `None`。
- **`ScopeFilter`**（XER-OF2.5）: `gen_id`/live-command 相関＋lifecycle＋重複抑制で
  「命令外の自律停止・高頻度 tick・milestone」を黙らせる（doc05 §5.3）。
- **`OperatorFeedbackBox.notify(...)`**（filter→build→**fail-open** deliver）＋`audit_log`。
  sink は注入 IF（`NoticeSink` Protocol / callable）。sink 失敗は raise せず fallback
  （XER-OF2 / L4OF-G2・doc05:270）。**0 actuation**: 出力は notice/None/AuditRecord のみ。
- **`OperatorNoticePublisher`**（`publisher.py`・gate-side emit seam・doc05 §8 / §8.10）: 別ノード
  gate の decision_event を `operator_notice.v0` JSON（`to_v0_payload`/`encode_notice`）に直列化し
  **`/operator/notice`**（`TOPIC_OPERATOR_NOTICE`・`std_msgs/String`）へ publish。**publish-only=0
  actuation**（R-26 / L4OF-G1・doc05:269）: 出力チャネルは注入 `publish` callable 1 本のみ。**wire
  語彙 = `WIRE_NOTICE_DECISIONS`（`rejected`/`needs_clarification`）**で、box の SPEAK 語彙
  `SPEAKABLE_DECISIONS` とは別＝**emergency は既存 `/emergency/event` 相乗りで `/operator/notice`
  へ二重 publish しない**（doc05:332・§8.10 item4 / doc03:111）。ROS は注入（`for_ros_node` は lazy rclpy・
  runtime のみ）で offline 検証可（`sinks.py` と同 injection 規律）。QoS 確定値: RELIABLE /
  KEEP_LAST `NOTICE_QOS_DEPTH=10` / VOLATILE（doc05 §8.5・§8.10 item 2）。
- **`OperatorNoticeDriver` / `wire_notice_subscriptions` / `OperatorFeedbackNode`**（`notice_node.py`・
  **subscriber runtime node**・doc05 §8.10 item4 `docs/mode-x-er/05-operator-feedback-and-voice-response.md:395`）:
  **`/operator/notice`**（`TOPIC_OPERATOR_NOTICE`・QoS = `build_notice_qos()`）と
  **`/emergency/event`**（`TOPIC_EMERGENCY_EVENT`・`std_msgs/String` JSON・QoS = RELIABLE/KEEP_LAST/
  `EMERGENCY_QOS_DEPTH=10`＝既存 publisher `warehouse_safety/emergency_guardian.py:117` と
  `warehouse_state/state_cache.py:59-61,93` に**一致させただけ**）を購読し、box（filter→template→sink）を駆動する。
  **SUBSCRIBE-ONLY = 0 actuation**（R-26 / L4OF-G1・doc05:269）: `wire_notice_subscriptions` は
  subscription を 2 本作るだけで **publisher / service client / action client を一切作らない**。
  - `emergency_event_to_decision`: `/emergency/event`（凍結コア形 `event_id/robot/type/severity/
    action_taken/timestamp/requires_llm_review`・`docs/architecture/12-infrastructure-common.md:232-240`）を
    **in-process** で `decision=emergency_stop` / `box=safety` / `reason_code="emergency"`（doc05 §1:30-36・
    §8.6:356）へ写像。**`type`→`reason_code` の逐語表は docs に無い**ので発明せず、`type` は
    `reason_detail`（人向け補足・productization/05:71）で運ぶ。`event_id`/`severity`/`action_taken`/
    `requires_llm_review` は v0 に landing 先が無く drop。
  - `/operator/notice` に `emergency_stop` が来たら **drop**（`dropped_off_wire_emergency`）＝
    二重発話の防止（doc05 §8.10 item4 / §8.7:366）。非 reject 級（accepted/warning/milestone）は
    drop せず box に渡し、ScopeFilter が理由付きで suppress（audit 保持・doc05:227）。
  - **非ブロッキング drain**（doc05 §8.5:345）: callback は decode＋enqueue のみ、render/sink は
    daemon worker の `drain()` 側。TTS が callback を塞がず RELIABLE back-pressure が gate に伝播しない。
  - **fail-open**（doc05:270 / productization/05:290）: 不正 payload は count＋log して drop
    （`state_cache.py:120` 前例）、delivery 例外は `delivery_errors` に計上して drain を継続。
  - sink は注入（`LoggingNoticeSink`＝runtime の stand-in speaker ＋ `RecordingSink` fallback）。
    **実 TTS は XER-OF3 で未実装**。
- **`LoggingNoticeSink`**（`sinks.py`）: 注入 log callable へ `severity` + `text` のみを書く sink（0 actuation）。
- **box 自身の event 語彙**（`box=l4_operator_feedback`・audit/fail-open 用・doc05:103）:
  `decision` ∈ `spoken/fell_open/suppressed`、`reason_code` ∈ `tts_failed/sink_unavailable`＋
  suppression 理由（`non_speakable_decision/uncorrelated_autonomous/duplicate_suppressed`）。
  これは**箱自身の失敗/抑制の内部 audit ラベル**で凍結契約ではない（doc05:109 の「入力理由」と別物）。

## 内部派生（NOT frozen）

- **`severity`**（`emergency/error/warning`）と **`dispatch`-相当の suppression reason** は
  `decision` から内部派生したラベル（`dispatch_effect` が内部派生なのと同型・mode-x-er/02:315・
  doc06 §7 :53）。`warehouse_interfaces` へ昇格しない。

## テスト（host・colcon 不要）

- `tests/unit/test_operator_feedback_builder.py` — determinism / golden（各 gate）/ decision
  filter / unknown→safe fallback / L4OF-G4 / severity。
- `tests/unit/test_operator_feedback_safety.py` — **R-26 / L4OF-G1（0 actuation）** ＋
  **XER-OF2 fail-open**（sink 例外で run 継続）。
- `tests/unit/test_operator_feedback_filter.py` — **XER-OF2.5 / L4OF-G5**（attribution・
  milestone・重複抑制・suppressed の audit 保持）。
- `tests/unit/test_operator_feedback_node.py` — **R-26 / L4OF-G1（subscribe-only=0 actuation）**:
  fake node の `create_publisher`/`create_client` が呼ばれたら AssertionError ＋ 2 subscription のみ。
  ＋ **二重経路ガード**（`/operator/notice` の `emergency_stop` は drop・両 wire に来ても notify 1 回）
  ＋ **非ブロッキング drain**（callback では sink/audit に触れない）＋ **fail-open**（不正 payload・
  delivery 例外）＋ **emergency 写像**（独立 oracle = doc12 コア形の literal）＋ **publisher→wire→
  subscriber 往復**。mutation で RED を実測: 二重 publish ガード除去 / callback 内 drain /
  `type`→reason_code 表の発明 / wiring への publisher 混入 / emergency QoS depth ドリフト。
- `tests/unit/test_operator_feedback_publisher.py` — **R-26 / L4OF-G1（publish-only=0 actuation）**
  ＋ 確定契約値（topic/QoS depth=10/schema_version）＋ **wire 語彙（reject 級−emergency・§8.10 item4）**
  ＋ fake-ROS 配線 ＋ publisher 出力＝box 入力の往復一致（producer/consumer 同形）。independent oracle
  （SPEAK/WIRE 語彙の literal pin）＋ emergency 二重 publish guard の除去 mutation で RED。
- 実行: repo root から `python3 -m pytest tests/unit/test_operator_feedback_*.py`
  （target py312。conftest が `ws/src/warehouse_llm_bridge` を sys.path へ追加）。

## 確定（本 contract PR = step②・doc05 §8.10）

- topic `/operator/notice`（`std_msgs/String`(JSON)・QoS RELIABLE/KEEP_LAST **depth=10**/VOLATILE・
  `schema_version="operator_notice.v0"`・MVP publisher = nav2_bridge/mcp_server・emergency は
  `/emergency/event` 相乗り）を doc05 §8.10 で**確定**し、doc03「Jetson 内部」表へ 1 行 additive。
  gate-side emit adapter `OperatorNoticePublisher` を配線（publish-only）。**凍結成立は依存トラック
  （safety-state/nav-traffic/wo/web）合意後**（Draft PR・`Refs #345`・§8.9）。

## 実装済（step③ = subscriber runtime node・doc05 §8.10 item4）

- `notice_node.py`（両 topic 購読 → box 駆動・subscribe-only=0 actuation）＋ `setup.py`
  console_scripts に `operator_feedback = warehouse_llm_bridge.operator_feedback.notice_node:main`。
  **launch 配線（`warehouse_bringup/launch/bringup.launch.py`）は含まない**＝共有ファイル・
  nav-traffic 所有ゆえ follow-up（`.claude/rules/parallel-workflow.md` §7.1）。

## 未凍結 / DEFER（別 owner・後続 slice）

- **live command 相関の供給源が未決（最重要）**: doc05 §5.3 :205-208 の speak 式は
  `gen_id ∈ {live な operator 命令}` を要求するが、**別プロセスの subscriber node にその集合を
  供給する経路が docs に無い**（doc05:202 は L3→action_map の in-process 相関を前提）。さらに
  凍結 `/emergency/event` コア形（doc12:232-240）は `gen_id`/`run_id` を持たない。結果として
  現状は `/operator/notice`・`/emergency/event` 双方が `uncorrelated_autonomous` で suppress
  （audit は保持）＝doc05:200 の「黙る例」に一致する。**発明せず**、seam
  （`OperatorNoticeDriver.register_live_command` / `ScopeFilter` 注入）だけ用意した。解錠には
  (a) node を x_er_bridge に in-process composition する、(b) `/emergency/event` へ
  `gen_id`/`run_id` を additive 追加（safety-state 所有・contract PR）、(c) doc05 §5.3 に
  emergency の filter-bypass policy を追記、のいずれかの **docs 決定**が要る。
- **`/emergency/event` の `type` → `reason_code` 逐語表が docs に無い**（doc05 §8.6:356 は
  `emergency`（near_collision / pose_stale）と示すのみ）。`type` は `reason_detail` で運び、
  `battery_critical`/`blocked_timeout` 専用文面は作らない（templates は fallback 経路を持つ）。
- `/emergency/event` の `event_id`/`severity`/`action_taken`/`requires_llm_review` は
  `operator_notice.v0` に landing 先が無く drop（attribution 強化が要るなら v1 の additive 判断）。
- 実 gate node（nav2_bridge/mcp_server）への publisher 配線 = 所有トラック follow-up。
- `OperatorNotice` の `warehouse_interfaces` 昇格 / `productization/01` Box 一覧登録 /
  観測 funnel への `l4_operator_feedback` 追加 = 別 owner（box-map / Eval-Obs）調整。
- EN locale テンプレート（`templates_en`）・実 TTS sink（XER-OF3）・web 併走 sink（XER-OF4）=
  後続 phase。
