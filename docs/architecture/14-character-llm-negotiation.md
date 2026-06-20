# キャラLLM + 交渉プロトコル設計

作成日: 2026-05-28

> **関連ドキュメント**:
> - [08 - LLM Bridge 共通](08-llm-bridge-common.md) -- 司令官LLM の同時発火制御
> - [12 - 共通基盤](12-infrastructure-common.md) -- Emergency Guardian / State Cache
> - [15 - MCPプラットフォーム](15-mcp-platform.md) -- Policy Gate / 競合状態の防止
> - [Mode A README](../mode-a/README.md) -- メイン動画回の位置づけ
> - [Mode C README](../mode-c/README.md) -- 実用検証回の位置づけ

## 概要

「LLMでminicarを動かしてみた」動画のメインコンテンツとして、Bot1 / Bot2 にそれぞれキャラ性を持たせた **キャラLLM** を追加し、デッドロック等の重要シーンで **交渉プロトコル**を発動する。会話で合意した内容を司令官LLMが承認して実機に反映する稟議制を採用（**案B**）。

## アーキテクチャ

```
┌────────────────────────────────────────────────────────────┐
│ 司令官LLM（既存、1個）                                       │
│  ├── 通常: 戦略判断 + MCP実行                                │
│  └── 重要シーン: /negotiation/start を publish して交渉起動  │
└────────────────────────────────────────────────────────────┘
       ↓ /negotiation/start
┌────────────────────────────────────────────────────────────┐
│ キャラLLM Bot1（新規、Opus）    キャラLLM Bot2（新規、Opus） │
│   ├── 通常: /character/speech に発話 publish（実況のみ）     │
│   └── 交渉中: 相手の発話を見て返答、合意したら proposal 発行 │
└────────────────────────────────────────────────────────────┘
       ↓ /negotiation/proposal（構造化JSON、gen_id付）
┌────────────────────────────────────────────────────────────┐
│ 司令官LLM（次サイクル）                                       │
│  └── proposal を situation JSON に取り込み → 検証 → 承認/拒否│
│     → Policy Gate → Warehouse MCP Server → Nav2             │
└────────────────────────────────────────────────────────────┘
```

**重要原則**: キャラLLMは **Nav2 / MCP を直接叩けない**。必ず司令官の承認を経由する稟議制とすることで、既存の安全機構（Policy Gate / gen_id / twist_mux）を壊さない。

## 動作モード

### A. 実況モード（通常時）

- 自分の状態・相手の状態・司令官の最近の決定を読んでコメント
- `/character/speech` トピックに発話を publish → 画面表示のみ
- フィラータイマー（4〜8秒に1回）+ イベント駆動（状態変化時）

### B. 交渉モード（重要シーンのみ）

#### 発動条件

| モード | トリガー |
|---|---|
| Mode A | 司令官がデッドロック検出（08a-llm-bridge-mode-a.md デッドロック検出アルゴリズム参照） |
| Mode C | Open-RMF エスカレーション（11c-traffic-mode-c.md 参照） |

#### フロー

1. 司令官が **`start_negotiation` MCP tool** を呼び出し（`gen_id`, `deadlock_or_escalation_id`, `starter`, `context` を引数で渡す。`deadlock_or_escalation_id` は必須＝tools.py）→ Warehouse MCP Server が `/negotiation/start` トピックを publish（状況スナップショット + gen_id + 先手 `"starter": "bot1"` 同梱）
2. キャラLLM Bot1 ←→ Bot2 が**バトンパス方式**で交互に発話（各最大4ターン、計8ターン上限）
3. 合意に達したら構造化JSON を `/negotiation/proposal` に publish
4. 司令官が次サイクルで proposal を situation JSON に取り込み
5. 司令官が検証 → Policy Gate → MCP → Nav2 で実行

#### バトンパス方式（ターン制プロトコル）

両キャラが同時発話する競合を防ぐため、明示的なターン制を実装する:

```
/negotiation/start (commander, starter="bot1", gen_id=N)
  ↓ 先手指定
Bot1 (キャラLLM, turn=1):
  - State Cache + 司令官の最新方針を読む
  - 発話を生成
  - publish: /character/speech (text)
  - publish: /negotiation/turn (turn=1, next="bot2")  ← バトン渡し
  ↓
Bot2 (キャラLLM, turn=2):
  - Bot1 の発話を /character/speech から読む
  - 発話を生成
  - publish: /character/speech (text)
  - publish: /negotiation/turn (turn=2, next="bot1")
  ↓
... (Bot1 turn 3, Bot2 turn 4, Bot1 turn 5, ...)
  ↓
合意条件:
  (a) いずれかのキャラが proposal フォーマット の JSON を出力 → /negotiation/proposal
  (b) 計8ターン経過しても合意なし → タイムアウト → 司令官が独自判断
  (c) 8秒経過 → タイムアウト
  (d) /negotiation/abort 受信 → 即中断
```

