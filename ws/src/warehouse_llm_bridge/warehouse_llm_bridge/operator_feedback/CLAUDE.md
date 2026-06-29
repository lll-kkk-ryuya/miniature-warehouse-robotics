# operator_feedback — L4 Operator Feedback Box (OFFLINE core, XER-OF1/OF2/OF2.5)

> 拒否/要確認/緊急の `decision_event` を「どの箱の・どの理由か」人向け文面へ変換する
> **deterministic（model 不要）offline notice builder**。**publish-only = 0 actuation（R-26 /
> L4OF-G1）**。本 module は `#345` が authorize する **offline 部のみ**。runtime ROS node /
> topic / TTS provider は **未配線・DEFER**。

- **担当トラック / ブランチ**: `feat/operator-feedback`（track #345・Part of epic #336）
- **Phase**: Mode X-ER / XER-OF1（doc05 §5.5 :249-260）
- **編集境界**: この `operator_feedback/` 配下＋`tests/unit/test_operator_feedback_*.py` のみ。
  `robotics_planning_core/`（Lane A）・`warehouse_interfaces`・`doc03`・config・
  `productization/01` Box 表は触らない。
- **依存**: 標準ライブラリのみ（`dataclasses` / `typing`）。pydantic / rclpy / ROS / 他トラック
  内部モジュールを import しない（疎結合・colcon なしで host 検証可能・doc16 §11）。

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
- `docs/mode-x-er/06-unfrozen-contract-resolutions.md` §7 `:186-200`（案A 採用方針のみ確定・
  型/QoS/topic名/schema_version は未凍結 draft）。

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
- 実行: repo root から `python3 -m pytest tests/unit/test_operator_feedback_*.py`
  （target py312。conftest が `ws/src/warehouse_llm_bridge` を sys.path へ追加）。

## 未凍結 / DEFER（contract PR・別 owner）

- topic `/operator/notice`（名前・型 `std_msgs/String`(JSON)・QoS RELIABLE/KEEP_LAST depth=20
  /VOLATILE・publisher）= doc03 contract PR（owner=skeleton/governance）。本 module は **配線しない**
  （doc05 §8.8 :369-376・doc06 §7 :186-200）。
- `OperatorNotice` の `warehouse_interfaces` 昇格 / `productization/01` Box 一覧登録 /
  観測 funnel への `l4_operator_feedback` 追加 = 別 owner（box-map / Eval-Obs）調整。
- EN locale テンプレート（`templates_en`）・実 TTS sink（XER-OF3）・web 併走 sink（XER-OF4）=
  後続 phase。
