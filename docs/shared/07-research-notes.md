# 調査メモ・未検証事項

作成日: 2026-05-21
更新日: 2026-05-26

## 未検証事項一覧

### ハードウェア

| # | 事項 | 影響度 | 確認方法 |
|---|------|--------|---------|
| 1 | Yahboom MicroROS Car の実寸（幅・長さ・高さ） | 高（通路幅設計に直結） | 実機到着後に実測 |
| 2 | Yahboom 2台を同一WiFiで同時制御した際の遅延・安定性 | 高 | Phase 2後半 で実測（課題T5と同時） |
| 3 | Jetson Orin Nano Super の在庫状況（スイッチサイエンス） | 中 | 発注前に確認 |
| 4 | RPLiDAR A1 の1,820×910mm環境での測定精度 | 中 | Phase 2 で実測 |
| 5 | Bambu Lab A1 mini の現在の日本公式価格 | 低 | Amazon で確認 |

### ソフトウェア

| # | 事項 | 影響度 | 確認方法 |
|---|------|--------|---------|
| 6 | ~~micro-ROS の ROS 2 Jazzy 公式対応状況~~ | ~~**最高**~~ → **確認済み（2026-05-22）** | micro_ros_setup GitHubにJazzyブランチ存在。Jazzy対応確定。Humbleフォールバックは保険として残す |
| 7 | Jetson Orin Nano Super での Isaac ROS release-3.x の動作 | 中 | Phase 5 で確認 |
| 8 | FoundationPose の Jetson Orin Nano Super 上での推論速度 | 低（Phase 5+ の拡張） | 実機テスト |
| 9 | RunPod A10G での Isaac Sim 5.1 の動作確認事例 | 中 | Phase 5 で確認 |

### ジオラマ

| # | 事項 | 影響度 | 確認方法 |
|---|------|--------|---------|
| 10 | PLA フィラメント1巻あたりの倉庫パーツ印刷枚数 | 低 | Phase 1 でテスト印刷 |
| 11 | テクスチャーペイント上での小型ロボット走行安定性 | 中 | Phase 1 で実測 |
| 12 | C922n（画角78度）で1,820×910mmを俯瞰する最低設置高さ | 中 | Phase 1 で実測 |

---

### ROS 2 バージョン

| # | 事項 | 影響度 | 確認方法 |
|---|------|--------|---------|
| 13 | ROS 2 Jazzy が LTS かどうか | 高（サポート期間に影響） | docs.ros.org で一次確認 |

### LLM連携

| # | 事項 | 影響度 | 確認方法 |
|---|------|--------|---------|
| 14 | Claude API（Sonnet）の応答速度（JSON構造化出力時） | 高 | Phase 0.5 で実測 |
| 15 | ChatGPT API（GPT-4o）の応答速度・JSON安定性 | 高 | Phase 4 で実測 |
| 16 | Gemini API（2.5 Flash）の応答速度・JSON安定性 | 中 | Phase 4 で実測 |
| 17 | LLMの判断遅延がロボット走行に与える影響（3秒間隔で十分か） | 高 | Phase 0.5 Gazeboで検証 |
| 18 | テザリング環境でのLLM API + micro-ROS同時通信の安定性 | 中 | Phase 3 で実測 |

### シミュレーション環境

| # | 事項 | 影響度 | 確認方法 |
|---|------|--------|---------|
| 19 | Docker + ROS 2 Jazzy + Gazebo Harmonic の Mac M4 での動作 | 高（開発環境の基盤） | Phase 0 で確認 |
| 20 | tiryoh/ros2-desktop-vnc:jazzy イメージの ARM64 動作確認 | 高 | Phase 0 で確認 |

---

## 技術課題（2026-05-22 追加）

設計レビューで発見された未検討の技術課題。設計の矛盾ではなく、実装時に検証・対処が必要な項目。

### Phase 0 で解決必須

| # | 課題 | 影響度 | 詳細 |
|---|------|--------|------|
| T1 | ~~micro-ROS の Jazzy 対応確認~~ | ~~**最高**~~ → **解決済み（2026-05-22）** | micro_ros_setup GitHubにJazzyブランチ確認。Jazzyで確定。Phase 0での最優先確認は不要 |
| T2 | AMCL が相手ロボットを動的障害物として誤認する問題 | **高** | AMCLはLiDARスキャンが静的地図と一致する前提。Bot1のMS200がBot2をスキャンすると、地図にない障害物として認識し、位置推定の信頼度が低下・ドリフトする可能性がある。Phase 2で実機検証が必要。対策候補: (1) AMCLのパーティクル数増加、(2) scan filterで相手ロボットのサイズの物体を除外、(3) Multi-Robot Costmap Layerで相手位置をAMCLにも通知 |

