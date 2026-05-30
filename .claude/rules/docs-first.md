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

## References

- [docs/README.md](../../docs/README.md) ドキュメントマップ / [docs/STATUS.md](../../docs/STATUS.md)
- [doc16 §3](../../docs/architecture/16-repository-and-conventions.md)（契約の真実は docs）
- [parallel-workflow.md §4](parallel-workflow.md) / [implementation-and-dependencies.md](implementation-and-dependencies.md)
