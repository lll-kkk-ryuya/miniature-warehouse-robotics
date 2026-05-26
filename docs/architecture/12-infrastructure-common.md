# 共通インフラストラクチャ設計

作成日: 2026-05-25
原本: `docs/12-hermes-agent-integration.md`

> **関連ドキュメント**
> - [システム統合 — Mode A/B（LLM単独 / 自作ルールベース）](../mode-a/12a-integration-mode-a.md)
> - [システム統合 — Mode C（LLM + Open-RMF）](../mode-c/12c-integration-mode-c.md)

---

## 概要

LLM Bridge の基盤として Hermes Agent（NousResearch）の Gateway モードを採用し、自作 Warehouse MCP Server でロボット制御を行う。LLMは戦略判断（タスク割当・優先順位・バッテリー管理）のみを担当し、交通管理はモードに応じた仕組み（Open-RMF / SimpleTrafficManager / なし）、物理安全は Nav2 + Emergency Guardian が担保する。

### 設計原則

```
止めるのはローカル安全系（LLM非経由）
次にどうするかをLLMが考える（3秒サイクル）
LLMのコマンドは直接実行せず Policy Gate を通す
```

### 採用の経緯

`08-llm-bridge-common.md` の論理設計（入出力JSON、アクション定義、フォールバック等）を維持しつつ、以下を Hermes Agent で代替:

| 自作設計のコンポーネント | Hermes Agent での代替 |
|------------------------|----------------------|
| LLMClient 基底クラス + 4プロバイダー実装 | Hermes 内蔵プロバイダー（20+社対応） |
| DecisionLog 構造 + ファイル出力 | Hermes 内蔵 Langfuse プラグイン |
| LLM切替のコード変更 | config.yaml 1行変更 |

---

## サイクル依存関係

各コンポーネントは異なる時間スケール（3層）で動作する。本セクションでは層間の依存方向と縮退運転の**共通原則**を定義する。各モード固有の具体的なデータフロー図は以下を参照:

- Mode A/B: `../mode-a/12a-integration-mode-a.md` のサイクル依存図
- Mode C: `../mode-c/12c-integration-mode-c.md` のサイクル依存図

### 3層の定義

| 層 | 時間スケール | LLM依存 | 構成要素 |
|----|------------|:-------:|---------|
| **Hard Real-time** | 即時〜50ms | ✕ | ESP32（速度クランプ、近接停止）、Nav2 Controller（経路追従、障害物回避）、AMCL（自己位置推定）、Emergency Guardian（距離・バッテリー・blocked監視） |
| **Soft Real-time** | 100ms〜イベント駆動 | ✕ | State Cache Node（状態集約）、Open-RMF（Mode C: 交通調停）、VirtualScanNode（Mode A/B: 仮想LaserScan） |
| **Non Real-time** | 3秒 | ◎ | LLM Bridge Node、Hermes Gateway、Warehouse MCP Server |

### 依存関係の原則

| 原則 | 内容 |
|------|------|
| **速い層 → 遅い層** | push で状態を流す。State Cache が時間スケールの段差（50ms ↔ 3秒）を吸収するバッファ |
| **遅い層 → 速い層** | 指示を出すが、速い層は安全上の理由で無視・上書きできる（Emergency Guardian の Nav2 cancel 等） |
| **下位層の独立性** | どの上位層が停止しても、下位層は自分の周期で動き続ける（縮退運転） |
| **上位層の依存性** | 下位層（Nav2, ESP32）が停止した場合、上位層（LLM）は意味を失う → 安全停止 |

### 縮退運転パターン

| 停止したコンポーネント | 影響 | 縮退動作 |
|----------------------|------|---------|
| LLM API / Hermes Gateway | 戦略判断なし | Nav2 が現在のゴールを継続。Emergency Guardian は独立動作 |
| 交通管理層（Open-RMF / VirtualScanNode） | 交通調停なし | Nav2 が単独で障害物回避。デッドロック時は Emergency Guardian が10秒で介入 |
| State Cache Node | LLM が古い状態で判断 | Emergency Guardian は /amcl_pose 直接購読のため影響なし。LLM Bridge は stale 検出で指示を保留 |
| Nav2 | 経路追従・障害物回避なし | ESP32 の速度クランプ + 近接停止が最終防衛線 |
| ESP32 / micro-ROS | 物理制御不能 | 全系統停止。バッテリー切断が唯一の安全策 |

