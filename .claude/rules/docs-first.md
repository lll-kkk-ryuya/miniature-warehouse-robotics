# docs 中心主義（docs-first）ルール

> plan・実装・レビューは常に **docs を正本（source of truth）** として行う。コード（pydantic / 実装）は docs を**検証・具現する側**であり、docs に無い契約・トピック・スキーマ・しきい値を発明しない。
> 関連: [parallel-workflow.md §4](parallel-workflow.md)（契約変更）/ [implementation-and-dependencies.md](implementation-and-dependencies.md)（produce/consume 記録）/ [docs/README.md](../../docs/README.md)（ドキュメントマップ）/ [doc16 §3](../../docs/architecture/16-repository-and-conventions.md)。

## 原則

- **真実は docs**。トピック名・型・JSON スキーマ・しきい値・トポロジの正本は設計 docs（doc03/08/12/13/14/15/16 等）。コードはそれを検証する側（doc16 §3 / parallel-workflow.md §4 項目4 再掲）。
- **着手前に正本 doc を読む**。どの doc が正本かは `docs/README.md` のマップ + 各パッケージ `CLAUDE.md` の「設計ドキュメント」節で特定する。

## 必須（plan 時）

- plan の各ステップに **根拠 doc（番号 + 節/行）** を併記する（例: 「50ms reflex = doc12:95-151」）。
- docs に未定義の判断が要るなら、それは設計の空白 → **plan を止めて docs を先に確定**（契約なら parallel-workflow.md §4、それ以外は `docs/*` ブランチの docs PR）。コードで暗黙に解釈して進めない。

## 必須（実装時）

- 実装が docs と食い違ったら、**コードを docs に合わせる**。docs が誤り/不足なら **先に docs を直す PR（`docs/*` ブランチ）** を出してから実装する。
- **「例示(illustrative)」と「凍結契約」を区別**する。両者がズレたら **凍結契約（`warehouse_interfaces` の pydantic）が優先**。docs の例示 JSON を逐語コピーしない（例: doc12 の State Cache JSON 例 vs 凍結 `StateSnapshot` 形）。
- 実装した公開 IF は当該パッケージ `CLAUDE.md` の produce/consume に記録（implementation-and-dependencies.md §2 と一体）。

## 必須（同期）

- docs を更新したら、関連コード / `CLAUDE.md` の参照（doc番号・行）も更新し、リンク腐敗を防ぐ。
- **実装中は docs に記載しながら進める**: 公開 IF（produce/consume・新トピック/型/しきい値）を当該 pkg `CLAUDE.md` に**都度記録**する（[implementation-and-dependencies.md §2](implementation-and-dependencies.md)）。docs から外れた契約/しきい値を発明しない。
- 実装スライス完了時に **docs 再照合チェックポイント**（実装が今も docs と一致するか）を PR の確認項目に含める。
- **完了（納期）の定義 = docs 整合まとめ＋突合ゲート**（session 運用での必須化は [parallel-workflow.md §1.1](parallel-workflow.md)）: スライス完了前に ① docs 再照合（実装↔docs 一致）→ ② `python3 scripts/check_consistency.py` **0 ERROR**（[consistency-check.md](consistency-check.md)）→ ③ 意味的・doc 跨ぎ矛盾は `/consistency-audit` → ④ **残るおかしな点・未決・暫定値を docs / PR 本文に列挙** — を行い、**結果を PR 確認項目に明記してから「完了」とする**。

## やってはいけない

- docs を読まずに plan / 実装を始める。
- docs に無いトピック名・型・スキーマ・しきい値を発明する。
- 実装の都合で凍結 docs / 契約を黙って変える（変更は docs PR / contract PR 経由）。
- docs の「例示」を「凍結契約」と取り違えてコピーする。

## plan 手順（plan 作成時の固定書式）

- plan の各ステップは **`[何をするか] — 根拠 doc(番号:節/行) — 検証方法`** の3点組で書く（例: 「State Cache 100ms 書出 — doc12 §4 / doc16 §4共有パス — unit: 偽生値→StateSnapshot.model_validate 通過」）。**根拠 doc を書けないステップ＝設計の空白** → そのステップで plan を止め、`docs/*`（契約なら contract）PR を先行。
- JSON / 型 / しきい値を扱うステップは、出所が **(a) 凍結契約 `warehouse_interfaces` の pydantic** か **(b) docs の例示 JSON** かを明記。両者がズレたら **凍結契約が優先**。着手前に `warehouse_interfaces/schemas.py` を `grep` で確認する（doc12 §4 旧 state.json が `StateSnapshot` と非互換だった事故=PR#42、[retrospectives L6](../../docs/dev/03-retrospectives.md)）。
- plan 中に **docs 同士の矛盾 / 正本の沈黙** を見つけたら、実装 plan を止めて docs を先に確定する（コードで暗黙に解釈しない）。

## 引用は必ず「たどれる実ファイル:行」で（全タスク共通・必須）

戦略/plan に限らず、**docs を根拠にする全タスク（plan・実装・レビュー・報告・memory・Issue/PR）**で以下を守る。出典は「言ったつもり」でなく**再検証できる形**にする。

- **doc を実際に開いて確認してから引用する**。記憶・文脈・他者の要約にある doc 番号を**そのまま転記しない**（実誤りの温床。2026-05-30 に当初計画が mcp_server/VirtualScan/trace_id を取りこぼした実例 → [retrospectives](../../docs/dev/03-retrospectives.md)）。
- **引用は略記で終えず、たどれる実ファイル:行を付ける**: `docs/architecture/16-repository-and-conventions.md:191` の形（repo-relative path + 行）。「doc16 §9」だけの略記は不可（将来 `grep`/`Read` で再検証できる形にする）。GitHub URL を併記する場合は行ズレに弱い点に注意し、file:line を一次ソースとする。
- **workflow / subagent / 検索結果の引用も鵜呑みにしない**。重要な doc:行・grep 結果・契約の有無は、**採用前に自分で `grep`/`Read` して裏取り**する（2026-05-30 に agent の「doc13§5.3 /v1/runs 非採用」が不正確だった実例）。
- **「編集境界・着手条件・consume/produce・契約の有無・しきい値」は記憶で補完しない**。doc16 §9 ブランチ表と `warehouse_interfaces` 実体を必ず `grep`/`Read` で確認。
- memory に残す主張も同様に **実ファイル:行を併記**して後から検証可能にする。

## References

- [docs/README.md](../../docs/README.md) ドキュメントマップ / [docs/STATUS.md](../../docs/STATUS.md)
- [doc16 §3](../../docs/architecture/16-repository-and-conventions.md)（契約の真実は docs）
- [parallel-workflow.md §4](parallel-workflow.md) / [implementation-and-dependencies.md](implementation-and-dependencies.md)
