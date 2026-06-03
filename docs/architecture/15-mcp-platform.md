# MCP プラットフォーム設計（Hermes + Warehouse MCP Server + 競合状態対策）

作成日: 2026-05-28
（`12-infrastructure-common.md` から分割）

> **関連ドキュメント**:
> - [12 - 共通インフラ](12-infrastructure-common.md) -- Emergency Guardian / State Cache / 安全レイヤー
> - [08 - LLM Bridge 共通](08-llm-bridge-common.md) -- 司令官LLM サイクル設計
> - [14 - キャラLLM + 交渉プロトコル](14-character-llm-negotiation.md) -- 演出レイヤ
> - [13 - Hermes セットアップ](13-hermes-setup.md) -- 起動手順

本書は LLM ↔ ROS 2 を仲介する MCP プラットフォーム層の設計をまとめる。Hermes Agent / Warehouse MCP Server / Policy Gate / 競合状態の防止を含む。

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
- ツール呼出しはサーバーサイドで実行され、最終テキストのみ返却（＝ Hermes ネイティブ経路。下記📌参照）

> 📌 **採用実装（S1, #4 / PR #70）= Bridge 仲介ディスパッチ**: 直前の「ツール呼出しはサーバーサイドで実行」は Hermes の機能説明であり、**本PJ採用の S1 トランスポートではない**。S1 は **LLM が Command JSON を返し、`warehouse_llm_bridge` が `action_map` で MCP 7ツール呼出に写像して自らディスパッチする**（凍結コード `action_map.py` + `tools.py:11-13` の verbatim 受理 + #41）。これにより Bridge が mint する冪等キー（C, §競合状態の防止§2）が成立する（サーバーサイド実行では Bridge が tool call を仲介できず C を注入できない）。Hermes ネイティブのサーバーサイド実行は将来の代替経路（キャンセル手段の確定＝**Issue #54** 後）。設計根拠は `08-llm-bridge-common.md §同時発火制御` の「採用実装」📌注記。

**セッション管理**: `/v1/chat/completions` は stateless（公式仕様）。会話履歴は `messages` に含める。サーバー側の会話継続が必要な場合は `/v1/responses` の `previous_response_id` を使用する。Phase 0.5 で実際のセッション管理方式を検証する。

### プロバイダー設定

```yaml
# hermes config
providers:
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
    model: claude-opus-4-8    # 最新世代Opus（全Claude Opus統一、16 §7）
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
        - start_negotiation
      prompts: false
      resources: false
```

`tools.include` で7ツールに絞り、トークンコストを最小化する（[Hermes MCP公式](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)）。

### トークンコスト見積もり（暫定推定、Phase 0.5 で実測予定）

| 項目 | トークン/ターン |
|------|---------------|
| System Prompt | 約500 |
| Warehouse MCP ツール定義（7個、gen_id required + start_negotiation 含む） | 約600 |
| situation JSON（gen_id + 2台分の状態 + history + escalation/negotiation_proposal） | 約1000 |
| LLM応答（reasoning + commands JSON） | 約200 |
| **入力合計** | **約2100 tokens/call** |
| **出力合計** | **約200 tokens/call** |

10分デモあたりのコスト推定:

| LLM | Mode A (~200回) | Mode C (~120回) |
|-----|----------------|-----------------|
| Claude Opus（最新世代、$15/$75 per MTok） | ~$9.0 | ~$5.4 |
| GPT-4o ($2.50/$10) | ~$1.40 | ~$0.84 |
| Gemini 2.5 Flash ($0.30/$2.50) | ~$0.22 | ~$0.13 |
| Grok 4.3（価格未確定、推定）| ~$1.50 | ~$0.90 |

4社合計: **Mode A ~$12/デモ、Mode C ~$7/デモ**（暫定値、Phase 0.5 実測後に確定。Claude を Opus 単価で再計算、16 §7）。詳細は `08-llm-bridge-common.md` のコスト見積もりセクション参照。

### Hermes 付加機能

