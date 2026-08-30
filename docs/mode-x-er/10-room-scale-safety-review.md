# 部屋スケール運用の安全レビュー（分析）— 人とロボットが同一走行平面に立つ構成の残余リスク評価

作成日: 2026-08-18
Status: **分析レビュー（analysis review）**。**本書は部屋運用の承認・運転許可ではない。** 本書が行うのは「何が守られていて・何が守られておらず・何を決めれば守られるか」の記述であり、**最終受け入れはオペレーターゲート**（[ADR-0009 §Open :82](../adr/0009-m1-room-scale-operation.md) の `# TODO(安全レビュー)` を閉じるのは本書ではなくオペレーターの裁定）。
スコープ: 単騎 M1（[ADR-0006](../adr/0006-single-bot-first.md)）× **部屋スケール運用**（[ADR-0009](../adr/0009-m1-room-scale-operation.md)・[GLOSSARY §11 :146](../GLOSSARY.md)）× X-lite profile。**docs のみ**を対象とし、コード・config は 1 行も変更しない。

> **なぜ本書が要るか**: [ADR-0009 帰結 ⑦ :51-53](../adr/0009-m1-room-scale-operation.md) が「**部屋では人とロボットが同一平面に立つため構造的保証が消える**／論証を多層防御の形に組み替えたうえで**安全レビューに掛ける**」と宣言し、[09 R-3 :255-267](09-hand-raise-summon.md) が組み替え後の論証を、[09 R-7〜R-9 :293-320](09-hand-raise-summon.md) がその論証への 4 件の補訂・前提条件・実論拠を置いた。**人が走行面上に立ちうる構成は本プロジェクトで初めて**（[09:267](09-hand-raise-summon.md)）であり、その残余リスク評価が未実施のまま残っている。本書はその**分析部分**を実施し記録する。

---

## 0. 本書の作法（判定語彙・layer 注記・発明しない）

### 0-1. 判定語彙（4 値・固定）

| 判定 | 意味 | 誰が閉じるか |
|---|---|---|
| **PASS** | 現行の設計・実装のまま部屋でも成立する（点検の結果、前提を失っていない） | — （本書で閉じる） |
| **CONDITIONAL** | 明示した前提条件が満たされる限りにおいて成立する。条件を本文に列挙する | 条件側の land／裁定 |
| **OPERATOR-GATE** | オペレーターの裁定が要る（選択肢と推奨を提示するが、本書は決めない） | オペレーター |
| **PHASE-1-GATE** | 実機実測が要る（測定手順を定義するが、値は本書で発明しない） | Phase 1 実測 |

**判定は「その項目が安全か」ではなく「その項目についてこのレビューで何が言い切れるか」を表す**。PASS は「リスクゼロ」ではなく「部屋転換によって前提を失っていないことを確認した」の意である。

**判定の入れ子の読み替え規則**: 上位項目（例 S-1 柱3 = CONDITIONAL）の内側に別種の判定（例 その柱の中の recovery 窓 = OPERATOR-GATE）が現れる場合、**上位判定は「内側の gate が全て閉じたときに成立する」の意**であり、内側の gate は §11 のチェックリストに**それぞれ独立の行**として現れる。上位が CONDITIONAL だからといって内側の OPERATOR-GATE が自動的に閉じることはない。

### 0-2. layer 注記（[.claude/rules/layer-annotation.md](../../.claude/rules/layer-annotation.md) 準拠）

本書は各ハザード・各緩和に **L0 / L0' / L1 / L2 / L3 / L4** を括弧併記する。番号体系は商用 box map（[productization/01 §レイヤ annotation 対応表 :180-187](../productization/01-commercial-box-map.md)）を正とし、**doc12 の安全レイヤー 4 層（Layer 0–3）とは軸が違う**ため裸で混ぜない（同 :195）。本書で頻出する帰属:

| 記号 | 本書での指示対象（実体） |
|---|---|
| **L4** | `gesture_detector`（publish-only・0 actuation）・`x_er_bridge`（commander node）＝[09 §4-1 :59-91](09-hand-raise-summon.md) |
| **L3** | `robotics_planning_core/`（Validator / Visual Resolver / Task Graph Executor / Command Compiler）・**召喚/指差しの候補生成規則はここ**（実行許可なし） |
| **L2** | Governance = `warehouse_mcp_server`（`policy_gate.py`）／ Contract = `warehouse_interfaces`（`schemas.py` の KNOWN_LOCATIONS validator）。Traffic（`warehouse_traffic`）は**単騎 X-lite では実質非アクティブ** |
| **L1** | Navigation = `warehouse_nav2_bridge`（REST→Nav2）・`warehouse_bringup/config/`（`nav2_params.yaml` / `collision_monitor.yaml` / `twist_mux.yaml`）／ Safety = `warehouse_safety`（Emergency Guardian） |
| **L0'** | ホスト側シリアルドライバ送信直前の 0.3 m/s ベクトルクランプ（`warehouse_m1_driver` の `clamp.py`）。公式 source は入手したが、stock FW を custom fork に置換しない現行方針の choke point（[02 P-7](../shared/02-hardware-design.md)） |
| **L0** | MCU 内の物理安全（M1 stock FW に serial command timeout は無い。G-g で command-stream watchdog の追加が必要。ESP32 自前ファームは 2 台構成の資産） |

### 0-3. 発明しないもの（docs-first）

本書は **docs に無いしきい値・規則・パラメータを発明しない**（[.claude/rules/docs-first.md](../../.claude/rules/docs-first.md)）。値が必要だが正本に無い箇所は次のいずれかで処理する:

- **PHASE-1-GATE として登録**し、測定手順だけを定義する（値は書かない）。
- **導出値として提示する場合は「導出式 ＋ 入力の出所（file:line）」を明記し、`例示値` の札を必ず付ける**（そのまま config へ写すことを禁じる）。
- [09:19](09-hand-raise-summon.md) の「発明しないもの」リスト（`/goal_pose` 直注入・**standoff distance 独立パラメータ**・新 location キー・safety 閾値の緩和・角速度上限の新設）は本書でも維持する。**唯一、S-2 で「standoff 相当パラメータの禁を解くか」をオペレーター裁定として提起する**が、本書では解かない。

---

## 1. レビュー対象 8 項目の由来（本書は集約であって新規提起ではない）

8 項目はすべて**既存 docs が「安全レビュー対象」と名指し済み**のものである。本書はそれを 1 箇所に集約し分析を加える。

| # | 項目 | docs 側の名指し（出所） |
|---|---|---|
| S-1 | R-3 多層防御論証の残余リスク評価 | [09 R-3 :255-267](09-hand-raise-summon.md) / [09 R-8-1 :306](09-hand-raise-summon.md) / [ADR-0009 :62](../adr/0009-m1-room-scale-operation.md) |
| S-2 | OQ-20 召喚レグ解決規則の再設計 | [09 R-7 :293-302](09-hand-raise-summon.md) / [ADR-0009 追加Open③ :120-122](../adr/0009-m1-room-scale-operation.md) |
| S-3 | C-3 collision_monitor 停止ポリゴン改訂 | [09 R-8-2 :308](09-hand-raise-summon.md) / [23 G-8 :736-743](../architecture/23-perception-and-localization.md) / [ADR-0009 追加Open② :116-118](../adr/0009-m1-room-scale-operation.md) / [02:359](../shared/02-hardware-design.md) C-3 |
| S-4 | #223 座標ゴール seam の到達集合監査 | [09 R-8-3 :310](09-hand-raise-summon.md) / [ADR-0009 追加Open⑤ :128-130](../adr/0009-m1-room-scale-operation.md) |
| S-5 | OQ-21 立位の人に対する L1 有効性 | [09 R-9 :312-320](09-hand-raise-summon.md) |
| S-6 | 運用規律の部屋版 | [09 R-3 柱5 :265](09-hand-raise-summon.md)（+ :41 / :142） |
| S-7 | waypoint 配置規律の形式化 | [09 R-3 柱2 :262](09-hand-raise-summon.md)（+ [09 OQ-17 :276](09-hand-raise-summon.md)） |
| S-8 | E-stop / Guardian 経路の部屋での妥当性確認 | [ADR-0009 帰結⑦ :53](../adr/0009-m1-room-scale-operation.md)（「残る保護」の点検）/ [12 §安全レイヤー :72-93](../architecture/12-infrastructure-common.md) |

**§11 のチェックリストと本表の対応（1:1 ではない点の明示）**: §11 の gate のうち **G-a / G-b ← S-3**、**G-c ← S-2**、**G-d ← S-4**、**G-e ← S-7**、**G-f ← S-5**、**G-g / G-l ← S-8**、**G-h ← S-6**、**G-i / G-k ← S-1 柱3** と対応する。**例外が 2 つある**:

- **G-j（sim / 実機の config 二重化＝OQ-22）は 8 項目のいずれにも対応しない。** これは [23 G-7 :717-734](../architecture/23-perception-and-localization.md) / [ADR-0009 追加Open① :107-114](../adr/0009-m1-room-scale-operation.md) が**構成問題**として登録した既存 OQ であり、本レビューが安全レビュー項目として提起したものではない。**チェックリストに載せる理由は「G-e（部屋 waypoint の確定）の land 可否を握るから」**であって、それ自体が安全論証の一部だからではない。加えて本書は **C-3 改訂と sim 回帰の衝突**という OQ-22 スコープ外の論点を新規提起している（§11 の「⚠️ G-j に付随する新規提起」）。
- **G-m（ジェスチャ誤検出）は S-x 節を持たず、H-12 として §2 のハザード表にのみ現れる。** 8 項目はいずれも「既存 docs が安全レビュー対象と名指ししたもの」だが、H-12 は**本レビューがハザード分析の過程で追加した行**であり、対応する既存 docs の名指しが無いためである。

---

## 2. ハザード分析表（総覧）

> ## ⚠️ 本レビュー最大の所見: 「残る保護 3 枚」のうち現時点で機能しているのは **1 枚だけ**
>
> [ADR-0009 帰結⑦ :53](../adr/0009-m1-room-scale-operation.md) は、構造的保証を失った後に「残る保護」として **①到達集合を 9 waypoint に限定する語彙 gate ②L1 collision_monitor ③L0' 0.3m/s クランプ** の 3 枚を挙げる。**実体を 2026-08-18 に実 Read した結果、現時点で機能しているのは ① のみ**である:
>
> | 保護 | 現状 | 根拠（実 Read） |
> |---|---|---|
> | ① 語彙 gate（**L3/L2**） | **機能している** | `handoff.py:66-90` の fail-closed reject ＋ `schemas.py:157-161` の validator。ただし**選択規則（H-1）と座標 seam（H-4）の穴あり**（S-1 柱1） |
> | ② L1 collision_monitor（**L1**） | **二重に無機能** | (a) 停止円 `radius: 0.09` < M1 内接 0.1157 ＝**車体内部発火**（[collision_monitor.yaml:68](../../ws/src/warehouse_bringup/config/collision_monitor.yaml)）。(b) **`traffic_mode: open-rmf` の env では node ごと起動しない**——`config/stg/warehouse.yaml:12` / `config/prod/warehouse.yaml:13` が `open-rmf`（`config/dev/warehouse.yaml:21` のみ `none`）で、[nav2_bringup.launch.py:126](../../ws/src/warehouse_bringup/launch/nav2_bringup.launch.py) の `collision_active` が node（:211）と lifecycle manager（:222）の**両方**を gate する（H-11） |
> | ③ L0' 0.3m/s クランプ（**L0'**） | **未結線** | `clamp_body_velocity` は実装済・R-26 unit 済だが、`ws/src/warehouse_m1_driver/setup.py:24-26` は **`console_scripts: []`**（「serial driver node entry point は FUNC_MOTION framing スライスで land」）。**クランプを呼ぶ実行体が存在しない**＝現時点の消費者は unit test のみ（H-9 / S-8） |
>
> **したがって本書の 8 項目のうち S-3（C-3 改訂）・S-8（L0' 結線）は「改善提案」ではなく「欠落の補填」である。** この 3 行が §11 チェックリストの G-b / G-k / G-l に対応する。

「部屋転換で**新しく生じた／性質が変わった**ハザード」を列挙する。ジオラマでも部屋でも同じハザード（壁への衝突・棚への接触など）は、部屋転換の評価軸ではないため本表に載せない。

