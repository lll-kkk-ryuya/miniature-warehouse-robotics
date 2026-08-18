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

### 0-2. layer 注記（[.claude/rules/layer-annotation.md](../../.claude/rules/layer-annotation.md) 準拠）

本書は各ハザード・各緩和に **L0 / L0' / L1 / L2 / L3 / L4** を括弧併記する。番号体系は商用 box map（[productization/01 §レイヤ annotation 対応表 :180-187](../productization/01-commercial-box-map.md)）を正とし、**doc12 の安全レイヤー 4 層（Layer 0–3）とは軸が違う**ため裸で混ぜない（同 :195）。本書で頻出する帰属:

| 記号 | 本書での指示対象（実体） |
|---|---|
| **L4** | `gesture_detector`（publish-only・0 actuation）・`x_er_bridge`（commander node）＝[09 §4-1 :59-91](09-hand-raise-summon.md) |
| **L3** | `robotics_planning_core/`（Validator / Visual Resolver / Task Graph Executor / Command Compiler）・**召喚/指差しの候補生成規則はここ**（実行許可なし） |
| **L2** | Governance = `warehouse_mcp_server`（`policy_gate.py`）／ Contract = `warehouse_interfaces`（`schemas.py` の KNOWN_LOCATIONS validator）。Traffic（`warehouse_traffic`）は**単騎 X-lite では実質非アクティブ** |
| **L1** | Navigation = `warehouse_nav2_bridge`（REST→Nav2）・`warehouse_bringup/config/`（`nav2_params.yaml` / `collision_monitor.yaml` / `twist_mux.yaml`）／ Safety = `warehouse_safety`（Emergency Guardian） |
| **L0'** | ホスト側シリアルドライバ送信直前の 0.3 m/s ベクトルクランプ（`warehouse_m1_driver` の `clamp.py`）＝M1 の STM32 がベンダ製バイナリゆえ MCU 内 L0 を置けないことの帰結（[02:325-329](../shared/02-hardware-design.md) 残課題 7） |
| **L0** | MCU 内の物理安全（M1 では**自前実装が無い**＝上記の理由。ESP32 自前ファームは 2 台構成の資産） |

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

---

## 2. ハザード分析表（総覧）

「部屋転換で**新しく生じた／性質が変わった**ハザード」を列挙する。ジオラマでも部屋でも同じハザード（壁への衝突・棚への接触など）は、部屋転換の評価軸ではないため本表に載せない。

