# LLM Bridge Node -- Mode A/B（LLM単独交通管理）

作成日: 2026-05-21
更新日: 2026-05-25

> **関連ドキュメント**:
> - [08 - LLM Bridge 共通設計](../architecture/08-llm-bridge-common.md) -- 共通インターフェース・フォールバック・Langfuse等
> - [08c - LLM Bridge Mode C](../mode-c/08c-llm-bridge-mode-c.md) -- LLM + Open-RMF
> - [12 - 共通基盤](../architecture/12-infrastructure-common.md) -- Emergency Guardian, State Cache
> - [15 - MCPプラットフォーム](../architecture/15-mcp-platform.md) -- Policy Gate, Warehouse MCP Server
> - [12a - システム統合 Mode A/B](12a-integration-mode-a.md) -- Nav2 Bridge, systemd構成

## アーキテクチャ（Mode A/B）

Mode A/BではOpen-RMFを使用せず、Claude（LLM）が交通管理を含む全判断を行う。

```
┌─ Mode A/B アーキテクチャ ──────────────────────────────────┐
│                                                            │
│  State Cache Node（別プロセス、100ms周期）                   │
│  ├── /bot{n}/odom, amcl_pose, battery 購読                 │
│  └── → /tmp/warehouse_state.json（atomic write）           │
│                                                            │
│  Emergency Guardian（別プロセス、50ms周期、LLM非経由）       │
│  └── 距離・バッテリー・blocked監視 → Nav2 cancel + cmd_vel停止│
│                                                            │
│  LLM Bridge Node（3秒タイマー）                             │
│  └── POST → Hermes Gateway                                 │
│                                                            │
│  Hermes Gateway（daemon, port 8642）                        │
│  ├── LLM推論（4社切替可能）                                 │
│  └── MCP → Warehouse MCP Server                            │
│                                                            │
│  Warehouse MCP Server（自作）                               │
│  ├── Policy Gate（全コマンド検証）                           │
│  └── Nav2 Bridge（BasicNavigator）→ Nav2                   │
│                                                            │
│  State Cache → LLM Bridge → Hermes → MCP                  │
│    → Warehouse MCP → Nav2 Bridge → Nav2                    │
└────────────────────────────────────────────────────────────┘
```

## 入力: LLMに送る状況データ（Mode A/B）

