# 実装フェーズ

作成日: 2026-05-21
更新日: 2026-05-23
期間: 約3.5ヶ月（15週間）

## フェーズ一覧

| フェーズ | 期間 | 内容 | 成果物 | 依存 |
|---------|------|------|--------|------|
| Phase 0 | 1週間 | 機材調達・環境準備 | 発注完了、Docker+ROS 2環境構築 | — |
| Phase 0.5 | 2週間 | Gazeboシミュレーション開発 | Gazebo上でLLM制御動作 | Phase 0（実機到着待ちと並行） |
| Phase 1 | 2週間 | ロボット1台セットアップ | 1台がROS 2で通信・走行 | Phase 0 |
| Phase 2 | 2週間 | SLAM(MS200) + Nav2(DWB→MPPI) + Multi-Robot Layer | 2台がジオラマ上を自律走行 | Phase 1 |
| Phase 3 | 2週間 | 2台協調 + LLM Bridge + TrafficManager(A/B/C) | LLM+交通管理で2台が動く | Phase 2 + Phase 0.5 |
| Phase 4 | 2週間 | LLM比較 + 交通管理4パターン比較 + WO統合 | 比較データ | Phase 3 |
| Phase 5 | 2週間 | Isaac Sim連携 | デジタルツイン映像 | Phase 4 |
| Phase 6 | 2週間 | 撮影・編集・公開 | YouTube動画完成 | Phase 5 |

※ Phase 0.5 は Phase 1 と並行実行可能（Mac上のシミュレーションのため実機不要）。並行した場合の実質期間は約13週間。

### ガントチャート概要

```
週:  1    2    3    4    5    6    7    8    9   10   11   12   13
     ├─P0─┤
     ├──── Phase 0.5（Mac Gazebo） ────┤
          ├──── Phase 1（実機1台） ────┤
                    ├──── Phase 2（SLAM+Nav2） ────┤
                                   ├──── Phase 3（2台+LLM） ────┤
                                                ├──── Phase 4（比較+WO） ──┤
                                                              ├── Phase 5 ──┤
                                                                        ├── P6 ──┤
```

---

## Phase 0: 機材調達・環境準備（1週間）

### タスク

- [ ] Yahboom MicroROS ESP32 Car × 2 を発注
- [ ] Bambu Lab A1 mini を発注
- [ ] Jetson Orin Nano Super Dev Kit の在庫確認・発注
- [ ] RPLiDAR A1 を発注
- [ ] カメラ・スタンド・LED を発注
- [ ] ホームセンターでベースボード素材を購入
- [ ] Mac に Docker Desktop をインストール
- [ ] Docker 内に ROS 2 Jazzy + Gazebo Harmonic 環境を構築
  - 推奨イメージ: `tiryoh/ros2-desktop-vnc:jazzy`（ARM64対応、VNC付き）
- [x] ~~**micro-ROS の ROS 2 Jazzy 対応状況を確認**~~ → **確認済み（2026-05-22）。Jazzy対応確定**
- [ ] RunPod アカウント登録（クラウドGPU用）
- [ ] Anthropic API キー取得（Claude用）
- [ ] OpenAI API キー取得（ChatGPT用）
- [ ] Google AI API キー取得（Gemini用）
- [ ] xAI API キー取得（Grok用）— モデル名・価格・Hermes Agent互換性の確認を含む
- [x] ~~Google Gemini 2.5 Flash の非推奨化スケジュール確認~~ → **確認済み（2026-05-26）。2026/10/16に非推奨化。後継: gemini-3.5-flash or gemini-3.1-flash-lite。Phase 4まで2.5-flashで問題なし**
- [ ] Hermes Agent インストール確認（`pip install hermes-agent`）
- [ ] Warehouse MCP Server プロトタイプ作成準備
- [ ] Langfuse Cloud アカウント作成 + プロジェクト作成（"miniature-warehouse-robotics"）
- [ ] Langfuse APIキー取得（HERMES_LANGFUSE_PUBLIC_KEY, HERMES_LANGFUSE_SECRET_KEY）
- [ ] Hermes Agent + Langfuse の接続確認（`hermes plugins list` → observability/langfuse: enabled）