各キャラは自分の turn でない時は発話しない（subscribe するだけ）。タイムアウト時計は司令官側で持つ。8秒 timeout と `/negotiation/abort` は turn 境界だけでなく、キャラLLM の発話生成中にも有効な episode-level stop 条件とし、発火後に戻った late speech / late proposal は parse・publish せず破棄する。

#### キャラLLM の状態入力ソース

キャラLLM が「自分・相手・司令官」の情報を得るチャネル:

| データ | 入力元 |
|---|---|
| 自分の状態（位置・バッテリー・goal） | `/state_cache/snapshot` の自分の bot エントリ |
| 相手の状態 | `/state_cache/snapshot` の相手の bot エントリ |
| 司令官の最新決定 | `/llm/reasoning` トピック（既存）+ `/llm/command` |
| 交渉開始シグナル | `/negotiation/start` |
| 相手の発話 | `/character/speech` |
| バトン | `/negotiation/turn` |
| 中断信号 | `/negotiation/abort` |
| gen_id（自分用） | `/llm/command` / `/llm/reasoning`（Bridge が採番・publish）。※ `/state_cache/snapshot` は State Cache が publish し、凍結 `StateSnapshot` に gen_id フィールドは無い |

キャラLLM は **直接 ROS topics を subscribe** する（State Cache JSON ファイル経由ではなく）。State Cache のリアルタイム性（100ms）とトピック整合性が必要なため。

#### proposal フォーマット

```json
{
  "negotiation_id": "neg-20260528-001",
  "gen_id": 142,
  "agreed_action": {
    "action": "yield",
    "by": "bot1",
    "to": "退避地点B",
    "duration": 5.0
  },
  "transcript": [
    {"speaker": "bot1", "text": "..."},
    {"speaker": "bot2", "text": "..."}
  ],
  "reached_at": 1717000000.123
}
```

## 安全設計（ガードレール）

| ガードレール | 内容 |
|---|---|
| 書き込み権限 | キャラLLMは `/character/speech` と `/negotiation/proposal` のみ publish。Nav2/MCP/cmd_vel には触れない |
| 交渉タイムアウト | 発話生成中も含め、8秒以内に合意できなければ司令官が独裁的に決定 |
| 提案フォーマット強制 | 自由文NG。JSON Schema で構造化を強制（キャラLLM のシステムプロンプトで指示 + Bridge側でvalidate） |
| 司令官の最終判断権 | 合意が安全条件（バッテリー/距離/Emergency中/Policy Gate）に反するなら拒否 → 司令官独自判断 |
| 発話回数制限 | 各キャラ最大4ターン（無限会話防止） |
| Emergency中の中断 | Emergency Guardian 発火時は、発話生成中でも交渉を即中断し、in-flight output / proposal も破棄 |
| gen_id 整合性 | proposal の gen_id と司令官の current_gen の差が **±2世代以内**なら受理、それ以上乖離なら破棄（負荷時のレース防止と、交渉途中で世代が進むケースの両立） |

## キャラLLM システムプロンプトの方針

キャラLLM は司令官と矛盾する提案を出さないよう以下を明示する:

```
あなたは倉庫ロボット {bot_id} の人格レイヤです。
- 司令官AIの最新の方針（situation JSON の `commander_latest_decision`）を尊重してください
- 提案する解決策は安全条件（バッテリー / 距離 / Emergency中ではないこと）を満たす範囲で
- 自由文ではなく構造化JSON で合意内容を出力してください
- 相手キャラの発話を読んで、最大4ターン以内に合意に達してください
- 性格: {personality}（例: 慎重派 / スピード重視）
```

キャラLLMが司令官と完全独立に動くと「会話で合意したのに司令官が拒否」のループが頻発するため、**司令官の方針を situation JSON に明示**して尊重させる。

## モード別の司令官システムプロンプトの差異（R1）

司令官LLM のシステムプロンプトは Mode A / Mode C で**意図的に分岐**する:

- **Mode A** (08a-llm-bridge-mode-a.md §システムプロンプト（Mode A/B）): デッドロック検出・交通管理を自分で行う指示を含む。交渉モード発動も自分の判断
- **Mode C** (08c-llm-bridge-mode-c.md §システムプロンプト（Mode C）): タスク割当のみ。交通管理は Open-RMF。交渉モード発動は escalation フィールド見て判断

