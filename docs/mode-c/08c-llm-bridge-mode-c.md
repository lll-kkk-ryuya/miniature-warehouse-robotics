# LLM Bridge Node -- Mode C（LLM + Open-RMF）

作成日: 2026-05-21
更新日: 2026-05-25

> **関連ドキュメント**:
> - [08 - LLM Bridge 共通設計](../architecture/08-llm-bridge-common.md) -- 共通インターフェース・フォールバック・Langfuse等
> - [08a - LLM Bridge Mode A/B](../mode-a/08a-llm-bridge-mode-a.md) -- LLM単独交通管理
> - [12 - 共通基盤](../architecture/12-infrastructure-common.md) -- Emergency Guardian, State Cache
> - [15 - MCPプラットフォーム](../architecture/15-mcp-platform.md) -- Policy Gate, Warehouse MCP Server
> - [12c - システム統合 Mode C](12c-integration-mode-c.md) -- Fleet Adapter, systemd構成
> - [11c - 交通管理 Mode C](11c-traffic-mode-c.md) -- RMFTrafficManager

## アーキテクチャ（Mode C）

Mode CではOpen-RMFが交通管理を担当し、Claude（LLM）はタスク割当・優先順位・バッテリー管理のみを行う。

```
┌─ Mode C アーキテクチャ ────────────────────────────────────┐
│                                                            │
│  State Cache Node（別プロセス、100ms周期）                   │
│  ├── /bot{n}/odom, amcl_pose, battery 購読                 │
│  └── → /tmp/warehouse/state.json（atomic write）           │
│                                                            │
│  Emergency Guardian（別プロセス、50ms周期、LLM非経由）       │
│  └── 距離・バッテリー・blocked監視 → Nav2 cancel + cmd_vel停止│
│                                                            │
│  LLM Bridge Node（5秒サイクル、Mode C）                     │
│  └── POST → Hermes Gateway                                 │
│                                                            │
│  Hermes Gateway（daemon, port 8642）                        │
│  ├── LLM推論（4社切替可能）                                 │
│  └── MCP → Warehouse MCP Server                            │
│                                                            │
│  Warehouse MCP Server（自作）                               │
│  ├── Policy Gate（全コマンド検証）                           │
│  └── Open-RMF → Fleet Adapter → Nav2                       │
│                                                            │
│  State Cache → LLM Bridge → Hermes → MCP                  │
│    → Warehouse MCP → Open-RMF → Fleet Adapter → Nav2      │
└────────────────────────────────────────────────────────────┘
```

## 入力: LLMに送る状況データ（Mode C）

Mode Cでは交通管理フィールドを省略し、Open-RMFからの交通状態サマリーを含める。Claudeはタスク割当・優先順位・バッテリー管理のみを判断する。

```json
{
  "timestamp": "2026-06-15T14:30:05",
  "turn": 42,
  "gen_id": 142,
  "warehouse": {
    "layout": "1.8m x 0.9m, 3 shelves, 2 aisles (200mm, no passing)"
  },
  "robots": {
    "bot1": {
      "position": {"x": 0.3, "y": 0.5},
      "status": "moving",
      "current_task": "shelf_1 → berth_A",
      "battery": 85
    },
    "bot2": {
      "position": {"x": 1.2, "y": 0.7},
      "status": "idle",
      "current_task": null,
      "battery": 72
    }
  },
  "traffic": {
    "mode": "open-rmf",
    "aisles": {
      "route_A": {"status": "occupied", "robot": "bot1", "eta_clear_s": 4.2},
      "route_B": {"status": "free"}
    },
    "conflicts": [],
    "escalation": null
  },
  "pending_tasks": [
    {"id": "task_3", "from": "shelf_3", "to": "berth_B"}
  ],
  "history": [
    {"turn": 41, "action": "bot1 navigate shelf_1", "result": "success"}
  ]
}
```

Mode Cのsituation JSONはMode A/B版より約200トークン少ない（predicted_position_3s、velocity、heading、obstacle_ahead、obstacle_distance を省略）。

## 出力: LLMが返す指示データ（Mode C）

Claudeはタスク割当のみ。経路選択・衝突回避・待機指示はOpen-RMFが自動処理するため、`via` パラメータは不要。

```json
{
  "reasoning": "task_3が新規到着。Bot2はidleなのでtask_3を割り当てる。Bot1は現在のtask_1を継続",
  "commands": [
    {"bot": "bot2", "action": "navigate", "destination": "shelf_3"},
    {"bot": "bot1", "action": "navigate", "destination": "berth_A"}
  ],
  "priority_explanation": "Bot2がidleかつshelf_3に近いため、task_3をBot2に割当"
}
```