### 完了条件

- 全機材の発注完了
- Mac 上で Docker + ROS 2 Jazzy + Gazebo Harmonic が動作する
- **ROS 2 バージョンが確定している** → Jazzy に確定（micro-ROS Jazzy対応確認済み 2026-05-22）
- 各LLM APIが呼び出せることを確認

---

## Phase 0.5: Gazebo シミュレーション開発（2週間、実機到着待ちと並行）

### タスク

- [x] Gazebo用の仮想ジオラマ作成（1.8m×0.9m、棚3つ、通路、バース） — `warehouse_sim`（`layout.py` 単一定数 + `world_generator` SDF 生成、PR #43）
- [x] 仮想minicarモデル（URDF）作成（サイズ・センサー配置） — `warehouse_description/urdf/minicar.urdf.xacro`（凍結フレーム `base_link`/`lidar_link`/`imu_link`、PR #43。寸法は Phase 1 実測で確定）
- [ ] Nav2 設定・チューニング（ミニチュアスケール用）
- [ ] 単体ナビゲーションテスト（ゴール指定→自律走行）
- [ ] 障害物回避テスト
- [ ] 2台同時走行テスト
- [ ] LLM Bridge Node のプロトタイプ開発（Hermes Gateway ベース）
  - Hermes Gateway daemon 起動・メモリ計測
  - ✅ Warehouse MCP Server（7ツール + Policy Gate + gen_id B-3 + per-call idempotency 強制）を偽 store（FileGenStore / FileIdempotencyStore / FileStateStore）に対し実装（PR #35/#41）。escalation/negotiation は stub、Nav2 Bridge / Open-RMF 送信は follow slice
  - ✅ State Cache Node + Emergency Guardian を実装（`warehouse_state` state_cache 100ms + aggregator、`warehouse_safety` emergency_guardian 50ms reflex、偽入力でユニット検証、PR #39）
  - Claude API → Hermes Gateway で Gazebo ロボットを制御するE2Eテスト
  - Hermes Memory / Skills の3秒ループでの動作検証（Skills はタスク割当パターンに限定、交通制御スキルは禁止）
- [ ] **メモリ検証（二段構え）**: Jetson 8GB に全スタックが収まるかを検証する
  - **段階1: Mac Docker近似テスト（Jetson到着前にできる）**
    - Mac M4 と Jetson はどちらも ARM64 のため、Docker のメモリ上限を絞れば擬似再現できる
    - `docker run --memory=6g --memory-swap=6g ...` で全スタックを起動し、OOM Killer で落ちないかを確認
    - 上限を 6GB にする理由: Jetson 8GB は JetPack(OS+CUDA+デスクトップ)が起動直後に約2〜2.5GB消費するため、アプリに使えるのは実質5.5〜6GB程度
    - これは早期スモークテスト（早期警告）。ここで落ちる設計は実機でも確実に落ちる
  - **段階2: Jetson実機での確定値（Jetson到着後）**
    - 全プロセス同時起動で `free -h` を30秒間隔×10分間記録
    - Nav2×2 + AMCL×2 + SLAM + micro-ROS Agent + Hermes Gateway + Warehouse MCP Server + State Cache + Emergency Guardian
    - 残りRAMが500MB未満の場合、Open-RMF導入の可否を再検討
  - **なぜ段階2が必須か（Mac近似では出ない理由）**
    - ユニファイドメモリ: Jetson Orin Nano は CPU と GPU が 8GB を共有する。GPU処理（GPU版コストマップ/Isaac連携等）が走ると同じ8GBを食い合う。Mac のメモリ上限指定では再現できない
    - JetPack のオーバーヘッド: OS+CUDA+デスクトップの実消費は実機でしか正確に測れない
  - **節約策**: ヘッドレス起動（デスクトップGUIを無効化）で約0.5〜1GB浮く。RAMが厳しい場合に検討
