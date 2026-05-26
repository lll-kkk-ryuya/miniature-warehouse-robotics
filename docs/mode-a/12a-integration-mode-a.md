# システム統合 — Mode A/B（LLM単独 / 自作ルールベース）

作成日: 2026-05-25
原本: `docs/12-hermes-agent-integration.md`

> **関連ドキュメント**
> - [共通インフラストラクチャ設計](../architecture/12-infrastructure-common.md)
> - [システム統合 — Mode C（LLM + Open-RMF）](../mode-c/12c-integration-mode-c.md)

---

## 概要

Mode A（LLM単独）および Mode B（自作ルールベース）では、Warehouse MCP Server が Nav2 Bridge を介して直接 Nav2 を制御する。Open-RMF は使用しない。

- **Mode A**: Warehouse MCP Server → Nav2 Bridge → Nav2（交通管理なし、LLMが全判断）
- **Mode B**: Warehouse MCP Server → SimpleTrafficManager + Nav2 Bridge → Nav2（通路ロックによる簡易交通管理）

Warehouse MCP Server は rclpy を持たないため、Nav2 制御用の薄い Bridge プロセス（rclpy + BasicNavigator）を別途起動し、REST 経由で呼び出す。これにより「MCP Server に rclpy を入れない」原則を維持する。

---

## サイクル依存図（Mode A/B）

共通原則は `../architecture/12-infrastructure-common.md` のサイクル依存関係セクションを参照。以下は Mode A/B 固有のデータフロー。

```
┌───────────────────────────────────────────────────────────────┐
│  Hard Real-time（LLM非経由、止まったら危険）                    │
│                                                                │
│  [ORBBEC MS200 LiDAR] ──/scan──→ [Nav2 Controller 50ms] ──/cmd_vel──→ Motor
│        │                              ↑                        │
│        └──→ [AMCL 5-10Hz] ──/amcl_pose──┘                     │
│                                                                │
│  [Emergency Guardian 50ms]                                     │
│        ├── /amcl_pose, /battery, /odom 購読                    │
│        ├── 危険検知 → Nav2 cancel + cmd_vel=0（即時介入）       │
│        └── /emergency/event → State Cache に push              │
│                                                                │
│  [ESP32 MCU] ── 速度0.3m/sクランプ、近接停止（最終防衛線）      │
└───────────────────────────────────────────────────────────────┘
                          │
                          │ push（状態を下位→上位へ流す）
                          ▼
┌───────────────────────────────────────────────────────────────┐
│  Soft Real-time（集約・補助、100ms〜10Hz）                      │
│                                                                │
│  [State Cache Node 100ms]                                      │
│        ├── /amcl_pose, /battery, /odom, /emergency/event 購読  │
│        └── → /tmp/warehouse_state.json（atomic write）         │
│                                                                │
│  [VirtualScanNode 10Hz]（相手ロボット認識）                     │
│        ├── 相手 /amcl_pose 購読                                │
│        └── → /{own_robot}/virtual_scan → Nav2 obstacle_layer   │
│           （Nav2が相手ロボットを障害物として回避）               │
└───────────────────────────────────────────────────────────────┘
                          │
                          │ pull（3秒ごとにスナップショット取得）
                          ▼
┌───────────────────────────────────────────────────────────────┐
│  Non Real-time（戦略+交通管理、停止しても物理は安全）           │
│                                                                │
│  [LLM Bridge Node 3秒タイマー]                                 │
│        ├── State Cache JSON からスナップショット取得            │
│        ├── predicted_position_3s を線形外挿で計算              │
│        ├── emergency 情報があれば付加                           │
│        └── → POST → Hermes Gateway                             │
│                         │                                      │
│  [Hermes Gateway]       │ LLM API（1-3秒）                     │
│        └── MCP → [Warehouse MCP Server]                        │
│                    ├── Policy Gate 検証                         │
│                    └── → Nav2 Bridge (REST, port 8645) → Nav2  │
│                                                                │
│  ※ Mode Aでは Claude が交通管理も担当（デッドロック検出、       │
│    via指定、wait/yield）。Open-RMF は起動しない。              │
└───────────────────────────────────────────────────────────────┘
```