---

## 安全レイヤー（4層）

```
Layer 0: micro-ROS / ESP32（ハードウェア安全 — 最終防衛線）
  └── ToF/LiDAR近接物体検出 → モータPWM停止 / motor enable OFF（MCU内、通信不要）
  └── 速度上限 0.3 m/s（MCU内で強制、ROS 2側の cmd_vel 値に関わらず上限クランプ）
  └── bumper / 近接センサ → モータ停止（MCU内、OS・ROS 2非依存）

Layer 1: Emergency Guardian（ソフトウェア安全、50ms周期目標）
  └── AMCL距離監視 → Nav2 goal cancel要求 + cmd_vel停止 → /emergency/event 発行
  └── バッテリー3段階ポリシー
  └── blocked タイムアウト検出
  └── ※ ハードリアルタイム保証ではない。最終防衛線はLayer 0

Layer 2: Open-RMF（Mode C）/ SimpleTrafficManager（Mode B）/ なし（Mode A）（交通管理、イベント駆動・非LLM）
  └── 経路衝突予測 → 待機・迂回指示
  └── デッドロック検出・解消

Layer 3: Claude / Hermes（戦略判断、3秒周期）
  └── 事後の説明・タスク再割当・復旧方針の提案
  └── LLM APIはrate limit/timeout/overloadの影響を受けるため制御deadline保証なし
```

### Emergency Guardian 詳細

```python
class EmergencyGuardian(Node):
    """50ms周期の安全監視。LLMに依存しない反射的安全系。"""

    DISTANCE_THRESHOLD = 0.3   # m, 2台間距離
    BATTERY_CRITICAL = 10      # %, Nav2 cancel要求 + cmd_vel停止
    BLOCKED_TIMEOUT = 10.0     # s, Nav2リカバリー発動

    def check_safety(self):
        # 1. 距離チェック
        dist = self.calc_distance(self.bot1_pose, self.bot2_pose)
        if dist < self.DISTANCE_THRESHOLD:
            self.emergency_stop("bot1", "near_collision")
            self.emergency_stop("bot2", "near_collision")

        # 2. バッテリーチェック
        for bot in ["bot1", "bot2"]:
            if self.battery[bot] < self.BATTERY_CRITICAL:
                self.emergency_stop(bot, "battery_critical")

        # 3. blocked チェック
        for bot in ["bot1", "bot2"]:
            if self.blocked_duration[bot] > self.BLOCKED_TIMEOUT:
                self.trigger_recovery(bot)

    def emergency_stop(self, bot, reason):
        """Nav2 goal cancel要求 + cmd_vel停止 + 構造化イベント発行

        注意: Nav2 cancelTask() は「タスク/goalのcancel要求」であり、
        物理的にその瞬間に停止することを保証するものではない。
        ロボットの制動距離・controller周期・通信遅延に依存する。
        物理停止の最終保証はESP32/MCU側のLayer 0が担う。
        """
        # 1. Nav2 goal cancel要求
        self.nav2_cancel[bot].cancel_goal()
        # 2. cmd_vel停止（Nav2 cancel応答を待たずに直接停止要求）
        stop_msg = Twist()  # all zeros
        self.cmd_vel_pubs[bot].publish(stop_msg)
        # 3. 構造化イベント発行
        event = {
            "event_id": f"emg-{datetime.now().strftime('%Y%m%d%H%M%S')}-{self.seq}",
            "robot": bot,
            "type": reason,
            "severity": "critical",
            "action_taken": ["nav2_goal_cancel", "cmd_vel_stop"],
            "timestamp": time.time(),
            "requires_llm_review": True
        }
        self.emergency_pub.publish(json.dumps(event))
```

### バッテリーポリシー（3段階）

