# mode-outdoor/ — Mode Outdoor: 屋外歩道 A→B 自律走行モード（設計提案・骨格）

> **位置づけ**: ROSMASTER M1 単騎（[ADR-0006](../adr/0006-single-bot-first.md)）を**屋外の歩道**で A 地点から B 地点まで、**特定の固定経路（teach-and-repeat 型）を RTK-GNSS 主体に完全自律で走らせる**新しい実行構成モードの**正本ルート**（「完全自律」＝無人ではなく遠隔監視下で常時介入可能な自律。遠隔の人が操作できない自動操縦は法の枠外＝[01 §5](01-legal-envelope-japan.md)）。
> **オペレーター指示（2026-09-12）**: 室内のジェスチャ召喚・standby HRI（[ADR-0009 Decision 2](../adr/0009-m1-room-scale-operation.md:21) の「ジェスチャ召喚を主役」）を**第一優先から外し、屋外設定を第一優先にする**。既存資産で組み、自己位置・知覚（信号）・GNSS・Orin / PC / クラウドの分担を設計する。
> **Status**: **02〜05・07 は記載済（設計値・実測未）／01 は一次情報つきで記載済／00・06 は箱＋要約**（2026-09-12 後半・HTML 図解 2 枚を新設）。旧記述: 例外として **[01 法規包絡](01-legal-envelope-japan.md) と [07 駆動系と車輪径](07-drivetrain-and-wheel-sizing.md) は記載済**（07 = エージェントチーム 4 レーンの統合・設計値・実測未）、01 は一次情報つきで記載済（条文・警察庁資料を実 Read・参照日 2026-09-12）。
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
| [00-mission-and-scope](00-mission-and-scope.md) | ミッション（歩道 A→B・GNSS 主体・完全自律）・スコープ IN/OUT・法的 2 段階（随伴 → 遠隔操作）・**ユーザー決定済 4 点（特定固定経路・RTK 必須・タイヤ換装で ≥4 km/h・survey-first）+ 裁定待ち 5 点**・survey-first・流用する既存資産 | 箱 |
| [01-legal-envelope-japan](01-legal-envelope-japan.md) | **法規包絡（日本・公道歩道）**: 遠隔操作型小型車の定義・寸法/速度/構造・非常停止装置（府令 + 警察庁運用基準）・標識・届出（事項・添付・期限）・通行ルール・信号の意味・罰則・M1 照合・設計への写像・事前相談チェックリスト | **記載済（一次情報・参照日 2026-09-12）** |
| [02-architecture-split-orin-pc-cloud](02-architecture-split-orin-pc-cloud.md) | Orin / PC / クラウドの分担: 3 原則・分担表・**遠隔操作リンクの置き場（doc22 の observe-only 不変条件と整合＝`operator_link_node` を別プロセス別 port・PC 卓は別アプリ）**・法要件の写像（映像は法的前提）・通信（DDS を WAN に流さない・Tailscale + カスタム WS 単一チャネル・MJPEG→x264→WebRTC・heartbeat 200 ms / 1.0 s 候補）・計算予算 | **記載済（設計値）** |
| [03-localization-gnss-and-ekf](03-localization-gnss-and-ekf.md) | 自己位置: 分類表の反転・TF 配信責任（map→odom = global EKF・odom→base_link = local EKF・`gnss_link` contract PR）・入力割当と N−1 differential・`navsat_transform` パラメータ（datum 固定・TF なし）・**fromLL は L3 compile 段（推奨）**・route ファイル形式（提案）・Nav2 差分表（行 pin）・RTK 受信機と品質ゲート契約（`/bot1/gnss/quality` 提案）・方位初期化・**Guardian の pose 源（AMCL 不在で恒久沈黙）の代替**・受け入れ条件・MOLA-LO 屋外 | **記載済（設計値）** |
| [04-perception-sidewalk-and-signals](04-perception-sidewalk-and-signals.md) | 知覚: 屋外センサ比較（OAK-D ×2 推奨・ZED 2i 代替・Mid-360 は Phase 2）・歩道走行可能領域（Phase 1 = ジオフェンス polygon + 幾何・Phase 2 = セグメンテーション検証器）・**負障害物 = `cliff_scan`（virtual_scan 契約の器を流用）**・歩行者用信号（事前登録 ROI・fail-closed 4 状態契約・非対称閾値）・歩行者・costmap 統合（nvblox は Phase 2）・受け入れ条件 | **記載済（設計値）** |
| [05-safety-envelope-and-intervention](05-safety-envelope-and-intervention.md) | 安全包絡と介入: 法が要求する 3 点・既存契約（`/operator/stop_request`・`/bot{n}/stop_state`）の実ファイル確認・**新規 producer 4 点を Guardian の停止理由へ写像（合流 topic `/safety/stop_request` 提案）**・減速は安全機構に数えない（ADR-0012）・横断ゲート = L2 restrict-only profile・物理非常停止（モータレグ遮断）・fail-active・R-26 unit 一覧 | **記載済（契約写像）** |
| [06-hardware-delta-and-base-selection](06-hardware-delta-and-base-selection.md) | ハード差分 BOM（GNSS・屋外カメラ・非常停止柱・標識・荷物箱・LTE）・**車輪大径化と法定 6 km/h の関係（保守基準 ≤147 mm）**・現状車体の屋外リスク・ベース選定 A/B | 箱（§2 は [07](07-drivetrain-and-wheel-sizing.md) と同期） |
| [07-drivetrain-and-wheel-sizing](07-drivetrain-and-wheel-sizing.md) | **駆動系と車輪径（記載済・設計値）**: 法定 6 km/h の判定方法（最大設定・往復 10 m・電池 ≥ 75 %）と FW clamp（車輪 167 rpm）から導く「法律ギリギリ径」（FW 190 mm / 物理 148 mm / 軸間 ≈ 185 mm → **実用 140〜147 mm・代表 144 mm＝最大設定 4.5 km/h・9.6 V で 4.1 km/h**。150 mm は FW clamp を構造と認める場合のみ）・モータ × 径の速度表・**4WD の要否（技術的に必要・メカナム放棄）**・トルク / 電力 / 停止距離・M1 機械制約（アーチ無し・前後バンパ・6 mm D 軸片持ち）・FW 挙動 5 点（低電圧ラッチ・短絡ブレーキ・yaw-adjust・SBUS・Keil toolchain）・選択肢 A〜F と Phase 1/2 推奨（6 km/h は FW 定数修正 + 1:40 = ADR 要）・実測ゲート G-W1〜8 | **記載済（設計値・実測未）** |
| [08-architecture-v2-reference-alignment](08-architecture-v2-reference-alignment.md) | **アーキテクチャ v2（提案・裁定待ち OQ-OD80）**: 参照アーキテクチャ（Autoware / Apollo / Junior・Talos・Boss / Bertha / Nav2 / NeBula・CERBERUS・CMU / Tsukuba）の要点表 → 共通スケルトン → **12 コンポーネント + 2 横断面**への再分解（(a) 地図・経路を一級に (b) 旧 06 を 安全層 / 司令ゲート / 車両 I/F に三分割 (c) 統治を 10 に集約）・MRM 4 段（0 減速 / A 快適停止 / B 即時停止 / E-STOP）・operation mode の一級化・**外部レビュー 11 主張の一次情報照合**（Route Server は humble 1.1.20 backport 済＝レビューの誤り／Static Layer・cuVSLAM blocker・differential の理由訂正）・屋外初期プロファイル（契約 0.3 m/s のまま）・実装順序と「確認実装」の範囲 | **提案（記載済）** |

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