| # | ハザード | 関与 layer | 現行の緩和（CURRENT） | 残余リスク | 判定 |
|---|---|---|---|---|---|
| **H-1** | 召喚レグが**操作者の足元に最も近い waypoint** を選び、ロボットが人へ向かって走る | **L3**（候補生成規則）／到達集合は **L2** 契約が限定 | 到達集合は 9 キーに限定（`schemas.py` validator + L3 Validator の二重 reject）。**snap 半径は召喚レグに無い**（[09:111](09-hand-raise-summon.md)） | 「9 キーのうち最も人に近い 1 点」が選ばれることは止まらない（[09 R-7 :298](09-hand-raise-summon.md)）。ジオラマの盤縁という構造的な壁が消えた | **OPERATOR-GATE**（→ S-2） |
| **H-2** | L1 反射停止が**名目上だけ存在**する（停止ポリゴンが車体内部） | **L1**（`collision_monitor.yaml`） | `PolygonStop` circle `radius: 0.09`（[collision_monitor.yaml:63-68](../../ws/src/warehouse_bringup/config/collision_monitor.yaml)） | M1 内接 115.7mm（[23:585](../architecture/23-perception-and-localization.md)）を下回る＝**接触してからでないと発火しない**。人が走行面上に立つ部屋では多層防御の 1 枚が欠落 | **PHASE-1-GATE**（仕様は S-3・反応余裕は実測） |
| **H-3** | recovery（Spin / BackUp / DriveOnHeading）が **L1 を bypass** して人の近くで動く | **L1**（`nav2_bringup.launch.py` の recovery remap）／抑止設計は **L2**（Nav2 behavior） | ⑥ = **BYPASS で確定済**（[12:559](../architecture/12-infrastructure-common.md)）。残留リスクも記録済（[12:560](../architecture/12-infrastructure-common.md)） | 部屋では recovery の発火場面が「人が近い」場面と相関しやすい（[23 G-10 :755-761](../architecture/23-perception-and-localization.md)）。C-3 を直しても**この窓は塞がらない** | **OPERATOR-GATE**（→ S-1 柱3） |
| **H-4** | 名前ゲートを通らない**座標ゴール seam** から走行面上の任意点（人のいる場所を含む）へ goal が入る | **L1 入口**（`warehouse_nav2_bridge`）／迂回されるのは **L2** 語彙 gate | 通常司令経路は `handoff.py` の `coordinate_goal_unfrozen` で fail-closed（[handoff.py:90](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/handoff.py)）。REST は loopback 127.0.0.1:8645 に限定 | seam 自体は生存し**座標の範囲チェックを持たない**（`warehouse_nav2_bridge/CLAUDE.md` の「#223 残 ③」）。ジオラマの「人は盤外」という暗黙の覆いが消えた | **OPERATOR-GATE**（→ S-4） |
| **H-5** | 立位の人を `/scan` が捉えても**止まれない**（検知 ≠ 停止） | **L1** | 2D LiDAR のスキャン面は人の下腿を横切る（[09 R-9 :316](09-hand-raise-summon.md)）＝検知側は有利 | スキャン平面の**実高さが未確定**（上面 147.5mm は上面であって平面ではない＝[23:495](../architecture/23-perception-and-localization.md) / OQ-15 [23:526](../architecture/23-perception-and-localization.md)）。停止距離・制動余裕も未実測 | **PHASE-1-GATE**（→ S-5） |
| **H-6** | 低く差し出された手・低い物体が検知されない | **L1**（水平 2D スキャン面の原理的限界） | 限界として明示済（[09:142](09-hand-raise-summon.md) / [09 R-3 柱4 :264](09-hand-raise-summon.md)）。3D 検知は nvblox の TARGET 機能で S1/S2 未通過 | 部屋でも同じ。**「3D で人を検知して止まる」とは主張しない**方針は不変。骨格 NN を反射経路に入れない（[23 §1 P1 :37-39](../architecture/23-perception-and-localization.md)）も不変 | **PASS**（限界の記述として部屋でも正確・運用規律で補う＝S-6） |
| **H-7** | 操作者の常在位置がそのまま waypoint になり、召喚が「人のいる場所へ行く」動作になる | **L3**（候補集合）／値の所在は **L2 Contract**（`config/warehouse.base.yaml` の `locations`） | 規律として宣言済（[09 R-3 柱2 :262](09-hand-raise-summon.md)）。検証可能な形にはなっていない | 部屋 waypoint 9 点の値は**未確定**（Phase 1 SLAM 後）。規律が受け入れ条件として機械化されていない | **PHASE-1-GATE**（形式化案は S-7） |
| **H-8** | 第三者・ペット・撮影者など**運用規律の外にいる人**が走行面上に入る | 全層に対して**層外**（＝運用でしか塞げない） | 非統制変数として ADR が明記（[ADR-0009 :59](../adr/0009-m1-room-scale-operation.md)「人の往来」） | 規律群が未合意。docs に根拠のある規律と本書の提案が混在 | **OPERATOR-GATE**（→ S-6） |
| **H-9** | ホストプロセス死・USB 断で MCU に最後の指令が残り**暴走**する | **L0'**（ホストクランプ）の限界／本来は **L0** deadman の責務 | L0' は全 `cmd_vel` の単一絞り点（[02:325-328](../shared/02-hardware-design.md)）。ただし**ホストが生きている間だけ有効**（[02:329](../shared/02-hardware-design.md)） | M1 の MCU 側 watchdog の有無が**未確認**（同 :329 の `# TODO(Phase 1)`）。L0 heartbeat deadman は「実 enforcement は Phase 1」（[12:79](../architecture/12-infrastructure-common.md)） | **PHASE-1-GATE**（→ S-8） |
| **H-10** | 到達点で**人の方を向かずに**停止する（yaw が落ちる）＝絵の問題だが、人との相対姿勢が設計不能 | **L1 入口**（`nav2_bridge` の `_pose` が `orientation.w=1.0` 固定） | ジオラマでは「goal は回転不要な向きで置く」で回避（[04 F-L3](../shared/04-diorama-layout.md)） | 部屋では回避手段が消える（[09 R-4 :269-271](09-hand-raise-summon.md)）。**安全ハザードとしては軽微**（停止位置は変わらない）だが、S-7 の配置規律と S-2 の到達圏設計に影響する | **PASS**（安全上の残余リスクは軽微・設計課題として S-7 へ送る） |

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
- collision_monitor は **`traffic_mode != open-rmf` で起動**する（[nav2_bringup.launch.py:198-211](../../ws/src/warehouse_bringup/launch/nav2_bringup.launch.py) の `condition=collision_active`）。X-lite / 単騎の既定 `traffic_mode: none`（[config/warehouse.base.yaml:6](../../config/warehouse.base.yaml)）では **gating off にならない＝起動する**。「X-lite では traffic 層が非アクティブ」という一般則（[.claude/rules/layer-annotation.md](../../.claude/rules/layer-annotation.md)）と混同して「L1 も止まっている」と誤読しないこと。
- observation source のうち `virtual_scan` は単騎では相手機が無く常時 silent だが、`source_timeout: 0.0`（[collision_monitor.yaml:81-90](../../ws/src/warehouse_bringup/config/collision_monitor.yaml)）で stale-source STOP を無効化済み＝**単騎でも誤 STOP しない**。実 `/scan` は node-level `source_timeout: 1.0`（:58）で LiDAR 途絶→STOP が生きる。
- 出力は `cmd_vel/nav2` のみで `cmd_vel/emergency` を書かない＝**twist_mux prio100（FROZEN）を迂回しない**（[collision_monitor.yaml:14-15](../../ws/src/warehouse_bringup/config/collision_monitor.yaml) / [12:545](../architecture/12-infrastructure-common.md)）。部屋転換で壊れる前提は無い。