以下の閾値はアプリケーション側のポリシーであり、ハードウェア仕様ではない。実機のバッテリー特性に応じて調整する。

| バッテリー残量 | Emergency Guardian | Policy Gate | Claude |
|--------------|-------------------|-------------|--------|
| < 10% | **Nav2 cancel要求 + cmd_vel停止**。最寄り安全地点へ退避 | 全コマンド拒否 | 事後通知のみ |
| 10-20% | 監視継続 | **新規タスク割当禁止**。実行中タスクは状況により継続可（dropoffまで残り僅かなら完了させる） | 充電指示を推奨 |
| 20-30% | 監視継続 | 新規タスク割当可 | 次タスク割当禁止、充電候補 |

---

## State Cache Node

### 設計

```python
class StateCacheNode(Node):
    """ROS 2トピックを購読し、集約JSONを100ms周期でファイル書出し"""

    def __init__(self):
        super().__init__('state_cache')
        self.state = {"robots": {}, "emergency": {"active": [], "history": []}}

        for bot in ["bot1", "bot2"]:
            self.create_subscription(
                PoseWithCovarianceStamped,
                f'/{bot}/amcl_pose',
                lambda msg, b=bot: self.update_pose(b, msg), 10)
            self.create_subscription(
                BatteryState,
                f'/{bot}/battery',
                lambda msg, b=bot: self.update_battery(b, msg), 10)
            self.create_subscription(
                Odometry,
                f'/{bot}/odom',
                lambda msg, b=bot: self.update_velocity(b, msg), 10)

        self.create_subscription(String, '/emergency/event',
                                 self.on_emergency, 10)

        self.create_timer(0.1, self.write_cache)  # 100ms

    def write_cache(self):
        """atomic write でファイル書出し"""
        tmp_path = "/tmp/warehouse_state.json.tmp"
        final_path = "/tmp/warehouse_state.json"

        with open(tmp_path, "w") as f:
            json.dump(self.state, f)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, final_path)
```

### State Cache JSON フォーマット

```json
{
  "robots": {
    "bot1": {
      "pose": {"x": 0.3, "y": 0.5, "yaw": 1.57},
      "velocity": {"linear": 0.1, "angular": 0.0},
      "battery": 85,
      "nav_status": "moving",
      "current_task": "t_041",
      "updated_at": 1710000000.123
    },
    "bot2": {
      "pose": {"x": 1.2, "y": 0.7, "yaw": 4.71},
      "velocity": {"linear": 0.0, "angular": 0.0},
      "battery": 72,
      "nav_status": "idle",
      "current_task": null,
      "updated_at": 1710000000.145
    }
  },
  "emergency": {
    "active": [],
    "history": [
      {
        "event_id": "emg-20260715-0001",
        "robot": "bot1",
        "type": "near_collision",
        "severity": "critical",
        "action_taken": ["nav2_goal_cancel", "cmd_vel_stop"],
        "timestamp": 1710000000.050,
        "requires_llm_review": true
      }
    ]
  }
}
```

### stale 判定

Warehouse MCP Server 側で、`updated_at` の経過時間に応じて状態を判定する:

```python
def get_fleet_status(self):
    state = self.read_state_cache()
    now = time.time()
    for bot, data in state["robots"].items():
        age = now - data["updated_at"]
        if age > 2.0:
            data["availability"] = "unavailable"  # 2秒以上 → 通信断の可能性
        elif age > 0.5:
            data["availability"] = "stale"         # 500ms以上 → 古い情報
        else:
            data["availability"] = "ok"
    return state
```

| 経過時間 | 状態 | Policy Gateの扱い |
|---------|------|------------------|
| < 500ms | `ok` | 通常通りコマンド受付 |
| 500ms - 2s | `stale` | dispatch_task拒否、cancel/charging は許可 |
| > 2s | `unavailable` | 全コマンド拒否、Claudeに通信断を通知 |

---

## Hermes Agent の構成

### 動作モード

Hermes Agent は **Gateway モード**（ヘッドレスデーモン）で動作する。

