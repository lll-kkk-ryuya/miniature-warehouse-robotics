# ソフトウェアアーキテクチャ

作成日: 2026-05-21
更新日: 2026-05-23

## システム全体構成

```
┌──────────────────────────────────────────────────┐
│  LLM API（クラウド）— 差し替え可能                 │
│  ├── Claude（Anthropic API）                      │
│  ├── ChatGPT（OpenAI API）                        │
│  ├── Gemini（Google AI API）                      │
│  └── Grok（xAI API）                              │
│  役割: 戦略判断（タスク割当・優先順位・バッテリー管理）│
└──────────────────┬───────────────────────────────┘
                   │ REST API（JSON）
                   ▼
┌──────────────────────────────────────────────────┐
│  Jetson Orin Nano Super（司令塔）                  │
│  ├── ROS 2 Jazzy（Ubuntu 24.04）                   │
│  ├── LLM Bridge Node（Mode A: 3秒 / Mode C: 5秒 → Hermes Gateway） │
│  ├── Hermes Gateway（daemon、Warehouse MCP Server接続）│
│  ├── Warehouse MCP Server（自作、Policy Gate + 座標変換）│
│  ├── State Cache Node（100ms周期、状態集約JSON書出し） │
│  ├── Emergency Guardian（50ms周期、LLM非経由安全監視） │
│  ├── Nav2（経路計画・障害物回避）                   │
│  ├── SLAM Toolbox（minicar ORBBEC MS200で地図生成）         │
│  ├── AMCL × 2（自己位置推定、Bot1/Bot2各1）          │
│  ├── FoundationPose（荷物認識、将来拡張）           │
│  └── WO連携API（REST/WebSocket）                  │
├──────────────────────────────────────────────────┤
│  WiFi UDP（micro-ROS Agent）                       │
├──────┬──────┬────────────────────────────────────┤
│ Bot1 │ Bot2 │  ESP32 + micro-ROS                  │
│      │      │  Pub: /odom, /scan                   │
│      │      │  Sub: /cmd_vel                       │
└──────┴──────┴────────────────────────────────────┘
        │
        ▼ （開発・撮影時のみ）
┌──────────────────────────────────────────────────┐
│  RunPod A10G（クラウド）                           │
│  └── Isaac Sim 5.1（デジタルツイン・映像生成）     │
│      ※RTコア必須 → A10G/L4/RTX 4090              │
└──────────────────────────────────────────────────┘
```

## 判断モデルと安全レイヤー（モードC: Open-RMF主方針）

### 3層判断モデル

| 判断レベル | 担当 | 応答速度 | 例 |
|---|---|---|---|
| 戦略（何をやるか） | LLM（Claude等） | 1-3秒 | 「Bot1にtask_1、Bot2にtask_2を割当」 |
| 交通管理（鉢合わせない） | Open-RMF | 即時 | 「Bot2は5秒待機、Bot1が先に通路A通過」 |
| 反射（ぶつからない） | Nav2 DWB/MPPI | 50ms | 「目の前に壁、停止」 |

LLMはタスク割当・優先順位・バッテリー管理の戦略判断のみ。交通管理（経路選択・衝突回避・待機指示）はOpen-RMFが即時処理。物理的安全はNav2が担保。LLMの応答遅延やエラーが安全性に影響しない設計。

### 4層安全レイヤー

| Layer | 担当 | 周期 | LLM依存 | 内容 |
|-------|------|------|:------:|------|
| 0 | ESP32 / micro-ROS | 即時（MCU内） | ✕ | ハードウェア安全停止（最終防衛線）、速度上限0.3m/sクランプ |
| 1 | Emergency Guardian | 50ms周期（目標） | ✕ | 2台間距離監視・バッテリー監視・blocked検出 → Nav2 cancel + cmd_vel停止 |
| 2 | Open-RMF | イベント駆動 | ✕ | 経路衝突予測・待機・迂回指示 |
| 3 | Claude / Hermes | Mode A: 3秒 / Mode C: 5秒 | ◎ | 事後の説明・タスク再割当・復旧方針の提案 |

