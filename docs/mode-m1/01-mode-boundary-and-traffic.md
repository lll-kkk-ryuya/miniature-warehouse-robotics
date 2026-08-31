# Mode M1 の境界 — Mode A/B/C から切り離す（traffic_mode 裁定・G-k の構造的解決）

> **Status**: オペレーター指示 2026-08-26（「M1 フェーズは Mode A/B と切り離して考える」）を受けた新設。
> **layer**: 本 doc が定めるのは launch / config 構成（**L1** collision_monitor の起動条件と **L2** Traffic の非アクティブ化）。凍結契約（`warehouse_interfaces`）には触れない。

## 1. 決定

1. **M1 フェーズの実行構成は「Mode M1」として、traffic_mode 軸のモード群（Mode A/B/C）から独立させる。**
   - Mode A/B/C は「2台のロボット間交通を誰が調停するか」で分かれた軸（[GLOSSARY §1](../GLOSSARY.md)）。単騎（[ADR-0006](../adr/0006-single-bot-first.md)）では**調停すべき相手ロボットが存在しない**ため、この軸そのものが M1 フェーズでは空転する。
2. **Open-RMF（Mode C）は持ち込まない。** Mode C 資産（`warehouse_rmf_adapter` / 11c 系設計）は削除せず**凍結保存**（2台復帰フェーズの資産 = ADR-0006 と同じ扱い）。
3. **SimpleTrafficManager（Mode B）も使わない。** 通路排他の対象（他 bot）が不在。
4. **Mode M1 の必須条件: collision_monitor が起動する構成で走らせる。**
   - launch gate の実体: `collision_active = traffic_mode != 'open-rmf'`（[nav2_bringup.launch.py:126](../../ws/src/warehouse_bringup/launch/nav2_bringup.launch.py)）。
   - したがって Mode M1 の `traffic_mode` は **`none` を既定**とする（`config/dev/warehouse.yaml:21` の現行値と同じ。凍結契約の変更なし・config 値の選択のみ）。
   - これは [mode-x-er/10 §11 G-k](../mode-x-er/10-room-scale-safety-review.md)（部屋デモ構成で `traffic_mode != 'open-rmf'` を確認）の**構造的解決**である: Mode M1 の定義自体が G-k を満たす構成を要求する。

## 2. なぜ collision_monitor を落とせないか（部屋 = 人がいる）

- 旧分岐の前提: 「Mode C では **Open-RMF がロボット間交通を調停**するから衝突監視は二重」（[collision_monitor.yaml:26-29](../../ws/src/warehouse_bringup/config/collision_monitor.yaml) / [12:550](../architecture/12-infrastructure-common.md)）。
- 部屋スケールでの帰結: **Open-RMF は「人」を知覚しない**。人に反応できる層は `/scan` を直接見る collision_monitor のみで、これが消えると人への L1 反射が丸ごと不在になる（[mode-x-er/10:99 H-11](../mode-x-er/10-room-scale-safety-review.md)）。
- Emergency Guardian は代替にならない: 人に対して寄与ゼロ・Guardian 単独死で 0.5s 後に走行再開する fail-active 窓（[mode-x-er/10:470](../mode-x-er/10-room-scale-safety-review.md)）。
- さらに MCU 側にも反射が無い（L0 不在 = [02:325-329](../shared/02-hardware-design.md) / watchdog 調査 = [02-m1-driver-and-watchdog.md §1](02-m1-driver-and-watchdog.md)）。

## 3. 現状とのギャップ（隠さない・要別 PR）

| # | ギャップ | 実体 | 解消経路 |
|---|---|---|---|
| 1 | `config/stg/warehouse.yaml:12` / `config/prod/warehouse.yaml:13` が `traffic_mode: open-rmf` のまま = そのまま部屋で走らせると L1 反射が消える | 実 Read 済（2026-08-26） | **stg/prod config 変更 PR**（prod config 変更は安全レビュー必須 = [.claude/rules/environments.md](../../.claude/rules/environments.md)）。本 doc は方針の記録であり config は未変更 |
| 2 | [setup/jetson-deploy.md:103-104](../setup/jetson-deploy.md) が「prod は `open-rmf` に一致させよ」と指示 = 本 doc と矛盾 | 実 Read 済 | jetson/setup rescope PR（G0 読み替えと同時） |
| 3 | [jetson/01](../jetson/01-fidelity-and-validation.md) G0-G7 の旧世界前提（ESP32×2 / MS200 / Nav2×2 / 「MCU がクランプ」） | 改訂対象は特定済み（行数保存の制約あり） | 同上 rescope PR |

## 4. モード対応表（境界の早見）

| 軸 | Mode A | Mode B | Mode C | **Mode M1** |
|---|---|---|---|---|
| `traffic_mode` | `none` | `simple` | `open-rmf` | **`none`（既定）** |
| 交通調停 | LLM | SimpleTrafficManager | Open-RMF | **不要（単騎）** |
| collision_monitor | 起動 | 起動 | **起動しない** | **起動（必須条件）** |
| 台数 | 2 | 2 | 2 | **1** |
| 走行環境 | ジオラマ | ジオラマ | ジオラマ | **部屋**（[ADR-0009](../adr/0009-m1-room-scale-operation.md)） |
| 車体 | ESP32 minicar | 同左 | 同左 | **ROSMASTER M1**（STM32 ベンダバイナリ） |

- **司令（commander）軸とは直交**: Mode M1 は「車体・走行・安全構成」の軸であり、司令が誰か（Mode A の LLM 司令官 / Mode X-ER の ER / ジェスチャ = [mode-x-er/09](../mode-x-er/09-hand-raise-summon.md) の ER バイパス）とは独立。M1 フェーズの主役司令はジェスチャ召喚（ADR-0009 Decision 2）。
- `mode_x_er:` config が `traffic_mode` と直交である既存の整理（[GLOSSARY §1](../GLOSSARY.md) `mode_x_er:` 項）と同じ構図。

## References

- [ADR-0006](../adr/0006-single-bot-first.md)（単騎）/ [ADR-0009](../adr/0009-m1-room-scale-operation.md)（部屋スケール）
- [mode-x-er/10-room-scale-safety-review.md](../mode-x-er/10-room-scale-safety-review.md)（H-11 / G-k / Guardian 実査）
- [architecture/12-infrastructure-common.md](../architecture/12-infrastructure-common.md)（cmd_vel 挿入トポロジ・:550 Mode C 向け real-scan-only 変種の defer）
- [nav2_bringup.launch.py:126](../../ws/src/warehouse_bringup/launch/nav2_bringup.launch.py) / [collision_monitor.yaml](../../ws/src/warehouse_bringup/config/collision_monitor.yaml)（launch gate 実体）
- [.claude/rules/environments.md](../../.claude/rules/environments.md)（prod config 変更 = 安全レビュー必須）
