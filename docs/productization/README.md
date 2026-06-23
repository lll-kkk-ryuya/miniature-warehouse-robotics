# Productization: 商用再利用 Box 設計

作成日: 2026-06-22

> **状態**: 設計提案。ここでは既存 Mode A/B/C、Mode X-ER、Mode X-ER-VLA、ROS 2 下位層を、商用案件で再利用できる box として整理する。本文は config key、ROS topic、REST API、`warehouse_interfaces` frozen contract を追加しない。

## 位置づけ

`docs/productization/` は、特定 mode の実装手順ではなく、案件ごとに再利用する機能部品の保管単位を定義する。

商用案件では、顧客ごとに以下が変わる。

- 現場 layout、known location、map、calibration
- robot fleet、driver、MCU、sensor
- model provider、ER / VLA / STT の実行環境
- 業務 policy、権限、安全基準、監査要件
- KPI、report、trace sink

一方で、以下は再利用したい。

- L4 orchestration / LLM Bridge / Robotics Bridge Super-Box
- L3 planning core
- command governance / Policy Gate
- Nav2 / Open-RMF / safety / micro-ROS の実行経路
- `eval_sdk` / observability
- box ごとの decision log / reject reason / emergency event の集計

## なぜ mode 配下に置かないか

商用再利用 box は Mode X-ER だけのものではない。Mode A/B の LLM Bridge、Mode C の Open-RMF、Mode X-ER の ER adapter、Mode X-ER-VLA の VLA fusion、下位の Nav2 / ESP32 / `eval_sdk` を横断する。そのため `docs/productization/` を mode-independent な設計章として置く。

## ファイル構成

| ファイル | 内容 |
|---|---|
| [01-commercial-box-map.md](01-commercial-box-map.md) | 商用再利用 box の全体 map、repo 実体、差し替え点 |
| [02-l4-robotics-bridge-box.md](02-l4-robotics-bridge-box.md) | LLM Bridge / Robotics Bridge Super-Box / ER / VLA / Langfuse の L4 box 設計 |
| [03-l3-planning-core-box.md](03-l3-planning-core-box.md) | Validator / Visual Resolver / Task Graph Executor / Command Compiler の商用 box 設計 |
| [04-box-storage-and-reuse-guidelines.md](04-box-storage-and-reuse-guidelines.md) | box の保管方法、成熟度、site profile、fixture、分割基準 |
| [05-decision-observability-and-tooling.md](05-decision-observability-and-tooling.md) | L3 / Contract / Governance / Safety の decision log、reject 集計、既存 tool と自作範囲 |
| [06-oss-reuse-and-box-small-designs.md](06-oss-reuse-and-box-small-designs.md) | L4 sub-box / Traffic / Navigation / Hardware / Eval の小設計と OSS 再利用方針 |
| [07-layer-tool-decision-matrix.md](07-layer-tool-decision-matrix.md) | layer / box ごとの OSS / tool 採用・候補・不採用・要 spike・採用条件 |
| [08-navigation-hardware-eval-gates.md](08-navigation-hardware-eval-gates.md) | Navigation / Hardware / Eval の acceptance gate と reason_code catalog |

## 読み方

まず [01-commercial-box-map.md](01-commercial-box-map.md) で全体の box 境界を読む。
L4 / L3 の設計粒度は [02](02-l4-robotics-bridge-box.md) と
[03](03-l3-planning-core-box.md) を正本にする。商用保管の型は
[04](04-box-storage-and-reuse-guidelines.md) に寄せる。

decision event と reject / emergency の集計方針は [05](05-decision-observability-and-tooling.md)、
OSS 再利用と小設計の広がりは [06](06-oss-reuse-and-box-small-designs.md)、
layer ごとの採用判断は [07](07-layer-tool-decision-matrix.md)、
Navigation / Hardware / Eval の下位 gate は
[08](08-navigation-hardware-eval-gates.md) を見る。

`reason_code` や acceptance gate は現時点では proposal catalog であり、
product contract ではない。実装や顧客案件へ昇格する場合は、owner doc、
package guidance、contract PR に分ける。

## 基本方針

商用 box は、model や robot を直接信頼しない。

```
L4 Input / Model Adapter / LLM Bridge
  -> L3 Planning Core
  -> Contract / Governance
  -> Traffic
  -> Navigation
  -> Safety
  -> Hardware
```

ER / VLA / LLM から Nav2、ROS topic、Jetson service、ESP32、`/cmd_vel` を直接叩かない。L4 は提案と trace を作り、L3 は command 候補へ変換し、L2 以降が実行許可と安全制御を担当する。

## LLM Bridge を含める理由

既存の `warehouse_llm_bridge` は単なる LLM 呼び出し wrapper ではない。サイクル制御、state 生成、Hermes 呼び出し、Langfuse trace、`action_map`、`gen_id` / `idempotency_key` 注入、MCP dispatch seam を持つ。

Mode X-ER / Mode X-ER-VLA では、これを **Robotics Bridge Super-Box** として拡張する。Gemini Robotics-ER や VLA は Bridge 管理下の adapter から呼び出し、raw output は L3 Planning Core に渡す。

Hermes が対象 model / audio / image API を安全に扱える場合は、Bridge から Hermes 経由で呼ぶ。Hermes が扱えない modality や runtime がある場合は、Bridge 管理下の direct adapter で呼ぶ。ただしどちらの場合も、trace、timeout、audit、L3 接続、MCP dispatch は Bridge 側が所有する。

ER / VLA / STT adapter、prompt / request template、raw output recorder、Langfuse observation policy は、商用資産として **L4 Robotics Bridge Super-Box の配下に保管する**。これは Hermes runtime に model 本体を保存するという意味ではない。Hermes は対応 provider への transport 候補であり、商用 box の所有境界は Robotics Bridge 側に置く。

Hermes Agent 公式機能で扱える provider transport、fallback、STT、vision transport、MCP 接続、plugin 拡張は Super-Box 内の Hermes-managed area として利用する。ただし、robotics 固有の Input Context、ER/VLA fusion、L3 handoff、`action_map`、`gen_id` / `idempotency_key` 注入、0 dispatch safety、Langfuse/Eval join は Bridge-owned area として残す。

## 非目標

- 商用 product repo を今すぐ分離しない。
- VLA output を velocity、trajectory、motor command として直接実行しない。
- customer site 固有の location / threshold / topic をここで凍結しない。
- `warehouse_interfaces` に新 schema を追加しない。