**Mode A/B の特徴**: Soft RT 層に Open-RMF がなく、代わりに VirtualScanNode が Nav2 レベルの衝突回避を補助する。交通管理（デッドロック検出、経路選択、待機指示）は Non RT 層の Claude が3秒周期で担当する。Claude が停止した場合、交通管理はなくなるが、Emergency Guardian（10秒 blocked timeout）と VirtualScanNode（Nav2 障害物回避）がフォールバックとして機能する。

---

## Mode A/B用 プロセス構成図

```
┌──────────────────────────────────────────────────────────┐
│  Jetson Orin Nano Super (8GB)                             │
│                                                           │
│  ┌──────────────────────────────────────────────────────┐ │
│  │  Emergency Guardian（rclpy、50ms周期目標、LLM非経由） │ │
│  │  ├── 2台間距離監視（< 0.3m → Nav2 cancel要求+cmd_vel停止）│ │
│  │  ├── バッテリー監視（< 10% → Nav2 cancel要求+cmd_vel停止）│ │
│  │  ├── blocked監視（> 10秒 → Nav2リカバリー要求）      │ │
│  │  ├── Nav2 goal cancel + cmd_vel停止（LLM非経由）     │ │
│  │  └── /emergency/event Publish（構造化イベント）       │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                           │
│  ┌──────────────────────────────────────────────────────┐ │
│  │  State Cache Node（rclpy、100ms周期目標）              │ │
│  │  ├── /bot{n}/amcl_pose, battery, odom 購読           │ │
│  │  ├── /emergency/event 購読                           │ │
│  │  └── → /tmp/warehouse_state.json（atomic write）     │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                           │
│  ┌──────────────────────────────────────────────────────┐ │
│  │  LLM Bridge Node（rclpy、3秒タイマー）               │ │
│  │  ├── 3秒タイマー → Hermes Gateway POST               │ │
│  │  ├── /emergency/event 受信 → 次回POSTに緊急情報付加   │ │
│  │  └── /llm/reasoning, /llm/command Publish             │ │
│  └───────────────────────┬──────────────────────────────┘ │
│                          │ HTTP POST                       │
│  ┌───────────────────────▼──────────────────────────────┐ │
│  │  Hermes Gateway（daemon, port 8642）                 │ │
│  │  ├── Provider: Claude / GPT / Gemini / Grok          │ │
│  │  ├── Memory（判断パターン永続化、FTS5）               │ │
│  │  ├── Skills（成功パターン学習）                       │ │
│  │  ├── Langfuse Plugin（LLMトレース自動記録）           │ │
│  │  └── MCP Client → Warehouse MCP Server（stdio）      │ │
│  └───────────────────────┬──────────────────────────────┘ │
│                          │ MCP (stdio)                     │
│  ┌───────────────────────▼──────────────────────────────┐ │
│  │  Warehouse MCP Server（自作、rclpy不要）             │ │
│  │  ├── Policy Gate（全コマンド検証、安全弁）            │ │
│  │  ├── State Cache読取（/tmp/warehouse_state.json）     │ │
│  │  ├── Command Audit Log（ローカルログ）                │ │
│  │  ├── traffic_mode切替                                 │ │
│  │  │   ├── "none"     → Nav2 Bridge経由（Mode A）       │ │
│  │  │   └── "simple"   → SimpleTrafficManager            │ │
│  │  │                     + Nav2 Bridge経由（Mode B）     │ │
│  │  └── 6ツール（共通設計参照）                          │ │
│  └───────────────────────┬──────────────────────────────┘ │
│                          │ REST (localhost)                 │
│  ┌───────────────────────▼──────────────────────────────┐ │
│  │  Nav2 Bridge（rclpy + BasicNavigator、REST受付）     │ │
│  │  ├── REST API → Nav2 Action Client 変換              │ │
│  │  ├── /bot1 namespace → BasicNavigator                │ │
│  │  └── /bot2 namespace → BasicNavigator                │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                           │
│  Nav2 × 2 (/bot1, /bot2)  |  AMCL × 2  |  SLAM           │
│  micro-ROS Agent  |  ESP32 × 2（最下層安全系）             │
└──────────────────────────────────────────────────────────┘
```

