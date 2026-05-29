# 開発の進め方と分担（実行手順書）

作成日: 2026-05-29

> **位置づけ**: 本書は「どの順で・誰が・いつ着手し・どこで合流するか」の**実行手順（ランブック）**。
> *構造とルール*は [16 - リポジトリ構成と実装規約](16-repository-and-conventions.md)、*フェーズ計画*は [06 - 実装フェーズ](06-implementation-phases.md) を正本とし、本書はそれらを**並列実行の手順**へ落とし込む。

> **前提となる2つの決定（2026-05-29 確定）**:
> 1. リポジトリ構成は **doc16 の `ws/src/` 集約**に統一する（scaffold の top-level `src/` から移行）。
> 2. 並列開発は **git worktree × 並列エージェント**で回す（1パッケージ＝1ブランチ＝1worktree）。

---

## 1. 分担の中核思想 — 「契約ファースト」

並列が成立する唯一の条件は、**各トラックがお互いを見ずに実装できること**。そのために最初に以下を**凍結（freeze）**し、以降は共有契約として扱う:

- **トピック契約**: トピック名・型（[03 §トピック設計](03-software-architecture.md)）
- **JSONスキーマ**: situation / command / proposal（doc08・doc14）→ `warehouse_interfaces/schemas.py` に pydantic で実装（doc16 §3）
- **共有ファイルパス・抽象IF**: `state.json` / `gen_store` のパスと `StateStore`/`GenStore` IF（doc16 §4・§6）

> 凍結後、各ドメインは**偽トピック・偽 `state.json`** に対して独立実装・独立テストする（doc16 §11）。
> **パッケージ分離 ＝ プロセス分離 ＝ ブランチ分離 ＝ worktree分離**（doc16 §9）。

---

## 2. 進め方の三層構造

```
   Step 0          Step 1                 Step 2              Step 3
 ┌────────┐   ┌──────────────┐    ┌──────────────┐    ┌──────────┐
 │ 土台    │ → │ 独立トラック   │ →  │ クリティカル   │ →  │  統合E2E  │
 │(逐次)   │   │ (並列・worktree)│    │ パス(直列)    │    │ (main上) │
 └────────┘   └──────────────┘    └──────────────┘    └──────────┘
  契約凍結       核心を厚く          sim→nav-traffic       随時マージ
                              ↑ 環境スパイクが前段ゲート
```

- **Step 0 だけは並列不可**（全worktreeの分岐元）。
- **Step 1 の独立トラックは完全並列**（Gazebo・実機ともに不要）。
- **Step 2 が全体所要を決める直列区間**。
- 現状 **Jetson未着＝全タスクがMac/ソフト/クラウドのみ** → ハードに律速されるものが無く、並列の好機。

---

## 3. 依存グラフと着手条件

```
        ┌─ feat/llm-bridge ★偽トピックで即着手・無依存（プロジェクトの核心・最重量）
        │   (llm_bridge + mcp_server + nav2_bridge)
        ├─ feat/safety-state ★独立・即着手（safety + state／ユニットテスト必須）
skeleton ┼─ hw/jetson-setup ★実機不要で即着手（deploy/jetson）
(土台1本) ├─ hw/firmware-esp32 ★実機不要で雛形まで（firmware/）
        ├─ feat/wo-metrics （trace_id契約の合意だけで着手）
        └─ [環境スパイク §5] ─→ feat/sim-gazebo ─→ feat/nav-traffic ─→ 統合E2E
                                (sim+description)  (traffic+nav config)
                                  ↑ クリティカルパス（最長・逐次）↑
```

| ブランチ | 担当パッケージ | 着手条件 | 優先度 |
|---|---|---|---|
| `feat/repo-skeleton` | `ws/`初期化・interfaces・bringup骨格・契約凍結 | **最初に単独マージ** | 最優先（逐次） |
| `feat/llm-bridge` | llm_bridge / mcp_server / nav2_bridge | skeleton後・即 | ★人手を最も厚く |
| `feat/safety-state` | safety / state | skeleton後・即 | 高（安全機構） |
| `hw/jetson-setup` | deploy/jetson・systemd | 即（skeleton前でも可） | 中 |
| `hw/firmware-esp32` | firmware/ | 即（雛形まで） | 中 |
| `feat/wo-metrics` | orchestrator | trace_id契約合意のみ | 低（後追い可） |
| `feat/sim-gazebo` | sim / description | **環境スパイク成功後** | クリティカルパス前段 |
| `feat/nav-traffic` | traffic / nav2 config | sim spawn後 | クリティカルパス本体 |

---

## 4. worktree 実行ランブック