- [ ] Claude API応答時間の実測: 約120回呼出しのp50/p95/p99レイテンシを記録
  - p95 > 2.5秒の場合、サイクル間隔を4-5秒に変更

### 完了条件

- Gazebo上で2台のロボットがNav2で自律走行できる
- LLM Bridge Node（Hermes Gateway + Warehouse MCP Server）が Gazebo上で動作する
- Hermes の Provider 切替で Claude → GPT が1行で切り替えられることを確認

> **進捗（2026-05-30, PR #43）**: 環境スパイク **GO**（doc16 §10）。環境成立（headless `gz sim` + `gpu_lidar`/ogre2 software GL + `ros_gz_bridge`）は **単一 bot（汎用ボックスモデル, `/bot1/{scan,odom,cmd_vel}`）の spike で確認**、`docker run --memory=6g` で OOM なし（メモリ検証 段階1 の sim サブセット）。本実装の `warehouse_sim` / `warehouse_description`（1.8×0.9 world 単一定数生成 + minicar URDF 凍結フレーム `bot{n}/base_link→{lidar_link,imu_link}` + `sim.launch.py` の bot1/bot2 spawn）は **単体テスト（`tests/unit/test_sim_*`）で text レベル検証**済。**実 bot1/bot2 の Gazebo E2E は未実施**（Nav2 E2E と併せ #8）。**残り**: Nav2 自律走行（#8 nav-traffic）／LLM Bridge E2E／Provider 切替／メモリ検証 段階2（Jetson 実測）。

### このフェーズの重要性

**実機到着前にソフトウェアの95%の問題を潰す。** シミュレーションで動いたコードはほぼそのまま実機で動く（ROS 2トピックが同じため）。

---

## Phase 1: ロボット1台セットアップ（2週間）

### タスク

- [ ] Yahboom MicroROS Car 1台を組み立て・動作確認
- [ ] **ロボットの実寸を計測**（幅・長さ・高さ）→ 通路幅を最終決定
- [ ] micro-ROS Agent を Jetson にインストール
- [ ] WiFi UDP でロボット ↔ Jetson の通信確認（テザリングでも可）
- [ ] `/cmd_vel` でロボットを遠隔操作（teleop）
- [ ] `/odom` でオドメトリデータ受信確認
- [ ] RViz2 でロボット位置を可視化
- [ ] ESP32のメモリ・CPU使用率を確認（MS200 + micro-ROS同時動作、課題T4）
- [ ] ベースボード塗装・通路テープ貼り
- [ ] **`gen_store` 実装方式の選定**（file / multiprocessing.Value / Redis のいずれか）— `15-mcp-platform.md §2` 参照
- [ ] **Hermes Gateway での gen_id required 引数の動作検証**（B-3 方式のスモークテスト）

### 完了条件

- Jetson から1台のロボットをROS 2で遠隔操作できる
- RViz2 にロボットの位置が表示される
- gen_store の読み書きが Bridge / MCP 間で動作確認できる

### リスク

- micro-ROS の Jazzy 対応が不完全な場合 → Humble にフォールバック
- WiFi遅延が大きい場合 → USB有線接続を検討

---

## Phase 2: SLAM + Nav2 自律走行（2週間）

### タスク

#### Phase 2 前半（SLAM + DWB）
- [ ] RPLiDAR A1 を Jetson に接続（外部トラッキング補正用、SLAMには使わない）
- [ ] minicar搭載ORBBEC MS200 LiDARでteleop走行し、SLAM Toolboxで2D地図を生成
- [ ] Nav2 をセットアップ（ローカルコントローラー: DWB）
- [ ] コストマップのチューニング（セルサイズ0.01〜0.02m）
- [ ] ロボットの footprint 設定（実測値ベース）
- [ ] ゴール指定 → 自律走行テスト
- [ ] 障害物回避テスト（MS200 LiDAR）
- [ ] 3Dプリントで棚・パレットを製作、ジオラマに配置