| 機能 | 活用 | 備考 |
|------|------|------|
| Memory | 「前回通路Aでデッドロックした」等の文脈記憶 | セッション横断で有効 |
| Skills | 限定的（モードCではパターンが少ない） | モードAでの比較時に活きる |
| Langfuse | 全LLM呼出しの自動トレース | 4社比較検証に必須 |

---

## Warehouse MCP Server

### ツール定義（7個）

**注**: すべてのツールは `gen_id: int` を required 引数として持つ。LLM は situation JSON の `gen_id` フィールドをそのまま渡す（B-3 安全機構、§2 参照）。さらに全ツールは `idempotency_key: str | None`（tool-call 単位の冪等キー、UUID）を**任意**引数として持つ。これは Bridge が注入し LLM は触らない（C, §競合状態の防止§2 参照）。

```python
# ※ 全ツールの先頭引数として `gen_id: int` を必ず持つ（B-3 安全機構）。以下は gen_id 以外の引数を示す。

# ツール1: dispatch_task
dispatch_task(
    gen_id: int,           # 必須: situation JSON の gen_id をそのまま渡す
    pickup: str | None = None,   # 任意（本プロジェクトのタスクは dropoff 中心。action_map は送らない）
    dropoff: str | None = None,  # 行き先の場所名（"berth_A"等）。action="wait" 時は不要
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
    gen_id: int,
    task_id: str  # タスクID または "current:{robot}" 形式（例: "current:bot1"）
)
# "current:{robot}" 指定時、Warehouse MCP Server が active_tasks[robot] から実際の task_id を解決

# ツール3: get_fleet_status
get_fleet_status(gen_id: int)
# → State Cache + TrafficManager からの統合情報を返却

# ツール4: get_task_queue
get_task_queue(gen_id: int)
# → 未割当・実行中・直近完了タスクの一覧

# ツール5: send_to_charging
send_to_charging(gen_id: int, robot: str)

# ツール6: escalation_response
escalation_response(
    gen_id: int,
    escalation_id: str,
    action: str,          # "reassign" | "cancel" | "retry"
    new_robot: str | None = None,
    reason: str = ""
)

# ツール7: start_negotiation（キャラLLM交渉を発動、14-character-llm-negotiation.md 参照）
start_negotiation(
    gen_id: int,
    deadlock_or_escalation_id: str,
    starter: str,         # "bot1" | "bot2" — 先手を指定
    context: str = ""     # 交渉の前提となる司令官の状況認識
)
# 内部処理: Warehouse MCP Server が /negotiation/start トピックを publish。
# キャラLLMがバトンパス方式で交渉開始、合意したら /negotiation/proposal を司令官の
# 次サイクル situation JSON に取り込み、司令官が検証して採用/拒否を判断する。
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

**トークンコスト影響**: `dispatch_task` に3パラメータ追加で約30トークン増、`gen_id` required 引数を全7ツールに追加で約20トークン増、`start_negotiation` 新規ツールで約50トークン増。合計 約600トークン/ターン（従来約500）。`idempotency_key`（C, §競合状態の防止§2）は schema 上は全7ツールに加わるが、**Bridge が tool call 送出時に注入し LLM 出力には現れない**ため、**LLM 出力トークンの増加はゼロ**（schema 定義文の入力側コストのみ、無視できる）。

**`cancel_task` の `"current:{robot}"` 規約**: LLM の出力 JSON には `task_id` が含まれないため、`cancel_task("current:bot1")` と指定すると Warehouse MCP Server 内部の `active_tasks: dict[str, str]`（robot → task_id マッピング）から実際の task_id を解決する。

> ▶ **REST 転送の実装（S2-PR2 HALF B, #4 / #86）**: 上表の「Nav2 Bridge エンドポイント」列は `warehouse_mcp_server/nav2_client.py` の `plan_nav2_request`（純関数）が実装する。`WarehouseTools.dispatch` は tool **受理後（`status=="ok"`）にのみ** `nav2_forwarder`（注入された `Nav2RestForwarder`, httpx 遅延 import）で `POST /api/v1/{navigate,wait,stop}` を発火する＝stale(B-3)/dup(C)/Policy 拒否は転送されない（R-26 の単一 seam）。`dropoff`→`destination` の凍結フィールドドリフトはここで明示変換（凍結契約を改名しない）。転送は fail-open。forwarder は **Mode A/B（`traffic_mode` none/simple）でのみ注入**、Mode C（open-rmf）は下記 TrafficManager 同様 Open-RMF 経由（forwarder 無し）。設計根拠と end-to-end 検証は `08-llm-bridge-common.md §同時発火制御` の「S2-PR2 HALF B」▶注記。

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

    # ※ 例示シグネチャ。凍結実装 tools.py は gen_id 後を keyword-only（`*`）にし順序は robot, pickup, dropoff... （食い違い時は tools.py 優先）
    async def dispatch_task(self, gen_id, pickup=None, dropoff=None, priority="normal", robot=None,
                            via=None, action="deliver", duration=None, idempotency_key=None):
        # 0. gen_id 検証（B-3、§2 参照）
        if not await self._check_gen(gen_id):
            return {"status": "rejected", "reason": "stale_generation", "received_gen": gen_id}
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
            # 場所名存在チェック（pickup は任意＝省略時はスキップ。本プロジェクトは dropoff 中心）
            if pickup is not None and pickup not in self.known_locations:
                return Reject(f"Unknown location: {pickup}")
            if dropoff not in self.known_locations:
                return Reject(f"Unknown location: {dropoff}")

            # 同一地点チェック（pickup 指定時のみ）
            if pickup is not None and pickup == dropoff:
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
            # しきい値は warehouse_interfaces.safety が単一ソース（包含境界: <=10 critical / <=20 low）
            battery = state.get("battery", 100)
            if battery <= 10:
                return Reject(f"{robot} battery critical ({battery}%)")
            if battery <= 20:
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

## 競合状態の防止（Race Condition Mitigation）

非同期な複数経路（Bridge / Hermes / MCP / Emergency Guardian / Nav2）が同一状態に書き込むため、以下の競合に対処する。**Mode A / Mode C 共通の対策**（共通インフラ層）。

### 1. `cmd_vel` 多重 publisher → `twist_mux`

ROS 2 の `/bot{n}/cmd_vel` トピックには複数の publisher が存在しうる:
- Nav2 Controller（パス追従、50ms 周期）
- Emergency Guardian（緊急停止、50ms 周期）

ROS 2 の pub/sub は「最後に publish した側が勝つ」ため、Emergency Guardian の停止指令が Nav2 Controller の前進指令で上書きされうる。

**対策**: ROS 2 公式の `twist_mux` パッケージで優先度制御。

```yaml
# config/twist_mux.yaml
topics:
  emergency:
    topic: /bot{n}/cmd_vel/emergency
    timeout: 0.5
    priority: 100   # 最優先（Emergency Guardian）
  nav2:
    topic: /bot{n}/cmd_vel/nav2
    timeout: 0.5
    priority: 10    # Nav2 Controller
