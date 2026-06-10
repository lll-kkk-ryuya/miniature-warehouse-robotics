# 04. docs↔code 整合システム（consistency system）

> **目的**: docs と凍結契約（`warehouse_interfaces` / `warehouse_description` / `config`）のドリフトを、**人手の監査に頼らず都度・自発的に**検出する。
> 背景: 多エージェント監査で 17〜25 件の doc↔code 矛盾（battery `<`/`<=`、`ROBOT_RADIUS` 0.1/0.075、`/llm/*` 型、STATUS SHA 陳腐化、設計跨ぎ矛盾）を手作業で発見した。これを仕組み化する。
> 親ルール: [docs-first.md](../../.claude/rules/docs-first.md)（思想）/ 実行担保: [consistency-check.md](../../.claude/rules/consistency-check.md)。

## 1. 設計原則 — 「ルールは文脈、hook/CI は強制」

Claude Code の公式仕様上、`CLAUDE.md` / `.claude/rules/*.md` は**毎セッション自動ロードされる文脈**だが「**強制ではない**」（docs 明言: *"must run at a specific point → write it as a hook"*、[hooks](https://code.claude.com/docs/en/hooks)）。したがって整合検査は **2 層**で組む:

| 層 | 何 | 強制力 | 捕まえる矛盾 |
|---|---|---|---|
| **決定論（script）** | `scripts/check_consistency.py` を **pre-commit / CI / Claude hook の3箇所**から呼ぶ | 強い（ERROR で停止） | 数値・トピック型・場所キー・派生値(SHA)の**単純ドリフト** |
| **モデル判定（skill）** | `/consistency-audit`（`docs-reviewer` を隔離実行） | 判定（提案） | 意味的・**doc 跨ぎ**の矛盾（例: doc08 `/stop` ↔ 同期 transport） |

> **1つの checker を 3箇所から呼ぶ**（DRY）。人間の commit 時（pre-commit）・PR 時（CI、**Claude 非依存の唯一の durable 層**）・Claude の編集直後（PostToolUse hook で自己修正）すべてで効く。

```
                   ┌─ pre-commit            （人間のローカル commit 時）
scripts/           ├─ CI ci.yml: consistency （PR 時・durable／最終保険）
check_consistency.py ─┤
(単一の真実)         └─ .claude/hooks PostToolUse（Claude 編集直後・自己修正）  ← 配線は人間
```

## 2. 決定論チェッカー `scripts/check_consistency.py`

- **純 stdlib**（`ast` + `re` + `git`）。pydantic/ROS/pyyaml を import せず、**ゼロインストールでどこでも高速**（pre-commit/hook 友好）。
- 単一ソースを **AST で読む**（複製しない）: `safety.py`（0.3 / 10 / 20）・`robot_dimensions.py`（`ROBOT_RADIUS=0.075`）・`locations.py`（`KNOWN_LOCATIONS` 9キー）・`config/warehouse.base.yaml`。
- **重大度の精度設計**:
  - **ERROR**（CI 赤）= 明白なドリフト: `A1` ROBOT_RADIUS 不一致 / `B1` config↔KNOWN_LOCATIONS 不一致 / `B2` 型表で `/llm/*`・`/wo/mission` が「カスタム」（doc16 §3 で `std_msgs/String` 確定）/ `B3` 旧 `laser` 系のセンサーフレーム名（凍結は `lidar_link`）/ `C1` STATUS が origin/main に**無い** SHA を固定。
  - **WARN**（surface のみ・CI 緑）= 要レビュー: `A2` battery 境界 `<`/`<=`（緊急停止文言で意図的な可能性・別トラック所有）/ `C1` STATUS SHA が**古い祖先**（次回 STATUS 更新で追従）/ `B4` cross-file `docNN:LINE`・`<path>.md:LINE` 参照の drift（参照先が EOF 超過／空行／表区切り／水平線＝アンカー喪失。#165 クラス。参照元の所有トラックが re-pin、bulk 自動修正しない §7.1）。
- **誤検知ガード**: `矛盾/誤り/旧/conflict/deprecated/~~` を含む説明行は除外。`B2` は**テーブル行限定**（doc16 §3 の解決文＝散文は除外）。
- 使い方:
  ```bash
  python3 scripts/check_consistency.py            # 全 docs 走査・人間可読
  python3 scripts/check_consistency.py --json      # hook 連携
  python3 scripts/check_consistency.py --report /tmp/warehouse/consistency-report.txt  # SessionStart 注入用
  python3 scripts/check_consistency.py docs/foo.md # 該当 doc のみ（hook/pre-commit）
  ```
  終了コード: `0`=ERROR 無し（WARN 可）、`1`=ERROR あり。

### 拡張（不変条件を増やす）
`CHECKS` リストに関数を1つ足すだけ。新しい単一ソースは AST 抽出関数（`_module_consts`）を再利用。**docs 側の値を直接 import / 参照し、ハードコードしない**こと。

## 3. モデル判定 `/consistency-audit` skill

[.claude/skills/consistency-audit/SKILL.md](../../.claude/skills/consistency-audit/SKILL.md)。`context: fork` + `agent: docs-reviewer` で**隔離コンテキスト**に重い grep&compare を逃がし findings だけ返す。決定論チェッカーを先に走らせ、その上で**機械では無理な**意味的・doc 跨ぎ矛盾（凍結契約 vs 例示 JSON、トピック契約 vs 実コード、doc08/`stop`・doc08a/`blocked` 類、鮮度、リンク腐敗）を judgment 監査する。
- 意味的矛盾は**解決方針を決め打ちせず**、docs に「⚠️ 未解決」注記 + **追跡 Issue**（運用例: #55。#54 もこのパターンで起票され、後に解決＝/stop 撤回）。

## 4. Claude Code hook 配線

> **配線先で規約を満たす**: 共有の **`.claude/settings.json`（コミット）は引き続き人間専任**（hook は任意 shell 実行のため。[.claude/hooks/README.md](../../.claude/hooks/README.md) / runbook 02）。一方 **phase-1 の (1) PostToolUse 自己修正は `.claude/settings.local.json`（ローカル・gitignore）に配線済み**（オーナー承認・#56 系。共有設定を変えないので human-only 規約と非衝突）。実体は専用ラッパー [`.claude/hooks/consistency-posttooluse.py`](../../.claude/hooks/consistency-posttooluse.py)（ERROR のみ block・失敗時は必ず非ブロック）。`workspace trust` 承認後に発火（[hooks-guide](https://code.claude.com/docs/en/hooks-guide)）。
>
> (2) SessionStart 注入 / (3) Stop の `type:"agent"` 判定は**任意の次フェーズ**（experimental 含む）。共有 settings.json に入れるなら人間が追記する。下記はその雛形。

```jsonc
{
  "hooks": {
    // (1) 編集直後の自己修正: ERROR を additionalContext で返し、次のモデル呼び出し前に止める
    "PostToolUse": [
      { "matcher": "Edit|Write|MultiEdit",
        "hooks": [ { "type": "command",
          "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/consistency-posttooluse.py || true" } ] }
    ],
    // (2) 毎セッション開始: 直近の整合レポートを短く注入（トークン節約のためレポートのみ）
    "SessionStart": [
      { "matcher": "startup|resume",
        "hooks": [ { "type": "command",
          "command": "python3 \"$CLAUDE_PROJECT_DIR\"/scripts/check_consistency.py --report /tmp/warehouse/consistency-report.txt >/dev/null 2>&1; echo 'docs-first 整合レポート:'; cat /tmp/warehouse/consistency-report.txt 2>/dev/null" } ] }
    ],
    // (3) 終了前の judgment 保険（実験的 type:agent。docs-reviewer 相当の検証を強制）
    "Stop": [
      { "hooks": [ { "type": "agent", "timeout": 120,
          "prompt": "scripts/check_consistency.py を実行し、さらに docs の閾値・トピック名・例示 JSON が warehouse_interfaces 凍結契約と矛盾しないか grep 照合。矛盾があれば {\"ok\":false,\"reason\":\"<矛盾>\"} を返す。" } ] }
    ]
  }
}
```
- **(1)** が最重要（編集の度に自己修正）。**(3)** は `type:"agent"` が experimental のため任意。配線は段階導入でよい（まず CI + pre-commit + (1)）。
- 注: hook は shell/agent を起動するだけで、**`.claude/agents/` のサブエージェントを直接呼べない**（[sub-agents](https://code.claude.com/docs/en/sub-agents)）。`docs-reviewer` の本格監査は**モデル**が `/consistency-audit` 経由で起動する（rule で誘導）。

## 5. 既知の限界・運用

- 決定論チェッカーは**列挙した不変条件のみ**検出。新しい契約値を凍結したら `CHECKS` に追加する（さもないと silent gap）。
- **`B4`（cross-file `doc:line` 参照 drift, #177）**: 構造的に壊れた参照のみ WARN（参照先が EOF 超過／空行／表区切り／水平線＝アンカー喪失）。アンカー*本文*の一致は検証しない（凍結アンカーマップが無い＝text judgment は `/consistency-audit` 側）。曖昧な doc 番号（例 `03` を複数ファイルが共有）は flag せず skip、**full-scan のみ**（per-file hook / pre-commit の `only` モードでは no-op）。FN 許容は既存方針（narrow FN）と同じ。re-pin は**参照元の所有トラック**（#165 由来の cross-track drift は bulk 自動修正しない §7.1）。本ブランチの full-scan で **64 WARN**（うち B4=62 が実在の #165 クラス drift＝warehouse_sim が指す doc12 行207 の空行化ほか doc08/doc13 も複数、残り C1 STATUS-SHA ×2）を surface（doc 内では参照例を `docNN:LINE` 形で literal 化しない＝自検出を避ける）。
- **WARN を一括自動修正しない**（境界条件は所有トラックの設計判断。surface に留める。parallel-workflow §7.1）。
- 意味的監査（skill）はモデル判定＝完全保証ではない。**CI（決定論）が唯一の hard gate**。
- 現状 main で出る WARN（STATUS SHA ×2）は本 PR では**修正せず surface**（鮮度は次回 STATUS 更新で追従）。※ battery 境界 ×5 は #90 で解消済（doc 群を凍結 `safety.battery_is_critical` の `≤ 10%` に整合）。
- **堅牢性**: checker 自身が落ちても（非UTF8・ソース欠落・定数が非リテラル式）traceback で全PRをブロックせず、`Z0-self-error` の **ERROR finding** として可視化する（`read_text(errors="replace")` + `main()` の try/except）。意味的監査は `/consistency-audit` skill 側へ。
- **既知の検出限界（narrow FN・surface 言語が日本語前提の運用で許容）**: ①B1 config パーサは2スペース・top-level `locations:` 前提（4スペース/タブ/ネストにすると無音 no-op）。②引用ブロック内のテーブル行（`> | ... |`）は B2 が拾わない。③`_NEGATION` 除外は日本語中心（英語の "not/old" 等は未対応）。④A1 は `ROBOT_RADIUS`〔=/は/:〕値 の語形のみ（"robot radius is 0.1" のような定数名なし表現は拾わない）。新しい語形のドリフトが出たら該当 check を拡張する。

## References
- 公式: [hooks](https://code.claude.com/docs/en/hooks) / [hooks-guide](https://code.claude.com/docs/en/hooks-guide) / [skills](https://code.claude.com/docs/en/skills) / [sub-agents](https://code.claude.com/docs/en/sub-agents) / [memory](https://code.claude.com/docs/en/memory) / [settings](https://code.claude.com/docs/en/settings)
- 本 repo: [docs-first.md](../../.claude/rules/docs-first.md) / [consistency-check.md](../../.claude/rules/consistency-check.md) / [parallel-workflow.md §7.1](../../.claude/rules/parallel-workflow.md) / [architecture/20-dev-quality-and-testing.md](../architecture/20-dev-quality-and-testing.md)