Mode A/BではClaude自身が交通管理を行うため、velocity、heading、predicted_position_3s、obstacle_ahead、obstacle_distance を含める。

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
      "velocity": {"linear": 0.1, "angular": 0.0},
      "heading": 1.57,
      "predicted_position_3s": {"x": 0.6, "y": 0.5},
      "status": "moving",
      "current_task": "shelf_1 → berth_A",
      "battery": 85,
      "obstacle_ahead": false,
      "obstacle_distance": null
    },
    "bot2": {
      "position": {"x": 1.2, "y": 0.7},
      "velocity": {"linear": 0.0, "angular": 0.0},
      "heading": 4.71,
      "predicted_position_3s": {"x": 1.2, "y": 0.7},
      "status": "blocked",
      "current_task": "shelf_2 → shipping_station",
      "battery": 72,
      "obstacle_ahead": true,
      "obstacle_distance": 0.15
    }
  },
  "pending_tasks": [
    {"id": "task_3", "from": "shelf_3", "to": "berth_B"}
  ],
  "history": [
    {"turn": 41, "action": "bot1 navigate shelf_1", "result": "success"},
    {"turn": 40, "action": "bot2 navigate shelf_2", "result": "blocked"}
  ]
}
```

## predicted_position_3s について

`predicted_position_3s`は、Mode A/B（Open-RMFなし）でClaude自身が交通管理を行う場合の補助データ。

**計算場所**: LLM Bridge Node が State Cache JSON の `pose`（position + yaw）と `velocity` から線形外挿で計算する。State Cache Node 側には含めない（Mode C では不要なフィールドのため）。同様に `obstacle_ahead` / `obstacle_distance` も LLM Bridge Node が `/bot{n}/scan` から計算する。

### 計算方法（線形外挿）

```python
# LLM Bridge Node 内で計算
predicted_x = position_x + velocity_linear * cos(heading) * 3.0
predicted_y = position_y + velocity_linear * sin(heading) * 3.0
```

### 目的と必要場面

| 場面 | positionだけの場合 | predicted_position_3sがある場合 |
|------|-------------------|-------------------------------|
| 渋滞予防 | 「2台は0.6m離れている、問題なし」→ 3秒後にデッドロック | 「3秒後に同じ通路で鉢合わせ」→ 今のうちに迂回指示 |
| タスク割当 | 現在位置だけで近いBotに割当 | 3秒後の位置で判断 → より効率的な割当 |
| 充電判断 | 「まだ23%ある」→ 先延ばし | 「充電ステーションから離れている方向」→ 今戻すべき |

### 限界（Claudeには参考値として提供）

- 曲がり角で右折する予定でも直進方向を予測する
- 壁の手前で停止するはずでも壁の向こう側を予測する
- ゴールに到着して停止する場合でも通過した先を予測する

精密な予測（Nav2の計画経路上の3秒後位置）は計算が複雑かつ経路自体が随時変わるため採用しない。Claudeの戦略判断には「2台が近づいている/離れている」程度の方向性情報で十分であり、精密な衝突回避はNav2（50ms）が担当する。

## 出力: LLMが返す指示データ（Mode A/B）

Claude自身が交通管理も行うため、`via`（経由ルート）や `wait`（待機）を含む。

```json
{
  "reasoning": "Bot2前方に障害物。通路B経由で迂回。Bot1を3秒待機させて通路の衝突を防ぐ",
  "commands": [
    {"bot": "bot1", "action": "wait",     "duration": 3},
    {"bot": "bot2", "action": "navigate", "destination": "shipping_station", "via": "route_B"},
    {"bot": "bot1", "action": "navigate", "destination": "berth_A"}
  ],
  "priority_explanation": "安全性 > 効率性。まず衝突回避を確保してからタスク再開"
}
```

## アクション定義（Mode A/B）

| action | パラメータ | 動作 |
|--------|----------|------|
| `navigate` | `destination` | 目的地へ移動（経路はNav2が決定） |
| `navigate` | `destination`, `via` | 経由地点付き移動（Claudeが経路を指定） |
| `wait` | `duration`（秒） | 指定秒数停止 |
| `stop` | -- | 緊急停止（Nav2キャンセル） |
| `yield` | `retreat_to` | 退避（デッドロック解消用後退） |
| `charge` | -- | 充電ステーションへ移動 |

## アクション → MCP ツール マッピング

Mode A/B では、Claude の出力 JSON 内の `action` を Warehouse MCP Server のツール呼出しに変換する。7ツールの定義は `../architecture/15-mcp-platform.md` を参照。

### マッピング表

| LLM 出力 action | MCP ツール | パラメータ変換 | Nav2 Bridge エンドポイント |
|-----------------|-----------|---------------|--------------------------|
| `navigate` (via なし) | `dispatch_task` | `dropoff=destination, robot=bot` | `POST /api/v1/navigate` |
| `navigate` (via あり) | `dispatch_task` | `dropoff=destination, robot=bot, via=route` | `POST /api/v1/navigate` (via付き) |
| `wait` | `dispatch_task` | `action="wait", robot=bot, duration=N` | `POST /api/v1/wait` |
| `stop` | `cancel_task` | `task_id="current:{bot}"` | `POST /api/v1/stop` |
| `yield` | `dispatch_task` | `action="yield", robot=bot, dropoff=retreat_to` | `POST /api/v1/navigate` (退避先) |
| `charge` | `send_to_charging` | `robot=bot` | `POST /api/v1/navigate` (charging_station) |

### 設計課題と解決策

**課題1: `dispatch_task` に `via` パラメータがない**

解決: `dispatch_task` に `via: str | None = None` を追加。Mode C では Open-RMF が経路を決定するため無視される。

```python
# Mode A: Claude が via を指定
dispatch_task(dropoff="shelf_1", robot="bot1", via="route_B")

