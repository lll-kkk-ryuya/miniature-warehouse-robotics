# L0 の battery brownout floor は percent policy と別名・別機構の voltage-based MCU floor（将来 phase・現行 L0 は cutoff 無し）

**Status**: accepted（2026-07-17 user approval・方針決定。実装は将来 phase に defer・cutoff 電圧は Phase-1 実機実測）

L0 firmware（micro-ROS / ESP32）の**現行設計は battery cutoff を持たない**——`/battery` の sensor publish と、MCU 生存不能時の物理切断（[doc12](../architecture/12-infrastructure-common.md):68）のみ。低残量の **percent 3段ポリシー**（`≤10%` critical / `≤20%` low）は **Layer 1**（Emergency Guardian / Policy Gate）が所有し、凍結契約 `battery_is_critical(pct)`（percent 基準・[safety.py](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py):41-43）を L1 が共用する。将来 phase で L0 に足すのは、**最小の over-discharge（brownout）floor** を、percent 基準の凍結 `battery_is_critical(pct)` とは**別名・別機構の voltage-based** floor として持つ方針——通信断でも過放電を MCU 常駐だけで止めるため（既決の comms-loss heartbeat deadman と同型）。cutoff 電圧は Phase-1 実機実測で確定し、docs には**導出ポリシーだけ**残す（値を発明しない）。

## Context / 背景

- **過放電を通信断でも止められるのは MCU 常駐のみ**。上位 ROS / Nav2 / Emergency Guardian は OS・通信・電源に依存して落ちうる。MCU 内で常駐する over-discharge 保護は、既決の comms-loss heartbeat deadman（[doc12](../architecture/12-infrastructure-common.md):79・`H-G6 heartbeat_lost`＝Layer 0 責務）と**同型の論理**（通信非依存の last-line floor）。
- **凍結 `battery_is_critical(pct)` は percent 基準で L1 共用**。[safety.py](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py):41-43 の判定は **0..100 の percent** を取り、Policy Gate（`warehouse_mcp_server`）と Emergency Guardian（`warehouse_safety`, 50ms reflex）が**同一定数を共用**する（[safety.py](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py):3-5）。percent 版を L0 にも作ると (a) **名前衝突**（同名 percent 判定が2層に散る）、(b) **層誤配置**（percent policy は L1 所有＝[doc12](../architecture/12-infrastructure-common.md):244-254）を招く。
- **percent は driver scale に依存**する（`sensor_msgs/BatteryState.percentage` は REP-147 fraction か 0..100 かがドライバ依存＝#44・[safety.py](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py):46-63 / [doc12](../architecture/12-infrastructure-common.md):244-254）。この scale 依存は config + 単一ヘルパで L1 側が吸収する設計であり、通信・config を持たない MCU の last-line floor には不適。ゆえに L0 floor は percent ではなく **voltage-based**（生電圧しきい値）とし、**別名の機構**にする。
- **cutoff 電圧は実測に残す**。セル化学・保護回路（BMS）依存で理論値を発明できない。閾値は Phase-1 実機実測（[doc12](../architecture/12-infrastructure-common.md) §バッテリーポリシー「実機のバッテリー特性に応じて調整」・[`.claude/rules/safety.md`](../../.claude/rules/safety.md) / doc16 §11 の実機 estop テスト）で確定する。

## Decision / 決定

1. **L0 firmware は将来 phase で最小の MCU over-discharge（brownout）floor を持つ**。これは **voltage-based**・**別名**（percent 基準の凍結 `battery_is_critical(pct)` とは**別関数・別機構**）で、通信・OS 非依存の last-line floor（heartbeat deadman と同型）。単方向 cut のみ（motor enable OFF / MCU 保護）で、percent policy の退避・タスク制御は担わない。
2. **percent 3段 policy は引き続き Layer 1 所有**（Emergency Guardian / Policy Gate＝[doc12](../architecture/12-infrastructure-common.md):244-254）。L0 に percent 版を複製しない。凍結 `battery_is_critical(pct)`（[safety.py](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py):41-43）は L1 の単一ソースのまま。
3. **L0 の現行設計は battery cutoff を持たない**。現行 firmware は `/battery` を sensor publish するのみ（`publishBattery`）で、cutoff は持たず、MCU 生存不能時の物理切断（[doc12](../architecture/12-infrastructure-common.md):68）が最終手段。本 ADR は将来 phase の**方針決定**であり、現行実装を変えない。
4. **cutoff 電圧値は Phase-1 実機実測**。docs には**導出ポリシーのみ**を書き、電圧値・`CMD_TIMEOUT` ms 等の実測依存パラメータは発明しない。凍結契約（`MAX_LINEAR_VELOCITY 0.3` build flag＝[safety.py](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py):18 / percent `battery_is_critical` / doc03 トピック name+type）には**触れない・改名しない**。

## トレードオフ / Trade-offs

- **voltage floor（L0）と percent policy（L1）が別軸で共存**する＝二重定義に見えうるが、**層・単位・fail 条件が異なる**（L0 = 生電圧の物理 brownout 保護・通信非依存 / L1 = 正規化 percent の運用退避・config 依存）。単位を percent に揃えて1機構にすると、L0 floor が driver scale・config に依存し、MCU の**通信非依存性を壊す**。
- **L0 に責務追加** = firmware complexity 増（ただし最小・単方向 cut のみ・percent 三段は持ち込まない）。
- **実装を将来 phase に defer** = 現行実機テストでは brownout 保護は電源側 BMS ／物理切断（[doc12](../architecture/12-infrastructure-common.md):68）依存。cutoff 電圧が未実測（Phase-1）ゆえ今 phase では値を確定できない。

