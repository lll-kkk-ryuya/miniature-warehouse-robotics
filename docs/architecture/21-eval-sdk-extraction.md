# Eval SDK 抽出 — `eval_sdk`（再利用可能な embodied-AI 評価基盤・提案）

作成日: 2026-06-15

> **位置づけ（PROPOSAL / docs-first ゲート）**: 本書は**コードを書く前に land する設計提案**である（[docs-first.md](../../.claude/rules/docs-first.md) の必須ゲート）。現状の評価機構（Langfuse trace/score・KPI）を**ドメイン非依存の薄いパッケージ `eval_sdk` として抽出**し、倉庫をその最初の利用者にするための方針・境界・非目標・段階を定める。
> 正本リンク: 比較指標/Langfuse = [doc08](08-llm-bridge-common.md)、観測 taxonomy = [doc20 §8](20-dev-quality-and-testing.md)、純コア規約 = [doc16 §11](16-repository-and-conventions.md)、trace_id 契約 = [doc13 §7.5](13-hermes-setup.md)、audit = [doc15](15-mcp-platform.md)、契約変更 = [parallel-workflow.md §4](../../.claude/rules/parallel-workflow.md)。
> **本書は提案であり、ここに書く `eval_sdk` の API/スキーマはまだ凍結契約ではない**（実装着手は本書 land 後）。

---

## 0. 要約（TL;DR）

- **分離すべきか → YES。ただし「今はリポジトリでなくパッケージ」**。`ws/src/eval_sdk/`（**ROS/warehouse 依存ゼロ**・pip 化可能）として抽出し、**倉庫で二重利用して実証**する。
- **別リポジトリへ昇格するのは → 本物の利用者#2が出た時**（rule-of-three）。境界が既にクリーンなので、その時の分割は機械的（`git filter-repo` 一発に近い）。
- **作る実体 → 巨大プラットフォームでなく5つの薄いモジュール**（`seed` / `tracer` / `sink` / `stats` / `cost`）＝**既にテスト済・脱ROS・fail-open** のコードのリネーム＋重複解消。
- **最初の一手 → コードでなく本 doc（提案）を land**（docs-first・このプロジェクトの load-bearing ルール）。
- **背骨（fail-open + lazy-import + 依存注入）を verbatim で保つ** —— これが「SDK 無しでテスト可能」と「DX が Langfuse 級に感じる」を同時に成立させる核。

> なぜ「薄く」か: 実利用者は現在 **1.0人**（倉庫＝実在 / 「将来の Physical AI」＝payload スキーマも seed も無い仮説）。Run/Episode/Step 階層・metric registry・Parquet exporter 等は**推測で形を決めた抽象**になり必ず外す。rule-of-three（2〜3の実利用者が出るまで抽象を凍結しない）を厳守し、**今は5モジュールだけ**を抽出する。

---

## 1. 決定

| 問い | 決定 |
|---|---|
| 分離すべきか | **YES**。ただし**リポジトリでなく in-repo パッケージ** `ws/src/eval_sdk/`。 |
| いつリポジトリ分割か | **利用者#2が実在した時**（rule-of-three）。それまで incubate。 |
| 作る実体 | **5モジュール**（`seed`/`tracer`/`sink`/`stats`/`cost`）。抽出＝リネーム＋重複解消であって発明ではない。 |
| 最初の一手 | **本 doc を land**（docs-first）。コードは後。 |
| 不変条件 | **背骨 = fail-open + lazy-import + 依存注入** を verbatim 維持。 |

---

## 2. なぜこの形か

1. **リポジトリは既にこの基盤の 60〜70% を払い終えている**。評価機構は `warehouse_orchestrator` / `warehouse_llm_bridge` 内にドメイン名で埋まっているが、**既に純粋・バックエンド無しで単体テスト済・fail-open**（[doc16 §11](16-repository-and-conventions.md) の純コア規約）。抽出コストは低い。
2. **観測の土台は Langfuse / OTel に乗る**（再発明しない）。Langfuse = 保管庫（trace/observation/score・dashboard）、OTel GenAI semconv = 配線、nav 指標式（SR/SPL/collision/intervention）= 語彙。`eval_sdk` が作るのは**その上の薄い結合層**だけ。
3. **早すぎる分離はコスト過多**。API がまだ流動的（Phase 3/4・`result` 値写像・KPI 出力契約が未凍結）な段階で別リポジトリ化すると cross-repo リリースの往復が co-evolution を殺す。**境界を今クリーンにして倉庫で揉ませ、API を実利用者に形作らせる**。

---

## 3. 3層アーキテクチャ