**穴（2 件）**:
1. **停止ポリゴンが車体内部**（H-2）。柱3 は「**C-3 改訂が land していれば**不変」と読むしかない（[09 R-8-2 :308](09-hand-raise-summon.md)）。→ S-3。
2. **recovery は L1 を bypass する**（H-3）。[12:559](../architecture/12-infrastructure-common.md) が「⑥ = BYPASS」を確定済みで、その根拠は「monitor 経由にすると stop polygon 自身が recovery を 0 化して自己ラッチ deadlock する」——**この根拠はジオラマの 200mm 隘路（R-42）由来**である。部屋では隘路 deadlock の圧力が下がる一方、「人の脚の横で旋回する」という新しい抑止理由が立つ（[23 G-10 :757-759](../architecture/23-perception-and-localization.md)）。**C-3 を直しても bypass 窓は塞がらない**点が重要で、これは S-3 とは独立の裁定を要する。
   - 選択肢: **(A)** F-5-4 の抑止条件を「通路内で回転不可」から「人の近傍では recovery を発火させない」へ差し替える（[23 G-10 :761](../architecture/23-perception-and-localization.md) が Slice 1 で再設計せよと言っている線）／ **(B)** recovery 中のみ slowdown polygon 化・monitor 有効化（[12:560](../architecture/12-infrastructure-common.md) の Phase-2 再訪トリガ側の案）／ **(C)** 現状維持 ＋ 運用規律（走行中は進路に立ち入らない＝S-6 D-1）で覆う。
   - **推奨: (A) ＋ (C)**。(B) は bypass を作った当の deadlock 理由と正面衝突するため、部屋で deadlock 圧力が実際に下がることを実測してからでないと採れない。
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
| **(i) snap 半径を導入** | 正射影点から半径 `r` 以内の location のみ候補。超えたら `clarify`（0 dispatch） | **⚠️ 安全方向に効かない**（下記 4-2） | demo が「近くに waypoint がある時だけ動く」になる |
| **(ii) 到達圏制約（候補フィルタ）** | 操作者の推定位置から**距離 `d_min` 以上**離れた location のみを候補にし、その中で最寄り | 「人へ向かう」を直接封じる。standoff を**配置ではなく候補フィルタ**で担保 | **`d_min` は standoff 相当パラメータ**＝[09:19](09-hand-raise-summon.md) / [09:141](09-hand-raise-summon.md) の「発明しない」と正面衝突（4-3） |
| **(iii) 両方** | `d_min ≤ d ≤ d_max` の帯に入る location のみ候補。帯が空なら `clarify` | 上下限が揃い fail-closed が明確 | パラメータ 2 個。ただし `d_max` は既存の sanity bound 2.0m を上限として再解釈できる |

### 4-2. **重要な分析結論: (i) 単独は召喚レグの安全問題を解かない（むしろ逆向き）**

指差しレグの `snap_radius_m 0.25`（[09:111](09-hand-raise-summon.md)）は「**意図した交点の近く**に location があるときだけ動く」という意味で正しく働く——交点は人が指した先であり、人自身ではないからである。

しかし召喚レグの入力は**人の足元**である。ここに同じ半径フィルタを掛けると、「**人の足元の近く**に location があるときだけ動く」になる。すなわち **(i) は「人に近い waypoint」を排除するどころか、それを選んだときだけ dispatch を許す**。半径を狭めるほど、選ばれる goal は人に近くなる。

> **したがって (i) を「安全のための snap 半径」として採用してはならない。** (i) に意味があるのは「センサ誤差で遠方の location が選ばれる暴発を防ぐ sanity 上限」としてであり、それは既に `sanity bound 2.0m` が果たしている役割である。

この点は [09 R-7 :301](09-hand-raise-summon.md) が「(i) snap 半径の導入 / (ii) 到達圏の制約 / (iii) 両方」と 3 案を並列に置いていることへの**実質的な絞り込み**であり、本レビューの主要な追加分析である。

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

