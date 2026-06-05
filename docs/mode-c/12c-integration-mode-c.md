# システム統合 — Mode C（LLM + Open-RMF）

作成日: 2026-05-25
由来: 旧 `12-hermes-agent-integration.md` を分割・再編（同ファイルは現存しない）

> **関連ドキュメント**
> - [共通インフラストラクチャ設計](../architecture/12-infrastructure-common.md)
> - [システム統合 — Mode A/B（LLM単独 / 自作ルールベース）](../mode-a/12a-integration-mode-a.md)

---

## 概要

Mode C では、Open-RMF が交通管理・経路調整を担い、LLM は戦略判断のみを行う。

- **Mode C**: Warehouse MCP Server → Open-RMF REST API → Fleet Adapter → Nav2

Open-RMF の Traffic Schedule が経路衝突予測・デッドロック解消をイベント駆動で処理するため、LLM の 3秒遅延が安全性に影響しない構造となる。

---

## サイクル依存図（Mode C）

共通原則は `../architecture/12-infrastructure-common.md` のサイクル依存関係セクションを参照。以下は Mode C 固有のデータフロー。

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
│  Soft Real-time（集約・調停、100ms〜イベント駆動）              │
│                                                                │
│  [State Cache Node 100ms]                                      │
│        ├── /amcl_pose, /battery, /odom, /emergency/event 購読  │
│        └── → /tmp/warehouse/state.json（atomic write）         │
│                                                                │
│  [Open-RMF イベント駆動]                                       │
│        ├── ← Fleet Adapter ← Nav2 フィードバック               │
│        ├── Traffic Schedule 計算 → 衝突予測・待機・迂回          │
│        └── → Fleet Adapter → Nav2 ゴール送信                   │
└───────────────────────────────────────────────────────────────┘
                          │
                          │ pull（5秒ごとにスナップショット取得、Mode C）
                          ▼