| モード | 用途 |
|--------|------|
| CLI/TUI (`hermes`) | 開発・デバッグ時の対話 |
| **Gateway** (`hermes gateway`) | **本番運用（HTTP API、daemon化）** |
| ACP (`hermes-acp`) | IDE統合（本PJでは不使用） |

### Gateway API

LLM Bridge Node は `POST /v1/chat/completions` で Hermes に状況を投入する:

```python
# LLM Bridge Node
resp = httpx.post("http://localhost:8642/v1/chat/completions", json={
    "messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(situation)}
    ],
    "model": "hermes-agent"
}, headers={
    "Authorization": f"Bearer {HERMES_API_KEY}",
    "Content-Type": "application/json"
})
```

- `Authorization: Bearer`: API_SERVER_KEY による認証（`.env` で設定）
- `role: system`: per-request system prompt（エフェメラル、コアプロンプトに追加）
- ツール呼出しはサーバーサイドで実行され、最終テキストのみ返却

**セッション管理**: `/v1/chat/completions` は stateless（公式仕様）。会話履歴は `messages` に含める。サーバー側の会話継続が必要な場合は `/v1/responses` の `previous_response_id` を使用する。Phase 0.5 で実際のセッション管理方式を検証する。

### プロバイダー設定

```yaml
# hermes config
providers:
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
    model: claude-sonnet-4
  openai:
    api_key: ${OPENAI_API_KEY}
    model: gpt-4o
  google:
    api_key: ${GOOGLE_API_KEY}
    model: gemini-2.5-flash
  xai:
    api_key: ${XAI_API_KEY}
    model: grok-4.3

active_provider: anthropic
```

切替: `active_provider` フィールドを変更するだけ。

### MCP設定（Warehouse MCP Server のみ）

```yaml
mcp_servers:
  warehouse:
    command: "python"
    args: ["-m", "warehouse_mcp_server"]
    tools:
      include:
        - dispatch_task
        - cancel_task
        - get_fleet_status
        - get_task_queue
        - send_to_charging
        - escalation_response
      prompts: false
      resources: false
```

`tools.include` で6ツールに絞り、トークンコストを最小化する（[Hermes MCP公式](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)）。

### トークンコスト見積もり

| 項目 | トークン/ターン |
|------|---------------|
| System Prompt | 約500 |
| Warehouse MCP ツール定義（6個） | 約500 |
| situation JSON（入力） | 約300 |
| LLM応答（出力） | 約200 |
| **合計** | **約1,500** |

10分デモ（200ターン）: 約300K トークン

| LLM | 推定コスト/デモ |
|-----|----------------|
| Claude Sonnet 4 | ~$0.45 |
| GPT-4o | ~$0.35 |
| Gemini 2.5 Flash | ~$0.20 |
| Grok 4.3 | ~$0.40（※価格未確定） |

全4社合計: 約$1.40/デモ。

### Hermes 付加機能

| 機能 | 活用 | 備考 |
|------|------|------|
| Memory | 「前回通路Aでデッドロックした」等の文脈記憶 | セッション横断で有効 |
| Skills | 限定的（モードCではパターンが少ない） | モードAでの比較時に活きる |
| Langfuse | 全LLM呼出しの自動トレース | 4社比較検証に必須 |

---

## Warehouse MCP Server

### ツール定義（6個）

```python
# ツール1: dispatch_task
dispatch_task(
    pickup: str,           # 場所名（"shelf_1"等）
    dropoff: str,          # 場所名（"berth_A"等）
    priority: str = "normal",  # "urgent" | "normal" | "low"
    robot: str | None = None,  # デフォルトはアロケーター割当
    # --- Mode A/B 拡張（Mode C では無視） ---
    via: str | None = None,        # 経由ルート名 ("route_A", "route_B" 等)
    action: str = "deliver",       # "deliver" | "wait" | "yield"
    duration: float | None = None  # action="wait" 時の待機秒数
)
# robot=None の場合、deterministic allocator が最適なロボットを選択
# Mode C では via, action, duration は無視（Open-RMFが経路を決定）

# ツール2: cancel_task
cancel_task(
    task_id: str  # タスクID または "current:{robot}" 形式（例: "current:bot1"）
)
# "current:{robot}" 指定時、Warehouse MCP Server が active_tasks[robot] から実際の task_id を解決

# ツール3: get_fleet_status
get_fleet_status()
# → State Cache + TrafficManager からの統合情報を返却

# ツール4: get_task_queue
get_task_queue()
# → 未割当・実行中・直近完了タスクの一覧

# ツール5: send_to_charging
send_to_charging(robot: str)

# ツール6: escalation_response
escalation_response(
    escalation_id: str,
    action: str,          # "reassign" | "cancel" | "retry"
    new_robot: str | None = None,
    reason: str = ""
)
```