詳細は `12-infrastructure-common.md` の安全レイヤー設計を参照。

## ROS 2 トピック設計

### ロボット → Jetson（micro-ROS経由）

| トピック | 型 | 内容 |
|---------|-----|------|
| `/bot{n}/odom` | `nav_msgs/Odometry` | エンコーダベースのオドメトリ |
| `/bot{n}/scan` | `sensor_msgs/LaserScan` | ORBBEC MS200 dToF LiDAR（360°スキャン、AMCL自己位置推定 + 障害物検知） |
| `/bot{n}/battery` | `sensor_msgs/BatteryState` | バッテリー残量（実機: micro-ROS firmware が供給 Phase 1+／sim: `warehouse_sim` の合成 publisher が供給 #44/#156） |

> ※ sim（#43 `ros_gz_bridge`）が **gz から ROS へ橋渡し**するのは `/bot{n}/scan` `/bot{n}/odom` `/bot{n}/cmd_vel` のみ。`/bot{n}/imu` は sim では橋渡ししない（gz トピックは URDF が出すが ros_gz_bridge 対象外＝doc03 契約外、必要時に bridge へ追加）。
> **`/bot{n}/battery`（#44/#156）**: gz に battery sensor は無いが、State Cache はスナップショットを `pose + velocity + battery` が揃った bot のみ出力する（doc12 §State Cache・本doc下記）ため、**battery 欠如＝その bot が situation JSON に一切現れない＝LLM 司令官が見えない**。よって sim は **合成 publisher `warehouse_sim.battery_publisher`（`sim_battery_publisher`）が `BatteryState` を publish** する（橋渡しではなく生成。gz `LinearBatteryPlugin` は `warehouse_description` 改変＋gz 独自スケールの再導入になるため不採用）。`percentage` は **config `safety.battery_percentage_scale`（単一ソース）と同一スケール**で出すため、producer（sim）と2消費者（State Cache / Emergency Guardian）でスケールがズレない（#44 split-brain 回避を producer 側でも担保）。実機ドライバの実スケール計測は **Phase 1（#44 OPEN 継続）**。

### Jetson → ロボット

| トピック | 型 | 内容 |
|---------|-----|------|
| `/bot{n}/cmd_vel` | `geometry_msgs/Twist` | 速度指令（前進・旋回） |

### Jetson 内部

