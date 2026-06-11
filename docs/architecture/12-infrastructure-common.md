# 共通インフラストラクチャ設計

作成日: 2026-05-25
由来: 旧 `12-hermes-agent-integration.md` を分割・再編（同ファイルは現存しない）

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

Layer 3: Claude / Hermes（戦略判断、Mode A: 3秒 / Mode C: 5秒サイクル）
  └── 事後の説明・タスク再割当・復旧方針の提案
  └── LLM APIはrate limit/timeout/overloadの影響を受けるため制御deadline保証なし
```

### 全体像 — 時間階層 × 安全レイヤー × 言語（早わかり）

> 上の **§3層の定義（時間スケール）** と **§安全レイヤー（4層）** を1枚に重ねた早わかり版（pedagogical）。正本はその2表で、本表は対応関係を俯瞰するもの。**上ほど「賢いが遅い・保証なし」、下ほど「単純だが速い・確実」**。

| 役割層 | 時間階層 | 周期（目標） | 安全レイヤー | 主な構成要素 | 言語・実装 | 一言 | 体の比喩 |
|---|---|---|---|---|---|---|---|
| 戦略 | Non-RT | 3〜5秒 | Layer 3 | LLM Bridge Node（＋CTRV 3秒予測）/ Hermes Gateway / Warehouse MCP / Claude·GPT·Gemini·Grok | **Python（自作）** ＋ LLM API | 考える・タスク割当 | 🧠 司令官（頭脳） |
| 調整 | Soft-RT | 100ms〜event | Layer 2＝交通管理のみ | State Cache・VirtualScan（A/B）〔安全層外〕 / SimpleTrafficManager（B）・Open-RMF（C）〔Layer 2〕 | **Python（自作）** ＋ C++（Open-RMF） | まとめる・交通整理 | 🗺️ 参謀（状況把握） |
| 自律走行 | Hard-RT | 50ms | （安全層外） | Nav2 Controller/Planner / AMCL / SLAM Toolbox | **C++（既存依存）** ＋ launch=Python | 走る・賢く避ける | 🚗 運転手 |
| 緊急監視 | Hard-RT | 50ms（目標） | Layer 1 | Emergency Guardian | **Python（自作）** | 見張る・緊急停止要求 | 🚨 監視員（補助ブレーキ） |
| 物理安全 | 即時 | µs〜ms | Layer 0 | ESP32 firmware / micro-ROS（on FreeRTOS） | **C++（自作）** | 動かす・確実に止める | 💪 手足＋ブレーキ |

注:
- **時間は3層（Hard/Soft/Non-RT）、安全は4層（Layer 0–3）で軸が異なる**。本表は両者を役割で並べた俯瞰図。
- **Nav2/AMCL/SLAM は時間=Hard-RT の自律走行スタック**だが「安全レイヤー」には属さない（＝安全層が守る対象。賢い回避は Nav2、確実な停止は Layer 0/1）。
- **Layer 2 は「交通管理」のみ**（Open-RMF〔Mode C〕／ SimpleTrafficManager〔Mode B〕＝§安全レイヤー:86）。同じ Soft-RT でも **State Cache（状態集約）・VirtualScan（センサ補助）・Orchestrator KPI（計測）は安全層外**（交通制御ではないため）。下のデータフロー図の帯ラベル「Layer2」は粗い俯瞰で、正準の層所属は本注と §安全レイヤー表。
- **言語の境界**: 確定時間が要る最下層（Layer 0）＝**C++ 自作**、その上の自律走行＝**C++ 既存依存**（YAML/launch で設定）、戦略・調整・監視のロジック＝**Python 自作**。**Python↔C++ は ROS 2 トピック（DDS）越しに疎結合**で会話し、直接呼び出さない。
- **Layer 0 が最終防衛線**: 上位が全停止しても MCU 内で速度上限 0.3 m/s ＋ 近接停止を保証（§安全レイヤー / `firmware/`）。

#### 各層の技術スタック（実体）

上表の各「役割層」に対応する実装・パッケージ。**自作=本リポジトリ（`ws/src/warehouse_*` ＝ Python / `firmware/` ＝ C++）、依存=既存の ROS 2 パッケージを設定して使う**もの。

| 役割層（時間 / 安全層） | 技術スタック（実体・パッケージ） |
|---|---|
| **戦略**（Non-RT / Layer 3） | LLM Bridge Node（Python・`warehouse_llm_bridge`）／ Hermes Gateway（Python・GCP VM・NousResearch Hermes）／ Warehouse MCP Server（Python・`warehouse_mcp_server`）／ nav2_bridge REST（Python・`warehouse_nav2_bridge`）／ LLM API（Claude/GPT/Gemini/Grok）＋ Langfuse ／ **CTRV 予測** `predicted_position_3s`（`situation.py:170-193`・旋回=円弧/直進=CV・**Mode A/B のみ**） |
| **調整**（Soft-RT・うち交通管理のみ Layer 2） | State Cache（Python・`warehouse_state`・**安全層外**）／ VirtualScan（Python・Mode A/B・**安全層外**）／ SimpleTrafficManager（Python・`warehouse_traffic`・Mode B・**Layer 2**）／ Open-RMF（**C++ 依存**・Mode C・**Layer 2**）／ Orchestrator KPI（Python・`warehouse_orchestrator`・**安全層外**） |
| **自律走行**（Hard-RT） | Nav2・AMCL・SLAM Toolbox・collision_monitor・twist_mux（**全て C++ 既存依存**）＋ 設定 `warehouse_bringup/config/nav2_params.yaml`・launch（Python） |
| **緊急監視**（Hard-RT / Layer 1） | Emergency Guardian（**Python 自作**・`warehouse_safety`） |
| **物理安全**（即時 / Layer 0） | ESP32 firmware（**C++ 自作**・FreeRTOS・PlatformIO・`firmware/`）／ micro-ROS（C・XRCE-DDS）／ micro-ROS Agent（C++・Jetson 上）／ on-robot センサ MS200（`/scan`）・エンコーダ・バッテリ（※**RPLiDAR A1 は Jetson-USB 固定の外部トラッキング用・optional**＝on-robot ではない。doc02:179-180 / doc03:166） |
| **横断**（全層共通） | ROS 2 Jazzy（DDS）／ 凍結契約 `warehouse_interfaces`（Python・pydantic）／ `warehouse_description`（URDF）／ Sim：Gazebo Harmonic＋ros_gz_bridge・Isaac Sim／ 実行機：Jetson Orin Nano（Ubuntu 24.04）／ 環境切替：`WAREHOUSE_ENV`＋config（doc19） |

> 各パッケージ責務の正本は各 `ws/src/warehouse_*/CLAUDE.md`、リポジトリ構成は doc16、環境/config は doc19。

#### 全体データフロー図（end-to-end）

**指令は上から下へ（⬇）、状態・センサは下から上へ（⬆）流れる**。下に行くほど速く・確実、上に行くほど賢く・遅い。

```
                       👤 人の指示（自然言語・曖昧でOK）  例:「在庫A運んで」
                                       │
   指令 ⬇                              ▼                            状態 ⬆
