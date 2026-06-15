# 並列セッションの実装規律（気をつけること洗い出し）

> 正本は各 `.claude/rules/*.md` と `docs/`。本書は運用チェックリスト（着手前＋毎サイクル参照）。
> 並列処理する worktree / セッションが、実装の前と毎サイクルに目を通す単一の参照。各項目は do/dont の箇条書きで、末尾に `(正本: <file §>)` を付す。判断の出所が記憶でなく実ファイルであることを担保する。

---

## 0. 一行原則

- **1セッション = 1 worktree = 1ブランチ = 1トラック**。docs を正本に、自トラックだけを、契約に additive に触り、GitHub で連絡し、PR でマージし、二値ゲートを満たして「完了」と言う。これ以外の近道は取らない (正本: `.claude/rules/parallel-workflow.md` §1)。
- 迷ったら**止まって orchestrator / 所有 Issue に surface** する。記憶で補完して進めない (正本: `.claude/rules/docs-first.md` §引用)。

---

## 1. 編集境界・所有権（自分の庭だけ耕す）

- **自トラックの担当ディレクトリのみ編集**する。他トラックのフォルダ・共有契約は触らない (正本: `.claude/rules/parallel-workflow.md` §1, :13 / 担当ディレクトリ表 `docs/architecture/16-repository-and-conventions.md` :184)。
- **他トラックの内部モジュールを import しない**。依存は凍結契約ハブ経由のみ（疎結合・循環禁止） (正本: `.claude/rules/parallel-workflow.md` §2.1, :74-75 / `.claude/rules/implementation-and-dependencies.md` §1)。
- **共有ファイルは単一所有者 or contract-PR**。所有者以外が変えたい時は所有トラックの Issue に先頭 worktree タグ付きで予告→合意→編集 (正本: `.claude/rules/parallel-workflow.md` §7.1, :179-187)。
  - `ws/src/warehouse_interfaces/**`=skeleton（contract-PR）／`config/warehouse.base.yaml`=bringup/skeleton／`warehouse_bringup/config/<file>.yaml`=1ファイル1責務で各担当（別ファイルなら自由）／`docs/STATUS.md`=orchestrator（自節のみ追記）／`.claude/**`・`.github/**`=governance(`track:docs`)・メタ作業は直列化 (正本: `.claude/rules/parallel-workflow.md` §7.1 表, :183-187)。
- **新規ファイル優先・1ファイル1責務**で衝突面を最小化。割り当て前にレーンの編集境界をペアワイズ照合し重なりゼロを確認。重なるなら別レーンにせず順序化（例: #166 は別レーンにせず #162 land 後に同一ブランチで PR#174） (正本: `.claude/rules/session-orchestration.md` §2, :24-29)。
- 凍結済みの**URDF リンク名・センサ frame_id・footprint・トピック名・型は触らない**（skeleton 固定 / 変更は §2 経由） (正本: `.claude/rules/parallel-workflow.md` §6, :168-171)。
- `package.xml` / `setup.py` の依存追加は**自パッケージ内のみ** (正本: `.claude/rules/parallel-workflow.md` §6, :170)。
- メタ作業（`.claude/**`・`.github/**`）は governance 単一所有・直列化。複数セッションの同時メタ編集は衝突の主因（L5 で governance PR と issue セッションが `hooks/`・`ci.yml` を二重編集し衝突しかけ＋複製 `CLAUDE 2.md` 混入） (正本: `docs/dev/03-retrospectives.md` L5, :17)。

---

## 2. 契約（warehouse_interfaces）は additive-first

