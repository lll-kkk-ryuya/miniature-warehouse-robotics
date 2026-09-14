# 08 — アーキテクチャ v2（参照アーキテクチャ整合・01〜09 の再分解・外部レビュー照合）

作成日: 2026-09-12
Status: **提案（裁定待ち = `OQ-OD80`）**。2026-09-12 のエージェントチーム 3 レーン（自動運転リファレンス構造／屋外ロボット学術スタック／外部レビュー主張の一次情報検証）の報告を統合した。外部一次情報は URL + 参照日、repo の pin は執筆時に実 Read（[D]=一次情報/実ファイル確認・[L]=調査レーン報告で URL あり（執筆者未再読）・[I]=推論）。**本 doc は 01〜09 の機能ツリー（[outdoor-architecture-tree.html](outdoor-architecture-tree.html) の旧版）を置換する v2 分解を提案する**。L0–L4 の権限レイヤ軸（[GLOSSARY §3](../GLOSSARY.md)）は**変えない**。変えるのは番号帯（機能分解）だけ。

> 正本ルート: [mode-outdoor/README](README.md)。02〜07 の各設計は不変で、本 doc は「箱の切り方」と「参照アーキテクチャとの差」と「外部レビューの照合」を持つ。図解 = [outdoor-architecture-tree.html](outdoor-architecture-tree.html)（v2 ツリー + 旧 01〜09 対応表）。

## 0. 要旨

1. **01〜09 の切り方は「概ね正しいが 3 箇所で責務が混在」**している。参照アーキテクチャ（Autoware / Apollo / DARPA Urban Challenge の Junior・Talos・Boss / Bertha / Nav2 / DARPA SubT の NeBula・CERBERUS・CMU）の交差集合と照合すると、変えるべきは次の 3 点だけ: **(a) 地図・経路を一級コンポーネントにする**（6 参照すべてが独立段として持つ・我々は 02/04/09 に散在）、**(b) 06 を「安全層／司令ゲート／車両 I/F」に三分割する**（凍結チェーン・twist_mux・シリアルドライバ・ファームが 1 箱に同居）、**(c) 08 の統治部分（横断ゲート）と 09 の許可部分（operation mode）を Governance に集約する**（知覚と統治の同居は「知覚が行動を決める」経路を生む）。
2. **v2 = 12 コンポーネント + 2 横断面**（§2）。運用スタック（Autoware / Apollo）に固有で研究プロトタイプに無いもの＝**実行許可（operation mode）と MRM（最小リスク挙動）の一級化**を採る。遠隔操作者が法的に必須である本モードでは、Nav2 にこれが無いこと自体が設計要件になる。
3. **MRM を 1 種類（prio100 ゼロ Twist）から 4 段へ**（§3-4）: MRM-0 減速（帯・自動）／MRM-A 快適停止（経路保持・条件回復で自動再開）／MRM-B 即時停止（ラッチ・遠隔者の明示解除）／E-STOP（法定・ハード・人手解除）。Autoware は comfortable → emergency の一方向遷移のみ許す。
4. **外部レビュー（2026-09-12）の 11 主張を一次情報で照合**（§4）: 8 件は正しく docs に反映、2 件は「正しい結論を誤った理由で守っていた」（Local Static Layer・cuVSLAM の blocker）、**1 件は誤り**（「Nav2 Route Server は Humble に無い」→ **`nav2_route` 1.1.20 が humble ブランチに backport 済**・index.ros.org で released [D]）。
5. **屋外初期プロファイル**（§5）は外部レビューの目標「事前に確認した舗装路の固定ルートを、許可された幅の中で低速走行し、判断できない状況では停止する」を採り、**契約 0.3 m/s のまま**で始める（車輪換装 [07](07-drivetrain-and-wheel-sizing.md) の前に現地データが取れる）。
6. **「確認実装」は 2 段**（§6）: 現地・実機なしで今すぐできる offline 実装（契約 additive・純ロジック producer + R-26 unit・route graph ツール・operator link 骨格）と、現地データ取得後に決めるもの（RTK 受信機・3D LiDAR・カメラ）。

## 1. 参照アーキテクチャの要点（分解と「安全・許可・MRM・地図・遠隔」の置き場）