`0.09 < 0.1157` — **停止円が車体の内側に完全に収まる**。障害物が車体に接触するまで polygon 内に入らないため、L1 反射は物理的に発火し得ない。[23 G-8 :738-740](../architecture/23-perception-and-localization.md) の記述を実ファイルで確認した。

### 5-2. **本レビューで発見した前提不足: `CIRCUMSCRIBED_RADIUS` はまだ存在しない**

[23 G-8 :742](../architecture/23-perception-and-localization.md) は改訂先を「外接 `CIRCUMSCRIBED_RADIUS` ≈0.184 + 反応余裕」と書くが、**`warehouse_description/robot_dimensions.py` に定義されているのは `ROBOT_RADIUS = 0.075` のみで、`CIRCUMSCRIBED_RADIUS` も `FOOTPRINT_POLYGON` も存在しない**（2026-08-18 実査）。両定数の追加は [23 F-6 :625](../architecture/23-perception-and-localization.md) が **additive な contract PR（`contract` ラベル ＋ 依存トラック予告）** として計画している未 land 項目である。

> **帰結: C-3 改訂は単独では land できない。** 順序は **① F-6① の additive contract PR（`FOOTPRINT_POLYGON` / `CIRCUMSCRIBED_RADIUS`）→ ② C-3 改訂 PR（`CIRCUMSCRIBED_RADIUS` を消費）** となる。値をハードコードして ① を飛ばすことは、[02:325-328](../shared/02-hardware-design.md) / `safety.py` の「単一ソースから import・値の再定義禁止」（[safety.py:11-18](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py)）に反する。**このチェーンは §11 のチェックリストに載せる。**

### 5-3. 要求仕様（形・不変条件）

1. **包含条件（必須・不変条件）**: 停止領域は**実車体を必ず包含**すること。円形を維持するなら `radius ≥ CIRCUMSCRIBED_RADIUS`（≈0.184）。多角形（前方バイアス）を採る場合も、**車体 footprint を完全に含む**こと。丸めは常に外側（切り上げ）＝[23 F-5-1 :612](../architecture/23-perception-and-localization.md) と同じ規律。
2. **反応余裕（margin）の導出式**:

   ```
   margin = v_max × t_react          # v_max = 0.3 m/s（MAX_LINEAR_VELOCITY）
   radius ≥ CIRCUMSCRIBED_RADIUS + margin
   ```

   - `v_max = 0.3 m/s` の出所: [safety.py:18](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py)（`MAX_LINEAR_VELOCITY`・単一ソース）/ [.claude/rules/safety.md](../../.claude/rules/safety.md) / [config/warehouse.base.yaml:15](../../config/warehouse.base.yaml)。
   - `t_react`（`/scan` 上に障害物が現れてからモータが止まるまでの実効レイテンシ。scan 周期 + collision_monitor 処理 + `cmd_vel/nav2` 反映 + L0' 送出 + 機械制動の総和）**の値は docs のどこにも存在しない**。→ **PHASE-1-GATE として登録**（測定手順は S-5 5-4 と同一セッション）。
   - **`例示値`（そのまま config へ写さないこと）**: 仮に `t_react = 0.2 s` なら `margin = 0.06 m`、`radius ≈ 0.244 m`。**この 0.2 s は本書が置いた仮定であり、docs 由来の値ではない。** 唯一の関連実測は AMCL 正常間隔 100-200ms（[12:510](../architecture/12-infrastructure-common.md) の R-39 由来）だが、これは**別系統（pose 到着）のレートであって制動レイテンシではない**——流用してはならない。
3. **`min_points` の妥当性（本書で導出・入力は既存 doc）**: CURRENT は `min_points: 4`（[collision_monitor.yaml:70](../../ws/src/warehouse_bringup/config/collision_monitor.yaml)）。T-mini Plus の角度分解能は 0.54°/667 点（6Hz）または 1.08°/333 点（12Hz）（[23 B-6 :494](../architecture/23-perception-and-localization.md)）。人の脚（幅を 0.1m と仮定＝**一般値・repo 由来ではない**）は距離 0.25m で角度幅 ≈22.6°、距離 1.0m で ≈5.7° を占める。0.54° 刻みなら順に ≈42 点 / ≈10 点、1.08° 刻みでも ≈21 点 / ≈5 点。**いずれも `min_points: 4` を満たす**＝改訂後の停止円内に立つ脚は点数不足で見落とされない。**`例示値`（脚幅 0.1m の仮定に依存）**。→ 5-4 の実測で確認する。
4. **副作用の点検（必須）**: 半径を 0.09 → ~0.24 級へ拡げると、**壁・家具に近い waypoint で常時 STOP** し得る。ジオラマの拘束（R-42 200mm 隘路・#156 の 0.15m head-on demo＝[collision_monitor.yaml:31-37](../../ws/src/warehouse_bringup/config/collision_monitor.yaml)）は部屋では消えるが（[23 G-2 :675](../architecture/23-perception-and-localization.md)）、**部屋固有の拘束（壁沿い waypoint・ドア開口通過）に置き換わる**。→ S-7 の配置規律 W-b と**同一 PR で整合を取る**こと。
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
- **人の位置を 3D で解釈して止める設計は採らない**（[09:142](09-hand-raise-summon.md) / [23 §1 P1 :37-39](../architecture/23-perception-and-localization.md)）。本節が主張するのは **2D LiDAR が立位の人体を捉えるという幾何学的事実のみ**である。