#### Phase 2 後半（MPPI + 2台目ハードウェアセットアップ）
- [ ] ローカルコントローラーをDWB → MPPIに切り替え（yaml 1行変更）
- [ ] MPPIパラメータチューニング（狭い通路200mmに最適化）
- [ ] 2台目のロボットをハードウェアセットアップ（組み立て・MS200動作確認・AMCL単体テスト）
- [ ] micro-ROS Agent が2台同時接続できることを検証（課題T5）
- [ ] WiFi帯域・UDP安定性の実測（課題T3）
- [ ] AMCL が相手ロボットを誤認しないか検証（課題T2）
- [ ] Multi-Robot Costmap Layer 実装（相手ロボットをコストマップに注入）— モードA/B での衝突回避に必須。モードC (Open-RMF) 導入が遅れる場合でも本Phaseで完了させること
- [ ] 2台同時走行での衝突回避テスト

### 完了条件

- ロボットがジオラマ上で指定位置まで自律走行できる
- MPPIコントローラーで狭い通路を通過できる
- 2台が互いを認識して衝突回避できる
- 棚の間の通路を通過できる

---

## Phase 3: 2台協調 + LLM Bridge Node + TrafficManager（2週間）

### タスク

#### Phase 3 前半（LLM Bridge + TrafficManager基盤）
- [ ] ROS 2 の namespace 分離（`/bot1/`, `/bot2/`）
- [ ] 2台同時のmicro-ROS通信テスト
- [ ] 2台同時のNav2走行テスト
- [ ] **TrafficManager インターフェース実装**（`mode-a/11a-traffic-mode-a.md` で定義済み）
  - NoTrafficManager（モードA: Claude単独）
  - SimpleTrafficManager（モードB: 通路排他制御）
  - config.yaml での切り替え機構
  - ※ `mode-a/11a-traffic-mode-a.md` ではPhase 2での実装を推奨しているが、Phase 2では2台同時走行の基盤確立を優先し、TrafficManager統合はPhase 3で実施
- [ ] **LLM Bridge Node の実機統合**
  - Gazebo版をベースに実機用に調整
  - Claude APIとの接続確認
  - 状態収集→JSON変換（trafficセクション含む）→API呼出→指示実行
  - Emergency Guardian（50ms周期安全監視、LLM非経由）との統合
  - Policy Gate（Warehouse MCP Server内コマンド検証）との統合
- [ ] シナリオ1テスト: 通常搬送（2台同時、モードA）
- [ ] シナリオ2テスト: 障害物出現→LLM判断→迂回
- [ ] シナリオ3テスト: デッドロック発生→Claude解消
- [ ] シナリオ4テスト: モードB（SimpleTrafficManager）での動作確認
- [ ] **キャラLLM 実況モード実装**（Opus、`/character/speech` publish、画面表示連動）— `14-character-llm-negotiation.md` 参照
- [ ] **キャラLLM の出力先決定**（OBS overlay / Web UI / ターミナル）+ TTS の採否判断
- [ ] **交渉モード Mode A 実装**: 司令官のデッドロック検出 → `/negotiation/start` (starter指定) → Bot1/Bot2 バトンパス交渉 → proposal → 司令官承認 → MCP実行
- [ ] **交渉のターン制（`/negotiation/turn` バトンパス）実装**— 14-character-llm-negotiation.md 参照
- [ ] **交渉連続失敗の保護**: タイムアウト後 N 回連続失敗で交渉モード一時停止（司令官独自判断にフォールバック）
- [ ] **Emergency Guardian → /negotiation/abort 連動**実装（Emergency 時に交渉即中断）
- [ ] gen_id (B-3: MCP tool schema の required 引数) を MCP Server に実装（`15-mcp-platform.md §2`）
- [ ] twist_mux 導入（Emergency=100, Nav2=10）— `15-mcp-platform.md §1. cmd_vel 多重 publisher`
- [ ] active_tasks Lock / Policy Gate atomic 実装