---

## Nav2 Bridge プロセス設計

Warehouse MCP Server は rclpy を持たないため、Nav2 との通信を担う薄い Bridge プロセスを Mode A/B 専用で起動する。

```python
class Nav2Bridge:
    """REST → Nav2 Action の変換。Mode A/B専用。

    Warehouse MCP Server からの REST リクエストを受け取り、
    Nav2 の BasicNavigator API を通じてロボットにゴールを送信する。
    Mode C では Open-RMF の Fleet Adapter が同等の役割を担うため不要。
    """

    def __init__(self):
        self.navigators = {
            "bot1": BasicNavigator(namespace="bot1"),
            "bot2": BasicNavigator(namespace="bot2"),
        }

    def navigate(self, robot: str, destination: str, via: str | None = None):
        """場所名を座標に変換し、Nav2 ゴールを送信する。

        Args:
            robot: ロボットID ("bot1" or "bot2")
            destination: 目的地の場所名 ("shelf_1", "berth_A" 等)
            via: 経由地の場所名（オプション）
        """
        ...

    def wait(self, robot: str, duration: float):
        """指定時間その場で待機する。

        Args:
            robot: ロボットID
            duration: 待機時間（秒）
        """
        ...

    def stop(self, robot: str):
        """Nav2 ゴールをキャンセルし、ロボットを停止させる。

        Args:
            robot: ロボットID
        """
        ...
```

**Mode A/B での Nav2 制御方法**: Warehouse MCP Server は rclpy を持たないため、Mode A/B では Nav2 制御用の薄い Bridge プロセス（rclpy + BasicNavigator）を別途起動し、REST 経由で呼び出す。これにより「MCP Server に rclpy を入れない」原則を維持する。具体的な実装方式は Phase 0.5 の Day 3-4 で検証する。

---

### Nav2 Bridge REST API 仕様

Nav2 Bridge は FastAPI（uvicorn）で実装し、rclpy と asyncio を共存させる。

**rclpy + asyncio 共存パターン**: rclpy の `executor.spin()` を `threading.Thread` で分離し、FastAPI（uvicorn）はメインスレッドの asyncio イベントループで動作させる（ROS 2 公式推奨パターン）。`rclpy.spin_once()` を asyncio から直接呼ぶ方式は callback delivery の遅延を招くため不採用。

```python
import threading
import rclpy
from rclpy.executors import MultiThreadedExecutor

# rclpy スレッド（バックグラウンド）
def ros_spin(executor):
    executor.spin()

executor = MultiThreadedExecutor()
executor.add_node(nav2_bridge_node)
ros_thread = threading.Thread(target=ros_spin, args=(executor,), daemon=True)
ros_thread.start()

# FastAPI（メインスレッド）
uvicorn.run(app, host="0.0.0.0", port=8645)
```

**ベースURL**: `http://localhost:8645`

#### エンドポイント一覧

| メソッド | パス | 用途 |
|---------|------|------|
| POST | `/api/v1/navigate` | ゴール送信（via対応） |
| POST | `/api/v1/wait` | 指定時間待機 |
| POST | `/api/v1/stop` | Nav2 cancel + cmd_vel停止 |
| GET | `/api/v1/status/{robot}` | ゴール進捗取得 |
| GET | `/health` | ヘルスチェック |

#### POST /api/v1/navigate

ゴールを Nav2 に送信する。`via` が指定された場合はウェイポイント走行。