| # | ハザード | 関与 layer | 現行の緩和（CURRENT） | 残余リスク | 判定 |
|---|---|---|---|---|---|
| **H-1** | 召喚レグが**操作者の足元に最も近い waypoint** を選び、ロボットが人へ向かって走る | **L3**（候補生成規則）／到達集合は **L2** 契約が限定 | 到達集合は 9 キーに限定（`schemas.py` validator + L3 Validator の二重 reject）。**snap 半径は召喚レグに無い**（[09:111](09-hand-raise-summon.md)） | 「9 キーのうち最も人に近い 1 点」が選ばれることは止まらない（[09 R-7 :298](09-hand-raise-summon.md)）。ジオラマの盤縁という構造的な壁が消えた | **OPERATOR-GATE**（→ S-2） |
| **H-2** | L1 反射停止が**名目上だけ存在**する（停止ポリゴンが車体内部） | **L1**（`collision_monitor.yaml`） | `PolygonStop` circle `radius: 0.09`（[collision_monitor.yaml:63-68](../../ws/src/warehouse_bringup/config/collision_monitor.yaml)）。**しかも起動するのは `traffic_mode != open-rmf` の env のみ**＝H-11 | M1 内接 115.7mm（[23:585](../architecture/23-perception-and-localization.md)）を下回る＝**接触してからでないと発火しない**。人が走行面上に立つ部屋では多層防御の 1 枚が欠落 | **PHASE-1-GATE**（仕様は S-3・反応余裕は実測） |
| **H-3** | recovery（Spin / BackUp / DriveOnHeading）が **L1 を bypass** して人の近くで動く | **L1**（`nav2_bringup.launch.py` の recovery remap）／抑止設計は **L2**（Nav2 behavior） | ⑥ = **BYPASS で確定済**（[12:559](../architecture/12-infrastructure-common.md)）。残留リスクも記録済（[12:560](../architecture/12-infrastructure-common.md)） | 部屋では recovery の発火場面が「人が近い」場面と相関しやすい（[23 G-10 :755-761](../architecture/23-perception-and-localization.md)）。C-3 を直しても**この窓は塞がらない**。**さらに [12:559-560](../architecture/12-infrastructure-common.md) が bypass の安全床として挙げる「ESP32 L0 近接停止 ＋ Guardian `near_collision`」は M1 では両方不在**（L0 自前実装なし＝§0-2 / Guardian は人に盲目＝S-8 10-2①）＝**bypass 中の物理保護は 0 層** | **OPERATOR-GATE**（→ S-1 柱3） |
| **H-4** | 名前ゲートを通らない**座標ゴール seam** から走行面上の任意点（人のいる場所を含む）へ goal が入る | **L1 入口**（`warehouse_nav2_bridge`）／迂回されるのは **L2** 語彙 gate | 通常司令経路は `handoff.py` の `coordinate_goal_unfrozen` で fail-closed（[handoff.py:90](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/handoff.py)）。REST は loopback 127.0.0.1:8645 に限定 | seam 自体は生存し**座標の範囲チェックを持たない**（`warehouse_nav2_bridge/CLAUDE.md` の「#223 残 ③」）。ジオラマの「人は盤外」という暗黙の覆いが消えた | **OPERATOR-GATE**（→ S-4） |
| **H-5** | 立位の人を `/scan` が捉えても**止まれない**（検知 ≠ 停止） | **L1** | 2D LiDAR のスキャン面は人の下腿を横切る（[09 R-9 :316](09-hand-raise-summon.md)）＝検知側は有利 | スキャン平面の**実高さが未確定**（上面 147.5mm は上面であって平面ではない＝[23:495](../architecture/23-perception-and-localization.md) / OQ-15 [23:526](../architecture/23-perception-and-localization.md)）。停止距離・制動余裕も未実測 | **PHASE-1-GATE**（→ S-5） |
| **H-6** | スキャン面より低い物体・身体が検知されない（低く差し出された手／**這う乳幼児・小型ペット・床に座る/寝ている人**） | **L1**（水平 2D スキャン面の原理的限界） | 限界として明示済（[09:142](09-hand-raise-summon.md) / [09 R-3 柱4 :264](09-hand-raise-summon.md)）。3D 検知は nvblox の TARGET 機能で S1/S2 未通過 | **ジオラマとは性質が変わる**——盤上の「低い手」は一時的・意図的な動作だったが、部屋の床には**スキャン面より低い身体が持続的に存在しうる**（乳幼児・ペット・床座）。技術的緩和は無く**入室管理でしか塞げない**（S-6 P-2） | **OPERATOR-GATE**（→ S-6 P-2。**旧判定 PASS から格下げ**） |
| **H-7** | 操作者の常在位置がそのまま waypoint になり、召喚が「人のいる場所へ行く」動作になる | **L3**（候補集合）／値の所在は **L2 Contract**（`config/warehouse.base.yaml` の `locations`） | 規律として宣言済（[09 R-3 柱2 :262](09-hand-raise-summon.md)）。検証可能な形にはなっていない | 部屋 waypoint 9 点の値は**未確定**（Phase 1 SLAM 後）。規律が受け入れ条件として機械化されていない | **PHASE-1-GATE**（形式化案は S-7） |
| **H-8** | 第三者・ペット・撮影者など**運用規律の外にいる人**が走行面上に入る | 全層に対して**層外**（＝運用でしか塞げない） | 非統制変数として ADR が明記（[ADR-0009 :59](../adr/0009-m1-room-scale-operation.md)「人の往来」） | 規律群が未合意。docs に根拠のある規律と本書の提案が混在 | **OPERATOR-GATE**（→ S-6） |
| **H-9** | ホストプロセス死・USB 断で MCU に最後の指令が残り**暴走**する | **L0'**（ホストクランプ）の限界／本来は **L0** deadman の責務 | L0' は設計上「全 `cmd_vel` の単一絞り点」（[02:325-328](../shared/02-hardware-design.md)）。**ただし CURRENT では未結線**——`warehouse_m1_driver/setup.py:24-26` が `console_scripts: []` で serial driver node が存在せず、`clamp_body_velocity` の消費者は unit test のみ。加えて結線後も**ホストが生きている間だけ有効**（[02 P-7c](../shared/02-hardware-design.md)） | 公式 STM32 V3.6.5 に serial command timeout がなく `ENABLE_IWDG=0` と確定。L0 heartbeat deadman は「実 enforcement は Phase 1」（[12:79](../architecture/12-infrastructure-common.md)） | **PHASE-1-GATE**（→ S-8） |
| **H-10** | 到達点で**人の方を向かずに**停止する（yaw が落ちる）＝絵の問題だが、人との相対姿勢が設計不能 | **L1 入口**（`nav2_bridge` の `_pose` が `orientation.w=1.0` 固定） | ジオラマでは「goal は回転不要な向きで置く」で回避（[04 F-L3](../shared/04-diorama-layout.md)） | 部屋では回避手段が消える（[09 R-4 :269-271](09-hand-raise-summon.md)）。**安全ハザードとしては軽微**（停止位置は変わらない）だが、S-7 の配置規律と S-2 の到達圏設計に影響する | **PASS**（安全上の残余リスクは軽微・設計課題として S-7 へ送る） |
| **H-11** | **stg / prod では collision_monitor が起動せず、L1 反射が丸ごと不在**になる | **L1**（launch gating） | `collision_active = traffic_mode != 'open-rmf'`（[nav2_bringup.launch.py:126](../../ws/src/warehouse_bringup/launch/nav2_bringup.launch.py)）が node（:211）と lifecycle manager（:222）の両方を gate。Mode C では Open-RMF が交通調停を持つ前提（[12:550](../architecture/12-infrastructure-common.md) / [collision_monitor.yaml:22-25](../../ws/src/warehouse_bringup/config/collision_monitor.yaml)） | **その前提は「相手ロボットとの交通」の話であって「人」の話ではない**。`config/stg/warehouse.yaml:12` / `config/prod/warehouse.yaml:13` は `open-rmf`（`config/dev/warehouse.yaml:21` のみ `none`）＝**実機寄りの env ほど L1 が消える**。部屋デモをどの env / launch 引数で回すかが安全を直接左右する | **CONDITIONAL**（条件: 部屋デモの構成で `traffic_mode != open-rmf` を確認＝§11 G-k） |
| **H-12** | **誰も呼んでいないのに発進する**（骨格 NN の false positive → 語彙 gate は全通過） | 検出 **L4**（`gesture_detector`）／ 以降 **L3→L2→L1** は「正当な司令」として通す | 時間窓多数決・ヒステリシス・不応期（`hold_duration_s 1.2` / `min_frames_in_window 12` / `enter/exit_ratio 0.80/0.50` / `refractory_s 5.0`＝[09:117](09-hand-raise-summon.md)）と confidence 閾値 plugin（[09:118](09-hand-raise-summon.md)） | **語彙 gate も L2 も「誤検出」を検出できない**（形式的に正当な navigate に見える）。上記しきい値は**すべて例示値・未実測**（[09:117](09-hand-raise-summon.md) が「すべて例示値・実測で確定」と明記）。加えて `gesture_detector` は **`ws/` に未実装**（2026-08-18 grep）＝FP 率の実測手段がまだ無い | **PHASE-1-GATE**（受け入れ条件のみ定義可＝S-6 8-3 / §11 G-m） |
| **H-13** | **段差・階段・敷居からの落下**、および床材による制動距離の変動 | **L1**（`/scan` は水平面のみ＝床の穴を見ない）／ **L0** cliff センサ不在 | 走行領域は SLAM 地図の free 空間に限定され、Nav2 は地図外へ plan しない | **2D LiDAR は負の障害物（段差・下り階段）を原理的に検知しない**。cliff / 落下センサの設計記述は本 repo の docs に見当たらない（2026-08-18 grep 範囲）。走行領域の確定そのものが未決（[ADR-0009 §Open :76](../adr/0009-m1-room-scale-operation.md) `# TODO(ユーザー判断)`）。床材（カーペット/フローリング/ラグ）は制動距離を変える | **OPERATOR-GATE**（走行領域の物理的境界の決め方＝§11 G-h に含める。制動は S-5 M-3 で床材込み測定） |

> **表に載せなかったもの（意図的）**: nvblox dynamic 層の再評価（[23 G-9 :745-753](../architecture/23-perception-and-localization.md) OQ-23）は**知覚 / 予算の問題**であり、L1 反射経路には入れない方針（P1）ゆえ本安全レビューの直接対象にしない。ただし「static TSDF に人が焼き付く」は**走行品質**の問題として S-6 の運用規律と接する（後述）。

---

## 3. S-1 — R-3 多層防御論証の残余リスク評価（柱ごと）

**対象**: [09 R-3 :259-265](09-hand-raise-summon.md) が置いた 5 本の柱。[09 R-8-1 :306](09-hand-raise-summon.md) が「構造的保証から多層防御への**後退**であり同等性は主張しない」と訂正済みで、その後の**残余リスク評価**が本節である。

### 柱1: 到達集合の限定（**L3** Validator + **L2** Contract）— 判定 **CONDITIONAL**

**実効性（確認できたこと）**:
- 通常のジェスチャ司令経路では、座標は plan draft に**構造的に載らない**。`handoff.py` の `_FORBIDDEN_KEY_RULES`（[handoff.py:66-90](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/handoff.py)）が `goal` / `waypoint` / `coordinate` を含むキーを draft 構築前に fail-closed reject し（`coordinate_goal_unfrozen`・:90）、`schemas.py` の `_known_location` validator（[schemas.py:157-161](../../ws/src/warehouse_interfaces/warehouse_interfaces/schemas.py)・[09](09-hand-raise-summon.md) は :159 で参照）が KNOWN_LOCATIONS 外を型レベルで弾く。**INV-1（幾何は draft の外）は機械強制されている**（[09:25](09-hand-raise-summon.md)）。
- したがって「**人の現在位置へ連続座標で直接向かう**」経路は、司令経路には存在しない。この部分は部屋でも真である。

**穴（3 件）**:
1. **到達集合の限定は「人へ向かわない」を含意しない**。9 キーに限定されていても、**選ぶ規則が「人に最も近い 1 点」**なら結果は人へ向かう（[09 R-7 :298](09-hand-raise-summon.md)）。柱1 が保証するのは*集合*であって*選択*ではない。→ S-2。
2. **柱1 は seam 単位では成立していない**。座標ゴール seam（H-4）は同じ L2 語彙 gate を通らない。→ S-4。
3. **柱1 の実効性は waypoint の「値」に従属する**。9 キーの座標が部屋のどこに置かれるかで、「最も人に近い 1 点」がどれだけ人に近いかが決まる。値は Phase 1 未確定。→ S-7。

**条件**: (i) S-2 の召喚レグ再設計が land、(ii) S-4 の座標 seam 裁定、(iii) S-7 の配置規律が受け入れ条件として効く — **この 3 つが揃って初めて柱1 は「人へ向かって走らない」を支える**。

### 柱2: waypoint 配置の規律（**L3** の候補集合を**値**の側から制約）— 判定 **PHASE-1-GATE**

規律の内容（操作者常在位置を waypoint にしない・召喚到達先は操作者の手前で止まる配置にする）は妥当だが、**現状は散文であって検証手段が無い**。既存の機械ゲート `tests/unit/test_known_locations_navigable.py` は「障害物からのクリアランス」しか見ておらず、「人からの距離」は見ていない（そもそも人の位置は config に存在しない）。→ 形式化案は S-7。

### 柱3: L1 collision_monitor（**L1**）+ L0' 0.3 m/s クランプ（**L0'**）— 判定 **CONDITIONAL**

**確認できた良い点**:
- observation source のうち `virtual_scan` は単騎では相手機が無く常時 silent だが、`source_timeout: 0.0`（[collision_monitor.yaml:81-90](../../ws/src/warehouse_bringup/config/collision_monitor.yaml)）で stale-source STOP を無効化済み＝**単騎でも誤 STOP しない**。実 `/scan` は node-level `source_timeout: 1.0`（:58）で LiDAR 途絶→STOP が生きる。
- 出力は `cmd_vel/nav2` のみで `cmd_vel/emergency` を書かない＝**twist_mux prio100（FROZEN）を迂回しない**（[collision_monitor.yaml:55](../../ws/src/warehouse_bringup/config/collision_monitor.yaml)（`cmd_vel_out_topic`）/ [12:545](../architecture/12-infrastructure-common.md)）。部屋転換で壊れる前提は無い。

**穴（3 件）**:

1. **停止ポリゴンが車体内部**（H-2）。柱3 は「**C-3 改訂が land していれば**不変」と読むしかない（[09 R-8-2 :308](09-hand-raise-summon.md)）。→ S-3。