#### Phase 3 後半（Open-RMF導入 — 主方針）
- [ ] RMFTrafficManager 実装（モードC）
- [ ] free_fleet ベースの Fleet Adapter 作成
- [ ] Navigation Graph 定義（通路2-3本）
- [ ] Claude + Open-RMF の統合テスト
- [ ] Open-RMF Dashboard（rmf-web）の起動・接続確認

### 完了条件

- Claudeの指示で2台のロボットが協調動作する
- **モードC（Open-RMF）が動作し、交通管理はOpen-RMFが自動処理する**
- モードA/B/Cの切り替えがconfig.yaml 1行で可能
- Open-RMF Dashboard でロボット位置・タスク状態がリアルタイム表示される
- デッドロック検出→Open-RMF解消→失敗時Claude介入が動作する
- WiFi経由での2台同時制御が安定している

### リスク

- 2台同時のWiFi通信で遅延が発生する場合 → チャンネル分離、通信頻度調整
- Claude API遅延が大きい場合 → Mode A の待機時間を1秒→2秒に拡大（サイクル長 3秒→4秒）
- Open-RMF導入が間に合わない場合 → モードA/Bで一時的に撮影し、Open-RMF完成後に再撮影

---

## Phase 4: LLM比較検証 + WO統合 + 交通管理比較（2週間）

### タスク

#### LLM比較検証
- [ ] Hermes Agent で ChatGPT Provider の接続確認
- [ ] Hermes Agent で Gemini Provider の接続確認
- [ ] Hermes Agent で Grok（xAI）Provider の接続確認
- [ ] **比較公平性ガード**: 比較 run は Hermes Memory/Skills OFF を起動時に assert（`memory.enabled:false`/`skills.enabled:false`、[08-llm-bridge-common.md](../architecture/08-llm-bridge-common.md) §比較の公平性 / R-36 / #103）
- [ ] **LLM比較シナリオの実行**（4社LLMで同一シナリオ）
  - シナリオ1: 通常搬送（障害物なし）
  - シナリオ2: 障害物出現→迂回判断
  - シナリオ3: 2台が同じ通路に向かう→衝突回避
  - シナリオ4: タスク3つ同時発生→優先順位判断
  - シナリオ5: バッテリー低下→充電 vs タスク続行

#### 交通管理4パターン比較（config.yaml切り替え）
- [ ] パターン1: Nav2のみ（TrafficManager無効、Claude無効）→ デッドロック頻発
- [ ] パターン2: Claude単独（モードA: none）→ 柔軟だが3秒遅延
- [ ] パターン3: Claude + 自作ルール（モードB: simple）→ 即時排他制御
- [ ] パターン4: Claude + Open-RMF（モードC: open-rmf）→ フル交通管理（Phase 3で実装済みの場合）

#### キャラLLM Mode C 拡張
- [ ] **交渉モード Mode C 実装**: Open-RMF エスカレーション時のみ交渉発動
- [ ] キャラLLMはPhase 4比較対象外（演出専用、`14-character-llm-negotiation.md` 参照）