```

- Emergency Guardian は `/bot{n}/cmd_vel/emergency` に publish
- Nav2 Controller は `/bot{n}/cmd_vel/nav2` に publish（remap）
- `twist_mux` ノードが優先度に従って `/bot{n}/cmd_vel` に転送
- Emergency が 0.5s 以内に来ていれば Nav2 を完全にブロック

### 2. MCP Server gen_id 検証 + 冪等キー検証（B-3 + C）

Bridge が cycle 開始時に `current_gen` を共有ストレージに公開する。さらに **全 MCP tool 定義に `gen_id: int` を required 引数として追加**し、LLM に system prompt で「situation JSON の gen_id を全 tool 呼び出しに含めよ」と指示する。MCP は受信した tool 引数の `gen_id` と共有ストレージの `current_gen` を比較し、古いなら reject する（**B-3**）。

ただし B-3 は `gen_id < current_gen` の比較のみで、**同一世代内の重複・再送（replay）は弾けない**（R-35 part B）。司令官は1呼び出しで bot1・bot2 両方に指示する＝**同一 gen_id の tool call が複数正当に発火する**ため、冪等キーは世代単位ではなく **tool-call 単位**でなければならない。そこで各 tool call に **1回限りの `idempotency_key`（UUID、Bridge が mint・LLM は echo しない）** を付与し、MCP は消費済みキーを `IdempotencyStore.check_and_add` で記録して replay を冪等に reject する（**C**）。設計背景は `08-llm-bridge-common.md` の「同時発火制御 → C. 冪等キー」を参照。

本書では MCP Server 側の実装を示す:

```python
class WarehouseMCPServer:
    def __init__(self, gen_store, idempotency_store):
        self.gen_store = gen_store                 # Bridge と共有（Redis/file/ROS param）
        self.idempotency_store = idempotency_store  # 消費済みキー記録（FileIdempotencyStore 等）

    async def _check_gen(self, gen_id: int) -> bool:
        """tool 呼び出しの先頭で必ず呼ぶ。古い世代なら False を返す（B-3）。"""
        cur_gen = await self.gen_store.get()
        if gen_id < cur_gen:
            log.warn(f"stale tool call gen={gen_id} < current={cur_gen}, rejected")
            return False
        return True

    def _check_idempotency(self, key: str | None, gen_id: int) -> bool:
        """key を消費して新規なら True、既知（replay）なら False を返す（C）。

        key=None（旧 producer・冪等キー未注入）は後方互換で許容（True）。
        check_and_add は単一プリミティブ（seen()/add() に分割せず呼び出し側の TOCTOU を防ぐ）。
        ファイル実装の load→check→write はロックしておらず、**単一プロセス/イベントループ内でのみ atomic**
        （複数プロセス共有時は Redis 等のロック付きバックエンドを使う。下記「共有ストレージ」参照）。
        同一 gen の別キーは全て True（bot1+bot2 のカーブアウト）。
        """
        if key is None:
            return True
        return self.idempotency_store.check_and_add(key, gen_id)

    async def dispatch_task(self, gen_id: int, robot: str, pickup: str, dropoff: str,
                            idempotency_key: str | None = None, **kwargs):
        # 0. gen_id 検証（B-3）
        if not await self._check_gen(gen_id):
            return {"status": "rejected", "reason": "stale_generation", "received_gen": gen_id}
        # 0.5 冪等キー検証（C）— 同一世代の重複・再送を弾く
        if not self._check_idempotency(idempotency_key, gen_id):
            return {"status": "rejected", "reason": "duplicate_command",
                    "idempotency_key": idempotency_key}
        # ... 既存処理（Policy Gate → 割当 → 実行）...
