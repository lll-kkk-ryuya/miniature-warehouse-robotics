# リポジトリ構成と実装規約

作成日: 2026-05-29

> **位置づけ**: 本ドキュメントは「最初のコードを書く前に確定すべき規約」をまとめた**実装の起点**。
> リポジトリ構成・パッケージ命名・メッセージ型・共有ファイルパス・gen_store方式・モデル方針・ブランチ戦略を一元定義する。
> 各ノードの**設計内容**は従来通り個別ドキュメント（03/08/12/14/15 等）を参照し、本書は**構造とルール**のみを定める。

> **関連ドキュメント**:
> - [03 - ソフトウェアアーキテクチャ](03-software-architecture.md) — トピック設計・ノード構成
> - [06 - 実装フェーズ](06-implementation-phases.md) — Phase 0-6 計画
> - [08 - LLM Bridge 共通](08-llm-bridge-common.md) — gen_store / 排他制御 / コスト
> - [12 - 共通基盤](12-infrastructure-common.md) — Emergency Guardian / State Cache
> - [15 - MCPプラットフォーム](15-mcp-platform.md) — Warehouse MCP Server / gen_id

---

## 1. リポジトリ構成（モノレポ — `ws/src/` 集約）

**ドメイン固有の colcon パッケージは `ws/src/` 配下の `warehouse_*` に集約する。** シミュレーション資産（URDF/world）も独立トップレベルディレクトリには置かず、ROS 2 パッケージ内に収める。`colcon build` 1コマンドで全体がビルドできる状態を維持する。**唯一の例外が `eval_sdk`**（doc21）＝倉庫に依存しない再利用可能な評価コアであることを名前で示すため**意図的に非 `warehouse_*` 命名**とする（`rclpy`/`warehouse_*` import ゼロ・pip 化可能・`langfuse` は optional pip extra）。`eval_sdk` も `package.xml`/`setup.py` を持つ colcon パッケージであり、§2 一覧・`ws/src/README.md` の正準レジストリに登録する（`scripts/check_consistency.py` の `B5-package-registry` が全 `ws/src/*` を対象に登録漏れを ERROR 検出）。

ESP32 ファームウェア（PlatformIO、MCU向けで colcon 非対象）と、デプロイ/設定資産はリポジトリルートに置く。

```
miniature-warehouse-robotics/
├── ws/                              # ROS 2 colcon ワークスペース
│   └── src/
│       ├── warehouse_interfaces/    # [ament_python] 契約: pydantic schemas/Store IF/paths（Phase4で.msg導入時に ament_cmake へ移行）
│       ├── warehouse_bringup/       # [ament_python] launch + config の単一ソース
│       │     launch/  config/  rviz/
│       ├── warehouse_description/   # [ament_python] URDF/xacro・meshes
│       ├── warehouse_sim/           # [ament_python] Gazebo world・ros_gz_bridge・sim launch
│       ├── warehouse_state/         # [ament_python] State Cache Node（100ms, atomic write）
│       ├── warehouse_safety/        # [ament_python] Emergency Guardian（50ms）+ twist_mux 設定
│       ├── warehouse_traffic/       # [ament_python] TrafficManager IF（None/Simple）+ VirtualScan
│       ├── warehouse_teleop/        # [ament_python] キーボード teleop（動作確認用）
│       ├── warehouse_nav2_bridge/   # [ament_python] Mode A/B: REST → BasicNavigator
│       ├── warehouse_llm_bridge/    # [ament_python] LLM Bridge Node（司令官 + キャラ）
│       ├── warehouse_mcp_server/    # [ament_python] Warehouse MCP Server（Hermes stdio 子）
│       ├── warehouse_orchestrator/  # [ament_python] KPI Collector + 分析スクリプト
│       ├── warehouse_rmf_adapter/   # [ament_python] Mode C 案A EasyFullControl Fleet Adapter（offline core: routing/namespacing/single-writer）
│       └── eval_sdk/                # [ament_python] ドメイン非依存 評価コア（seed/tracer/sink/stats/cost）。意図的に非 warehouse_＝ROS/warehouse 依存ゼロ・pip 化可能（doc21）
├── firmware/                        # ESP32 micro-ROS（PlatformIO、colcon 対象外）
│   ├── platformio.ini
│   └── src/
├── deploy/
│   ├── gcp/                         # 既存: Hermes Gateway VPS
│   ├── jetson/                      # Jetson セットアップ・systemd・監視スクリプト
│   └── hermes/                      # Hermes config.yaml / .env.example
├── config/                          # 倉庫 config（env分離: warehouse.base.yaml + dev|stg|prod/。WAREHOUSE_ENV で選択。doc19）
├── docs/
└── ws/build, ws/install, ws/log     # .gitignore 対象（生成物）
```