### Mode A/B 用パラメータ利用パターン

Mode A/B では `dispatch_task` の拡張パラメータ（`via`, `action`, `duration`）を使用して、Open-RMF なしでの交通管理を実現する。Mode C ではこれらのパラメータは無視される（Open-RMF が経路・待機・迂回を自動処理するため）。

| LLM 出力 action | MCP ツール | パラメータ | Nav2 Bridge エンドポイント |
|-----------------|-----------|-----------|--------------------------|
| `navigate` (via なし) | `dispatch_task` | `dropoff=目的地` | `POST /api/v1/navigate` |
| `navigate` (via あり) | `dispatch_task` | `dropoff=目的地, via=経由ルート` | `POST /api/v1/navigate` (via付き) |
| `wait` | `dispatch_task` | `action="wait", duration=秒数` | `POST /api/v1/wait` |
| `yield` | `dispatch_task` | `action="yield", dropoff=退避先` | `POST /api/v1/navigate` (退避先) |
| `stop` | `cancel_task` | `task_id="current:{robot}"` | `POST /api/v1/stop` |
| `charge` | `send_to_charging` | `robot=対象` | `POST /api/v1/navigate` (charging_station) |

**トークンコスト影響**: `dispatch_task` に3パラメータ追加で約30トークン増。6ツール合計で約530トークン/ターン（従来約500）。

**`cancel_task` の `"current:{robot}"` 規約**: LLM の出力 JSON には `task_id` が含まれないため、`cancel_task("current:bot1")` と指定すると Warehouse MCP Server 内部の `active_tasks: dict[str, str]`（robot → task_id マッピング）から実際の task_id を解決する。

### モード切替（TrafficManager パターン）

```python
class WarehouseMCPServer:
    def __init__(self, config):
        MANAGERS = {
            "none": NoTrafficManager,       # Mode A: Nav2 Bridge経由（別プロセス）
            "simple": SimpleTrafficManager,  # Mode B: 通路ロック + Nav2 Bridge経由（別プロセス）
            "open-rmf": RMFTrafficManager,   # Mode C: Open-RMF API
        }
        self.traffic = MANAGERS[config["traffic_mode"]]()
        self.allocator = RobotAllocator(self.traffic, config)
        self.policy_gate = PolicyGate(self.traffic, config)
        self.audit_log = CommandAuditLog(config)

    def dispatch_task(self, pickup, dropoff, priority="normal", robot=None,
                      via=None, action="deliver", duration=None):
        # 1. Policy Gate（全コマンドの入口で必ず検証）
        result = self.policy_gate.validate_dispatch(robot, pickup, dropoff, priority, action)
        if result.rejected:
            self.audit_log.record("dispatch_task", "rejected", result.reason)
            return {"status": "rejected", "reason": result.reason}

        # 2. ロボット割当（robot=None の場合、deterministic allocatorが決定）
        if robot is None:
            robot = self.allocator.select_best(pickup, dropoff, priority)

        # 3. 実行（内部実装はモードとactionで異なる）
        if action == "wait":
            response = self.traffic.wait_robot(robot, duration)
        elif action == "yield":
            response = self.traffic.submit_task(robot, pickup, dropoff, priority)
        else:
            response = self.traffic.submit_task(robot, pickup, dropoff, priority)

        # 4. Audit Log
        self.audit_log.record("dispatch_task", "executed", response,
                              robot=robot, action=action)

        return response
```

LLM側のツール定義はモードによって変わらない。内部の実行先が透過的に切り替わる。