```

検証順序は **gen → 冪等 → Policy Gate**。冪等チェックは Policy Gate の前に置く（重複コマンドが二重に副作用を持たないよう、副作用を伴う処理の手前で落とす）。`idempotency_key` は **required ではない**（`gen_id` と異なり optional。旧 producer や冪等キー未注入の経路を壊さない後方互換）。

MCP tool 定義例（全7ツール共通で `gen_id` を required に。`idempotency_key` は**全7ツールに追加するが optional**＝`required` に入れない。`gen_id` と違い、旧 producer や冪等キー未注入の経路を壊さない後方互換のため）:

```json
{
  "name": "dispatch_task",
  "description": "ロボットにタスクを割り当てる。gen_id は situation JSON の gen_id をそのまま渡すこと",
  "input_schema": {
    "type": "object",
    "required": ["gen_id"],
    "properties": {
      "gen_id": {"type": "integer", "description": "Situation JSON で受け取った gen_id をそのまま渡す（安全機構）"},
      "idempotency_key": {"type": ["string", "null"], "description": "Bridge が注入する tool-call 単位の冪等キー（UUID）。LLM は触らない（任意・後方互換）"},
      "robot": {"type": ["string", "null"]},
      "pickup": {"type": ["string", "null"]},
      "dropoff": {"type": ["string", "null"]}
    }
  }
}
```

> **`idempotency_key` は LLM に生成させない**: schema 上は引数だが、Bridge が tool call 送出時に注入する（`08-llm-bridge-common.md` の信頼の非対称性参照）。LLM 出力には現れないため**追加 LLM 出力トークンはゼロ**。`gen_id`（LLM が echo＝出力トークン増）とは対照的。

共有ストレージは Mac/Docker 開発時はファイル（`gen_store` は `/tmp/warehouse/gen_store`、冪等キー記録は `/tmp/warehouse/idempotency_store`）、Jetson 本番時は `multiprocessing` 共有 / Redis のいずれか（**Phase 1 で選定**）。冪等キー記録はファイル実装で `FileIdempotencyStore`（`{key: gen}` の JSON map、atomic write、gen-window=8 で eviction）。

#### なぜ HTTP ヘッダ方式（X-Bridge-Gen）にしなかったか

当初は HTTP ヘッダで gen_id を渡す案も検討したが、**Hermes Gateway は OpenAI 互換 API を使用しており、クライアントの HTTP ヘッダが内部の MCP tool 呼び出しに転送される保証がない**。tool schema の required 引数として強制する方が、JSON Schema 検証で LLM の出力漏れも検出でき堅牢。

### 3. `active_tasks` 辞書の競合 → `asyncio.Lock`

Warehouse MCP Server 内の `active_tasks: dict[str, str]`（robot → task_id）は、`dispatch_task` で書き込まれ `cancel_task("current:{robot}")` で読まれる。これらが並行実行されると wrong task_id を解決する可能性がある（B案 gen_id でほぼ防げるが、同一 cycle 内の連続呼び出しは防げない）。

```python
class WarehouseMCPServer:
    def __init__(self, ...):
        self.active_tasks: dict[str, str] = {}
        self._active_tasks_lock = asyncio.Lock()

    async def dispatch_task(self, ...):
        # ... gen_id check, policy gate ...
        async with self._active_tasks_lock:
            self.active_tasks[robot] = new_task_id

    async def cancel_task(self, task_id, ...):
        if task_id.startswith("current:"):
            robot = task_id.split(":", 1)[1]
            async with self._active_tasks_lock:
                task_id = self.active_tasks.get(robot)
        # ... 実行 ...