**設計上の根拠**:
- ROS 2 ノードと LLM が同一プロセスに同居しない原則（doc12）を、**パッケージ分離＝プロセス分離＝ブランチ分離**として構造に反映する。
- `warehouse_mcp_server` は Hermes Gateway の stdio 子プロセス（`python -m warehouse_mcp_server`）として起動できるが、ソースはモノレポ統一のため `ws/src/` に置く。rclpy には依存せず純 Python + MCP SDK で動く。**ただし S2-PR2 HALF B 以降、commander サイクルの tool dispatch は同一トラック in-process（`warehouse_llm_bridge` が `WarehouseTools().dispatch` を注入。#81 / doc08:167-175）**＝stdio 子プロセス起動は Hermes ネイティブ/外部 MCP client 用の経路で、commander 経路では必須でない。
- `warehouse_sim` の Gazebo world とジオラマ寸法は**単一定数定義**から生成し、Phase 5 の Isaac Sim シーンも同じ寸法定数を参照できるようにする（シミュレータ差し替え時の改修を最小化）。

---

## 2. パッケージ命名・責務一覧

| パッケージ | ビルド | 責務 | 主担当 | Phase |
|---|---|---|---|---|
| `warehouse_interfaces` | ament_python | 契約コード化: pydantic schemas / Store IF / 共有パス（Phase4で .msg 導入時に ament_cmake へ） | ros2/bridge | 0.5 |
| `warehouse_bringup` | ament_python | launch・config 集約（パラメータの単一ソース） | ros2 | 0.5 |
| `warehouse_description` | ament_python | minicar URDF/xacro・meshes | ros2/sim | 0.5 |
| `warehouse_sim` | ament_python | Gazebo world・`ros_gz_bridge`・sim 起動 | sim | 0.5 |
| `warehouse_state` | ament_python | State Cache Node（状態集約JSON） | bridge | 0.5 |
| `warehouse_safety` | ament_python | Emergency Guardian・twist_mux | bridge | 0.5 |
| `warehouse_traffic` | ament_python | TrafficManager IF（None/Simple） | ros2 | 0.5→3 |
| `warehouse_teleop` | ament_python | 手動操縦（足場） | ros2/hw | 1 |
| `warehouse_nav2_bridge` | ament_python | REST→BasicNavigator（Mode A/B 実行先） | bridge | 0.5 |
| `warehouse_llm_bridge` | ament_python | 司令官LLM サイクル・排他制御・キャラLLM | bridge | 0.5→3 |
| `warehouse_mcp_server` | ament_python | 7ツール + Policy Gate + gen_id 検証 | bridge | 0.5 |
| `warehouse_orchestrator` | ament_python | KPI 計測・Langfuse score・分析 | wo | 0.5→4 |
| `warehouse_rmf_adapter` | ament_python | Mode C 案A EasyFullControl Fleet Adapter（offline core: routing/namespacing/single-writer・rclpy/rmf_* 非依存） | ros2/nav-traffic | 3 |
| `eval_sdk` | ament_python | ドメイン非依存 embodied-AI 評価コア（`seed`/`tracer`/`sink`/`stats`/`cost`）。**意図的に非 `warehouse_*`**＝`rclpy`/`warehouse_*` import ゼロ・`langfuse` は optional pip extra（doc21・抽出 PR#273） | wo/docs | 0.5→4 |