### Phase 1 で検証必須

| # | 課題 | 影響度 | 詳細 |
|---|------|--------|------|
| T3 | WiFi帯域と通信安定性 | **高** | 2台同時通信量: MS200 LiDAR(10-15Hz)×2 + odom(20-50Hz)×2 + cmd_vel(20Hz)×2 ≒ 30-40KB/s。帯域自体は余裕だが、UDP パケットロス・ジッター・テザリング遅延の実測が必要。Emergency Guardian(50ms周期) + State Cache(100ms周期) + LLM API(Mode A:3秒/Mode C:5秒)同時通信時の安定性も確認 |
| T4 | ESP32 のメモリ・CPU制約 | **中** | ESP32 SRAM 520KB中、FreeRTOS+WiFi(~150KB) + micro-ROS(~100KB) + MS200バッファ(~20KB) + モーター制御(~10KB) = ~280KB使用、残り~240KB。Yahboom公式がMS200+micro-ROSで動作確認済みなら問題ないはず。Phase 1で1台の実測で確認 |
| T5 | micro-ROS Agent の多重化 | **中** | 1つのmicro-ROS Agent（Jetson上）が2台のESP32クライアントを同時処理できるか。仕様上は可能（UDPポートで識別）だが、2台同時の実測が必要。Phase 1で1台、Phase 2後半で2台接続して検証 |

### Phase 2-3 で設計詳細化が必要

| # | 課題 | 影響度 | 詳細 |
|---|------|--------|------|
| T6 | TFツリー構造の文書化 | **中** | 2台構成のTFツリーが未文書化。あるべき構造: `map(共有)→bot1/odom→bot1/base_link→bot1/lidar_link` と `map→bot2/odom→bot2/base_link→bot2/lidar_link`。namespace分離と static_transform_publisher の設定をPhase 2で確定・文書化 |
| T7 | Map Server の共有方式 | **中** | 2つのAMCLインスタンスが同じ地図を使う。方式: 1つのMap Serverが `/map` を配信し、両AMCLが購読する（グローバルnamespace）。明示的に文書化・設定が必要 |
| T8 | SimpleTrafficManager の通路ロック解放トリガー | **中** | doc11のコードで `release_aisle()` メソッドは定義済みだが、呼び出しタイミングが未定義。候補: (A) Nav2のゴール到達コールバック、(B) ロボット位置が通路外に出たことをposition監視で検出、(C) タイムアウト。Phase 3実装時に確定 |
| T9 | Phase 2後半とPhase 3前半の「2台目セットアップ」の区分明確化 | **低** | Phase 2後半=ハードウェアセットアップ（組み立て・MS200動作確認・AMCL単体テスト）、Phase 3前半=ソフトウェア統合（namespace分離・2台同時Nav2・TrafficManager）と解釈。doc06に明記すべき |

### Phase 3後半以降で設計が必要

| # | 課題 | 影響度 | 詳細 |
|---|------|--------|------|
| T10 | Navigation Graph と OccupancyGrid の共存 | **中** | Open-RMF（モードC）はNavigation Graph（ノードとエッジ）、Nav2はOccupancyGrid（ピクセル地図）を使用。両方は別レイヤーとして共存: RMFが「どの通路を使うか」（高レベル）を決め、Nav2が「通路内をどう走るか」（低レベル）を決める。Phase 3後半のOpen-RMF導入時に設計詳細化 |

### 軽微（実装時に対処）

| # | 課題 | 影響度 | 詳細 |
|---|------|--------|------|
| T11 | コスト見積もりの精度 | **中** | 暫定見積もり（Phase 0.5 で実測予定）: Mode A ~$1.80/デモ（Sonnet 4、約200回、~2100 tokens/call）、Mode C ~$1.08/デモ（約120回）。tokens/call が gen_id + start_negotiation + situation 拡張で約2100 tokens に増加（2026-05-28 更新）。Phase 4 比較検証本番では4社合計 Mode A ~$5、Mode C ~$3 を見込む。Emergency Guardian（50ms、LLM非経由）は安全担当のため、LLM呼出し回数は変動しない |
| T12 | システムプロンプトのトークン数 | **低** | 推定: ~400-500トークン。状況JSON: ~800-1200トークン。合計~1500-2000トークン/回。3秒間隔に対して応答遅延への影響は数十ms程度で無視可能 |

