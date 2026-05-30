# docs 中心主義（docs-first）ルール

> plan・実装・レビューは常に **docs を正本（source of truth）** として行う。コード（pydantic / 実装）は docs を**検証・具現する側**であり、docs に無い契約・トピック・スキーマ・しきい値を発明しない。
> 関連: [parallel-workflow.md §4](parallel-workflow.md)（契約変更）/ [implementation-and-dependencies.md](implementation-and-dependencies.md)（produce/consume 記録）/ [docs/README.md](../../docs/README.md)（ドキュメントマップ）/ [doc16 §3](../../docs/architecture/16-repository-and-conventions.md)。

## 原則

- **真実は docs**。トピック名・型・JSON スキーマ・しきい値・トポロジの正本は設計 docs（doc03/08/12/13/14/15/16 等）。コードはそれを検証する側（doc16 §3 / parallel-workflow.md §4.4 再掲）。
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
- 実装スライス完了時に **docs 再照合チェックポイント**（実装が今も docs と一致するか）を PR の確認項目に含める。

## やってはいけない

- docs を読まずに plan / 実装を始める。
- docs に無いトピック名・型・スキーマ・しきい値を発明する。
- 実装の都合で凍結 docs / 契約を黙って変える（変更は docs PR / contract PR 経由）。
- docs の「例示」を「凍結契約」と取り違えてコピーする。

## plan 手順（plan 作成時の固定書式）

- plan の各ステップは **`[何をするか] — 根拠 doc(番号:節/行) — 検証方法`** の3点組で書く（例: 「State Cache 100ms 書出 — doc12 §4 / doc16 §4共有パス — unit: 偽生値→StateSnapshot.model_validate 通過」）。**根拠 doc を書けないステップ＝設計の空白** → そのステップで plan を止め、`docs/*`（契約なら contract）PR を先行。
- JSON / 型 / しきい値を扱うステップは、出所が **(a) 凍結契約 `warehouse_interfaces` の pydantic** か **(b) docs の例示 JSON** かを明記。両者がズレたら **凍結契約が優先**。着手前に `warehouse_interfaces/schemas.py` を `grep` で確認する（doc12 §4 旧 state.json が `StateSnapshot` と非互換だった事故=PR#42、[retrospectives L6](../../docs/dev/03-retrospectives.md)）。
- plan 中に **docs 同士の矛盾 / 正本の沈黙** を見つけたら、実装 plan を止めて docs を先に確定する（コードで暗黙に解釈しない）。

## References

- [docs/README.md](../../docs/README.md) ドキュメントマップ / [docs/STATUS.md](../../docs/STATUS.md)
- [doc16 §3](../../docs/architecture/16-repository-and-conventions.md)（契約の真実は docs）
- [parallel-workflow.md §4](parallel-workflow.md) / [implementation-and-dependencies.md](implementation-and-dependencies.md)