Phase 4 比較検証では「同じ Claude モデルでも Mode によりプロンプトが違う」点に注意。比較公平性のため、**同じ Mode 内で4社を比較**する原則とする。

## モデル選択

| ロール | モデル | 理由 |
|---|---|---|
| 司令官LLM | Opus（最新世代、Phase 4 で4社比較） | 戦略判断の質が重要 |
| キャラLLM | **Opus（最新世代）** | 全 Claude Opus 統一（16 §7）。max_tokens=60 で1〜2文。※旧 Haiku 設計（応答0.3-0.5s）からの変更で応答テンポは要実測 |

キャラLLM はテンポ重視（Opus 化に伴い応答速度の実測が必要、16 §7 検討事項）。Phase 4 の比較対象には含めない（演出専用扱い）。

## サイクル設計

司令官LLM は cycle 長（`cycle.mode_a_seconds` / `cycle.mode_c_seconds`。正本 `config/warehouse.base.yaml`、要点は [docs/README](../README.md) モード切替節）に従う。キャラLLM は司令官と独立:

| ロール | サイクル | 駆動方式 |
|---|---|---|
| 司令官（Mode A） | 3秒 | レスポンス駆動（応答後1秒待機） |
| 司令官（Mode C） | 5秒 | レスポンス駆動（応答後3秒待機） |
| キャラLLM 実況 | 4〜8秒 + イベント駆動 | 独立ループ |
| キャラLLM 交渉中 | 相手の発話到着で発火 | イベント駆動 |

Bot1キャラ / Bot2キャラ / 司令官 の3プロセスが完全並行動作（**論理的並行**。司令官は別プロセス必須=doc08 §commander cycle だが、Bot1/Bot2 の2ペルソナは1つの `character_llm` ノードに同居してよい=バトンは in-process・`/character/speech`・`/negotiation/turn` は観測用に publish。ターン進行の純 async エンジン=最大8/各4ターン・8秒timeout（発話生成中も有効）・abort（turn境界/発話生成中とも late proposal 破棄）=は ROS 非依存で host unit テスト=実装 Slice 1。3 OS プロセスへの分割は要件でない）。

## 衝突対策との関係

| 既存対策 | キャラLLMでの扱い |
|---|---|
| A+B (HTTPキャンセル + MCP gen_id) | 不要（書き込みなし） |
| twist_mux | 関係なし |
| Policy Gate | 関係なし（proposal は司令官経由で Policy Gate 通過） |
| active_tasks Lock | 関係なし |
| Emergency Guardian | **連動必要**: Emergency 時に交渉を中断する仕組みを追加 |

## ROS 2 トピック

| トピック | 型 | publisher | subscriber |
|---|---|---|---|
| `/character/speech` | std_msgs/String (JSON) | キャラLLM Bot1/Bot2 | 画面表示, Langfuse, 相手キャラ |
| `/negotiation/start` | std_msgs/String (JSON) | Warehouse MCP Server（司令官の `start_negotiation` ツール経由、§本文参照） | キャラLLM Bot1/Bot2 |
| `/negotiation/turn` | std_msgs/String (JSON) `{turn, next}` | キャラLLM | キャラLLM（バトン受け渡し）|
| `/negotiation/proposal` | std_msgs/String (JSON) | キャラLLM | 司令官LLM |
| `/negotiation/abort` | std_msgs/String | Emergency Guardian | キャラLLM Bot1/Bot2 |
| `/state_cache/snapshot` | std_msgs/String (JSON) | State Cache Node | キャラLLM（司令官/MCP はファイル `/tmp/warehouse/state.json` 経由で読む） |
| `/llm/reasoning` | std_msgs/String | 司令官LLM | キャラLLM |
| `/llm/command` | std_msgs/String (JSON) | 司令官LLM | キャラLLM |

## モード別の重要度

| | Mode A | Mode C |
|---|---|---|
| 交渉発動頻度 | 高（衝突しやすい設計） | 低（Open-RMFが解決） |
| 動画的役割 | **メインショー**（毎回交渉が起きる） | クライマックス（たまに起きる "Open-RMFにも限界" の瞬間） |
| キャラ実況の量 | 中（司令官が頻繁に喋るので役割分担） | 多（司令官沈黙時間が長く、キャラが間を埋める） |

## Langfuse 統合

