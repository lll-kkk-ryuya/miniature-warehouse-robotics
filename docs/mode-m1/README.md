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
| [04-runtime-speed-limiter](04-runtime-speed-limiter.md) | OQ-T3 の設計解: Nav2 `speed_limit_topic` による**走行中速度上限の動的変更**。三層モデル（起動基準値 = `_operating_vx_max`/RewrittenYaml ／ runtime 帯 = `nav2_msgs/SpeedLimit` ／ L0' 最終クランプ不変） |

## 関連 ADR（正本は docs/adr/ — 移動・複製しない）

ADR 本体は [docs/adr/](../adr/README.md) の `NNNN-slug.md` 連番が**正準**であり、番号の詰め替え・本ツリーへの移動/複製はしない（全参照を割るため＝[adr/README](../adr/README.md) の命名規約）。
本節は **M1 フェーズから見た分野別ビュー（索引）**にすぎず、**決定内容・トレードオフ・Open の正本は各 ADR 本体**。食い違ったら ADR 本体が勝つ。

| ADR | M1 フェーズでの意味 | 状態 |
|---|---|---|
| [0005](../adr/0005-l0-battery-brownout-floor.md) | L0（MCU）の battery brownout floor は **voltage-based・別名機構で将来 phase**。現行 L0 は cutoff を持たず `/battery` publish + 物理切断のみで、percent 3段 policy は L1 所有＝M1 でも電源系の last-line floor は未実装のまま（cutoff 電圧は Phase-1 実機実測） | accepted（2026-07-17・実装は将来 phase に defer） |
| [0006](../adr/0006-single-bot-first.md) | M1 は **1台（`bot1`）のみで実装・検証・撮影**。2台系の設計 doc と実装資産（SimpleTrafficManager / HeadOnInjector / negotiation engine / VirtualScan 相互注入）は削除せず凍結保存し、**`/scan` 由来の L1 物理反射（collision_monitor）は1台でも現役**（Decision 3） | accepted（初回公開ゲートの再定義のみ Open） |
| [0007](../adr/0007-no-overhead-camera-gesture-via-onboard-nn.md) | ER/知覚の画像入力は**搭載 HP60C に一本化**（固定俯瞰カメラを ER 入力・ジェスチャ検出に使わない）。ジェスチャ2種は**ローカル骨格 NN**（第1候補 MediaPipe・Apache-2.0・CPU）で決定論認識し、既存 L3/L2 ゲートを1ステップも迂回しない（③速度セレクタ追加後はジェスチャ **3 種**＝ADR 末尾 2026-08-28 追補） | accepted（撮影用俯瞰 C922n の扱いのみ Open） |
| [0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md) | M1 の distro = **ROS 2 Humble / Ubuntu 22.04**。理由は ①Orin Nano で Isaac ROS を使う道が 3.x=Humble のみ ②Yahboom driver（HP60C の閉ソース `.so` 等）が Humble 固定。MPPI は Humble にもリリース有り。代償 = **Gazebo の公式ペア喪失**（Humble↔Fortress） | proposed（Gazebo の扱いが Open・[shared/02](../shared/02-hardware-design.md):383 のオペレーター指示で proposed 保持） |
| [0009](../adr/0009-m1-room-scale-operation.md) | M1 の走行環境は**実際の部屋（room scale）**。ジオラマ（1.8×0.9m）は走行に使わず凍結保存（sim の回帰環境としては現状維持）、デモは倉庫設定を薄め**ジェスチャ召喚を主役**にする。`KNOWN_LOCATIONS` の 9 キーは凍結のまま**値のみ**部屋の実測 waypoint へ差し替え、W3（ジオラマ 9 点再設計）は中止 | accepted（部屋の範囲・撮影構図・room sim world の要否・sim/実機 config 二重化・安全論証の再レビューが Open） |
| [0010](../adr/0010-raise-speed-cap-to-platform-max.md) | 凍結契約 `MAX_LINEAR_VELOCITY = 0.3 m/s` を**プラットフォーム上限へ再定義**（値 = 実機 car_type のファーム clamp。公式 V3.6.5 の M1 候補 = **`0x0A` → 0.7 m/s**・実機確認待ち）。運用値は config が持ち、**L0' クランプは方向保存・暴走バックストップとして維持**＝[02](02-m1-driver-and-watchdog.md) の serial driver slice での結線（G-l）が引き上げの前提条件。運用値を走行中に切り替える経路は [04](04-runtime-speed-limiter.md) | accepted（値 pin は実機確認 + S-SPEED 実測待ち・コードは 0.3 のまま） |
| ADR-0011（ros2_control 採否） | **未裁定・未起票**。Phase 1 は本ツリー [02](02-m1-driver-and-watchdog.md) の Python driver で進め、ros2_control は Phase 2 TARGET 候補として別途裁定する（本ツリーは先取りしない）＝[02 §5](02-m1-driver-and-watchdog.md) | 未裁定（ADR ファイル未作成） |