┌───────────────────────────────────────────────────────────────┐
│  Non Real-time（戦略判断、停止しても物理は安全）                │
│                                                                │
│  [LLM Bridge Node 5秒サイクル（Mode C）]                                 │
│        ├── State Cache JSON からスナップショット取得            │
│        ├── emergency 情報があれば付加                           │
│        └── → POST → Hermes Gateway                             │
│                         │                                      │
│  [Hermes Gateway]       │ LLM API（1-3秒）                     │
│        └── MCP → [Warehouse MCP Server]                        │
│                    ├── Policy Gate 検証                         │
│                    └── → Open-RMF Task API → Fleet Adapter → Nav2
└───────────────────────────────────────────────────────────────┘
```

**Mode C の特徴**: Soft RT 層に Open-RMF がおり、経路衝突予測・待機・迂回をイベント駆動で即時処理する。LLM（Non RT 層）はタスク割当のみで、交通管理に関与しない。LLM が停止しても Open-RMF + Nav2 で交通管理と物理制御が継続する。

---

## Mode C用 プロセス構成図（6プロセス + ROS 2基盤）

```
┌──────────────────────────────────────────────────────────┐
│  Jetson Orin Nano Super (8GB)                             │
│                                                           │
│  ┌──────────────────────────────────────────────────────┐ │
│  │  Emergency Guardian（rclpy、50ms周期目標、LLM非経由） │ │
│  │  ├── 2台間距離監視（< 0.3m → Nav2 cancel要求+cmd_vel停止）│ │
│  │  ├── バッテリー監視（≤ 10% → Nav2 cancel要求+cmd_vel停止）│ │
│  │  ├── blocked監視（> 10秒 → Nav2リカバリー要求）      │ │
│  │  ├── Nav2 goal cancel + cmd_vel停止（LLM非経由）     │ │
│  │  └── /emergency/event Publish（構造化イベント）       │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                           │
│  ┌──────────────────────────────────────────────────────┐ │
│  │  State Cache Node（rclpy、100ms周期目標）              │ │
│  │  ├── /bot{n}/amcl_pose, battery, odom 購読           │ │
│  │  ├── /emergency/event 購読                           │ │
│  │  └── → /tmp/warehouse/state.json（atomic write）     │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                           │
│  ┌──────────────────────────────────────────────────────┐ │
│  │  LLM Bridge Node（rclpy、5秒サイクル（Mode C））               │ │
│  │  ├── 5秒サイクル（Mode C） → Hermes Gateway POST               │ │
│  │  ├── /emergency/event 受信 → 次回POSTに緊急情報付加   │ │
│  │  └── /llm/reasoning, /llm/command Publish             │ │
│  └───────────────────────┬──────────────────────────────┘ │
│                          │ HTTP POST                       │
│  ┌───────────────────────▼──────────────────────────────┐ │
│  │  Hermes Gateway（daemon, port 8642）                 │ │
│  │  ├── Provider: Claude / GPT / Gemini / Grok          │ │
│  │  ├── Memory（判断パターン永続化）                     │ │
│  │  ├── Skills（成功パターン学習）                       │ │
│  │  ├── Langfuse Plugin（LLMトレース自動記録）           │ │
│  │  └── MCP Client → Warehouse MCP Server（stdio）      │ │
│  └───────────────────────┬──────────────────────────────┘ │
│                          │ MCP (stdio)                     │
│  ┌───────────────────────▼──────────────────────────────┐ │
│  │  Warehouse MCP Server（自作、rclpy不要）             │ │
│  │  ├── Policy Gate（全コマンド検証、安全弁）            │ │
│  │  ├── State Cache読取（/tmp/warehouse/state.json）     │ │
│  │  ├── Command Audit Log（ローカルログ）                │ │
│  │  ├── traffic_mode切替                                 │ │
│  │  │   └── "open-rmf" → Open-RMF REST API              │ │
│  │  └── 7ツール（共通設計参照）                          │ │
│  └───────────────────────┬──────────────────────────────┘ │
│                          │ REST (Open-RMF API)             │
│  ┌───────────────────────▼──────────────────────────────┐ │
│  │  Open-RMF                                            │ │
│  │  ├── rmf_traffic_schedule（経路計画・衝突予測）       │ │
│  │  ├── Fleet Adapter（free_fleet + battery拡張）       │ │
│  │  │   ├── zenoh bridge → Nav2 /bot1                   │ │
│  │  │   └── zenoh bridge → Nav2 /bot2                   │ │
│  │  ├── rmf-web API Server（REST, port 8000）           │ │
│  │  ├── Navigation Graph（shelf_1, berth_A等のwaypoint）│ │
│  │  └── Task Dispatcher: 無効（Allocator/RMF biddingで割当）│ │
│  └──────────────────────────────────────────────────────┘ │
│                                                           │
│  Nav2 × 2 (/bot1, /bot2)  |  AMCL × 2  |  SLAM           │
│  micro-ROS Agent  |  ESP32 × 2（最下層安全系）             │
└──────────────────────────────────────────────────────────┘
```

> ⚠️ **dispatch 経路の補足（S2-PR2 HALF B / #4 以降）**: 上図の `Hermes Gateway →(MCP stdio)→ Warehouse MCP Server` 線は **Hermes ネイティブのツール実行経路**で、採用の commander dispatch ではない。採用形では Bridge が `WarehouseTools().dispatch` を **in-process 呼出し**する（doc08:166-168 / doc15:50,211）。Mode C では下段の Nav2 制御は **Open-RMF 経由**のため、Mode A/B の Nav2 Bridge REST forwarder は注入されない（`llm_bridge.py` の `NAV2_BRIDGE_MODES = {none, simple}`）。Open-RMF への dispatch 転送経路は後続スライス。

---

## Mode C用 通信フロー・タイミング図

```
t=0.0s   LLM Bridge Node: サイクル開始（前回応答から3秒経過）
t=0.0s   LLM Bridge Node: current_gen += 1, gen_store に publish
t=0.0s   LLM Bridge Node: emergency情報があれば付加
t=0.1s   LLM Bridge Node: POST /v1/chat/completions（Hermes Gateway, situation JSON に gen_id 同梱）
t=0.2s   Hermes: LLM API呼出し開始
t=1.5s   Hermes: LLM応答受信
t=1.5s   Hermes: MCP → Warehouse MCP Server ツール呼出し
t=1.5s   Warehouse MCP: gen_id 検証 → Policy Gate 検証
t=1.6s   Warehouse MCP: Open-RMF Task API に dispatch
t=1.7s   Open-RMF: Traffic Schedule 計算 → Fleet Adapter → Nav2 ゴール送信
t=2.0s   Hermes: run完了 → LLM Bridge Node にレスポンス返却
t=2.0s   LLM Bridge Node: /llm/reasoning, /llm/command Publish
t=2.0s〜5.0s  待機（3秒、Mode C は Open-RMF が即応するためコスト優先）
t=5.0s   次のサイクル開始
```

（並行して）
```
常時    Emergency Guardian: 50ms周期で安全監視
常時    State Cache Node: 100ms周期で状態ファイル更新
常時    Open-RMF: Fleet Adapter が Nav2 フィードバック監視・交通調整
```

---

## Mode C用 systemd構成（Jetson起動時）

```
# 起動順序（After= で依存関係を制御）