**命名規約**:
- パッケージ名: `warehouse_<役割>`（snake_case）。**例外**: `eval_sdk` のみ非 `warehouse_*`＝ドメイン非依存の再利用コアであることを名前で示すための意図的命名（doc21）。
- ノード名・トピック名: snake_case。ロボット固有トピックは namespace `/bot1` `/bot2` を `PushRosNamespace` で注入し、ノード内にハードコードしない。
- 共有/単一インスタンス（`/map`, micro-ROS Agent, Hermes, MCP, State Cache, Emergency Guardian）は namespace 外（グローバル）。

**ビルドタイプ規約**:
- **全パッケージ ament_python**（Phase 0.5〜3）。`warehouse_interfaces` は `.msg/.srv` を持たず（§3: 初期は `std_msgs/String` の JSON 運用）、pydantic schemas・Store IF・共有パス定数のみを提供する**純 Python 契約パッケージ**のため ament_python とする。
- **Phase 4 で構造化 `.msg`（`Situation.msg` 等）を導入する際**にのみ、`warehouse_interfaces` を **ament_cmake へ移行**（rosidl は Python パッケージで生成不可）。または ament_cmake の msg 専用パッケージへ分割し、pydantic 変換層は ament_python 側へ残す。移行は購読側の型差し替えのみで済むよう §3 の2段構えに従う。
- launch は `.launch.py`（Python 形式）。XML は使わない（`.claude/rules/code-style.md`）。
- YAML（config/param）は 2スペースインデント。

---

## 3. カスタムメッセージ規約

doc03 では `/llm/situation` `/llm/command` `/wo/mission` を「カスタム（JSON）」と表記し、doc14 のトピック表は `std_msgs/String (JSON)` で統一している。この不整合を以下で確定する:

- **初期（Phase 0.5〜3）は `std_msgs/String` に JSON 文字列を格納する**方式で統一する。スキーマ変更が頻繁な段階で `.msg` 再ビルドのオーバーヘッドを避け、Gazebo E2E に最短到達するため。
- JSON のスキーマは各設計ドキュメント（08a/08c の situation JSON、14 の proposal 等）を**唯一の真実**とし、Python 側は `pydantic` 等で検証する。
- 安定後（Phase 4 以降、必要なら）`warehouse_interfaces` の構造化 `.msg`（`Situation.msg` / `Command.msg`）へ移行する2段構え。移行は購読側の型差し替えのみで済むよう、JSON ⇄ dataclass の変換層を `warehouse_interfaces` 側に置く。

---

## 4. 共有ファイルパス・実行時ディレクトリ規約

別プロセス間（rclpy ノード群 ↔ Hermes stdio 子の MCP Server）で共有するファイルのパスを固定する。ズレると Bridge / MCP / State Cache が噛み合わない。

| 用途 | パス（開発時） | 書込 | 読込 | 方式 |
|---|---|---|---|---|
| 状態スナップショット | `/tmp/warehouse/state.json` | State Cache Node | LLM Bridge | `tmp`→`os.replace` の atomic write |
| gen_store | `/tmp/warehouse/gen_store` | LLM Bridge | Warehouse MCP Server | §6 参照 |
| Command Audit Log | `$WAREHOUSE_AUDIT_LOG_PATH`（既定 `/tmp/warehouse/audit.jsonl`） | MCP Server | 分析 | JSON Lines 追記 |
| 冪等キーストア | `/tmp/warehouse/idempotency_store`（`idempotency_store_path()`） | Warehouse MCP Server | Warehouse MCP Server | per-call UUID 消費記録＝C 層 replay 拒否（R-35 / doc15 §2。`IdempotencyStore.check_and_add`、入口は `GenChecker.check`） |
| 倉庫 config | `config/warehouse.base.yaml` + `config/$WAREHOUSE_ENV/warehouse.yaml`（base+overlay） | — | 全ノード | YAML |