### 7-2. 測定手順の定義（PHASE-1-GATE・S-3 の `t_react` と同一セッション）

> **前提**: 実機 M1 ＋ 部屋。**人を使う試験は最後**に置き、それ以前は器物で行う（安全側）。速度は常に ≤0.3 m/s（[safety.py:18](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py)）。

| # | 測定 | 手順 | 出力（どの gate へ供給するか） |
|---|---|---|---|
| **M-1** | **スキャン平面の実高さ**（OQ-15 と同時） | 既知高さの薄板ターゲットを床から段階的に上げ／下げ、`/scan` に現れる/消える境界高さを記録（複数距離で） | OQ-15 の確定・7-1 の前提検証 |
| **M-2** | **脚相当ターゲットの点数** | 人の脛に相当する直径の円柱を距離 0.25 / 0.5 / 1.0 / 2.0m に置き、polygon 内点数を記録 | `min_points`（現 4）の妥当性＝S-3 5-3③ の実測置換 |
| **M-3** | **停止距離 `d_stop`** | 静止した器物ターゲットへ 0.3 m/s で直進 → STOP 発火位置から静止までの距離を n 試行（n は実施者判断）記録。中央値と最悪値 | `t_react = d_stop / v` を逆算 → **S-3 の `margin`** |
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
| **P-2** | **第三者・ペットの入室管理**（デモ中は走行領域への出入りを制限し、実施者以外の在室者に D-1/D-2 を事前共有する） | ADR が「人の往来」を非統制変数として挙げる（[ADR-0009 :59](../adr/0009-m1-room-scale-operation.md)）に留まり、**規律としては未定義**。H-8 は技術的に塞げない | ペットは D-1 を理解しない＝**入室させない**以外の緩和が無い |
| **P-3** | **撮影者の立ち位置を waypoint 集合と分離する**（カメラ位置・三脚も障害物・常在位置として扱う） | 撮影構図は ADR-0009 の Open（[:77](../adr/0009-m1-room-scale-operation.md)）。撮影者は「操作者ではない常在者」であり D-3/D-4 の射程外 | S-7 W-a の「操作者常在位置集合 O」に**撮影ポジションを含めるか**が設計判断 |
| **P-4** | **走行前に地図と実環境の一致を確認する**（家具が動いていれば SLAM 地図を取り直す） | D-5 の実行手順化。加えて [23 G-9 :745-753](../architecture/23-perception-and-localization.md)（人が static TSDF に焼き付く）が示すとおり、**人が居る状態で地図を作らない**ことが要る | nvblox 採否（OQ-23）とは独立に、2D SLAM 地図取得にも同じ注意が要る |

### S-6 判定: **OPERATOR-GATE**

（D-1〜D-5 は既存 docs の集約＝そのまま採用可。**P-1〜P-4 の採否がオペレーター裁定**。特に P-1 は H-9 と対であり、Phase 1 の MCU watchdog 実測結果（S-8）次第で必須化しうる。）

---

## 9. S-7 — waypoint 配置規律の形式化（Phase 1 実測時の受け入れ条件）

**目的**: [09 R-3 柱2 :262](09-hand-raise-summon.md) の散文規律を、**Phase 1 で `config/warehouse.base.yaml` の `locations` 9 点を実測値へ差し替える PR の受け入れ条件**として書ける形にする。既存の機械ゲート `tests/unit/test_known_locations_navigable.py`（[23 G-7 :728](../architecture/23-perception-and-localization.md) が参照）の拡張として設計する。

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

### S-7 判定: **PHASE-1-GATE**

（W-b〜W-e は Phase 1 実測後に機械化可能。**W-a の実装形は S-2 と束ねた OPERATOR-GATE**。W-b' は本書が新たに指摘した S-3 との結合であり、C-3 改訂 PR と waypoint 確定 PR の順序に効く。）

---

## 10. S-8 — E-stop / Guardian 経路の部屋での妥当性確認（点検結果）

**想定は「影響なし」**であったが、**点検の結果 3 件の限定が見つかった**ので記録する。

### 10-1. 点検した経路と結果