┌─ 🧠 戦略 ── Non-RT 3〜5秒 ── Layer3 ── Python(自作)+外部API ──────────────┐
│  [LLM Bridge Node] ──situation(JSON)──▶ [Hermes Gateway] ──▶ Claude/GPT/  │
│   warehouse_llm_bridge ◀──command(JSON)── GCP・NousResearch  Gemini/Grok   │
│        └ tool 呼出                                          +Langfuse(記録) │
│        └ ★ situation に predicted_position_3s = CTRV 3秒外挿を同梱         │
│             （situation.py:170-193・旋回=円弧/直進=CV・Mode A/B のみ）      │
│  [Warehouse MCP Server] ─[Policy Gate]→ [nav2_bridge(REST)]               │
│   warehouse_mcp_server   速度/座標/電池/重複を検査→受理(ok)のみ  warehouse_nav2_bridge│
└──────────────────────────│ goal/cancel ───────────────── ▲ /state(JSON) ──┘
┌─ 🗺 調整 ── Soft-RT 100ms〜 ── Layer2 ── Python(自作)+C++(Open-RMF) ───────┐
│  [State Cache] = 全状態を集約(situation の素) / [VirtualScan](A/B) / KPI   │
│   warehouse_state                                                         │
│  交通管理:  ModeB [SimpleTrafficManager](Py)   ModeC [Open-RMF](C++)       │
└──────────────────────────│ goal ─────────────────── ▲ /odom /scan /battery ┘
┌─ 🚗 自律走行 ── Hard-RT 50ms ── C++(既存依存・設定=YAML/launch) ───────────┐
│  [Nav2] Controller(DWB/MPPI)・Planner・BT・costmap / [AMCL] / [SLAM]       │
│   → 経路追従＋賢い障害物回避 → /cmd_vel(nav)                                │
└──────────────────────────│ /cmd_vel(nav) ────────────────────────────────┘
┌─ 🚨 緊急監視 ── Hard-RT 50ms目標 ── Layer1 ── Python(自作) ────────────────┐
│  [Emergency Guardian] warehouse_safety : 2台間距離/電池/blocked を監視      │
│   危険→ Nav2 cancel ＋ /cmd_vel/emergency=0 を最優先注入                    │
│  [twist_mux] 多重化: emergency(prio100) ＞ nav ⇒ /cmd_vel(最終)            │
└──────────────────────────│ /cmd_vel(最終) ───────────────────────────────┘
                           │   … Wi-Fi / UDP …
        ┄┄▶ [micro-ROS Agent](Jetson・C++) ⇄ XRCE-DDS ⇄ ◀┄┄ ROS2網との橋
                           │
┌─ 💪 物理安全 ── 即時(µs〜ms) ── Layer0 ── C++(自作) on FreeRTOS/PlatformIO ─┐
│  [ESP32 firmware] ×2 (firmware/)  ＝ 最終防衛線                            │
│   ① clampLinear: ≤0.3 m/s を MCU 内で強制（ROS 値に関係なく）              │
│   ② 近接/バンパ → モータ即停止（通信・OS 非依存）                          │
│   ③ モータ PWM 駆動 ／ センサ pub: /odom /scan /battery ───┐               │
└────────────────────────────────────────────────────────── │ ─────────────┘
   実機(on-robot): Yahboom ESP32 Car×2 / MS200(/scan) / エンコーダ / バッテリ ｜ 外部固定: RPLiDAR A1(Jetson-USB・任意)
         └────── センサ値は ⬆ で State Cache へ還流し situation を再構成 ──────┘

── 横断（全層共通の土台）──────────────────────────────────────────────────
   通信  : ROS 2 Jazzy (DDS)         契約: warehouse_interfaces (pydantic・凍結)
   記述  : warehouse_description(URDF) Sim : Gazebo Harmonic+ros_gz_bridge / Isaac Sim
   実行機: Jetson Orin Nano (Ubuntu 24.04)
   環境  : WAREHOUSE_ENV + config/<env>  (dev=Mac/Docker/Gazebo ・ prod=Jetson実機)