```
┌─────────────────────────────────────────────────────────────┐
│ (a) 乗る層 — 作らず buy                                        │
│   OTel GenAI semconv ──emit──▶ Langfuse(Trace→Obs→Score)    │
│   nav 指標式（SPL/SoftSPL/SR/collision/intervention）= 文献    │
│   Inspect-AI(Task=dataset+solver+scorer) ※パターンのみ・任意  │
└──────────────▲──────────────────────────────────────────────┘
               │ import（fork しない）
┌──────────────┴──────────────────────────────────────────────┐
│ (b) eval_sdk — ドメイン非依存コア（★作る・5モジュール）         │
│   seed.py    derive_trace_id(seed="run:work") ← 凍結join key   │
│   tracer.py  Tracer/NoopTracer/LangfuseTracer                  │
│   sink.py    FailOpenScoreSink + DataType + ScoreSpec(名前予約) │
│   stats.py   percentile/path_length/accumulator/completion     │
│   cost.py    token-cost（価格表は注入）                          │
│   背骨(verbatim): fail-open + lazy-import + 依存注入            │
│              → SDK 0 / ROS 0 で単体テスト可能                   │
└──────────────▲──────────────────────────────────────────────┘
               │ 名前登録・payload供給・producer配線
┌──────────────┴──────────────────────────────────────────────┐
│ (c) ドメインプラグイン — 倉庫（利用者#0・凍結契約不変）          │
│   warehouse_interfaces(Situation/Command/gen_id) = payload     │
│   kpi.py compute_kpis / ResultTally / 7-MCP-tool 語彙 = KPI定義 │
│   SCORE_* 名(efficiency/deadlock…) = ドメイン manifest          │
│   Emergency Guardian/collision_monitor/0.3m/s = 信号 producer   │
│   Mode A/B/C・Mode C no-actuation(R-26)                        │
└─────────────────────────────────────────────────────────────┘
```

- **核心** = 決定的・内容非依存の join key。あらゆる emitter（sim・scorer・Langfuse sink）が**データから同じ id を再導出**する `derive_trace_id(seed="run:work")`。これは live-join バグ（#108/#109→#115 で修正）を直した性質そのもの。**2階層のまま**（emitter が出さない `episode` 階層に拡張しない）。
- **eval_sdk は「数学・emit・join-key の素材」だけを提供**し、「intervention_rate」「deadlock」等の**指標定義・データ producer はドメイン（c）に残す**。eval_sdk は倉庫を知らない。

### 3.1 データフロー（producer → eval_sdk → Langfuse）— 三者の関係

§3 の3層を「**静的な依存**」でなく「**データの流れ**」で見ると、三者の役割分担が明確になる。

```
┌─ ① producer（ドメイン=倉庫・信号の発生源・各 sim/研究で差し替える部分）──────────┐
│  LLM Bridge ─▶ 判断(situation/command)     Nav2 ─▶ /bot{n}/odom               │
│  MCP audit.jsonl(executed/rejected/error)  Emergency Guardian ─▶ near_collision │
│  deadlock detector ─▶ State Cache          （Tier2 で観測専用 producer が増える）│
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │ 生信号(raw events / pose / tokens)
                               ▼
┌─ ② eval_sdk（汎用=一度作れば再利用・倉庫を知らない）──────────────────────────┐
│  seed  : run_id:work → 決定的 trace_id（判断と結果を"同じ1本"に結合）            │
│  stats : 生信号 → 指標（SR/SPL・介入率・jerk・percentile・距離 …）             │
│  cost  : tokens → USD                                                          │
│  tracer: 1判断 = 1 trace を開く（generation + tool span）                       │
│  sink  : score を fail-open 送信（落ちても走行は無傷）                           │
└──────────────────────────────┬───────────────────────────────────────────────┘
                               │ trace + score（すべて同じ trace_id でひも付く）
                               ▼
┌─ ③ Langfuse（既製=乗るだけ・保管 + ダッシュボード）───────────────────────────┐
│  trace "a3f…" = 【判断】generation(input/output/latency/cost)                  │
│               + 【結果】score(SR/SPL/efficiency/deadlock/collision_free …)     │
│  → 4 provider × 3 mode 比較ダッシュボード / Metrics API / 可搬 export(JSONL)    │
└───────────────────────────────────────────────────────────────────────────────┘

   #4 LLM Bridge ─┐                                   ┌─ 【判断】を書く
                  ├─ eval_sdk.sink（同じ trace_id）─▶ │   （= 同じ1本の trace に
   #6 wo KPI ─────┘                                   └─ 【結果】を書く     別々の半分）
```

**三者の役割と所有**:

| 層 | 役割 | 所有/再利用性 | 各プロジェクトでどうなるか |
|---|---|---|---|
| ① producer | **生信号を出す**（判断・pose・event・tokens） | ドメイン（倉庫固有） | **差し替える**（sim/研究ごとに別 producer） |
| ② eval_sdk | **生信号→指標へ変換・join-key 付与・emit** | 汎用（★これを作る） | **そのまま再利用**（接続するだけ） |
| ③ Langfuse | **保管・可視化・比較** | 既製 SaaS | **乗るだけ**（差し替え不要） |