# Mode C: via は無視（Open-RMFが経路決定）
dispatch_task(dropoff="shelf_1")  # via 未指定
```

**課題2: `wait` に対応する MCP ツールがない**

解決: `dispatch_task` に `action` パラメータを追加（デフォルト `"deliver"`）。`action="wait"` の場合、Nav2 Bridge が待機処理に分岐する。新ツール追加不要。

`action="wait"` 時は `pickup` と `dropoff` は不要だが、MCP ツールのシグネチャ上は必須パラメータのため、`pickup="_wait"`, `dropoff="_wait"` の予約値を使用する。Policy Gate は `action="wait"` 時に場所名検証をスキップする。

```python
# Mode A: Claude が wait を指示
dispatch_task(pickup="_wait", dropoff="_wait", action="wait", robot="bot1", duration=3.0)
# → Policy Gate: action="wait" → pickup/dropoff 検証スキップ
# → Nav2 Bridge: POST /api/v1/wait {"robot": "bot1", "duration": 3.0}
```

**課題3: `stop` に `task_id` が必要だが LLM 出力に含まれない**

解決: `cancel_task` が `"current:{robot}"` 形式の task_id を受け付ける。Warehouse MCP Server 内部の `active_tasks: dict[str, str]`（robot → task_id）から実際の task_id を解決する。

```python
# Claude の出力: {"bot": "bot1", "action": "stop"}
# → MCP ツール呼出し:
cancel_task(task_id="current:bot1")
# → Warehouse MCP Server 内部: active_tasks["bot1"] = "nav_003" → cancel_task("nav_003")
```

### dispatch_task 拡張後のシグネチャ

```python
dispatch_task(
    pickup: str,
    dropoff: str,
    priority: str = "normal",
    robot: str | None = None,
    # --- Mode A/B 拡張（Mode C では無視） ---
    via: str | None = None,        # 経由ルート名
    action: str = "deliver",       # "deliver" | "wait" | "yield"
    duration: float | None = None  # action="wait" 時の待機秒数
)
```

Mode C 互換性: `via`, `action`, `duration` を無視。Open-RMF Fleet Adapter が `pickup`/`dropoff` のみ使用。トークンコスト影響: 約30トークン増（3パラメータ追加分）。

## システムプロンプト（Mode A/B）

```
あなたは倉庫ロボット2台の司令官AIです。

## 倉庫レイアウト
- 1.8m × 0.9m のミニチュア倉庫
- 棚1 (0.2, 0.3), 棚2 (0.7, 0.3), 棚3 (1.2, 0.3)
- バースA (0.2, 0.8), バースB (0.7, 0.8)
- 出荷ステーション (0.2, 0.1), 充電ステーション (1.2, 0.1)
- 通路幅200mm（すれ違い不可）

## ルール
- 2台が同じ通路に入らないよう制御する
- 障害物がある場合は迂回ルートを指示する
- バッテリー管理（3段階）:
  - 10%以下: 緊急停止（Policy Gateが全コマンド拒否、Emergency Guardianが自動停止）
  - 10-20%: 新規タスク割当禁止、充電ステーションへの移動を推奨
  - 20-30%: 次タスク割当禁止、充電候補として検討
- 安全性 > 効率性（衝突回避を最優先）

## 安全機構（必ず守る）
- 状況JSON の `gen_id` フィールドを、すべての MCP tool 呼出しの `gen_id` 引数にそのまま渡してください（B-3 安全機構、`15-mcp-platform.md §2` 参照）
- デッドロックを検出した場合（08a-llm-bridge-mode-a.md デッドロック検出アルゴリズム）、自分で解消を試みる前に `start_negotiation` ツールでキャラLLM交渉を発動できます（任意、`14-character-llm-negotiation.md` 参照）
- `negotiation_proposal` が状況JSONに含まれていれば、その提案を検証し、安全条件（バッテリー/距離/Emergency中でない）を満たすなら採用してください