```

> 読み方: **下り（⬇）＝指令**（人→LLM→Nav2→ESP32→モータ）、**上り（⬆）＝状態/センサ**（ESP32→State Cache→situation→LLM）。各層は上位が落ちても自分の周期で動き続ける（§依存関係の原則・§縮退運転）。物理停止の最終保証は **Layer 0（ESP32）**。

### Emergency Guardian 詳細

> **【検討中 #126 / Phase 2】** 本節の amcl_pose ベース近接監視は R-39（doc07）の通り実効100-200ms stale、かつ Python の 50ms 反射は R-40 の通り GC/GIL で最悪応答時間を保証できない。**近接の物理反射を `nav2_collision_monitor`（`/scan`+`virtual_scan` の polygon stop + `source_timeout`）へ、blocked を Nav2 `progress_checker` へ委譲し、本ノードを battery/event/LLM review の policy 層へ縮退する**責務再配置を評価中（PoC ゲート #67 は close 済）。**cmd_vel 挿入トポロジは §末尾「collision_monitor 委譲: cmd_vel 挿入トポロジ」で確定済 #126**（採用・配線 impl は nav-traffic 調整で defer）。下記コードは現行（自作）設計で、採用形が決まり次第 docs PR で更新する。**「置換ではなく補完」**＝collision_monitor は battery/event/LLM review を持たない。
>
> **【済 #126: edge-trigger】** `/emergency/event` は `(robot, type)` の**立ち上がり時のみ発行**（`gl.EdgeLatch`。持続条件で 20Hz 連発しない／解消→再発で再発火）。下記擬似コードの `emergency_pub.publish(...)` は実装では rising edge にゲートされる。**物理停止（zero `Twist` を `/cmd_vel/emergency` へ）と Nav2 cancel は毎 tick 維持（level）**＝twist_mux prio100 入力が 0.5s で失効するため。event のコア形（`event_id/robot/type/severity/action_taken/timestamp/requires_llm_review` [+任意 `detail`], :141-150）は不変。collision_monitor/progress_checker への近接・blocked 委譲は上記の通り別途（cmd_vel 挿入トポロジは §末尾「collision_monitor 委譲: cmd_vel 挿入トポロジ」で確定済 #126、配線 impl は nav-traffic 調整で defer）。

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

        # 2. バッテリーチェック（境界は閾値を含む = 凍結契約 safety.battery_is_critical
        #    `pct <= BATTERY_CRITICAL_PCT` および下表「≤ 10%」と一致。10% ちょうどで停止）
        for bot in ["bot1", "bot2"]:
            if self.battery[bot] <= self.BATTERY_CRITICAL:
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
        # twist_mux 経由で優先度100の /cmd_vel/emergency に publish（詳細は「競合状態の防止」セクション）
        stop_msg = Twist()  # all zeros
        self.cmd_vel_emergency_pubs[bot].publish(stop_msg)
        # 3. キャラLLM交渉中なら即中断（14-character-llm-negotiation.md 参照）
        self.negotiation_abort_pub.publish(
            String(data=json.dumps({"reason": "emergency", "bot": bot, "event_id": event_id}))
        )
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
| ≤ 10% | **Nav2 cancel要求 + cmd_vel停止**。最寄り安全地点へ退避 | 全コマンド拒否 | 事後通知のみ |
| 10%超〜20% | 監視継続 | **新規タスク割当禁止**。実行中タスクは状況により継続可（dropoffまで残り僅かなら完了させる） | 充電指示を推奨 |
| 20-30% | 監視継続 | 新規タスク割当可 | 次タスク割当禁止、充電候補 |

> **`percentage` スケールの単一正規化（#44）**: `sensor_msgs/BatteryState.percentage` のスケールはドライバ依存（REP-147 は 0..1 の fraction、0..100 で出すドライバもある）。上表の閾値（`≤10%` 等、凍結契約 `warehouse_interfaces.safety.BATTERY_CRITICAL_PCT=10`）は **0..100 の百分率**前提のため、スケールは **config `safety.battery_percentage_scale`（`percent` ＝0..100 / `fraction` ＝0..1）で明示宣言**し、**単一ヘルパ `warehouse_interfaces.safety.normalize_battery_percent(raw, scale)` で正規化**する。State Cache（snapshot 書込）と Emergency Guardian（50ms estop）は**同一ヘルパ・同一スケール**を使い消費者間でズレない（スケールを値から推測するヒューリスティックは禁止＝0..100 ドライバの 0.5% を 50% と誤読し critical estop を *見逃す*ため）。**既定 `percent` は fail-safe**（万一 fraction ドライバを percent と読むと満充電が低残量に見え *誤* estop ＝安全側に倒れ、critical estop の *見逃し* は起きない）。**不正なスケール値**（typo 等）は `load_config` 検証＋各ノード起動時の `validate_battery_scale` で **fail-fast（起動拒否）**＝安全機構を黙って無効化しない（未知スケールを per-reading で握り潰すと battery=unknown→no-estop の fail-OPEN になるため、起動時に loud に落とす）。実機 Yahboom ドライバの実スケールは **Phase 1 で計測し config を確定**する（`.claude/rules/safety.md` / doc16 §11 の実機 estop テストで検証）。**sim（#156）**: gz に battery sensor は無いため `warehouse_sim` の合成 publisher（`sim_battery_publisher`）が `/bot{n}/battery` を生成するが、これも **同一 config キー `safety.battery_percentage_scale` を読んで同一スケールで出す**（=producer も同じ単一ソースに従う）ので、split-brain は producer 側でも生じない（doc03 §トピック設計）。実スケールが定義により既知の sim では計測不要、計測が要るのは実機ドライバのみ。

---

## State Cache Node

### 設計

State Cache producer（`warehouse_state` パッケージ）は各 bot の `/{bot}/amcl_pose`・`/{bot}/battery`・`/{bot}/odom`・`/{bot}/scan` と `/emergency/event` を購読し、`StateAggregator` に最新生値を蓄積する。100ms タイマーで **凍結契約 `warehouse_interfaces.schemas.StateSnapshot` 形状**のスナップショットを構築し、`FileStateStore`（atomic `tmp` + `os.replace`）で `/tmp/warehouse/state.json` に書き出すと同時に、同一ペイロードを `/state_cache/snapshot` トピックへ publish する。

```python
class StateCacheNode(Node):
    """生トピックを購読し、StateSnapshot 形状の集約を 100ms 周期で書き出す。"""

    def __init__(self):
        super().__init__("state_cache")
        self._agg = StateAggregator(bots=("bot1", "bot2"))
        self._store = FileStateStore()  # 既定 state_path() = /tmp/warehouse/state.json
        self._snapshot_pub = self.create_publisher(String, "/state_cache/snapshot", qos)

        for bot in ("bot1", "bot2"):
            self.create_subscription(PoseWithCovarianceStamped, f"/{bot}/amcl_pose",
                                     lambda m, b=bot: self._agg.set_pose(b, _pose(m)), qos)
            self.create_subscription(BatteryState, f"/{bot}/battery",
                                     lambda m, b=bot: self._agg.set_battery(b, _batt(m)), qos)
            self.create_subscription(Odometry, f"/{bot}/odom",
                                     lambda m, b=bot: self._agg.set_velocity(b, _vel(m)), qos)
            self.create_subscription(LaserScan, f"/{bot}/scan",
                                     lambda m, b=bot: self._agg.set_scan(b, _scan(m)), qos)
        self.create_subscription(String, "/emergency/event", self._on_emergency, qos)
        self.create_timer(0.1, self._write_cache)  # 100ms

    def _write_cache(self):
        # StateSnapshot 検証済み dict（+ emergency 追加キー）を atomic 書き出し + publish
        payload = self._agg.build_snapshot(datetime.now(UTC).isoformat())
        self._store.write(payload)                        # tmp + os.replace（FileStateStore）
        self._snapshot_pub.publish(String(data=json.dumps(payload)))