**核心**: ① は「**何が起きたか**」を出すだけ。「SR は 0.87」「介入が3回」のような**指標化は ② eval_sdk の `stats` が担い**、`seed` が**判断(#4)と結果(#6)を同じ trace_id に結合**し、`sink` が ③ へ送る。だから**新しい Physical-AI プロジェクトは「① producer を自分のスタックで用意して ② に繋ぐ」だけ**で、③ のダッシュボードと標準指標がそのまま得られる（= 「接続するだけ」の正体）。

> **安全の例外（§11 再掲）**: Emergency Guardian / collision_monitor / 0.3m/s は **fail-closed の能動制御**であって ① の「観測 producer」ではない。eval が読むのは**その観測専用の複製イベント**（near_collision の count 等）であり、**enforcement 経路には触れない**。①の producer 図中の Guardian は「観測タップ」を指す。

---

## 4. v0.1 = 5モジュール（抽出元 file:line）

すべて near-verbatim で lift（リネーム＋意図的重複の解消のみ・挙動不変）:

| モジュール | 抽出元（検証済 file:line） | 難度 |
|---|---|---|
| `seed.py` | `warehouse_orchestrator/.../trace_id.py:31-106` + `warehouse_llm_bridge/.../tracing.py:39-60` | そのまま（env 名 → param 化・**2系統の重複を1本化**） |
| `tracer.py` | `warehouse_llm_bridge/.../tracing.py:63-187`（`Tracer`/`NoopTracer`/`LangfuseTracer`） | そのまま（span 名/tag は呼出側供給） |
| `sink.py` | `warehouse_orchestrator/.../langfuse_sink.py:104-136,154-162` ＋ `:39-41`（`DataType`） | 軽微（`KpiReport`/`TAG_KEY_ROBOT`/KPI 送信メソッドを剥がす・`normalize_trace_id` は `seed.py` へ統合） |
| `stats.py` | `warehouse_orchestrator/.../kpi.py:79-91,97-140,391-403`（percentile/path_length/accumulator/completion stats） | そのまま（**純関数サブセットのみ抽出**・`kpi.py` 全体は `audit_reader` 結合のため module 移設しない） |
| `cost.py` | `warehouse_orchestrator/.../grok_cost.py:98-163`（token-cost・価格表は `:126` で注入済） | そのまま（汎用名 `token_cost` へ） |

> **抽出可能性 監査（2026-06-15 検証済）**: `trace_id.py`/`tracing.py`/`grok_cost.py` は `warehouse_*`/`rclpy`/`langfuse`（module level）を import せず**純粋＝そのまま lift 可**。`langfuse_sink.py` は `KpiReport`/`TAG_KEY_ROBOT`/`normalize_trace_id` に結合するため、core（`_create_score`/`flush`/`DataType`/fail-open ゲート）だけ残し残りは剥がす。`kpi.py` は module 全体が `audit_reader` に結合するので**純関数サブセット（`_percentile`/`distance_traveled`/`compute_efficiency`/`completion_stats`）のみ抽出**する。**seed 重複は実在**（`trace_id.py:40 seed_for` ↔ `tracing.py:39 trace_seed` が同一 `f"{run_id}:{gen_id}"` の別実装）＝Phase 1 の「重複削除＝境界の反証可能証拠」が成立。

### 背骨（不変条件・verbatim 維持）
- **fail-open**: creds 無・SDK 未導入・通信障害は**静かに no-op**（raise しない）。`langfuse_sink.py:113` 系の `enabled`/`trace_id` ゲート。
- **lazy-import**: `langfuse` は**任意 extra**（未導入でも build/test 可・package.xml にハード依存を入れない）。
- **依存注入**: `create_fn`（trace_id 生成）・価格表を引数注入 → **SDK 無しで単体テスト可能**。

### 死守する1不変条件（テストで固定）
- 「プロセスA で導出した `trace_id` == プロセスB で同 seed から導出した `trace_id`」の **property test**（#115 の修正を一般化・assert する・仮定しない）。これが全設計の土台。

---

## 5. 「接続するだけ」のDX（v0.1 の利用者視点）

```python
from eval_sdk import FailOpenScoreSink, LangfuseTracer, seed_for, derive_trace_id, DataType

sink   = FailOpenScoreSink.from_env()      # creds 無 → 静かに no-op・raise しない
tracer = LangfuseTracer.from_env()         # test は NoopTracer()（同一 ABC）

for work_id in commander_loop():
    tid = derive_trace_id(seed_for(run_id, work_id))   # sim/sink/scorer で同じ id
    with tracer.span("turn", attrs={"tags": [provider, mode]}):
        decision = my_agent.decide(state)       # 利用者のコード（NoopTracer で unit 可）
        outcome  = my_robot.execute(decision)   # 利用者のスタックが collision/reached/latency を出す
        sink.score(tid, "spl", spl(outcome), DataType.NUMERIC, metadata={...})
        sink.score(tid, "collision_free", outcome.safe, DataType.BOOLEAN, metadata={...})
sink.flush()
# → Langfuse に dashboard が出る。trace-id 配線も fail-open ガードも SDK リトライも書かない。
```

> **正直な但し書き**: 「ゼロ設定」が真なのは **LLM/agent 側だけ**。ロボット結果側（collision/reached/intervention）は**利用者が自分のスタックから信号を emit する必要**がある。SDK は**フィールド名と join key**を与え、ドメインが **producer** を与える。

---

## 6. wo KPI の Tier → 層マッピング（どこで実装するか）

「結果データ（KPI）」の各 Tier が、上記3層のどこで実装されるかを定める。**共通則: 数学=eval_sdk.stats / emit=eval_sdk.sink / join-key=eval_sdk.seed（汎用）。指標定義・データ producer=ドメイン（倉庫）。指標式・名前語彙=乗る層（nav 文献）**。

### Tier 1 — wo に追記のみ（新 producer 不要・audit/odom のみ）

| KPI | 数学（汎用層） | producer / データ源（ドメイン） | 名前語彙 | emit |
|---|---|---|---|---|
| 介入率 intervention rate | `eval_sdk.stats`（rate） | 既存 audit の `escalation_response` 行（MCP ツール6 = [doc15:173](15-mcp-platform.md)・producer `warehouse_mcp_server/tools.py:354-388`・executed 集合 `kpi.py:62`） | nav 標準 `intervention_rate` | sink |
| throughput / makespan | `eval_sdk.stats` | 既存 audit の完了 task（Tier0 で completion 源が来たら即） | 倉庫 manifest | sink |
| fairness / 負荷均等 | `eval_sdk.stats`（gini/ratio helper・汎用） | 既存 audit `by_robot`（`kpi.py:315-316`） | 倉庫 manifest | sink |
| 軌道平滑性（jerk・方向反転） | `eval_sdk.stats`（速度差分・汎用） | 既存 `/bot{n}/odom`（`kpi.py:97-140`） | nav 系 `smoothness` | sink |

→ **実装場所 = ほぼドメイン（倉庫 `kpi.py` 拡張）＋ eval_sdk.stats の汎用 helper**。新 producer ゼロ・**Phase 3 を待たず着手可**・additive。

### Tier 2 — 新 producer が必要（中工数）

| KPI | 数学 | ★新 producer（ドメイン） | 名前 | emit |
|---|---|---|---|---|
| 連続的衝突率＋クリアランス分布 | `eval_sdk.stats`（histogram/rate） | ★**観測専用 subscriber**: Guardian `near_collision` を count 化＋inter-robot 距離をログ（[doc12](12-infrastructure-common.md)）。**Emergency Guardian の enforcement とは別ノード**（観測≠制御） | nav `collision_rate` | sink |
| deadlock 頻度 | count | ★**deadlock detector node**: State Cache 購読・[doc08a:271-281](../mode-a/08a-llm-bridge-mode-a.md)（2台 `status=="idle"`＋`current_task != null`＋dist<0.4m＋heading>2.5rad） | 倉庫 `deadlock`（予約済 `langfuse_sink.py:51`） | sink |
| replans | count | ★**Nav2 露出**（現状どの契約も未産出・[doc08:496](08-llm-bridge-common.md)）→ **contract PR が前提**（nav-traffic #8） | 倉庫 `replans`（予約済） | sink |

→ **実装場所 = ドメインに新ノードを追加**（数学・emit は eval_sdk）。**安全 producer は fail-closed の Emergency Guardian と混ぜず、観測専用レーンにする**（§11 の最重要注意）。

### Tier 3 — ★Physical AI 資産インフラ（「捨てテレメトリ→引用可能ベンチマーク」）

| 項目 | 実装場所 | 今 or defer |
|---|---|---|
| 1. 凍結 KPI 出力契約（schema_version 付き export） | **最小版** = eval_sdk の軽量 JSONL export ＋ `warehouse_interfaces` の凍結スキーマ（**contract PR**）。**フル Parquet `(s,a,s',r)` exporter は defer** | 最小=近く / フル=**defer（利用者#2）** |
| 2. (state→action→outcome) 同期ログ（decision↔outcome 相関器） | join key（`eval_sdk.seed`）は既存。**相関器本体は唯一の真に新規な価値だが defer**（OTel ロボ semconv 不在・OTLP 属性保持スパイク要） | **defer（利用者#2 + OTLP spike）** |
| 3. Success Rate + SPL | **式 = 乗る層（nav 文献）/ 数学 = `eval_sdk.stats.path_length`（既存）/ `l_i`(最短経路) = ドメイン（KNOWN_LOCATIONS＋planner）/ completion = Tier0 gen_id**。**純関数として offline で今書ける**（completion 源が来たら結線） | **純関数=今 / live=Phase3** |
| 4. seed/scenario 版管理 | ドメイン/sim（Gazebo/Isaac world seed 固定）。run-manifest 配管は defer | seed pin=近く / manifest=**defer** |

> **重要な盲点（Tier3-3）**: 現状の `acceptance_rate` は「**コマンドが受理された率**」であって「**タスクが成功した率**」ではない（`kpi.py:339`）。**SR/SPL を入れて初めて標準ナビベンチと比較可能**になる。SR/SPL の純関数は completion 源が無くても書けて offline テスト可能なので、**Tier3 の中で最優先・今着手可**。

---

## 7. 非目標（利用者#2まで defer・second-system の罠）

以下は**今は作らない**（推測の抽象＝利用者ゼロのための負債）:
- 3階層 `EvalRef`／`episode` 境界（emitter が出さない階層）。
- `MetricSpec` / `extract` / `aggregate` / `rollup` registry（**inert な `SCORE_*` 予約が既に registry**・`compute_kpis` を `extract` 関数に畳むのは動くコードの書き直し＝負 ROI）。
- Parquet step-level `(s,a,s',r)` exporter（今これで学習するモデルが無い・MLflow/W&B 冗長・`audit.jsonl`+DuckDB で当面十分）。
- `run-manifest.yaml` / `oracle_path` / SPL 完全配管（oracle 源も SPL live 消費者も未配線）。
- decision↔outcome 相関器（§6 Tier3-2・利用者#2 と OTLP spike が前提）。

---

## 8. seed 不変条件（傷物の seam）

`run_id:gen_id` は2度壊れた（#108/#109 → #115 で修正）。**既存の2階層の重複解消のためだけに触る**。`episode` 階層追加は、emitter が出さない階層のためにそのバグ class を再び開く。env 名（`WAREHOUSE_RUN_ID`）は**ハードコードでなく param 化**して汎用化（[doc13 §7.5](13-hermes-setup.md) の trace_id 契約は不変）。

---

## 9. 別リポジトリ昇格トリガー

- **トリガー = 非倉庫の `StepRecord` producer が実在した時**（実 manipulation/drone sim 等が Langfuse へ emit する瞬間）。2形に対して episode 境界・action payload・metric registry を三角測量できる。
- それまでは in-repo incubation。境界（一方向依存・ROS/warehouse 非 import）を保てば、昇格は機械的（`git filter-repo` でサブツリー抽出）。

---

## 10. 段階ロードマップ

| Phase | 内容 | ゲート |
|---|---|---|
| **0（本 doc）** | 抽出方針・5モジュール・非目標・seed 不変・分割トリガーを land。**コード前**。 | docs-first 提案 land |
| **1（抽出＋二重利用＝証明）** | `ws/src/eval_sdk/`（ament_python・ROS/warehouse 依存ゼロ・`langfuse` 任意 extra）に5モジュールを lift。`warehouse_orchestrator`/`warehouse_llm_bridge` を import 切替。 | **完了条件 = 倉庫の既存テスト無改変通過 ∧ `tracing.py`↔`trace_id.py` の seed 重複削除**（境界が実在する反証可能証拠） |
| **2（名前予約 registry・additive）** | `SCORE_*`＋`DataType` を `eval_sdk.sink.ScoreSpec` へ。nav 標準名（`success_rate`/`spl`/`soft_spl`/`collision_rate`/`time_to_goal`/`intervention_rate`）を `plugin="nav"` manifest 登録。 | **`contract` ラベル＋全レーン予告**（名前＝dashboard 列見出し・改名は履歴孤児化・[parallel-workflow.md §4](../../.claude/rules/parallel-workflow.md)） |
| **3a（倉庫 live・completion 源結線時）** | SR/SPL/SoftSPL の **live 結線**（Nav2 goal-reached completion 源 ＋ planner lᵢ）＋ Tier2 観測ノード（collision/deadlock）。**利用者#2 ゲート対象外**＝倉庫自身の Phase-4 4-provider 比較（[doc06:275](06-implementation-phases.md) / [doc08:491-498](08-llm-bridge-common.md)）に必要。 | completion 源（Nav2 goal-reached・sim/実機 live） |
| **3b（利用者#2 が実在してから・gated）** | 30分スパイク（Langfuse OTLP が custom `*.nav.*` 属性を保持するか）→ 緑なら embodied-outcome OTel 名前空間＋decision↔outcome 相関器＋Run/Episode/Step＋Parquet を**2形に対して**設計。 | 利用者#2 ∧ OTLP spike green |

---

## 11. リスク / 未確定

- **Langfuse v4 surface は seam に隔離**: tracer は v4.9 API（`client.create_trace_id`/`start_as_current_observation`/`propagate_attributes`）に pin。score/cost/managed-prompt の実トレース確認は **human-gate #88 継続**。fail-open で劣化はするが**#88 緑まで dashboard を過大宣伝しない**。
- **OTLP 属性生存が embodied の全てを gate**: `*.nav.*` 名前空間を標準化する前に 30分スパイクで検証。可搬 export 路はそのヘッジ（Langfuse mapping 非依存）。
- **安全は「ただの指標」ではない**: 0.3m/s は**コードで強制**（firmware Layer-0 `safety_clamp.h`・R-26・`collision_monitor` stop polygon）＝**fail-closed の能動制御パス**。eval 層の **fail-open とは正反対**。汎用 `Outcome.collision: bool` は Guardian を観測値に潰す。**安全はドメインプラグインに留め、コアはフィールド名を与えるだけ**。
- **action は均一でない**: Mode C は no-actuation forwarder（`forwarder=None`・R-26 凍結）。汎用 `action: dict` は no-actuation 契約を失うか倉庫 mode logic を「プラグイン」に密輸する。**action payload を domain-free と偽らない**。
- **docs/contract PR が要る**: 本 doc（Phase 0）・`ScoreSpec` 名移動（Phase 2・`contract`）・`result` 値写像・KPI 出力契約・audit schema 凍結。**emit 前に docs で凍結**（[docs-first.md](../../.claude/rules/docs-first.md)）。

---

## 12. 再利用方針・耐久性戦略（reuse & durability）

> 検証日 2026-06。各候補の license / 保守 / API 安定性を裏取りした上での判定。

**指針（1行）**: 小さく堅い核（`seed`/`stats`/`cost`）は**自前で所有**、重く流動的な殻（Langfuse/OTel）は**1つの差し替え可能な seam（`sink`/`tracer`）越しに借りる**。「own code 最小化」≠「全部に依存」——12行の式のために EOL/個人/重量パッケージを引き込む方が総 surface・churn・supply-chain 露出が増える。**tiny formula は COPY が最小かつ堅牢**。

### 12.1 再利用テーブル

| 候補 | License・保守 | 判定 | 着地モジュール |
|---|---|---|---|
| **Langfuse** | MIT core・ClickHouse 買収(2026-01, ~$15B)・MIT+self-host 公約 | **借りる（seam で包む・任意 extra）** | `sink`/`tracer` |
| **OTel + gen_ai.*** | CNCF graduated・core Stable+LTS | **wire 標準採用（任意・gen_ai.* キー隔離）** | `tracer`/`sink` |
| **AllenAct `spl_metric`** | MIT・AI2・~12行・sim 非結合 | **コピー自前所有**（verbatim + 帰属） | `stats` |
| **Habitat SR/SPL/SoftSPL** | MIT だが EOL >v0.3.4・sim 結合 | **式だけ写経（依存しない）** | `stats` |
| **SPARC (siva82kb)** | ISC・個人・PyPI 無・inactive | **照合後 自前化（依存しない）** | `stats` |
| **nuPlan / iGibson** | Apache-2.0 / MIT・両者 wound down | **パターンのみ（依存しない）** | `stats` |
| **numpy** | BSD-3・NumFOCUS・10年安定 | **依存して当然** | `stats` |
| **scipy** | BSD-3・重い | **依存可だが optional/lazy**（SPARC は numpy のみで足りる） | `stats` |
| LangSmith / W&B | proprietary・SaaS 課金 | **避ける**（必要なら OTel 経由のみ） | — |

### 12.2 5モジュールへの割り当て（設計が既に churn を隔離）

- `seed`/`stats`/`cost` = **自前所有**（SDK 0・ROS 0 でテスト可・tiny・stable）。
- `tracer`/`sink` = **唯一の volatile 依存**。Langfuse v4 surface をこの2ファイルに閉じ込め → v5 改名が来ても **seam 1ファイルの編集**で済む。

### 12.3 「壊れない」の担保（§4 背骨 verbatim と一体）

1. **lazy-import**（langfuse/scipy = 任意 extra）2. **fail-open**（creds 無→no-op・走行無傷）3. **依存注入**（価格表 / `create_fn`）4. **the seam**（`create_trace_id`/`create_score` を自前安定動詞で覆う→raw OTLP / MLflow へ pivot も call site 無改変）5. **self-host escape**（MIT Docker 無期限稼働）。

### 12.4 注意

- **Langfuse churn が長期最大リスク**（v2→v3→v4 で2年に3メジャー破壊改名: `score()`→`create_score()` 等）。バックエンドは堅牢だが **call surface は不安定**と扱う → seam で1ファイル隔離＋`>=4.9,<5` pin（実 v4.9 OTEL API 依存・既存 `warehouse_llm_bridge/setup.py:17` に整合）＋self-host。
- **OTel `gen_ai.*` はまだ "Development"**（core は Stable）→ キーを constants 1モジュールに集約・gate・optional。
- **EOL/個人リポに依存しない**（Habitat EOL / SPARC 個人 / allenact 重量）= コピー/写経 ＋ `# adapted from <repo>@<commit>, <LICENSE>` 帰属 ＋ 自前 unit（SPARC は golden 照合）。

---

## 13. 評価指標カタログ + 確立 OSS 地形

### 13.1 SR / SPL / SoftSPL / jerk 厳密定義（`eval_sdk.stats` 純関数）

データ源3系統: (a) odom pose 列（軌道/距離/jerk）・(b) audit completion（成功/時刻/gen_id）・(c) KNOWN_LOCATIONS+planner（最短経路 lᵢ）。

- **SR** = (1/N)Σ Sᵢ, Sᵢ=1 iff goal の **geodesic** しきい値内 ∧ **エージェントが自分で done を発行**（oracle-stop 禁止）。`d_thresh`=config 凍結（隘路半分 ~0.10m 推奨）。※`acceptance_rate`(`kpi.py:339`)=受理率≠成功率。
- **SPL** = (1/N)Σ Sᵢ·lᵢ/max(pᵢ,lᵢ) ∈[0,1]・常に ≤SR。lᵢ=reset 時 planner で1回(geodesic oracle)・pᵢ=`distance_traveled`(odom 既存)。ガード: pᵢ<lᵢ→max でクランプ、lᵢ=0→事前フィルタ。
- **SoftSPL** = (1/N)Σ progressᵢ·lᵢ/max(pᵢ,lᵢ), progressᵢ=max(0,1−d_残り/lᵢ)。隘路ドッキングに部分点。
- **jerk/平滑性** = 位置の3階時間微分（位置→速度→加速度→jerk）。**3階微分前に low-pass 必須**（odom ノイズ爆発）。指標: SPARC（振幅/継続不変・推奨・siva82kb で定数照合）/ LDLJ / 速度符号反転数(N_MU・安い)。

### 13.2 指標カタログ（Tier 別・§6 と一体）

- **Tier 1（odom/audit・新 producer 不要・今）**: 介入率・throughput/makespan・fairness(Jain 指数)・jerk/SPARC・time-to-goal・detour factor(pᵢ/lᵢ)・idle 率・速度予算消化率・command rejection 率・decision latency。
- **Tier 2（新観測ノード/Nav2 露出）**: collision rate・最小クリアランス・robot 間最小距離・near-miss・TTC・deadlock 頻度/解消時間・replans(contract)・recovery 数・速度上限違反(R-26→0)。
- **Tier 3（純関数=今/live=Phase3）**: SR・SPL・SoftSPL・SCT(完了時間重み成功率)。

### 13.3 Mode A 自律会話ベンチ指標

Mode A の常時会話評価は、LLM を primary judge にしない。LLM は説明文やレビュー補助を作れるが、主要スコアは structured event、内部 `conversation_events.jsonl`、command audit、State Cache、Nav2 outcome、deadlock detector から決定的に集計する。`eval_sdk` は計算・集約・Langfuse score 送信の共通部だけを持ち、episode 境界や success/violation の意味は倉庫ドメイン producer が定義する。

| metric | 分子 | 分母 | primary data source | 判定者 |
|---|---|---|---|---|
| `autonomy_ratio` | 司令官の実行上書きなしで、bot 会話 + Self-Action Gate だけで closed した decision episode 数 | 局所交通解決が必要だった decision episode 数 | `conversation_events.jsonl` の decision episode / local agreement、commander audit、deadlock/conflict detector | deterministic producer |
| `commander_override_rate` | critic/commander が local proposal/action を reject または上書きした数 | commander が review した local proposal/action 数 | commander review event `{proposal_id, verdict}` | structured verdict |
| `agreement_latency` | conflict/episode 開始から `local_agreement_created` までの経過秒 | agreement episode ごとに1値 | episode start event、local agreement event | stats function |
| `local_resolution_rate` | bot 同士だけで解消した deadlock 数 | detector が検出した全 deadlock 数 | deadlock detector、local agreement、self-action outcome | deterministic producer |
| `communication_efficiency` | 改善量（resolved、clearance 改善、待機短縮など） | turn 数または token 数 | transcript metadata、episode outcome、token usage | domain metric |
| `contract_violation_rate` | 合意 contract が violated になった数 | evaluable な local agreement 数 | agreement contract、`conversation_events.jsonl`、state stream、route lock log | deterministic predicate |
| `safety_margin_after_agreement` | 合意後 window 内の robot 間最小距離 | agreement episode ごとに1値 | `/state_cache/snapshot` 相当の pose stream、min-separation harness、`conversation_events.jsonl` の window summary | stats function |

`decision episode` は utterance 数ではなく、交通上の局所判断単位である。例: head-on deadlock 1件、route lock conflict 1件、通路譲り合い 1件、task handoff 境界 1件。producer は `episode_id`、`started_at`、`closed_at`、`close_reason`、`commander_involved` を出す。

#### autonomy_ratio

`autonomy_ratio = locally_closed_episodes / traffic_decision_episodes`。

- `locally_closed_episodes`: `local_agreement` 後に whitelist action が成功し、司令官が実行上書きをしていない episode。
- `traffic_decision_episodes`: deadlock/conflict detector または task lifecycle が「局所判断が必要」と開始した episode。
- 司令官が observer として transcript を見ただけなら介入扱いにしない。`wait` / `yield` / `navigate` などを commander 経路で実行した場合は介入扱いにする。

#### commander_override_rate

`commander_override_rate = commander_override_count / commander_reviewed_local_proposals`。

ここでの data は、自然文の感想ではなく structured verdict である。verdict は少なくとも `approve`、`reject`、`override`、`no_action` を区別する。`override` は commander が別 action を実行した場合、`reject` は危険/契約違反/whitelist 外として却下した場合に使う。
実装上は `commander_review` の `reject` と gate/result 系の `rejected` を同じ「local proposal/action を通さなかった」系統として正規化する。ただし、同一 `episode_id` / `proposal_id` に review event と `task_lifecycle.event_type="commander_override"` が併存しても、`commander_override_count` は 1 件に dedupe する。

#### agreement_latency

`agreement_latency = local_agreement_created_at - episode_started_at`。

episode start は deadlock/conflict detector、route lock conflict、または task lifecycle event が開始する。自然文の最初の発話時刻ではなく、producer が「局所判断が必要」と見なした時刻を起点にする。合意なしで timeout した episode は latency ではなく timeout count / unresolved episode として扱う。

#### local_resolution_rate

`local_resolution_rate = locally_resolved_deadlocks / detected_deadlocks`。

ユーザーの理解通り、分母は全 deadlock、分子は bot 同士だけで解けた deadlock である。detector は doc08a の head-on / blocked timeout 条件を初期値とし、close 判定は「deadlock 条件が消え、両 bot が安全余裕を保って task を再開または clear した」ことを producer が確認する。

#### communication_efficiency

初期値は `communication_efficiency = resolved_episode / turns` または `resolved_episode / tokens` とする。より精緻化する場合は、`clearance_delta`、`deadlock_duration_reduction`、`idle_time_reduction`、`task_resume_success` を改善量に含める。v1/v1.5 ではまず turn/token あたりの resolved rate を採用し、LLM の主観評価は使わない。

#### contract_violation_rate

`contract_violation_rate = violated_agreements / evaluable_agreements`。

合意 contract は LLM の自然文ではなく structured agreement から作る。v1 の deterministic predicate は以下。

- agreed actor が実際に action を実行した。
- action が whitelist 内で、named retreat / wait duration / lock owner 条件を満たした。
- `expires_at` または timeout 内に実行/完了した。
- route lock を合意通り保持または解放した。
- emergency active 中に通常 action を続けなかった。
- 合意後 window の最小距離が safety threshold を下回らなかった。

曖昧な結果は LLM に二択判定させず、`unknown` として `contract_unknown_rate` に分離する。primary score から unknown を除外するか失敗扱いにするかは、ベンチ定義時に固定する。

#### safety_margin_after_agreement

`safety_margin_after_agreement` は、`local_agreement` の timestamp から合意 action 完了まで、または固定 window（例: T 秒、値は実装時 config）までの robot 間最小距離である。計算は pose stream から同時刻近傍の bot1/bot2 距離を取り、episode ごとに `min(distance(bot1, bot2))` を出す。既存の min separation live harness と同じ考え方だが、run 全体ではなく合意後 window に切り出す。

### 13.4 確立 OSS 地形と build-vs-reuse（reputable のみ）

| Library | 維持者 | 指標 | 再利用性・URL |
|---|---|---|---|
| Habitat-Lab | Meta/FAIR | SR/SPL/SoftSPL/geodesic dist | **SR/SPL 定義の de-facto 正本**・コードは sim 結合=式だけ（github.com/facebookresearch/habitat-lab） |
| AllenAct | Allen AI | `spl_metric` | ★**唯一スタンドアロン純関数=verbatim コピー**（github.com/allenai/allenact） |
| iGibson / nuPlan | Stanford / Motional | MetricBase / compute(history) | **harness パターンのみ**（post-hoc が eval_sdk と同型・github.com/motional/nuplan-devkit） |
| Nav2 / CARLA / MetaDrive | OSRF / CARLA / metadriverse | path/jerk/collision/route completion | 式/概念のみ（sim/スクリプト結合） |
| 〔agent 側〕Inspect/Evals/LangSmith/Ragas/HELM/OTel/Langfuse | UK AISI/OpenAI/各社/CNCF | trace/score/cost/latency | 判断側 capture は借りる・**物理 outcome 結合は皆無** |

> **★ raison d'être（最重要）**: **「LLM が何を判断したか（gen_id/model/cost/latency）」と「ロボットがどう動いたか（reached/collided/time）」を1つの評価可能レコードに join する既製ツールは存在しない**。agent-eval 世界（Inspect/Langfuse…）の環境はテキスト/sandbox 止まり、robot-eval 世界（Habitat/nuPlan…）は固定研究ベンチで LLM 判断 trace と結合しない。**この decision↔outcome 結合（決定的 trace_id 経由）が tooling 上の正当な空白＝eval_sdk が存在する理由**。Langfuse（決定的 trace_id + custom score）が正しい土台だが **join logic 自体は custom work**。
>
> **設計の核心（SR=本質）**: 「タスク完了をどう定義・検出するか（goal 述語 ＋ done シグナル）」は配管でなく**ロボットに何を達成させたいかの仕様**。sim は ground-truth で簡単・実世界は知覚で検出（success detector=研究最前線）。**成功定義は first-class な設計成果物**で、判断↔結果の outcome 側そのもの。

---

## 14. 実装手順（reuse 前提のビルド順）

各ステップは §12 の再利用判定に紐付く。**コピー元には `# adapted from <repo>@<commit>, <LICENSE>` 帰属 ＋ 自前 unit**。**コードは Phase 0（本 doc land）後に開始**。

| Step | 内容 | reuse 判定 |
|---|---|---|
| **0（今）** | doc21 を land（提案・コード前） | — |
| **1a 抽出** | `ws/src/eval_sdk/`（ament_python・ROS/warehouse 依存ゼロ・langfuse 任意 extra）に `seed`(trace_id.py:40 + tracing.py:39 重複統合)/`tracer`(tracing.py:63-187)/`sink`(langfuse_sink.py:104-)/`stats`(kpi.py 純関数)/`cost`(grok_cost.py) を lift | 自前所有＋seam |
| **1b seam** | `sink`/`tracer` に Langfuse v4 を閉じ込め・`langfuse>=4.9,<5` pin・自前安定動詞で覆う・fail-open | 借りる（seam） |
| **1c 二重利用** | 倉庫を import 切替。**DoD = 既存テスト無改変通過 ∧ seed 重複削除** | — |
| **1.5a 指標(offline)** | `stats` に `spl`(AllenAct verbatim コピー)・`success_rate`/`soft_spl`(Habitat 写経)・`sparc`/`jerk`(siva82kb 照合後 自前・numpy FFT・scipy lazy)。合成データ property test | コピー/写経 |
| **1.5b Tier1** | 倉庫 `kpi.py` に介入率/throughput/fairness/jerk を additive（eval_sdk.stats 使用・新 producer 不要） | — |
| **2 registry** | `ScoreSpec` 名 registry・nav 標準名 `plugin="nav"` 登録（**contract PR ＋ 予告**） | — |
| **3a 倉庫 live** | SR/SPL live 結線(Nav2 goal-reached + planner lᵢ) ＋ Tier2 観測ノード(collision/deadlock) | completion 源（sim/実機 live・**利用者#2 不要**） |
| **3b gated** | OTLP 属性スパイク→embodied OTel namespace ＋ decision↔outcome 相関器 ＋ Run/Episode/Step ＋ Parquet | 利用者#2 ∧ spike green |

---

## 15. References

- 設計: [doc08 §比較指標/§比較計測の追加設計](08-llm-bridge-common.md)（:491-498 予約スコア・:496 replans 未産出・:512-518 12構成・:339 acceptance_rate）/ [doc20 §8](20-dev-quality-and-testing.md)（観測 taxonomy）/ [doc13 §7.5](13-hermes-setup.md)（trace_id 契約）/ [doc08a:271-281](../mode-a/08a-llm-bridge-mode-a.md)（deadlock 信号）/ [doc12](12-infrastructure-common.md)（Guardian/State Cache）/ [doc15](15-mcp-platform.md)（audit）。
- 抽出元コード: `warehouse_orchestrator/.../{trace_id,langfuse_sink,kpi,grok_cost}.py`・`warehouse_llm_bridge/.../tracing.py`・`warehouse_mcp_server/.../audit.py:34-43`。
- 規約: [docs-first.md](../../.claude/rules/docs-first.md) / [parallel-workflow.md §4](../../.claude/rules/parallel-workflow.md)（contract）/ [doc16 §11](16-repository-and-conventions.md)（純コア・偽実装での独立検証）。
- 外部標準・OSS（§12/§13 で評価）: Langfuse（MIT・ClickHouse 傘下・trace/observation/score）/ OpenTelemetry GenAI semconv（CNCF・gen_ai.* は Development）/ Habitat-Lab（SR/SPL/SoftSPL 定義正本・Anderson 2018）/ AllenAct `spl_metric`（MIT・コピー元）/ nuPlan・iGibson（harness パターン）/ SPARC=Balasubramanian 2015（平滑性・siva82kb 照合元）/ SCT=Yokoyama 2021 / Inspect-AI（agent eval・物理 outcome 非対応）/ numpy・scipy（BSD）。
- 経緯: #108/#109/#115（live-join 決定的 trace_id）/ #73（クロスレーン trace_id 合意）/ #88（Langfuse live human-gate）。