## 関連図解（HTML・本ツリー・2026-09-12 新設）

- [outdoor-architecture-tree.html](outdoor-architecture-tree.html) — **v2 機能ツリー（12 コンポーネント + 2 横断面・提案）**＋旧 01〜09 → v2 対応表＋外部レビュー照合の反映表。正本 = [08](08-architecture-v2-reference-alignment.md)。比較元 = [architecture/robot-architecture-tree.html](../architecture/robot-architecture-tree.html)
- [outdoor-localization-perception-flow.html](outdoor-localization-perception-flow.html) — 屋外 Runtime Data Flow（RTK・2 段 EKF・`cliff_scan`・凍結 cmd_vel チェーン・遠隔操作リンク・停止理由の合流・GNSS 品質と Guardian pose 源・段階・OQ）。比較元 = [architecture/perception-localization-flow.html](../architecture/perception-localization-flow.html)

## 本ツリーへの authoring 方針

- **番号体系**: `NN-<kebab-英語>.md` の連番**末尾**に追加（既存番号を詰め替えない）。追加時は [docs/README.md](../README.md) の mode-outdoor 表と本 README「## ファイル」表の**双方**に索引行を足す（forward + backlink をペアで＝[docs-authoring skill](../../.claude/skills/docs-authoring/SKILL.md)）。
- **既存正本へは複製せず追記**: 車体は mode-m1、知覚は doc23、ハードは shared/02 に末尾追補で forward link を置き、本ツリーからは相互リンクで辿る（本 README「本ツリーが持たないもの」を維持）。
- **用語**は [GLOSSARY §12](../GLOSSARY.md)（Mode Outdoor・遠隔操作型小型車・遠隔操作通行 / 随伴通行・非常停止装置（法定）・横断ゲート・リンク断 watchdog・GNSS 品質ゲート・ジオフェンス）を正準にする。提案段階の語は「提案・未凍結」と明記。
- **法規は一次情報で書く**: 条文（e-Gov）・警察庁資料・通達を実 Read し、**条番号 + 数値 + URL + 参照日**を残す。**本ツリーは法的助言ではない**。最終確認は届出先の警察署（事前相談）で行う（[01 §9](01-legal-envelope-japan.md)）。
- **layer 注記**: 構成要素には L0–L4 / 観測面を併記する（[.claude/rules/layer-annotation.md](../../.claude/rules/layer-annotation.md)）。正準対応表（[productization/01:174](../productization/01-commercial-box-map.md:174)）に行が無い component（EKF / `navsat_transform` / MOLA-LO 等）は「**帰属未定（暫定 L1 Navigation）**」と書き、対応表への 1 行追記は governance PR で行う。doc12 の安全レイヤー 4 層（Layer 0–3）とは混ぜない。