## アクション定義（Mode C）

| action | パラメータ | 動作 |
|--------|----------|------|
| `navigate` | `destination` | 目的地へ移動（経路はOpen-RMF/Nav2が決定） |
| `stop` | -- | 緊急停止（Nav2キャンセル） |
| `charge` | -- | 充電ステーションへ移動 |

**Mode CでClaudeが使うアクション**: `navigate`（目的地のみ）、`stop`、`charge` の3つ。

## システムプロンプト（Mode C）

```
あなたは倉庫ロボット2台の戦略司令官AIです。

## あなたの役割
タスク割当・優先順位変更・バッテリー管理のみを行う。
経路選択・衝突回避・待機指示は交通管理システム（Open-RMF）が自動処理するため、あなたは関与しない。

## 倉庫レイアウト
- 1.8m × 0.9m のミニチュア倉庫
- 棚1 (0.2, 0.3), 棚2 (0.7, 0.3), 棚3 (1.2, 0.3)
- バースA (0.2, 0.8), バースB (0.7, 0.8)
- 出荷ステーション (0.2, 0.1), 充電ステーション (1.2, 0.1)

## ルール
- 未処理タスクを割り当てる（pickup/dropoffと優先度を指定。ロボットの選択はアロケーターに任せる＝robot指定なし。デバッグ時のみrobot指定可）
- バッテリー管理（3段階）:
  - 10%以下: 緊急停止（Policy Gateが全コマンド拒否、Emergency Guardianが自動停止）
  - 10-20%: 新規タスク割当禁止、充電ステーションへの移動を推奨
  - 20-30%: 次タスク割当禁止、充電候補として検討
- 交通管理（衝突回避・経路選択・待機）には関与しない — Open-RMFが自動処理する
- escalationフィールドがnullでない場合のみ、Open-RMFが解決できなかった問題に対処する

## 安全機構（必ず守る）
- 状況JSON の `gen_id` フィールドを、すべての MCP tool 呼出しの `gen_id` 引数にそのまま渡してください（B-3 安全機構、`15-mcp-platform.md §2` 参照）
- `escalation` が立っているときは `start_negotiation` ツールでキャラLLM交渉を発動できます（Mode C ではクライマックス演出用、任意）
- `negotiation_proposal` が状況JSONに含まれていれば、その提案を検証し、安全条件を満たすなら採用してください

## 使用可能なアクション
- navigate: 目的地を指定（経路はOpen-RMFが決定）
- stop: 緊急停止
- charge: 充電ステーションへ移動

## 出力形式（必ずこのJSONで返す）
{
  "reasoning": "判断理由を日本語で説明",
  "commands": [
    {"bot": "bot1", "action": "navigate|stop|charge", "destination": "場所名"}
  ],
  "priority_explanation": "判断の優先順位の説明"
}
```

## 3層責任分担（Mode C: Open-RMF主方針）

### 基本原則

**時間で分ける。** 即時判断はNav2/Open-RMF、戦略判断はClaude（LLM）。

```
Nav2（50ms）     → Open-RMF（即時）  → Claude（1-3秒）
物理的安全        交通管理             戦略判断
「ぶつからない」   「鉢合わせない」      「何をやるか」
```

### Nav2 の責任（50ms -- 物理安全）

| 判断内容 | 担当ノード | 応答速度 |
|---------|-----------|---------|
| 壁・棚との衝突回避 | Nav2 DWB/MPPI | 50ms |
| ゴールまでの経路追従・速度調整 | Nav2 Controller | 50ms |
| 一時的なスタックからのリカバリー | Nav2 Recovery（3回失敗でOpen-RMFにエスカレーション） | 数秒 |
| 速度上限の強制（0.3m/s） | ESP32 / Nav2パラメータ | 即時 |
| 到着検知 | Nav2 | 即時 |

### Open-RMF の責任（即時 -- 交通管理）

| 判断内容 | 具体例 |
|---------|--------|
| 経路選択 | 「通路Aが最短、通路A経由で」 |
| 衝突予測 | 「Bot1とBot2が3秒後に通路Aで鉢合わせ」 |
| 衝突回避（待機指示） | 「Bot2を5秒待たせてBot1を先に通す」 |
| 通路のロック管理 | 「通路Aをbot1に割当、解放後にbot2に割当」 |
| デッドロック検出・解消 | 「Bot1を後退させてBot2が先に通過」 |
| 迂回ルート計算 | 「通路Aが塞がっている、通路B経由に切替」 |