| 経路 | layer | 部屋転換で前提を失うか | 所見 |
|---|---|---|---|
| **twist_mux emergency prio100（FROZEN）** | **L1** | **失わない** | 環境非依存の優先度契約。collision_monitor は `cmd_vel/nav2` のみに書き prio100 を迂回しない（[collision_monitor.yaml:14-15](../../ws/src/warehouse_bringup/config/collision_monitor.yaml) / [12:545](../architecture/12-infrastructure-common.md)）。**PASS** |
| **Emergency Guardian の pose freshness guard ＋ 変位ゲート** | **L1** | **失わない** | ゲートの根拠は AMCL が motion-gated であること（`update_min_d 0.05` / `update_min_a 0.2`）＝**車体とアルゴリズムの性質**であり環境に依らない（[12 末尾追補](../architecture/12-infrastructure-common.md) / [config/warehouse.base.yaml:26-29](../../config/warehouse.base.yaml)）。**PASS** |
| **Guardian の `near_collision`（2 台間近接監視）** | **L1** | **前提を失ってはいないが、寄与がゼロ** | しきい値は `emergency_min_distance: 0.3 # m（2台間）`（[config/warehouse.base.yaml:16](../../config/warehouse.base.yaml)）で、入力は `/{bot}/amcl_pose`。**単騎では相手機が無く沈黙**し、**人は AMCL pose を持たないので Guardian からは見えない**。→ **「Guardian は人に対して構造的に無力」**（下記 10-2①） |
| **L0' 0.3 m/s ベクトルクランプ** | **L0'** | **失わない（が限界が重くなる）** | 全 `cmd_vel` の単一絞り点（[02:325-328](../shared/02-hardware-design.md) / `warehouse_m1_driver` の `clamp.py`）。値は単一ソース `MAX_LINEAR_VELOCITY = 0.3`（[safety.py:18](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py)）。**PASS**（ただし 10-2②） |
| **L0 heartbeat / watchdog deadman** | **L0** | **前提を確認できない** | [12:79](../architecture/12-infrastructure-common.md) が Layer 0 の責務としつつ「**実 enforcement は Phase 1**」。M1 は STM32 がベンダ製バイナリで**自前 L0 を置けない**（[02:325-329](../shared/02-hardware-design.md) 残課題 7）。MCU 側 watchdog の有無は `# TODO(Phase 1)` で**未確認**（[02:329](../shared/02-hardware-design.md)）。→ **PHASE-1-GATE**（10-2②） |
| **物理 E-stop ボタン** | — | **そもそも docs に記述が無い** | 2026-08-18 に `docs/shared/02` / `docs/architecture/12` / `.claude/rules/safety.md` を grep した範囲で、物理 E-stop ボタンの設計記述は見当たらない。→ S-6 P-1 の提案根拠 |

### 10-2. 記録すべき 3 件の限定

1. **Guardian は人に対して寄与ゼロ。** これは欠陥ではなく設計どおり（Guardian の近接監視は AMCL pose を持つ**ロボット同士**の距離監視である）。しかし [ADR-0009 帰結⑦ :53](../adr/0009-m1-room-scale-operation.md) の「残る保護は語彙 gate ＋ L1 collision_monitor ＋ L0' クランプ」という列挙は正確であり、**Guardian をこの列挙に足してはならない**（人に対する保護としては数えられない）。本書はこの点を明示的に記録する。
2. **最終防衛線が MCU（L0）からホスト（L0'）へ移っている構成である。** ESP32 自前ファームの世界では「上位が全滅しても MCU が止める」（[12:112](../architecture/12-infrastructure-common.md)）が成立したが、M1 では **L0' はホストプロセスが生きている間だけ有効**（[02:329](../shared/02-hardware-design.md)）。**人が走行面上に立つ構成では、この差の重みが増す**（H-9）。→ **Phase 1 で MCU watchdog の有無を実測**し、無ければ [02:329](../shared/02-hardware-design.md) が挙げる代替（Guardian からの明示 stop フレーム送出 ＋ 電源系での縮退）＋ S-6 P-1 を組み合わせる。
3. **pose_stale は「人検知」ではない。** [GLOSSARY §11](../GLOSSARY.md) の **operational stop（運用停止）** の定義どおり、localization ロストは運用停止であって protective stop ではない。部屋で人が増えても pose_stale の意味は変わらず、**人に対する protective は L1（S-3）と L0'（速度上限）が担う**。変位ゲートの唯一の残留（匍匐前進で発火が遅れる・v→0 で非有界＝[12 末尾追補](../architecture/12-infrastructure-common.md)）も、人保護とは別系統なので部屋転換で悪化しない。