- **環境分離（dev/stg/prod）は `WAREHOUSE_ENV` で切替**。config は base+overlay で解決し、runtime dir（dev=`/tmp/warehouse/` / prod=`/run/warehouse/`）も環境別。詳細は **[doc19 - 環境分離と設定](19-environments-and-config.md)**（旧 `$WAREHOUSE_CONFIG_PATH` 単一ファイル方式を置換）。
- アクセスは抽象インターフェース越し（例 `StateStore` / `GenStore`）にし、実体（file / Redis 等）を Phase 進行で差し替えられるようにする。

---

## 5. config.yaml 単一ソース原則

- ROS 2 ノードのパラメータ（Nav2 / AMCL / SLAM / twist_mux / footprint / 速度上限）は **`warehouse_bringup/config/` に集約**し、各ノードパッケージは config を持たず launch 引数で受け取る。
- 1ファイル1責務に分割（`nav2_params.yaml` / `slam_params.yaml` / `amcl.yaml` / `twist_mux.yaml` / `bot_params.yaml`）→ 別担当が別ファイルを編集でき、衝突を避ける。
- モード切替・サイクル長・キャラLLM設定は倉庫 config（`config/warehouse.base.yaml` + `config/<env>/warehouse.yaml` の base+overlay。**doc19**）に集約。`traffic_mode` 等のキー名・「1行変更でモード切替」は不変で、mode-a/c docs の「config.yaml」はこの倉庫 config を指す。
- ハードウェア固有パラメータ（footprint・wheel base・エンコーダ分解能）は実測前は**暫定値＋`# TODO: Phase 1 実測で確定` マーカー**を付け、確定後に1ファイル差し替えで済む構造にする（ロボット実寸 T1 未確定のため）。

---

## 6. gen_store 方式の決定

排他制御 B-3（MCP tool の required `gen_id` 引数）で、LLM Bridge と Warehouse MCP Server が**別プロセス間**で `current_gen` を共有する必要がある（doc08 §同時発火制御 / doc15）。方式は doc06 で「Phase 1 で選定」とされていたが、実装着手のため以下に暫定確定する:

- **Phase 0.5〜1: file 方式**（`/tmp/warehouse/gen_store`）に固定。Mac/Gazebo の単一ホストでは十分。
- **抽象 IF `GenStore`（`get()` / `set()`）越しにアクセス**し、Bridge / MCP の両方が同 IF を使う。
- **最終選定（file 継続 / `multiprocessing.Value` / Redis）は Phase 1 のメモリ・競合実測後**に確定（doc06 Phase 1 タスク）。IF 化により実装差し替えのみで移行可能。

> 選定基準: Jetson 実機で Bridge↔MCP が別プロセスのまま競合・遅延なく共有できるか。file が atomic に問題なければ追加依存（Redis）を入れない（YAGNI）。

---

## 7. モデル方針の確定（全 Claude Opus 統一）

`.claude/CLAUDE.md` Model Policy（「常に Opus（最新世代）を使用する。`opus` エイリアスを用い特定バージョンに固定しない。haiku/sonnet へのダウングレードは行わない」）に従い、**ランタイムのLLMロールも含めて Claude は全て Opus（最新世代）に統一する**。これにより従来ドキュメントの「司令官=Sonnet / キャラ=Haiku」設計は廃止。

| ロール | 旧設計 | **確定（本書）** |
|---|---|---|
| 司令官LLM（Claude） | Sonnet 4 | **Opus（最新世代）** |
| キャラLLM（Bot1/Bot2） | Haiku 4.5 | **Opus（最新世代）** |
| Phase 4 比較対象の他社 | GPT-4o / Gemini 2.5 Flash / Grok 4.3 | 変更なし（比較は Claude=Opus 対 他社） |