## 出力形式（必ずこのJSONで返す）
{
  "reasoning": "判断理由を日本語で説明",
  "commands": [
    {"bot": "bot1", "action": "navigate|wait|stop|yield|charge", "destination": "場所名", "duration": 秒数, "via": "経由ルート", "retreat_to": "退避先"}
  ],
  "priority_explanation": "判断の優先順位の説明"
}
```

## デッドロック検出アルゴリズム（Mode A/B）

Mode A/B では Open-RMF の Traffic Schedule がないため、デッドロックの検出と解消を Claude が担当する。通路幅200mm（すれ違い不可）のミニチュア倉庫では、2台が通路の両端から進入した場合にデッドロックが発生する。

### デッドロックの定義

2台が互いの進路を塞ぎ、どちらも前進できない状態。本プロジェクトでは以下の条件が**全て**満たされた場合にデッドロックと判断する:

| 条件 | 判定値 | 根拠 |
|------|--------|------|
| 2台とも `status` が `"blocked"` | — | 両方が進めない |
| 2台の距離が 0.4m 以内 | `distance < 0.4` | 通路内で対面している。**注意**: Emergency Guardian は 0.3m で緊急停止を発動する。0.3-0.4m の帯域では Claude のデッドロック解消シーケンスが Emergency Guardian の介入で中断される可能性がある。中断後は次の3秒サイクルで Claude が再判断する |
| 2台の `heading` が対向 | `|heading差| > 2.5 rad` | 互いの方向を向いている（π ≈ 3.14）。**注意**: 本閾値は直線通路での対向デッドロックを対象とする。T字路での90度対向（heading差 ≈ 1.57rad）は本条件では検出されないが、Emergency Guardian の blocked timeout（10秒）でフォールバック検出される |

### 検出パターン（situation JSON から）

Claude は3秒周期の situation JSON から以下の3パターンでデッドロックを検出する:

**パターン1: 相対位置 + 対向 heading**
```json
{
  "bot1": {"position": {"x": 0.5, "y": 0.5}, "heading": 0.0, "status": "blocked"},
  "bot2": {"position": {"x": 0.7, "y": 0.5}, "heading": 3.14, "status": "blocked"}
}
```
→ 距離 0.2m、heading 差 3.14 rad → デッドロック

**パターン2: blocked 持続（2サイクル以上）**
```json
{
  "history": [
    {"turn": 41, "action": "bot1 navigate shelf_1", "result": "blocked"},
    {"turn": 40, "action": "bot1 navigate shelf_1", "result": "blocked"}
  ]
}
```
→ 2ターン連続 blocked → デッドロックの兆候

**パターン3: predicted_position_3s の収束**
```json
{
  "bot1": {"position": {"x": 0.5, "y": 0.5}, "predicted_position_3s": {"x": 0.7, "y": 0.5}},
  "bot2": {"position": {"x": 0.9, "y": 0.5}, "predicted_position_3s": {"x": 0.7, "y": 0.5}}
}
```
→ 3秒後の予測位置が同一地点に収束 → 衝突/デッドロック予兆

### システムプロンプトへの追加指示

Mode A/B 用システムプロンプトに以下を追加する:

```
## デッドロック検出ルール
以下の条件が全て満たされたらデッドロックと判断:
1. 2台とも status が "blocked"
2. 2台の距離が 0.4m 以内
3. 2台の heading が対向（差が 2.5rad 以上）

デッドロック検出時の解消手順:
1. 優先度が低い方のタスクのロボットに yield を指示
   - retreat_to は最寄りの退避ポイント（通路入口等）
2. 優先度が高い方のロボットに wait を指示（duration=5秒）
3. 優先度が同じ場合: task_id が小さい方（先着）を優先

predicted_position_3s が同一地点に収束している場合は、
デッドロック予兆として事前に回避（wait または via で迂回）。
```

### 解消シーケンス

```
t=0s   Claude: bot1=blocked, bot2=blocked, 距離0.3m, heading差3.1rad
         → デッドロック検出
         → bot2 (low priority) に yield(retreat_to="route_B_start")
         → bot1 に wait(duration=5)

t=0-1s Warehouse MCP Server:
         dispatch_task(action="yield", robot="bot2", dropoff="route_B_start")
         dispatch_task(action="wait", robot="bot1", duration=5)

t=1-3s Nav2 Bridge:
         bot2: Nav2 cancel → route_B_start へ移動開始
         bot1: Nav2 cancel → 5秒待機

t=3-4s bot2 が退避完了（route_B_start 到着）
t=5s   bot1 の wait 完了 → 通路を通過

t=6s   Claude（次サイクル）:
         bot1 は通過済み → 元タスク継続
         bot2 に navigate（別ルート経由で元目的地へ）