## Status / 残件（隠さない）

- **2026-09-12 新設（骨格・docs 先行）**。**ユーザー決定済**: 特定の固定経路（teach-and-repeat）・RTK 必須・タイヤ換装で ≥4 km/h・survey-first（仕様・既存 tool・企業事例・論文を調査してから採用を決める）。裁定待ち 5 点（走行環境の段階 / 屋外知覚センサ / 横断の権威 / 契約と命名 / 速度契約の再導出）は [00 §4](00-mission-and-scope.md)。裁定後に ADR-0014 を起票し、本 README §関連 ADR と [adr/README](../adr/README.md) の予約行を実体へ差し替える。
- 索引: [docs/README.md](../README.md)「構成」ツリーと末尾の mode-outdoor 表に登録済（本 PR）。根 `.claude/CLAUDE.md`「Important Paths」への登録は **governance PR（別・人間承認）**。
- [docs/STATUS.md](../STATUS.md) への反映は round 境界の refresh（orchestrator 所有）で行う。
- HTML 図解（[html-explainer](../../.claude/skills/html-explainer/SKILL.md)・Orin/PC/クラウド分担図）は未作成。
- [01](01-legal-envelope-japan.md) の**法解釈グレー 3 点**（自律走行 + 随伴の扱い／「運送の用に供する」該当性／ソフト速度上限の「構造上」該当性）は事前相談で確定するまで**未決**として残す（[01 §10](01-legal-envelope-japan.md)）。
- 02 / 05 / 06 の「草案表」は 2026-09-12 の所見の転記であり**裁定済ではない**。裁定後に本文へ昇格させるか破棄する。
- **2026-09-12（後半）**: [07](07-drivetrain-and-wheel-sizing.md) を新設（エージェントチーム 4 レーン = 法規 / 駆動系物理 / 駆動方式・車体 / M1 実機制約の統合・数値再計算・一次情報再 Read）。06 §2 の保守基準（≤147 mm）に合わせ 07 の推奨径を **144 mm 級**に統合、「ホイールアーチ干渉」は**撤回**（アーチ無し・干渉は前後ロアバンパと前後輪どうし）。裁定待ちに **`OQ-OD70`（6 km/h を要件にするか＝案 C・ADR-0014 の一部）** を追加。同 PR で [01 追補②](01-legal-envelope-japan.md)（型式認定基準の実 Read = 最高速度試験・突出 8 mm・非常停止ボタン配置）と [mode-m1/02](../mode-m1/02-m1-driver-and-watchdog.md) 末尾追補（FW 挙動 5 点・Keil toolchain = ADR-0013 前提ゲートへの申し送り `OQ-OD75`）を追加。GLOSSARY §12 に「FW 換算速度」「車輪スケール k」を追補。
- **2026-09-12（後半②）**: エージェントチーム 3 レーン（自己位置・RTK／知覚／アーキ・安全・通信）の統合で **02〜05 を設計値で記載**（全 pin は執筆時に実 Read・外部一次情報は URL + 参照日・新規 topic / 型 / 値はすべて [提案] 未凍結）。HTML 図解 2 枚（tree / flow）を新設。07 の指摘 5 点（anchor `shared/02:761`・「自律走行（安全層外）」語・144 mm 基準への統一・06:35 との整合・02 §2-2「映像は法的前提」）を解消。主要な新規裁定候補: fromLL は L3 compile 段（`OQ-OD33`）／Guardian pose 源 = global EKF + `/bot1/gnss/quality`（`OQ-OD38/39`）／`operator_link_node` は web_bridge と別プロセス別 port（`OQ-OD23`）／停止理由の合流 topic（`OQ-OD53`）／減速は安全機構に数えない（`OQ-OD54`）。
- **2026-09-12（後半③）**: エージェントチーム 3 レーン（自動運転リファレンス構造／屋外ロボット学術スタック／外部レビュー主張の一次情報検証）で **[08](08-architecture-v2-reference-alignment.md) を新設**（v2 分解 12+2・MRM 4 段・operation mode 一級化・実装順序）、[outdoor-architecture-tree.html](outdoor-architecture-tree.html) を v2 に差し替え。外部レビュー照合で **doc23 の 3 記述を同一行訂正**（Local Static Layer の理由・cuVSLAM blocker = ADR-0008 の Humble pin・differential の「凍結」緩和）＋ doc23 末尾追補②。**Nav2 Route Server は humble 1.1.20 に backport 済**（レビューの「Humble に無い」は誤り）→ 03 §3-2 案 D・`OQ-OD3D/81`。04 に keepout filter・nvblox ESDF の限界・`limit` 非対応・elevation_mapping_cupy 行、05 に MRM 段分け・`OQ-OD58` 三択（recovery 無効化を初期プロファイル推奨）を追加。GLOSSARY §12 に「MRM」「司令ゲート」を追補。

- **2026-09-13**: 車輪径 150 mm をユーザー裁定（FW clamp を構造根拠に含める＝案 A'、退避先 144 mm）。[07 追補③](07-drivetrain-and-wheel-sizing.md)＝裁定・実装スライス 1（`m1_driver` wheel_scale / odom・branch `feat/m1-wheel-scale-odom`・PR pending・R-26 61 本・mutation 11/11）・G-W9 追加・`OQ-OD76/77`。[06 末尾追補](06-hardware-delta-and-base-selection.md)＝購入リスト（最小構成 ≈ ¥30,800・経路 A/B）。[mode-m1/02](../mode-m1/02-m1-driver-and-watchdog.md) 末尾追補②＝param 一覧。
