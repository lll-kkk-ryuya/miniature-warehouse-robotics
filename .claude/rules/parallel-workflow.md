# 並列開発・分担ルール

> 本ルールは複数セッション（worktree）で並行開発する際の**協調プロトコル**を定める。
> 構造・命名は [docs/architecture/16](../../docs/architecture/16-repository-and-conventions.md)、
> 実行手順は [docs/architecture/17](../../docs/architecture/17-development-workflow.md) を正本とし、本書は**運用規約**のみを定める。

---

## 1. 基本原則

- **1セッション = 1 worktree = 1ブランチ = 1トラック**。同一ブランチ／同一ディレクトリを2セッションで同時に触らない。
- **`main` は統合専用・常にクリーン**に保つ（直編集・直 push 禁止、ブランチ先行）。
- 各セッションは root `.claude/CLAUDE.md` ＋ 自パッケージの `CLAUDE.md` ＋ 本ルールを読み、**自トラックの担当ディレクトリのみ**を編集する。
- **同一マシンの並列ブランチは必ず worktree を使う**（新規 clone ではない）。worktree は `.git` を共有しコミットが相互即時可視・軽量。完全に独立したパッケージでも worktree でよい（むしろ衝突ゼロで最適）。**新規 clone を使うのは別マシンの場合のみ**（＝ Jetson 実機。Phase 1〜 `deploy/jetson` 実行用）。判断軸と根拠は [doc17 §4.0](../../docs/architecture/17-development-workflow.md)。

### worktree 作成・破棄チェックリスト

**作成時（着手）**:
1. **Issue を選ぶ**: `ready` ラベルのものだけ着手可。`blocked`（依存未解決）は不可。新トラックなら**先に epic Issue を作る**（§3。Issue/ラベル無しで始めない＝トラッキング不能になる）。
2. **命名はブランチ表に従う**（doc16 §9 / doc17）。フォルダ `../mwr-<track>`、ブランチ `feat/<track>` または `hw/<track>`（例 `feat/sim-gazebo` / `feat/wo-metrics` / `hw/firmware-esp32`）。勝手に短縮・改名しない。
3. **最新 main を基点に作成**:
   ```bash
   git fetch origin                                   # 念のため最新化
   git worktree add ../mwr-<track> -b <branch> main   # フォルダ＋新ブランチ
   ```
4. **セッション起動（session 名を付ける）**: `cd ../mwr-<track> && claude -n "mwr-<track>"`（`-n/--name` で session 表示名を付与。下記「session 命名規約」）。Opus を確認。キックオフで「担当 Issue 番号・編集境界（自パッケージのみ）・本ルール参照」を伝える。
5. **Issue を着手中に更新**: `blocked`→`ready`、assign または着手コメント（他セッションとの重複防止）。

**破棄時（完了）**:
1. PR を出す（`[track] ...` / `Closes #N` / track ラベル）。`colcon build` 通過・安全機構はユニットテスト通過を確認。
2. main へマージ（マージ順 doc17 §6）。
3. クリーンを確認して掃除:
   ```bash
   git worktree remove ../mwr-<track>   # 未コミット残があると拒否される
   git branch -d <branch>               # マージ済みブランチを削除
   git worktree prune                   # 手で消した残骸の掃除
   ```

**禁止事項**:
- `main` worktree で開発しない（統合専用）。
- 同一ブランチを2 worktree で checkout しない（git が拒否する）。
- `blocked` Issue のトラックを先行着手しない。
- 別トラックのフォルダ／共有契約を触らない（契約変更は §4 経由）。
- **新トラックを epic Issue／ラベル無しで開始しない**（協調不能になる）。

### session 命名規約（worktree 起動時に必ず付ける）

並列 worktree の識別性のため、**`claude` 起動時に session 表示名を付ける**（どの worktree のセッションかを prompt box・`/resume` picker・端末タイトルで即判別でき、「どのセッションだっけ」を防ぐ）。

- **規約**: session 名 = **worktree フォルダ名 `mwr-<track>`**（`git worktree list` と一致し、PR/Issue 冒頭タグ（[merge-and-communication.md](merge-and-communication.md) §2）の `worktree:` フィールドと対応）。起動は `claude -n "mwr-<track>"`（`-n/--name`。Claude Code v2.1.160 で確認。help: 「Set a display name for this session（prompt box / `/resume` picker / 端末タイトルに表示）」）。再開は `claude --resume mwr-<track>`、改名は `/rename`。
- **`--session-id <uuid>` は別物**（UUID 必須の機械ID＝transcript ファイル名であり表示名ではない）。同一 worktree を常に同じ transcript で再開したい場合のみ、worktree パスから導出した deterministic UUID（例 `uuid5`）を `-n` と併用してよい。
- **任意の自動化（有効化は人間）**: `SessionStart` hook の `sessionTitle`（Claude Code v2.1.152+）で cwd/branch から自動命名、または `statusLine` に `session_name` / `workspace.git_worktree` を表示する手もある。ただし **hooks / `settings.json` の有効化は人間が `/update-config` 等で行う**（エージェントによる `settings.json` 自己改変は禁止。[.claude/hooks/README.md](../hooks/README.md) / §7.1）。