---

## 調査済み事項

### Isaac Sim の GPU 制約（2026-05-19 確認）

- Isaac Sim は RTコア（レイトレーシング）が必須
- A100 / H100 は RTコア非搭載のため **動作しない**
- 使用可能: A10G, L4, RTX 4090 等の RTX系GPU
- クラウドでは RunPod / Vast.ai の A10G が費用対効果が高い

### Jetson Orin Nano Super（2026-05-19 確認）

- 2024年12月発売。旧モデルの約半額（$249）で性能67 TOPS に向上
- 旧 Dev Kit はスイッチサイエンスで販売終了済み
- Isaac ROS 最新版は Jetson Thor がメインターゲットに移行中

### シミュレーター最新状況（2026-05-21 確認）

- Gazebo Harmonic: LTS、EOL 2028年9月。ROS 2 Jazzy の公式ペア（※Jazzy自体のLTS認定は未確認、doc03参照）
- Gazebo Jetty: LTS、EOL 2030年9月。ROS 2 Rolling向け（Jazzyには非推奨）
- Gazebo Ionic: 非LTS、EOL 2026年9月（使用しない）
- Gazebo Classic（旧版）: 2025年1月に完全EOL
- Webots R2025a: Apple Silicon ネイティブ対応。Nav2連携可能。Macで手軽にテスト可能
- Isaac Sim 5.1: 現在のGA版。6.0は Early Developer Preview
- Isaac Sim がオープンソース化（GitHub公開）
- O3DE 25.05: ROS 2ネイティブ統合。コミュニティ小

開発環境は Docker + Gazebo Harmonic を採用。Webots は補助的に検討。

### NVIDIA モデル適用判断（2026-05-19 確認）

- Gr00t N1: ヒューマノイド専用 → 今回は不使用
- cuMotion: ロボットアーム向け → 今回は不使用
- Cosmos: H100 必要で過剰 → 将来検討
- FoundationPose: 荷物認識に使える → Phase 5+ で検討
- Isaac Sim: デジタルツインに使う → Phase 5 で実装

---

## リスク調査結果（2026-05-26 実施）

プロジェクト全体のリスク分析を実施し、Web調査で検証可能なものを調査した。

### リスク一覧（全34件、深刻度別）

#### 🔴 高リスク（7件）— プロジェクト失敗に直結しうる

| ID | リスク | 調査結果 | 残存リスク | 検証タイミング |
|----|--------|---------|:----------:|--------------|
| R-02 | Jetson 8GB RAM で全プロセスが動くか | 未検証（実機必要） | 🔴 | Phase 0.5 Day 1-2 |
| R-04 | ロボット実寸が未測定（通路幅200mm前提が崩壊する可能性） | Yahboom公式に寸法記載なし。~150mmは推定のまま | 🟡 | Phase 1（実測） |
| R-08 | WiFi が唯一の通信経路（micro-ROS + LLM API 全依存） | 未検証（実機必要） | 🔴 | Phase 1 |
| R-12 | rclpy + FastAPI + asyncio の共存（Nav2 Bridge） | MultiThreadedExecutor分離パターンで設計済み。実装時に検証 | 🟡 | Phase 0.5 Day 3-4 |
| R-16 | 15週間のスケジュールが非現実的（1人開発） | 未検証。Isaac Sim（Phase 5）カットで緩和可能 | 🔴 | Phase 0 で再見積もり |
| R-25 | Fleet Adapter の詳細設計が未着手 | 未着手だが Open-RMF aptパッケージ利用で工数低減見込み | 🟡 | Phase 3 後半 |
| R-26 | テスト戦略の完全な欠如 | 未対応。最低限 Emergency Guardian + Policy Gate のユニットテスト必要 | 🟡 | Phase 0.5 |

#### 調査済み — リスク低下確認