2. **⚠️ そもそも起動しない env がある**（H-11・**本レビューで実査**）。柱3 は「collision_monitor が動いている」ことを暗黙の前提にしているが、起動条件は `collision_active = traffic_mode != 'open-rmf'`（[nav2_bringup.launch.py:126](../../ws/src/warehouse_bringup/launch/nav2_bringup.launch.py)）で、これが **node（:211）と `lifecycle_manager_collision_monitor`（:222）の両方**を gate する。overlay の実値は:

   | env | `traffic_mode` | 実体 | collision_monitor |
   |---|---|---|---|
   | dev | `none` | [config/dev/warehouse.yaml:21](../../config/dev/warehouse.yaml) | **起動する** |
   | stg | `open-rmf` | [config/stg/warehouse.yaml:12](../../config/stg/warehouse.yaml) | **起動しない** |
   | prod | `open-rmf` | [config/prod/warehouse.yaml:13](../../config/prod/warehouse.yaml) | **起動しない** |

   （base 既定は `none`＝[config/warehouse.base.yaml:6](../../config/warehouse.base.yaml) だが、**overlay が後勝ち**＝[.claude/rules/environments.md](../../.claude/rules/environments.md)。）Mode C で gate off にする設計判断そのものは正当だが（Open-RMF が交通を持つ＝[12:550](../architecture/12-infrastructure-common.md)）、**その前提は「相手ロボットとの交通」の話であって「人」の話ではない**。ジオラマには走行面上に人がいなかったのでこの穴は表面化しなかったが、**部屋では「実機寄りの env ほど人に対する L1 反射が消える」**という倒錯が起きる。
   - **対応**: 部屋デモを回す env / launch 引数で `traffic_mode != open-rmf` を確認することを **§11 G-k** として登録する。Mode C を部屋で使いたいなら「Mode C 向け real-`/scan`-only collision_monitor 構成」（[12:550](../architecture/12-infrastructure-common.md) が Mode C impl 時へ defer した項目）を先に決める必要がある。
   - なお「X-lite では traffic 層（`warehouse_traffic`）が非アクティブ」という一般則（[.claude/rules/layer-annotation.md](../../.claude/rules/layer-annotation.md)）と、この launch gating は**別の話**である。混同しない。

3. **recovery は L1 を bypass する**（H-3）。[12:559](../architecture/12-infrastructure-common.md) が「⑥ = BYPASS」を確定済みで、その根拠は「monitor 経由にすると stop polygon 自身が recovery を 0 化して自己ラッチ deadlock する」——**この根拠はジオラマの 200mm 隘路（R-42）由来**である。部屋では隘路 deadlock の圧力が下がる一方、「人の脚の横で旋回する」という新しい抑止理由が立つ（[23 G-10 :757-759](../architecture/23-perception-and-localization.md)）。**C-3 を直しても bypass 窓は塞がらない**点が重要で、これは S-3 とは独立の裁定を要する。
   - **⚠️ さらに、bypass の安全床として挙げられた 2 つが M1 では両方不在**（本レビューで実査）。[12:559](../architecture/12-infrastructure-common.md) は bypass の安全性を「twist_mux emergency prio100 ＋ **ESP32 Layer0（≤0.3 m/s クランプ＋近接停止）が最終床**」で、[12:560](../architecture/12-infrastructure-common.md) は残留リスクを「bypass 中の物理近接ガードは **ESP32 proximity stop ＋ Guardian `amcl_pose near_collision`** に限定される」で説明している。しかし M1 では:
     - **ESP32 自前ファームが存在しない**。M1 の公式 STM32 source は入手済みだが、stock FW に本プロジェクトの近接停止・command-stream watchdog は無く、custom fork への置換も未実施（[02 P-7](../shared/02-hardware-design.md)）。代替の L0' は**さらに未結線**（§2 冒頭・S-8）。
     - **Guardian `near_collision` は人を見ない**（入力は `/{bot}/amcl_pose`・しきい値は 2 台間＝[config/warehouse.base.yaml:16](../../config/warehouse.base.yaml)。S-8 10-2①）。[12:561](../architecture/12-infrastructure-common.md) が「粗い backup として RETAIN」と決めた当のガードが、人に対しては存在しないに等しい。
     - **帰結: 部屋で recovery が発火している間、人に対する物理保護は 0 層**。残るのは Nav2 の bounded / 低速 / 短時間という性質と、運用規律 D-1 のみ。
   - 選択肢: **(A)** F-5-4 の抑止条件を「通路内で回転不可」から「人の近傍では recovery を発火させない」へ差し替える（[23 G-10 :761](../architecture/23-perception-and-localization.md) が Slice 1 で再設計せよと言っている線）／ **(B)** recovery 中のみ slowdown polygon 化・monitor 有効化（[12:560](../architecture/12-infrastructure-common.md) の Phase-2 再訪トリガ側の案）／ **(C)** 現状維持 ＋ 運用規律（走行中は進路に立ち入らない＝S-6 D-1）で覆う。
   - **推奨: (A) ＋ (C)**。(B) は bypass を作った当の deadlock 理由と正面衝突するため、部屋で deadlock 圧力が実際に下がることを実測してからでないと採れない。
   - **layer の帰属（訂正）**: recovery 抑止の実体は `behavior_server` / BT 設定・`nav2_params.yaml` / `nav2_bringup.launch.py` であり、正準対応表（[productization/01:185](../productization/01-commercial-box-map.md)）ではこれらは **L1**（Navigation: `warehouse_bringup/config/`）である。[23 G-10 :761](../architecture/23-perception-and-localization.md) は同じ項目を「**L2**（Nav2 behavior）」と注記しているが、**本書は正準対応表に従い L1 とする**（23 の当該行は main 由来のため本 PR では触らない＝注記に留める）。
   - 判定: **OPERATOR-GATE**。

### 柱4: 低い手・低い障害物の限界（**L1** の原理的限界）— 判定 **PASS**

[09:142](09-hand-raise-summon.md) / [09 R-3 柱4 :264](09-hand-raise-summon.md) の記述は部屋でもそのまま正確である。**「3D で人を検知して止まる」とは主張しない**・**骨格 NN を反射経路に入れない**（[23 §1 P1 :37-39](../architecture/23-perception-and-localization.md)）の 2 方針は環境非依存で、部屋転換によって前提を失っていない。ただし柱4 は*緩和*ではなく*限界の宣言*であり、**運用規律（S-6 D-2）でしか覆えない**ことを明記しておく。

### 柱5: 運用規律（層外）— 判定 **OPERATOR-GATE**

[09 R-3 柱5 :265](09-hand-raise-summon.md) の「走行中の robot の進路に立ち入らない」は 1 行の言い換えに留まる。部屋は非統制環境（[ADR-0009 :59](../adr/0009-m1-room-scale-operation.md)）であり、規律群として展開し合意を取る必要がある。→ S-6。

### S-1 総合判定: **CONDITIONAL**

**組み替え後の論証は、5 本の柱のうち柱1・柱3 が条件付き、柱2 が未確定値待ち、柱5 が未合意**である。すなわち **現時点で「多層防御が成立している」とは言えない**。[09 R-8-1 :306](09-hand-raise-summon.md) の「同等性は主張しない」に加え、本書は **「現時点では多層防御としても未完成である」** と記録する（悲観ではなく、S-2〜S-7 を閉じれば完成する形になっている、という意味である）。

---

## 4. S-2 — OQ-20: 召喚レグ解決規則の再設計（設計比較と推奨）

**問題の再確認**: 召喚レグは「人物胴中心を走行面へ正射影 → 最寄り location（**snap 半径を課さず sanity bound 2.0m のみ**）」（[09:111](09-hand-raise-summon.md)）。ジオラマでは正射影点が必ず盤外に落ち、盤縁が構造的な壁として働いていた。部屋では正射影点が**人の足元＝走行面の内側**に落ちる（[09 R-7 :297-298](09-hand-raise-summon.md)）。

### 4-1. 3 案の比較（**L3** 候補生成規則）

| 案 | 規則 | 部屋での効果 | 副作用・欠点 |
|---|---|---|---|
| **(i) snap 半径を導入** | 正射影点から半径 `r` 以内の location のみ候補。超えたら `clarify`（0 dispatch） | **安全対策としては無効**（危険ケースを残し安全ケースだけを削る＝下記 4-2）。**ただし有害ではない**（dispatch 集合は CURRENT の真部分集合） | demo が「近くに waypoint がある時だけ動く」になる＝**可用性を下げるだけ** |
| **(ii) 到達圏制約（候補フィルタ）** | 操作者の推定位置から**距離 `d_min` 以上**離れた location のみを候補にし、その中で最寄り | 「人へ向かう」を直接封じる。standoff を**配置ではなく候補フィルタ**で担保 | **`d_min` は standoff 相当パラメータ**＝[09:19](09-hand-raise-summon.md) / [09:141](09-hand-raise-summon.md) の「発明しない」と正面衝突（4-3） |
| **(iii) 両方** | `d_min ≤ d ≤ d_max` の帯に入る location のみ候補。帯が空なら `clarify` | 上下限が揃い fail-closed が明確 | パラメータ 2 個。ただし `d_max` は既存の sanity bound 2.0m を上限として再解釈できる |

### 4-2. **重要な分析結論: (i) 単独は「危険ケースを残し、安全ケースだけを削る」＝安全対策としては無効**

指差しレグの `snap_radius_m 0.25`（[09:111](09-hand-raise-summon.md)・実体は [config/warehouse.base.yaml:117](../../config/warehouse.base.yaml) に既存＝**新設パラメータではない**・同行に「例示値」と明記）は「**意図した交点の近く**に location があるときだけ動く」という意味で正しく働く——交点は人が指した先であり、人自身ではないからである。

しかし召喚レグの入力は**人の足元**である。ここに同じ半径フィルタを掛けると、「**人の足元の近く**に location があるときだけ動く」になる。すなわち **(i) は「人に近い waypoint」を排除せず、むしろ「人に近い waypoint が選ばれるケース」だけを通過させる**。半径を狭めるほど、通過するケースの goal は人に近くなる。

**正確な評価（危険度の比較）**: (i) の dispatch 集合は CURRENT（sanity bound 2.0m のみ）の**真部分集合**であり、両者が dispatch するケースでの**選択先は同一**である（どちらも「最寄り location」を選ぶ）。したがって **(i) は CURRENT より危険にはならない**——採用しても実害は無い。しかし削られるのは「人から遠い location しか無い＝比較的安全なケース」であり、**危険ケース（人のすぐ横に waypoint がある）はそのまま通る**。

> **結論: (i) は安全対策として無効（ただし有害ではない）。** H-1 のリスクを一切減らさないため、**(i) を入れたことをもって「召喚レグの安全問題に対処した」と扱ってはならない**。(i) に固有の価値があるとすれば「センサ誤差で遠方の location が選ばれる暴発の抑制」であり、それは既に `sanity bound 2.0m` が担っている役割の強化にすぎない。

この点は [09 R-7 :301](09-hand-raise-summon.md) が「(i) snap 半径の導入 / (ii) 到達圏の制約 / (iii) 両方」と 3 案を**等価な選択肢として**並列に置いていることへの**実質的な絞り込み**であり、本レビューの主要な追加分析である（3 案は等価ではなく、安全に効くのは (ii) の成分のみ）。

### 4-3. `standoff パラメータは発明しない`（[09:141](09-hand-raise-summon.md)）との整合裁定

- **禁止の原文**: [09:19](09-hand-raise-summon.md) の「発明しないもの」は **`standoff distance 独立パラメータ`** を挙げ、[09:141](09-hand-raise-summon.md) は「到達点は必ず 9 location ＝盤外の人へは構造的に到達できない。**standoff パラメータは発明しない**」と書く。
- **禁止の根拠が消えている**: :141 の禁止理由は「**構造的保証があるから standoff は不要**」であって、「standoff という概念が悪い」ではない。その構造的保証は部屋で消えた（[ADR-0009 :53](../adr/0009-m1-room-scale-operation.md) / [09 R-3 :257](09-hand-raise-summon.md)）。**前提が消えた禁止をそのまま維持すると、代替の防護が無い状態が固定される**。
- **一方で禁止を勝手に解くのは docs-first 違反**（[09 R-3 柱2 :262](09-hand-raise-summon.md) も「`standoff` パラメータは :141 のとおり発明しない——**配置で解く**」と、あくまで配置解を指定している）。
- **裁定が必要な問い（オペレーター）**: **部屋での standoff を「(α) waypoint 配置だけで担保する」か「(β) `d_min` を導入して L3 候補フィルタでも担保する」か。**
  - (α) の弱点: 配置は **Phase 1 実測後の 1 回きりの設計**であり、操作者の立ち位置が変われば崩れる。実行時のチェックが無い。
  - (β) の弱点: 新パラメータ＝[09:19](09-hand-raise-summon.md) の明示的禁止を解く（docs 改訂が要る）。加えて `d_min` の値そのものが未定＝ PHASE-1-GATE を 1 個増やす。
  - **推奨: (β)＝(iii) 案（下限 `d_min` の導入 ＋ 上限は既存 sanity bound の再解釈）を採り、(α) を第一防衛として併用する二重化。** 理由は S-1 柱1 の穴 3 が示すとおり、**配置単独では実行時に何も検査しない**ため。ただし禁止解除は docs 改訂（[09](09-hand-raise-summon.md) 末尾追補 or ADR）を先行させること。

### 4-4. (ii)/(iii) を採る場合の注意（本書で明記しておく限界）