---

## 2. パッケージ間「依存」の扱い（2分類）

依存には性質の異なる2種類があり、扱う場所を分ける。

### 2.1 コード依存（build / runtime）→ `package.xml` で宣言・契約ハブに集約

- **共有依存は2つのみ**:
  - `warehouse_interfaces`（凍結契約: pydantic schemas / `StateStore`・`GenStore` IF / 共有パス）。
  - `warehouse_description`（ロボット記述の**単一ソース**: URDF / `frame_id` / footprint / 寸法定数。sim・nav・bringup が同一を参照する**読み取り専用の共有アセット**。doc16 §9）。
- 各パッケージは **この2共有パッケージにのみ依存**し、**他トラックの内部モジュールを import しない**（疎結合）。`warehouse_description` は track 実装を持たず、定数/記述を提供するのみ。
- 依存方向は一方向（共有パッケージ ← 各パッケージ）。**循環依存は禁止**（共有パッケージは他トラックを import しない）。
- 例: `warehouse_llm_bridge` は interfaces の `Situation`/`Command` schema と `StateStore` IF にのみ依存。`warehouse_state` が `StateStore` を**実装**する。両者は IF 越しにのみ結合 → 実体が無くても**偽実装（fake）で各々独立に開発・テストできる**（doc16 §11）。

```
            warehouse_interfaces  ← 凍結契約（ハブ）
          ┌─────────┼─────────┬──────────┐
   llm_bridge   state/safety   sim/desc   orchestrator
   （契約を consume / produce するだけ。互いを import しない）
```

### 2.2 作業依存（順序）→ GitHub Issue の依存リンクで管理

- **1トラック = 1 epic Issue**。タスクは Issue 本文のチェックリスト（出典: doc06 / doc17）。
- 順序依存は Issue 本文に **`Depends on #N` / `Blocked by #N`** を明記。
- ラベルで状態可視化（§3）。例: nav-traffic Issue は `Blocked by #<sim>`。

---

## 3. GitHub ラベル / Issue / PR 規約

### ラベル体系

| 種別 | ラベル | 意味 |
|---|---|---|
| トラック | `track:skeleton` `track:llm-bridge` `track:safety-state` `track:sim` `track:nav-traffic` `track:wo` `track:jetson` `track:firmware` `track:docs` | どの担当領域か |
| 契約 | `contract` | `warehouse_interfaces`（凍結契約）に触れる → **全トラック影響・特別レビュー** |
| 経路 | `critical-path` | sim→nav-traffic（全体所要を律速） |
| 状態 | `blocked` / `ready` | 依存待ち / 着手可能 |

### Issue 規約

- 各トラックの epic Issue にトラックラベルを付与。
- 依存は本文に `Depends on #N`。着手可能になったら `blocked`→`ready` に張り替え。

### PR 規約

**原則**:
- **ブランチ先行・`main` への直コミット/直 push 禁止**。全変更は feature ブランチ → PR → マージ。
- **マージは必ず PR を作成してから行う。** PR を作らないマージ、および「PR 作成と同一操作（同一ターン）での即時 self-merge」は禁止。**手順を分ける**: ①PR 提出 → ②CI 緑・レビュー可能状態を確保（PR を可視化）→ ③その上でマージ。作成者が勢いで即マージしない。
- **1 PR = 1トラック = 1 epic Issue**。巨大 PR は避け、レビュー可能な単位に分割する。
- PR を出す前に **`git merge main`** で最新を取り込み、衝突を手元で解消してから提出する。

**作成時の必須項目**:
- タイトル: `[track] 要約`（命令形。例 `[llm-bridge] add commander cycle`）。
- 本文: ①何を/なぜ ②**`Closes #N`** ③影響範囲 ④テスト結果（`colcon build` / 安全機構の unit）⑤**契約変更の有無**。
- ラベル: **track ラベル必須**。`warehouse_interfaces`（凍結契約）に触れたら **`contract` 必須**（§4）。
- WIP は **Draft PR**。レビュー可能になってから Ready に切替。

**マージ条件（すべて満たす）**:
1. `colcon build` 通過（理想は CI で自動検証。CI 整備は dev-tooling トラック）。
2. 安全機構（Emergency Guardian / Policy Gate）は**ユニットテスト通過**（doc16 §11）。
3. **契約後方互換**（破壊的なら `contract` ラベル＋依存トラック合意 §4）。
4. レビュー承認（contract PR は依存トラックの合意必須）。