| ID | リスク | 調査前 | 調査結果 | 調査後 |
|----|--------|:------:|---------|:------:|
| R-01 | Hermes Agent ARM64/Jetson対応 | 🔴 | **Pure Python wheel（py3-none-any）、C拡張なし。Docker マルチアーチ対応済み（amd64/arm64明示）。主要バイナリ依存（pydantic-core, psutil, cryptography）は全て aarch64 wheel 提供済み。Jetsonユーザー実在（Issue #11454 — SSL問題はOS層、Hermes固有ではない）。プロジェクト極めて活発（★167K、週1リリース、最終コミット2026-05-26）** | 🟢 |
| R-03 | Open-RMF Jazzy + ARM64 ビルド | 🔴 | **Jazzy向け正式リリース済み（2024年6月）。rosdistroに45+パッケージがBloomリリースされており `apt install ros-jazzy-rmf-*` でバイナリインストール可能（ソースビルド不要）。ARM64バイナリはROS 2 buildfarmで自動ビルド。ARM64固有の問題報告なし。jazzyブランチは2025年12月まで活発にメンテナンス。free_fleetもjazzyブランチあり（2025年8月更新）** | 🟢 |
| R-20 | Hermes Agent への過度な依存 | 🔴 | **プロジェクト極めて活発（★167K、contributor 30+、週1リリース）で放棄リスクは低い。ただし中核機能をすべてHermesに依存する構造は変わらず** | 🟡 |
| R-29 | Gemini 2.5 Flash 非推奨化 | 🟡 | **gemini-2.0-flashは2026/6/1停止（間もなく）だが、gemini-2.5-flashは2026/10/16まで猶予あり（5ヶ月）。後継gemini-3.5-flashは約5倍の価格（$0.30→$1.50/MTok入力）。安価代替としてgemini-3.1-flash-lite（$0.25/$1.50）が利用可能。移行はモデル名1行変更のみ** | 🟢 |

#### 🟡 中リスク（主要14件）

| ID | リスク | 検証タイミング |
|----|--------|--------------|
| R-05 | WiFi テザリング同時通信安定性（micro-ROS x2 + LLM API + Langfuse） | Phase 1-2 |
| R-06 | AMCL が相手ロボットを動的障害物として誤認（T2） | Phase 2 後半 |
| R-07 | LLM API レイテンシ + Hermes オーバーヘッドが3秒サイクルに収まるか | Phase 0.5 |
| R-09 | Jetson Orin Nano が唯一の計算ノード（熱暴走・クラッシュで全機能喪失） | Phase 1 |
| R-10 | Hermes Gateway が SPOF（クラッシュで新規タスク・ログ全停止） | Phase 0.5 |
| R-13 | Open-RMF + free_fleet + zenoh + Nav2 の統合チェーン | Phase 3 後半 |
| R-14 | Hermes MCP stdio通信 と Warehouse MCP Server の安定性 | Phase 0.5 |
| R-15 | Emergency Guardian の cmd_vel と Nav2 Controller の競合（タイミングウィンドウ） | Phase 2 |
| R-17 | Open-RMF の学習曲線（「3-5日」は楽観的） | Phase 3 |
| R-18 | Isaac Sim（Phase 5）の実現可能性（RunPod A10G環境構築 + 3Dシーン + ROS 2 Bridge を2週間） | Phase 5（フォールバック: Isaac Simなしでも動画成立） |
| R-21 | dispatch_task の過積載（action/via/duration、LLM誤用リスク） | Phase 0.5 |
| R-22 | ファイルベース State Cache（I/O遅延、データ鮮度） | Phase 0.5 |
| R-27 | エラーリカバリーフローの一部が未設計 | Phase 3 |
| R-28 | SimpleTrafficManager の release_aisle トリガーが未確定 | Phase 3 |

#### 🟢 低リスク（6件）

| ID | リスク |
|----|--------|
| R-11 | /tmp/warehouse_state.json の tmpfs 設定 |
| R-19 | Jetson Orin Nano Super の在庫リスク |
| R-23 | predicted_position_3s 線形外挿の限界（壁の向こうを予測） |
| R-24 | 3モード維持のテスト工数（3倍） |
| R-30 | Grok API のモデル名・価格が未確定 |
| R-33 | デモ中のハードウェア故障（バッテリー切れ、ESP32ハングアップ） |

#### YouTubeデモリスク（2件）

| ID | リスク | 対策 |
|----|--------|------|
| R-31 | Before/After の差が視覚的に弱い | LLM思考ログのリアルタイム表示、RViz/Foxgloveオーバーレイ |
| R-32 | LLM比較で有意な差が出ない | 複雑シナリオ（同時3タスク、バッテリー低下+障害物）、JSON形式エラー率での差別化 |