- API 呼出のモデル文字列は最新世代 Opus を使う（バージョン固定しない方針のため、新リリース時に更新）。
- コスト試算は Opus 単価（$15/MTok 入力・$75/MTok 出力）で再計算する（§影響箇所参照）。

> ⚠️ **検討事項（要追跡）**: キャラLLM を Haiku に選んだ元設計理由は**テンポ重視**（応答 0.3-0.5s、`max_tokens=60`）だった。Opus 化で応答が ~1-2s に伸び、コストも大幅増（Haiku比で約30倍/トークン）。Mode A の「キャラ同士の軽快な掛け合い」が動画の主役（doc14 §動画的役割「メインショー」/ [mode-a/README](../mode-a/README.md)。※ [05-video-storyboard](../shared/05-video-storyboard.md) は現状この交渉シーンを含まず未反映＝要追補）であるため、Phase 0.5/3 のテストで会話テンポが損なわれないかを実測し、テンポが致命的なら本方針の例外化（キャラのみ別系統）を再検討する。現時点は CLAUDE.md 厳守で全 Opus を既定とする。

---

## 8. .gitignore 必須項目

実装開始前に以下を `.gitignore` に追加する（現状は `.env` 系のみ）:

```gitignore
# ROS 2 colcon 生成物
ws/build/
ws/install/
ws/log/
# Python
__pycache__/
*.pyc
# 認証情報（safety.md）
**/.env
# スケジューラ等のロック
*.lock
.claude/scheduled_tasks.lock
# PlatformIO
firmware/.pio/
```

---

## 9. git ブランチ戦略

`main` から各 feature ブランチを切り、**担当ディレクトリを物理分離**して並行作業の衝突を排除する（main 直 push 禁止、ブランチ先行）。

| ブランチ | 担当ディレクトリ | 着手可能条件 |
|---|---|---|
| `feat/repo-skeleton` | `ws/` 初期化・`warehouse_interfaces`・`warehouse_bringup` 骨格・`.gitignore` | **最初にマージ（全土台）** |
| `feat/sim-gazebo` | `warehouse_sim`・`warehouse_description` | skeleton 後 + §環境スパイク成功 → **条件充足: スパイク GO（2026-05-30）。PR #43 マージ済（#7 closed）。実 bot1/bot2 Gazebo E2E は #8** |
| `feat/safety-state` | `warehouse_safety`・`warehouse_state` | skeleton 後（独立・並行可） |
| `feat/nav-traffic` | `warehouse_traffic`・`warehouse_rmf_adapter`・`bringup/config/nav2*` | sim spawn 後 |
| `feat/llm-bridge` | `warehouse_llm_bridge`・`warehouse_mcp_server`・`warehouse_nav2_bridge` | **偽トピックで即着手可（Gazebo/実機不要）** |
| `feat/wo-metrics` | `warehouse_orchestrator` | bridge と trace_id 受け渡し合意のみ |
| `feat/eval-sdk` | `ws/src/eval_sdk` | doc21（#269）land 後＝既存評価コア（trace/score/cost）の純関数サブセット抽出（Phase 1 抽出=#273）。ROS/warehouse 非依存ゆえ独立着手可 |
| `hw/jetson-setup` | `deploy/jetson`・`docs/setup` | **実機不要で即着手可** |
| `hw/firmware-esp32` | `firmware/` | 実機不要で雛形まで可 |

**衝突防止ルール**:
- `bringup/config/` は1ファイル1責務に分割し、別担当が別ファイルを触る。
- `package.xml` / `setup.py` の依存追加は各担当が自パッケージ内のみ編集。
- URDF ↔ world のインターフェース（リンク名・センサ frame_id・footprint）は skeleton マージ時に固定し、description と sim 両担当が参照する。
- ROS 2 トピック名・型（doc03 のトピック表）を**共有契約**とし、各ドメインはこの契約に対して独立実装する。

