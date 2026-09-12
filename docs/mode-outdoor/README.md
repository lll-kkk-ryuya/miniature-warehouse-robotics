# mode-outdoor/ — Mode Outdoor: 屋外歩道 A→B 自律走行モード（設計提案・骨格）

> **位置づけ**: ROSMASTER M1 単騎（[ADR-0006](../adr/0006-single-bot-first.md)）を**屋外の歩道**で A 地点から B 地点まで **GNSS を主体に完全自律で走らせる**新しい実行構成モードの**正本ルート**。
> **オペレーター指示（2026-09-12）**: 室内のジェスチャ召喚・standby HRI（[ADR-0009 Decision 2](../adr/0009-m1-room-scale-operation.md:21) の「ジェスチャ召喚を主役」）を**第一優先から外し、屋外設定を第一優先にする**。既存資産で組み、自己位置・知覚（信号）・GNSS・Orin / PC / クラウドの分担を設計する。
> **Status**: **骨格のみ（箱）**。各 doc の「何を書くか」節に従って順次埋める。例外として **[01 法規包絡](01-legal-envelope-japan.md) だけは一次情報つきで記載済**（条文・警察庁資料を実 Read・参照日 2026-09-12）。
> **決定の扱い**: 方針転換そのものは hard-to-reverse なので ADR 化する（**ADR-0014 予約**＝[adr/README](../adr/README.md)。裁定待ち＝[00 §4](00-mission-and-scope.md)）。本ツリーは ADR を複製せず、設計の中身を持つ。

## 本ツリーが持たないもの（重複禁止・参照で辿る）

- **車体・bring-up・driver・watchdog・停止権限** = [mode-m1/](../mode-m1/README.md)。屋外でも同じ M1 車体である限り、[02 driver/watchdog](../mode-m1/02-m1-driver-and-watchdog.md)・[03 joystick](../mode-m1/03-joystick-teleop-bringup.md)・[05 停止権限](../mode-m1/05-operation-state-and-stop-authority.md) が正本。本ツリーは**屋外で追加になる要件**だけを持つ。
- **知覚・自己位置の TARGET 設計（室内）** = [architecture/23](../architecture/23-perception-and-localization.md)。原則 P1（L1 に GPU を持ち込まない＝[23:37](../architecture/23-perception-and-localization.md:37)）・P2（cmd_vel 経路は 1 バイトも変えない＝[23:41](../architecture/23-perception-and-localization.md:41)）は屋外でも不変。屋外差分（GNSS の反転・歩道知覚）だけを [03](03-localization-gnss-and-ekf.md) / [04](04-perception-sidewalk-and-signals.md) に置く。
- **ハードウェア実体** = [shared/02](../shared/02-hardware-design.md)。差分 BOM だけ [06](06-hardware-delta-and-base-selection.md)。
- **環境分離 dev/stg/prod** = [architecture/19](../architecture/19-environments-and-config.md:16)。**観測面** = [architecture/22](../architecture/22-web-observability.md)。**レイヤ定義** = [productization/01 §レイヤ annotation 対応表](../productization/01-commercial-box-map.md:174)。
- **法規**は本ツリー [01](01-legal-envelope-japan.md) が正本（他 doc に複製しない）。

## ファイル

| ファイル | 内容 | 状態 |
|---------|------|------|
| [README](README.md) | 位置づけ・境界（mode-m1 / doc23 との分担）・関連 ADR・authoring 方針・残件 | 骨格 |
| [00-mission-and-scope](00-mission-and-scope.md) | ミッション（歩道 A→B・GNSS 主体・完全自律）・スコープ IN/OUT・法的 2 段階（随伴 → 遠隔操作）・**オペレーター裁定待ち 6 点**・流用する既存資産 | 箱 |
| [01-legal-envelope-japan](01-legal-envelope-japan.md) | **法規包絡（日本・公道歩道）**: 遠隔操作型小型車の定義・寸法/速度/構造・非常停止装置（府令 + 警察庁運用基準）・標識・届出（事項・添付・期限）・通行ルール・信号の意味・罰則・M1 照合・設計への写像・事前相談チェックリスト | **記載済（一次情報・参照日 2026-09-12）** |
| [02-architecture-split-orin-pc-cloud](02-architecture-split-orin-pc-cloud.md) | Orin（車載）/ PC（遠隔操作者卓）/ クラウドの分担: 3 原則・分担表・時間階層・リンク断時の挙動・通信・計算予算 | 箱（草案表あり） |
| [03-localization-gnss-and-ekf](03-localization-gnss-and-ekf.md) | 自己位置: RTK-GNSS + `navsat_transform` + 2 段 EKF（local/global）・datum・TF 単一所有の屋外版・**Humble 制約（FollowGPSWaypoints は Iron 以降 → fromLL + 既存座標 goal）** | 箱 |
| [04-perception-sidewalk-and-signals](04-perception-sidewalk-and-signals.md) | 知覚: センサ前提（HP60C は屋外主センサにしない）・歩道走行可能領域・障害物/負障害物（縁石）・歩行者用信号の検出と状態分類・歩行者への進路譲り | 箱 |
| [05-safety-envelope-and-intervention](05-safety-envelope-and-intervention.md) | 安全包絡と介入: 法が要求する 3 点（操作可能・通信断停止・物理非常停止）・既存停止資産の流用・新規 producer（リンク断 watchdog / GNSS 品質ゲート / ジオフェンス）・横断ゲート（L2）・fail-active 対策（ADR-0013） | 箱（草案表あり） |
| [06-hardware-delta-and-base-selection](06-hardware-delta-and-base-selection.md) | ハード差分 BOM（GNSS・屋外カメラ・非常停止柱・標識・荷物箱・LTE）・**車輪大径化と法定 6 km/h の関係（ADR-0010 定数から導出）**・現状車体の屋外リスク・ベース選定 A/B | 箱（導出表あり） |

