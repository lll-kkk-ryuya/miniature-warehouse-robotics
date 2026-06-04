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

1. 司令官が **`start_negotiation` MCP tool** を呼び出し（gen_id, starter, context を引数で渡す）→ Warehouse MCP Server が `/negotiation/start` トピックを publish（状況スナップショット + gen_id + 先手 `"starter": "bot1"` 同梱）
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

各キャラは自分の turn でない時は発話しない（subscribe するだけ）。タイムアウト時計は司令官側で持つ。

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
| 交渉タイムアウト | 8秒以内に合意できなければ司令官が独裁的に決定 |
| 提案フォーマット強制 | 自由文NG。JSON Schema で構造化を強制（キャラLLM のシステムプロンプトで指示 + Bridge側でvalidate） |
| 司令官の最終判断権 | 合意が安全条件（バッテリー/距離/Emergency中/Policy Gate）に反するなら拒否 → 司令官独自判断 |
| 発話回数制限 | 各キャラ最大4ターン（無限会話防止） |
| Emergency中の中断 | Emergency Guardian 発火時は交渉を即中断、proposal も破棄 |
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

- **Mode A** (08a-llm-bridge-mode-a.md:217): デッドロック検出・交通管理を自分で行う指示を含む。交渉モード発動も自分の判断
- **Mode C** (08c-llm-bridge-mode-c.md:116): タスク割当のみ。交通管理は Open-RMF。交渉モード発動は escalation フィールド見て判断

Phase 4 比較検証では「同じ Claude モデルでも Mode によりプロンプトが違う」点に注意。比較公平性のため、**同じ Mode 内で4社を比較**する原則とする。

## モデル選択

| ロール | モデル | 理由 |
|---|---|---|
| 司令官LLM | Opus（最新世代、Phase 4 で4社比較） | 戦略判断の質が重要 |
| キャラLLM | **Opus（最新世代）** | 全 Claude Opus 統一（16 §7）。max_tokens=60 で1〜2文。※旧 Haiku 設計（応答0.3-0.5s）からの変更で応答テンポは要実測 |

キャラLLM はテンポ重視（Opus 化に伴い応答速度の実測が必要、16 §7 検討事項）。Phase 4 の比較対象には含めない（演出専用扱い）。

## サイクル設計

司令官LLM は [project_mode_positioning](../../memory) 参照のサイクル長。キャラLLM は司令官と独立:

| ロール | サイクル | 駆動方式 |
|---|---|---|
| 司令官（Mode A） | 3秒 | レスポンス駆動（応答後1秒待機） |
| 司令官（Mode C） | 5秒 | レスポンス駆動（応答後3秒待機） |
| キャラLLM 実況 | 4〜8秒 + イベント駆動 | 独立ループ |
| キャラLLM 交渉中 | 相手の発話到着で発火 | イベント駆動 |

Bot1キャラ / Bot2キャラ / 司令官 の3プロセスが完全並行動作。

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

- キャラLLMの呼出しも Langfuse に trace 記録
- セッション名: `demo_{mode}_{scenario}_character_{bot_id}_{datetime}`
- 司令官 trace とは別 trace で分離（比較分析時に切り分け可能）
- 交渉モードでは Bot1/Bot2 のtraceを `negotiation_id` で紐付け

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

- Emergency Guardian が `/emergency/event` を発行する際、同時に `/negotiation/abort` も発行
- キャラLLM はこのトピックを subscribe しており、即時に交渉を中断 + proposal を破棄
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

## References

- [Anthropic - Multi-agent System Design](https://docs.anthropic.com/) -- 参照日: 2026-05-28
- 既存設計: `08a-llm-bridge-mode-a.md` デッドロック検出アルゴリズム
- 既存設計: `11c-traffic-mode-c.md` エスカレーション階層
