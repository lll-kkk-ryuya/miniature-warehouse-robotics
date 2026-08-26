# mode-m1/ — Mode M1: ROSMASTER M1 単騎・部屋スケール実行モード

> **位置づけ**: ROSMASTER M1 単騎（[ADR-0006](../adr/0006-single-bot-first.md)）・部屋スケール（[ADR-0009](../adr/0009-m1-room-scale-operation.md)）フェーズの**実行構成（車体・走行・安全・bring-up）の正本ルート**。
> **オペレーター指示（2026-08-26）**: 本フェーズは Mode A/B（および Mode C）と**切り離して考える** — traffic_mode 軸のモード群から独立した platform mode として新設する（境界の正本 = [01](01-mode-boundary-and-traffic.md)）。
>
> **本ツリーが持たないもの（重複禁止・参照で辿る）**: ジェスチャ司令の設計 = [mode-x-er/09](../mode-x-er/09-hand-raise-summon.md)、部屋運用の安全レビュー = [mode-x-er/10](../mode-x-er/10-room-scale-safety-review.md)、standby/HRI = [mode-x-er/11](../mode-x-er/11-standby-and-hri-features.md)、ハードウェア実体 = [shared/02](../shared/02-hardware-design.md)・[shared/11](../shared/11-m1-assembly-manual.md)、知覚スタック = [architecture/23](../architecture/23-perception-and-localization.md)、実機ゲート G0-G7 = [jetson/01](../jetson/01-fidelity-and-validation.md)。本ツリーは M1 の**実行構成と bring-up 固有の設計**に閉じる。

## ファイル

| ファイル | 内容 |
|---------|------|
| [01-mode-boundary-and-traffic](01-mode-boundary-and-traffic.md) | Mode A/B/C との境界・traffic_mode の裁定（collision_monitor 常時起動 = G-k の構造的解決）・stg/prod config とのギャップ |
| [02-m1-driver-and-watchdog](02-m1-driver-and-watchdog.md) | `warehouse_m1_driver` serial node 設計（L0' 結線 = G-l）＋ **watchdog 多層停止設計**（STM32 側 watchdog 不在の調査確定・G-g 実機確認手順） |
| [03-joystick-teleop-bringup](03-joystick-teleop-bringup.md) | 物理起動の最初の目標 = **joystick 手動走行**。成功の 3 段ゲート（M0 給電 / M1 疎通 / M2 ROS 走行）・実機プローブ・joy 経路設計 |

## 関連図解（HTML・既存）

- [architecture/robot-architecture-tree.html](../architecture/robot-architecture-tree.html) — ロボットアーキテクチャ全体ツリー（#518）
- [architecture/perception-localization-flow.html](../architecture/perception-localization-flow.html) — 知覚・自己位置データフロー（#530）

## Status / 残件（隠さない）

- 新設: 2026-08-26（オペレーター指示・docs 先行 = docs-first）。実装（driver node / joy 変換 node）は未着手。
- `config/stg` / `config/prod` の `traffic_mode: open-rmf` は**未変更**（[01 §2](01-mode-boundary-and-traffic.md)。prod config 変更は安全レビュー必須の別 PR = [.claude/rules/environments.md](../../.claude/rules/environments.md)）。
- [jetson/01](../jetson/01-fidelity-and-validation.md) の G0-G7 は旧世界（ESP32×2 / MS200 / 2台）前提の記述が残る — rescope は別 PR（調査済み・改訂対象は特定済み）。
- ros2_control 採否（ADR-0011）は**未裁定** — [02 §5](02-m1-driver-and-watchdog.md)。