## 関連 ADR（正本は docs/adr/ — 移動・複製しない）

| ADR | Mode Outdoor から見た意味 | 状態 |
|---|---|---|
| **0014（予約）** | **屋外歩道自律走行を第一優先にする**（[ADR-0009 Decision 2](../adr/0009-m1-room-scale-operation.md:21) の supersede・室内ジェスチャ/HRI は凍結保存）。裁定待ち項目は [00 §4](00-mission-and-scope.md) | **未起票・番号予約のみ**（[adr/README](../adr/README.md)） |
| [0009](../adr/0009-m1-room-scale-operation.md) | 部屋スケール運用・ジェスチャ召喚主役。**Decision 2 が本モードで格下げ対象**（削除ではなく凍結保存）。Decision 1（ジオラマ凍結）・5（`KNOWN_LOCATIONS` 9 キー凍結）は不変 | accepted（部分 supersede 予定） |
| [0013](../adr/0013-stm32-command-stream-watchdog.md) | STM32 command-stream watchdog（W-3）。**屋外の fail-active 対策の前提**（ホスト死で MCU が走り続ける故障は歩道では致命的＝[05](05-safety-envelope-and-intervention.md)） | accepted（実装は前提ゲート 4 点通過後） |
| [0010](../adr/0010-raise-speed-cap-to-platform-max.md) | 速度上限 = プラットフォーム上限（FW clamp 0.7 m/s 候補）。**法定「構造上 6 km/h を超えられない」と車輪径の関係**は [06 §2](06-hardware-delta-and-base-selection.md) | accepted（値 pin は実機確認待ち） |
| [0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md) | Humble 固定 → Nav2 `FollowGPSWaypoints` は Iron 以降のため使えず、[03](03-localization-gnss-and-ekf.md) で `fromLL` + 既存座標 goal の代替を設計する | proposed |
| [0007](../adr/0007-no-overhead-camera-gesture-via-onboard-nn.md) | ジェスチャ認識は搭載 HP60C + 骨格 NN。**屋外では HP60C（構造化光）を主センサにしない**ため、決定の射程は室内に留まる（[04 §1](04-perception-sidewalk-and-signals.md)） | accepted（射程注記のみ） |
| [0006](../adr/0006-single-bot-first.md) | 単騎 M1 で実装・検証（屋外でも同じ） | accepted |

## 本ツリーへの authoring 方針

- **番号体系**: `NN-<kebab-英語>.md` の連番**末尾**に追加（既存番号を詰め替えない）。追加時は [docs/README.md](../README.md) の mode-outdoor 表と本 README「## ファイル」表の**双方**に索引行を足す（forward + backlink をペアで＝[docs-authoring skill](../../.claude/skills/docs-authoring/SKILL.md)）。
- **既存正本へは複製せず追記**: 車体は mode-m1、知覚は doc23、ハードは shared/02 に末尾追補で forward link を置き、本ツリーからは相互リンクで辿る（本 README「本ツリーが持たないもの」を維持）。
- **用語**は [GLOSSARY §12](../GLOSSARY.md)（Mode Outdoor・遠隔操作型小型車・遠隔操作通行 / 随伴通行・非常停止装置（法定）・横断ゲート・リンク断 watchdog・GNSS 品質ゲート・ジオフェンス）を正準にする。提案段階の語は「提案・未凍結」と明記。
- **法規は一次情報で書く**: 条文（e-Gov）・警察庁資料・通達を実 Read し、**条番号 + 数値 + URL + 参照日**を残す。**本ツリーは法的助言ではない**。最終確認は届出先の警察署（事前相談）で行う（[01 §9](01-legal-envelope-japan.md)）。
- **layer 注記**: 構成要素には L0–L4 / 観測面 / 自律走行層（安全層外）を併記する（[.claude/rules/layer-annotation.md](../../.claude/rules/layer-annotation.md)）。

## Status / 残件（隠さない）

- **2026-09-12 新設（骨格・docs 先行）**。オペレーター裁定待ち 6 点（走行環境の段階 / RTK 採否 / 屋外知覚センサ / 横断の権威 / メカナムの扱い / 契約と命名）は [00 §4](00-mission-and-scope.md)。裁定後に ADR-0014 を起票し、本 README §関連 ADR と [adr/README](../adr/README.md) の予約行を実体へ差し替える。
- 索引: [docs/README.md](../README.md)「構成」ツリーと末尾の mode-outdoor 表に登録済（本 PR）。根 `.claude/CLAUDE.md`「Important Paths」への登録は **governance PR（別・人間承認）**。
- [docs/STATUS.md](../STATUS.md) への反映は round 境界の refresh（orchestrator 所有）で行う。
- HTML 図解（[html-explainer](../../.claude/skills/html-explainer/SKILL.md)・Orin/PC/クラウド分担図）は未作成。
- [01](01-legal-envelope-japan.md) の**法解釈グレー 3 点**（自律走行 + 随伴の扱い／「運送の用に供する」該当性／ソフト速度上限の「構造上」該当性）は事前相談で確定するまで**未決**として残す（[01 §10](01-legal-envelope-japan.md)）。
- 02 / 05 / 06 の「草案表」は 2026-09-12 の所見の転記であり**裁定済ではない**。裁定後に本文へ昇格させるか破棄する。