## 本ツリーへの authoring 方針（何をここに書き、何を書かないか）

- **M1 の実行構成・bring-up 固有の設計 doc は本ツリー配下**に `NN-<kebab-英語>.md` 連番の**末尾**として追加する（既存番号を詰め替えない＝全参照を割るため。[.claude/rules/docs-authoring-and-glossary.md](../../.claude/rules/docs-authoring-and-glossary.md) §必須 2）。追加時は `docs/README.md` の mode-m1 表と本 README「## ファイル」表の**双方**に索引行を足す（forward + backlink をペアで）。
- **既に正本を持つテーマは既存正本へ追記し、本ツリーへ複製しない**（冒頭「本ツリーが持たないもの」を維持）——ジェスチャ司令 = [mode-x-er/09](../mode-x-er/09-hand-raise-summon.md)、部屋運用の安全レビュー = [mode-x-er/10](../mode-x-er/10-room-scale-safety-review.md)、standby/HRI = [mode-x-er/11](../mode-x-er/11-standby-and-hri-features.md)、ハードウェア実体 = [shared/02](../shared/02-hardware-design.md)、知覚スタック = [architecture/23](../architecture/23-perception-and-localization.md)、実機ゲート = [jetson/01](../jetson/01-fidelity-and-validation.md)。本ツリーからは**相互リンクで辿る**。
- **ADR は [docs/adr/](../adr/README.md) が正準**。M1 に効く決定を起こしたら ADR 本体を `docs/adr/NNNN-slug.md` に置き、本 README「§関連 ADR」へ索引行を 1 行足す（ADR 本体を本ツリーへ移さない・内容を複製しない）。

## 関連図解（HTML・既存）

- [architecture/robot-architecture-tree.html](../architecture/robot-architecture-tree.html) — ロボットアーキテクチャ全体ツリー（#518）
- [architecture/perception-localization-flow.html](../architecture/perception-localization-flow.html) — 知覚・自己位置データフロー（#530）

## Status / 残件（隠さない）

- 新設: 2026-08-26（オペレーター指示・docs 先行 = docs-first）。実装（driver node / joy 変換 node）は未着手。
- `config/stg` / `config/prod` の `traffic_mode: open-rmf` は**未変更**（[01 §2](01-mode-boundary-and-traffic.md)。prod config 変更は安全レビュー必須の別 PR = [.claude/rules/environments.md](../../.claude/rules/environments.md)）。
- [jetson/01](../jetson/01-fidelity-and-validation.md) の G0-G7 は旧世界（ESP32×2 / MS200 / 2台）前提の記述が残る — rescope は別 PR（調査済み・改訂対象は特定済み）。
- ros2_control 採否（ADR-0011）は**未裁定** — [02 §5](02-m1-driver-and-watchdog.md)。
- [04](04-runtime-speed-limiter.md) = OQ-T3（走行中の速度上限変更）の設計解 doc を同ラウンド（2026-08-28）で新設。**経路選定のみ確定・実装（publisher node / Nav2 配線）は未着手**。
- 「## 関連 ADR」索引を新設（2026-08-28）。**ADR 本体は [docs/adr/](../adr/README.md) から移動・複製しない**方針を明文化し、本ツリーは分野別ビュー（索引）のみを持つ。