- **「操作者の推定位置」の出所は骨格 NN（L4）**である。これを L3 の候補フィルタに使うことは、**反射安全に GPU を入れない方針**（[09:142](09-hand-raise-summon.md) / [23 §1 P1 :37-39](../architecture/23-perception-and-localization.md)）には**違反しない**（L3 は反射経路ではない）。ただし **NN が人物位置を誤れば `d_min` フィルタも誤る**。誤りは両方向に出るが、**危険側は「人が実際より遠くにいると推定され、近い waypoint が候補に残る」ケース**である。
- したがって (ii)/(iii) は **fail-closed で設計する**こと: 人物位置の推定信頼度が閾値未満・深度欠損・帯が空 → **`clarify`（0 dispatch）**。これは [09 §6 :117](09-hand-raise-summon.md)（fps 低下時は確定させない）・[09 §7 :131-133](09-hand-raise-summon.md)（CLARIFY / UNRESOLVED は 0 dispatch）の既存方針の延長であり、新方針の発明ではない。
- **`d_min` は「人に触れない距離」ではない**。ロボットは goal で止まるが、経路は goal 手前を通る。`d_min` が担保するのは*到達点*であって*経路*ではない。**経路上の接近は L1（S-3）と運用規律（S-6）の担当**である——この分担を混同しない。
- **⚠️ カメラ仰角の解 (a) と `d_min` の信頼性は相反する**。[09 R-2(a) :245](09-hand-raise-summon.md) は仰角問題の解として「操作者が **≈3.1m** 離れて立つ」を挙げるが、同行が「**キーポイント 3D 誤差 σ（OQ-7）が距離とともに悪化**する」と明記している。`d_min` フィルタの入力は人物推定位置なので、**(a) を採ると `d_min` の判定精度が最も悪い距離帯で運用することになる**。R-2 の仰角裁定（[09 OQ-16 :275](09-hand-raise-summon.md)）と S-2 の `d_min` 裁定は**独立に決められない**——(a) を採るなら `d_min` に σ 分のマージンを積む必要があり、その σ は [09 OQ-19 :278](09-hand-raise-summon.md)（距離 ~3m での σ 実測）の結果に従属する。
- **複数人が同時に挙手した場合は本節の射程外**（既に fail-closed）。[09 §7 :131](09-hand-raise-summon.md) が「複数人同時挙手 / 指差し曖昧（margin 不足）→ **CLARIFY → 0 dispatch**（勝手に選ばない）」を確定済みで、`d_min` の有無に関わらずロボットは動かない。**したがって本節が扱うのは「単独の操作者が呼んだとき、どの waypoint へ行くか」だけ**である。

### S-2 判定: **OPERATOR-GATE**

（裁定事項＝4-3 の (α)/(β)。推奨＝(β)＝(iii) 案 + 配置併用。`d_min` の値は裁定後の **PHASE-1-GATE**。実装は L3 の候補生成規則の変更＝別 PR で安全レビュー対象。）

---

## 5. S-3 — C-3: collision_monitor 停止ポリゴン改訂の要求仕様

**本節は仕様と受け入れ条件のみを定める。改訂の実装は別 PR**（L1 反射経路ゆえ L2 の costmap 変更と混ぜない＝[23 F-6 :624](../architecture/23-perception-and-localization.md)）。

### 5-1. 現状の無機能の確認（実 Read で裏取り）

| 量 | 値 | 出所 |
|---|---|---|
| CURRENT の `PolygonStop` | `type: "circle"` / `radius: 0.09` | [collision_monitor.yaml:63-68](../../ws/src/warehouse_bringup/config/collision_monitor.yaml) |
| その根拠（旧車体） | 旧 ~150mm 車体の内接 `ROBOT_RADIUS 0.075` ＋ 余裕 | [collision_monitor.yaml:65-67](../../ws/src/warehouse_bringup/config/collision_monitor.yaml) / [robot_dimensions.py:66](../../ws/src/warehouse_description/warehouse_description/robot_dimensions.py) |
| M1 内接半径 | **0.1157 m** | [23:585](../architecture/23-perception-and-localization.md) |
| M1 外接半径 | **≈0.184 m** | [23:584](../architecture/23-perception-and-localization.md) / [02:357](../shared/02-hardware-design.md) C-1 |

`0.09 < 0.1157` — **停止円が車体の内側に完全に収まる**。障害物が車体に接触するまで polygon 内に入らないため、L1 反射は物理的に発火し得ない。[23 G-8 :740-741](../architecture/23-perception-and-localization.md) の記述を実ファイルで確認した。

### 5-2. **本レビューで発見した前提不足: `CIRCUMSCRIBED_RADIUS` はまだ存在しない**

[23 G-8 :742](../architecture/23-perception-and-localization.md) は改訂先を「外接 `CIRCUMSCRIBED_RADIUS` ≈0.184 + 反応余裕」と書くが、**`warehouse_description/robot_dimensions.py` に定義されているのは `ROBOT_RADIUS = 0.075` のみで、`CIRCUMSCRIBED_RADIUS` も `FOOTPRINT_POLYGON` も存在しない**（2026-08-18 実査）。両定数の追加は [23 F-6 :625](../architecture/23-perception-and-localization.md) が **additive な contract PR（`contract` ラベル ＋ 依存トラック予告）** として計画している未 land 項目である。

> **帰結: C-3 改訂は単独では land できない。** 順序は **① F-6① の additive contract PR（`FOOTPRINT_POLYGON` / `CIRCUMSCRIBED_RADIUS`）→ ② C-3 改訂 PR（`CIRCUMSCRIBED_RADIUS` を消費）** となる。値をハードコードして ① を飛ばすことは、[02:325-328](../shared/02-hardware-design.md) / `safety.py` の「単一ソースから import・値の再定義禁止」（[safety.py:8-12](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py)）に反する。**このチェーンは §11 のチェックリストに載せる。**

### 5-3. 要求仕様（形・不変条件）

1. **包含条件（必須・不変条件）**: 停止領域は**実車体を必ず包含**すること。円形を維持するなら `radius ≥ CIRCUMSCRIBED_RADIUS`（≈0.184）。丸めは常に外側（切り上げ）＝[23 F-5-1 :614](../architecture/23-perception-and-localization.md) と同じ規律。
   - **⚠️ 「包含」だけでは不十分（前方バイアス多角形の穴）**: [12:561](../architecture/12-infrastructure-common.md) は「polygon を縮小／**forward-bias** する選択肢」を検討線上に置いており、`collision_monitor.yaml:31-37` のヘッダも「a forward-biased polygon may be needed」と書く。しかし**車体 footprint を含むだけの前方バイアス多角形**は、後方・側方の反応余裕が 0（車体表面と一致）でも仕様に適合してしまう。M1 は **BackUp / Spin recovery** を持ち（S-1 柱3）、**その場回転では車体四隅が外接円を掃く**（[23 G-10 :759](../architecture/23-perception-and-localization.md)）。
   - **したがって不変条件を強化する**: 「footprint を包含する」に加え、**ロボットが動きうる全方向（前進・後退・その場回転を含む）について、停止領域の境界が車体表面から `margin` 以上離れていること**。実質的には「外接円 + margin の円」がこれを最も単純に満たす。前方バイアスを採る場合は、**「後退・旋回を BT / behavior_server で禁止する」ことを同一 PR の前提条件として明記**しない限り採らない（＝S-1 柱3 の (A) と束ねる）。
2. **反応余裕（margin）の導出式**:

   ```
   margin = v_max × t_react          # v_max = 0.3 m/s（MAX_LINEAR_VELOCITY）
   radius ≥ CIRCUMSCRIBED_RADIUS + margin
   ```

   - `v_max = 0.3 m/s` の出所: [safety.py:18](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py)（`MAX_LINEAR_VELOCITY`・単一ソース）/ [.claude/rules/safety.md](../../.claude/rules/safety.md) / [config/warehouse.base.yaml:15](../../config/warehouse.base.yaml)。
   - `t_react`（`/scan` 上に障害物が現れてからモータが止まるまでの実効レイテンシ。scan 周期 + collision_monitor 処理 + `cmd_vel/nav2` 反映 + シリアル送出 + 機械制動の総和）**の実測値は docs のどこにも存在しない**。→ **PHASE-1-GATE として登録**（測定手順は S-5 7-2 と同一セッション）。
   - **ただし下界は repo から導出できる（本書で導出・入力は既存 doc）**:

     | 寄与 | 下界 | 出所 |
     |---|---|---|
     | `/scan` の 1 周期（最悪＝直前に通り過ぎた場合） | **167 ms**（6Hz）／ 83 ms（12Hz） | T-mini Plus 走査 6Hz（12Hz 選択可）＝[23 B-6 :494](../architecture/23-perception-and-localization.md) |
     | MCU auto-report / 指令反映の粒度 | **40 ms**（25Hz 固定） | [02:334](../shared/02-hardware-design.md) |
     | collision_monitor 処理 + DDS + twist_mux + シリアル送出 + 機械制動 | 未知（> 0） | — |

     → **`t_react` の下界は概ね 0.21 s（6Hz 構成）／ 0.12 s（12Hz 構成）** であり、実測値はこれを上回る。
   - **`例示値`（そのまま config へ写さないこと）**: 仮に `t_react = 0.25 s`（上記 6Hz 下界 0.21 s に処理・制動の余地を見た**本書の仮置き**）なら `margin = 0.075 m`、`radius ≈ 0.26 m`。**この 0.25 s は docs 由来の値ではない。** なお AMCL 正常間隔 100-200ms（[12:510](../architecture/12-infrastructure-common.md) の R-39 由来）は**別系統（pose 到着）のレートであって制動レイテンシではない**——流用してはならない。
   - **⚠️ scan rate の選択（6Hz / 12Hz）が L1 の反応余裕を直接決める。** [23 B-6 :494](../architecture/23-perception-and-localization.md) は 6Hz/12Hz を **V11a（MOLA-LO の motion skew）の観点で**実測して決めるとしているが、上表のとおり**同じ選択が L1 停止円の半径を ~0.03 m 動かす**。**localization 側の都合だけで決めない**こと——V11a と C-3 は同じ決定を共有する（§11 G-f に含める）。
3. **`min_points` の妥当性（本書で導出・入力は既存 doc）**: CURRENT は `min_points: 4`（[collision_monitor.yaml:70](../../ws/src/warehouse_bringup/config/collision_monitor.yaml)）。T-mini Plus の角度分解能は 0.54°/667 点（6Hz）または 1.08°/333 点（12Hz）（[23 B-6 :494](../architecture/23-perception-and-localization.md)）。人の脚（幅を 0.1m と仮定＝**一般値・repo 由来ではない**）は距離 0.25m で角度幅 ≈22.6°、距離 1.0m で ≈5.7° を占める。0.54° 刻みなら順に ≈42 点 / ≈10 点、1.08° 刻みでも ≈21 点 / ≈5 点。**いずれも `min_points: 4` を満たす**＝改訂後の停止円内に立つ脚は点数不足で見落とされない。**`例示値`（脚幅 0.1m の仮定に依存）**。→ 5-4 の実測で確認する。
4. **副作用の点検（必須）**: 半径を 0.09 → ~0.24 級へ拡げると、**壁・家具に近い waypoint で常時 STOP** し得る。ジオラマの拘束（R-42 200mm 隘路・#156 の 0.15m head-on demo＝[collision_monitor.yaml:31-37](../../ws/src/warehouse_bringup/config/collision_monitor.yaml)）は部屋では消えるが（[23 G-2 表 F-2 下段行 :675](../architecture/23-perception-and-localization.md)）、**部屋固有の拘束（壁沿い waypoint・ドア開口通過）に置き換わる**。→ S-7 の配置規律 W-b と**同一 PR で整合を取る**こと。
5. **単騎での source 構成**: `virtual_scan` は相手機不在で常時 silent。`source_timeout: 0.0`（[collision_monitor.yaml:90](../../ws/src/warehouse_bringup/config/collision_monitor.yaml)）が既にあるため改訂不要。**実 `/scan` の node-level `source_timeout: 1.0`（:58）は維持**（LiDAR 途絶 → STOP は部屋でこそ重要）。

### 5-4. 受け入れ条件（改訂 PR の DoD 候補）

- [ ] **R-26 safety unit**（独立オラクル・mutation で赤くなる＝[.claude/rules/safety.md](../../.claude/rules/safety.md) / [doc20 §9](../architecture/20-dev-quality-and-testing.md)）で **`PolygonStop.radius ≥ CIRCUMSCRIBED_RADIUS`** を pin する。オラクルは `robot_dimensions` の定数から**独立に**（yaml をパースして比較）取ること。
- [ ] `CIRCUMSCRIBED_RADIUS` を**ハードコードせず import**（5-2 の順序制約）。
- [ ] 出力トポロジ不変の回帰: `cmd_vel_out_topic == cmd_vel/nav2` かつ `cmd_vel/emergency` を書かないこと（[12:545](../architecture/12-infrastructure-common.md) / 既存 R-26）。
- [ ] 誤発火試験（sim / live）: 部屋想定のクリアランスで通常走行が STOP でラッチしないこと。
- [ ] `t_react` 実測値を doc に記録し、`margin` の導出を config コメントに残す（値の出所を追える形）。

### S-3 判定: **PHASE-1-GATE**

（形・不変条件・受け入れ条件は本書で確定。**`t_react` の実測が無ければ `radius` の値は決められない**。加えて 5-2 の contract PR 前提あり。）

---

## 6. S-4 — #223 座標ゴール seam の到達集合監査

### 6-1. 到達集合の監査（seam ごと・実 Read で裏取り）