### S-8 判定: **PASS**（3 件の限定付き。うち②は **PHASE-1-GATE** として §11 に登録）

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
| **G-g** | **MCU 側 watchdog の有無の実機確認**（無ければ代替手段を決める） | PHASE-1-GATE | S-6 P-1 の要否を決定づける | [02:329](../shared/02-hardware-design.md) / [12:79](../architecture/12-infrastructure-common.md) / 本書 S-8 ② | 未実施 |
| **G-h** | **運用規律の合意**（D-1〜D-5 の採用 ＋ P-1〜P-4 の採否） | OPERATOR-GATE | G-g の結果で P-1 が必須化しうる | [09 R-3 柱5 :265](09-hand-raise-summon.md) / 本書 S-6 | 未合意 |
| **G-i** | **recovery bypass 窓の裁定**（推奨 (A) 抑止条件の差し替え + (C) 運用規律） | OPERATOR-GATE → L2（Nav2 behavior）実装 | G-b とは独立（C-3 を直しても塞がらない） | [12:559-560](../architecture/12-infrastructure-common.md) / [23 G-10 :755-761](../architecture/23-perception-and-localization.md) / 本書 S-1 柱3 | 未裁定 |
| **G-j** | **sim / 実機の config 二重化（OQ-22）の方式決定** | OPERATOR / 所有トラック調整 | **G-b・G-e の land 可否を握る**（`collision_monitor.yaml` / `locations` の env 分離） | [23 G-7 :717-734](../architecture/23-perception-and-localization.md) / [ADR-0009 追加Open① :107-114](../adr/0009-m1-room-scale-operation.md) | 未決 |

**依存グラフ（要約）**: `G-j → G-a → G-f → G-b → (G-e W-b') → 運用開始判断`、これに並行して `G-c / G-d / G-g → G-h / G-i`。**最長経路は G-j → G-a → G-f → G-b** であり、**config 二重化（OQ-22）が安全側の critical path でもある**——これは [23 G-11 :765](../architecture/23-perception-and-localization.md) が Slice 1 の最初のステップとして指定したものと同じ項目であり、本書の分析はそれを**安全上の前提条件としても**裏づける。

---

## 12. 判定サマリと、本書が閉じていないもの

### 12-1. 8 項目の判定

| 項目 | 判定 | 一行要約 |
|---|---|---|
| S-1 R-3 多層防御論証 | **CONDITIONAL** | 5 柱のうち柱1・柱3 が条件付き・柱2 が値待ち・柱5 が未合意＝**現時点では多層防御としても未完成** |
| S-2 OQ-20 召喚レグ | **OPERATOR-GATE** | **(i) snap 半径単独は逆効果**。推奨 (iii)＝`d_min` 下限 + 既存 sanity 上限。ただし「standoff を発明しない」の解除裁定が先 |
| S-3 C-3 停止ポリゴン | **PHASE-1-GATE** | 無機能を実ファイルで確認。要求仕様と受け入れ条件を確定。**`CIRCUMSCRIBED_RADIUS` が未存在**＝contract PR が先行 |
| S-4 #223 座標 seam | **OPERATOR-GATE** | 司令経路からは到達不能・loopback 限定＝残余は誤操作。推奨 (A) config gate 化 +(C) 規律。(B) polygon 検証は所有境界を壊す |
| S-5 OQ-21 L1 有効性 | **PHASE-1-GATE** | 「立位の人は `/scan` に写る」は平面高さの不確かさに対し**頑健＝支持**。ただし止まれるかは S-3 従属。M-1〜M-5 を定義 |
| S-6 運用規律 | **OPERATOR-GATE** | D-1〜D-5（docs 根拠あり）＋ P-1〜P-4（本書の提案）。**物理 E-stop は docs に記述が無い** |
| S-7 waypoint 配置規律 | **PHASE-1-GATE** | W-a〜W-e として形式化。**W-b'（C-3 の停止円との両立）は本書の新規指摘**。W-a の実装形は S-2 と同じ裁定の表裏 |
| S-8 E-stop / Guardian | **PASS**（限定 3 件） | 経路は前提を失っていない。ただし **Guardian の人への寄与はゼロ**・**最終防衛線が L0 から L0' へ移っている**（後者は PHASE-1-GATE＝G-g） |

### 12-2. 未決・本書が自信を持てない箇所（隠さない）