| トピック | 型 | 内容 |
|---------|-----|------|
| `/bot{n}/amcl_pose` | `geometry_msgs/PoseWithCovarianceStamped` | AMCL推定位置（地図上の位置） |
| `/map` | `nav_msgs/OccupancyGrid` | 2D地図（実機=SLAM生成 / sim=`map_generator`決定論生成。doc09 §7） |
| `/bot{n}/path` | `nav_msgs/Path` | Nav2の計画経路 |
| `/bot{n}/goal_pose` | `geometry_msgs/PoseStamped` | 目標位置（モードC: Fleet Adapter発行、モードA/B: Warehouse MCP Server内部で発行） |
| `/emergency/event` | `std_msgs/String` | Emergency Guardian構造化イベント（JSON） |
| `/wo/mission` | `std_msgs/String`（JSON） | WOからのミッション指示（Phase 4 で `.msg` 化, doc16 §3） |
| `/llm/situation` | `std_msgs/String`（JSON） | LLMに送る状況データ（Phase 4 で `.msg` 化, doc16 §3） |
| `/llm/command` | `std_msgs/String`（JSON） | LLMからの指示データ（Phase 4 で `.msg` 化, doc16 §3） |
| `/llm/reasoning` | `std_msgs/String` | LLMの判断理由（表示用） |
| `/state_cache/snapshot` | `std_msgs/String`（JSON） | State Cache の集約スナップショット（凍結 `StateSnapshot`、100ms周期）。司令官/MCP はファイル `/tmp/warehouse/state.json`、キャラLLM はこのトピックで購読（doc12 §State Cache 配信2系統） |
| `/character/speech` | `std_msgs/String`（JSON） | キャラLLM Bot1/Bot2 の発話（実況・交渉ターン）。画面表示 / Langfuse / 相手キャラが購読（doc14 §ROS 2 トピック） |
| `/negotiation/start` | `std_msgs/String`（JSON） | キャラLLM 交渉の開始（司令官の `start_negotiation` 経由で Warehouse MCP Server が発行、gen_id + starter 同梱。Mode A/C 両方で発動）（doc14 §交渉プロトコル） |
| `/negotiation/turn` | `std_msgs/String`（JSON） | 交渉のバトンパス（`{turn, next}`、キャラLLM 間のターン制御）（doc14 §バトンパス方式） |
| `/negotiation/proposal` | `std_msgs/String`（JSON） | 交渉合意の構造化提案（凍結契約 `Proposal`、gen_id付）→ 司令官が次サイクルで取込→検証→Policy Gate→MCP（doc14 §交渉プロトコル） |
| `/negotiation/abort` | `std_msgs/String`（JSON） | 交渉中断信号（Emergency Guardian が **estop（緊急停止）時のみ** `{reason, bot, event_id}` を `/emergency/event` と同 `event_id` で publish・低危険度 recovery では発行しない）（doc12 Emergency Guardian / doc14 §R2 / 08a:363-372） |
| `/bot{n}/virtual_scan` | `sensor_msgs/LaserScan` | 相手ロボを仮想障害物として自機 Nav2 `obstacle_layer` に注入（frame `bot{n}/base_link`、近接時のみ）。モードA/B のみ起動（モードC は Open-RMF）（11a §VirtualScan） |
| `/nav2_bridge/goal_result` | `std_msgs/String`（JSON） | Nav2 Bridge のゴール完了/失敗通知（`{robot, task_id, result}`、200msポーリング検出）→ State Cache Node が `nav_status` 更新。モードA/B 専用（12a §ゴールフィードバック設計） |

> ※ 上表は Jetson 内部の**全アプリ契約トピックを網羅**する（doc03 = トピック契約の単一参照カタログ）。各トピックの publisher/subscriber・プロトコル・ペイロード schema の**正本は 内容列がリンクする設計 doc**（doc12 State Cache / doc14 キャラLLM・交渉 / 11a VirtualScan / 12a Nav2 Bridge）であり、doc03 は topic 名・型・一行責務のみを保持する（詳細の二重管理＝ドリフトを避ける）。生 Nav2/tf・`/clock`・costmap 等の plumbing トピックは契約対象外（doc03 スコープ外）。

## ソフトウェアスタック詳細

### ROS 2 Jazzy

| 項目 | 内容 |
|------|------|
| バージョン | Jazzy Jalisco |
| OS | Ubuntu 24.04 |
| EOL | 2029年5月（LTSリリース、2024年5月リリース） |
| Isaac ROS対応 | release-3.x の公式対応バージョン |

Humbleではなく Jazzy を選択する理由:
- Isaac ROS最新版が Jazzy をターゲットにしている
- Nav2、SLAM Toolbox ともに Jazzy 公式対応済み
- Humble（EOL 2027年5月）より長いサポート期間

**確認済み（2026-05-29）**: ROS 2 Jazzy Jalisco は公式LTSリリース（2024年5月リリース、EOL 2029年5月）。Humbleも確定LTS（EOL 2027年5月）。

### micro-ROS（ESP32側）

| 項目 | 内容 |
|------|------|
| RTOS | FreeRTOS |
| 通信 | WiFi UDP → micro-ROS Agent（Jetson上で実行） |
| 対応ROS 2 | Humble対応済み、**Jazzy対応済み**（2026-05-22確認、micro_ros_setupにJazzyブランチ存在） |

注意:
- WiFi経由のUDP転送では遅延が生じる。タイムクリティカルな制御ループが必要になった場合はUSB有線接続を検討。
- micro-ROS の Jazzy 対応は確認済み（2026-05-22確認）。ROS 2 Jazzy で統一して進める。Humbleフォールバック計画は保険として残す。