| 呼び出し経路 | 語彙 gate を通るか | 座標範囲チェック | 部屋での到達集合 |
|---|---|---|---|
| ジェスチャ司令（L4→L3→L2→L1） | **通る**（`handoff.py:66-90` → L3 Validator → `schemas.py:157-161` → Policy Gate `known_locations`） | 不要（座標を持たない） | KNOWN_LOCATIONS 9 キーのみ |
| ER 司令 / LLM 司令（Mode A/C） | **通る**（同上） | 同上 | 同上 |
| **REST `/api/v1/navigate` の `goal[x,y]`（#223 additive）** | **通らない**（`destination` を使わないため `_coord` を経由しない） | **無い**（`warehouse_nav2_bridge/CLAUDE.md` 「#223 残 ③」= map 範囲は本パッケージが所有しない＝発明しない） | **走行面上の任意点**（人のいる場所を含む） |

**呼び出し元の実在**（監査で確認）:
- `HeadOnInjector`（`warehouse_nav2_bridge` の #223 座標スワップ直列化器）＝**2 台 head-on デモ専用**。単騎・部屋では使わない。
- `scripts/slice3_inject_swap.sh`（live operator wrapper・REST を直叩き）＝**operator tooling**。
- 形式検証は `INVALID_GOAL`(400)（destination/goal の両方・両無・不正座標）＝**値の妥当性ではなく形の妥当性**しか見ない。

**露出範囲**: REST は **loopback `127.0.0.1:8645`**（co-located MCP 前提・`warehouse_nav2_bridge/CLAUDE.md` の produce 節）。ネットワーク越しの第三者からは叩けない。したがって **残余リスクは「オペレーター／スクリプトの誤操作」に限定**される——ジオラマでは誤操作しても盤外へは出られず、人は盤外にいた。**部屋ではその覆いが無い**（[09 R-8-3 :310](09-hand-raise-summon.md)）。

### 6-2. 緩和選択肢の比較（**L1 入口** = `warehouse_nav2_bridge`）

| 案 | 内容 | 長所 | 短所 |
|---|---|---|---|
| **(A) seam の config gate 化** | 座標 `goal` の受理を config フラグで既定 OFF にし、operator tooling 使用時のみ明示 ON | 低コスト・fail-closed・**seam を消さずに封じる**（#223 の資産を捨てない）・所有境界内（`warehouse_nav2_bridge` 自身の API） | 新 config キー＝docs 追記が要る（additive・[08 §3](08-x-er-bridge-node-spec.md) と同型の手順） |
| **(B) valid-polygon 検証を追加** | 走行可能領域の polygon 内のみ受理 | 意味的に最も強い | **所有境界の破壊**——`warehouse_nav2_bridge` は map/valid polygon を所有しない（「#223 残 ③」が明示）。valid polygon は calibration artifact 側の責務（[09:155](09-hand-raise-summon.md)）＝**二重定義になる** |

**⚠️ 「#223 残 ③」の緩和根拠は部屋では成立しない（本レビューで確認）**: 原文は「**座標範囲チェックなし**（map 範囲は本パッケージが所有しない＝**Nav2 planner が到達不能を弾く**。発明しない）」（`warehouse_nav2_bridge/CLAUDE.md` の「#223 残」節）。すなわち「範囲チェックが無くても planner が守る」という緩和が付いている。**この緩和は部屋の人に対しては働かない**——部屋の空床に立っている人は **static map 上の障害物ではない**（走行可能セルとして地図に載っている）ため、その座標を goal にしても **plan は成立し、planner は弾かない**。ジオラマでは「盤外の座標＝地図外＝到達不能」で planner が実質的な壁になっていたが、**部屋では人の立つ場所こそ最も reachable な自由空間**である。したがって残 ③ は「未実装だが planner が代替している」ではなく「**部屋では代替が無い**」と読むべきで、これは推奨案 (A) を強める根拠になる。
| **(C) 運用規律のみ** | 部屋デモ中は座標経路を使わない・operator tooling 限定 | ゼロコスト | 強制力ゼロ。誤操作を止めない |

**推奨: (A) を第一、(C) を併用。(B) は非推奨。** (B) が魅力的に見えるのは「範囲チェックが無い」という指摘への直訳だからだが、範囲を知っているのは L3 の Visual Resolver / calibration artifact であって L1 入口ではない。ここに polygon を持たせると、部屋レイアウトの真実が 2 箇所に分裂する。

### 6-3. 付随所見（安全ではなく設計）

`#223 残 ②`（yaw drop・`orientation.w = 1.0` 固定）は H-10 のとおり**安全ハザードとしては軽微**（停止位置は変わらず、向きだけが設計不能）。ただし (A) を採ると座標経路が既定 OFF になるため、**「人の方を向いて止まる」を座標ゴールで実現する道も同時に閉じる**。yaw 対応は `_pose` の quaternion 化＝別変更・所有トラック判断（[ADR-0009 帰結③ :43](../adr/0009-m1-room-scale-operation.md) / [09 R-4 :271](09-hand-raise-summon.md)）であり、**(A) の裁定と yaw 対応の要否は同時に検討すると良い**（片方だけ決めると手戻りする）。

### S-4 判定: **OPERATOR-GATE**

（裁定事項＝(A)/(B)/(C) の選択。推奨＝(A)+(C)。実装は `warehouse_nav2_bridge` 所有トラックの別 PR。）

---

## 7. S-5 — OQ-21: 立位の人に対する L1 有効性（分析 ＋ 測定手順）

### 7-1. 分析（幾何学的な検知可能性）— [09 R-9](09-hand-raise-summon.md) の主張を支持する

- **スキャン面の高さは「上面 147.5mm」であって平面高さではない。** [02:302](../shared/02-hardware-design.md) が与えるのは「LiDAR **上面** 147.50mm」であり、[23:495](../architecture/23-perception-and-localization.md) は明示的に「**上面 ≠ スキャン平面**」と注記する。実平面高さは **OQ-15**（[23:526](../architecture/23-perception-and-localization.md)）で未確定。
- **それでも結論は robust**: 成人の下腿（脛）は床上おおむね 0.1–0.5m の帯を占める（**一般値・本 repo docs 由来ではない**）。スキャン平面が上面 147.5mm より下（LiDAR 筐体内のどこか）であっても、**この帯の内側に収まる**公算が高い。すなわち「**立位の人は `/scan` に写る**」という [09 R-9 :316](09-hand-raise-summon.md) の主張は、平面高さの不確かさに対して頑健である。
  - ただし**頑健さの限界**を正直に書く: スキャン平面が極端に低い（床上数 cm）場合、**足首より下**を見ることになり、点数と安定性が落ちる。逆に極端に高い場合は膝上を見る。どちらでも脛帯を外れはしないが、**`min_points` の充足性は距離依存**（S-3 5-3③ の導出）なので実測で確認する。
- **検知 ≠ 停止**（[09 R-9 :318](09-hand-raise-summon.md)）。C-3 未改訂の間は、脚が `/scan` に写っても停止領域が車体内部にあるため止まらない。**S-5 の価値は S-3 が land して初めて実現する。**
- **⚠️ 立位を前提にしている（(c) 採用時は再評価）**: 本節の結論は「操作者が**立っている**」ことに依存する。[09 R-2 :247](09-hand-raise-summon.md) はカメラ仰角の解の 1 つとして **(c) 操作者がしゃがむ / 座る**を挙げており、**これが採られると本節の前提が崩れる**——しゃがんだ人の胴・膝はスキャン平面より低くなりうる（H-6 と同じ穴に落ちる）。[09 OQ-16 :275](09-hand-raise-summon.md) の仰角裁定が (c) に着地した場合、**S-5 の「支持」判定は再評価が要る**。
- **人の位置を 3D で解釈して止める設計は採らない**（[09:142](09-hand-raise-summon.md) / [23 §1 P1 :37-39](../architecture/23-perception-and-localization.md)）。本節が主張するのは **2D LiDAR が立位の人体を捉えるという幾何学的事実のみ**である。

### 7-2. 測定手順の定義（PHASE-1-GATE・S-3 の `t_react` と同一セッション）

> **前提**: 実機 M1 ＋ 部屋。**人を使う試験は最後**に置き、それ以前は器物で行う（安全側）。速度は常に ≤0.3 m/s（[safety.py:18](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py)）。

| # | 測定 | 手順 | 出力（どの gate へ供給するか） |
|---|---|---|---|
| **M-1** | **スキャン平面の実高さ**（OQ-15 と同時） | 既知高さの薄板ターゲットを床から段階的に上げ／下げ、`/scan` に現れる/消える境界高さを記録（複数距離で） | OQ-15 の確定・7-1 の前提検証 |
| **M-2** | **脚相当ターゲットの点数** | 人の脛に相当する直径の円柱を距離 0.25 / 0.5 / 1.0 / 2.0m（**例示値**）に置き、polygon 内点数を記録。**判定に効くのは改訂後の停止円内（~0.26m 級）の点数だけ**——1.0 / 2.0m は停止円外で `min_points` 判定に関与しないため、costmap / planner 側の参考データとして扱う | `min_points`（現 4）の妥当性＝S-3 5-3③ の実測置換 |
| **M-3** | **停止距離 `d_stop`** | 静止した器物ターゲットへ 0.3 m/s で直進 → STOP 発火位置から静止までの距離を n 試行（n は実施者判断）記録。中央値と最悪値。**実運用と同一の床材で測る**（カーペット / フローリング / ラグで制動距離は変わる＝H-13）。**scan rate 6Hz / 12Hz の両方**で測る（S-3 5-3② の結合） | `t_react = d_stop / v` を逆算 → **S-3 の `margin`** ／ scan rate の裁定 |
| **M-4** | **制動余裕の検証** | 改訂後の `radius` で `radius − CIRCUMSCRIBED_RADIUS − d_stop ≥ 0` を確認 | C-3 改訂 PR の受け入れ条件 |
| **M-5** | **立位の人での確認**（最後・器物試験が全て緑になってから） | 静止した人の前方へ低速接近し STOP を確認。**人は静止・退避可能な位置・実施者が別に電源断できる状態**で行う | [09 OQ-21 :320](09-hand-raise-summon.md) の閉じ |

> **本書は `d_stop` / `t_react` / 平面高さのいずれについても数値を置かない。** 置けば発明になる（[.claude/rules/docs-first.md](../../.claude/rules/docs-first.md)）。

### S-5 判定: **PHASE-1-GATE**

（分析部分＝「立位の人は幾何学的に `/scan` に写る」は**支持**。ただし「止まれる」は S-3 に従属し、値は M-1〜M-5 の実測待ち。）

---

## 8. S-6 — 運用規律の部屋版（docs 根拠のあるもの / 本書の提案）

**位置づけ**: 運用規律は**どの layer にも属さない（層外）**。層で塞げない残り（H-6 の低い手・H-8 の第三者・H-3 の recovery 窓）を覆う最後の手段であり、**技術的緩和の代替ではない**。

### 8-1. docs に根拠のある規律（D 系列）

| # | 規律 | 出所 | 部屋版の言い換え |
|---|---|---|---|
| **D-1** | 走行中の robot の進路に立ち入らない | [09 R-3 柱5 :265](09-hand-raise-summon.md) | そのまま。**recovery 中も含む**（H-3 の窓を覆うのはこれ） |
| **D-2** | 低い手・低い物を差し出すのは robot 停止時のみ | [09:142](09-hand-raise-summon.md)（「盤面への手入れは robot 停止時のみ」の部屋版） | 走行面に手・足・物を置くのは停止確認後 |
| **D-3** | 操作者の立ち位置を**床マーキング**で拘束する | [09:41](09-hand-raise-summon.md)（「立ち位置は床マーキングの運用規律で拘束する」＝**ジオラマ時点で既に存在する規律**） | 部屋では**距離要件と結合**する（[09 R-2(a) :245](09-hand-raise-summon.md) の「仰角 22° 相当に戻すには ≈3.1m」など、S-2 の `d_min`・S-7 の W-a と同一の床マークで表現する） |
| **D-4** | 操作者常在位置を waypoint にしない | [09 R-3 柱2 :262](09-hand-raise-summon.md) | S-7 W-a として形式化 |
| **D-5** | 環境が非統制であることを前提に運用する（照明・床材・**人の往来**・家具移動） | [ADR-0009 トレードオフ :59](../adr/0009-m1-room-scale-operation.md) | デモ前に走行領域の状態を確認する（家具位置が地図と一致しているか） |

### 8-2. 本書の**提案**（docs に根拠が無い＝要オペレーター採否）

> 以下は **提案**である。docs 正本に無い規律をここで確定させない。