#### WO統合・可視化
- [ ] 各LLMの判断ログ記録・分析（応答速度、正確性、タスク完了時間、効率性=総移動距離、エラー率、コスト。指標定義は [doc08 §比較指標](08-llm-bridge-common.md#比較指標)）
- [ ] WO Bridge Node の実装（REST API ↔ ROS 2トピック）
- [ ] WO画面にロボット位置のリアルタイム表示
- [ ] KPI計算・表示
- [ ] 荷物トレイの3Dプリント・取り付け

### 完了条件

- 4つのLLM（Claude / ChatGPT / Gemini / Grok）で同じシナリオを実行完了
- **交通モード軸**: 交通管理の2〜4パターン（none/simple/open-rmf）で比較データが揃っている（**デッドロック頻度 `deadlock`（#55 land 後に確定）・衝突回避 `collision_free`・リプラン `replans`・タスク完了時間 `task_completion_time`**。指標定義は [doc08 §比較計測の追加設計](08-llm-bridge-common.md) に集約。`deadlock`/`collision_free`/`replans` は**交通モード軸の指標**であり、LLM 公平性比較軸（下行）とは別軸）
- **LLM比較軸**: 4つのLLM間の比較データが揃っている（応答速度・判断の正確性・タスク完了時間・効率性=総移動距離・エラー率・コスト。指標の定義と取得方法は [doc08 §比較指標](08-llm-bridge-common.md#比較指標)）
- WO画面からミッション状況が可視化される

---

## Phase 5: Isaac Sim連携・デジタルツイン（2週間）

### タスク

- [ ] RunPod A10G でIsaac Sim 5.1 環境構築
- [ ] ジオラマと同じレイアウトの3Dシーンを構築
- [ ] ロボットの3Dモデル配置
- [ ] ROS 2 Bridge で位置データを同期（リアルタイム or ログ再生）
- [ ] デジタルツイン映像の録画
- [ ] 実機映像 ↔ CG映像の並列表示テスト
- [ ] FoundationPose の試行（荷物認識、余裕があれば）

### 完了条件

- Isaac Sim 上でロボットが実機と同じ動きをする映像が撮れる
- 実機映像とCG映像を並列で表示できる

---

## Phase 6: 撮影・編集・公開（2週間）

### タスク

- [ ] ナレーション原稿の作成
- [ ] Before映像の撮影（AIなし、渋滞発生）
- [ ] Claude編の撮影（障害物投入、リアルタイム判断）
- [ ] ChatGPT編の撮影（同じシナリオ）
- [ ] LLM思考ログの画面キャプチャ
- [ ] WO画面のキャプチャ
- [ ] Isaac Sim映像のキャプチャ
- [ ] 比較結果グラフの作成
- [ ] 動画編集（DaVinci Resolve / Premiere Pro）
- [ ] サムネイル作成
- [ ] 公開安全チェック（合成データのみ、顧客情報なし、APIキーの映り込み防止）
- [ ] YouTube アップロード・公開
- [ ] 概要欄にCTA記載

### 完了条件

- YouTube に動画が公開されている
- 営業で送付可能なURLがある

---

## マイルストーン

| 時期 | マイルストーン |
|------|--------------|
| Phase 0 完了 | 「環境が整った」 |
| Phase 0.5 完了 | 「シミュレーションで動いた」← Macだけで到達可能 |
| Phase 1 完了 | 「実機1台が動いた」← 最初の感動ポイント |
| Phase 2 完了 | 「自律走行できた」 |
| Phase 3 完了 | 「AIが2台を指揮できた」← 技術的な核心 |
| Phase 4 完了 | 「4社LLM比較ができた」← コンテンツの核心 |
| Phase 5 完了 | 「デジタルツインができた」 |
| Phase 6 完了 | 「映像が公開された」← 営業開始 |

---

## References

- [Hermes Agent — GitHub](https://github.com/NousResearch/hermes-agent) — 参照日: 2026-05-23
- [Yahboom ESP32 MicroROS Robot Car — 公式](https://category.yahboom.net/products/microros-esp32) — 参照日: 2026-05-19
- [tiryoh/ros2-desktop-vnc — Docker Hub](https://hub.docker.com/r/tiryoh/ros2-desktop-vnc) — 参照日: 2026-05-21
- [Nav2 Documentation](https://docs.nav2.org/) — 参照日: 2026-05-19
- [SLAM Toolbox — GitHub](https://github.com/SteveMacenski/slam_toolbox) — 参照日: 2026-05-19
