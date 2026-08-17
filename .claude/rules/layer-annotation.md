# レイヤ注記（layer-annotation）ルール

> 回答・plan・実装・PR/Issue 本文・レビュー結果で、対象のコード / 設計 / 変更が
> **どの layer（L0–L4・traffic・観測面）に属するか**を常に明記する。
> 発端: 2026-07-11 のユーザー指示（「executor.py はどこの実装？layer のどこ？」という迷子の再発防止）。
> 正準対応表は [docs/productization/01-commercial-box-map.md](../../docs/productization/01-commercial-box-map.md) §レイヤ annotation 対応表（本ルールは表を**複製しない**＝腐敗防止）。

## 原則

- コード・設計・変更に言及するときは、**layer 帰属を括弧で併記**する。
  例: 「`task_graph_executor/executor.py`（**L3** Planning Core・actuation 権限なし）」「`policy_gate.py`（**L2** Governance・`warehouse_mcp_server`）」。
- PR / Issue 本文の「影響範囲」には**対象 layer を明記**する（例: 「L3 のみ・L2/L1/L0 不変更」）。
- layer が変更の安全性を語る: L2/L1/L0（実行許可・運動・物理安全）に触れる変更は、L4/L3（提案・検証）だけの変更より重いレビューを要する。

## 注意（誤帰属の典型源）

- **layer ≠ process**: L3 Planning Core のコードは L4 commander node（`llm_bridge.py` / `x_er_bridge.py`）のプロセス内で動くが、レイヤ帰属は L3 のまま（実行許可なし）。
- **layer ≠ package**: `warehouse_llm_bridge` は L4 と L3 の両方を含む。annotation は package 名でなく file 単位で行う。**同名 `executor.py` が 2 つ併存**（L4=`warehouse_llm_bridge/executor.py`（MCP dispatch）／ L3=`robotics_planning_core/task_graph_executor/executor.py`）。
- **帰属未定は未定と書く**（例: State Cache＝box 帰属未定 F3・暫定 Safety）。断定しない。
- **他のレイヤ体系と混同しない**: 安全レイヤー 4 層（doc12 の Layer 0–3）・時間 3 層とは別軸。読み替えは [docs/productization/11-l2-contract-governance-traffic-box.md](../../docs/productization/11-l2-contract-governance-traffic-box.md) §レイヤ番号の対応を正とする。

## 必須（運用）

- layer が即答できないときは、**正準対応表を実 Read してから**答える（記憶で帰属を断定しない。docs-first.md §引用と同じ規律）。
- 対応表に無い新規 component を実装したら、**同じ PR で対応表に1行追記**する（実装と annotation 正本の同期）。
- traffic 層（`warehouse_traffic`・≥0.15m 通路排他）は L2 Box の Traffic であり、**X-lite / `TRAFFIC_MODE=none` 構成では非アクティブ**——「どの安全網がその構成で効いているか」を layer と合わせて正確に言う。

## References

- [docs/productization/01-commercial-box-map.md](../../docs/productization/01-commercial-box-map.md) §レイヤ annotation 対応表（正準）
- [docs/mode-x-er/01-architecture-and-flow.md](../../docs/mode-x-er/01-architecture-and-flow.md)（レイヤ図）/ [docs/GLOSSARY.md](../../docs/GLOSSARY.md) §3
- [docs-first.md](docs-first.md)（引用・実 Read 規律の親ルール）