```

### 4. Policy Gate の validate → register atomic 化

`validate_dispatch` で「同一目的地2台禁止」をチェックし、その後 `register_task` を呼ぶフローでは、検査→登録の間に別の dispatch が割り込みうる:

```
時系列:
  t=0   dispatch(bot1, shelf_2): validate → 通過
  t=0.001 dispatch(bot2, shelf_2): validate → 通過（bot1 未登録）
  t=0.002 dispatch(bot1, shelf_2): register
  t=0.003 dispatch(bot2, shelf_2): register  ← 重複！
```

**対策**: Policy Gate 全体を 1 つの `asyncio.Lock` で覆い、validate と register を atomic に行う。

```python
class PolicyGate:
    def __init__(self, ...):
        self._gate_lock = asyncio.Lock()

    async def validate_and_register_dispatch(self, robot, pickup, dropoff, ...):
        async with self._gate_lock:
            result = self._validate_dispatch_inner(robot, pickup, dropoff, ...)
            if result.accepted:
                self._register_task_inner(robot, pickup, dropoff)
            return result
```

呼び出し側（`dispatch_task`）は `validate` と `register` を別々に呼ばず、上記の合成メソッドを使う。

### 各対策の Mode 適用範囲まとめ

| # | 対策 | Mode A | Mode B | Mode C | 配置場所 |
|---|---|---|---|---|---|
| 1 | twist_mux | ✅ | ✅ | ✅ | Jetson の launch ファイル（共通） |
| 2 | MCP gen_id 検証（B-3） | ✅ | ✅ | ✅ | Warehouse MCP Server（共通） |
| 2b | MCP 冪等キー検証（C, `idempotency_key`） | ✅ | ✅ | ✅ | Warehouse MCP Server（共通） |
| 3 | active_tasks Lock | ✅ | ✅ | ✅ | Warehouse MCP Server（共通） |
| 4 | Policy Gate atomic | ✅ | ✅ | ✅ | Policy Gate（共通） |

**Mode による差**: 重要度は Mode A > Mode B > Mode C（コマンド頻度が高いほど競合確率が高い）。ただし Mode C でもエスカレーション時の遅延応答が古いタスクを発行しうるため、全モードで実装する。

---