| # | 提案 | 提案理由（本書の分析） | 注意 |
|---|---|---|---|
| **P-1** | **停止手段の到達性を確保する**（実施者が電源／USB を即座に断てる位置に立つ、または物理 E-stop の設置を検討する） | **本 repo の docs に物理 E-stop ボタンの記述は見当たらない**（2026-08-18 grep）。存在するのは ①ソフト estop（Emergency Guardian → `cmd_vel/emergency` prio100）②L0' ホストクランプ ③電源系の物理断（[02:329](../shared/02-hardware-design.md) が「電源系での縮退で補う」と言及）。H-9 のとおり **ホスト死時に効く手段が電源断しか無い可能性がある** | 物理 E-stop の追加は**ハードウェア設計の変更**＝[02](../shared/02-hardware-design.md) 所有トラックの判断。本書は要否を決めない |
| **P-2** | **第三者・ペット・乳幼児の入室管理**（デモ中は走行領域への出入りを制限し、実施者以外の在室者に D-1/D-2 を事前共有する） | ADR が「人の往来」を非統制変数として挙げる（[ADR-0009 :59](../adr/0009-m1-room-scale-operation.md)）に留まり、**規律としては未定義**。H-8 は技術的に塞げない。**さらに H-6（スキャン面より低い身体）は乳幼児・小型ペット・床に座る人に対して技術的緩和が一切無い**——2D LiDAR の平面より低いものは見えず、3D 検知は S1/S2 未通過（[09:142](09-hand-raise-summon.md)）。**入室管理が唯一の緩和**である | ペット・乳幼児は D-1 を理解しない＝**入室させない**以外の緩和が無い。**H-6 が PASS から OPERATOR-GATE へ格下げされた実質はこの 1 行**である |
| **P-3** | **撮影者の立ち位置を waypoint 集合と分離する**（カメラ位置・三脚も障害物・常在位置として扱う） | 撮影構図は ADR-0009 の Open（[:77](../adr/0009-m1-room-scale-operation.md)）。撮影者は「操作者ではない常在者」であり D-3/D-4 の射程外 | S-7 W-a の「操作者常在位置集合 O」に**撮影ポジションを含めるか**が設計判断 |
| **P-4** | **走行前に地図と実環境の一致を確認する**（家具が動いていれば SLAM 地図を取り直す） | D-5 の実行手順化。加えて [23 G-9 :745-753](../architecture/23-perception-and-localization.md)（人が static TSDF に焼き付く）が示すとおり、**人が居る状態で地図を作らない**ことが要る | nvblox 採否（OQ-23）とは独立に、2D SLAM 地図取得にも同じ注意が要る |

### S-6 判定: **OPERATOR-GATE**

（D-1〜D-5 は既存 docs の集約＝そのまま採用可。**P-1〜P-4 の採否がオペレーター裁定**。特に P-1 は H-9 と対であり、stock FW に command watchdog が無いと確定したため G-g 完了までは必須に近い。**P-2 は H-6 の唯一の緩和**であり、選択ではなく前提条件に近い。）

---

## 9. S-7 — waypoint 配置規律の形式化（Phase 1 実測時の受け入れ条件）

**目的**: [09 R-3 柱2 :262](09-hand-raise-summon.md) の散文規律を、**Phase 1 で `config/warehouse.base.yaml` の `locations` 9 点を実測値へ差し替える PR の受け入れ条件**として書ける形にする。既存の機械ゲート `tests/unit/test_known_locations_navigable.py`（[23 G-7 :728](../architecture/23-perception-and-localization.md) が参照）の拡張として設計する。

**既存ゲートが今なにを見ているか（実 Read・4 テスト）**:

| テスト（同ファイル内の関数） | 検証内容 | W 系列との対応 |
|---|---|---|
| `test_every_known_location_cell_is_free` | 9 点の goal セルが `map.pgm` の free セルであること | W-b の前段 |
| `test_every_known_location_clears_the_inscribed_radius` | 最近接占有セル中心までの距離 ≥ `ROBOT_RADIUS + 1 cell` | **W-b の現行版**（オラクルが円形 0.075 固定＝footprint 化が要る） |
| `test_named_location_acceptance_disks_are_disjoint` | goal_checker の `xy_goal_tolerance` 由来の受理円が互いに重ならないこと | **W-c の既存の弱い版**（「受理円が重ならない」であって「間隔 ≥ 2×交点誤差」ではない。ジェスチャの交点誤差は考慮していない） |
| `test_all_known_locations_share_one_traversable_component` | 9 点が単一の走行可能連結成分に属すること | 部屋でもそのまま有効（W 系列に対応物なし＝追加要件ではなく既存要件として維持） |

**すなわち W-a / W-b'（人からの距離・停止円との両立）に対応する既存テストは存在しない**——これが本節が新規に定義する部分である。

### 9-1. 形式化案（W 系列）

| # | 条件 | 形式 | 値の出所 | 状態 |
|---|---|---|---|---|
| **W-a** | 各 waypoint は**操作者常在位置集合 `O`** から `d_min` 以上離れている | `∀w ∈ locations, ∀o ∈ O : dist(w, o) ≥ d_min` | `d_min` = S-2 (β) を採る場合の到達圏下限と**同一パラメータ**（二重定義しない）。`O` = D-3 の床マーキング位置 | **OPERATOR-GATE**（`O` を config 化するか運用規律のみで持つか）＋ **PHASE-1-GATE**（値） |
| **W-b** | 各 waypoint の障害物クリアランスが車体を包含する | `clearance(w) ≥ 内接 0.1157`。その場回転を要する waypoint は `≥ 外接 0.184` | [23:584-585](../architecture/23-perception-and-localization.md)。既存 test の `ROBOT_RADIUS + 1 cell` オラクルを **`FOOTPRINT_POLYGON` 由来へパラメータ化**（[23 G-4 :694](../architecture/23-perception-and-localization.md) / [04 W3](../shared/04-diorama-layout.md)） | **PHASE-1-GATE**（部屋 map 取得後）／ 前提に F-6① contract PR（S-3 5-2 と同じチェーン） |
| **W-b'** | **C-3 改訂後の停止円と両立する** | `clearance(w) ≥ PolygonStop.radius`（さもないと waypoint 到達と同時に L1 が STOP でラッチする） | S-3 の `radius`（未確定） | **PHASE-1-GATE**（S-3 に従属。**本書が新たに指摘する結合**） |
| **W-c** | waypoint 間隔が交点誤差の 2 倍以上 | `∀ w1≠w2 : dist(w1,w2) ≥ 2 × σ_交点`（≈0.5m 相当） | [09:113](09-hand-raise-summon.md) OQ-11 の申し送り。**宛先はジオラマ再設計から部屋 waypoint 設計へ差し替え済**（[09 R-2④ :253](09-hand-raise-summon.md)） | **PHASE-1-GATE**（σ は OQ-7 / OQ-19 の実測） |
| **W-d** | sentry pose の 3 条件を満たす waypoint が存在する | 9 キー内 ∧ 操作者側への視線 ∧ **召喚到達先になりにくい**（同一だと `duplicate_destination` reject で「呼んだのに動かない」） | [09:44](09-hand-raise-summon.md) / [09 R-2② :249](09-hand-raise-summon.md)。reject 機構は `policy_gate.py` の duplicate check | **PHASE-1-GATE** |
| **W-e** | 召喚の到達先が 2 点以上あり「左右で行き先が変わる」意味論を保つ | 少なくとも 2 つの waypoint が W-a を満たしつつ操作者の左右に分かれる | [09:45](09-hand-raise-summon.md) / [09 R-2③ :251](09-hand-raise-summon.md) | **PHASE-1-GATE** |

### 9-2. **W-a の実装形が未決である点（正直に）**

W-b〜W-e は既存 config（`locations`）と既存 map だけで機械検証できる。**W-a だけは「操作者常在位置 `O`」という現在 config に存在しない情報を要求する。** 選択肢:

- **(α) `O` を config 化する**（例: `locations` とは別の additive キー）→ 機械検証可能になるが、**新 config キーの発明**＝docs 追記が要る。加えて「人の立ち位置を config に固定する」ことは D-3（床マーキング）と 1:1 対応する必要がある（両者がズレたら意味を失う）。
- **(β) `O` を config 化せず、W-a を PR レビューの目視条件に留める**→ 発明ゼロだが、機械ゲートにならない。

**推奨: 判断を S-2 と束ねる。** S-2 で (β)＝`d_min` 導入を採るなら、`d_min` と `O` は L3 候補フィルタの入力として**どのみち実行時に必要**になるため、(α) が自然に従う。S-2 で (α)＝配置のみを採るなら、W-a も (β) の目視条件で整合する。**S-2 と S-7 W-a は同じ裁定の表裏である**——別々に決めると矛盾する。

### 9-3. 未評価の衝突可能性: `charging_station`（記録のみ）

W-a（waypoint は操作者常在位置から `d_min` 以上離す）は、**物理的な置き場所の制約を持つ waypoint と衝突しうる**。とくに `charging_station` は充電器・ケーブルの位置に縛られ、その位置は「人が居ない場所」とは限らない（部屋では壁のコンセント脇＝人の動線上でありうる）。同様に `shelf_*` / `berth_*` も、部屋で何を指すかがまだ決まっていない（[ADR-0009 §Open :79](../adr/0009-m1-room-scale-operation.md) `# TODO(実装スライス)`「9 キーの部屋での役割名の意味づけ」）。

**本書はこの衝突を評価していない**（部屋の家具配置・充電器位置が未確定のため評価しようがない）。**W-a を適用する際に「物理制約で動かせない waypoint が `d_min` を満たさない」ケースが出た場合、それは配置の失敗ではなく `d_min`／運用規律（`O` の定義）側で解くべき**である、とだけ記録しておく。→ §11 G-e の一部。

### S-7 判定: **PHASE-1-GATE**

（W-b〜W-e は Phase 1 実測後に機械化可能。**W-a の実装形は S-2 と束ねた OPERATOR-GATE**。W-b' は本書が新たに指摘した S-3 との結合であり、C-3 改訂 PR と waypoint 確定 PR の順序に効く。）

---

## 10. S-8 — E-stop / Guardian 経路の部屋での妥当性確認（点検結果）

**想定は「影響なし」**であったが、**点検の結果 5 件の限定が見つかり、うち 1 件（L0' 未結線）は「限定」ではなく欠落**であった。

### 10-1. 点検した経路と結果

| 経路 | layer | 部屋転換で前提を失うか | 所見 |
|---|---|---|---|
| **twist_mux emergency prio100（FROZEN）** | **L1** | **失わない（が部分故障モードあり）** | 環境非依存の優先度契約。collision_monitor は `cmd_vel/nav2` のみに書き prio100 を迂回しない（[collision_monitor.yaml:55](../../ws/src/warehouse_bringup/config/collision_monitor.yaml)（`cmd_vel_out_topic`）/ [12:545](../architecture/12-infrastructure-common.md)）。**PASS**（ただし 10-2④ の Guardian 単独死モード） |
| **Emergency Guardian の pose freshness guard ＋ 変位ゲート** | **L1** | **失わない** | ゲートの根拠は AMCL が motion-gated であること（`update_min_d 0.05` / `update_min_a 0.2`）＝**車体とアルゴリズムの性質**であり環境に依らない（[12 末尾追補](../architecture/12-infrastructure-common.md) / [config/warehouse.base.yaml:26-29](../../config/warehouse.base.yaml)）。**PASS** |
| **Guardian の `near_collision`（2 台間近接監視）** | **L1** | **前提を失ってはいないが、寄与がゼロ** | しきい値は `emergency_min_distance: 0.3 # m（2台間）`（[config/warehouse.base.yaml:16](../../config/warehouse.base.yaml)）で、入力は `/{bot}/amcl_pose`。**単騎では相手機が無く沈黙**し、**人は AMCL pose を持たないので Guardian からは見えない**。→ **「Guardian は人に対して構造的に無力」**（下記 10-2①） |
| **L0' 0.3 m/s ベクトルクランプ** | **L0'** | **⚠️ 前提以前に未結線** | 設計上は全 `cmd_vel` の単一絞り点（[02:325-328](../shared/02-hardware-design.md)）で、純関数 `clamp_body_velocity` は実装済・R-26 unit 済（`warehouse_m1_driver` の `clamp.py`）。値は単一ソース `MAX_LINEAR_VELOCITY = 0.3`（[safety.py:18](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py)）。**しかし `ws/src/warehouse_m1_driver/setup.py:24-26` は `console_scripts: []`**（「No console_scripts yet: this slice ships the pure L0' clamp only. The serial driver node entry point lands with the FUNC_MOTION framing slice.」）＝**クランプを呼ぶ実行体が無い**（消費者は unit test のみ）。→ **CONDITIONAL**（10-2②） |
| **L0 heartbeat / watchdog deadman** | **L0** | **stock FW には存在しない** | [12:79](../architecture/12-infrastructure-common.md) が Layer 0 の責務としつつ「**実 enforcement は Phase 1**」。公式 STM32 V3.6.5 は serial command timeout を持たず、`ENABLE_IWDG=0`。→ G-g で command-stream watchdog の実装・host test・USB 抜線試験が必要（[02 P-7c](../shared/02-hardware-design.md)） |
| **物理 E-stop ボタン** | — | **そもそも docs に記述が無い** | 2026-08-18 に `docs/shared/02` / `docs/architecture/12` / `.claude/rules/safety.md` を grep した範囲で、物理 E-stop ボタンの設計記述は見当たらない。→ S-6 P-1 の提案根拠 |

### 10-2. 記録すべき 3 件の限定