```

### フォールバック（Claude が検出失敗した場合）

Claude が3秒サイクルでデッドロックを見逃した場合の安全ネット:

| 段階 | 担当 | 条件 | 動作 |
|------|------|------|------|
| 1 | Emergency Guardian | `blocked > 10秒` | Nav2 recovery behavior 発動（spin → backup → replan） |
| 2 | Emergency Guardian | recovery 失敗 | `/emergency/event` 発行（`"type": "blocked_timeout"`） |
| 3 | Claude（次サイクル） | emergency 情報受信 | 強制的にデッドロック解消判断（yield + wait） |
| 4 | Emergency Guardian | `blocked > 30秒` | Nav2 cancel + cmd_vel 停止（安全停止） |

Emergency Guardian の `BLOCKED_TIMEOUT = 10秒` は Claude の3サイクル（9秒）分の余裕がある。Claude が3サイクル連続で見逃した場合のみ Emergency Guardian が介入する。

## 経由ルート（WAYPOINTS）

LLMが `"via": "route_B"` と指示した場合に使用する中間地点:

```python
WAYPOINTS = {
    "route_A": {"x": 0.45, "y": 0.5},  # 通路A中間点
    "route_B": {"x": 0.95, "y": 0.5},  # 通路B中間点
    "route_C": {"x": 0.6, "y": 0.15},  # 通路C（横断）中間点
}
```

※座標はジオラマの実測後に確定する。

## Multi-Robot Costmap Layer（Mode A/B用衝突回避）

Mode A/BではOpen-RMFが交通管理を行わないため、Multi-Robot Costmap Layer（自作）でNav2レベルの衝突回避を補強する。

実装方法:
- Nav2 Costmap Pluginとして自作（C++）
- または、仮想LaserScanを生成して標準obstacle_layerに注入（Python可）

実装Phase:
1. Phase 2a: LiDARの直接検出のみ（自作なし、正面は検出可能）
2. Phase 2b: Multi-Robot Layerを追加（死角の問題を解消）
3. Phase 3後半〜: モードC（Open-RMF）導入時はMulti-Robot Layer不要

## Nav2 Simple Commander の活用

LLM Bridge Node から Nav2 にゴールを送信する際は、`nav2_simple_commander` を使う。直接トピックをPublishするよりもエラーハンドリングが容易。

**注意**: 以下のコード例は設計意図の説明用。実装では Warehouse MCP Server 経由に置き換わる（`../architecture/15-mcp-platform.md` 参照）。

```python
# NOTE: 設計説明用。実装では Warehouse MCP Server (dispatch_task等) を使用する。
from nav2_simple_commander.robot_navigator import BasicNavigator
from geometry_msgs.msg import PoseStamped

class CommandParser:
    def __init__(self):
        self.navigators = {
            "bot1": BasicNavigator(namespace="bot1"),
            "bot2": BasicNavigator(namespace="bot2"),
        }

    def execute_navigate(self, bot: str, destination: str, via: str | None = None):
        """LLMの navigate 指示を実行"""
        nav = self.navigators[bot]
        goal = PoseStamped()
        goal.header.frame_id = "map"
        coords = LOCATIONS[destination]
        goal.pose.position.x = coords["x"]
        goal.pose.position.y = coords["y"]

        if via:
            # 経由地点がある場合はウェイポイント走行
            waypoint = PoseStamped()
            waypoint.header.frame_id = "map"
            via_coords = WAYPOINTS[via]
            waypoint.pose.position.x = via_coords["x"]
            waypoint.pose.position.y = via_coords["y"]
            nav.goThroughPoses([waypoint, goal])
        else:
            nav.goToPose(goal)

    def execute_wait(self, bot: str, duration: float):
        """LLMの wait 指示を実行"""
        nav = self.navigators[bot]
        nav.cancelTask()  # 現在のタスクを停止
        # duration秒後に再開（タイマーで管理）

    def execute_stop(self, bot: str):
        """LLMの stop 指示を実行"""
        nav = self.navigators[bot]
        nav.cancelTask()

    def execute_yield(self, bot: str, retreat_to: str):
        """LLMの yield 指示を実行（デッドロック解消用後退）"""
        nav = self.navigators[bot]
        nav.cancelTask()
        self.execute_navigate(bot, retreat_to)

    def execute_charge(self, bot: str):
        """LLMの charge 指示を実行（充電ステーションへ移動）"""
        self.execute_navigate(bot, "charging_station")
```

## References

- [Nav2 Simple Commander API](https://docs.nav2.org/commander_api/index.html) -- 参照日: 2026-05-21（※実装ではWarehouse MCP Serverに置き換え）
- [rclpy -- ROS 2 Python Client Library](https://docs.ros2.org/latest/api/rclpy/) -- 参照日: 2026-05-21
