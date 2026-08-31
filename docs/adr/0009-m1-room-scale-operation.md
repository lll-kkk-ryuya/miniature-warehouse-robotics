# M1 フェーズは部屋スケールで運用する（ジオラマは M1 では走行しない）

**Status**: accepted（2026-08-18 オペレーター決定。決定そのものは確定。部屋の範囲・撮影構図・room sim world の要否・**sim/実機の config 二重化・安全論証の再レビュー等**が Open ＝ §Open および末尾【2026-08-18 追補②】参照）

単騎 M1（[ADR-0006](0006-single-bot-first.md)）の走行環境を、ミニチュア倉庫ジオラマ（1800×900mm）から**実際の部屋（room scale）**へ移す。ジオラマは M1 フェーズでは**走行に使わず凍結保存**する（sim の回帰環境としては現状維持・2台復帰フェーズの資産）。部屋デモの形は**倉庫設定を薄め、ジェスチャ召喚（手挙げ・指差し）を主役**にする。直前に land した [PR #530](https://github.com/lll-kkk-ryuya/miniature-warehouse-robotics/pull/530)（OQ-3 = 非円形 footprint の F 系列）は**破棄せず re-scope して生存**させる。

## Context / 背景

- **OQ-3 は解けたが、解の中身は「ジオラマ運用制約の束」だった。** M1 外接円 184mm vs 通路 280mm（[23:244](../architecture/23-perception-and-localization.md)）は #530 の (c) ハイブリッド＝非円形 footprint 採用で決着した（[23:571-577](../architecture/23-perception-and-localization.md) F-1）。ただしレイアウト側の代償として **280mm 通路は直進専用（その場回転は幾何学的に不可能）**・**回転は ≥ ~420mm 角の交差点／転回ポケットでのみ**・**通路端 goal は回転不要な向きで置く**という制約が同時に課された（[04:178-193](../shared/04-diorama-layout.md) F-L1〜F-L3）。
- **そのレイアウトが 1800×900mm に収まるかは、いまだ未計算のまま。** 「棚3列 + 縦通路2本 + 横断通路1本 + バース2 + 出荷/充電ステーション」の収まりは `# TODO(発注前〜Phase 1)` として未解決であり（[04:132](../shared/04-diorama-layout.md)）、F-L4 は**交差点 ≥ ~420mm 角での作図をやり直せ**と要求している（[04:201-204](../shared/04-diorama-layout.md)）。すなわち #530 は OQ-3 を解いた一方で、**盤面収まり問題を難しくした側面がある**。
- **現行ジオラマに M1 を載せると、走行目標点が 1 点も成立しない（W3）。** [PR #528](https://github.com/lll-kkk-ryuya/miniature-warehouse-robotics/pull/528) が確定した `locations` 9 点は円形 `robot_radius` 0.075m 基準の検証であり、committed `map.pgm` × `config/warehouse.base.yaml` からの実測クリアランスは **95.1〜125.1mm**。**9 点すべてが外接 184mm 未満＝どこでもその場回転不可**、**うち 6 点は内接 115.7mm すら下回り goal として不成立**（[04:195-199](../shared/04-diorama-layout.md) / [23:633](../architecture/23-perception-and-localization.md) F-7 W3）。
- **一方、部屋では通路制約がそもそも発生しない。** M1 実寸は 231.4 × 284.4mm・対角 ≈367mm（[04:115](../shared/04-diorama-layout.md) / [04:119](../shared/04-diorama-layout.md)）で、一般的な室内のドア開口・家具間隔はこれを大きく上回る。280mm 隘路・420mm 転回ポケット・24.3mm/側 クリアランスという**攻めた数値の束が、環境を変えるだけで丸ごと不要になる**。
- **物理ジオラマは未着工であり、作り直しコストはまだ払っていない。** 「ジオラマは未着工のため、通路幅は車体に従属する（逆ではない）」（[04:111](../shared/04-diorama-layout.md)）。したがって本 ADR の「凍結保存」が指す実体は、主に**設計 doc と sim world（`map.pgm` / `warehouse_sim.layout`）**であって、造作物の廃棄ではない。
- **主役デモは盤面サイズに依存しない。** 手挙げ召喚・指差しは 1 台で成立し（[ADR-0006:12](0006-single-bot-first.md)）、その設計正本 [mode-x-er/09](../mode-x-er/09-hand-raise-summon.md) は ER をバイパスする決定論ローカル経路（:53 (iii) 採用）と KNOWN_LOCATIONS への snap（:149 (a) 採用）で閉じており、**走行面の広さを前提にしていない**。

## Decision / 決定

オペレーター裁定（2026-08-18）は以下の 4 点。**1〜4 は裁定そのもの**、5〜6 は裁定から機械的に導かれる帰結として本 ADR が確定する。

1. **ジオラマ（1.8×0.9m）は M1 フェーズでは使わない＝凍結保存**する。sim の回帰環境としては現状維持とし、2台復帰フェーズの資産として保持する。
2. **部屋デモの形＝倉庫設定は薄めてジェスチャ召喚中心**にする。手挙げ召喚／指差しを主役に置き、倉庫タスクの演出は従とする（参考: [ZED2i Body Tracking 召喚事例](https://qiita.com/motoms/items/b87d08448cdaddb24c35) 系の形）。
3. **PR #530（OQ-3 F 系列）は re-scope して land 済み扱い**とする。破棄・revert はせず、本ラウンドで scope 注記を適用する。
4. **記録方法＝ADR 新規 + 関連 docs 一括改訂**（本 ADR ＋ 各 doc への末尾追補）。

導出される確定事項:

5. **`KNOWN_LOCATIONS` の 9 キーは凍結のまま・改名しない。** 変わるのは**値**であり、Phase 1 の部屋 SLAM 地図取得後に実測 waypoint へ差し替える。凍結ハブ（`locations.py` の 9 キー・`schemas.py:159` の語彙 gate）は無改訂で、L3 Validator の語彙 gate も従来どおり効く。
6. **ジェスチャ司令の構造は不変。** ER バイパス（[09:53](../mode-x-er/09-hand-raise-summon.md)）・INV-1／INV-2（座標を draft に載せず L3/L2 を 1 ステップも迂回しない・[09:25](../mode-x-er/09-hand-raise-summon.md), [09:57](../mode-x-er/09-hand-raise-summon.md)）・KNOWN_LOCATIONS への snap は**そのまま**。変わるのは snap 先の座標値と、§3 の幾何前提の実測値である。

## Consequences / 帰結

① **W3（ジオラマ 9 点の再設計）は中止（cancelled）。** [23:633](../architecture/23-perception-and-localization.md) F-7 と [04:195-199](../shared/04-diorama-layout.md) F-L3 が「M1 実機マップ取得後に 9 点すべてを再設計する」と宣言した別スライスは、**ジオラマを走らない以上その前提を失う**。設計作業が実在したこと自体は記録として残す——本ラウンドの並行レーンが M1 向けの 9 点再設計案を作成しており、**ジオラマ復帰フェーズの参考として保存**する（`# TODO`: 成果物の保存先は未定＝下記 Open）。**再設計の宛先は「ジオラマ 9 点」から「部屋 waypoint 9 点」へ差し替わる**（Decision 5）。

② **F 系列の scope を二分する。** 詳細は [23 末尾【2026-08-18 追補】G 系列](../architecture/23-perception-and-localization.md)。
   - **部屋でも生存（robot-intrinsic）**: 非円形 footprint polygon の採用そのもの（F-1）・`footprint:` 化と `consider_footprint: true` の同一 PR 制約（F-5-1/2・#67 教訓）・inflation を内接 0.1157 基準へ再調整すること（F-5-3 の**手法**）・R-26 safety unit の書き換え（F-5-6）・`FOOTPRINT_POLYGON` / `CIRCUMSCRIBED_RADIUS` の additive 追加（F-6）。**部屋にもドア開口・家具の隙間はあり、矩形車体を矩形として扱う価値は失われない。**
   - **ジオラマ限定の歴史記録**: 280mm / 510mm / **交差点セル寸法としての** ≈367mm 角という**通路数値**（出所は [04:128-130](../shared/04-diorama-layout.md) の 3 行＝M1 暫定試算表。[23](../architecture/23-perception-and-localization.md) F-2 の表はこのうち **280mm の 1 行**を引き写したもの）・≥ ~420mm 角の転回ポケット（F-4-2 / [04](../shared/04-diorama-layout.md) F-L2 由来）・24.3mm/側 の直進クリアランスと ±14mm 低コスト帯（F-2 / F-5-3 の**具体値**）・(b) 拡幅案の却下理由（F-3）・レイアウト側制約 F-4 と [04](../shared/04-diorama-layout.md) F-L1〜F-L5 の全体。**通路内 Spin recovery 抑止（F-5-4）は「ジオラマ限定」ではなく「手法は生存・発火条件は再評価」へ訂正**（[23 G-2 / G-10](../architecture/23-perception-and-localization.md)）。
   - F-2 のうち **M1 実寸 231.4×284.4mm / 外接 184mm / 内接 115.7mm は車体の性質**であり、環境に依らず生存する。

③ **OQ の再スコープ。** 「ジオラマの通路幾何から出た問い」と「車体・実装から出た問い」を分ける。
   - **OQ-19（280mm 通路の許容 yaw 誤差）/ OQ-21（±14mm 低コスト帯での MPPI 追従）= ジオラマ限定・凍結。** どちらも 280mm 通路という盤面数値から導かれた問いで、部屋では発火しない。**ただし背後の幾何的事実——矩形の有効掃引幅が `231.4·cosθ + 284.4·sinθ` で yaw とともに増える（[23:590](../architecture/23-perception-and-localization.md)）——は車体の性質として残る**ので、部屋で狭い開口（ドア等）を通す際に同じ形で再計算する。
   - **OQ-18（sim 車体と実寸の同時移行可否）= 性質が変わる。** sim がジオラマのまま・実機が部屋になるため、「sim の車体を M1 実寸へ倒すか」は**実機の可否を握らなくなり、sim 回帰の忠実度だけの問題**へ降格する（下記 ④）。
   - **OQ-20（`base_link` の前後非対称オフセット・[23:641](../architecture/23-perception-and-localization.md)）= そのまま LIVE。** 車体の回転中心という **robot-intrinsic** な量であり、環境を変えても消えない。Phase 1 実機実測で確定する。
   - **⚠️ 別件として重要度が上がる残件: goal の yaw が落ちている。** `nav2_bridge.py` の `_pose` が `orientation.w = 1.0` を固定し、座標ゴールは yaw 非対応のまま通る（＝`warehouse_nav2_bridge/CLAUDE.md` の「#223 残 ②」。impl は行 pin せずキーで指す＝[session-orchestration.md §8](../../.claude/rules/session-orchestration.md)）。**これは OQ-20 ではなく #223 の残件**である。ジオラマでは F-L3「通路端の goal は回転不要な向きで置く」で回避できていたが、**部屋では召喚の到達姿勢（人の方を向いて止まる）が絵の質に直結する**ため、重要度が上がる。yaw 対応は `_pose` の quaternion 化＝別変更（所有トラック判断）。

④ **sim はジオラマのまま（回帰環境）。** `map.pgm` と `warehouse_sim.layout` は変更せず、2D costmap 系の回帰を守る場として維持する（[23:629](../architecture/23-perception-and-localization.md) F-7「sim は CURRENT 2D costmap の回帰を守る場」）。**代償として、sim と実機の環境が乖離する**——sim で緑でも部屋での挙動を保証しない。**room-scale の sim world を作るかは Open**（下記）。

⑤ **動画の「実機パート」の撮影前提が崩れる＝再設計が要る（本 ADR では行わない）。** [05](../shared/05-video-storyboard.md) は 1800×900mm 盤面の俯瞰画を主素材とする構成であり、**実機パートについては**部屋走行で成立しない（**sim パート＝先行リリース構成はジオラマのまま有効**＝帰結 ④）。**Open item として立て、本ラウンドでは storyboard を書き換えない。**

⑥ **Phase 1 は「部屋の SLAM 地図取得」を含む。** ジオラマ由来の暫定値——`locations` の 9 座標（[09:17](../mode-x-er/09-hand-raise-summon.md) P5「暫定値・ジオラマ再設計待ち」）・実機用の `map.pgm`・通路幅表——は **M1 実機については supersede** される（sim 側は ④ のとおり不変）。なお現行 doc06 では SLAM 地図生成は **Phase 2 前半**（[06:155](../architecture/06-implementation-phases.md)）にあり、Phase 1 のタスクには「ベースボード塗装・通路テープ貼り」（[06:132](../architecture/06-implementation-phases.md)）というジオラマ造作が含まれる。この対応関係の整理は [06 末尾追記](../architecture/06-implementation-phases.md)で行う。

⑦ **ジェスチャ設計の幾何前提が変わる（構造は不変・Decision 6）。** 最も効くのは**カメラ仰角**と**安全論証**の 2 点で、詳細は [09 末尾【2026-08-18 追補】](../mode-x-er/09-hand-raise-summon.md)。要旨:
   - ジオラマでは盤面が机高にあり、カメラ（走行面上 0.09-0.13m）と操作者の肩がほぼ同じ高さ帯に入っていた。**部屋では操作者が床に立つため、肩を見上げる仰角がおよそ倍**になる。[09:43](../mode-x-er/09-hand-raise-summon.md) の「Phase 1 は水平固定」とパン/チルト雲台の不採用は、**部屋では再検討が要る**。
   - **安全論証の書き換えが要る。** [09:141](../mode-x-er/09-hand-raise-summon.md) は「到達点は必ず盤面上の 9 location ＝**盤外の人へは構造的に到達できない**」を安全の根拠にしているが、部屋では**人とロボットが同一平面に立つ**ためこの構造的保証が消える。残る保護は「到達集合を 9 waypoint に限定する語彙 gate ＋ L1 collision_monitor ＋ L0' 0.3m/s クランプ」であり、**論証をこの形に組み替えたうえで安全レビューに掛ける**必要がある（新規レビュー項目）。

## トレードオフ / Trade-offs

- **プロジェクトの看板が M1 フェーズでは画面に出ない。** 「ミニチュア倉庫ジオラマ（1.8m×0.9m）に自律走行ロボット」という前提は root `.claude/CLAUDE.md` の Project Overview から [00](../shared/00-project-overview.md) まで一貫した土台であり、その視覚的アイデンティティを今回は使わない。
- **「倉庫」の説得力が絵として落ちる。** 棚・バース・通路という倉庫記号を薄める以上、「倉庫ロボット」ではなく「部屋を走るロボット」に見える。Decision 2 はそれを承知でジェスチャ召喚の分かりやすさを取る選択である。
- **環境統制が効かなくなる。** 照明・床材の反射・人の往来・家具の移動は、閉じた盤面には無かった非統制変数。AMCL / MOLA-LO（[23 B-2](../architecture/23-perception-and-localization.md)）の再現性はジオラマより悪化しうる。一方で**走行面積が広がりクリアランスが緩む**ぶん、狭隘由来の失敗（#125 の「経路を commit できず停止」）からは解放される。
- **sim の予測力が落ちる**（帰結 ④）。回帰は守れるが、部屋での失敗を sim が先に捕まえられなくなる。
- **撮影素材の作り直しコスト**（帰結 ⑤）。
- **安全論証の再構築コスト**（帰結 ⑦）。「人に到達できない」という強い構造的保証を失い、より弱い多層防御の論証へ置き換える。**【訂正 2026-08-18】これは構造的保証（幾何学的に不可能）から多層防御（緩和）への後退であり、同等性は主張しない**（旧文「実質的な安全性の低下ではなく論証形式の変更」は撤回）。**残余リスク評価は安全レビューで行う**（[09 R-8-1](../mode-x-er/09-hand-raise-summon.md)）。
- **#530 の作業のうちレイアウト側（F-3 / F-4 / F-L1〜F-L5）は、当面使われない記録になる。** 破棄はしないが、投じた検討がすぐには実を結ばない。

## Considered Options / 却下

- **(A) ジオラマを M1 実寸で作り直す（F-L4 の作図を完遂する）**: 却下。420mm 角の転回ポケットを交差点ごとに確保したうえで「棚3列 + 縦通路2本 + 横断通路1本 + バース2 + 出荷/充電ステーション」が 1800×900mm に収まる保証はなく（[04:132](../shared/04-diorama-layout.md) が未計算のまま）、収まったとしても **9 点の再設計 + 実機マップ再取得**は部屋案と同額のコストとして残る。**同じコストを払って、なお盤面制約が残る。**
- **(B) 盤面を広げる（例 2400×1200mm）**: 却下。設置場所・可搬性・造作コストが増える一方、「ミニチュア」という売りは薄まる。薄まるなら部屋のほうが徹底している。
- **(C) M1 を諦めて小型車体（~150mm 級）に戻す**: 却下。M1 は購入済みで、[ADR-0006](0006-single-bot-first.md)（単騎）・[ADR-0008](0008-ros2-distro-humble-for-rosmaster-m1.md)（Humble pin）が M1 前提で積み上がっている。車体を戻せば distro 判断まで巻き戻る。
- **(D) ジオラマで走るが、その場回転を諦めて直進専用トポロジだけで巡回する**: 却下。回転制約以前に、**9 点中 6 点が内接 115.7mm を下回り goal として成立しない**（[04:197](../shared/04-diorama-layout.md)）。goal が置けなければ走行デモが組めない。
- **(E) 部屋に実寸の棚を置いて倉庫を再現する**: 却下（Decision 2 と非整合）。倉庫設定を薄める裁定と真逆で、造作コストも (A) と同種。
- **(F) PR #530 を revert する**: 却下（Decision 3）。footprint polygon 採用は部屋でも生存する（帰結 ②）ため、revert は生きた決定まで捨てることになる。**scope 注記で足りる。**

## Open / 未決

- `# TODO(ユーザー判断)` **部屋のどの範囲を走行領域とするか・家具構成**（走行面の確定。SLAM 地図取得の前提）。
- `# TODO(ユーザー判断)` **撮影構図**（帰結 ⑤。[05](../shared/05-video-storyboard.md) の俯瞰主体構成の代替）。
- `# TODO(ユーザー判断)` **room-scale の sim world を作るか**（帰結 ④）。作らない場合、sim 回帰＝ジオラマのままで実機との乖離を許容する。
- `# TODO(実装スライス)` **`KNOWN_LOCATIONS` 9 キーの部屋での役割名の意味づけ**（`shelf_1` / `berth_A` / `charging_station` 等が部屋で何を指すか）。**キー改名はしない**（Decision 5）ので、名前と実体の乖離をどう扱うかを決める。
- `# TODO(実装スライス)` **W3 の M1 向け 9 点再設計案の保存先**（帰結 ①。現時点で docs 未収録）。
- `# TODO(Phase 1 実測)` **[09 §3](../mode-x-er/09-hand-raise-summon.md) の幾何前提の部屋での再実測**（カメラ仰角・操作者の立ち位置・sentry pose の再定義・snap 半径 0.25m の妥当性）。
- `# TODO(安全レビュー)` **人とロボットが同一平面に立つ構成での安全論証の組み替え**（帰結 ⑦）。
- `# TODO(別ラウンド)` **HTML companion への反映**（[perception-localization-flow.html](../architecture/perception-localization-flow.html) / [robot-architecture-tree.html](../architecture/robot-architecture-tree.html)）。本 ADR ではスコープ外。
- `# TODO(ユーザー判断)` **初回公開ゲートの再定義**（[ADR-0006](0006-single-bot-first.md) の Open がさらに変質する——ジオラマ前提の「倉庫デモ」ゲートは部屋では測れない）。

## References

- [ADR-0006 ロボット1台先行構成（single-bot-first）](0006-single-bot-first.md) — 本 ADR の前提（単騎 M1）
- [ADR-0007 俯瞰カメラ不使用・搭載 NN でジェスチャ認識](0007-no-overhead-camera-gesture-via-onboard-nn.md) — 主役に格上げされるデモの決定
- [ADR-0008 ROS 2 distro を Jazzy から Humble へ](0008-ros2-distro-humble-for-rosmaster-m1.md) — M1 前提の distro pin
- [shared/04-diorama-layout.md](../shared/04-diorama-layout.md) — ジオラマ正本（:111 未着工 / :115 M1 実寸 / :132 収まり未計算 / F-L1〜F-L5 :178-210 / W3 :195-199）＋末尾【2026-08-18 追補】
- [architecture/23-perception-and-localization.md](../architecture/23-perception-and-localization.md) — OQ-3 / F 系列の正本（:244 OQ-3 / :565-655 F-1〜F-9 / :633 W3 / :639-642 OQ-18〜21）＋末尾【2026-08-18 追補】G 系列
- [mode-x-er/09-hand-raise-summon.md](../mode-x-er/09-hand-raise-summon.md) — ジェスチャ司令の正本（:41,:43,:44 幾何・sentry / :141 安全論証 / :149 snap 採用）＋末尾【2026-08-18 追補】
- [shared/05-video-storyboard.md](../shared/05-video-storyboard.md) — 撮影構成（再設計が Open）
- [architecture/06-implementation-phases.md](../architecture/06-implementation-phases.md) — Phase 定義（:132 ジオラマ造作 / :155 SLAM 地図生成）
- [PR #530](https://github.com/lll-kkk-ryuya/miniature-warehouse-robotics/pull/530)（OQ-3 F 系列 / Issue #519）/ [PR #528](https://github.com/lll-kkk-ryuya/miniature-warehouse-robotics/pull/528)（走行目標点 9 点）
- `ws/src/warehouse_nav2_bridge/warehouse_nav2_bridge/nav2_bridge.py`（`orientation.w = 1.0` 固定＝yaw 非対応・#223 残 ②。行 pin ではなくキーで指す＝[session-orchestration.md §8](../../.claude/rules/session-orchestration.md)）
- [docs/GLOSSARY.md §11](../GLOSSARY.md) — **部屋スケール運用（room-scale operation）** の正準定義（本 ADR と同時追加・双方向）
- [dev/03-retrospectives.md](../dev/03-retrospectives.md) — #165 末尾追記規律（本 ADR に伴う各 doc 改訂はすべて末尾追補）

---

## 【2026-08-18 追補②】二重監査の反映（§Open の追加項目・§帰結の訂正）

本 ADR の初版に対する**二重独立監査**（いずれも FIX-FIRST）の指摘を反映する。**Status（accepted）と Decision 1〜4 は変更しない**——変わるのは「何が Open として残っているか」の正確さと、いくつかの帰結の言い方である。既存本文の行は動かさず（[#165 教訓](../dev/03-retrospectives.md)）、同一行内で訂正できたものは §帰結 ②（F-2 の帰属・≈367mm の限定）・③（impl 参照をキー指しへ）・⑤（実機パート限定）・§トレードオフ（安全論証の同等性主張の撤回）・Status に反映済で、その根拠が本節である。

### 追加 Open ①: sim / 実機の config 二重化（Slice 1 の前提・最重要）

**部屋でも `footprint:` 化は実施する**（帰結 ② の「生存」は不変）。しかし**どの config に入れるかが未決**であり、これは Slice 1 が land できるかを直接握る。

- **`nav2_params.yaml` は env overlay を持たない単一ソース**（`config/dev|stg|prod/warehouse.yaml` は Nav2 params を上書きしない・2026-08-18 実査）。したがって M1 footprint 化（両 costmap の `robot_radius` → `footprint:` ＋ `FollowPath.CostCritic.consider_footprint` の flip）は、**実機（部屋）と同時に「ジオラマ map の sim 回帰」も変える**。帰結 ④ が「sim はジオラマのまま」と宣言した以上、両立方法を決めないと Slice 1 は着手できない。
- **同居方法は未決 OQ**: (a) Nav2 params に env overlay を新設 / (b) sim 専用 params を分ける / (c) sim も M1 実寸化する（sim URDF・`robot_dimensions.py` の PROVISIONAL 群まで）。所有＝ sim / nav-traffic / bringup の調整事項。
- **`config/warehouse.base.yaml` の `locations` も単一ソース**（env overlay に `locations` は無い・同上実査）。`tests/unit/test_known_locations_navigable.py` はこの base 値を**committed なジオラマ `map.pgm`** に対して検証しており、M1 内接 0.1157m では **9 点中 6 点が不成立**（[04:197](../shared/04-diorama-layout.md)）。すなわち **実機値 / sim 値の分離手段が未決**であり、**決め方によって当該テストのオラクル定義（どの map に対して何の内接半径で検証するか）が変わる**。
- **⚠️ 帰結 ③ の OQ-18 に関する記述の訂正**: 初版は OQ-18（sim 車体と実寸の同時移行可否）を「実機の可否を握らなくなり、sim 回帰の忠実度だけの問題へ降格」としたが、**これは誤り**。上記のとおり OQ-18 は「**Slice 1 が land できるか・sim ゲートが緑を保てるか**」を握る**構成問題として LIVE** である。詳細と新規 OQ-22 の登録は [23 G-7](../architecture/23-perception-and-localization.md)（訂正は同 doc G-3 に同一行内で反映済）。

### 追加 Open ②: C-3（collision_monitor）改訂は部屋運用の前提条件

帰結 ⑦ の多層防御論証は「L1 collision_monitor が生きている」ことに依存するが、**CURRENT の `PolygonStop` は `radius: 0.09` で M1 の内接 0.1157 を下回る＝車体内部で発火し機能しない**。**C-3 改訂（外接 `CIRCUMSCRIBED_RADIUS` ≈0.184 + 反応余裕へ・別 PR・安全レビュー必須）の完了は、部屋で走らせる前提条件**とする（[23 G-8](../architecture/23-perception-and-localization.md) / [09 R-8-2](../mode-x-er/09-hand-raise-summon.md)）。未改訂のまま部屋運用に入ると、多層防御の 1 枚が名目上だけ存在する状態になる。

### 追加 Open ③: 召喚レグの解決規則の再設計（安全レビュー対象）

**召喚レグは snap 半径を持たない**（[09:111](../mode-x-er/09-hand-raise-summon.md)「人は盤外ゆえ snap 半径は課さず sanity bound 2.0m のみ」）。ジオラマでは正射影点が必ず盤外に落ちるため盤縁が構造的な壁として働いていたが、**部屋では正射影点が人の足元＝走行面の内側に落ちる**ため、「最寄り location」＝**操作者に最も近い waypoint** となり「操作者の足元へ向かう」動作になる。**snap 半径の導入 or 到達圏の制約が Phase-1 設計として必須**（[09 R-7](../mode-x-er/09-hand-raise-summon.md)）。**安全レビュー対象**に含める。

### 追加 Open ④: nvblox dynamic 層「不採用」の再評価

[23:74](../architecture/23-perception-and-localization.md) / [23:278](../architecture/23-perception-and-localization.md) の不採用根拠「走行面上に人はいない（ジオラマに人はいない）」は、**部屋では成立しない**。people segmentation の GPU コスト（S1 の 8GB 予算に直撃）vs 静的 TSDF に人が焼き付く問題、の裁定を**新規 OQ-23** として登録した。**ジオラマ体積前提のメモリ試算**（[23:212-214](../architecture/23-perception-and-localization.md)）も部屋では桁が変わるため同 OQ に含める（[23 G-9](../architecture/23-perception-and-localization.md)）。

### 追加 Open ⑤: #223 座標ゴール seam を安全レビュー対象に含める

Nav2 Bridge の `navigate` には**名前ゲートを経由しない座標ゴール経路**があり、**座標の範囲チェックを持たない**（`warehouse_nav2_bridge/CLAUDE.md` の「#223 残 ②/③」。operator tooling 専用）。ジオラマでは「人は盤外」という暗黙の覆いが最後の防波堤になっていたが、**部屋ではその覆いが無い**。帰結 ⑦ の安全レビュー項目に本 seam を含める（[09 R-8-3](../mode-x-er/09-hand-raise-summon.md)）。

### Issue #519（Slice 1）への申し送り

**Slice 1（F-5 の params 実装）は継続**（footprint polygon 採用は部屋でも生存＝帰結 ②）。ただし **F-5-3 の具体値（±14mm 低コスト帯・inflation ≈0.125-0.126）は G-2 で無効**（手法のみ生存）・**F-5-4 は「ジオラマ限定」ではなく発火条件の差し替え**・**着手前に [23](../architecture/23-perception-and-localization.md) の G 系列（G-1〜G-12）を必読**とし、**G-7 の config 二重化（OQ-22）を最初のステップ**とすること（正本は [23 G-11](../architecture/23-perception-and-localization.md)）。

### 本追補の関連リンク（双方向）

- [architecture/23-perception-and-localization.md](../architecture/23-perception-and-localization.md) 末尾【2026-08-18 追補②】**G-7〜G-12**
- [mode-x-er/09-hand-raise-summon.md](../mode-x-er/09-hand-raise-summon.md) 末尾【2026-08-18 追補②】**R-7〜R-10**
- [shared/00-project-overview.md](../shared/00-project-overview.md) / [shared/01-budget-and-procurement.md](../shared/01-budget-and-procurement.md) / [shared/02-hardware-design.md](../shared/02-hardware-design.md) 各末尾【2026-08-18 追記】（入口 doc からの back-link）

---

## 【2026-08-18 追補③】§Open「`# TODO(安全レビュー)`」に対応する記録の所在

帰結 ⑦（:51-53）と §Open の `# TODO(安全レビュー)`（:82）が要求した**安全レビューの分析部分**は、新設 doc **[mode-x-er/10-room-scale-safety-review.md](../mode-x-er/10-room-scale-safety-review.md)** に記録した（本追補はポインタのみ。**Status = accepted と Decision 1〜4、および既存本文の行は一切変更しない**＝追補② :105 と同じ扱い）。

- 内容: 8 項目のハザード分析（ハザード × layer × 現行緩和 × 残余リスク × 判定）＋ **部屋運用開始の前提条件チェックリスト**。追加 Open ②（C-3）・③（召喚レグ）・⑤（#223 seam）はそれぞれ同 doc の S-3 / S-2 / S-4 に対応する。
- **⚠️ 最も重要な所見**: 帰結 ⑦ :53 が「残る保護」として挙げた **①語彙 gate ②L1 collision_monitor ③L0' 0.3m/s クランプ** のうち、**実体を確認した結果、現時点で機能しているのは ① のみ**である（② は停止円が車体内部かつ `traffic_mode: open-rmf` の env では node ごと未起動、③ は serial driver node が未 land で未結線）。詳細は同 doc §2 冒頭 / §12-1b。
- **本 ADR の Open 項目は本追補では閉じない。** 同 doc は**分析レビューであって運転許可ではなく**、`# TODO(安全レビュー)` を閉じるのはオペレーターの裁定である（同 doc §12-3）。閉じる場合の受け皿は同 doc §11 のチェックリスト充足を確認したうえでの本 ADR 末尾追補または後続 ADR。

---

## 【2026-08-30 追補④】追補③の「③ L0' 未結線」は #550 で解消（Status・Decision・既存本文の行は不変）

追補③の「現時点で機能しているのは ① のみ」（:149）は **2026-08-18 レビュー時点**の実体であり、当時の記述として保存する（追補③冒頭 :146 の規律どおり既存行は変更しない）。その後の変化のみ記録する:

- **③ L0' 0.3m/s クランプは PR #550（`93bfc93`・2026-08-26 merge）で結線済み＝[10 §11 G-l](../mode-x-er/10-room-scale-safety-review.md) は land 済**。`warehouse_m1_driver` の `console_scripts` に `m1_driver` が入り、全 dispatch が `clamp_body_velocity` を必経（`M1DriverCore.on_cmd_vel`・R-26 unit = `tests/unit/test_m1_driver_core.py` で pin）。
- **「結線済み」≠「実機確認済み」**: 実機動作確認（[mode-m1/03](../mode-m1/03-joystick-teleop-bringup.md) の M0-M2 ゲート・M2 negative test・W-1 brake 実測）と G-g（USB 抜線試験）は**未実施**（2026-08-30 時点で実施記録なし）。③の復帰はコード実体の話に留まる。
- **② L1 collision_monitor は依然無機能**（停止円が車体内部＋`traffic_mode: open-rmf` の stg/prod では node ごと未起動）＝追加 Open ②（C-3 改訂）は未充足のまま。
- **§Open の `# TODO(安全レビュー)`（:82）は本追補でも閉じない**（追補③ :150 と同じ。閉じるのはオペレーター裁定＝[10 §12-3](../mode-x-er/10-room-scale-safety-review.md)）。詳細の同期先は [10 §2 冒頭 / §10-2② / §12-1b](../mode-x-er/10-room-scale-safety-review.md) と [09 追補③ ⑤ :340](../mode-x-er/09-hand-raise-summon.md)（いずれも本追補と同一 PR で「結線済み・実機未確認」へ更新済）。