### Nav2

| 項目 | 内容 |
|------|------|
| グローバルプランナー | NavFn / Dijkstra（×2、Bot1/Bot2各1） |
| ローカルコントローラー | Phase 2前半: DWB → Phase 2後半: MPPI Controller に移行（×2） |
| コストマップ | 2Dコストマップ（SLAMの地図ベース）（×2） |
| リカバリー動作 | Spin, BackUp, Wait（×2） |

小規模環境（1,820×910mm）でのチューニングポイント:
- コストマップのセルサイズを小さくする（0.01〜0.02m）
- 更新レートを上げる
- ロボットの footprint を正確に設定する

### SLAM Toolbox

| 項目 | 内容 |
|------|------|
| モード | 非同期モード（軽量動作） |
| センサー | minicar搭載 ORBBEC MS200（teleop走行でスキャン） |
| 出力 | 2D OccupancyGrid |

minicarをteleop（手動操縦）でジオラマ内を走り回らせ、搭載MS200の360°スキャンデータからSLAM地図を生成する。固定設置のRPLiDAR A1はSLAMには使用しない（固定位置からでは棚裏の遮蔽により不完全な地図になるため）。

---

## LLM Bridge Node（新規・中核コンポーネント）

### アーキテクチャ

```
LLM API（Claude/ChatGPT/Gemini/Grok）
    ▲               │
    │ 状況JSON       │ 指示JSON
    │               ▼
LLM Bridge Node（ROS 2 ノード）
    ▲               │
    │ ROS 2トピック   │ Nav2 ゴール送信
    │               ▼
  Bot1/Bot2        Nav2
```

### LLM Bridge Node の役割

1. ROS 2トピックからロボットの状態を収集（位置、障害物、バッテリー等）
2. 状態を構造化JSON に変換してLLM APIに送信（サイクル長: Mode A=3秒 / Mode C=5秒、レスポンス駆動）
3. LLMの返答（指示JSON）をパースしてNav2にゴール送信
4. LLMの判断理由（reasoning）をログ記録・画面表示
5. LLMの差し替え（Claude ↔ ChatGPT ↔ Gemini ↔ Grok）を1行で切替可能（Hermes Agent経由）

### フォールバック設計

| 異常 | 動作 |
|------|------|
| LLM APIタイムアウト | 前回の指示を継続 |
| LLM APIエラー | Nav2単体で自律走行を継続 |
| 不正なJSON返答 | 無視して再リクエスト |
| 物理的に不可能な指示 | Nav2が拒否（安全ネット） |

詳細設計は `08-llm-bridge-common.md` を参照。Hermes Agent + Warehouse MCP Server の統合設計は `15-mcp-platform.md` を参照。

---

## Warehouse Orchestrator 連携

### アーキテクチャ

```
WO（Webアプリ）  ←→  WO Bridge Node（ROS 2）  ←→  Nav2
    │                      │
    │ REST/WebSocket        │ ROS 2 トピック
    │                      │
    └── 診断画面            └── ミッション指示
        KPI表示                  目標位置送信
        ヒートマップ              状態受信
```

### WO Bridge Node の役割

1. WOからミッション指示を受け取る（REST API）
2. Nav2にゴールを送信する
3. ロボットの位置・状態をWOに返す（WebSocket）
4. Before/After の切り替え制御

### Before/After 制御ロジック

| モード | 動作 |
|--------|------|
| Before | 各ロボットが独立して最短経路で走行（渋滞が発生する設定） |
| After | LLMの指示に従い、一方通行ルール・迂回・タイミング調整を適用 |

---

## NVIDIAモデル活用

| モデル | Phase | 用途 |
|--------|-------|------|
| Isaac Sim 5.1 | Phase 5 | デジタルツイン構築、Before/After映像 |
| Isaac ROS | Phase 2〜 | Jetson上でNav2高速化 |
| FoundationPose | Phase 5+ | 荷物（箱・パレット）の姿勢認識 |
| SLAM Toolbox | Phase 2 | 2D地図生成 |