### Step 0: `feat/repo-skeleton`（逐次）

このブランチの仕事の **8割は「契約の凍結」、2割がディレクトリ改名**。

1. **ディレクトリ移行**: `src/` → `ws/src/`。`warehouse_msgs`→`warehouse_interfaces`、`warehouse_nav`→`warehouse_traffic`＋`warehouse_nav2_bridge`、不足4パッケージ（`state`/`description`/`sim`/`teleop`）の骨格生成。各パッケージに最小 `package.xml`＋`setup.py`＋空ノードエントリ。
2. **契約の凍結（最重要）**: §1の3点（トピック契約／pydanticスキーマ／共有パス・抽象IF）を実装。
3. **偽トピック・偽 `state.json` ハーネス**（doc16 §11）。
4. **`warehouse_bringup/config/`** を1ファイル1責務で分割（中身stub可、ファイルを先に作り編集衝突を回避。doc16 §5）。
5. `.gitignore` 追記（doc16 §8）。
6. `colcon build` 成功確認 → **mainへマージ**。

### Step 1: worktree 展開（skeletonマージ後）

```bash
git worktree add ../mwr-llm-bridge   feat/llm-bridge
git worktree add ../mwr-safety-state feat/safety-state
git worktree add ../mwr-jetson       hw/jetson-setup
git worktree add ../mwr-firmware     hw/firmware-esp32
```

- 各worktreeに**1エージェント**を割り当て、**`feat/llm-bridge` に最優先で厚く**配置（核心・最重量・偽トピックで完全独立）。
- **同時に環境スパイク（§5）を起動**（成果物ブランチではなく probe。sim系を全ブロックしうるため最優先で成否確定）。

### Step 2: クリティカルパス

環境スパイク成功 → `feat/sim-gazebo`（spawn成功まで）→ `feat/nav-traffic`。

### Step 3: 統合

★独立トラックは随時 main へマージ → sim系 → nav-traffic → main上で統合E2E。

---

## 5. ゲート（先に潰すべき技術リスク）

| ゲート | 内容 | 失敗時の退避 |
|---|---|---|
| **環境スパイク**（doc16 §10） | `tiryoh/ros2-desktop-vnc:jazzy`(ARM64) でヘッドレス `gz sim` + LiDAR + `ros_gz_bridge` が成立するか | Linux/x86 機 or クラウドGPU での Gazebo。可視化は RViz2 に寄せる |
| **メモリ検証**（doc06 Phase0.5） | 段階1: Mac Docker `--memory=6g` で全スタックがOOMしないか | ヘッドレス起動で0.5〜1GB節約。Open-RMF可否を再検討 |
| **APIレイテンシ**（doc06 Phase0.5） | Claude応答 p95 > 2.5s ならサイクル4-5秒へ | サイクル長を config で延長 |

---

## 6. マージ順と衝突防止

**マージ順**: `feat/repo-skeleton` → 独立3〜4本（llm-bridge/hw-*/wo-metrics）随時 → sim系 → nav-traffic → 統合E2E。

**衝突防止ルール**（doc16 §9）:
- 凍結済みの**トピック名・型・JSONスキーマは触らない**（変更は別途 skeleton へPR）。
- `bringup/config/` は1ファイル1責務 → 別担当は別ファイルのみ編集。
- `package.xml`/`setup.py` の依存追加は**自パッケージ内のみ**。
- URDFリンク名・センサ frame_id・footprint は skeleton で固定 → description と sim が同じものを参照。

---

## 7. フェーズ計画（doc06）との対応

| doc06 Phase | 本書での扱い |
|---|---|
| Phase 0 | 機材調達（発注済・[memory]）+ Docker/ROS2環境 = 環境スパイクの前提 |
| **Phase 0.5** | **本書 Step 0〜2 の主戦場**（Gazebo + LLM Bridge を Mac 単体で並列開発） |
| Phase 1〜2 | Jetson着・実機到着後。skeleton で付けた `# TODO: Phase 1 実測で確定` を実値に差し替え（doc16 §5） |
| Phase 3〜6 | 並列で育てた各パッケージを実機統合 → 比較 → Isaac → 撮影 |

---

## References

- [06 - 実装フェーズ](06-implementation-phases.md) — Phase 0-6 計画（正本）
- [16 - リポジトリ構成と実装規約](16-repository-and-conventions.md) — 構造・命名・ブランチ戦略（正本）
- [03 - ソフトウェアアーキテクチャ](03-software-architecture.md) — トピック契約
- [08 - LLM Bridge 共通](08-llm-bridge-common.md) / [14 - キャラLLM・交渉](14-character-llm-negotiation.md) — JSONスキーマ