**Request:**
```json
{
  "robot": "bot1",
  "destination": "shelf_1",
  "via": "route_B"
}
```

**Response (200):**
```json
{
  "task_id": "nav_001",
  "status": "accepted",
  "robot": "bot1",
  "destination": "shelf_1"
}
```

ゴール送信後すぐにレスポンスを返す（fire-and-forget）。ゴール完了は `GET /api/v1/status/{robot}` で確認、または State Cache Node 経由で次の3秒サイクルに反映される。

#### POST /api/v1/wait

指定秒数の待機を実行する。現在のNav2ゴールがある場合は一時停止する。

**Request:**
```json
{
  "robot": "bot1",
  "duration": 3.0
}
```

**Response (200):**
```json
{
  "task_id": "wait_001",
  "status": "accepted",
  "robot": "bot1",
  "duration": 3.0
}
```

内部実装: `cancelTask()` で現在ゴールを停止 → `asyncio.sleep(duration)` → 完了。待機中も Emergency Guardian の安全監視は継続する。

#### POST /api/v1/stop

Nav2 ゴールをキャンセルし、cmd_vel に停止指令を送信する。

**Request:**
```json
{
  "robot": "bot1"
}
```

**Response (200):**
```json
{
  "status": "stopped",
  "cancelled_task_id": "nav_001",
  "robot": "bot1"
}
```

#### GET /api/v1/status/{robot}

指定ロボットの現在のナビゲーション状態を返す。

**Response (200):**
```json
{
  "robot": "bot1",
  "nav_status": "navigating",
  "current_task_id": "nav_001",
  "destination": "shelf_1",
  "progress": 0.6,
  "eta_seconds": 2.1
}
```

`nav_status` の値:

| 値 | 意味 |
|----|------|
| `idle` | ゴールなし |
| `navigating` | ゴールへ移動中 |
| `waiting` | wait 実行中 |
| `succeeded` | ゴール到着完了 |
| `failed` | ゴール失敗（Nav2がreject/abort） |

#### GET /health

Nav2 Bridge のヘルスチェック。systemd のヘルスチェックに使用。

**Response (200):**
```json
{
  "status": "ok",
  "navigators": {
    "bot1": "ready",
    "bot2": "ready"
  },
  "uptime_seconds": 1234.5
}
```

#### エラーコード

| HTTP | エラーコード | 意味 |
|------|------------|------|
| 400 | `INVALID_ROBOT` | 不明なロボットID（bot1/bot2以外） |
| 400 | `INVALID_LOCATION` | 不明な場所名（LOCATIONS辞書に存在しない） |
| 400 | `INVALID_VIA` | 不明な経由ルート名（WAYPOINTS辞書に存在しない） |
| 400 | `INVALID_DURATION` | duration が 0以下 または 30秒超 |
| 409 | `ALREADY_NAVIGATING` | 既にゴール実行中（stop してから再送信） |
| 503 | `NAV2_NOT_READY` | Nav2 が未起動またはlifecycle inactive |

**エラーレスポンス形式:**
```json
{
  "status": "error",
  "error_code": "INVALID_LOCATION",
  "detail": "Unknown location: shelf_99"
}
```

#### ゴールフィードバック設計

Nav2 Bridge 内部で `BasicNavigator.isTaskComplete()` を200ms周期でポーリングし、ゴールの完了/失敗を検出する。

```python
class Nav2Bridge:
    def __init__(self):
        self.active_tasks: dict[str, GoalState] = {}  # robot -> GoalState

    async def monitor_goal(self, robot: str, task_id: str):
        """200ms周期でNav2ゴール完了を監視"""
        while not self.navigators[robot].isTaskComplete():
            await asyncio.sleep(0.2)

        result = self.navigators[robot].getResult()
        self.active_tasks[robot].status = (
            "succeeded" if result == TaskResult.SUCCEEDED
            else "failed"
        )
        # ROS 2トピックでState Cacheに通知
        self.goal_result_pub.publish(json.dumps({
            "robot": robot,
            "task_id": task_id,
            "result": self.active_tasks[robot].status
        }))
```

