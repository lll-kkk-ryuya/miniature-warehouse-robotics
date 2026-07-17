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
| 14 | Claude API（Opus）の応答速度（JSON構造化出力時） | 高 | Phase 0.5 で実測 |
| 15 | ChatGPT API（GPT-4o）の応答速度・JSON安定性 | 高 | Phase 4 で実測 |
| 16 | Gemini API（2.5 Flash）の応答速度・JSON安定性 | 中 | Phase 4 で実測 |
| 17 | LLMの判断遅延がロボット走行に与える影響（3秒間隔で十分か） | 高 | Phase 0.5 Gazeboで検証 |
| 18 | テザリング環境でのLLM API + micro-ROS同時通信の安定性 | 中 | Phase 3 で実測 |

### シミュレーション環境

| # | 事項 | 影響度 | 確認方法 |
|---|------|--------|---------|
| 19 | ~~Docker + ROS 2 Jazzy + Gazebo Harmonic の Mac M4 での動作~~ → **成立確認（2026-05-30, #43）** | 高（開発環境の基盤） | 環境スパイク **GO**。gz-sim8 8.11 headless + gpu_lidar(ogre2/software GL) + ros_gz_bridge、`--memory=6g` OOM なし。`warehouse_sim/spike/RESULT.md` |
| 20 | ~~tiryoh/ros2-desktop-vnc:jazzy イメージの ARM64 動作確認~~ → **動作確認済（2026-05-30, #43）** | 高 | ARM64 で gz sim + ros_gz_bridge 動作。スパイクは**単一 bot**（`/bot1/{scan,odom,cmd_vel}`）の gz sim+bridge 動作のみ確認（`warehouse_sim/spike/RESULT.md`。colcon build は CI/PR ゲートで担保＝スパイク成果物ではない）。実 `sim.launch.py` の bot1/bot2 spawn は launch/単体テストレベルで、Gazebo E2E は未実施（#8） |

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
| T5 | micro-ROS Agent の多重化 | **中** | 1つのmicro-ROS Agent（Jetson上）が2台のESP32クライアントを同時処理できるか。**識別は UDP ポートではなく XRCE `client_key`（session）**で行われる（R-37 で確認）。**host spike で distinct client_key なら単一Agentで2台双方向OKを実証**（[firmware/spike/RESULT.md](../../firmware/spike/RESULT.md)）。Phase 1で1台、Phase 2後半で2台を実機接続して検証 |

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
| T11 | コスト見積もりの精度 | **中** | 暫定見積もり（Phase 0.5 で実測予定）: Mode A ~$9.0/デモ（Opus、約200回、~2100 tokens/call）、Mode C ~$5.4/デモ（約120回）。tokens/call が gen_id + start_negotiation + situation 拡張で約2100 tokens に増加（2026-05-28 更新）。全 Claude Opus 統一で Sonnet 単価から約5倍に上方修正（2026-05-29、16-repository-and-conventions.md §7）。Phase 4 比較検証本番では4社合計 Mode A ~$12、Mode C ~$7 を見込む。Emergency Guardian（50ms、LLM非経由）は安全担当のため、LLM呼出し回数は変動しない |
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
| R-13 | Open-RMF + Fleet Adapter（EasyFullControl 自作） + Nav2 の統合チェーン（free_fleet/zenoh 不採用＝R-44 / 11c §3.5） | Phase 3 後半 |
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
| R-11 | /tmp/warehouse/state.json の tmpfs 設定 |
| R-19 | Jetson Orin Nano Super の在庫リスク |
| R-23 | predicted_position_3s CTRV 外挿の限界（壁/ゴール停止を無視。旋回は angular で考慮） |
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

## 技術監査で発見した設計リスク（2026-05-29 追加）

設計ドキュメントの深掘り技術監査（並列レビュー＋Web事実確認）で発見した、**実装時に破綻・危険となりうる設計レベルのリスク**。ドキュメント間の表記矛盾ではなく、工学的に「動かない/危ない」可能性のある項目。R-02/R-05/R-13/R-15 等の既存項目を具体化・格上げするものを含む。

### 🔴 高（設計の前提に関わる）