- 依存は**凍結契約2つのみ**: `warehouse_interfaces`（pydantic schemas / `StateStore`・`GenStore` IF / 共有パス）と `warehouse_description`（URDF / frame_id / footprint / 寸法の単一ソース・読み取り専用） (正本: `.claude/rules/parallel-workflow.md` §2.1, :71-73)。
- **既存フィールドの削除・改名・型変更は破壊的＝原則禁止**。追加は optional field / 新 store / 新トピック（既存購読者が無視できる形）を既定とする（pydantic `extra="ignore"` で前方互換。#36 gen_id 冪等化が好例） (正本: `.claude/rules/parallel-workflow.md` §7.2, :191)。
- 契約を変える PR は **`contract` ラベル必須**。**マージ前に依存する全トラックの Issue に予告**しレビュー合意を得る (正本: `.claude/rules/parallel-workflow.md` §4, :140-141)。
- 実装中に他トラックの新しい型/トピック/値が要ると気づいたら（emergent dependency）: ①他トラック内部を import しない→②契約拡張として `contract` PR→③依存全トラックに予告→④契約 PR マージ＝凍結後に双方が実装→⑤待てなければ fake/stub で独立を保ち後で差し替え (正本: `.claude/rules/implementation-and-dependencies.md` §3, :30-34)。
- トピック名・型・JSON スキーマの真実は設計 doc（doc03/08/14）。**コード（pydantic）は検証する側＝勝手にスキーマを拡張しない** (正本: `.claude/rules/parallel-workflow.md` §4, :143)。
- 別トラック所有の正準パスに置くべき成果物を一時 package-local に置く時は、受け取り側 Issue に `move-to:/owner:#N` 予告＋`# TODO(move): ...` マーカー。受け取り側が移管 PR で削除（二重定義の恒久化を防ぐ） (正本: `.claude/rules/implementation-and-dependencies.md` §5, :43-46)。

---

## 3. docs-first（記憶でなくソース）

- **着手前に正本 doc を実 Read** し、どの doc が正本かを `docs/README.md` マップ＋各パッケージ `CLAUDE.md` の「設計ドキュメント」節で特定する (正本: `.claude/rules/docs-first.md` §原則, :9)。
- **引用は必ず「たどれる実ファイル:行」で**。記憶・文脈・他者の要約にある doc 番号をそのまま転記しない（2026-05-30 に計画が mcp_server/VirtualScan/trace_id を取りこぼした実例） (正本: `.claude/rules/docs-first.md` §引用, :46-47)。
- **workflow / subagent / 検索結果の引用も鵜呑みにしない**。重要な doc:行・grep 結果・契約の有無は採用前に自分で `grep`/`Read` で裏取り（agent の「doc13§5.3 非採用」が不正確だった実例） (正本: `.claude/rules/docs-first.md` §引用, :48)。
- **docs に無いトピック名・型・スキーマ・しきい値を発明しない**。docs に未定義の判断が要れば設計の空白＝plan を止めて docs を先に確定（契約なら §2、それ以外は `docs/*` PR） (正本: `.claude/rules/docs-first.md` §必須(plan時), :14)。
- 実装が docs と食い違ったら**コードを docs に合わせる**。docs が誤り/不足なら**先に `docs/*` ブランチの PR** を出してから実装 (正本: `.claude/rules/docs-first.md` §必須(実装時), :18)。
- **「例示(illustrative)」と「凍結契約」を区別**し、ズレたら凍結契約（`warehouse_interfaces` の pydantic）優先。docs の例示 JSON を逐語コピーしない（doc12 §4 旧 state.json が `StateSnapshot` と非互換だった安全バグ＝PR#42） (正本: `.claude/rules/docs-first.md` §必須(実装時), :19 / `docs/dev/03-retrospectives.md` L6, :18)。
- 実装中は**公開 IF（produce/consume・新トピック/型/しきい値）を当該 pkg `CLAUDE.md` に都度記録**する。他セッションが docs だけで「必要なものが既にあるか・誰が出すか」を把握でき、重複実装・暗黙依存を防ぐ (正本: `.claude/rules/implementation-and-dependencies.md` §2, :11-25)。

---

## 4. 周辺 PR の観察・同期（並列は止まっていない前提で動く）

- PR を出す前に **`git merge main`（origin/main 最新）** で取り込み、衝突を手元で解消してから提出する（並列で main は常に進む） (正本: `.claude/rules/parallel-workflow.md` §PR規約, :115 / `.claude/rules/merge-and-communication.md` §1, :9)。
- **マージ済み PR のレビュー / 自分の進捗確認はローカル作業ツリーを正本にしない**。先に `HEAD...origin/main` で遅れを確認し、**origin/main を正本として検証**する（ローカルが古いと幽霊バグを追う） (正本: `.claude/rules/docs-first.md` §引用 = 出典を再検証できる形にする, :47)。
- **本文中に `docs/path:line` で引用する時は、上方や他者の行を書き換えない**。in-body への挿入は行ズレで下流の file:line 引用を silent に腐らせる（#165 が doc12 line92 に+84挿入し cross-track 参照~37件をドリフトさせ、check_consistency が拾えなかった実例） (正本: `docs/dev/03-retrospectives.md` 教訓ログ運用 = 観測→対策→反映先, :3 / `.claude/rules/docs-first.md` §引用 file:line 一次ソース, :47)。
- 周辺で参照していた file:line がズレたら**自分の引用を再 pin**（grep で実位置を取り直す）。記憶の行番号を据え置かない (正本: `.claude/rules/docs-first.md` §引用, :49)。
- 自分の編集が cross-track 参照に影響しうる時（共有 doc の行挿入等）は、影響先トラックの Issue に予告し、可能なら**末尾 / 該当節末尾に追記**して上方の行を動かさない (正本: `.claude/rules/parallel-workflow.md` §7.1, :179)。