完了通知は `/nav2_bridge/goal_result` トピック（`std_msgs/String`）で State Cache Node に伝達される。State Cache Node が `nav_status` を更新し、次の3秒サイクルで Claude が確認できる。

---

## Mode A/B用 systemd構成（Jetson起動時）

```
# 起動順序（After= で依存関係を制御）

1. micro_ros_agent.service          ← 最初（ロボット通信）
2. nav2_bot1.service                ← micro-ROS後
3. nav2_bot2.service                ← micro-ROS後
4. emergency_guardian.service       ← Nav2後（安全監視開始）
5. state_cache.service              ← Nav2後（状態収集開始）
6. nav2_bridge.service              ← Nav2後（Mode A/B専用、REST → Nav2 変換、port 8645）
7. hermes_gateway.service           ← 独立（LLM準備）
8. warehouse_mcp.service            ← hermes後（MCP接続）
9. llm_bridge.service               ← 全て起動後（3秒ループ開始）
```

各サービス共通:
- `Restart=on-failure`（落ちたら再起動）
- `StandardOutput=journal`（journaldにログ）
- ヘルスチェック（各ノードが `/health` トピック or REST endpointを公開）

---

## Mode A/B用 通信フロー・タイミング図

```
t=0.0s   LLM Bridge Node: 3秒タイマー発火
t=0.0s   LLM Bridge Node: emergency情報があれば付加
t=0.1s   LLM Bridge Node: POST /v1/chat/completions（Hermes Gateway）
t=0.2s   Hermes: LLM API呼出し開始
t=1.5s   Hermes: LLM応答受信
t=1.5s   Hermes: MCP → Warehouse MCP Server ツール呼出し
t=1.5s   Warehouse MCP: Policy Gate 検証
t=1.6s   Warehouse MCP: Nav2 Bridge REST API に dispatch
t=1.6s   Nav2 Bridge: BasicNavigator → Nav2 ゴール送信
t=1.7s   Nav2: ロボット移動開始
t=2.0s   Hermes: run完了 → LLM Bridge Node にレスポンス返却
t=2.0s   LLM Bridge Node: /llm/reasoning, /llm/command Publish
t=3.0s   次のサイクル開始
```

（並行して）
```
常時    Emergency Guardian: 50ms周期で安全監視
常時    State Cache Node: 100ms周期で状態ファイル更新
```

---

## Mode A/B固有のリスクと対策

| リスク | 影響 | 対策 | フォールバック |
|--------|------|------|---------------|
| Claudeの3秒遅延で衝突 | 高 | Multi-Robot Costmap Layerで緩和。各ロボットのfootprintを他方のcostmapに反映し、Nav2レベルで回避経路を自動計算 | Emergency Guardian（50ms周期）が最終安全網 |
| Nav2 Bridge REST通信の遅延 | 低 | localhost通信なので低リスク（< 1ms） | subprocess fallback |
| Claudeのデッドロック検出精度 | 中 | predicted_position_3sで補助。現在の速度ベクトルから3秒後の予測位置を計算し、LLMに提供することで判断精度を向上 | Emergency Guardian の blocked タイムアウト（10秒）で検出 |
| Hermes Gateway メモリ > 2GB | 高 | Phase 0.5で `htop` 計測 | Skills/Memory無効化で軽量化。最終手段は案D（SDK直接） |
| Hermes Gateway ARM64非対応 | 低 | **2026-05-26調査: Pure Python wheel、Docker ARM64対応済み、Jetsonユーザー実在。動作する可能性が高い** | 案D（SDK直接） |
| レイテンシ > 4秒 | 中 | ポーリング間隔を適応的に調整 | 5秒間隔に延長 |
| State Cache ファイルI/O遅延 | 低 | tmpfs使用（RAMディスク） | REST API方式に変更 |