| ID | リスク | 詳細 | 対策 | 検証 |
|----|--------|------|------|------|
| R-35 ✅ | **排他制御の二重防御が両方とも穴（A/B とも解決済）** | (A)HTTPキャンセルはHermesの**サーバー側tool実行を止めない**懸念だったが、採用トランスポートは **Bridge 仲介の in-process dispatch**（doc08 採用実装📌）でサーバー側 tool 実行が無く、止めるべき run が存在しない（`/v1/runs/{id}/stop` 不要）。(B)gen_id検証が `gen_id < cur_gen` のみで**同一世代の重複呼出し**を弾けない（冪等性なし）→ C 層で解決。 | **(A) #54 解決**＝明示 `/stop` 撤回、Layer A は client-side cancel（`wait_for`）のみ・主担保 B-3+C（実測スパイクは in-process 経路では不要化＝サーバー側 run 無し。Hermes ネイティブ採用時のみ再検証, doc08:179）。**(B) #41 解決**＝各 tool call に 1回限り idempotency key(UUID) を付与し MCP が消費記録（C 層）。 | ✅ (A)#54 /(B)#41（unit `test_stale_call_rejected_when_stop_noop_54` 他, R-26）|
| R-36 | **Hermes Memory/Skills 有効化が Phase 4 比較を破壊** | 自己学習エージェントは同一プロンプトでも応答が変動 → 4社LLM公平比較の**再現性が崩壊**。`13-hermes-setup.md` の Hermes 既定が `memory.memory_enabled:true`/`user_profile_enabled:true`（＋`skills`/`session_search` toolset） | 比較時は **Hermes config で `memory.memory_enabled:false`＋`user_profile_enabled:false`＋`skills`/`session_search` toolset 除外** を必須化（運用契約は [08-llm-bridge-common.md](../architecture/08-llm-bridge-common.md) §比較の公平性 で固定・Bridge 起動時 intent assert＝#103。旧 `memory.enabled` 表記は実在しないキーで #103 修正済）。Hermes採用の費用対効果を案D(LiteLLM等)と再評価 | Phase 4前 |
| R-37 | **micro-ROS Agent 2台同時接続の既知不具合**（T5/R-05格上げ） | 上流 micro-ROS-Agent **Issue #21**（2019起票・**2022 無修正クローズ**・**STM32/NuttX**, ESP32ではない）: 1Agentに複数ボードをUDP接続すると**pub/subの片方しか通らない**／別ポート(Case2)でも解消せず。**根本原因＝XRCE `client_key`（Agent側のsession識別子）衝突**（ホスト既定キーは弱RNG: rmw_microxrcedds `rmw_init.c:114-118` `srand(uxr_nanos()); client_key=rand()`）。**host spike で強制再現＋対策実証済**（同一キーで Agent が "session re-established"→片方向喪失 / distinct キーで2 session独立。[firmware/spike/RESULT.md](../../firmware/spike/RESULT.md)）。**「2台協調」の根幹リスク**（従来「中」→**高**） | **第一対策＝両ESP32に distinct `client_key`（`rmw_uros_options_set_client_key()`、BOT_ID/MAC由来）→ 単一Agent(:8888)で2台双方向OK（spike fixA 実証）**。不可なら**USB有線**(#21 Case5)。**2 Agent/別ポートは降格**（#21 Case2/#62 で別問題・不要）。Phase2後半→**前倒し検証(host)済** | Phase 1（実機WiFi＋ファームのkey差確認でクローズ） |
| R-38 | **Open-RMFが8GB Jetsonに載らない恐れ**（R-02具体化） | fleet adapter/traffic schedule の**メモリ漸増（解放されない）既知問題**（公式Discourse）。主方針 Mode C が物理的に不成立のリスク | planner cache上限/`malloc_trim` を最初から導入。載らなければ **Mode B 格下げ or Open-RMF別マシンoffload** の分岐を用意（Go/No-Goゲート化） | Phase 0.5 メモリゲート → **段階1 実走済(2026-06-13)=GO-leaning(条件付き)**: `--memory=6g` 全スタック+常駐Hermes で **OOM無**(oom_kill=0)∧**working-set残RAM≈3GiB≥500MB**∧Hermes計上∧stack live＝GO4条件充足→**#180 解錠**。harness 生 headroom(−1MB)は page-cache 算入アーティファクト＝採用せず(working-set 基準が `free -h` available 準拠＝正。`spike/memory-gate/RESULT.md`/PR#257)。**最終確定=段階2 実機Jetson `free -h`+Open-RMF実プロセス実測(漸増)** |