- キャラLLM の呼出しも Langfuse に trace 記録する（司令官と同じ session/observability 基盤に同居）。trace 木・tag taxonomy・multi-actor モデルの詳細は本章末の §Langfuse trace/observation モデル節を参照。
- **session は司令官と共有**: 1 ラン（動画1テイク）= 1 Langfuse session（session_id = `run_{mode}_{provider}_{scenario}_{ts}`、[doc08 §セッション命名規則](08-llm-bridge-common.md#セッション命名規則) の採用実装 `build_session_id` と同一スキーム）。旧のキャラ専用・別 session 方式は廃止し、キャラ trace を司令官と別 session に散らさない（session replay でランの会話全体＝司令官判断＋bot1/bot2 交渉を1スレッドで追えるようにする）。
- **司令官 trace とは別 trace で分離するが、同一 session に束ねる**（分離は trace/observation 単位であって session 単位ではない）。交渉モードのキャラ発話は司令官サイクル trace 内の `agent` span（同一 trace）に同居し、非交渉の実況発話（§A 実況モードの4〜8秒フィラー）は session 直下の独立軽量 trace（`character.idle_chatter`）として記録する。
- 交渉モードでは Bot1/Bot2 の発話を `negotiation_id` で紐付ける（別 trace にせず **同一 trace 内の `agent` span ＋ `metadata.negotiation_id`** で束ね、session replay で会話全体を追う）。

### Langfuse Cloud 無料枠への影響（R4）

| 要素 | 観測数の概算 |
|---|---|
| Mode A 10分デモ（司令官のみ） | 約200 obs |
| Mode A 10分デモ（司令官 + キャラ×2） | **約600 obs**（3倍） |
| Mode C 10分デモ（司令官のみ） | 約120 obs |
| Mode C 10分デモ（司令官 + キャラ×2） | **約400 obs** |

Langfuse Cloud 無料枠 50K obs/月 → Mode A 比較検証4社で月33デモ程度で到達。**Phase 4 比較検証本番ではセルフホスト移行が必要**になる可能性が高い。`12-infrastructure-common.md` のデプロイ方針表に反映済。

## Emergency 中の交渉中断（R2）

`/negotiation/abort` トピックは **Emergency Guardian** が発火する:

- Emergency Guardian は **緊急停止（estop）時のみ** `/emergency/event` と同じ `event_id` で `/negotiation/abort` を発行する。**低危険度の recovery（`blocked_timeout` 等）では発行しない**: 交渉はデッドロック解消の手段であり、`blocked_timeout` recovery はそのフォールバック（08a-llm-bridge-mode-a.md §フォールバック :363-372）なので、ここで交渉を中断すると本末転倒になるため。
- キャラLLM はこのトピックを subscribe しており、turn 境界待ちではなく発話生成中でも即時に交渉を中断 + proposal を破棄
- 司令官は次サイクルの situation JSON で Emergency 状態を見て、独自判断モードに切替

実装詳細は `12-infrastructure-common.md` の Emergency Guardian セクション参照。

## Phase 段階導入（06-implementation-phases.md と同期）

| Phase | 内容 |
|---|---|
| Phase 0.5 (Gazebo) | キャラLLM未実装、司令官のみで動作確認 |
| Phase 3 (実機 Mode A) | キャラLLM実況モード本格運用 + 交渉モード Mode A で実装、Emergency連動、ターン制バトンパス |
| Phase 4 (4社比較) | Mode C でも交渉モード実装。**キャラLLMは演出専用、Phase 4 比較対象は司令官のみ**（公平性のため） |

## 交渉スコア

交渉エピソードの**記述指標**として以下を Langfuse score で記録する。キャラLLM は **Phase 4 の4社比較対象外**（演出専用 :255 / [doc06](06-implementation-phases.md):263）のため、これらは **provider 能力比較には用いない**（交渉の質・テンポの可視化のみ）。metadata は `{negotiation_id, mode}`（provider 比較軸を持たない）。[doc08 §比較計測の追加設計](08-llm-bridge-common.md#比較計測の追加設計) から本節を参照する。

| score 名 | data_type | 定義（データ源） | Phase | 状態 |
|---|---|---|---|---|
| `negotiation_rounds` | NUMERIC | 1 交渉（`negotiation_id`）で交わされた `/negotiation/turn`（:206）の総ターン数。上限 **8**（各キャラ最大4ターン・計8ターン上限 :60 / :140） | Phase 3（Mode A 交渉）/ Phase 4（Mode C, :254-255） | ⚠️ Phase依存・暫定（交渉実装が前提） |
| `agreement_reached` | BOOLEAN | **`true`**: いずれかのキャラが proposal フォーマット JSON を `/negotiation/proposal` に publish（合意条件 (a) :87 / proposal 例 :112-130）。**`false`**: 計8ターン経過し合意なし (b :88) / 8秒タイムアウト (c :89 / :137) / `/negotiation/abort` 受信 (d :90) | 同上 | ⚠️ Phase依存・暫定 |

trace は `negotiation_id` で Bot1/Bot2 を紐付ける（§Langfuse 統合 :226）。**凍結契約 `warehouse_interfaces` の変更は不要**（score の trace_id / metadata は Langfuse 突合キー）。

## Langfuse trace/observation モデル（Phase 3/4 observability）

> 本節は §Langfuse 統合（:221-226）の trace/session/tag モデルを Phase 3/4 の4社比較向けに精緻化する。正本は設計ノート `~/Developer/mwr-handoff/langfuse-observability-design.md`（repo 未追跡・local）§2.1/§3.1/§4.2/§4.3/§6 と、採用済み session スキーム [doc08 §セッション命名規則](08-llm-bridge-common.md#セッション命名規則)（`run_{mode}_{provider}_{scenario}_{ts}`、#78）。
> ここに挙げる session_id / trace_id / tag / observation 型はいずれも **Langfuse・Audit Log の突合キーであり、凍結契約 `warehouse_interfaces` ではない**（ROS トピック契約に追加しない。:266 / 設計 §8.1）。値は **docs 例示**であって、固定名前空間（後述）を超える新タグ・新弁別子は作らない。

### trace 木（1 ラン = 1 session）

1 ラン（動画1テイク）の全サイクルを 1 session に束ね、各サイクルを 1 trace、各 LLM 呼出/ツール/イベントを observation として入れ子化する（設計 §2.1）:

```
SESSION  session_id = "run_{mode}_{provider}_{scenario}_{ts}"      # 1 ラン = 1 session（doc08 採用 build_session_id）
└─ TRACE  name="commander_cycle"  metadata.gen_id=N                # 1 司令官サイクル
   ├─ GENERATION "commander.decide"        ← 司令官 LLM 呼出し（Hermes が cost/token/latency を出す）
   │    ├─ TOOL "mcp.dispatch_task"     input={gen_id:N, robot:bot1, ...}
   │    └─ TOOL "mcp.send_to_charging"  input={gen_id:N, robot:bot2}
   ├─ AGENT "negotiation"  （Mode A・交渉発生サイクルのみ）
   │    ├─ GENERATION "bot1.negotiate" metadata{actor:bot1, turn_index:0}
   │    ├─ GENERATION "bot2.negotiate" metadata{actor:bot2, turn_index:1}
   │    └─ GENERATION "bot1.negotiate" metadata{actor:bot1, turn_index:2}
   └─ EVENT "policy_gate"  metadata{verdict: accepted|rejected, reason}
```

- **交渉（重要シーン）** はこの `agent` span（`name="negotiation"`）配下に、ターン毎の `generation`（バトンパス :65-91、最大8ターン）として残し、順序は `metadata.turn_index` で固定する（設計 §6.1-6.2）。
- **実況（通常時）** は司令官サイクルと無関係に発火する（§A 実況モードの4〜8秒フィラー）ため、session 直下の独立軽量 trace `character.idle_chatter` として記録する（設計 §6.3）。司令官サイクル trace には同居させない。
- `agent` / `tool` / `event` の型名は設計 §2.1（Langfuse observation types）由来の **例示**であり、ROS 契約ではない。

### tag / metadata taxonomy（robot は observation 属性）

**robot を trace tag にしない**: 1 司令官サイクル trace には bot1 と bot2 が同居する（司令官は 1 呼出で 2 台分を指示し、交渉も bot1/bot2 の両 actor＝:28-29）。trace 全体に `robot:bot1` を貼ると `robot:bot1`/`robot:bot2` が両方集約されサイクル単位で識別不能になるため、**robot は generation/observation 単位の属性**にする（設計 §4.2）。score も tag を持てないため、robot/role/provider/gen_id は score の metadata に複製する（[doc08 §セッション命名規則](08-llm-bridge-common.md#セッション命名規則) の score 注記と同方針）。

| レベル | tags（低カーディナリティ文字列） | metadata（ネスト JSON 可） |
|---|---|---|
| trace（1サイクル） | `[provider, mode, "prompt:<name>", env=<v>]` | `{gen_id, traffic_mode, scenario, run_id}` |
| observation（1 actor / 1 tool） | `["robot:bot1","role:character"]` / `["robot:commander","role:commander"]` / `["tool:dispatch_task"]` | `{robot, role, actor, negotiation_id?, turn_index?, gen_id, trace_id}` |

trace tag の正本は doc08 §trace 所有と doc20 §8 であり、本節は character negotiation で使う observation 側の補足である。trace tag は正本どおり `provider` / `mode` / `prompt:<name>` / `env=<v>` を使い、provider 値は `claude/openai/google/xai` に揃える。robot/role/tool は trace 全体の tag ではなく observation 側の属性または observation-local tag として扱う。

```
provider={claude|openai|google|xai}   mode={A|B|C}   prompt:<name>   env=<dev|stg|prod>
robot={bot1|bot2|commander}   role={commander|character}   tool=<tool_name>
```

- `traffic_mode` と `mode` は同義 → trace tag は正本の `mode` 値に統一し、詳細文字列が必要なら metadata 側に置く。`mode`=`traffic_mode` は doc08 §セッション命名規則と整合する。
- `gen_id` は高カーディナリティ（数千の一意値）になるため **tag 化せず metadata のみ**に置く（設計 §4.3）。
- 本節は §交渉スコア（:257-266）の score metadata `{negotiation_id, mode}` を**補完**する（上書きしない）。交渉スコアは provider 比較軸を持たない記述指標であり、robot/role/provider/trace_id は observation/score の metadata 側に置いて両立する。

### Phase 3 で確認する未確定事項（断定しない）

以下は Hermes ビルトイン Langfuse プラグインの実挙動に依存し、Langfuse 公式 doc では確認できない（設計 §7.2 / Open Questions）。**Phase 3 実トレースで検証するまで暫定**であり、本 doc14 では断定しない:

- Hermes が inbound `metadata.trace_id` を尊重し、自身の generation を同一 trace 配下に合成するか。しなければ司令官 generation が別 trace になり、`trace_id`＋timestamp による Audit Log 突合へ降格する。
- キャラと司令官を同一 session に束ねる方針が UI replay / 比較で実用的か（§Langfuse 統合 の旧・別 session 方式からの変更点）。
- prompt 連携（Langfuse Prompt Management）・xAI Grok の cost カスタムモデル定義・SDK 4.9.0 スモークは **本 doc14 の範囲外**（doc08/doc13 と #88 の wo/bridge コードレーンが担当）。

## Mode A 常時会話・限定自己実行（設計確定 v1/v1.5）

本節は、既存の「司令官が最終承認する交渉モード」（§交渉型キャラクターLLM）を置き換える実装済み契約ではなく、次段階の Mode A を **自律会話評価ベンチ**として扱うための確定方針である。現行の安全前提（Policy Gate / Emergency Guardian / Nav2 safety）は維持し、任意座標の直接実行は解禁しない。

### 目的

- **Mode A**: bot 同士の常時会話で局所交通問題をできるだけ解決し、司令官は observer/critic に近づける。
- **Mode C**: 司令官・交通管理が主役。Mode A と同じ常時会話を入れる場合も、比較対象の中心は司令官/交通管理に置く。
- **評価観点**: 「会話が自然か」だけでなく、「司令官介入なしで安全に解けたか」を測れるようにする。

### 常時会話の4層

| 層 | 役割 | 実行権限 |
|---|---|---|
| 1. 自然文実況 | `bot1`/`bot2` の発話。UI、動画、Langfuse trace 用の人間向け表現 | なし。自然文から制御を推定しない |
| 2. 構造化合意 | 発話と別に `proposal` / `agreement` / `intent` を構造化して残す | なし。機械判定・監査・評価の入力 |
| 3. 限定自己実行 | bot 自身だけに効く安全行動を Self-Action Gate 経由で実行 | whitelist のみ |
| 4. 司令官 observer/critic | 会話、合意、実行結果を監視し、必要時だけ批評・却下・上書き | 非 whitelist 行動、失敗、危険、task 境界で介入 |

`observer/critic` は、司令官を最初から全行動の決定者にするのではなく、通常は観測者として transcript / proposal / telemetry を見て、逸脱時に critic として介入する役割である。critic の介入は「安全・task 契約・評価ログを壊す恐れがある時」に限定する。

### 自然文と構造化データの分離

常時会話では、LLM 出力を次の2系統に分ける。

| 出力 | 用途 | 例 |
|---|---|---|
| `speech` | UI / 動画 / 人間向け transcript | 「先に少し下がるので通ってください」 |
| structured event | 安全判定 / audit / eval / tool call | `{actor:"bot1", intent:"yield", action:"yield_to_retreat_A", target:"bot1", expires_at:...}` |

制御は structured event だけを見る。自然文は説明・演出・監査補助であり、自然文だけを根拠に Nav2/MCP へ送らない。曖昧な発話は `proposal` にはできるが、Self-Action Gate または司令官 critic が明示承認するまで実行しない。

LLM tool call を使う場合も、tool call は直接 Nav2/MCP を叩かず、structured event を生成する入口として扱う。実行可否は Self-Action Gate または司令官 critic が判定する。常時会話の persona model は高速・低コストな外部 LLM を許容するが、具体的な provider/model 名は config と live benchmark で選び、本 doc では固定しない。offline / CI では既存の scripted persona fallback を維持する。

### 限定自己実行 whitelist

Mode A の bot persona に渡せる実行権限は「自分だけに効く安全行動」に限定する。v1 で許可する候補は以下。

| action | 意味 | 主な gate 条件 |
|---|---|---|
| `wait_self` | 自分の Nav2 task を短時間待機/停止する | duration 上限、fresh state、emergency なし |
| `yield_to_retreat_A` | 自分が `retreat_A` へ退避する | named retreat のみ、current pose fresh、衝突余裕 |
| `yield_to_retreat_B` | 自分が `retreat_B` へ退避する | named retreat のみ、current pose fresh、衝突余裕 |
| `release_route_lock` | 自分が保持する route lock を解放する | lock owner が自分、解放後の task 再開条件を記録 |

ここでいう「座標実行権限」は、bot persona が任意の `(x, y, yaw)` や任意の destination を直接 Nav2/MCP に渡せる権限を指す。これは誤座標、古い状態、LLM の幻覚、task 横取りを引き起こすため Mode A v1 では禁止する。座標を伴う移動が必要な場合は、司令官または既存 Bridge/MCP/Policy Gate が owned config の `known_locations` / retreat 名を検証して実行する。

### 段階導入ロードマップ

Mode A の bot 権限は、会話品質ではなく **安全性・再現性・評価可能性**で段階昇格する。現在の着手範囲は **v1 + v1.5** とし、任意座標、task 変更、他 bot への直接命令は含めない。

| version | bot に許すこと | 明示的に禁止すること | 昇格条件 |
|---|---|---|---|
| v0 | 発話、提案、合意ログだけ。実行なし | Nav2/MCP への実行、task 変更 | transcript、structured event、Langfuse/audit が Gazebo なしの unit/replay で安定する |
| v1 | 自分だけの安全行動: `wait_self`、`yield_to_retreat_A/B`、`release_route_lock` | 任意座標、task 変更、他 bot への命令 | Gazebo で反射系を壊さず、合意後最小距離と `contract_violation_rate` が許容範囲 |
| v1.5 | task lifecycle event を正しく出す/読む。会話 episode と task/audit を結合する | 新 task 生成、目的地変更、route graph 変更 | `task_assigned/started/paused/resumed/completed/failed` が監査ログで追える |
| v2 | 局所契約の実行: どちらが先に通るか、誰が待つか、いつ再開するか | route graph 変更、座標指定 | commander-only より deadlock 解消時間、override 率、throughput が改善 |
| v3 | route lock / corridor lock の交渉と expiry 更新 | map 座標生成、未知地点移動 | lock owner、expiry、fairness が安定し、lock 詰まりが増えない |
| v4 | 司令官へ再経路 request を出す | bot 自身による直接再経路 | request→commander/Policy Gate/Nav2 検証の一方向フローが安定 |
| v5 | 検証済み route graph 上の候補選択 | raw `(x,y,yaw)` 直接指定 | route graph と map が実測済みで、全候補を検証器が reject/accept できる |

v1/v1.5 では、bot は「自分が待つ」「自分が退避する」「自分の lock を解放する」「task 状態を説明・記録する」だけを扱う。座標と task source of truth は map/config、Warehouse Orchestrator、commander、Nav2/Policy Gate 側に残す。

### 司令官の介入条件

Mode A では司令官を常時の実行者にしない。ただし、以下のいずれかでは observer から critic / commander に昇格する。

- bot 間合意が turn/time budget 内に成立しない。
- structured event が whitelist 外の行動を要求する。
- arbitrary coordinate、非許可 destination、他 bot への直接命令を含む。
- emergency active、state stale、battery critical、または安全 gate が reject した。
- 合意後に action timeout、route lock 不整合、near miss、contract violation が発生した。
- deadlock が同じ場所/同じ2台で再発する。
- task assigned / started / completed / failed など task lifecycle の境界に入った。
- human/operator が介入を要求した。
- periodic audit cycle で critic が不整合を検出した。

司令官の loop は「局所自己実行の期限」ではなく「監査・再割当・非局所判断の cadence」として扱う。常時会話と Self-Action Gate は event-driven に動き、司令官は必要な時だけ追従または上書きする。

### 会話対象のスケール方針

2台構成では全会話でも成立する。3台以上では全員会話が破綻するため、次の順序で対象を絞る。

1. 同じ corridor / route lock / conflict pair にいる近傍 bot。
2. 同じ task group または同じ交差点を共有する bot。
3. それ以外は shared blackboard に要約イベントだけ publish し、全 transcript を配らない。

将来の「トランシーバー」表現はこの routing 方針の UI/演出であり、実体は targeted transcript と shared blackboard の組み合わせにする。

### task 設計の境界

Mode A ベンチでは、bot 会話だけで task を勝手に作らない。task の生成・割当・完了判定は Warehouse Orchestrator または commander 側が source of truth を持つ。bot 会話が扱ってよいのは、与えられた task を安全に進めるための局所順序、譲り合い、待機、退避、route lock 解放である。

論理イベントとしては `task_assigned` / `task_started` / `task_paused` / `task_resumed` / `task_completed` / `task_failed` / `local_agreement_created` / `local_agreement_executed` / `commander_override` を記録できるようにする。ただし、これは本節時点では評価・監査 vocabulary であり、新しい ROS topic 名や `warehouse_interfaces` の凍結契約ではない。

### v1/v1.5 会話イベント要件

v1/v1.5 で必要な会話は、雑談ではなく task と局所交通 episode に紐づく。最低限、各 structured event は以下を持つ。

| field | 意味 |
|---|---|
| `event_id` | 監査・Langfuse・eval の突合 id |
| `episode_id` | deadlock、route conflict、task handoff など局所判断単位 |
| `task_id` | 関連 task。未割当の idle chatter では `null` 可 |
| `actor` | 発話/提案した bot |
| `audience` | 相手 bot、commander、blackboard のいずれか |
| `speech` | 人間向け自然文 |
| `intent` | `yield`、`wait`、`resume`、`release_lock`、`inform_task_state` など |
| `candidate_action` | 実行候補。v1 では whitelist action のみ |
| `requires_ack` | 相手の同意が必要か |
| `expires_at` | 古い合意を実行しないための期限 |
| `state_ref` | どの state snapshot / gen_id を見て判断したか |
| `verdict` | gate/critic の `accepted`、`rejected`、`unknown` |

`expires_at` と `state_ref` は安全判定に使うため、persona/model 出力をそのまま信じない。persona/model は `speech`、`intent`、`candidate_action` を提案できるが、Bridge は受信時刻、現在 `gen_id`、読み取った state snapshot から `expires_at` / `state_ref` を stamp して Self-Action Gate に渡す。model が遠い期限や未来 `gen_id` を自己申告しても、Bridge-stamped envelope で上書きする。

会話が発火する trigger は、`task_assigned`、`task_started`、route/corridor conflict、deadlock precursor、`task_paused`、`task_resumed`、`task_failed`、emergency recovery 後の再開確認とする。steady state の実況は UI 用に残せるが、評価 episode には入れない。

### v1/v1.5 実装メモ

- 会話 event は **内部 JSONL** `conversation_events.jsonl` に出す。既定 path は `WAREHOUSE_RUNTIME_DIR` 配下（dev では `/tmp/warehouse/conversation_events.jsonl`）、テスト/実験では `WAREHOUSE_CONVERSATION_EVENT_LOG_PATH` で上書きできる。
- この event log は frozen `warehouse_interfaces` でも ROS topic でもない。MCP command audit (`audit.jsonl`) に混ぜると既存 KPI の `executed/rejected/error` 集計が歪むため、v1/v1.5 では別ファイルにする。
- `wait_self` の初期上限は 5 秒とし、長すぎる待機は Self-Action Gate が reject する。将来 config 化する場合も、評価 run ごとに固定して比較する。
- `release_route_lock` は route-lock owner store がある場合だけ owner を確認して解放する。owner 不明の lock 解放は reject し、任意の route graph 変更には昇格しない。

## References

- [Anthropic - Multi-agent System Design](https://docs.anthropic.com/) -- 参照日: 2026-05-28
- 既存設計: `08a-llm-bridge-mode-a.md` デッドロック検出アルゴリズム
- 既存設計: `11c-traffic-mode-c.md` エスカレーション階層