### Isaac Sim 連携

- クラウド（RunPod A10G）上で Isaac Sim を実行
- ジオラマと同じレイアウトの3Dシーンを構築
- ROS 2 Bridge でロボットの位置データを同期
- 開発・撮影時のみ使用（常時起動不要）

---

## 開発環境

### Mac（開発マシン）— MacBook Pro M4 16GB

| ツール | 用途 | Phase |
|--------|------|-------|
| Docker Desktop | ROS 2 + Gazebo コンテナ実行 | Phase 0〜 |
| `tiryoh/ros2-desktop-vnc:jazzy` | ROS 2 Jazzy + Gazebo Harmonic（gz-sim 8.11, ARM64-native。動作確認済 2026-05-30 / #43） | Phase 0〜 |
| VS Code | ローカル開発 + Remote SSH（Jetson接続） | Phase 0〜 |
| Git | バージョン管理 | Phase 0〜 |

### Jetson（実行マシン）

| ツール | 用途 | Phase |
|--------|------|-------|
| ROS 2 Jazzy（Ubuntu 24.04） | 全ノードのホスト | Phase 1〜 |
| Nav2 | 経路計画・障害物回避 | Phase 2〜 |
| SLAM Toolbox | 2D地図生成 | Phase 2 |
| micro-ROS Agent | minicarとのWiFi通信 | Phase 1〜 |
| LLM Bridge Node | LLM API連携（Hermes Gateway + Warehouse MCP Server） | Phase 3〜 |

### 可視化・モニタリング

| ツール | 用途 | Phase |
|--------|------|-------|
| RViz2 | 地図・経路・ロボット位置のリアルタイム表示 | Phase 1〜 |
| Foxglove | デモ画面・KPIダッシュボード（YouTube撮影用に検討） | Phase 4〜 |

### クラウド

| ツール | 用途 | Phase |
|--------|------|-------|
| Isaac Sim 5.1（RunPod A10G） | デジタルツイン映像生成 | Phase 5 |
| Claude / ChatGPT / Gemini / Grok API | LLM司令官（Hermes Agent経由） | Phase 0.5〜 |

---

## References

- [micro-ROS](https://micro.ros.org/) — 参照日: 2026-05-19
- [Nav2 Documentation](https://docs.nav2.org/) — 参照日: 2026-05-19
- [Nav2 Simple Commander API](https://docs.nav2.org/commander_api/index.html) — 参照日: 2026-05-21（※実装では Warehouse MCP Server に置き換え、`15-mcp-platform.md` 参照）
- [Hermes Agent — GitHub](https://github.com/NousResearch/hermes-agent) — 参照日: 2026-05-23
- [SLAM Toolbox — GitHub](https://github.com/SteveMacenski/slam_toolbox) — 参照日: 2026-05-19
- [Isaac ROS Getting Started](https://nvidia-isaac-ros.github.io/getting_started/index.html) — 参照日: 2026-05-19
- [FoundationPose — Isaac ROS](https://nvidia-isaac-ros.github.io/concepts/pose_estimation/foundationpose/index.html) — 参照日: 2026-05-19
- [Anthropic API Documentation](https://docs.anthropic.com/) — 参照日: 2026-05-21
- [OpenAI API Reference](https://platform.openai.com/docs/api-reference) — 参照日: 2026-05-21
- [Google AI Gemini API](https://ai.google.dev/gemini-api/docs) — 参照日: 2026-05-21
- [Gazebo Harmonic — gazebosim.org](https://gazebosim.org/docs/harmonic/) — 参照日: 2026-05-21
- [tiryoh/ros2-desktop-vnc — Docker Hub](https://hub.docker.com/r/tiryoh/ros2-desktop-vnc) — 参照日: 2026-05-21
- [Foxglove](https://foxglove.dev/) — 参照日: 2026-05-21
- [rclpy — ROS 2 Python Client Library](https://docs.ros2.org/latest/api/rclpy/) — 参照日: 2026-05-21