| 参照 | 分解 | 安全監視 / 実行許可 / MRM / 遠隔の置き場 | 6 kg 歩道ロボへ転写するもの |
|---|---|---|---|
| **Autoware**（AWF / TIER IV・名古屋大起源）[L] | v1 = Sensing / Map / Localization / Perception / Planning / Control / Vehicle Interface の 7 stack。v1 設計は「driving capability のみ」で fail-safe・HMI・state monitoring は future work と明記 → `system/` 群を後付け（diagnostics graph・component/topic state monitor・**MRM handler**・**operation mode**・**vehicle_cmd_gate**） | AD API `operation_mode`（Stop / Autonomous / Local / Remote）が一級。`vehicle_cmd_gate` が auto / external / emergency の 3 系統から 1 つを選ぶ**単一ゲート**（`external_emergency_stop_heartbeat_timeout`）。MRM = EMERGENCY_STOP / COMFORTABLE_STOP / PULL_OVER、状態 NONE/OPERATING/SUCCEEDED/FAILED。RTI（介入要求）。heartbeat は timestamp + sequence | **operation mode の一級化・単一ゲート・MRM 段分け・RTI・heartbeat 形式** |
| **Apollo**（Baidu）[L] | perception / prediction / planning / control / localization / routing / map / canbus / **guardian** / **monitor** / dreamview / external_command | **Monitor（検知）と Guardian（作用）を分離**。Guardian は「ヒューズ」: モジュール報告が 2.5 s 途絶 or safety_mode_trigger で control command を遮断し制動。周期 10 ms | **検知と作用の分離**（我々の Guardian は両方を兼ねる） |
| **Junior**（Stanford・JFR 2008）[L] | sensor interfaces / perception / navigation（複数 planner + 階層 FSM）/ drive-by-wire / **global services**（logging・time・IPC・**watchdog**・**health monitor**・process controller） | Wireless E-Stop → top-level control の pause/disable。Health monitor から emergency stop と power on/off が Vehicle Interface へ直結 | **global services（横断面）に health monitor と watchdog を置く** |
| **Talos**（MIT・JFR 2008）[L] | sensors / perception / positioning / navigator / drivability map / motion planner / controller / **ADU** | **ADU = RTOS の単純ハードウェア**。watchdog 内蔵で「計算機が不正なコマンドを出すか送信を止めると自動的に PAUSE」。車両状態 PAUSE/RUN/STANDBY/E-STOP は **ADU 内の状態機械** | **状態機械と watchdog を最下層ハードに置く**（= W-3 / estop_relay の理想形） |
| **Boss**（CMU・JFR 2008）[L] | perception + 3 層 planning（mission / behavioral / motion） | 健全性監視は層として提示されない。エラー回復は behavioral 層 | mission / behavior / motion の分離 |
| **Bertha**（Daimler / KIT・2014）[L] | 知覚 / **詳細地図（交通規則属性: 優先権・関連信号・速度制限）** / 地図相対自己位置 / motion planning / 制御 / HMI | 「市販の緊急ブレーキが自律機能の**下に敷かれている**ため、軌道計画・制御で考慮不要」 | **安全停止はプランナの関心事にしない**（BT に埋めない）・**地図に交通規則属性を持たせる** |
| **Nav2**（Macenski・IROS 2020）[D]/[L] | BT Navigator / Planner / Controller / Smoother / Behavior / **Route Server** / Waypoint Follower / Collision Monitor / Map Server / Costmap2D / Lifecycle | Collision Monitor は「Nav2 から独立した safety layer」（Humble で追加）。**実行許可・法令順守・MRM は Nav2 に無い＝アプリ層へ委譲**。Route Server は「ナビゲーショングラフ」で teach-and-repeat 経路を扱い、**Humble 1.1.20 に backport 済**（[References](#references)） | Nav2 の外側に Governance / MRM を置く。**Route Server を Humble で候補に上げる** |
| **サーベイ**（Yurtsever 2020 / Badue 2021）[L] | perception（localization・mapping・移動物体・**信号検出**）/ decision-making（route・path・**behavior selection**・motion・control）。HMI を中核に数える（Yurtsever） | 安全監視・実行許可・MRM を独立に図示しない | 「正準図」は運用面を欠く＝運用スタックを参照する理由 |
| **NeBula**（JPL CoSTAR・2021）[L] | Perception / Planning / World Belief / Communications / **Operations**（人間監督を一級） | HeRO: データ品質と健全性の confidence test → 不調モジュール再初期化 → 品質重み付き多重化。STEP: 軌道全滅で emergency stopping sequence | **自己申告品質の多重化**・全軌道不成立を停止トリガに |
| **CERBERUS**（ETH RSL・Science Robotics 2022）[L] | CompSLAM / 2.5D elevation + CNN traversability / GBPlanner2（local / global）/ 状態機械 / path tracker | **health check を通過した推定値のみ下流へ**。traversability safety module は固定距離バック + 30 cm look-ahead。Human Supervisor 1 名・supervised-autonomous | **悪い pose を publish 側で止める**・監督 1 名 |
| **CMU Explorer**（Cao・ICRA 2022）[L] | terrain_analysis（2.5D）/ local_planner / waypoint following / TARE・FAR | 明示的 health monitor 無し。**mission 層は waypoint + 速度 + 走行境界しか出さない**（双方向 1 topic ずつ）。`considerDrop`（地面推定より下の点も同等コスト）+ `noDataObstacle`（点数不足セル = 最大コスト） | **mission の最小契約**・**未観測 = 通行不可**・落差の絶対値コスト化 |
| **CSIRO / VT&R3 / Tsukuba Challenge** [L] | CSIRO: OHM heightmap fatal cost + ハード e-stop。VT&R3: teach-and-repeat pose graph。**Tsukuba**: 横断歩道・信号交差点で**自律停止 → 操作者が安全確認 → 再開**、全機体に非常停止ボタン必須 | — | 横断は自律停止をデフォルト・遠隔者 confirm で解除（[05 §4](05-safety-envelope-and-intervention.md) と一致） |
| 公開アーキテクチャ無し [L] | Waymo / Tesla / Starship / Serve / Kiwibot / Cartken（査読付きシステム論文なし）・JackRabbot（データセット論文） | — | 引用しない |

## 2. 共通スケルトンと v2 分解（コンポーネント × L 軸 × 現行 01〜09 からの移設）

| v2 | 責務（1 行） | L 軸 | 現行 01〜09 から移るもの | 参照 |
|---|---|---|---|---|
| **00_Platform_Contract**（v2.1 追加・2026-09-14） | 車体寸法・車輪半径・実効輪距・旋回校正・frame 名・停止時間予算・運用包絡・依存版の**単一正本**。走行指令を出さない | 前提（L 軸外・凍結契約と同格） | `warehouse_description`（URDF / frames / footprint）＋ `config/warehouse.base.yaml` の屋外 overlay ＋ [00](00-mission-and-scope.md) の運用条件 | 外部レビュー v3 §2（[09 §2-l](09-external-review-v3-response.md)） |
| **01_Sensing** | 取得・時刻付与・変換（エンコーダ→Odometry・IMU 補正・カメラ内深度生成）まで。**行動判断をしない**（[09 §2-k](09-external-review-v3-response.md)・2026-09-14 語修正） | L4/L1 境界 | 01 そのまま（RTK 受信機・NTRIP・OAK-D ×2・T-mini・IMU・エンコーダ） | 全参照 |
| **02_Map_and_Route ★新設** | 地図・**datum**・teach 経路（node/edge）・**keepout（許可帯）**・横断点レジストリ（`crossing_id`・ROI）・速度属性の**正本** | L3 | 02 の datum・04 の `/map` 再定義・09 の route_store・04 の横断点登録（`OQ-OD47`） | Autoware Map / Apollo map+routing / Bertha 地図属性 / Nav2 Route Server |
| **03_Localization**（＋TF 配信責任契約） | 地図相対姿勢の推定・フレーム単一配信 | 自律走行（安全層外） | 02 + 03（TF は成果物であって段ではない → 03 の配下契約へ） | Autoware / Bertha / Talos positioning |
| **04_Perception**（信号・歩行者を含む） | publish-only の認識。行動を決めない | L4 | 04 + 08 の `traffic_light_classifier` / `pedestrian_detector`。地形（terrain）検出をここへ | 全参照・Badue（信号検出は perception） |
| **05_Mission_and_Route** | 次の waypoint + 速度上限 + 走行境界だけを出す。route event（横断・狭路・帰還）を Governance へ | L3 | 05 の「waypoint 再生」・09 の route_store の実行側・`fromLL` compile | CMU（最小契約）・Nav2 Route Server（Humble backport）・VT&R3 |
| **06_Navigation** | Nav2: BT / planner / controller / costmap（keepout filter は global・local 両方）。**屋外初期プロファイルでは recovery 無効** | L1 Navigation | 05（Nav2 部分） | Nav2 |
| **07_Safety_Layer** | 幾何停止（collision_monitor: scan + cliff_scan。**Humble は node-level `source_timeout` のみ・途絶は fail-open**＝[09 §1 #1](09-external-review-v3-response.md)）＋ 手動・遠隔経路用の第 2 CM（`OQ-OD88`）。**地図上の絶対位置に非依存**（センサ TF・時刻には依存）・凍結 | L1 Safety | 06 の `safety_chain/`（collision_monitor） | Nav2 Collision Monitor（独立 safety layer）・Bertha |
| **08_Command_Gate** | **全コマンド源の単一合流点**（twist_mux: emergency prio100 > remote/teleop > nav2 prio10）・heartbeat | L1 | 06 の twist_mux | Autoware `vehicle_cmd_gate` / Apollo Guardian の位置 |
| **09_Vehicle_Interface** | クランプ（k）・停止上乗せ・W-1/W-2・STM32 FW・**estop_relay（法定）**・W-3 | L0' / L0 | 06 の `m1_driver` / `stm32_firmware` / `estop_relay` | Talos ADU（状態機械 + watchdog をハードに） |
| **10_Governance** | 「いま何をしてよいか」の一級状態: **operation mode（Stop / Auto / Remote / Manual）**・Policy Gate・**横断ゲート**・許可窓 | L2 | 08 の `crossing_gate`・09 の許可状態・既存 `warehouse_mcp_server` の Policy Gate・[mode-m1/05](../mode-m1/05-operation-state-and-stop-authority.md) の運転モード | Autoware operation mode / Apollo |
| **11_Remote_and_HMI** | 遠隔リンク（`operator_link_node`）・PC 卓・heartbeat・映像・teleop 再生・**RTI（介入要求）** | L4 / L2 | 09 から route_store と許可状態を除いたもの | Autoware Remote mode + RTI / Junior Wireless E-Stop / NeBula Operations |
| **12_Failsafe_MRM** | 監視の結果から **MRM 段を選び実行**（Emergency Guardian = MRM-B の作用点）・ラッチ・再開規則 | L2 → L1 | 06 の `emergency_guardian` + 新規停止理由 4 種 | Autoware `mrm_handler` / Apollo Guardian |
| **X1_Observability**（横断） | Langfuse / audit / rosbag2 / 走行記録 | 観測面 | 07 そのまま | Junior global services |
| **X2_Diagnostics ★新設**（横断） | 機能単位の診断 DAG（センサ鮮度・リンク・GNSS 品質・地形・計算 tick jitter）。**検知だけ**を担い 12 へ渡す | 観測面 → L2 | 06 / 09 に散っていた `link_loss_watchdog`・`gnss_quality_gate`・`geofence`・（新）`terrain_monitor`・（新）compute stall | Apollo Monitor / Autoware diagnostics graph / Junior health monitor |

**変更の骨子は (a)(b)(c) の 3 つだけ**。02〜07 の設計（RTK・cliff_scan・信号契約・停止契約・車輪）はそのまま v2 の箱に入る。

## 3. v2 で新設・再定義する 4 箱の中身

### 3-1. 02_Map_and_Route（地図・経路の正本を一級に）

| 要素 | 内容 | 出所 |
|---|---|---|
| datum | `[lat, lon, yaw]` を **route と `navsat_transform` の単一ソース**にする。`datum` は `wait_for_datum: true` のときだけ宣言される（[03 §2-3](03-localization-gnss-and-ekf.md)） | robot_localization humble-devel [D] |
| route graph | node（RTK 座標 + yaw）/ edge（速度上限・縁石リップ有無・operator gate 必須・横断 `crossing_id`）。**Nav2 Route Server（`nav2_route` 1.1.20・humble backport）が候補**: 経路網 + route operations（区間イベント）を標準で持つ。自前 YAML（[03 §3-3](03-localization-gnss-and-ekf.md)）は fallback | index.ros.org / humble `package.xml` [D] |
| keepout（許可帯） | 歩道ポリゴンを**マスク**にし `KeepoutFilter` を global と local の**両方**へ（Nav2 公式の best practice）。**Inflation Layer は keepout に自動適用されない**（filters は plugins と分離）→ 膨張はマスク作成側で持つ | Nav2 docs keepout tutorial [D] |
| 横断点レジストリ | `crossing_id`・進入方位・灯器 ROI・想定距離（[04 §4](04-perception-sidewalk-and-signals.md)）。Bertha 型「地図に交通規則属性」 | Ziegler 2014 [L] |
| 解像度分割 | 全域 0.05 m（100 m 角で 400 万セル）・近傍（local 6〜10 m 角）も **0.05 級**（[03 §3-4](03-localization-gnss-and-ekf.md) #4 と統一・2026-09-14 訂正）。屋内の 0.01 m を全域に使わない。**セル解像度 ≠ 測位精度** | [03 §3-4](03-localization-gnss-and-ekf.md) #4 |

### 3-2. 08_Command_Gate（司令ゲート）

- 現行 twist_mux（`emergency` prio100 > `nav2` prio10）を **Autoware `vehicle_cmd_gate` の「選択」機能のみに相当**と位置づける（モード・操作権・指令期限・解除条件・最終クランプは 09 / 10 / 12 が担い、gate 全体 = 08 + 09 + 10 + 12＝[09 §2-c](09-external-review-v3-response.md)・2026-09-14 訂正）。入力源 = ① Nav2（auto）② teleop / 遠隔再生（external）③ Guardian（emergency）。**新しい速度源を足さない**（[mode-m1/05](../mode-m1/05-operation-state-and-stop-authority.md) の規律）。
- heartbeat の途絶は Autoware `external_emergency_stop_heartbeat_timeout`・Apollo Guardian の 2.5 s・Talos ADU watchdog と同型（[02 §3-3](02-architecture-split-orin-pc-cloud.md)）。

### 3-3. 10_Governance（operation mode の一級化）

- 既存の「運転モード（AUTO / MANUAL）」（[GLOSSARY §11](../GLOSSARY.md)・[mode-m1/05 §2](../mode-m1/05-operation-state-and-stop-authority.md)）を **additive に拡張**する提案: `STOP`（許可なし）/ `AUTO`（Nav2）/ `REMOTE`（遠隔 teleop）/ `MANUAL`（現場ゲームパッド）。Autoware の Stop / Autonomous / Local / Remote と 1:1。**遷移中はモードを変えた操作者が安全を担保**（Autoware `is_in_transition` の規律）。
- 横断ゲート（[05 §4](05-safety-envelope-and-intervention.md)）と Policy Gate（restrict-only・[ADR-0004](../adr/0004-l2-restrict-only-policy-profile.md)）はここに同居し、**知覚（04）とは箱を分ける**。

### 3-4. 12_Failsafe_MRM（最小リスク挙動の段分け）

| 段 | トリガ例 | 挙動 | 実装点 | 解除 | Autoware 対応 |
|---|---|---|---|---|---|
| **MRM-0** | 混雑・float 縮退・歩行者接近 | 速度帯を下げる（**安全機構ではない**＝[ADR-0012](../adr/0012-speed-band-no-l2-best-effort.md)） | 06（`speed_limit`） | 自動 | — |
| **MRM-A（快適停止）** | 測位品質低下・知覚劣化・軽度診断 NG・全軌道不成立 | **減速停止・経路保持**（再開可能） | 12 → 08（Nav2 の pause / BT halt ＋ 走行許可の撤回。**帯 0 は使わない**＝`speed_limit 0.0` は「制限なし」[mode-m1/04:71](../mode-m1/04-runtime-speed-limiter.md:71)・2026-09-14 訂正） | 自動（条件回復 + 許可窓） | COMFORTABLE_STOP |
| **MRM-B（即時停止）** | 衝突リスク・リンク断・ジオフェンス逸脱・重度診断 NG | **prio100 ゼロ Twist・ラッチ** | 12（Emergency Guardian）→ 08 | **遠隔操作者の明示解除 + 再アーム** | EMERGENCY_STOP |
| **E-STOP（法定）** | 物理押しボタン | 原動機直接停止（モータレグ遮断） | 09（ハード） | **人手の解除操作のみ** | — |
| （不採用） | — | PULL_OVER | — | — | 歩道幅・固定経路では「寄せる先」が定義できない |

- 遷移の「一方向」は**異常が残る間の深刻度低下を禁止する**意味に限定する（A→PAUSED→READY・ACTIVE→B・任意→E-STOP・B→READY（原因解除＋明示 reset）は必要＝[09 §2-e](09-external-review-v3-response.md)・2026-09-14 訂正）。MRC（最小リスク状態）= **停止＋保持が成立した状態**（車輪速度フィードバックで確認・ゼロ指令送信の事実ではない）。表示灯・遠隔者への通知は**別フラグ**（リンク断時はローカル保留・回復後送信）で MRC の成立条件に含めない。
- 停止理由の契約は ISO 3691-4 の語彙で **operational stop（自動再開可）/ protective stop（人手復帰）** に型分けする案（`stop_request` を `(category, severity, resumable)` の 3 つ組へ additive 拡張＝`OQ-OD82`）。現行 `pose_stale` / `blocked_timeout` = operational、`near_collision` / `battery_critical` = protective。
- **RTI（介入要求）**: 自律継続不能時に遠隔操作者へ手動化を要求する一級イベント（11 へ）。

## 4. 外部レビュー（2026-09-12）の照合結果と docs への反映

| # | レビューの主張 | 判定 | 一次情報 | 反映 |
|---|---|---|---|---|
| 1 | Local の Static Layer は rolling でも重ねられる。「rolling なので無意味」は誤り | **誤った理由**（結論「採らない」は維持） | Nav2 humble `static_layer.cpp`: rolling 時は `lookupTransform(map_frame_, global_frame_)` でセル毎に変換投影・`updateBounds` の早期 return は非 rolling 限定 [D] | [doc23 §2 表](../architecture/23-perception-and-localization.md:73) の理由を「Static の座は `/map` 再定義が継ぐ・CURRENT 非保持」へ**同一行で訂正**＋ doc23 末尾追補 |
| 2 | Keepout は global / local 両方。Inflation は自動適用されない | **正** | Nav2 keepout tutorial「global と local を同時に有効化」「filters は layer plugins と分離（inflation との干渉回避）」[D] | [04 §6](04-perception-sidewalk-and-signals.md) に keepout_filter 行を追加・§3-1 |
| 3 | Isaac ROS 4.6（2026-08-18）で Orin 対応。「RGB-D は Thor 専用」は更新要。現行は JP7.2 / Jazzy | **条件付き**（事実は正・結論は不変） | Release Notes: 4.6.0 = 2026-08-18・Jetson Orin / JetPack 7.2 / Ubuntu 24.04 / Jazzy。Orin **Nano** の記述なし。Humble 系は 3.2 ライン（最終 U15 2025-12-10・JetPack 6.x）[D] | [doc23 B-1](../architecture/23-perception-and-localization.md:440) の blocker を「ハード」から **ADR-0008 の Humble pin** へ訂正（末尾追補）→ **2026-09-14 再訂正: Isaac ROS 3.2 は Humble + JetPack 6.1 / 6.2 + Orin が公式対象＝Humble pin は blocker ではない**。真の理由 = HP60C ステレオ IR 無し（ハード）＋ OAK-D 等を足す場合のカメラ同期要件・資源・検証負荷（[09 §1 #15](09-external-review-v3-response.md)・[doc23:804](../architecture/23-perception-and-localization.md:804) 同一行再訂正）。[04 §6](04-perception-sidewalk-and-signals.md) の「4.6 系の資料を誤参照しない」は生存 |
| 4 | Nav2 Route Server は Jazzy→Kilted 導入で Humble 標準ではない | **誤** | `nav2_route` 1.1.20 が humble ブランチに存在（backport #5359・2025-07-21）・index.ros.org で humble released [D]（執筆者が再確認）・rosdistro humble `distribution.yaml` の navigation2 `1.1.20-1` の packages に `nav2_route` あり＝**バイナリ配布済** [D 2026-09-14]（機体導入・依存互換は未検証＝`OQ-OD3D/81` のまま） | [03 §3-2](03-localization-gnss-and-ekf.md) に案 D（Route Server）を追加・`OQ-OD3D`・§3-1 |
| 5 | `differential` は位置と姿勢の両方を差分化。N−1 は目安で共分散調整も選択肢 | **正** | robot_localization docs: 「N−1 を differential に、**または共分散を十分大きく**」「navsat_transform 経由の GPS は `_differential: false`」[D] | [doc23 B-4](../architecture/23-perception-and-localization.md:478) の「凍結」を rule-of-thumb へ緩和・[03 §2-2](03-localization-gnss-and-ekf.md) に「GNSS は differential にしない」を根拠追加（`OQ-OD35` はほぼ決着） |
| 6 | datum の固定・アンテナ取付位置の補正が必要 | **条件付き**（既に我々の立場） | `navsat_transform.cpp`: `datum` は `wait_for_datum: true` のときだけ宣言・レバーアームは NavSatFix `frame_id` の TF で自動補正 [D] | [03 §2-3](03-localization-gnss-and-ekf.md) に 2 点追記（`gnss_link` = NavSatFix `frame_id` 一致必須・`datum` 単独指定は無効） |
| 7 | elevation_mapping_cupy は ROS 世代・移植状態の確認要 | **正**（ROS 2 は未マージ） | main は ROS 1（catkin）。ROS 2 は未マージブランチのみ・rosdistro 未リリース [D] | [04 §3](04-perception-sidewalk-and-signals.md) に 1 行追加。cliff detector 自作が本命のまま |
| 8 | nvblox の ESDF スライスだけでは下り段差・傾斜を判定できない | **正** | nvblox `esdf_integrator.h`: スライスは「高さ帯内の**障害物**までの距離」のみ。cliff / slope 機能なし [D] | [04 §6](04-perception-sidewalk-and-signals.md) Nvblox 行に根拠追加（Phase 2 に上がっても cliff_scan は残る） |
| 9 | 屋外初期版では Collision Monitor を迂回する recovery（後退・旋回）を使わない | **正**（手段も確認） | Humble `behavior_plugins` は param 配列＝空にできる・recovery 無しの BT が公式同梱 [D] | [05 §6](05-safety-envelope-and-intervention.md) `OQ-OD58` を三択化し **(c) recovery 無効化を屋外初期プロファイルの推奨候補**に |
| 10 | Collision Monitor は PointCloud2 も取れる | **正**（既に「取れるが入れない」と明記） | Humble sources = LaserScan / PointCloud2 / Range・action = stop / slowdown / approach（**`limit` は Humble に無い**）[D] | [04 §6](04-perception-sidewalk-and-signals.md) に `limit` 非対応を注記。P1 は設計上の選択 |
| 11 | Guardian がゼロ速度を送れても Jetson が止まれば送れない → MCU watchdog は別に必要 | **正**（既に我々の立場） | [mode-m1/02:64,67](../mode-m1/02-m1-driver-and-watchdog.md:64) W-3・[ADR-0013](../adr/0013-stm32-command-stream-watchdog.md) [D] | [05 §6](05-safety-envelope-and-intervention.md) に「速度 4 倍で W-4 の代替余地が小さい＝ゲート (d) の優先度が上がる」を追記 |

レビューの目標設定（「事前に確認した舗装路の固定ルートを、許可された幅の中で低速走行し、判断できない状況では停止する」・乾燥平坦・監視下・上限 0.3 m/s）は §5 の初期プロファイルとして採用する。レビューの機能名（`route_manager` / `localization_health_monitor` / `terrain_monitor` / `operation_manager`）は v2 の 05 / X2 / X2 / 10 に対応し、用語は本 repo の正準（[GLOSSARY](../GLOSSARY.md)）を使う。

## 5. 屋外初期プロファイル（Phase 0 outdoor・契約 0.3 m/s のまま）

| 項目 | 初期値 | 根拠 |
|---|---|---|
| 走行環境 | 乾燥・平坦な舗装路・私有地（または随伴通行）・監視者常時 | [01 §5](01-legal-envelope-japan.md) / 外部レビュー |
| 速度 | **契約 `MAX_LINEAR_VELOCITY = 0.3 m/s` のまま**（車輪換装前でも実施可） | 契約変更なし＝contract PR 不要で現地データが取れる |
| 経路 | teach-and-repeat 固定ルート・keepout マスク両 costmap・許可帯の外は lethal | §3-1 |
| recovery | **無効**（**recovery を持たない BT を先に指定し、その上で** `behavior_plugins: []`。既定 BT のまま plugins を空にすると Spin / Wait / BackUp の server 不在で BT 構築時に throw＝[09 §1 #20](09-external-review-v3-response.md)） | §4 #9 |
| 未観測 | **通行不可**（`noDataObstacle` 型・`track_unknown_space`） | CMU / STEP [L] |
| 停止 | センサ鮮度監視（X2: scan・cliff_scan・GNSS 品質・heartbeat）→ **走行許可の失効**（09。Humble CM は途絶で止まらない＝[09 §2-b](09-external-review-v3-response.md)）・MRM-A/B・E-STOP | §3-4 |
| 再開 | 品質回復 + 明示再開条件（再アーム） | [05 §3](05-safety-envelope-and-intervention.md) |
| 記録 | センサ・推定・指令・実速度・停止理由を run record で対応づけ | X1 |
| 解像度 | 全域 0.05 m・近傍も 0.05 級（03 と統一・2026-09-14） | §3-1 |

## 6. 実装順序と「確認実装」の範囲

外部レビューの順序 1〜6 を採用し、本 repo のゲートへ紐付ける:

| 順 | 作る・確認するもの | 次へ進む条件 | 本 repo の対応 |
|---|---|---|---|
| 0 | **車体契約（00_Platform_Contract）と既存コードの照合・Humble CM 意味論の再裁定・09 の下位停止（W-3 / 独立監視回路 / 期限付き走行許可）**（2026-09-14 追加） | FW 停止挙動・CM 版・mux 配線・車輪 API・TF 担当が確定し、ホスト停止・シリアル断で止まる | [09 §4](09-external-review-v3-response.md)・[ADR-0013](../adr/0013-stm32-command-stream-watchdog.md)・`OQ-OD89/95/96` |
| 1 | 屋外の運用条件・ルート幅・段差・傾斜・停止方法 | 走行・停止できる範囲が明確 | [00 §4](00-mission-and-scope.md) 裁定 1・[07 §10](07-drivetrain-and-wheel-sizing.md) G-W1 |
| 2 | 現地でセンサと車体状態を記録 | 測位が弱い区間・路面が見えない区間を特定 | [03 §6](03-localization-gnss-and-ekf.md) L-4・[04 §7](04-perception-sidewalk-and-signals.md) P-5 |
| 3 | 地図・許可帯・経由点・測位プロファイル | ルート全体で位置と向きの誤差を許容 | §3-1・[03](03-localization-gnss-and-ekf.md) |
| 4 | 地形監視・品質監視・運転状態管理 | 危険と判断不能を検出して停止 | X2・12・10 |
| 5 | 既存 Nav2 による低速走行 | 閉塞・センサ欠落・通信断・再開を含めて確認 | §5 プロファイル・[05 §7](05-safety-envelope-and-intervention.md) R-26 |
| 6 | nvblox・Route Server・新基盤への移行 | 計測で分かった不足を改善し既存の停止契約を維持 | Phase 2 |

**今すぐ offline で実装できる「確認実装」**（実機・現地不要・fake seam + R-26 unit）:

1. 契約 additive（contract PR）: `gnss_link` を `FROZEN_LINK_NAMES` へ・`GnssQuality` 型・`stop_request` の `reason_code / category / resumable`・operation mode の追加状態。
2. 純ロジック producer + R-26 unit: リンク断 watchdog・GNSS 品質ゲート・ジオフェンス・terrain monitor（`considerDrop` / `noDataObstacle` 型の判定関数）・compute tick jitter 監視・MRM 段選択器。
3. route graph ファイル + compile ツール（`fromLL` を teach 時に一度）+ keepout マスク生成 + 横断点レジストリの検証（YAML → schema）。
4. `operator_link_node` 骨格（WS 制御/映像 2 接続＝[09 §2-d](09-external-review-v3-response.md)・運ぶ意味の閉集合・AST pin）と PC 卓の最小 UI。
5. 屋外初期プロファイル（Nav2 params overlay・recovery 無効・keepout filter・local 8〜10 m）と sim での回帰。

**現地データ取得後に決めるもの**: RTK 受信機クラス（`OQ-OD37`）・屋外カメラ（`OQ-OD43`）・3D LiDAR の要否・車輪径の最終値（[07](07-drivetrain-and-wheel-sizing.md)）。

## 7. OPEN QUESTIONS（接頭辞 `OQ-OD8*`）

- `OQ-OD80` v2 分解（12 + 2）を採用するか。採用なら [outdoor-architecture-tree.html](outdoor-architecture-tree.html) を正本の索引にし、02〜07 の見出しの「箱名」を v2 に揃える（ユーザー裁定）。
- `OQ-OD81` Nav2 Route Server（`nav2_route` 1.1.20 humble）を 02/05 の経路正本に採るか、自前 YAML（[03 §3-3](03-localization-gnss-and-ekf.md)）で始めるか（`OQ-OD3D` と同一）。
- `OQ-OD82` `stop_request` を `(category, severity, resumable)` の 3 つ組へ additive 拡張するか（ISO 3691-4 語彙・operational / protective）。
- `OQ-OD83` MRM-A（快適停止・経路保持・自動再開）の実装経路: Nav2 pause / BT halt ＋ 走行許可撤回（**帯 0 は不可**＝`speed_limit 0.0` は「制限なし」・[09 §1 #14](09-external-review-v3-response.md)）。
- `OQ-OD84` 運転モードを `STOP / AUTO / REMOTE / MANUAL` へ additive 拡張するか（[GLOSSARY §11 運転モード](../GLOSSARY.md) との整合・[mode-m1/05 §2](../mode-m1/05-operation-state-and-stop-authority.md) の「6 状態機械は採用しない」との両立）。
- `OQ-OD85` X2 Diagnostics を DAG（Autoware diagnostics graph 型）にするか、既存の Guardian 監視プロファイル（[23 追補 A-5](../architecture/23-perception-and-localization.md:332)）の拡張で足りるか。
- `OQ-OD86` Talos ADU 型（状態機械 + watchdog を最下層ハードに置く）を W-3（[ADR-0013](../adr/0013-stm32-command-stream-watchdog.md)）と estop_relay の統合像として持つか。
- `OQ-OD87` 「計算ストール」の自己監視（Guardian 50 ms tick の jitter 超過で MRM-B）を R-26 unit 込みで足すか。

## References

docs 内（file:line は執筆時に実 Read）:

- [outdoor-architecture-tree.html](outdoor-architecture-tree.html)（v2 ツリー・旧 01〜09 対応表）/ [outdoor-localization-perception-flow.html](outdoor-localization-perception-flow.html)
- [00](00-mission-and-scope.md) / [01](01-legal-envelope-japan.md) / [02](02-architecture-split-orin-pc-cloud.md) / [03](03-localization-gnss-and-ekf.md) / [04](04-perception-sidewalk-and-signals.md) / [05](05-safety-envelope-and-intervention.md) / [06](06-hardware-delta-and-base-selection.md) / [07](07-drivetrain-and-wheel-sizing.md)
- [architecture/23-perception-and-localization.md](../architecture/23-perception-and-localization.md)（[:73](../architecture/23-perception-and-localization.md:73) Static Layer 行 / [:440](../architecture/23-perception-and-localization.md:440) B-1 / [:478](../architecture/23-perception-and-localization.md:478) B-4 / [:332](../architecture/23-perception-and-localization.md:332) A-5・末尾追補 2026-09-12②）
- [mode-m1/02-m1-driver-and-watchdog.md:64,67](../mode-m1/02-m1-driver-and-watchdog.md:64) / [mode-m1/05](../mode-m1/05-operation-state-and-stop-authority.md) / [ADR-0004](../adr/0004-l2-restrict-only-policy-profile.md) / [ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md) / [ADR-0012](../adr/0012-speed-band-no-l2-best-effort.md) / [ADR-0013](../adr/0013-stm32-command-stream-watchdog.md) / [GLOSSARY §3・§11・§12](../GLOSSARY.md)

一次情報（参照日 2026-09-12・[L] は調査レーン報告・執筆者未再読）:

- Autoware Documentation（architecture v1 / v2・AD API operation_mode / fail-safe / routing / heartbeat）<https://github.com/autowarefoundation/autoware-documentation>・autoware_universe `system/autoware_mrm_handler` / `autoware_command_mode_decider` / `control/autoware_vehicle_cmd_gate` <https://github.com/autowarefoundation/autoware_universe>・Kato et al. ICCPS 2018 doi:10.1109/ICCPS.2018.00035 [L]
- Apollo `modules/guardian/README.md` / `modules/monitor/README.md` <https://github.com/ApolloAuto/apollo> [L]
- Urmson et al. JFR 2008 doi:10.1002/rob.20255（Boss）/ Montemerlo et al. JFR 2008 <https://robots.stanford.edu/papers/junior08.pdf>（Junior）/ Leonard et al. JFR 2008 <https://april.eecs.umich.edu/pdfs/mitduc2008.pdf>（Talos）/ Ziegler et al. IEEE ITS Mag. 2014 doi:10.1109/MITS.2014.2306552（Bertha）[L]
- Macenski et al. IROS 2020 arXiv:2003.00368 / Nav2 Docs（Navigation Servers・Humble 移行ガイド Collision Monitor）[L] / **`nav2_route` humble 1.1.20** <https://index.ros.org/p/nav2_route/>・<https://github.com/ros-navigation/navigation2/blob/humble/nav2_route/package.xml> [D]
- Nav2 humble `static_layer.cpp` <https://github.com/ros-navigation/navigation2/blob/humble/nav2_costmap_2d/plugins/static_layer.cpp> / keepout tutorial <https://github.com/ros-navigation/docs.nav2.org/blob/jazzy/docs/tutorials/general_tutorials/navigation2_with_keepout_filter/navigation2_with_keepout_filter.md> / `nav2_collision_monitor` README・`types.hpp`（humble）/ `nav2_bringup/params/nav2_params.yaml`（humble）[D]
- robot_localization humble-devel `doc/state_estimation_nodes.rst` / `doc/configuring_robot_localization.rst` / `src/navsat_transform.cpp` <https://github.com/cra-ros-pkg/robot_localization/tree/humble-devel> [D]
- Isaac ROS Release Notes <https://nvidia-isaac-ros.github.io/releases/index.html>（4.6.0 = 2026-08-18・Orin / JetPack 7.2 / Jazzy）/ nvblox `esdf_integrator.h` <https://github.com/nvidia-isaac/nvblox> [D]
- elevation_mapping_cupy <https://github.com/leggedrobotics/elevation_mapping_cupy>（ROS 2 未マージ）[D] / Miki et al. IROS 2022 arXiv:2204.12876 [L]
- Agha et al. NeBula arXiv:2103.11470 / Fan et al. STEP arXiv:2103.02828 / Tranzatto et al. Science Robotics 2022 doi:10.1126/scirobotics.abp9742 / Cao et al. ICRA 2022 arXiv:2110.14573（`terrainAnalysis.cpp` の `considerDrop` / `noDataObstacle`）/ Hudson et al. CSIRO arXiv:2104.09053 / VT&R3 <https://github.com/utiasASRL/vtr3> / Tsukuba Challenge IEEE ROBIO 2008・SII 2011 [L]
- Yurtsever et al. IEEE Access 2020 doi:10.1109/ACCESS.2020.2983149 / Badue et al. ESWA 2021 doi:10.1016/j.eswa.2020.113816 [L]
- ISO 3691-4:2023（operational / protective stop）/ ISO 23793-1:2024（MRM）/ ISO 13482:2014 [L]

## 【2026-09-14 追補】外部レビュー v3 の反映（v2.1）

正本 = [09](09-external-review-v3-response.md)（4 レーン照合・27 主張）。本 doc への同一行反映: §2（`00_Platform_Contract` 行追加・01 の「行動判断をしない」・07 の Humble fail-open と「地図上の絶対位置に非依存」）・§3-1（解像度を 03 と統一）・§3-2（twist_mux = 選択機能のみ）・§3-4（MRM-A の「帯 0」撤回・遷移「一方向」の意味限定・MRC 定義）・§4 #3（cuVSLAM 再訂正: Isaac ROS 3.2 は Humble 公式）・#4（`nav2_route` バイナリ配布 [D]）・§5（recovery は BT 先・停止行）・§6（順序 0 = 車体契約照合と 09 の下位停止・`operator_link_node` は 2 接続）・§7 `OQ-OD83`。**12 区分は維持**（レビュー判定と一致）。新規 OQ = `OQ-OD88`〜`97`（[09 §7](09-external-review-v3-response.md)）。