---

## 5. マージ規律

- **`main` への直コミット / 直 push 禁止・ブランチ先行**。全変更は feature ブランチ→PR→`main`（マージ先は main 一本。dev/stg/prod はブランチでなく環境） (正本: `.claude/rules/merge-and-communication.md` §1, :8-9 / `.claude/rules/parallel-workflow.md` §1, :12)。
- **マージは必ず PR を作成してから**。PR を作らないマージ、および PR 作成と同一ターンの即時 self-merge は禁止。手順を分ける: **①PR 提出 → ②CI 緑・レビュー可視（PR を可視化）→ ③別ステップでマージ** (正本: `.claude/rules/parallel-workflow.md` §PR規約, :113)。
- **マージ条件をすべて満たす**: `colcon build` 通過／安全機構（Emergency Guardian / Policy Gate）unit テスト通過／契約後方互換（破壊的なら `contract` ＋依存トラック合意）／レビュー承認 (正本: `.claude/rules/parallel-workflow.md` §PR規約 マージ条件, :123-127)。
- **マージ順は doc17 §6**: `feat/repo-skeleton` → 独立トラック随時（llm-bridge/hw-*/wo-metrics）→ sim 系 → nav-traffic → 統合E2E (正本: `docs/architecture/17-development-workflow.md` §6, :145-147)。
- **squash merge 推奨**。マージ後は worktree とブランチを掃除 (正本: `.claude/rules/parallel-workflow.md` §PR規約 マージ後, :130-131)。
- **掃除のマージ済み判定は `gh pr view <N> --json state --jq .state` が `MERGED` か**で行う。squash は別コミットになり `git branch --merged` は偽陰性。stale は `git branch -D` ＋ `git push origin --delete` で即削除 (正本: `.claude/rules/parallel-workflow.md` §7.3, :195 / `docs/dev/03-retrospectives.md` L4, :16)。
- 巨大 PR を避け **1 PR = 1トラック = 1 epic Issue** のレビュー可能単位に分割。WIP は Draft PR (正本: `.claude/rules/parallel-workflow.md` §PR規約, :114, :121)。

---

## 6. worktree 衛生

- **1セッション = 1 worktree = 1ブランチ**。同一ブランチ／同一ディレクトリを2セッションで同時に触らない (正本: `.claude/rules/parallel-workflow.md` §1, :11)。
- **同一マシンの並列は必ず worktree**（新規 clone でない）。新規 clone は別マシン（＝Jetson 実機）のみ (正本: `.claude/rules/parallel-workflow.md` §1, :14)。
- **同一ブランチを2 worktree で checkout しない**（git が拒否する）。`main` worktree で開発しない（統合専用） (正本: `.claude/rules/parallel-workflow.md` §1 禁止事項, :40-41)。
- 作成は最新 main 基点: `git worktree add ../mwr-<track> -b <branch> main`。フォルダ `../mwr-<track>`／ブランチ `feat/<track>` or `hw/<track>`（doc16 §9 ブランチ表に従い勝手に短縮・改名しない） (正本: `.claude/rules/parallel-workflow.md` §1 作成CL, :20-25 / `docs/architecture/16-repository-and-conventions.md` §9, :178-182)。
- **session 名を付ける**: `claude -n "mwr-<track>"`（`git worktree list` と一致）。`--session-id <uuid>` は表示名でなく transcript ID＝別物 (正本: `.claude/rules/parallel-workflow.md` §session命名規約, :59-60)。
- **同一 worktree+branch に2自律セッションを起動しない（dual-session collision）**。HEAD リセット・編集途中コミットの捕捉・内部不整合コミットが起きる。reflog の `reset: moving to` ＋他者作成ファイル/コミットで検知したら **STOP・自分の差分を patch 保全・user へ surface** (正本: `.claude/rules/parallel-workflow.md` §1, :11 = 同一ディレクトリ同時禁止 / `.claude/rules/session-orchestration.md` §2 編集境界の非衝突, :24-29)。
- 破棄時: `git worktree remove`（未コミット残があると拒否）→`git branch -d`→`git worktree prune` (正本: `.claude/rules/parallel-workflow.md` §1 破棄CL, :32-37)。