1. **Guardian は人に対して寄与ゼロ。** これは欠陥ではなく設計どおり（Guardian の近接監視は AMCL pose を持つ**ロボット同士**の距離監視である）。しかし [ADR-0009 帰結⑦ :53](../adr/0009-m1-room-scale-operation.md) の「残る保護は語彙 gate ＋ L1 collision_monitor ＋ L0' クランプ」という列挙は正確であり、**Guardian をこの列挙に足してはならない**（人に対する保護としては数えられない）。本書はこの点を明示的に記録する。
2. **⚠️ L0' は「限界が重い」以前に、まだ結線されていない。** ESP32 自前ファームの世界では「上位が全滅しても MCU が止める」（[12:112](../architecture/12-infrastructure-common.md)）が成立したが、M1 では最終防衛線が **L0'（ホスト側 driver 送信直前クランプ）** へ移った（[02:325-328](../shared/02-hardware-design.md)）。本レビューで実体を確認したところ:
   - `clamp_body_velocity`（純関数・非有限→停止・`hypot` ベクトルクランプ）は**実装済で R-26 unit も通っている**。
   - **しかし `ws/src/warehouse_m1_driver/setup.py:24-26` の `console_scripts` は空**で、serial driver node は「FUNC_MOTION framing スライスで land」と明記されている。すなわち **`/cmd_vel` を受けて `FUNC_MOTION` フレームを送る実行体そのものがまだ存在せず、クランプは呼ばれない**。
   - **帰結: [ADR-0009 :53](../adr/0009-m1-room-scale-operation.md) の「残る保護」3 枚目は、現時点で紙の上にしかない**（§2 冒頭の表）。判定を **PASS → CONDITIONAL** へ降格する。条件 = **(i) m1_driver の serial driver node スライスが land し、(ii) その dispatch 経路が `clamp_body_velocity` を必ず通ることを R-26 unit で pin する**（＝§11 G-l）。
   - **結線後も残る限界**は従来どおり: L0' は**ホストプロセスが生きている間だけ有効**（[02 P-7c](../shared/02-hardware-design.md)）。stock FW に通信途絶停止が無いことは source で確定したため、**G-g の MCU command-stream watchdog を実装・host test・実機 USB 抜線試験で閉じる**。それまでは Guardian zero frame（断線時には無効）と S-6 P-1 を縮退手段として組み合わせる（＝§11 G-g / H-9）。
3. **pose_stale は「人検知」ではない。** [GLOSSARY §11 :142](../GLOSSARY.md) の **operational stop（運用停止）** の定義どおり、localization ロストは運用停止であって protective stop ではない。部屋で人が増えても pose_stale の意味は変わらず、**人に対する protective は L1（S-3）と L0'（速度上限）が担う**。変位ゲートの唯一の残留（匍匐前進で発火が遅れる・v→0 で非有界＝[12 末尾追補](../architecture/12-infrastructure-common.md)）も、人保護とは別系統なので部屋転換で悪化しない。

4. **⚠️ Guardian プロセスの単独死は「0.5 秒後に走行が再開する」部分故障モードである。** Guardian の estop は **level（毎 tick 再アサート）** であり、その理由は「**twist_mux prio100 入力が 0.5s で失効するため**」と明記されている（[12:511](../architecture/12-infrastructure-common.md)）。実体は `twist_mux.yaml:42-45` の emergency 入力 `timeout: 0.5` / `priority: 100`。したがって:
   - Guardian が estop を出した**直後にプロセスが死ぬ**と、0.5s 後に prio100 入力が失効し、**twist_mux は次点（prio10 = `cmd_vel/nav2`）を通し始める**。Nav2 が goal を持ったままなら**走行が再開する**。
   - これは fail-safe ではなく **fail-active** の窓である。設計としては「毎 tick 再アサートする生きた Guardian」を前提にしており、その前提が壊れたときの縮退が定義されていない。
   - **部屋転換で新しく生じた問題ではない**（ジオラマでも同じ）。しかし**人が走行面上に立つ構成では帰結の重大さが変わる**ため、本レビューで記録する。**塞ぐなら L1（C-3 改訂後の collision_monitor は Guardian と独立に動く）と L0'（結線後）が受け皿**になる——すなわち②③が埋まるほどこの窓の危険度も下がる、という依存関係にある。
   - 本書は対処を提案しない（`warehouse_safety` / `twist_mux.yaml` 所有トラックの判断）。**記録と、S-8 の PASS をこの点に限定して弱めることに留める。**

5. **`warehouse_m1_driver` / L0' が正準レイヤ対応表に載っていない。** [productization/01 §レイヤ annotation 対応表 :180-187](../productization/01-commercial-box-map.md) の L0 行は `firmware/src/main.cpp` / `safety_clamp.h` / `kinematics.h` のみを挙げ、**`warehouse_m1_driver` の記載が無い**（2026-08-18 grep で 0 件）。M1 単騎構成では L0' がクランプの実体である以上、対応表に 1 行要る。**本 PR では追記しない**（`docs/productization/**` は別トラック所有＝[.claude/rules/layer-annotation.md](../../.claude/rules/layer-annotation.md) の「対応表に無い新規 component は同じ PR で追記」は実装 PR 側の義務）。**申し送りとして記録する。**

### S-8 判定: **PASS → 限定付き（L0' 行のみ CONDITIONAL）**

- **PASS と言えるのは**「twist_mux prio100・Guardian freshness guard + 変位ゲート・pose_stale の意味論は、**部屋転換によって前提を失っていない**」という点に限る。
- **PASS と言えないもの**: **L0' は未結線（CONDITIONAL＝§11 G-l）**・stock MCU の command watchdog は不在（**PHASE-1-GATE**＝§11 G-g）・Guardian は人に寄与ゼロ（記録）・Guardian 単独死の 0.5s 窓（記録）・対応表の欠落（申し送り）。

---

## 11. 部屋運用開始の前提条件チェックリスト（全 gate の集約）

> **本表を全て満たしても、運用開始の可否はオペレーターが決める。** 本書は「これらが未達なら部屋で走らせるべきでない」と言うのみで、「満たせば走らせてよい」とは言わない（安全レビューは十分条件を与えない）。

| # | 前提条件 | 種別 | 依存・順序 | 出所 | 状態 |
|---|---|---|---|---|---|
| **G-a** | **`FOOTPRINT_POLYGON` / `CIRCUMSCRIBED_RADIUS` の additive contract PR が land** | contract PR | **G-b の前提** | [23 F-6 :625](../architecture/23-perception-and-localization.md)（`robot_dimensions.py` に未存在＝本書 5-2 で実査） | 未 land |
| **G-b** | **C-3 collision_monitor 改訂が land**（`radius ≥ CIRCUMSCRIBED_RADIUS + margin`・R-26 unit 付き） | 実装 PR（L1・安全レビュー必須） | G-a → G-b。`margin` は G-f に従属 | [23 G-8 :736-743](../architecture/23-perception-and-localization.md) / [09 R-8-2 :308](09-hand-raise-summon.md) / [ADR-0009 :116-118](../adr/0009-m1-room-scale-operation.md) / 本書 S-3 | 未着手 |
| **G-c** | **OQ-20 召喚レグ解決規則の裁定 → 再設計 land**（(α) 配置のみ / (β) `d_min` 導入。推奨 (β)+(iii)） | OPERATOR-GATE → L3 実装 PR | G-e（waypoint 値）と相互依存 | [09 R-7 :293-302](09-hand-raise-summon.md) / 本書 S-2 | 未裁定 |
| **G-d** | **#223 座標ゴール seam の裁定**（推奨 (A) config gate 化 + (C) 運用規律） | OPERATOR-GATE → L1 実装 PR | yaw 対応の要否と同時に検討 | [09 R-8-3 :310](09-hand-raise-summon.md) / [ADR-0009 :128-130](../adr/0009-m1-room-scale-operation.md) / 本書 S-4 | 未裁定 |
| **G-e** | **部屋 waypoint 9 点の実測・確定と W-a〜W-e の充足** | PHASE-1-GATE（+ W-a は OPERATOR-GATE） | Phase 1 SLAM 地図取得後。**W-b' により G-b の `radius` 確定が先** | [ADR-0009 Decision 5 :27](../adr/0009-m1-room-scale-operation.md) / [09 OQ-17 :276](09-hand-raise-summon.md) / 本書 S-7 | 未実施 |
| **G-f** | **L1 実測（M-1〜M-5）**: スキャン平面高さ・脚の点数・停止距離 `d_stop` → `t_react` | PHASE-1-GATE | G-b の `margin` を供給。M-5（人での確認）は G-b land 後 | [09 OQ-21 :320](09-hand-raise-summon.md) / [23 OQ-15 :526](../architecture/23-perception-and-localization.md) / 本書 S-5 | 未実施 |
| **G-g** | **MCU command-stream watchdog を追加し、host test と実機 USB 抜線で停止を確認** | PHASE-1-GATE | stock V3.6.5 に不在と確定。完了までは S-6 P-1 を必須扱い | [02 P-7c](../shared/02-hardware-design.md) / [12:79](../architecture/12-infrastructure-common.md) / 本書 S-8 ② | 未実装 |
| **G-h** | **運用規律の合意**（D-1〜D-5 の採用 ＋ P-1〜P-4 の採否） | OPERATOR-GATE | G-g の結果で P-1 が必須化しうる | [09 R-3 柱5 :265](09-hand-raise-summon.md) / 本書 S-6 | 未合意 |
| **G-i** | **recovery bypass 窓の裁定**（推奨 (A) 抑止条件の差し替え + (C) 運用規律） | OPERATOR-GATE → L2（Nav2 behavior）実装 | G-b とは独立（C-3 を直しても塞がらない） | [12:559-560](../architecture/12-infrastructure-common.md) / [23 G-10 :755-761](../architecture/23-perception-and-localization.md) / 本書 S-1 柱3 | 未裁定 |
| **G-j** | **sim / 実機の config 二重化（OQ-22）の方式決定** | OPERATOR / 所有トラック調整 | **G-e（`locations` の実機値 / sim 値の分離）の land 可否を握る**。**G-a / G-b の前提ではない** | [23 G-7 :717-734](../architecture/23-perception-and-localization.md) / [ADR-0009 追加Open① :107-114](../adr/0009-m1-room-scale-operation.md)。**⚠️ OQ-22 の対象は `nav2_params.yaml`（footprint）と `config/warehouse.base.yaml`（`locations`）の 2 つで、`collision_monitor.yaml` を含まない**（[23 G-7 :734](../architecture/23-perception-and-localization.md) の OQ-22 定義を実 Read で確認）。C-3 改訂を sim / 実機で分けるべきかは**本書の新規提起**であり OQ-22 の既存スコープ外＝下記「⚠️ G-j に付随する新規提起」 | 未決 |
| **G-k** | **部屋デモの env / launch 構成で `traffic_mode != 'open-rmf'`（＝collision_monitor が起動する）ことを確認** | 構成確認（実行前チェック） | **G-b の効果が出るための必要条件**（起動しなければ改訂した停止円も存在しない） | 本書 S-1 柱3 穴2 / H-11。実体 = `config/{dev,stg,prod}/warehouse.yaml` と [nav2_bringup.launch.py:126](../../ws/src/warehouse_bringup/launch/nav2_bringup.launch.py) | **未確認**（dev のみ条件を満たす） |
| **G-l** | **`warehouse_m1_driver` の serial driver node スライスが land し、dispatch 経路が `clamp_body_velocity` を必ず通ることを R-26 unit で pin** | 実装 PR（L0'・安全レビュー対象） | **「残る保護」3 枚目の実体化**。G-g（MCU watchdog）とは別物（こちらはホスト側の存在そのもの） | 本書 S-8 10-2② / H-9。実体 = `ws/src/warehouse_m1_driver/setup.py:24-26` の `console_scripts: []` | 未 land |
| **G-m** | **ジェスチャ誤検出（false positive）率の実測と hold window しきい値の確定** | PHASE-1-GATE | `gesture_detector` の実装 land が前提（現在 `ws/` に存在しない） | 本書 H-12 / [09 §6 :117](09-hand-raise-summon.md)（しきい値はすべて例示値・実測で確定と明記） | 未実施（実装も未着手） |

**依存グラフ（訂正版）**:

```
G-a (additive contract PR: CIRCUMSCRIBED_RADIUS)
  └→ G-b (C-3 改訂) ──┬→ G-e の W-b' (waypoint clearance ≥ 停止円)
G-f (M-1〜M-3: 平面高さ・点数・d_stop → t_react) ──┘        └→ 運用開始判断
G-b ──→ G-f の M-4/M-5（改訂後の制動余裕検証・人での確認は G-b land 後）
G-k (env/launch 構成の確認) ──→ G-b の効果が出るための必要条件
G-j (OQ-22 config 二重化) ──→ G-e（locations の実機値 / sim 値の分離）
G-l (m1_driver serial node + clamp 結線)  ─┐
G-g (MCU command-stream watchdog 実装＋抜線試験) ─┼→ 運用開始判断
G-c / G-d / G-i / G-h / G-m ───────────────┘
```

- **`G-a → G-b` は真**（`CIRCUMSCRIBED_RADIUS` が無いと C-3 は値を import できない＝S-3 5-2）。
- **`G-j → G-a` は撤回**（初版の誤り）。G-a は `warehouse_description` への **additive な定数追加**であり、既存 `ROBOT_RADIUS` の値も意味も変えない（[23 F-6 :625](../architecture/23-perception-and-localization.md)）。**sim / 実機のどちらの config にも触れないため OQ-22 を待たない。**
- **`G-f ↔ G-b` は相互依存（一方向ではない）**: M-1〜M-3（平面高さ・脚の点数・`d_stop`）は **G-b の前**に測って `margin` を供給し、**M-4（改訂後の制動余裕検証）と M-5（人での確認）は G-b の land 後**に行う（§7-2 の表と一致）。
- **最長経路は `G-a → G-b → G-e(W-b') → 運用開始判断`**（G-f は G-b の入力として並走）。**したがって安全側の critical path の起点は G-a（additive contract PR）であって G-j ではない。**

> **⚠️ G-j に付随する新規提起（本書が初出・OQ-22 のスコープ外）**: C-3 改訂は `collision_monitor.yaml` の単一ソースを書き換えるため、**sim（ジオラマ map・~150mm 車体想定）の 2D 回帰にも同時に効く**。停止円を 0.09 → ~0.26 級へ拡げれば、ジオラマの 280mm 通路（片側クリアランス 24.3mm）では**常時 STOP** になり sim ゲートが赤くなる公算が高い。OQ-22 は `nav2_params.yaml` と `locations` しか対象にしていないので、**`collision_monitor.yaml` の env 分離（あるいは sim を M1 実寸へ倒す）を OQ-22 に含めるかどうかの裁定が別途要る**。これは G-b の land 可否に直結する。所有＝ sim / nav-traffic / bringup の調整事項。