**マージ後**:
- **squash merge 推奨**（1トラックの履歴を集約）。
- worktree とブランチを掃除（§1 破棄チェックリスト: `git worktree remove` → `git branch -d`）。
- マージ順は **doc17 §6**（skeleton → 独立トラック随時 → sim系 → nav-traffic → 統合E2E）。

---

## 4. 契約変更プロトコル（最重要のやり取り）

凍結契約 `warehouse_interfaces` の変更は全トラックに波及するため、以下を厳守する。

1. 契約を変える PR には **`contract` ラベル必須**。
2. **マージ前に、依存する全トラックの Issue にコメントで予告**し、レビュー合意を得る。
3. 契約は**安定が前提**（doc16 §3: Phase 4 まで `std_msgs/String` の JSON 文字列で運用し、頻繁な `.msg` 再ビルドを回避）。変更は**最小限・後方互換優先**。
4. トピック名・型・JSON スキーマの「真実」は設計ドキュメント（doc03 / doc08 / doc14）。コード（pydantic）はそれを検証する側であり、**勝手にスキーマを拡張しない**。

---

## 5. パッケージ別 `CLAUDE.md` 規約

`ws/src/warehouse_*/CLAUDE.md` を**必須**とし、worktree セッションが `cd` した瞬間に担当範囲の文脈を得られるようにする。最低限以下を含める:

```markdown
# warehouse_<pkg> — <一行責務>

- **担当トラック / ブランチ**: feat/<track>
- **Phase**: <doc06のPhase>
- **編集境界**: このパッケージ配下のみ。他パッケージ・共有契約は触らない（変更は §4）
- **消費する契約**: <warehouse_interfaces の schema / IF>
- **生産する契約 / トピック**: <publish するトピック・型、実装する IF>
- **依存**: warehouse_interfaces のみ（他トラック内部を import しない）
- **テスト**: <偽トピック / 偽 state.json での独立検証方法。安全機構はユニット必須>
- **設計ドキュメント**: docs/... へのリンク
```

---

## 6. 衝突防止チェックリスト（doc16 §9 再掲）

- 凍結済みの**トピック名・型・JSONスキーマは触らない**（変更は §4 経由）。
- `warehouse_bringup/config/` は **1ファイル1責務**に分割 → 別担当は別ファイルのみ編集。
- `package.xml` / `setup.py` の依存追加は**自パッケージ内のみ**。
- URDF リンク名・センサ frame_id・footprint は skeleton で固定 → description と sim が同じものを参照。

---

## 7. 衝突回避の追加規約（教訓由来。詳細は [docs/dev/03-retrospectives](../../docs/dev/03-retrospectives.md)）

### 7.1 共有ファイルは「単一所有者 or contract-PR」

共有ファイルの同時編集が並列事故の主因。各共有ファイルに**単一の所有トラック**を割り当て、所有者以外が変えたい時は **所有トラックの Issue に予告（先頭 worktree タグ）→ 合意 → 編集**（§4 経由）。

| 共有ファイル / ディレクトリ | 所有 | 他トラックが変えたい時 |
|---|---|---|
| `ws/src/warehouse_interfaces/**`（凍結契約） | skeleton | **contract-PR**（`contract` ラベル＋予告 §4） |
| `config/warehouse.base.yaml` | bringup/skeleton | 所有 Issue へ予告→合意。環境差分は `config/<env>/`（environments.md） |
| `warehouse_bringup/config/<file>.yaml` | doc16 §5 の1ファイル1責務に従う各担当 | **別ファイルなら自由**・同一ファイルは所有者調整 |
| `docs/STATUS.md` | オーケストレーター | 各トラックは自節のみ追記、構造変更は予告 |
| **`.claude/**` / `.github/**`（メタ/ガバナンス）** | **governance（`track:docs`）** | governance Issue 経由。複数同時のメタ作業は直列化 |

### 7.2 契約変更は additive-first

既存フィールドの**削除・改名・型変更は破壊的＝原則禁止**（必要なら予告＋全消費トラック合意＋移行期間）。追加は **optional field / 新 store / 新トピック**（既存購読者が無視できる形）を既定とする（pydantic `extra="ignore"` で前方互換）。#36 gen_id 冪等化が好例。

### 7.3 掃除は squash 前提で判定（`--merged` を使わない）

squash マージは別コミットになるため `git branch --merged` は**偽陰性**。マージ済み判定は `gh pr view <N> --json state --jq .state` が `MERGED` か で行い、`git branch -D` ＋ `git push origin --delete` で stale を即削除（§1 破棄チェックリスト）。

---

## References

- [docs/dev/ 開発プロセス](../../docs/dev/README.md)（playbook / operator-runbook / retrospectives）
- [docs/architecture/16 - リポジトリ構成と実装規約](../../docs/architecture/16-repository-and-conventions.md)
- [docs/architecture/17 - 開発の進め方と分担](../../docs/architecture/17-development-workflow.md)
- `.claude/rules/code-style.md` / `safety.md` / `ros2.md`