### 🟡 中

| ID | リスク | 詳細 | 対策 | 検証 |
|----|--------|------|------|------|
| R-39 | **Emergency Guardian 距離監視がAMCL律速** | 50ms周期でも入力 `/amcl_pose` はAMCL更新(5-10Hz)＝**実効100-200ms古い**。「50ms反射安全系」は過大表現 | 近接の物理反射を **`nav2_collision_monitor`**（`/scan`+`/bot{n}/virtual_scan` の polygon stop/approach・`source_timeout` で stale 停止）へ委譲し Guardian は policy 層へ縮退。blocked は Nav2 `progress_checker` へ委譲。最終保証は **ESP32近接(Layer0)**。採用是非は **#126**（PoC=#67 でゲート） | Phase 2 |
| R-40 | **rclpy 50ms周期がGC/GILで破綻しうる** | PythonのGCスパイクで周期が散発的に飛ぶ（実測報告あり）＝最悪応答時間が有界でない | Guardianで `gc.disable()`/`gc.freeze()`。物理反射は **C++(`nav2_collision_monitor`)+ESP32(Layer0)** へ委譲し、締切の無い policy 層のみ Python に残す（最悪応答時間が要る hot path を Python から外す）＝**#126** | Phase 0.5(Jetson) |
| R-41 | **MS200測距精度がミニチュアに不足の恐れ** | 安価2D dToF誤差±1-3cmが地図解像度1cm・通路片側余裕25mmと同オーダー → AMCL収束困難・壁を数セルぶれて認識 | Phase2で**測距誤差を実測**し地図解像度を誤差の2-3倍へ。余裕不足なら通路幅を再設計 | Phase 2 |
| R-42 | **200mm通路×150mm車体でinflation余裕ゼロ** | 標準Nav2 inflationでは通行可能幅が中央1点に収束し追従不能。`11a` の `ROBOT_RADIUS=0.1`(直径200mm)は車体150mmと矛盾し通路を塞ぐ | **非標準inflation**(壁直近のみ高コスト)。`ROBOT_RADIUS`を実測75mmへ | Phase 2 |
| R-43 | **LaserScanのmicro-ROS UDP転送(MTU/fragmentation)** | 360°/0.4°=900点×float≒3.6KB/scan、UDP MTU 512B、2台分常時送出。**R-37 host spike(loopback)はこのMTU/フラグメンテーションを未検証**（小型 std_msgs/Int32 のみ。要 Phase 1 実機） | **既定=(b) Reliable QoS + MTU/fragmentation設定を第一手**とし0.4°フル分解能保持。実機でRAM圧迫(T4)・再送レイテンシ・単一フラグメント欠落が保てなければ **fallback=(a)ダウンサンプル(0.4°→1-2°、単一datagram収容∧AMCL収束∧200mm隘路R-41/R-42を通る最粗)→最終手段(c)USB有線**。値はPhase 1実機で導出。決定=[R-43用語](../GLOSSARY.md) / firmware [PHASE1_CHECKLIST D2](../../firmware/PHASE1_CHECKLIST.md) | Phase 1(T3併せ) |
| R-44 | **free_fleetがESP32 micro-ROS構成に不適合** | (主因) free_fleet の zenoh ブリッジは「各ロボットが自前の**非 namespaced** Nav2 を持つ**分散・異種**フリート」前提だが、本構成は中央 Jetson に **namespaced `/bot{n}`** Nav2 を集約＝transport が冗長・前提が逆。(副因) ロボットが ESP32 micro-ROS で free_fleet/zenoh プロセスを載せられない。現行 free_fleet 自体が EasyFullControl 上の実装（旧 client/server は deprecated）。文献根拠と2案の詳細は `docs/mode-c/11c-traffic-mode-c.md` §3.5 | **判断＝No-Go（free_fleet）**。採用＝`rmf_fleet_adapter` **EasyFullControl** で自作 adapter が `/bot{n}` Nav2 を直接駆動（案A・zenoh 無し、Traffic Schedule/Negotiation は不変）。縮退＝Nav2 Bridge REST `:8645`（案B・RMF 交通管理を放棄）。詳細 `11c §3.5` | **文献判断＝済（11c §3.5）**。実装/2台 Open-RMF E2E/Jetson メモリ実測は **R-38 ゲート（:243）通過後の Phase 3 後半・別 Issue ＝ defer** |
| R-45 | **LLM比較がモデル格差混在** | Claude(Opus) / GPT-4o / Gemini 2.5 **Flash** / Grok でフラッグシップ級とFlash級が混在 → 速度・コスト・正確性比較がミスリード | 比較は**同格モデルで揃える**方針を決定 | Phase 4設計時 |
| R-46 | **BLOCKED_TIMEOUTがサイクル長に未連動** | `BLOCKED_TIMEOUT=10秒` は「3サイクル(9秒)余裕」前提だが、p95>2.5sでサイクルが4-5秒に延びると余裕計算が破綻 | `BLOCKED_TIMEOUT` を**サイクル長の関数**(例 max(10, 3×cycle))にしレイテンシ実測後再計算 | Phase 0.5 |
| R-47 | **キャラLLM交渉のターン制ハング/proposal陳腐化** | `/negotiation/turn` 取りこぼしで最大8秒停止。proposalが±2世代(最大10秒前=0.3m/sで3m相当)で陳腐化したまま反映の危険 | turnを **Reliable+TransientLocal QoS** + seq/nonce検証。proposalに状態スナップショット同梱し陳腐化時は破棄 | Phase 3 |
| R-48 | **DDS discovery over WiFi のマルチキャスト問題** | マルチホスト(Isaac/RViz別マシン)時、WiFiがマルチキャストをドロップしdiscovery不成立・断続 | **Discovery Server / ユニキャスト初期ピア**設定、`ROS_DOMAIN_ID`固定 | Phase 5/撮影時 |