---

## 7. 連絡・surface

- **連絡は GitHub の Issue / PR コメントで行う**（チャットでなく記録に残し後から追える）。独立ターミナルセッションへの直接 input 注入は不可＝GitHub が**唯一の再起動耐性チャネル** (正本: `.claude/rules/merge-and-communication.md` §2, :21 / `.claude/rules/session-orchestration.md` §0, :11, :17)。
- **すべての PR 本文・Issue・コメントの先頭行に worktree タグ**: `[worktree: mwr-<track> | branch: <branch> | track: #N]`。worktree 名は `git worktree list`、ブランチ名は `git rev-parse --abbrev-ref HEAD` (正本: `.claude/rules/merge-and-communication.md` §2, :26-33)。
- **worker は自動で気づかない＝毎サイクル冒頭で自 Issue を poll** する（`gh issue view #N --json comments`）。orchestrator→worker は post、worker は pull (正本: `.claude/rules/session-orchestration.md` §0③, :13 / §3, :34)。
- `SendMessage` の宛先は**自セッションが spawn した subagent / team teammate のみ**。別ターミナルの独立セッションには届かない＝それを前提にした設計をしない (正本: `.claude/rules/session-orchestration.md` §0②, :12 / §7, :59)。
- **迷ったら orchestrator / 所有 Issue に surface**。契約変更・編集境界の疑義・行ドリフトは黙って進めずコメントで予告する (正本: `.claude/rules/parallel-workflow.md` §7.1, :179 / §4, :141)。

---

## 8. 外向きアクション（不可逆操作は承認制）

- **`gh issue create` / `gh ... comment` / push などの外向きアクションはユーザー承認なしで実行しない**。orchestrator→worker 指示も「ドラフト→ユーザー承認→`gh` で post」 (正本: `.claude/rules/session-orchestration.md` §7, :60 / §0, :17)。
- **`settings.json` / hook の配線・有効化は人間専有**。エージェントによる settings.json 自己改変は禁止（L2 で hook 自己追加が拒否された実例） (正本: `.claude/rules/parallel-workflow.md` §1, :61 / `.claude/rules/session-orchestration.md` §7, :61 / `docs/dev/03-retrospectives.md` L2, :14)。
- reversible（PR 作成）と irreversible（マージ / 公開コメント / 設定変更）を分け、後者は操作ごとに明示承認（L1 で曖昧一括実行が classifier にブロックされた実例） (正本: `docs/dev/03-retrospectives.md` L1, :13)。
- **`.claude/**` の main 直編集 / 直 push をしない**（governance ブランチ→PR） (正本: `.claude/rules/session-orchestration.md` §7, :62)。

---

## 9. 安全

- **認証情報・APIキー・WiFiパスワードをコミットしない**。Isaac Sim 設定にクラウド GPU 認証情報を含めない (正本: `.claude/rules/safety.md`, :3, :6)。
- **ロボット速度制限をコードで強制する（ミニチュアスケール最大 0.3 m/s）** (正本: `.claude/rules/safety.md`, :4)。
- **安全機構（Emergency Guardian / Policy Gate）は unit テスト通過が必須（R-26）**。マージ条件に含む (正本: `.claude/rules/parallel-workflow.md` §PR規約 マージ条件2, :125)。
- 実機デモ前に緊急停止ロジックをテストする (正本: `.claude/rules/safety.md`, :5)。

---

## 10. 完了の規律

- **完了は二値ゲート**。下記をすべて満たし、結果を PR の確認項目に明記して初めて「完了（納期）」と宣言する。未達なら未完了として扱う (正本: `.claude/rules/parallel-workflow.md` §1.1, :53)。
  1. 実装が今も docs と一致するか**再照合**（実装↔docs） (正本: `.claude/rules/docs-first.md` §必須(同期), :26)。
  2. `python3 scripts/check_consistency.py` が **0 ERROR**。ERROR はコードでなく docs を凍結契約に合わせて直す。WARN は要レビュー＝所有トラック判断は勝手に直さず surface に留める (正本: `.claude/rules/consistency-check.md` §必須(編集後), :17-22)。
  3. 機械で拾えない意味的・doc 跨ぎ矛盾は **`/consistency-audit`**（docs-reviewer 隔離実行） (正本: `.claude/rules/consistency-check.md` §必須(plan/PR時), :26)。
  4. **残るおかしな点・未決・暫定値を docs / PR 本文に列挙**（隠さない） (正本: `.claude/rules/parallel-workflow.md` §1.1 step3④, :52)。