1. micro_ros_agent.service          ← 最初（ロボット通信）
2. nav2_bot1.service                ← micro-ROS後
3. nav2_bot2.service                ← micro-ROS後
4. emergency_guardian.service       ← Nav2後（安全監視開始）
5. state_cache.service              ← Nav2後（状態収集開始）
6. rmf_web.service                  ← Nav2後（Open-RMF起動）
7. hermes_gateway.service           ← 独立（LLM準備）
8. warehouse_mcp.service            ← hermes後（MCP接続）
9. llm_bridge.service               ← 全て起動後（5秒サイクル開始: Mode C）
```

各サービス共通:
- `Restart=on-failure`（落ちたら再起動）
- `StandardOutput=journal`（journaldにログ）
- ヘルスチェック（各ノードが `/health` トピック or REST endpointを公開）

---

## Open-RMF連携のリスクと対策

| リスク | 影響 | 対策 | フォールバック |
|--------|------|------|---------------|
| Open-RMF Jazzy ビルド失敗 | 低→中 | **2026-05-26調査: aptバイナリ提供確認済み（`apt install ros-jazzy-rmf-*`）。ARM64含む。ソースビルド不要の見込み** | Mode A/B で先行、Mode Cは後回し |
| free_fleet zenoh通信不安定 | 中 | Phase 3で検証 | 直接 ROS 2 Action Client に切替 |
| Hermes Gateway ARM64非対応 | 低 | **2026-05-26調査: Pure Python wheel、Docker ARM64対応済み、Jetsonユーザー実在。動作する可能性が高い** | 案D（SDK直接） |
| Hermes Gateway メモリ > 2GB | 高 | Phase 0.5で `htop` 計測 | Skills/Memory無効化で軽量化。最終手段は案D（SDK直接） |
| レイテンシ > 4秒 | 中 | ポーリング間隔を適応的に調整 | 5秒間隔に延長 |
| State Cache ファイルI/O遅延 | 低 | tmpfs使用（RAMディスク） | REST API方式に変更 |

---

## Phase 0.5 検証順序

```
Day 1-2: Hermes Gateway 基本検証（Mac Docker）
  ├── pip install hermes-agent
  ├── hermes gateway → メモリ計測
  ├── POST /v1/chat/completions → レイテンシ計測
  ├── MCP接続（簡易テストサーバー）
  └── 判定: 動作OK → 続行、NG → 案D（SDK直接）

Day 3-4: Warehouse MCP Server プロトタイプ
  ├── 7ツールのスキーマ定義
  ├── Policy Gate 基本実装
  ├── Mode A（Nav2 Bridge経由）で Gazebo ロボット制御
  └── dispatch_task → Gazebo ロボットが移動することを確認

Day 5-7: State Cache + Emergency Guardian
  ├── State Cache Node → /tmp/warehouse/state.json 書出し
  ├── Emergency Guardian → 距離監視 → Nav2 cancel
  ├── LLM Bridge Node → 5秒サイクル（Mode C） → Hermes POST
  └── E2E: Claude が Gazebo 上の2台を戦略的に制御

Day 8-10: Mode C（Open-RMF）統合（余裕があれば）
  ├── Open-RMF Jazzy ビルド
  ├── Navigation Graph 定義
  ├── Fleet Adapter → Nav2 接続
  └── dispatch_task → Open-RMF → Nav2 の E2E
```