1. **`t_react`（検知〜停止レイテンシ）の推定に足る情報が repo に無い。** S-3 で置いた 0.2 s は**本書の仮定**であり、docs 由来ではない。AMCL 100-200ms（[12:510](../architecture/12-infrastructure-common.md)）は別系統のレートで流用不可。→ G-f。
2. **成人の肩高 1.30-1.45m（[09 R-2① :239](09-hand-raise-summon.md) が既に `# TODO(Phase 1 実測)` を付けている）と、本書 S-5 で使った「下腿 0.1–0.5m」「脚幅 0.1m」はいずれも一般値**であり repo 由来ではない。S-5 の結論（検知は頑健）はこの仮定に対して感度が低いと判断したが、**M-1/M-2 で覆る可能性はある**。
3. **`d_min`（到達圏下限）の妥当な水準を本書は示せない。** ロボットの停止距離（G-f）・人の反応・絵としての近さのトレードオフであり、実測と演出判断の両方が要る。
4. **recovery bypass 窓（H-3）の実際の発火頻度が未知。** [12:560](../architecture/12-infrastructure-common.md) の Phase-2 再訪トリガは live PoC 前提で、部屋での頻度は測っていない。推奨 (A) は「発火を減らす」方向であって「窓を閉じる」ものではない——**この窓は運用規律 D-1 に依存し続ける**。
5. **単騎 X-lite での `traffic_mode` の実運用値を launch 引数レベルで最終確認していない。** 本書は config 既定 `traffic_mode: none`（[config/warehouse.base.yaml:6](../../config/warehouse.base.yaml)）と launch の `condition=collision_active`（`traffic_mode != open-rmf`）から「collision_monitor は起動する」と判断したが、**部屋デモの実 launch 引数で `traffic_mode` が上書きされないこと**は運用時に確認されたい（`config/<env>/warehouse.yaml` は本 worktree で未確認）。
6. **本書は L2 Policy Gate の各チェック（freshness / battery / emergency / rate / duplicate）を個別には再評価していない。** これらは部屋転換で前提が変わらない（時間・電池・重複の話であって空間の話ではない）と判断したためだが、**`duplicate_destination` だけは S-7 W-d 経由で部屋の waypoint 設計と結合する**点だけ記録しておく。
7. **HTML companion への反映は本書のスコープ外**（[ADR-0009 Open :83](../adr/0009-m1-room-scale-operation.md) と同じ扱い）。

### 12-3. 本書が ADR-0009 に対して持つ関係

[ADR-0009 §Open :82](../adr/0009-m1-room-scale-operation.md) の `# TODO(安全レビュー)`「人とロボットが同一平面に立つ構成での安全論証の組み替え（帰結 ⑦）」に対し、**本書は「組み替え後の論証の残余リスク評価」という分析成果物を提供する**。**ADR-0009 は accepted であり本書はこれを編集しない**（[ADR-0009 :105](../adr/0009-m1-room-scale-operation.md) の「既存本文の行は動かさず」規律・[#165 教訓](../dev/03-retrospectives.md)）。当該 Open 項目を**閉じるかどうかはオペレーターの裁定**であり、閉じるとすれば §11 のチェックリストが埋まった時点、閉じ方は ADR-0009 の末尾追補（第三の追補）または後続 ADR となる——**本書はその判断を先取りしない**。

---

## 13. References（双方向）

### 決定・設計の正本（forward）

- **決定正本**: [adr/0009-m1-room-scale-operation.md](../adr/0009-m1-room-scale-operation.md)（帰結 ⑦ :51-53 / トレードオフの訂正 :62 / §Open の `# TODO(安全レビュー)` :82 / 追補② 追加Open①〜⑤ :107-130）
- **ジェスチャ司令の設計正本**: [09-hand-raise-summon.md](09-hand-raise-summon.md)（§3 幾何 :39-45 / §5 幾何解決 :111-113 / §8 安全 :137-143 / 追補 R-1〜R-6 :211-285 / 追補② R-7〜R-10 :289-326）
- **知覚・Nav2 側**: [architecture/23-perception-and-localization.md](../architecture/23-perception-and-localization.md)（§1 P1/P2 :37-43 / scan 面 :495 / OQ-15 :526 / F-2 幾何 :583-590 / F-6 :622-625 / G 系列 :659-775、とくに **G-7 :717-734**・**G-8 :736-743**・**G-10 :755-761**）
- **安全レイヤーとインフラ**: [architecture/12-infrastructure-common.md](../architecture/12-infrastructure-common.md)（安全レイヤー 4 層 :72-93 / L0 deadman :79 / freshness guard :506-513 / collision_monitor トポロジ :522-552 / Open ⑤⑥① の確定 :554-561 / 変位ゲート追補 :601-）
- **ハードウェア**: [shared/02-hardware-design.md](../shared/02-hardware-design.md)（M1 実寸・LiDAR 上面 :302 / L0' の限界と MCU watchdog 未確認 :329 / C 系列 C-1〜C-4 :357-359 / 部屋スケールとの関係 :525-528）
- **レイアウト側（ジオラマ・歴史記録）**: [shared/04-diorama-layout.md](../shared/04-diorama-layout.md)（F-L 系列・W3 注記）
- **用語**: [docs/GLOSSARY.md §11 :146](../GLOSSARY.md)（**部屋スケール運用**・**operational stop**・**非円形 footprint**）
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
