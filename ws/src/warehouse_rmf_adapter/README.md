# warehouse_rmf_adapter

Mode C 案A（R-44 採用方針）の自作 **EasyFullControl Fleet Adapter** — 中央 namespaced
`/bot{n}` Nav2 を zenoh 無しで直駆動する設計の置き場。

> **GATE-前 設計スキャフォールド（実装なし）。** 実装・`colcon build`・apt・live 駆動は
> **R-38 メモリゲート（#187）通過後**（docs/mode-c/11c-traffic-mode-c.md:273 §3.5 D）。

- 責務・produce/consume・残未決・編集境界: [CLAUDE.md](CLAUDE.md)
- 設計骨子モジュール: `warehouse_rmf_adapter/fleet_adapter.py`
- 設計正本: [docs/mode-c/11c-traffic-mode-c.md](../../../docs/mode-c/11c-traffic-mode-c.md) §3.5
  ＋ 同ファイル末尾「付録: §3.5 GATE-前 ステータス」
- track #180（nav-traffic）/ R-44 = #117 / R-38 ゲート = #187