**注意**: Open-RMF標準のタスク割当フローでは、core RMF systemがfleet adaptersにavailability/statusを問い合わせ、各fleet adapterがbidを返し、RMFがwinning bidを決定する（[Fleet Adapter Tutorial](https://osrf.github.io/ros2multirobotbook/integration_fleets_adapter_tutorial.html)）。Task Dispatcherを無効化してClaudeに直接robot指定させる設計はこの標準フローから外れるため、`robot=None`（allocator/RMF bidding任せ）をデフォルトとする。

### Policy Gate

全ツールの入口に必ず通す:

```python
class PolicyGate:
    """LLMの指示を検証する安全弁。全MCPツールの入口に配置。"""

    def validate_dispatch(self, robot, pickup, dropoff, priority, action="deliver"):
        # action="wait" 時は場所名検証をスキップ（pickup/dropoff は "_wait" 予約値）
        if action == "wait":
            if robot is None:
                return Reject("action='wait' requires robot specification")
            # 場所名・同一地点チェックをスキップし、ロボット状態チェックへ進む
        else:
            # 場所名存在チェック
            if pickup not in self.known_locations:
                return Reject(f"Unknown location: {pickup}")
            if dropoff not in self.known_locations:
                return Reject(f"Unknown location: {dropoff}")

            # 同一地点チェック
            if pickup == dropoff:
                return Reject("Pickup and dropoff are the same")

        # ロボット状態チェック（指定時のみ）
        if robot:
            state = self.state_cache.get_robot(robot)
            if state is None:
                return Reject(f"Unknown robot: {robot}")
            availability = state.get("availability", "ok")
            if availability == "unavailable":
                return Reject(f"{robot} state is unavailable (no updates >2s)")
            if availability == "stale":
                return Reject(f"{robot} state is stale (no updates >500ms)")

            # バッテリーポリシー
            battery = state.get("battery", 100)
            if battery < 10:
                return Reject(f"{robot} battery critical ({battery}%)")
            if battery < 20:
                return Reject(f"{robot} battery low ({battery}%), no new tasks")

            # Emergency中のrobot禁止
            if self.is_in_emergency(robot):
                return Reject(f"{robot} is in emergency state")

        # レートリミット
        if robot and self.rate_limited(robot):
            return Reject(f"{robot} received command too recently")

        # タスク重複チェック（action="wait"/"yield" 時はスキップ）
        if action == "deliver" and self.duplicate_task(pickup, dropoff):
            return Reject(f"Duplicate task: {pickup} → {dropoff}")

        return Accept()

    def validate_cancel(self, task_id):
        if not self.task_exists(task_id):
            return Reject(f"Task {task_id} not found")
        if self.task_already_completed(task_id):
            return Reject(f"Task {task_id} already completed")
        return Accept()

    def validate_charging(self, robot):
        state = self.state_cache.get_robot(robot)
        if state and state.get("battery", 100) > 80:
            return Reject(f"{robot} battery is {state['battery']}%, charging not needed")
        return Accept()

    def validate_escalation(self, escalation_id, action):
        if not self.escalation_exists(escalation_id):
            return Reject(f"Escalation {escalation_id} not found")
        if self.escalation_already_resolved(escalation_id):
            return Reject(f"Escalation {escalation_id} already resolved")
        if action not in ["reassign", "cancel", "retry"]:
            return Reject(f"Unknown action: {action}")
        return Accept()
```

### Command Audit Log

Langfuse（LLMトレース）とは別に、ロボット制御側のローカルログ:

```python
class CommandAuditLog:
    """全MCPコマンドのローカルログ。Langfuseとは独立。"""

    def record(self, tool, result, detail, robot=None):
        entry = {
            "timestamp": time.time(),
            "tool": tool,
            "result": result,       # "executed" | "rejected" | "error"
            "detail": detail,
            "robot": robot,
            "traffic_mode": self.traffic_mode
        }
        # JSONLines形式でローカルファイルに追記
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
```

記録内容:
- Claudeが何を提案したか
- MCPツールが何を受け取ったか
- Policy Gateが通したか拒否したか（理由付き）
- 実際にOpen-RMF/Nav2へ何を送ったか
- 結果は成功したか

---

## Emergency後の状態同期

Emergency GuardianがNav2をcancelした後、MCP/Claudeとの状態同期が重要:

```
Emergency Guardian:
  bot1のNav2 goalをcancel
  ↓
  /emergency/event を Publish（構造化イベント）
  ↓
State Cache Node:
  /emergency/event を受信
  state["emergency"]["active"] に追加
  /tmp/warehouse_state.json に反映
  ↓
Warehouse MCP Server:
  次の get_fleet_status() で emergency 情報を含めてLLMに返す
  ↓
Claude（次の3秒サイクルで）:
  「bot1が緊急停止した。タスク再割当を検討」
```

emergency/event フォーマット:

```json
{
  "event_id": "emg-20260715-0001",
  "robot": "bot1",
  "type": "near_collision",
  "severity": "critical",
  "action_taken": ["nav2_goal_cancel", "cmd_vel_stop"],
  "timestamp": 1710000000.050,
  "requires_llm_review": true,
  "detail": {
    "distance": 0.25,
    "other_robot": "bot2",
    "bot1_position": {"x": 0.5, "y": 0.4},
    "bot2_position": {"x": 0.55, "y": 0.42}
  }
}
```

---

## 既存設計との整合性

### 変更なし（そのまま維持）

- `08-llm-bridge-common.md` のシステムプロンプト
- `08-llm-bridge-common.md` のフォールバック設計
- `08-llm-bridge-common.md` の比較検証ログ（Langfuse統合）
- `../mode-a/11a-traffic-mode-a.md` / `../mode-c/11c-traffic-mode-c.md` の TrafficManager インターフェース
- `../mode-a/11a-traffic-mode-a.md` / `../mode-c/11c-traffic-mode-c.md` の モードA/B/C 切替設計
- `../mode-a/11a-traffic-mode-a.md` / `../mode-c/11c-traffic-mode-c.md` の エスカレーション階層
- `03-software-architecture.md` の 3層判断モデル

### 変更あり

| 項目 | 旧 | 新 |
|------|-----|-----|
| Hermes統合方式 | Python Library モード（プロセス内） | Gateway daemon（別プロセス、HTTP API） |
| Nav2制御 | Nav2 MCP Server（ajtudela） | 自作 Warehouse MCP Server → モード別実行先 |
| 緊急制御 | 0.5秒サイクル（LLM経由） | Emergency Guardian（50ms周期目標、LLM非経由） |
| 状態取得 | State Collector（LLM Bridge内） | State Cache Node（別プロセス、atomic write） |
| コマンド検証 | Command Validator（LLM Bridge内） | Policy Gate（Warehouse MCP Server内） |
| warehouse_tools | Hermes Plugin | Warehouse MCP Server内のPython関数 |
| dispatch_task | robot必須 | robot=None（アロケーター割当がデフォルト） |

---

## Nav2 MCP Server の不採用

`ajtudela/nav2_mcp_server` は以下の理由で不採用とし、自作 Warehouse MCP Server に置き換え:

| 理由 | 詳細 |
|------|------|
| マルチロボット非対応 | シングルトン `BasicNavigator()` でnamespace指定不可 |
| 座標のみ | `navigate_to_pose(x, y, yaw)` で場所名不可 |
| モードCと不整合 | モードCでは Claude → Open-RMF → Nav2 の経路。LLMが直接Nav2を操作しない |

---

## 責務分離

| コンポーネント | 責務 | 周期（目標値） | rclpy | LLM |
|--------------|------|-------------|:---:|:---:|
| ESP32 / micro-ROS | ハードウェア安全停止（最終防衛線） | **即時（MCU内）** | ✕ | ✕ |
| Emergency Guardian | ソフトウェア安全監視 | **50ms周期（目標）** | ◎ | ✕ |
| State Cache Node | 状態集約・配信 | **100ms周期（目標）** | ◎ | ✕ |
| Open-RMF（Mode C）/ SimpleTrafficManager（Mode B） | 交通管理・経路調整 | **イベント駆動（非LLM、ハードRT保証なし）** | ◎ | ✕ |
| LLM Bridge Node | タイマー・ROS 2 Pub | **3秒周期** | ◎ | ✕ |
| Hermes Gateway | LLM推論 | **応答時間不定（API依存）** | ✕ | ◎ |
| Warehouse MCP Server | 検証・実行 | **数十ms** | ✕ | ✕ |

**注意**: Emergency Guardian の50ms、State Cache の100ms は設計目標値であり、ROS 2/rclpy（通常のUbuntu上）ではハードリアルタイム保証ではない。安全停止の最終防衛線はESP32/MCU側に置く。LLM APIはrate limit・timeout・overloadの影響を受けるため、応答時間にdeadline保証がない。

**rclpyとLLMが同一プロセスに同居しない。** イベントループ共存問題が構造的に排除されている。

---

## ローカルモデルに関する決定

Jetson Orin Nano Super（8GB LPDDR5 共有メモリ）ではローカルLLMモデルの実行は非現実的。ROS 2 + Nav2 + Open-RMF + Hermes Gateway が既にメモリを消費しており、追加余裕がない。

**決定**: クラウドAPI のみで運用。ローカルモデルはスコープ外。

将来のPhysical AI展開（完全ローカル環境）を見据え、以下が有効:
- Hermes Agentのプロバイダー非依存設計がクラウド→ローカル移行パスとして機能
- モードCのアーキテクチャはローカルLLM（推論5-10秒）でも安全に動作（交通管理はOpen-RMFがイベント駆動で処理、LLM非依存）
- Jetson AGX Orin（64GB）やJetson Thor世代ではローカル実行が現実的

---

## References

### 設計判断の根拠（一次情報）

- [ROS 2 Real-Time Programming](https://docs.ros.org/en/foxy/Tutorials/Demos/Real-Time-Programming.html) — LLMを安全停止系に入れない根拠。リアルタイムループでは非決定的処理を避けるべき
- [ROS 2 Executors](https://docs.ros.org/en/foxy/Concepts/About-Executors.html) — rclpy executor/callback モデル。MCP stdioとの分離根拠
- [Nav2 Simple Commander API](https://docs.nav2.org/commander_api/index.html) — cancelTask()はgoal cancel要求であり物理停止保証ではない
- [MCP Transports Specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) — stdio transport: JSON-RPC on stdin/stdout。rclpy分離の根拠
- [Linux rename(2)](https://man7.org/linux/man-pages/man2/rename.2.html) — atomic rename保証。State Cache共有ファイル方式の根拠
- [OpenRMF Documentation](https://openrmf.readthedocs.io/) — Traffic Schedule Database、EasyTrafficLight API
- [Fleet Adapter Tutorial](https://osrf.github.io/ros2multirobotbook/integration_fleets_adapter_tutorial.html) — タスク割当bidding flow。dispatch_task(robot=None)の根拠

### プロジェクト関連

- [Hermes Agent — GitHub](https://github.com/NousResearch/hermes-agent) — 参照日: 2026-05-23
- [Hermes Agent — Official Docs](https://hermes-agent.nousresearch.com/docs/) — 参照日: 2026-05-23
- [Hermes Agent — Gateway API Server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server) — 参照日: 2026-05-23
- [Open-RMF — GitHub](https://github.com/open-rmf/rmf) — 参照日: 2026-05-23
- [Open-RMF rmf_api_msgs — GitHub](https://github.com/open-rmf/rmf_api_msgs) — 参照日: 2026-05-23
- [Free Fleet — GitHub](https://github.com/open-rmf/free_fleet) — 参照日: 2026-05-23
- [WiseVision ROS 2 MCP Server — GitHub](https://github.com/wise-vision/mcp_server_ros_2) — 参照日: 2026-05-23（調査対象、不採用）
- [Nav2 MCP Server — GitHub](https://github.com/ajtudela/nav2_mcp_server) — 参照日: 2026-05-23（調査対象、不採用）
- [Langfuse — 公式サイト](https://langfuse.com/) — 参照日: 2026-05-23