### Claude（LLM）の責任（1-3秒 -- 戦略判断のみ）

| 判断内容 | 具体例 |
|---------|--------|
| タスク割当 | 「Bot1はshelf_1に近いからtask_1を担当」 |
| タスク優先順位変更 | 「緊急タスクを優先、Bot2が引き継げ」 |
| バッテリー管理 | 「Bot1は充電ステーションへ戻れ」 |
| エスカレーション対応 | 「Open-RMFが3回失敗、タスク自体を変更する」 |

**注意**: Mode CではClaudeは交通管理に関与しない。経路選択・衝突回避・迂回ルートは全てOpen-RMFが即時処理する。Claudeは「どの荷物を誰が運ぶか」だけを判断する。

## エスカレーション階層

```
レベル0: Emergency Guardian（50ms周期、LLM非経由、全レベル横串）
  常時監視し、危険を検知したら即時に物理停止を実行する。
  - 2台が0.3m以内に接近 → Nav2 cancel + cmd_vel=0
  - blocked > 10秒 / バッテリー < 10% → 強制停止
  検知事象は /emergency/event で State Cache 経由で
  次サイクルの situation JSON に付加されClaudeに通知される。
  （これは「上位への問い合わせ」ではなく即時介入。詳細は
   12-infrastructure-common.md の安全レイヤー設計を参照）

レベル1: Nav2が物理的にstuck
  Nav2リカバリー（Spin, BackUp）を3回試行
  → 失敗 → Open-RMFに報告（別経路を計算）
  → Open-RMFも失敗 → Claudeにエスカレーション（目的地変更を判断）

レベル2: Open-RMFの交通調整が失敗
  Open-RMFが3回調整を試行
  → 失敗 → Claudeにエスカレーション（タスク変更を判断）

レベル3: 両方同時に発生
  → 安全優先: まずNav2が全ロボットを停止
  → Open-RMFが状況を整理
  → Claudeが全体状況を見てタスク再割当

各レイヤーは「自分で解決できない問題」だけを上位に投げる。
レベル0のEmergency Guardianは階層と並行して常時稼働する。
```

## 交通管理レイヤー（Open-RMF -- Mode C主方針）

交通管理はOpen-RMF（Mode C）を主方針とする。プラグイン方式でMode A/Bへの切替も可能（YouTube比較検証用）。詳細設計は `11c-traffic-mode-c.md` を参照。

### Claudeとの通信ルール

- Claudeは「何をするか（WHAT: タスク割当・優先順位・バッテリー）」のみ
- Open-RMFは「どう実現するか（HOW: 経路・衝突回避・待機時間）」を担当
- Mode Cでは、Claudeの指示はOpen-RMF Task API経由でNav2に送る（Nav2 MCP Serverは使わない）
- Open-RMFの調整中（conflicts.status = "in_progress"）はClaudeは介入しない
- Open-RMFの調整が3回失敗した場合のみClaudeにエスカレーション
- Claudeが強制的にOpen-RMFを無視する仕組み（override等）は設けない

## 通信フロー（タイミング）

**注意**: 以下は実装アーキテクチャ（doc12準拠）の通信フロー。

```
t=0.0s   LLM Bridge Node: State Cache JSON読取（/tmp/warehouse/state.json）
t=0.0s   LLM Bridge Node: emergency情報があれば付加
t=0.1s   LLM Bridge Node: POST → Hermes Gateway
t=0.2s   Hermes Gateway: LLM API呼出し開始
t=1.5s   Hermes Gateway: LLM応答受信 → MCP → Warehouse MCP Server ツール呼出し
t=1.5s   Warehouse MCP Server: Policy Gate検証 → Open-RMF/Nav2へ実行
t=2.0s   Hermes Gateway: run完了 → LLM Bridge Node にレスポンス返却
t=2.0s   LLM Bridge Node: /llm/reasoning, /llm/command Publish
t=5.0s   次のサイクル開始（Mode C: 応答後3秒待機 → サイクル長 ~5秒）

（並行して常時動作: Emergency Guardian 50ms周期、State Cache Node 100ms周期）
```

## References

- [Open-RMF Documentation](https://osrf.github.io/ros2multirobotbook/intro.html) -- 参照日: 2026-05-21
- [rclpy -- ROS 2 Python Client Library](https://docs.ros2.org/latest/api/rclpy/) -- 参照日: 2026-05-21