**マージ順の原則**: `feat/repo-skeleton` → 独立3本（`feat/llm-bridge`/`hw-*`/`feat/wo-metrics`）は随時 → sim 系 → nav-traffic → 統合E2E。

---

## 10. Phase 0.5 の最優先ゲート（環境スパイク）

実装着手後、最初に潰すべき技術リスク: **`tiryoh/ros2-desktop-vnc:jazzy`（ARM64）上で、ヘッドレス `gz sim` + LiDAR センサ（GpuLidar、不可なら CPU ray cast フォールバック）+ `ros_gz_bridge` が成立するか**。これが Phase 0.5 を「Mac 単体で完結できる」前提の分岐点。成立しない場合は Linux/x86 機またはクラウド GPU での Gazebo に退避する。可視化は Gazebo GUI ではなく RViz2 に寄せる（ソフトウェア OpenGL で GUI が実用に耐えない可能性が高いため）。

> **結果（2026-05-30, PR #43）: GO。** `gz sim`（Gazebo Harmonic / gz-sim8 **8.11.0**）が `--headless-rendering` で起動し、**`gpu_lidar` が ogre2 + ソフトウェア GL（llvmpipe / `LIBGL_ALWAYS_SOFTWARE=1`）で初期化成立**（CPU ray-cast フォールバック不要）。`ros_gz_bridge` で `/bot{n}/{scan,odom,cmd_vel}` を橋渡し、`/bot{n}/scan` ~9–10Hz（frame_id `bot{n}/lidar_link`、ranges 非空）、`cmd_vel` で実移動を確認。`docker run --memory=6g` で OOM なし。**退避（Linux/x86・クラウド GPU）不要 → Phase 0.5 は Mac 単体で完結可能**。再現コード・証跡: `ws/src/warehouse_sim/spike/`（`run_spike.sh` / `RESULT.md`）。

---

## 11. テスト戦略（最小規約）

- 安全機構（Emergency Guardian / Policy Gate、および firmware の Layer-0 速度クランプ）は**ユニットテスト必須**（doc07 R-26）。距離・バッテリー・stale・重複の各拒否ケースを偽入力で検証する（Layer-0 クランプは非有限 `cmd_vel`→stop・上限超過→クランプを host R-26 unit `firmware/test/test_clamp` で固定。R-26 の本来の対象は Guardian/Policy Gate だが、Layer-0 firmware クランプも同規律の拡張）。
- LLM Bridge / MCP Server は Gazebo・実機なしで E2E テストできる形に設計する（偽トピック・偽 State Cache JSON で先行検証）。
- 周期保証（50ms/100ms）は非RT Linux でベストエフォート。Mac Docker では「ロジックの正しさ」のみ検証し、周期実測は Jetson 実機（Phase 0.5 段階2）へ送る。

---

## 12. 本書確定に伴う既存ドキュメント更新状況

| 項目 | 対象 | 状況 |
|---|---|---|
| モデル方針（全Opus統一） | 08, 13, 14, 15, 06, 07, README, mode-a/README | §7 と同時に更新 |
| コスト試算（Opus単価で再計算） | 08 §コスト, 15, 07 T11 | 同上 |
| gen_store 方式（file 暫定確定） | 06 Phase1, 08, 15 | 本書を参照点とする |
| カスタムメッセージ（JSON-in-String 統一） | 03 | 本書を参照点とする |
| リポジトリ構成・命名規約 | （新規・本書） | 確定 |

---

## References

- `.claude/CLAUDE.md` — Model Policy / Code Conventions
- `.claude/rules/code-style.md` — launch は .launch.py、YAML 2スペース
- `.claude/rules/safety.md` — 速度上限 0.3 m/s 強制、認証情報非コミット
- [ROS 2 Jazzy — Creating a package](https://docs.ros.org/en/jazzy/Tutorials/Beginner-Client-Libraries/Creating-Your-First-ROS2-Package.html) — 参照日: 2026-05-29