```

> **非有限値・未完了 bot の扱い**: `StateAggregator` は非有限（NaN/Inf）の pose/velocity/battery を捨てて最後の正常値を保持し、pose + velocity + battery が揃わない bot は**省略**する（`battery=0` の偽値を出さない）。これにより `json.dumps` に NaN/Infinity が混入せず、下流の安全演算を汚さない。`build_snapshot` は dump 前に `StateSnapshot.model_validate` で凍結契約に照合する。

> **配信2系統**: State Cache は ①`/tmp/warehouse/state.json`（atomic file、LLM Bridge / Warehouse MCP Server が読む）と ②`/state_cache/snapshot` トピック（`std_msgs/String`、キャラLLM が購読、`14-character-llm-negotiation.md` 参照）の両方に同一スナップショットを出力する。

### State Cache JSON フォーマット

```json
{
  "timestamp": "2026-05-30T12:34:56.789012+00:00",
  "robots": {
    "bot1": {
      "position": {"x": 0.3, "y": 0.5},
      "velocity": {"linear": 0.1, "angular": 0.0},
      "heading": 1.57,
      "status": "moving",
      "battery": 85,
      "obstacle_distance": 0.42
    },
    "bot2": {
      "position": {"x": 1.2, "y": 0.7},
      "velocity": {"linear": 0.0, "angular": 0.0},
      "heading": 4.71,
      "status": "idle",
      "battery": 72,
      "obstacle_distance": null
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

> **正本は凍結契約 `warehouse_interfaces.schemas.StateSnapshot` / `RobotSnapshot`**（#30）。形状変更は rules §4（`contract` ラベル＋依存トラック予告）に従う。フィールド対応:
>
> - **top-level `timestamp`**（ISO 8601 / UTC、`datetime.now(UTC).isoformat()`）＝ スナップショット全体の鮮度の単一ソース。**旧 per-robot `updated_at`（epoch float）は廃止**。全 robot は同一 100ms スナップショットで書かれるため、鮮度はスナップショット単位で判定する（次節「stale 判定」）。
> - **`robots[bot]` = `RobotSnapshot`**: `position{x,y}`（地図座標 m）＋ `heading`（yaw rad、旧 `pose.yaw` 相当を独立フィールド化）／ `velocity{linear,angular}`（m/s・rad/s）／ `status`（`"moving"` | `"idle"` のみ、linear 速度から `derive_status` で導出＝**`"blocked"` は産出しない**。Nav2 `nav_status` 統合は Phase-2 TODO）／ `battery`（int 0–100、契約が範囲検証）／ `obstacle_distance`（最近傍障害物 [m]、`/{bot}/scan` 由来・不明時 `null`）。**`status` が `"blocked"` を出さない**ため、Mode A/B のデッドロック検出（`mode-a/08a` §デッドロック検出アルゴリズム）は `status=="blocked"` ではなく `status=="idle"`（velocity≈0）＋ `current_task != null`（Bridge が `Situation` 構築時に付与）＋ 近接 0.4m ＋ 対向 heading 2.5rad で判定する（#55, 方針b）。これは LLM 向け（3秒サイクル）推論で、上記「Emergency Guardian 詳細」の `blocked_duration`（pose 変位ベースの独立タイマー＝`warehouse_safety` の `BlockTracker`、`RobotState.status` でも Nav2 でもない）とは**別系統の独立フォールバック**である。将来 `status` に `"blocked"` を導出する場合（＝方針a・契約拡張）も `RobotState.status` は無制約 `str` のため後方互換（条件①に `OR status=="blocked"` を足すだけ）。
> - **`current_task` / `nav_status` は state.json に含めない**（契約 `RobotSnapshot` に無い）。タスク／ナビ状態は LLM Bridge の関心で、`Situation` 構築時に付与する（`predicted_position_3s`・`obstacle_ahead` も Bridge が計算、doc mode-a/08a）。
> - **`emergency` は `StateSnapshot` 外の追加 top-level キー**。`StateSnapshot` は `extra="ignore"` のため再読込時に無視され、契約安全に共存する。State Cache が in-memory 集約して付与する（契約には入れない）。`active` / `history` は**上限付きリング**で、持続 estop（Guardian が条件継続中 ~20 events/s 再送）でも `state.json` が無制限に増えない。明示的な clear／解決プロトコル + Guardian 側エッジトリガは Phase-2 TODO。`emergency` を契約 schema に昇格するかは将来の contract 判断（rules §4）。

### stale 判定

Warehouse MCP Server（Policy Gate）側で、**top-level `timestamp` の経過時間**に応じて robot 鮮度を判定する。鮮度はスナップショット単位（全 robot 共通）で、`availability` は **契約フィールドではなく MCP が局所導出**する（#5 が明示的な `availability` を出すまでの暫定。実装: `warehouse_mcp_server.policy_gate`）:

```python
# warehouse_mcp_server/policy_gate.py（要点）
STALE_AFTER_S = 0.5
UNAVAILABLE_AFTER_S = 2.0

def check_robot_state(robot_snapshot, now, snapshot_ts):
    if robot_snapshot is None:
        return "unknown_robot"          # robots に存在しない
    age = now - snapshot_ts             # snapshot_ts = top-level timestamp（ISO → epoch）
    if age > UNAVAILABLE_AFTER_S:
        return "robot_unavailable"
    if age > STALE_AFTER_S:
        return "robot_stale"
    return None
```

| 経過時間（`now - timestamp`） | availability | Policy Gate の扱い |
|---|---|---|
| < 500ms | `ok` | 通常通りコマンド受付 |
| 500ms – 2s | `stale` | dispatch_task 拒否、cancel/charging は許可 |
| > 2s | `unavailable` | 全コマンド拒否、Claude に通信断を通知 |

> **`timestamp` の堅牢性**: top-level `timestamp` が**破損**（非 ISO 等）なら fail-closed（`state_timestamp_corrupt` で拒否）。**欠落**は #5 producer 投入前の暫定として accept（鮮度チェックを skip）。詳細は `15-mcp-platform.md` の Policy Gate / availability を参照。

---

## Hermes Agent / Warehouse MCP Server / 競合状態の防止

> **このセクション群は `15-mcp-platform.md` に分離しました**。本書では Emergency Guardian / State Cache / Emergency後同期 等の「共通基盤」を扱い、MCP層（Hermes / Warehouse MCP / Policy Gate / 競合状態対策）は `15-mcp-platform.md` を参照してください。

| トピック | 参照先 |
|---|---|
| Hermes Agent 動作モード・API・プロバイダー設定・トークンコスト | [15-mcp-platform.md#hermes-agent-の構成](15-mcp-platform.md) |
| Warehouse MCP Server ツール定義・モード切替・Mode A/B 用パラメータ | [15-mcp-platform.md#warehouse-mcp-server](15-mcp-platform.md) |
| Policy Gate 検証ロジック・rate limiting | [15-mcp-platform.md#policy-gate](15-mcp-platform.md) |
| Command Audit Log | [15-mcp-platform.md#command-audit-log](15-mcp-platform.md) |
| 競合状態の防止（twist_mux / MCP gen_id / active_tasks Lock / Policy Gate atomic） | [15-mcp-platform.md#競合状態の防止](15-mcp-platform.md) |

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
  /tmp/warehouse/state.json に反映
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
| LLM Bridge Node | タイマー・ROS 2 Pub | **Mode A: 3秒 / Mode C: 5秒サイクル** | ◎ | ✕ |
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

## Emergency Guardian — pose freshness guard / progress_checker 委譲（#126）

> 配置上は §「Emergency Guardian 詳細」（:95-151）の付録。**本文中盤への挿入は cross-track の `doc12:<行>` 参照（`:249` ×8 llm-bridge / `:372` nav2-bridge 等）を行ズレで腐敗させ、かつ #5=safety-state の編集境界を越えて他トラックの参照を直す羽目になるため、行が安定する末尾（参照ゼロの References 直前）に置く**。本節は #126 の **freshness guard**・**progress_checker 委譲の責務明文化**（実装 = `warehouse_safety` の `guard_logic.py` / `emergency_guardian.py`）と、**collision_monitor 委譲の cmd_vel 挿入トポロジ**（docs 先行・配線 impl は nav-traffic 調整で defer）を確定する。**いずれも凍結契約 `warehouse_interfaces` を変更しない**。

### freshness guard（pose 到着鮮度 ＝ localization ロスト検出）

- **問題**: §:66 の通り Emergency Guardian は `/{bot}/amcl_pose` を直接購読するが、**pose 到着の鮮度判定を持たない**。AMCL 失調・ノード断で pose が途絶しても気付かず、最後に観測した stale な位置で近接判定を続けてしまう（=「走行中に自己位置を見失っても止まらない」穴）。
- **判定（純ロジック・R-26 unit）**: per-bot で最後に pose を受信した時刻（`time.monotonic()`）を保持し、`age = now - last_pose_t` が **`safety.pose_freshness_timeout` を超えたら stale**（localization ロスト疑い）と判定する（`guard_logic.evaluate(..., pose_freshness_timeout=...)`、rclpy 非依存）。**初回 pose 受信前（`age=None`）は stale としない**（まだ localize していない停止中ロボットを誤って estop しない）。正常 AMCL cadence では発火しない。
- **しきい値**: `safety.pose_freshness_timeout`（config、**既定 1.0s・暫定**）。根拠 = R-39（doc07:249）の AMCL 5-10Hz ＝ 正常間隔 100-200ms に対し **~5×**（正常 cadence/jitter で誤発火しない最小余裕）。LLM dispatch 用の別系統である Policy Gate の `STALE_AFTER_S=0.5`／`UNAVAILABLE_AFTER_S=2.0`（:264-265）の中間に位置する。**TODO Phase-2: 実機 AMCL レートを実測して確定**（`emergency_min_distance`/`blocked_timeout` と同様の暫定値）。
- **動作 = precautionary estop（fail-safe）**: stale ＝ ロボットが自己位置不明のまま走行している恐れ → §:58/:66 の「下位層停止 → 安全停止」ドクトリンに従い **estop 相当**（Nav2 goal cancel ＋ `/{bot}/cmd_vel/emergency` へ zero `Twist`）＋ `/emergency/event`（`type="pose_stale"`）を発行する。**物理停止は level**（毎 tick 再アサート — twist_mux prio100 入力が 0.5s で失効するため）＝ **pose 鮮度が回復すれば EdgeLatch がリセットされ自動解除**される。誤検出（一過性の AMCL hiccup）でも被害は「最大 ~1s 停止 → 自動復帰」に限定され、走行中 localization ロストを見逃す false-negative の被害（衝突）より安全側。
- **`/emergency/event` 後方互換**: `type` に新値 `"pose_stale"` を足すのみ ＝ **additive**。コア形（`event_id/robot/type/severity/action_taken/timestamp/requires_llm_review` [+任意 `detail`]、:141-150）は不変で、State Cache は `active`/`history` ring に積むだけ（既存購読者は無視可）。**凍結契約 `warehouse_interfaces` は無変更**。
- **scan 鮮度は対象外（Phase-2 defer）**: `/scan` の stale 停止（`source_timeout`）は本来 `nav2_collision_monitor` のパラメータであり（R-39, doc07:249）、Guardian は `/scan` を購読しない（§:97 の collision_monitor 委譲。cmd_vel 挿入トポロジは下記 §「collision_monitor 委譲」で確定済、配線 impl は nav-traffic 調整で defer）。本節の freshness は **amcl_pose のみ**を対象とする、collision_monitor 採用までの interim な Guardian policy 層ガードである。

### blocked → Nav2 `progress_checker` 委譲（責務の明文化）

- **責務の所在**: nav 実行中の進捗喪失（経路追従が進まない）は **Nav2 `progress_checker`（`nav2_controller::SimpleProgressChecker`、`ws/src/warehouse_bringup/config/nav2_params.yaml:94-97`：`required_movement_radius 0.05` / `movement_time_allowance 10.0`）が `FAILED_TO_MAKE_PROGRESS` を上げ controller recovery を起動**する責務である（`nav2_params.yaml` 所有 = nav-traffic, doc16:187）。Guardian はこれを置換しない（既存パラメータで足り、本スライスは `nav2_params.yaml` を編集しない）。
- **Guardian 側 blocked は独立フォールバックとして維持**: `BlockTracker`（pose 変位ベース・`epsilon 0.02m`・**nav_status 非依存**）が `blocked_timeout` 超過で **low-harm `recovery` event**（estop ではない・物理停止しない）を出す。これは §:254 が言う「`RobotState.status` でも Nav2 でもない**別系統の独立フォールバック**」＝ progress_checker / State Cache `status` と独立に動く粗いセーフティネットで、estop に格上げせず**維持**する。
- **既知の限界（Phase-2 で縮退）**: Guardian は nav goal / `nav_status` feed を持たないため、**goal の無い正規 idle（静止）も `blocked_timeout` で誤検出**しうる。解消には blocked を nav_status でゲートする必要があり ＝ **Phase-2**（Guardian への nav_status 配線、本スライス対象外）。誤検出は low-harm（event のみ・LLM レビュー）なので暫定許容する。
- **status 値域は不変（ガードレール）**: 上記いずれも `RobotSnapshot.status`/`RobotState.status` に `"blocked"`/`"stale"` を**導出・追加しない**（:254 / `derive_status` は moving/idle のみ）。`"blocked"` 導出は #55 方針a ＝ 契約拡張で別 contract-PR（#55 は #128 で**方針b**に確定済 ＝ status 非依存信号で再基礎付け）。`pose_stale` は **status 値ではなく `/emergency/event` の `type` 値**である点に注意。

### collision_monitor 委譲: cmd_vel 挿入トポロジ（#126・docs 先行・配線は defer）

> :97 / :427 が「cmd_vel 挿入トポロジ未定義」のため defer していた collision_monitor 委譲の**トポロジを確定**する（docs-first 先行）。本節は **docs のみ**で、実配線（launch remap・collision_monitor config）は **nav-traffic 所有ファイル**（`nav2_bringup.launch.py` / `nav2_params.yaml`、doc16:187）に触れるため **nav-traffic 調整の impl slice に defer**。Guardian は **`/scan` を購読しない**（policy 層のまま）。

**配置 = Nav2 速度経路上・twist_mux の nav2(prio10) 入力の上流**（emergency prio100 は不変）:

```
Nav2 controller_server (FollowPath)
  └(remap cmd_vel)→ /bot{n}/cmd_vel/nav2_raw          ← 新・中間 plumbing topic（doc03 スコープ外, :111）
       nav2_collision_monitor  (per-bot, /bot{n} ns)
         cmd_vel_in_topic  = cmd_vel/nav2_raw
         cmd_vel_out_topic = cmd_vel/nav2             ← twist_mux prio10 入力（既存・不変）
         observation_sources = /bot{n}/scan（MS200, doc03:78）＋ /bot{n}/virtual_scan（相手ロボ, Mode A/B, 11a §VirtualScan:271-317）
         polygons = stop（＋任意 slowdown）／ source_timeout（/scan 途絶→停止）
Nav2 behavior_server (recovery: BackUp/DriveOnHeading/Spin)
  └→ collision_monitor 経由 か 直接 cmd_vel/nav2 へ bypass か = Open ⑥
     （recovery は障害物方向へ動くため stop polygon に永続的に阻まれうる, nav2_bringup.launch.py:166-180）
  /bot{n}/cmd_vel/nav2 (prio10) ┐
  Guardian /bot{n}/cmd_vel/emergency (prio100) ┤→ twist_mux(/bot{n}) → /bot{n}/cmd_vel
```

（中間トピック名 `cmd_vel/nav2_raw` は**例示**。確定すべき契約は「collision_monitor 出力 = 既存 twist_mux prio10 入力 `cmd_vel/nav2`」「入力 = controller_server の remap 先（behavior_server の recovery 経路の扱いは Open ⑥）」の 2 点。）

- **なぜ twist_mux の上流か（下流でない）**: ① 標準 Nav2 では collision_monitor を velocity producer と base の間に置く。② **twist_mux の emergency prio100 override は FROZEN safety contract**（`twist_mux.yaml` / doc15:389-395）＝ collision_monitor を**下流**（merged 出力）に置くと override 意味論を壊し prio100 を迂回しうる。nav2 経路のみに挟めば **prio100 不変**。③「**補完 not 置換**」（:97）＝ collision_monitor は nav 経路の物理近接反射（C++・高レート・amcl 非律速）を担い、Guardian は battery/event/LLM-review/freshness(pose, #152)/blocked policy ＋ prio100 estop を保持する。④ Mode A/B の唯一の運動源は Nav2（Guardian は zero `Twist` のみ）なので nav 経路を止めれば停止する。
- **source_timeout と #152 freshness は別系統の補完**: collision_monitor `source_timeout` = **/scan 鮮度**→物理停止（nav 経路）。Guardian `pose_freshness_timeout`（#152）= **/amcl_pose 鮮度**→policy estop（localization 整合）。別センサ・別層で重複しない（:427 が defer としていた scan 鮮度は本トポロジで collision_monitor 責務と確定）。
- **virtual_scan は dual-consumer（移設ではない）**: collision_monitor は `/bot{n}/virtual_scan` の**追加 subscriber**。同トピックは引き続き **costmap obstacle_layer の `observation_source`（`nav2_params.yaml:221,274` / 11a:280,299）として planning 用に維持**する（同一トピック・2 consumer ＝ reflex collision_monitor ＋ planning costmap）。**costmap から外さない**。Mode C（Open-RMF）で costmap 側 virtual_scan を外す（11a:317,321）のは交通調停を Open-RMF に委ねるためで、本トポロジ（Mode A/B）の dual-consumer とは別件。
- **責務移動（collision_monitor land 時）**: Guardian の amcl_pose ベース `near_collision` 物理反射は collision_monitor の scan polygon（高速・C++）に置換される。Guardian の `near_collision` を**撤去**するか**粗い backup として残す**かは **impl slice の決定**（本 docs PR は topology のみ確定、撤去はしない）。最終保証は ESP32 Layer0（:75-78）。
- **/emergency/event surfacing**: collision_monitor は Nav2 C++ node で `/emergency/event` を出さない。LLM-review stream へ出すなら Guardian（or 小 bridge）が collision_monitor の state を購読し **additive な新 `type`（例 `collision_monitor_stop`）** で発行する（#152 の additive 規約踏襲＝コア形 :141-150 不変。debounce 等のしきい値を設ける場合は #152 と同じ厳密 `>` 規約に従う）。機構詳細は impl slice。
- **Mode 別**: Mode A/B = scan ＋ virtual_scan polygon（virtual_scan は Mode C で gating off, 11a:317）。Mode C = Open-RMF が交通調停＝collision_monitor は real `/scan` のみ or Open-RMF に委譲（詳細は Mode C impl 時。本 PR は Mode A/B 主眼）。
- **defer / 触らない**: 配線は nav-traffic 所有（`nav2_bringup.launch.py` remap・collision_monitor config・lifecycle_manager 登録・`nav2_params.yaml`、doc16:187）＝ nav-traffic Issue へ予告し調整。nav_status gating（BlockTracker idle 誤検出解消）は Phase-2。**凍結契約 `warehouse_interfaces` 不変・twist_mux emergency prio100 不変**。
- **impl slice で確定する Open 項目**: ① Guardian `near_collision` 撤去 vs backup、② collision_monitor polygon 寸法（隘路 200mm × 車体 150mm の R-42 と整合）、③ `source_timeout` 値、④ Mode C の scan 構成、⑤ event surfacing 機構、⑥ **behavior_server recovery（BackUp/DriveOnHeading/Spin）を collision_monitor 経由とするか直接 `cmd_vel/nav2` へ bypass するか**（recovery は障害物方向へ動くため stop polygon に阻まれうる ＝ R-42 隘路で deadlock 懸念。slowdown polygon 化／recovery 中 monitor 無効も選択肢, `nav2_bringup.launch.py:166-180`）。（⑤⑥① は下記「Open 項目の確定（#233）」で確定／②③ は live tune＝human・④ は Mode C impl 時）

### collision_monitor 委譲: Open 項目の確定（#233 — Closes #126 ゲートの docs 確定分）

> :552 の Open ①〜⑥ のうち、本 PR（#233・**`Refs #126` であり `Closes` ではない**＝live PoC は人間 Docker ゲートで残）で **⑤・⑥ を確定し ① を docs-defer** する。**②③ は live tune＝human**（下記 `tests/e2e/README.md` の 2-bot PoC runbook で実走）、**④ は Mode C impl 時**。配線（`nav2_bringup.launch.py` / `collision_monitor.yaml`）は #229（`bce4853`）で land 済＝本 PR はコード不変・docs のみ。**凍結契約不変**（`warehouse_interfaces` / twist_mux emergency prio100 / `/emergency/event` コア形 :141-150 ＝ additive `type` のみ）。

- **⑤ event surfacing ＝ 確定: Phase-2 へ defer**（collision_monitor stop を当面 `/emergency/event` に surface しない）。根拠: ① stop は自己完結した C++ reflex で**安全は surfacing に不依存**（出さなくても物理停止は効く）。② `/emergency/event` は Guardian 級緊急（estop / `pose_stale`(#152) / battery）に予約された stream で、routine な高レート近接 reflex を混ぜると `requires_llm_review` triage を希釈する。③ stop cadence が live 未測のため #152 の厳密 `>` debounce しきい値を意味づけて設定できない。④ 機構は :549 に記録済（Guardian or 小 bridge が `collision_monitor_state`(`collision_monitor.yaml:56`) を購読し additive な `collision_monitor_stop` type を発行・コア形 :141-150 不変）＝Phase-2 turnkey。実装は `warehouse_safety` 所有（本レーン編集境界外）。**戦略層**（commander が見る situation＝VirtualScan / CTRV `predicted_position_3s`）は別途充足されており、defer が延期するのは reflex の **observability/LLM-review surfacing のみ**で、これは `collision_monitor_state`（rosbag 可）＋ `polygon_stop` viz(`collision_monitor.yaml:72`) から回収できる（＝surfacing が「冗長」なのではなく**層が異なる**）。
- **⑥ behavior_server recovery 経路 ＝ 確定: BYPASS**（recovery は collision_monitor を経由せず `cmd_vel/nav2` を直接 publish）。現配線がこの形（`nav2_bringup.launch.py:179`＝recovery remap `cmd_vel`→`cmd_vel/nav2`）で、`tests/unit/test_collision_monitor_launch.py:124`（`test_behavior_server_bypasses_collision_monitor`）が全 mode で pin 済＝本確定は**既存・テスト済挙動の批准**。根拠: recovery（Spin / BackUp / DriveOnHeading）は**障害物方向へ動く**ため monitor 経由にすると、recovery を起動した当の stop polygon breach が recovery 速度を 0 化し、**R-42 200mm 隘路で自己ラッチ deadlock**（breach から永久に脱出不能）になる。bypass の安全性: recovery は bounded・低速・Nav2 supervised・短時間で、twist_mux emergency prio100（Guardian estop）が nav2 経路の上に残り、ESP32 Layer0（≤0.3 m/s クランプ＋近接停止, :73-78）が最終床。
  - **残留リスク（記録）**: bypass 中の物理近接ガードは ESP32 proximity stop ＋ Guardian `amcl_pose near_collision`（AMCL 律速・Python）に限定され、**robot-robot 近接**（`virtual_scan` も bypass）は粗いガードのみになる狭い窓がある。**Phase-2 再訪トリガ**＝live PoC で recovery が (i) stop polygon に阻まれる **または** (ii) near-miss を出す場合に、slowdown polygon 化／recovery 中 monitor 無効化を検討（impl-slice）。
- **① Guardian `near_collision` 撤去 vs 維持 ＝ docs-defer**（本 PR で**非決定**・`warehouse_safety` を触らない）。この判定は safety-state スライス所有（撤去対象コードが `warehouse_safety`）であり、かつ **collision_monitor の live-PoC Go に GATE** する。撤去の前提＝**R-39 reflex 成立 ∧ #156 non-mask ∧ R-42 non-misfire の全充足**（双方向ゲート）。それを満たすまで Guardian `near_collision` は**冗長な粗 backup として RETAIN**（:548「撤去はしない、topology のみ確定」と整合・Guardian=prio100 / collision_monitor=prio10 で別層＝二重発火しても prio100 が支配し競合しない）。**① の帰結は ② に依存**: polygon が 0.15m head-on で over-trip する（`collision_monitor.yaml:32-37`＝暫定 0.09 circle に相手表面 ~0.075m が入る）なら、safety-state は near_collision を残し polygon を縮小／forward-bias する選択肢を採りうる。最終床は ESP32 Layer0（:73-78）。

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