### 🟢 低／解決済み

| ID | リスク | 状態 |
|----|--------|------|
| R-49 | **DWB→MPPI「yaml 1行」は不正確** | pluginの型指定は1行だが**パラメータブロック全体の差し替え＋再チューニング**が必要。doc記述に注記推奨。MPPIチューニング工数を独立計上 |
| R-50 | **Policy Gate は速度を強制しない**（責務所在の明確化） | 一部doc記述「Policy Gateで≤0.3m/s強制」は誤り。速度上限は **ESP32(Layer0)+Nav2 param** の責務。安全保証の所在を統一（誤認するとMCU実装漏れに気づけない） |
| R-51 | **Jetson給電は USB-C 不可・DCバレルジャック必須** | ✅ **解決済み(2026-05-29)**。NVIDIA公式「USB-Cはoutput専用」。Super Dev Kitは19V DC電源同梱で追加投資不要。`02-hardware-design.md` 修正済み |
| R-52 | **gen_store の `multiprocessing.Value` はプロセス間共有不可** | ✅ **解決済み**。Bridge↔MCPは別プロセスツリーのため `multiprocessing.Value` 不可。`16-repository-and-conventions.md §6` で **file方式(暫定)** に確定 |

### 技術監査ソース（2026-05-29）

- [micro-ROS-Agent Issue #21（複数ボード単一Agent）](https://github.com/micro-ROS/micro-ROS-Agent/issues/21) — 参照日: 2026-05-29
- [Open-RMF: Memory growth in fleet adapter and traffic schedule](https://discourse.openrobotics.org/t/memory-growth-in-fleet-adapter-and-traffic-schedule-664/44530/1) — 参照日: 2026-05-29
- [Barkhausen Institut: Soft Realtime Performance of Rclpy](https://www.barkhauseninstitut.org/research/lab-1/our-blog/posts/soft-realtime-performance-of-rclpy) — 参照日: 2026-05-29
- [Hermes Agent — API Server (run stop endpoint)](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server) — 参照日: 2026-05-29
- [Jetson Orin Nano Dev Kit Getting Started（19V電源同梱）](https://developer.nvidia.com/embedded/learn/get-started-jetson-orin-nano-devkit) — 参照日: 2026-05-29

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
