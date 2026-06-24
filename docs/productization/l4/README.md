# L4 Productization Skeleton

作成日: 2026-06-24

> **状態**: 設計提案。ここでは L4 Robotics Bridge Super-Box の内部 sub-box、
> transport、seam を、商用案件で再利用できる単位へ分解する。新しい config key、
> ROS topic、REST API、`warehouse_interfaces` frozen contract は追加しない。

## 位置づけ

`docs/productization/l4/` は、`docs/productization/02-l4-robotics-bridge-box.md`
の下位詳細である。`02` は L4 全体の境界、ここは L4 内部の sub-box ごとの
商用再利用 skeleton を扱う。

L4 では、model や provider を直接安全経路へ接続しない。すべて
Robotics Bridge Super-Box の入力、trace、timeout、audit、L3 handoff、`action_map`
を通す。

## ファイル構成

| ファイル | 内容 |
|---|---|
| [model-transport-adapter.md](model-transport-adapter.md) | LLM / ER / VLA / STT / Vision の Model Transport / Adapter sub-box、Hermes-first 方針、再利用可能箇所、商用化注意点 |
| [model-transport-adapter.html](model-transport-adapter.html) | Model Transport / Adapter の詳細図。`layer-l4-detail.html` の Model Adapter 部分をさらに細分化 |

## L4 skeleton

| L4 内部要素 | 種別 | 商用保管するもの | 現状 |
|---|---|---|---|
| Input Context | sub-box | input manifest、artifact refs、stale / missing / secret policy、fixture | `situation.py` は実装済み。audio / image / calibration ref は未凍結 |
| Model Transport / Adapter | sub-box + transport | adapter registry、provider request template、transport selection、raw output recorder、timeout / 0 dispatch fixture | commander LLM の Hermes transport は実装済み。ER / VLA / STT registry は proposal |
| Fusion | optional sub-box | disagreement policy、confidence fusion、operator clarification、reason_code fixture | ER 単体では pass-through。ER + VLA で有効化 |
| L3 Handoff | seam | raw model output から L3 Planning Core へ渡す内部表現、禁止 field reject | 未凍結。L3 側 docs と合わせて凍結する |
| action_map / MCP dispatch | seam | `Command` -> ToolCall 写像、`gen_id` / `idempotency_key` mint、accepted-motion handoff | 実装済み |
| Trace / Langfuse | demoted into Super-Box | root trace、generation span、MCP span、Eval score join | Bridge-owned 実装済み。Hermes plugin は HLF-G0〜G5 後に再評価 |

## レイヤ別 skeleton の増やし方

各 layer で詳細 skeleton を増やす場合は、次の型にそろえる。

1. `docs/productization/<layer>/README.md` に layer 内の box / sub-box / seam 一覧を書く。
2. 1 box につき `kebab-case.md` を作り、再利用可能な箇所、Bridge-owned / site-owned の境界、商用化注意点、fixture、acceptance gate を書く。
3. 図が必要なものは同名 `.html` を作る。図は `.md` の正本を補助するだけで、凍結契約の一次ソースにしない。
4. 新しい frozen contract、topic、config key が必要になったら、productization docs だけで決めず owner docs と contract PR に分ける。

## 商用化の共通注意点

- provider や model 名を顧客案件の business logic に直書きしない。
- site-specific な location、calibration、safety threshold、operator policy は site profile と fixture に分離する。
- API key、endpoint、customer data、raw audio / image の retention は顧客ごとの契約・監査要件に従う。
- model fallback を有効にする場合、比較 run と本番耐障害 run を分ける。
- observability は fail-open とし、Langfuse / OTLP / storage 障害で robot motion path を止めない。
- model output は提案であり、motion 採用判定は L3 / Governance / Safety が行う。