## Considered Options / 却下

- **percent 版判定を L0 にも複製**：却下。凍結 `battery_is_critical(pct)`（[safety.py](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py):41-43）と**名前衝突**し、percent policy を L1 所有とする層配置（[doc12](../architecture/12-infrastructure-common.md):244-254）に反する。percent は driver scale 依存（#44・[doc12](../architecture/12-infrastructure-common.md):244-254）で、通信・config を持たない MCU floor に不適。
- **L0 に何も足さず L1 percent policy のみに委ねる**：不十分。通信断／Guardian 停止時に過放電を止める last-line floor が無い（常駐は L0 のみ）。ゆえに将来 phase で最小 voltage floor を足す方針を**今 ADR 化**して層所有を固定する。
- **今 phase で L0 brownout floor を実装**：defer。cutoff 電圧が Phase-1 未実測で、docs-first 上**値を発明できない**（[`.claude/rules/docs-first.md`](../../.claude/rules/docs-first.md)）。

## Consequences / 帰結

- **Accepted**（方針決定）・実装は将来 phase に defer・閾値は Phase-1 実測。owner = safety-state。
- [doc12](../architecture/12-infrastructure-common.md) §安全レイヤー **Layer 1** の battery bullet（:82）と §バッテリーポリシー intro（:246）を **net-zero で in-line 延長**（#165 行ズレ回避＝節末追記・行追加でなく既存行の拡張）し、「**L0 現行 cutoff 無し**・percent policy は L1・将来の brownout floor は本 ADR（voltage-based・別名）」を記して本 ADR へ back-link（[doc12](../architecture/12-infrastructure-common.md):68 の物理切断・:79 の deadman と同型）。
- firmware [`main.cpp`](../../firmware/src/main.cpp) の safety watchdog コメント（"MCU hard-cut floor only" ／ L1 pin）を本 ADR に整合（現行 cutoff 無し・percent policy は L1・将来 brownout floor は voltage-based 別名）。
- 本 ADR は [adr/README](README.md) から back-link される。`docs/GLOSSARY.md` は本 round では**触らない**（並行 firmware PR〔branch `docs/firmware-phase1-decisions`〕が同ファイルを編集中＝共有ファイル衝突回避）。**追跡 residual（follow-up）**: その firmware PR が land した後に、[GLOSSARY.md](../GLOSSARY.md) の safety 語群（現行 `command-stream watchdog（comms-loss deadman）` エントリ近傍＝[:70](../GLOSSARY.md)）へ「**L0 voltage-based（over-discharge/brownout）floor**」を 1 語追補する（正準語／percent `battery_is_critical(pct)` とは**別名・非対称**である旨／[adr/0005](0005-l0-battery-brownout-floor.md) へリンク）＝**ADR↔GLOSSARY 双方向**リンクで閉じる。現時点は同ファイルへの同時編集を避けて defer し、residual として明示追跡する（「GLOSSARY は不変」で終わらせない）。
- **3条件**（ADR 化の要件）: ①**hard to reverse**＝安全クリティカルな floor の L0/L1 層配置は後から再アーキが高コスト ②**surprising**＝現行 L0 に無い responsibility を将来足す＋percent と**別名の voltage 機構**にする理由は文脈なしでは意外 ③**real trade-off**＝L0 vs L1・voltage vs percent の実在する二者択一。

## References（`origin/main` `9c29d3c` で検証済み file:line）

- 凍結 percent 判定（L1 共用・触れない）: [safety.py](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py):41-43（`battery_is_critical(pct)`・percent 基準）・:3-5（Policy Gate ∧ Emergency Guardian 共用）・:20-22（`BATTERY_CRITICAL_PCT 10` / `BATTERY_LOW_PCT 20`）
- 凍結 speed floor（触れない）: [safety.py](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py):18（`MAX_LINEAR_VELOCITY 0.3`）・:11-12（config may lower, never raise）
- percent scale の driver 依存（#44）: [safety.py](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py):46-63 / [doc12](../architecture/12-infrastructure-common.md):244-254（`safety.battery_percentage_scale` + `normalize_battery_percent`）
- L0 現行責務（cutoff 無し）: [doc12](../architecture/12-infrastructure-common.md):75-79（Layer 0 の列挙）・:79（heartbeat deadman＝comms-loss と同型・`H-G6 heartbeat_lost`）・:68（MCU 生存不能時の物理切断）
- percent 3段 policy = L1 所有: [doc12](../architecture/12-infrastructure-common.md):244-254（§バッテリーポリシー 表・#44 scale note）
- 実測に残す根拠: [doc12](../architecture/12-infrastructure-common.md) §バッテリーポリシー（実機特性で調整）・[`.claude/rules/safety.md`](../../.claude/rules/safety.md)（実機 estop テスト・0.3 m/s hard cap）
- firmware sensor publish（現行 cutoff 無し）: [`main.cpp`](../../firmware/src/main.cpp):144（`publishBattery`）・:147-158（safety watchdog コメント）
- 対の既決機構: [doc12](../architecture/12-infrastructure-common.md):79（comms-loss heartbeat deadman＝L0・同型）
- 様式 / 親: [ADR-FORMAT](../../.claude/skills/domain-modeling/ADR-FORMAT.md) / [adr/README](README.md) / [`.claude/rules/docs-first.md`](../../.claude/rules/docs-first.md)