### 推奨アクション（Phase 0 開始時の優先順位）

| 優先度 | アクション | 根拠 |
|--------|----------|------|
| **1** | `pip install hermes-agent` のJetson ARM64確認 | R-01は🟢だが実機確認が最終判定。失敗時は案D（2-3週間追加） |
| **2** | Jetson 8GB で Nav2x2 + Hermes 同時起動 → `free -h` 記録 | R-02が最大の🔴。500MB未満なら Open-RMF 断念を即決 |
| **3** | `apt install ros-jazzy-rmf-fleet-adapter` のJetson確認 | R-03は🟢（aptバイナリあり）だが実機最終確認 |
| **4** | スケジュール見直し: 15週→20週、または Isaac Sim カット | R-16対応。Phase 5 を完全オプション化 |
| **5** | Emergency Guardian + Policy Gate のユニットテスト作成 | R-26対応。Phase 0.5 で最低限のテスト基盤 |
| **6** | Yahboom サポートにロボット寸法を問い合わせ | R-04対応。実機到着前に情報取得を試みる |
| **7** | Gemini モデル名を gemini-2.5-flash のまま維持、Phase 4 で再評価 | R-29は🟢。10月まで猶予あり |

### 調査ソース

- [Hermes Agent — GitHub](https://github.com/NousResearch/hermes-agent) — 参照日: 2026-05-26
- [Hermes Agent — PyPI](https://pypi.org/project/hermes-agent/) — 参照日: 2026-05-26
- [Open-RMF rmf — GitHub](https://github.com/open-rmf/rmf) — 参照日: 2026-05-26
- [Open-RMF rmf_ros2 jazzy branch — GitHub](https://github.com/open-rmf/rmf_ros2/tree/jazzy) — 参照日: 2026-05-26
- [Open-RMF free_fleet — GitHub](https://github.com/open-rmf/free_fleet) — 参照日: 2026-05-26
- [Gemini API Deprecations — Google AI](https://ai.google.dev/gemini-api/docs/deprecations) — 参照日: 2026-05-26
- [Gemini API Pricing — Google AI](https://ai.google.dev/gemini-api/docs/pricing) — 参照日: 2026-05-26
- [Yahboom MicroROS ESP32 — 公式](https://category.yahboom.net/products/microros-esp32) — 参照日: 2026-05-26（寸法記載なし）

---

## References

- [Jetson Orin Nano Super Developer Kit — NVIDIA](https://www.nvidia.com/en-us/autonomous-machines/embedded-systems/jetson-orin/nano-super-developer-kit/) — 参照日: 2026-05-19
- [Jetson Orin Nano Super — スイッチサイエンス](https://www.switch-science.com/products/10188) — 参照日: 2026-05-19
- [Jetson Orin Nano Super — 菱洋エレクトロ](https://ryoyo-gpu.jp/product/jetson/orin_nano_super_devkit/) — 参照日: 2026-05-19
- [Isaac Sim Requirements — NVIDIA](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/requirements.html) — 参照日: 2026-05-19
- [Isaac ROS Getting Started](https://nvidia-isaac-ros.github.io/getting_started/index.html) — 参照日: 2026-05-19
- [NVIDIA Isaac GR00T N1 — NVIDIA Newsroom](https://nvidianews.nvidia.com/news/nvidia-isaac-gr00t-n1-open-foundation-model-simulation-frameworks) — 参照日: 2026-05-19
- [FoundationPose — Isaac ROS](https://nvidia-isaac-ros.github.io/concepts/pose_estimation/foundationpose/index.html) — 参照日: 2026-05-19
- [Yahboom ESP32 MicroROS Robot Car — 公式](https://category.yahboom.net/products/microros-esp32) — 参照日: 2026-05-19
- [Bambu Lab A1 mini — Amazon.co.jp](https://www.amazon.co.jp/dp/B0CRYJBKQQ) — 参照日: 2026-05-19
- [micro-ROS](https://micro.ros.org/) — 参照日: 2026-05-19
- [Mini Warehouse — Printables.com](https://www.printables.com/model/561782) — 参照日: 2026-05-19
- [Pallet Rack 1:10 — Printables.com](https://www.printables.com/model/567874) — 参照日: 2026-05-19