---

## 12. 判定サマリと、本書が閉じていないもの

### 12-1. 8 項目の判定

| 項目 | 判定 | 一行要約 |
|---|---|---|
| S-1 R-3 多層防御論証 | **CONDITIONAL** | 5 柱のうち柱1・柱3 が条件付き・柱2 が値待ち・柱5 が未合意＝**現時点では多層防御としても未完成** |
| S-2 OQ-20 召喚レグ | **OPERATOR-GATE** | **(i) snap 半径単独は安全対策として無効**（危険ケースを残し安全ケースだけを削る。CURRENT より危険にはならないが H-1 を減らさない）。推奨 (iii)＝`d_min` 下限 + 既存 sanity 上限。ただし「standoff を発明しない」の解除裁定が先 |
| S-3 C-3 停止ポリゴン | **PHASE-1-GATE** | 無機能を実ファイルで確認。要求仕様と受け入れ条件を確定。**`CIRCUMSCRIBED_RADIUS` が未存在**＝contract PR が先行 |
| S-4 #223 座標 seam | **OPERATOR-GATE** | 司令経路からは到達不能・loopback 限定＝残余は誤操作。推奨 (A) config gate 化 +(C) 規律。(B) polygon 検証は所有境界を壊す |
| S-5 OQ-21 L1 有効性 | **PHASE-1-GATE** | 「**立位の**人は `/scan` に写る」は平面高さの不確かさに対し**頑健＝支持**（[09 R-2(c) :247](09-hand-raise-summon.md) のしゃがむ案が採られたら再評価）。ただし止まれるかは S-3 従属。M-1〜M-5 を定義 |
| S-6 運用規律 | **OPERATOR-GATE** | D-1〜D-5（docs 根拠あり）＋ P-1〜P-4（本書の提案）。**物理 E-stop は docs に記述が無い** |
| S-7 waypoint 配置規律 | **PHASE-1-GATE** | W-a〜W-e として形式化。**W-b'（C-3 の停止円との両立）は本書の新規指摘**。W-a の実装形は S-2 と同じ裁定の表裏 |
| S-8 E-stop / Guardian | **PASS（限定付き）／ L0' 行のみ CONDITIONAL** | twist_mux prio100・freshness guard + 変位ゲート・pose_stale 意味論は前提を失っていない。**ただし L0' は未結線＝CONDITIONAL（G-l）**・stock MCU の command watchdog は不在（G-g）・**Guardian の人への寄与はゼロ**・**Guardian 単独死で 0.5s 後に走行再開する部分故障モード**（[12:511](../architecture/12-infrastructure-common.md) / `twist_mux.yaml:42-45`）|

### 12-1b. 「残る保護 3 枚」の現況（本レビュー最大の所見の再掲）

| # | 保護（[ADR-0009 :53](../adr/0009-m1-room-scale-operation.md) の列挙） | 現況 | 閉じる gate |
|---|---|---|---|
| ① | 到達集合を 9 waypoint に限定する語彙 gate（**L3/L2**） | **機能している**（ただし選択規則 H-1 と座標 seam H-4 の穴あり） | G-c / G-d |
| ② | L1 collision_monitor（**L1**） | **無機能**（車体内部発火 ＋ stg/prod では未起動） | G-a → G-b ＋ G-k |
| ③ | L0' 0.3 m/s クランプ（**L0'**） | **未結線**（`console_scripts: []`） | G-l |

**現時点で機能している保護は 3 枚中 1 枚（①のみ）。** これは部屋転換が壊したものではなく、**部屋転換によって「壊れていたことが安全上重大になった」**ものである（ジオラマでは人が走行面上にいなかったため ②③ の欠落が顕在化しなかった）。

### 12-2. 未決・本書が自信を持てない箇所（隠さない）

1. **`t_react`（検知〜停止レイテンシ）の実測値が repo に無い。** 下界（6Hz で ~0.21 s / 12Hz で ~0.12 s）は S-3 5-3② で導出したが、**処理・DDS・機械制動の寄与は未知**。S-3 の例示 0.25 s は**本書の仮置き**であり docs 由来ではない。AMCL 100-200ms（[12:510](../architecture/12-infrastructure-common.md)）は別系統のレートで流用不可。→ G-f。
2. **成人の肩高 1.30-1.45m（[09 R-2① :239](09-hand-raise-summon.md) が既に `# TODO(Phase 1 実測)` を付けている）と、本書 S-5 で使った「下腿 0.1–0.5m」「脚幅 0.1m」はいずれも一般値**であり repo 由来ではない。S-5 の結論（検知は頑健）はこの仮定に対して感度が低いと判断したが、**M-1/M-2 で覆る可能性はある**。
3. **`d_min`（到達圏下限）の妥当な水準を本書は示せない。** ロボットの停止距離（G-f）・人の反応・絵としての近さのトレードオフであり、実測と演出判断の両方が要る。
4. **recovery bypass 窓（H-3）の実際の発火頻度が未知。** [12:560](../architecture/12-infrastructure-common.md) の Phase-2 再訪トリガは live PoC 前提で、部屋での頻度は測っていない。推奨 (A) は「発火を減らす」方向であって「窓を閉じる」ものではない——**この窓は運用規律 D-1 に依存し続ける**。
5. **`traffic_mode` overlay は実査で解消したが、`launch` 引数レベルの上書きは依然として運用時確認事項。** 3 env の overlay 実値（dev=`none` / stg=`prod`=`open-rmf`）は確認済み（H-11・S-1 柱3 穴2）。残るのは「部屋デモを起動する具体的な `ros2 launch` 引数で `traffic_mode` がどう解決されるか」で、これは実行時の構成＝**G-k** として gate 化した。
6. **本書は L2 Policy Gate の各チェック（freshness / battery / emergency / rate / duplicate）を個別には再評価していない。** これらは部屋転換で前提が変わらない（時間・電池・重複の話であって空間の話ではない）と判断したためだが、**`duplicate_destination` だけは S-7 W-d 経由で部屋の waypoint 設計と結合する**点だけ記録しておく。
7. **HTML companion への反映は本書のスコープ外**（[ADR-0009 Open :83](../adr/0009-m1-room-scale-operation.md) と同じ扱い）。
8. **H-12（誤検出 dispatch）は受け入れ条件しか書けない。** `gesture_detector` は `ws/` に存在せず（2026-08-18 grep）、[09 §6 :117](09-hand-raise-summon.md) のしきい値群は**すべて例示値**である。したがって「FP 率がどれくらいか」「hold 1.2s で十分か」は**本書では一切判断できない**。書けたのは「実測して確定せよ・実測前に部屋で走らせるな」という gate（G-m）だけである。
9. **C-3 改訂が sim ゲートに与える影響を定量していない。** §11 の「G-j に付随する新規提起」で「ジオラマ 280mm 通路では常時 STOP になる公算」と書いたが、**実際に sim を回して確認したわけではない**（本レビューは docs のみ）。停止円半径・通路幅・`min_points` の組み合わせ次第では回避できる可能性もある。**確認は G-b の実装スライス側**。

### 12-3. 本書が ADR-0009 に対して持つ関係

[ADR-0009 §Open :82](../adr/0009-m1-room-scale-operation.md) の `# TODO(安全レビュー)`「人とロボットが同一平面に立つ構成での安全論証の組み替え（帰結 ⑦）」に対し、**本書は「組み替え後の論証の残余リスク評価」という分析成果物を提供する**。**ADR-0009 は accepted であり本書はこれを編集しない**（[ADR-0009 :105](../adr/0009-m1-room-scale-operation.md) の「既存本文の行は動かさず」規律・[#165 教訓](../dev/03-retrospectives.md)）。当該 Open 項目を**閉じるかどうかはオペレーターの裁定**であり、閉じるとすれば §11 のチェックリストが埋まった時点、閉じ方は ADR-0009 の末尾追補（第三の追補）または後続 ADR となる——**本書はその判断を先取りしない**。

---

## 13. References（双方向）

### 決定・設計の正本（forward）

- **決定正本**: [adr/0009-m1-room-scale-operation.md](../adr/0009-m1-room-scale-operation.md)（帰結 ⑦ :51-53 / トレードオフの訂正 :62 / §Open の `# TODO(安全レビュー)` :82 / 追補② 追加Open①〜⑤ :107-130）
- **ジェスチャ司令の設計正本**: [09-hand-raise-summon.md](09-hand-raise-summon.md)（§3 幾何 :39-45 / §5 幾何解決 :111-113 / §8 安全 :137-143 / 追補 R-1〜R-6 :211-285 / 追補② R-7〜R-10 :289-326）
- **知覚・Nav2 側**: [architecture/23-perception-and-localization.md](../architecture/23-perception-and-localization.md)（§1 P1/P2 :37-43 / scan 面 :495 / OQ-15 :526 / F-2 幾何 :583-590 / F-6 :622-625 / G 系列 :659-775、とくに **G-7 :717-734**・**G-8 :736-743**・**G-10 :755-761**）
- **安全レイヤーとインフラ**: [architecture/12-infrastructure-common.md](../architecture/12-infrastructure-common.md)（安全レイヤー 4 層 :72-93 / L0 deadman :79 / freshness guard :506-513 / collision_monitor トポロジ :522-552 / Open ⑤⑥① の確定 :554-561 / 変位ゲート追補 :601-）
- **ハードウェア**: [shared/02-hardware-design.md](../shared/02-hardware-design.md)（M1 実寸・LiDAR 上面 / L0' の限界と stock FW watchdog 不在 = P-7c / C 系列 / 部屋スケールとの関係）
- **レイアウト側（ジオラマ・歴史記録）**: [shared/04-diorama-layout.md](../shared/04-diorama-layout.md)（F-L 系列・W3 注記）
- **用語**: [docs/GLOSSARY.md §11](../GLOSSARY.md)（**部屋スケール運用** :146 / **operational stop** :142 / **非円形 footprint** :145）
- **規約**: [.claude/rules/safety.md](../../.claude/rules/safety.md)（R-26 独立オラクル・0.3 m/s）/ [.claude/rules/layer-annotation.md](../../.claude/rules/layer-annotation.md)（layer 注記）/ [.claude/rules/docs-first.md](../../.claude/rules/docs-first.md)（発明しない・file:line 引用）/ [productization/01 §レイヤ annotation 対応表 :174-195](../productization/01-commercial-box-map.md)

### CURRENT 実体（impl-target は行 pin せずキーで指す＝[session-orchestration.md §8](../../.claude/rules/session-orchestration.md)）

- `ws/src/warehouse_bringup/config/collision_monitor.yaml` の `PolygonStop.radius` / `min_points` / `source_timeout`（L1・S-3）
- `ws/src/warehouse_bringup/launch/nav2_bringup.launch.py` の `collision_active` gating と recovery remap（L1/L2・S-1 柱3）
- `ws/src/warehouse_description/warehouse_description/robot_dimensions.py` の `ROBOT_RADIUS`（＋未存在の `CIRCUMSCRIBED_RADIUS` / `FOOTPRINT_POLYGON`・S-3 5-2）
- `ws/src/warehouse_nav2_bridge/` の `navigate` 座標 `goal` seam と `_pose`（L1 入口・S-4 / H-10）
- `ws/src/warehouse_llm_bridge/.../robotics_planning_core/handoff.py` の `_FORBIDDEN_KEY_RULES`（L3・S-1 柱1）
- `ws/src/warehouse_interfaces/warehouse_interfaces/schemas.py` の KNOWN_LOCATIONS validator / `safety.py` の `MAX_LINEAR_VELOCITY`（L2 Contract / L0'）
- `ws/src/warehouse_m1_driver/warehouse_m1_driver/clamp.py`（L0' ベクトルクランプ・S-8）
- `config/warehouse.base.yaml` の `safety.*` / `locations`（S-7 / S-8）
- `tests/unit/test_known_locations_navigable.py`（S-7 W-b の拡張先）

### backlink（本書を指す側）

- [09-hand-raise-summon.md](09-hand-raise-summon.md) 末尾【2026-08-18 追補③】
- [architecture/23-perception-and-localization.md](../architecture/23-perception-and-localization.md) 末尾 G-14
- [docs/README.md](../README.md) mode-x-er 表 / [mode-x-er/README.md](README.md) 末尾索引

---

## 【2026-08-26 追記】§11 ゲートの設計・実装ホーム（mode-m1/ 新設に伴う backlink）

- **G-k** の構造的解決（Mode M1 の必須条件 = collision_monitor が起動する構成）: [mode-m1/01-mode-boundary-and-traffic.md](../mode-m1/01-mode-boundary-and-traffic.md)
- **G-l / G-g** の設計ホーム（L0' driver node・W-1〜W-4 多層停止・G-g 実機 5 分手順・MCU watchdog 不在のソース調査）: [mode-m1/02-m1-driver-and-watchdog.md](../mode-m1/02-m1-driver-and-watchdog.md)
- bring-up の成功ゲート（M0/M1/M2・実機プローブ）: [mode-m1/03-joystick-teleop-bringup.md](../mode-m1/03-joystick-teleop-bringup.md)
- §11 表の**状態列の更新はゲートを閉じた PR が行う**（本追記はリンクのみ・判定不変）。