- **中途半端で止まらない**: `colcon build` 通過・安全 unit と並ぶ必須ゲートを満たすまで「完了」と言わない (正本: `.claude/rules/parallel-workflow.md` §1.1, :53)。
- **証拠を PR に残す**: テスト欄に `colcon build` 通過・`check_consistency.py` 通過・安全 unit（R-26）を明記。契約変更の有無を本文に書く (正本: `.claude/rules/merge-and-communication.md` §3, :38 / `.claude/rules/parallel-workflow.md` §PR規約 作成時, :119)。

---

## 11. 環境・実行 gotcha

- **WAREHOUSE_ENV で環境選択**（dev | stg | prod、未設定は dev）。接続先 URL・パス・モード・sim/実機は config から読み、環境名・エンドポイント・キーをコードにハードコードしない (正本: `.claude/rules/environments.md` §必須)。
- **設定は base + overlay**: `config/warehouse.base.yaml` → `config/$WAREHOUSE_ENV/warehouse.yaml` → 環境変数（後勝ち）。環境差分のみ `<env>/` に書き、共通値を base に書かない (正本: `.claude/rules/environments.md` §必須)。
- **secrets は `config/<env>/.env`（`**/.env` で gitignore・コミット厳禁）**。リポジトリに置くのは `.env.example`（プレースホルダのみ）。dev/stg/prod で別キーを使う (正本: `.claude/rules/environments.md` §Secrets / `.claude/rules/safety.md`, :3)。
- **ROS / colcon / launch 検証は tiryoh コンテナ必須**（host とランタイムが異なる）。pure-python ゲート（pytest / ruff / check_consistency）は Docker 不要・host で回る (正本: `.claude/rules/parallel-workflow.md` §PR規約 マージ条件1 `colcon build`, :124 / 環境前提 `docs/architecture/16-repository-and-conventions.md` §9, :178)。
- **host python の版差に注意**: pytest 等は `python3.12`（host の `python3` は別系）、ruff は `python3 -m ruff` で起動する。版を取り違えると import / 文法エラーで誤判定する（実行系メモは local-memory / 各 pkg `CLAUDE.md` を確認） (正本: `.claude/rules/consistency-check.md` §必須(編集後) `python3 scripts/check_consistency.py`, :17-20)。
- コンテナでフルスタックを起動する時は **`WAREHOUSE_CONFIG_DIR` 等の必須 env を先に通す**（未設定だと guardian 等が起動時に落ちる）。再利用 gotcha は各 pkg `CLAUDE.md` produce/consume と TODO 節で確認 (正本: `.claude/rules/implementation-and-dependencies.md` §2 前提・未確定(TODO), :21-22)。

---

## 自己改善（このドキュメントの育て方）

- 並列をスムーズにする学び / 改善は **PR を立てて本ドキュメントに追記**（governance ブランチ＝`.claude/**`・`docs/**` は governance 単一所有）。PR 本文に**作成意図＋周辺の詳細**（観測・対策・反映先）を必ず書く (正本: `.claude/rules/parallel-workflow.md` §7.1 governance 所有, :187 / `docs/dev/03-retrospectives.md` 教訓ログ運用, :3)。
- **追記は EOF または該当節末尾に**。他者の行・上方の file:line 引用を書き換えない（#165 の行ドリフト / dual-session collision の教訓）。in-body 挿入は下流参照を silent に腐らせる (正本: `docs/dev/03-retrospectives.md` L6 / 行ドリフト運用, :18 / `.claude/rules/docs-first.md` §引用 file:line 一次ソース, :47)。
- 1エントリの書式: `- [YYYY-MM-DD][worktree タグ] 学び`。
- **緊急の運用メモは Issue / PR コメントで surface**（即時性が要るものを doc 追記まで待たない。GitHub が唯一の再起動耐性チャネル） (正本: `.claude/rules/session-orchestration.md` §0③, :13)。
- **設計正本は `docs/`、変更は PR 必須**。secrets は本書にも一切書かない (正本: `.claude/rules/docs-first.md` §原則, :8 / `.claude/rules/safety.md`, :3)。

### 学びログ

<!-- EOF にこの形で追記。上方の行は触らない。 -->
<!-- - [YYYY-MM-DD][worktree: mwr-<track> | branch: <branch> | track: #N] 学び -->
