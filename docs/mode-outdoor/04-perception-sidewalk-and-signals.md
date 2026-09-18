# 04 — 知覚: 歩道走行可能領域・障害物・負障害物・歩行者用信号

作成日: 2026-09-12
Status: **記載済（設計値・一次情報つき・実装未）**。2026-09-12 のエージェントチーム（知覚レーン）の報告を統合し、repo の pin は執筆時に実 Read、外部一次情報は URL + 参照日（[D]=一次情報/実ファイル確認・[L]=調査レーン報告で URL あり（執筆者未再読）・[I]=推論）。

> 正本ルート: [mode-outdoor/README](README.md)。室内知覚の原則 P1（L1 に GPU 依存を持ち込まない＝[23:37](../architecture/23-perception-and-localization.md:37)）・P2（cmd_vel 経路は 1 バイトも変えない＝[23:41](../architecture/23-perception-and-localization.md:41)）は**屋外でも不変**。知覚は costmap のセル値と L4 producer の出力にのみ影響し、`cmd_vel` を publish しない。図解は [outdoor-localization-perception-flow.html](outdoor-localization-perception-flow.html) §1-2。
> レイヤ注記: costmap 層・cliff 検出・セグメンテーションは**自律走行（安全層外）**、信号分類・歩行者検出は **L4 知覚（publish-only・0 actuation）**、横断の許可は **L2**（[05 §4](05-safety-envelope-and-intervention.md)）。

## 0. 位置づけ

屋外で新規に要る知覚は 4 つ: ①歩道の走行可能領域（歩道から出ない）②障害物と**負障害物**（縁石落ち）③**歩行者用信号**の状態 ④歩行者（進路を譲る義務＝[01 §6](01-legal-envelope-japan.md)）。**固定経路（teach-and-repeat）という前提が効く**: 走行可能領域も横断点も**事前登録**でき、実行時の知覚は「登録済みのものの検証」に縮む。信号の**判定結果は L4 producer**、**横断の許可は L2 ゲート**が持つ。

## 1. センサ前提と候補

### 1-1. 手持ちセンサ（確定事実）

| センサ | 事実（参照日 2026-09-12） | 屋外での扱い |
|---|---|---|
| Nuwa-HP60C 深度カメラ | 公式ページは **structured light**・有効深度 4 m・FOV 73.8°。屋外・直射日光の記述なし [D] | **屋外の主センサにしない**。室内（[ADR-0007](../adr/0007-no-overhead-camera-gesture-via-onboard-nn.md)）の射程は不変 |
| YDLIDAR T-mini Plus（2D・12 m） | 公式ページに **60 kLux** の耐環境光 [D] | 反射安全（collision_monitor）と obstacle 層の入力として**継続**。縁石より低い物・張り出しは見えない。直射日光下は**要実測**（`OQ-OD40`） |
| ICM-20948 IMU / エンコーダ | [shared/02 §A](../shared/02-hardware-design.md) | 自己位置側（[03](03-localization-gnss-and-ekf.md)） |

### 1-2. 屋外向け候補の比較（Humble / Orin Nano 対応は一次情報で確認・[L]）

| センサ | 深度方式 | 直射日光 | 負障害物（縁石） | Orin 負荷 | Humble ドライバ | 概算 | 屋外実績 |
|---|---|---|---|---|---|---|---|
| **OAK-D Pro W** | アクティブステレオ（**カメラ側 VPU で深度計算**）+ IR ドット | ステレオ本体は◎。IR ドットは**日中ほぼ無効**（公式が夜間のみ有効と明言） | 150° 広角の下向き 1 台が効率的 | **最小**（深度 on-device＝Orin GPU 温存） | `ros-humble-depthai-ros` バイナリ有 | $529・IP66 | 産業筐体 |
| OAK-D S2 (W) | 同上（IR 無し版は純パッシブ） | 同上 | 同上 | 最小 | 同上 | $329 | 防水は要ケース |
| **ZED 2i** | パッシブステレオ + Neural depth（GPU） | ◎（CPL 偏光フィルタ内蔵オプション） | 下向き取付で可。無テクスチャ舗装は苦手 | **高**（ZED SDK が CUDA 常時＝S1 予算を直撃） | `zed-ros2-wrapper` Humble 公式（要 ZED SDK 5.x） | $519・IP66・IMU/気圧/磁気内蔵 | 公式に屋外・Orin Nano Super 対応 |
| RealSense D455 / D435 | IR アクティブステレオ | ステレオ部は可 | 可 | 中 | `realsense-ros` Humble 有。**JetPack 6 の起動トラブル報告多数**（IMU 不可等） | $400 級 | 防水なし |
| Livox Mid-360 | 3D LiDAR（非反復スキャン） | **◎**（100 klx 誤警報 <0.01 %） | **△**: 垂直 FOV −7°〜+52°＝下向き 7° しかない | 低（点群は CPU） | `livox_ros_driver2`（ソースビルド） | 要見積（$400〜1,350） | IP67・屋外 SLAM 実績 |

**推奨**（`OQ-OD43` で確定）:

- **(a) 信号用の前方 RGB = OAK-D Pro W（または S2 W）1 台**。深度を Orin GPU で作らないため、室内で S1 が測ろうとしている 8 GB の固定コスト（[23 §7](../architecture/23-perception-and-localization.md:208)）に手を付けずに済み、信号検出の TensorRT 予算が残る。ZED 2i は IMU/気圧/磁気内蔵と IP66 が魅力だが CUDA を常時握るため Phase 1 では採らない。
- **(b) 縁石 / 負障害物用の下向き深度 = 同じ OAK-D 系をもう 1 台、10〜20° 下向き**。ドライバが 1 種類で済む。
- **(c) 3D LiDAR（Mid-360）は Phase 1 で不要**。垂直下向き 7° は負障害物の主武器にならず、水平方向は T-mini Plus + カメラで足りる。Phase 2 の候補（夜間・逆光・雨の冗長化）として保持。

## 2. 歩道走行可能領域

| 案 | 中身 | Orin Nano 8 GB での現実性 | 判定 |
|---|---|---|---|
| A. セマンティックセグメンテーション | Cityscapes `sidewalk` クラス → TensorRT | SegFormer-B0 は 48 fps（デスクトップ GPU・TensorRT 無し）[L] → Orin Nano では 1/5〜1/10 が現実線 [I]。**Cityscapes 学習品は車載視点**で歩道上視点の精度が落ちる（Mapillary Vistas 学習が良いとの報告）[L] | **Phase 2** |
| B. 幾何的 地面平面 / 縁石検出 | 下向き深度 → RANSAC 地面 + 法線不連続 | 学習モデル 0。縁石 2 cm（[06 §3](06-hardware-delta-and-base-selection.md)）を距離 2 m で分離できるかが実測ゲート（`OQ-OD45`） | **Phase 1 主** |
| **C. RTK ジオフェンス polygon + 障害物層** | **固定経路なので走行可能領域は事前に地図で持てる** | 追加計算ゼロ。RTK 断時の縮退先が要る（[05 §3](05-safety-envelope-and-intervention.md)） | **Phase 1 主** |

**Phase 1 最小スタック = C + B**。走行可能領域は「学習で毎フレーム推定するもの」ではなく「事前登録 polygon（旧 Static Layer の座を継ぐ `/map` の再定義＝§6）」にし、実行時の知覚は **(i) RTK 逸脱の検出（ジオフェンス）(ii) 縁石落ちの検出 (iii) 障害物** に絞る。**Phase 2 = A を「ジオフェンスの検証器」として追加**（出力を単独権威にせず、polygon と不一致なら減速 / 停止＝fail-closed）。点字ブロック・排水溝・芝生は Phase 1 では polygon 側の属性（通行可だが `slow` 帯）として持ち、知覚で判定しない [I]。

## 3. 障害物・負障害物（縁石落ち・段差）

`nav2_costmap_2d` は**負障害物を表現する機構を持たない**（obstacle_layer は高さ帯にヒットした点を marking するだけで「地面が無い」を表現できない）[D]。

| 案 | 可否 | 評価 |
|---|---|---|
| STVL（`spatio_temporal_voxel_layer`） | Humble release 有（2.3.4）[L] | 「欠落した地面」の marking は素直に書けない（decay は誤検出対策）。**採らない** |
| 自作 cliff detector → 仮想 LaserScan | 可 | **本命**。下向き深度から「期待地面高より低い / 点が返らない」領域を検出し、その方位・距離に**仮想的な壁**を撃つ。判定は CMU `terrain_analysis` 型（地面推定より下の点も同等コスト = `considerDrop`・点数不足セルは最大コスト = `noDataObstacle`＝**未観測 = 通行不可**）[L] |
| elevation_mapping_cupy（ETH RSL） | main は ROS 1・ROS 2 は未マージブランチのみ・Humble バイナリ無し [D] | GPU 常駐で 6 kg 機・固定経路には過剰。**Phase 1 不採用**（[08 §4 #7](08-architecture-v2-reference-alignment.md)） |

**推奨: 既存 VirtualScan パターンの流用（`/bot1/cliff_scan`・additive 提案・未凍結）**。本プロジェクトは「実在しない障害物を `sensor_msgs/LaserScan` で注入し、costmap と collision_monitor の **dual-consumer** で共有する」契約を既に持つ:

- 契約: [doc12:547](../architecture/12-infrastructure-common.md:547)「virtual_scan は dual-consumer（移設ではない）・costmap から外さない」[D]
- costmap 側: [nav2_params.yaml:221](../../ws/src/warehouse_bringup/config/nav2_params.yaml:221) / [:230](../../ws/src/warehouse_bringup/config/nav2_params.yaml:230)（`marking: true` / `clearing: false`＝inf 光線が実障害物を消さない）/ [:274](../../ws/src/warehouse_bringup/config/nav2_params.yaml:274) [D]
- collision_monitor 側: [collision_monitor.yaml:27-28](../../ws/src/warehouse_bringup/config/collision_monitor.yaml:27) / [:76](../../ws/src/warehouse_bringup/config/collision_monitor.yaml:76) / [:81-86](../../ws/src/warehouse_bringup/config/collision_monitor.yaml:81)（条件付き publisher ゆえ `source_timeout` を 0.0＝沈黙は故障でない）[D]
- 生成側の形: [virtual_scan_logic.py:19](../../ws/src/warehouse_traffic/warehouse_traffic/virtual_scan_logic.py:19)（±15°）/ [:24](../../ws/src/warehouse_traffic/warehouse_traffic/virtual_scan_logic.py:24)（10 Hz）[D]

**P1 を壊さない理由**: collision_monitor の observation source は **LaserScan のまま**。GPU / 深度は cliff detector の**内部**に閉じ、L1 が見るのは契約化された LaserScan 型だけ。 **Humble 注記（2026-09-14）**: Humble の CM に per-source `source_timeout` は無く、途絶した source は点が消えるだけ（fail-open）。cliff_scan の途絶・無効深度（NaN / inf / 空 / 静止画）は **X2 の鮮度・品質監視 → 走行許可失効**で止める（[09 §2-b / 2-i](09-external-review-v3-response.md)）。depth 依存（カメラ・VPU・USB・時刻・外部パラメータ）は LaserScan 化しても消えない。ただし **`source_timeout` の扱いは virtual_scan 型（沈黙可）ではなく scan 型（途絶＝停止）に寄せる**べき: 崖は「近づいたときだけ現れる」のではなく「センサが死んだら見えなくなる」ものだから、沈黙を安全と解釈してはいけない [I]（`OQ-OD44`）。

## 4. 歩行者用信号の検出・状態分類

| 項目 | 内容 |
|---|---|
| 法定の意味（[01 §6](01-legal-envelope-japan.md)） | **青 = 進行可／青点滅 = 横断を始めてはならない（横断中は速やかに終えるか引き返す）／赤 = 横断不可** |
| 公開データセット [L] | **PTL Dataset / ImVisible（LYTNet）**: 上海の交差点 5,000 枚超・歩行者用灯器 5 クラス + 横断歩道中心線注釈。**Bosch STLD / LISA は車両用灯器**で流用不可 |
| 日本特化の先行研究 [L] | "Does your robot know when to cross the road?"（IEEE 2024）: 日本の街路で赤人形・**経過時間表示**・無表示を区別して「渡らない」判断に使う |
| 音響式 [L] | Audio-Visual Traffic Light State Detection（IROS 2024）: 音声特徴 + 赤/緑画素比の融合。日本の音響式信号は**事前登録交差点なら強い補助証拠**（`OQ-OD48`） |
| 日本語の公開データ | 検索範囲では**見つからず**＝自前収集前提（`OQ-OD41`） |
| 検出器 [L] | YOLO 系 nano + TensorRT。Orin Nano Super の公式ベンチは 640 入力 FP16 で数 ms 級。信号は小さいので入力解像度を上げ、実効 20〜40 fps [I]。**10 Hz で十分**（信号は 1 s 未満で変わらない） |

**「どの灯器が自分のものか」は固定横断点の事前登録で解く**: 交差点ごとに `crossing_id` を持ち、`{RTK 位置, 進入方位, 画像内 ROI（方位角 ± 幅・仰角 ± 幅）, 想定灯器高さ, 想定距離}` を事前登録（正本の置き場は `OQ-OD47`）。実行時は (i) RTK + 方位で ROI を画像に投影 → (ii) ROI 内の検出のみ採用 → (iii) 距離・見かけサイズの妥当性チェック。「複数灯器から選ぶ」問題が**登録済み 1 個の検証問題**に縮む＝固定経路の最大の恩恵。

**出力契約（L4 producer → L2 横断ゲート・fail-closed・additive 提案・未凍結）**

| 状態 | 条件 | L2 ゲートの扱い |
|---|---|---|
| `GREEN` | 直近 N フレーム（例 10 = 1 s）の**時間窓多数決が 8/10 以上** かつ ROI 整合 かつ 検出の `age = now − source_stamp` が `0 ≤ age < max_age`（例 0.5 s。max_age は用途ごとの時間予算から・消費側も使用時点で検査 = 追補 ③ #4）| 横断開始を許可（承認トークンと AND） |
| `GREEN_FLASHING` | 窓内で GREEN/OFF が交番（**明滅周期 0.5 s = 2 Hz**・警察庁「交通信号灯器仕様書」由来＝科警研 横関ら, 交通工学論文集 5(2) B_17-B_23, 2019 [D 2026-09-16]。**2026-09-16 訂正**: 旧「~1 Hz」は車両用閃光の値。判定は末尾追補 §5 の二段レート＝10 Hz 多数決だけに頼らない）、または明示クラス | **新規横断の開始は禁止**（横断中は継続＝[01 §6](01-legal-envelope-japan.md)） |
| `RED` | 多数決 RED | 禁止 |
| `UNKNOWN` | **上記いずれも成立しない全ての場合**（検出ゼロ・票割れ・stale・ROI 外・カメラ断・クラウド断） | **禁止（fail-closed）**。LLM / ER は `UNKNOWN` を `GREEN` に昇格できない |

「青点滅 → 青」「赤 → 青」の誤りをゼロにするため、**GREEN への遷移のみ閾値を非対称に厳しく**する（GREEN 判定は 8/10・GREEN 離脱は 1 フレームで即）[I]。

## 5. 歩行者（進路を譲る）

- 検出: 前方 OAK-D の RGB で人物検出（信号検出器と同じ TensorRT 経路・別クラス）。深度で距離。
- 挙動: 前方 X m 以内に歩行者 → `slow` 帯（best-effort）→ さらに近ければ **停止**（L1 collision_monitor の stop polygon が最終担保＝pose 非依存の床）。「進路を譲る」＝停止して待つ（法 14 条の 2）。退避は Phase 2。（⚠ 2026-09-16 書換候補 = 末尾追補 ② `OQ-OD4D`: 04 は位置・速度・不確かさ・最終観測時刻のみを出し、減速は帯セレクタの入力・MRM 判断と停止要求は 12・回避は 06・幾何反射停止は 07〔観測できる範囲に限る〕・駆動停止 / 禁止 / 保持は 09 が担う = 追補 ③ #6）
- L2 は関与しない（位置 goal の許可には影響しないため）。

## 6. costmap 統合（Humble・Phase 1 は P1/P2 と 8 GB を守る）

| 層 | Phase 1 | 根拠 |
|---|---|---|
| Obstacle Layer ← `/bot1/scan` | 維持 | T-mini Plus 60 kLux・既存契約 |
| Obstacle Layer ← `/bot1/cliff_scan`（新） | **追加**（`marking: true` / `clearing: false`） | §3。既存 virtual_scan と同じ書き方（[nav2_params.yaml:230](../../ws/src/warehouse_bringup/config/nav2_params.yaml:230)） |
| Obstacle Layer ← 前方深度 → `pointcloud_to_laserscan` | **追加**（Humble バイナリ有 [L]） | 3D を 2D に潰すだけ＝GPU 0。段差・張り出しは cliff 経路で別に拾う |
| `/map`（旧 Static Layer） | **再定義**: RTK 歩道ポリゴンをラスタ化した走行可能領域マップ（§2 案 C）。単独 publisher・decay しない性質はそのまま | global costmap の static_layer を外すか再定義するかは [03 §3-4](03-localization-gnss-and-ekf.md) #2 と同期 |
| Nvblox Layer | **Phase 2 へ延期** | Isaac ROS **3.2 Update 1 で JetPack 6.2 + Orin Nano Super（Humble）対応**・3.2 で「Nav2 costmap 統合修正」[L]。ただし屋外は pose 焼き付き（[23:79](../architecture/23-perception-and-localization.md:79)）+ 歩行者（室内で dynamic 層不採用とした前提が逆転）+ GPU 予算。**ESDF 2D スライスは「高さ帯内の障害物までの距離」だけを持ち、負障害物（段差下り）・傾斜を表現しない**（nvblox `esdf_integrator.h` [D]）＝Phase 2 に上がっても `cliff_scan` は残る。**4.6 系（Jazzy・JP7.2）の資料を誤参照しない**（`OQ-OD46`・[08 §4 #3,#8](08-architecture-v2-reference-alignment.md)） |
| **Keepout Filter（`filters`）** | **追加（global・local の両方）** | 歩道ポリゴンの許可帯をマスク化。Nav2 公式は「global と local を同時に有効化」を best practice とし、**filters は layer plugins と分離＝Inflation Layer は keepout に自動適用されない**（膨張はマスク作成側で持つ）[D]（[08 §3-1](08-architecture-v2-reference-alignment.md)）。正本は [08 §2 02_Map_and_Route](08-architecture-v2-reference-alignment.md) |
| Voxel Layer / STVL | 不採用（retreat plan） | [23:71](../architecture/23-perception-and-localization.md:71) の判断を踏襲 |
| collision_monitor observation | **`scan` + `cliff_scan` のみ** | **P1 不変 = LaserScan のみ**（[23:37](../architecture/23-perception-and-localization.md:37)）＝設計上の選択（Humble の collision_monitor 自体は LaserScan / PointCloud2 / Range を取れる）。**Humble の action は stop / slowdown / approach の 3 種で `limit` は無い**（Iron 以降）[D] → polygon 再設計で `limit` を前提にしない（[08 §4 #10](08-architecture-v2-reference-alignment.md)） |

メモリ / GPU の実数値は**未計測**。屋外 S1 として「idle → OAK-D ドライバ → +YOLO TensorRT → +cliff detector → +Nav2」の差分測定を室内 S1（[23 §7](../architecture/23-perception-and-localization.md:208)）と同じ手順で回す（[02 §4](02-architecture-split-orin-pc-cloud.md)）。

## 7. 受け入れ条件（設計値・実測未）

| # | 項目 | 目安 |
|---|---|---|
| P-1 | 信号分類の混同行列 | **「青点滅→青」「赤→青」= 0 件**（必須）。`UNKNOWN` 率は運用指標 |
| P-2 | 縁石 2 cm の検出距離 | 制動距離（[07 §6](07-drivetrain-and-wheel-sizing.md)・4.5 km/h で ≈ 0.8 m）+ 余裕 ≥ 1.5 m [I]（`OQ-OD45`） |
| P-3 | ジオフェンス逸脱の検出遅れ | RTK fix 時 < 0.3 m [I] |
| P-4 | 歩行者停止 | 前方 1.0 m 以内で停止（[05](05-safety-envelope-and-intervention.md) の polygon と整合） |
| P-5 | T-mini Plus 直射日光下の有効距離 | 公称 60 kLux の実測（`OQ-OD40`） |

## 8. 再利用 / 変更 / 新規 / 不採用（[perception-localization-flow.html](../architecture/perception-localization-flow.html) §1-2）

| ノード | 屋外判定 | 理由 |
|---|---|---|
| ascamera（HP60C） | **不採用（室内限定）** | structured light・屋外記述なし |
| nvblox TSDF/ESDF・Nvblox Layer | **延期（Phase 2）** | 対応はあるが pose 焼き付き・歩行者・GPU 予算 |
| Static Layer ← `/map` | **変更（再定義）** | 周壁は無い。RTK 歩道ポリゴンのラスタへ |
| Obstacle Layer ← scan | **流用** | 60 kLux 公称・要実測 |
| Obstacle Layer ← virtual_scan | **変更（用途差替）** | `/bot1/cliff_scan` に置換。**契約の形は 100 % 流用** |
| Inflation Layer | **変更（値のみ）** | 0.085 は 200 mm 隘路の live 実証値（#125）。歩道幅・150 mm 輪（2026-09-13 裁定＝[07](07-drivetrain-and-wheel-sizing.md)）で再調整 |
| Local costmap rolling 3 m | **変更（値のみ）** | 0.3 m/s で 10 秒先 → 1.25 m/s で 2.4 秒。8〜10 m・0.05 級へ（`OQ-OD4A`） |
| collision_monitor observation sources | **流用（+1 source）** | **P1 不変**。`scan` + `cliff_scan` |

## 9. OPEN QUESTIONS（接頭辞 `OQ-OD4*`）

- `OQ-OD40` T-mini Plus の直射日光下の実性能（60 kLux 公称の実測）。
- `OQ-OD41` 信号認識の学習データ（自前収集の可否・公開データセットの流用）。
- `OQ-OD42` 車両（右左折車）の検出を横断ゲートの条件に含めるか。
- `OQ-OD43` 屋外カメラの選定確定: OAK-D Pro W / S2 W（on-device 深度）vs ZED 2i（IMU 等内蔵・CUDA 常時）。S1 予算の前提が変わる。
- `OQ-OD44` cliff detector の `source_timeout` 契約: virtual_scan 型（沈黙可）か scan 型（途絶＝停止）か。 **→ 裁定済（2026-09-18）= [追補 ⑩ §1](04-perception-sidewalk-and-signals.md:1013)**: **scan 型**（per-source override を置かない）。ただし Humble では CM 内で停止にならず、途絶検出は CM の外（X2 / Guardian `scan_stale`）に残る。
- `OQ-OD45` 縁石 2 cm を何 m 手前で分離できるか（取付高さ・俯角・制動距離）。実測ゲート。
- `OQ-OD46` Nvblox Layer を屋外で採るか（Isaac ROS 3.2 系の資料基準・歩行者 = dynamic 層の要否）。
- `OQ-OD47` 事前登録横断点スキーマ（`crossing_id`・進入方位・灯器 ROI・想定距離）の正本の置き場（route 定義か地図か）。
- `OQ-OD48` 音響式信号のマイク入力を採るか。
- `OQ-OD49` 歩道領域セグメンテーションの学習ドメイン（Cityscapes か Mapillary Vistas か）。Phase 2。
- `OQ-OD4A` Local costmap の rolling 幅と resolution の再導出（1.25 m/s）。

## References

docs 内（file:line は執筆時に実 Read）:

- [architecture/23-perception-and-localization.md](../architecture/23-perception-and-localization.md)（[:37](../architecture/23-perception-and-localization.md:37) P1 / [:41](../architecture/23-perception-and-localization.md:41) P2 / [:71](../architecture/23-perception-and-localization.md:71) Voxel 不採用 / [:79](../architecture/23-perception-and-localization.md:79) 焼き付き / [:208](../architecture/23-perception-and-localization.md:208) S1）
- [architecture/12-infrastructure-common.md:547](../architecture/12-infrastructure-common.md:547)（dual-consumer）/ [nav2_params.yaml:221,230,274](../../ws/src/warehouse_bringup/config/nav2_params.yaml:230) / [collision_monitor.yaml:27-28,76,81-86](../../ws/src/warehouse_bringup/config/collision_monitor.yaml:81) / [virtual_scan_logic.py:19,24](../../ws/src/warehouse_traffic/warehouse_traffic/virtual_scan_logic.py:19)
- [ADR-0007](../adr/0007-no-overhead-camera-gesture-via-onboard-nn.md) / [01 §6](01-legal-envelope-japan.md) / [03](03-localization-gnss-and-ekf.md) / [05 §3-4](05-safety-envelope-and-intervention.md) / [06](06-hardware-delta-and-base-selection.md) / [07 §6](07-drivetrain-and-wheel-sizing.md)

一次情報（参照日 2026-09-12・[L] は調査レーン報告）:

- Nuwa HP60C 公式 <https://category.yahboom.net/products/hp60c>（structured light）[D] / YDLIDAR T-mini Plus 公式 <https://www.ydlidar.com/product/ydlidar-t-mini-plus>（60 kLux）[D]
- Isaac ROS Release Notes <https://nvidia-isaac-ros.github.io/releases/index.html> / isaac_ros_nvblox <https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_nvblox/index.html> [L]
- OAK-D Pro W <https://shop.luxonis.com/products/oak-d-pro-w> / OAK-D S2 <https://shop.luxonis.com/products/oak-d-s2> / depthai-ros Humble <https://docs.ros.org/en/humble/p/depthai-ros/> [L]
- ZED 2i <https://store.stereolabs.com/products/zed-2i/> / zed-ros2-wrapper <https://github.com/stereolabs/zed-ros2-wrapper> [L] / Livox Mid-360 <https://www.livoxtech.com/mid-360/specs> / livox_ros_driver2 <https://github.com/Livox-SDK/livox_ros_driver2> [L] / librealsense JetPack 6 issue <https://github.com/realsenseai/librealsense/issues/14025> [L]
- spatio_temporal_voxel_layer <https://index.ros.org/p/spatio_temporal_voxel_layer/> / pointcloud_to_laserscan <https://docs.ros.org/en/humble/p/pointcloud_to_laserscan/> [L]
- ImVisible / LYTNet <https://github.com/samuelyu2002/ImVisible> / arXiv:1907.09706 / "Does your robot know when to cross the road?" <https://ieeexplore.ieee.org/document/10465985/> / Audio-Visual Traffic Light State Detection（IROS 2024）<https://arxiv.org/abs/2404.19281> / Ultralytics Jetson benchmarks <https://docs.ultralytics.com/guides/nvidia-jetson/> / SegFormer <https://arxiv.org/pdf/2105.15203> [L]

## 【2026-09-14 追補】外部レビュー v3 の反映（terrain / cliff 契約・取付幾何・Humble CM 注記）

正本 = [09 §2-i](09-external-review-v3-response.md)。§3 の `cliff_scan`（LaserScan 器の流用）は維持しつつ、**LaserScan だけでは表現できない情報**を別出力にする。

| 項目 | 契約（提案・未凍結） |
|---|---|
| 観測結果 | `FLOOR_CONFIRMED` / `DROP_DETECTED` / `UNKNOWN` を区別（`UNKNOWN` は LaserScan に落ちない → 別 topic `/bot1/terrain/coverage`（案）で観測範囲・品質・許可境界を出し、停止判断（X2 → 走行許可）へ渡す） |
| 無効深度 | NaN / inf / 0 / 空点群 / 有効画素過少 / 凍結フレーム（新しい stamp でも同一画像）を検出し「品質不成立」にする（追補 ③ #5: 同一画像は疑いの材料とし機器側フレーム番号・計測時刻・受信状態と併用して確定。`UNKNOWN` は視野外・遮蔽・鮮度切れ・変換不成立・証拠不足も含む）|
| 走行許可 | 今から踏む領域 + 停止までに必要な領域（[05 追補 5](05-safety-envelope-and-intervention.md) の停止距離式）が**観測済み**であること |
| 距離 | cliff 端までの距離は車体中心でなく**車輪接地点・footprint** で評価 |
| 時刻 | 変換後も元の計測時刻を維持（古い depth に現在時刻を付け直さない） |
| costmap | cliff 用 marking は分離し、別 scan の自由空間 raytracing で崖を消さない（実装は**別 ObstacleLayer instance**。`clearing:false` だけでは同一 layer 内の別 source の raytracing から守れない = 追補 ③ #2 [D]）。時間経過・未観測で安全な床へ戻さず、**新しい床面観測でのみ clear** |
| 後退・旋回 | 前方カメラだけでは後方・側面の接地点を保護できない → 初期は観測範囲外へ進ませない（帰還の長距離後退禁止＝[09 §2-h](09-external-review-v3-response.md)） |

- **取付幾何**（§2 の下向き 10〜20° と `OQ-OD45` の補足）: 水平から下向き θ・カメラ高さ h で光軸が平面地面に当たる距離は `h / tan θ`（h = 0.30 m: 10° → 約 1.70 m・20° → 約 0.82 m）。これは**光軸の交点**であり視野全体の最近点ではない。車輪直前の死角・最小測距距離・垂直 FOV と併せて実測で決める。
- 前方 depth を 2D へ落とす際は高さしきい値と地面傾き補正が要る。2D LiDAR は走査面外を見ない。人物分類の成功を幾何停止の前提にしない。耐候性（IP・逆光・濡れ面）は型番と運用条件ごとに試験。
- **Humble 注記**は §3（[:66](04-perception-sidewalk-and-signals.md:66) 同一行）。`elevation_mapping_cupy` 行（§3・[D] 2026-09-12）はレビューが「未検証」としたが一次情報で確認済＝記述維持。Phase 2 で版・fork・commit を指定して再評価。

## 【2026-09-16 追補】外部知覚レビュー（04 内部分解・NN 候補・他箱との所有裁定）— ② 調査 6 レーン統合版

正本 = 本 doc（親 §2〜§7・2026-09-14 追補）+ [09 §2-i](09-external-review-v3-response.md)。図解 = [outdoor-perception-tree.html](outdoor-perception-tree.html)（04 の内部ツリー・所有裁定表・NN 表・OAK 表・出力 → 受け取り先表）。2026-09-16 に受領した外部知覚レビュー（「NN の性能 × 観測をつなぐ記憶 × 自車体・走行環境に合わせたデータ」で差を作る／Perception を地形認識・物体認識・信号認識・時系列状態管理・品質情報に分解／既存モデルで始め走行ログの弱点だけ追加学習）を、**v2.1 の箱 12+2 は不変**のまま 04 の内部分解として受け止める。同日、調査 6 レーン（A 検出器の最善探し／B OAK on-device／C 歩行者信号の候補／D NVIDIA スタック／E 随伴 Mac／F 重複の洗い出し）の報告を本追補に統合した（レーン報告は repo 外・**本追補が正本**。外部一次情報は URL + 参照日 2026-09-16・[D]=実読 / [L]=検索要約のみ / [I]=推論）。sub-directory 名は責務ラベル（1 dir ≠ 1 node / process・家は既存 `warehouse_perception` の拡張 = [09 §3](09-external-review-v3-response.md)）。**ユーザー裁定（2026-09-16）**: 11 sub-dir の粒度は速度優先で問題なし（採用方向・`OQ-OD4B`）。

> **レイヤ注記**（[:7](04-perception-sidewalk-and-signals.md:7) と同じ軸。レーン F 指摘 F1「全 sub-dir = L4」の自己矛盾を訂正）: 01_Geometry・05_Temporal_State・07_Output_Adapters（cliff / obstacle scan・coverage）・04_Surface_Semantics = **自律走行（安全層外・costmap / X2 への入力）**／02_Objects・03_Traffic_Signals・06_Prediction = **L4 知覚（publish-only・0 actuation）**／08_Quality_Evidence・10_Evaluation = **観測面（横断）**／09_Runtime_and_Models = 基盤（単一 layer に帰属させない・版 pin は 00）／00_Contracts = 00_Platform_Contract の参照。[productization/01 対応表](../productization/01-commercial-box-map.md:174) への行追加は実装 PR で行う（未実装のため本追補では追補 §9 に提案のみ）。P1（[23:37](../architecture/23-perception-and-localization.md:37)）/ P2（[23:41](../architecture/23-perception-and-localization.md:41)）は不変。

### 1. 04 内部分解（11 sub-dir・[提案] = `OQ-OD4B`）

| sub-dir | 責務（1 行） | 既に正本にあるもの | 新規 [提案] |
|---|---|---|---|
| 00_Contracts | inputs / observation_time / output_schema の consume 宣言 | 元計測時刻の維持（2026-09-14 追補「時刻」）・TF 配信責任（[03 §2-1](03-localization-gnss-and-ekf.md)） | 04 内に車体定数（h・θ・footprint・T_total・a_min）を複製しない = 00_Platform_Contract 参照。OAK 経由なら `i_get_base_device_timestamp: true` が時刻契約の実装点（追補 §4） |
| 01_Geometry | depth_validity / ground_estimator / obstacle_geometry / terrain_features / terrain_coverage | 無効深度（追補）・RANSAC 床面（親 §2 案 B）・FLOOR / DROP / UNKNOWN（追補・09 §2-i）・`h / tan θ` | **`FLOOR_CONFIRMED` = 床を観測できた**に限定し、段差高・勾配・凹凸・推定誤差を別フィールド。`UNKNOWN` ≠ `DROP_DETECTED`（`OQ-OD4C`）。下向きカメラの **MinZ**（OAK 800P ≈ 70 cm）の実測が前提（`OQ-OD4Q`）／**型 v0 = `TerrainState` / `TerrainCoverage`（→ 追補 ④）** |
| 02_Objects | detector / depth_association / tracker / uncertainty | 親 §5（前方 RGB 検出 + 深度で距離） | 検出器の既定を YOLOX-S から **RF-DETR-Nano** へ（追補 §3-1・`OQ-OD4N`）／推論の置き場 = Orin GPU か OAK on-device か（追補 §4・`OQ-OD4O`）／枠内深度の単純平均を避ける（OAK 既定 = MEDIAN・bbox 0.5 倍）／ByteTrack + odom で自車移動補償／「人は検出・距離 UNKNOWN」の出力（`OQ-OD4D`） |
| 03_Traffic_Signals | target_association / roi_refinement / lamp_classifier / temporal_state / observation_validity | 親 §4（登録 ROI・4 状態 fail-closed・時間窓多数決 8/10・非対称閾値・stale 0.5 s） | **青点滅は 0.5 s 周期 = 2 Hz**（親 §4 同一行訂正済・追補 §5）／候補 8 系統を同一 bag で比較（追補 §3-2）／ROI ずれの更新方式（`OQ-OD4F`）／出力に `crossing_id`・対応付けの確度・最終観測時刻 |
| 04_Surface_Semantics | sidewalk_segmentation / surface_attributes | 親 §2 案 A（Phase 2・検証器・単独権威にしない） | PIDNet-S（MIT）を Phase 2 主候補・YOLOE-26 は測定器（追補 §3-3）。**不一致の観測を出すのは 04、減速 / 停止は帯セレクタ・12**（レーン F 指摘 F3） |
| 05_Temporal_State | local_evidence_grid / ego_motion_compensation / expiry_and_reset | 追補「costmap」（新しい床面観測でのみ clear） | 局所観測履歴は costmap obstacle 層と二重化しない（`OQ-OD4E`）。物体履歴は 02・信号履歴は 03 が所有 |
| 06_Prediction | motion_baseline / learned_prediction | — | Phase 2（等速モデル + 広がる不確かさ → Trajectron++ は困る場面をログで確認してから） |
| 07_Output_Adapters | obstacle_scan / cliff_scan / terrain_grid / objects_signals | 親 §3 `cliff_scan`・親 §6 `pointcloud_to_laserscan`・coverage（2026-09-14 追補） | 追跡物体は depthai-ros の `vision_msgs/Detection3DArray`（spatial）を出発点に型を決める（追補 §4）／地形特徴グリッド・灯器観測の topic（型未凍結・contract PR）／consumer 側 costmap layer の所有（`OQ-OD4H`）／**coverage の型 v0 = `TerrainCoverage`（→ 追補 ④）** |
| 08_Quality_Evidence | observation_metrics / processing_metrics | [09 §2-b](09-external-review-v3-response.md)（X2 sensor_health） | 04 は自己申告 producer・判定点は X2 の 1 か所（`OQ-OD4E`）。撮影 → 消費までの遅延・滞留を指標に（新規 compute 指標。既存 `OQ-OD87` は Guardian tick jitter であり別物）／**自己申告の型 v0 = `ObservationQuality`（→ 追補 ④）** |
| 09_Runtime_and_Models | inference_backends / model_manifest / resource_budget | [02 §4](02-architecture-split-orin-pc-cloud.md)（屋外 S1 差分測定）・依存版の一覧は 00（[00 末尾追補](00-mission-and-scope.md)） | model_manifest（モデル名・重み hash・**入力サイズ・RGB/BGR・正規化・リサイズ方法・出力の解釈・ラベル順**・ONNX opset・TensorRT 版・GPU arch・**評価結果**〔bag id + 混同行列〕= 1 採用単位・追補 ③）= 04 が artifact として所有し、版一覧は 00 へ forward link（競合させない）。**engine はボード上で焼く**（`OQ-OD4U`）。配置案 OAK = 深度（+ 検出）／GPU = 検出・信号分類／CPU = 追跡・地形・点滅判定／**manifest の型 v0 = `ModelManifest`（→ 追補 ④）** |
| 10_Evaluation | bag_replay / scenario_sets / metrics / regression | 親 §7 P-1〜P-5・記録基盤 = run record（[jetson/03 §3](../jetson/03-build-deploy-run-and-run-records.md)・`mwr_run_record.py` が `ros2 bag record` を配線済。実走記録は未実施） | 差別化指標（走行距離当たりの誤停止・介入回数・危険検出時点の停止余裕・対象灯器の取り違え・認識結果の遅延）を additive。P-6（案）= 低視点 gap の実測（`OQ-OD4T`） |

### 2. 他箱との所有裁定（原則: 04 は観測 + 不確かさ + 品質の自己申告だけ。判定・履歴の正本・車体定数・記録は既存の箱に残し、同じ情報を 2 か所で判定しない）

列「重なる箱」は v2.1 の箱番号（00〜12・X1・X2）、列「正本」は doc 番号（`mode-outdoor/NN`）で区別する（レーン F 指摘 F13）。

| 04 の sub-dir | 重なる箱（v2.1） | 裁定案 | 正本（doc・file:line） |
|---|---|---|---|
| 00 | 00_Platform_Contract・01_Sensing・`warehouse_interfaces` | 04 = consume 宣言のみ。車体・校正・T_total・a_min = 00／時刻付与・OAK 内深度生成・`pointcloud_to_laserscan` の「変換」= 01 の責務定義と重なるため 07 の adapter は「変換の所有は 01・意味付け（cliff / coverage）は 04」と読む／型 = contract PR | [08 §2](08-architecture-v2-reference-alignment.md:39-40)・[09 §2-l](09-external-review-v3-response.md:186)・[09 §3](09-external-review-v3-response.md:198) |
| 01 terrain | 06 Navigation（costmap）・02 Map & Route（keepout）・10 Governance | **三分離**: ①床が見えた = 04 ／ ②この車体が通れる形状 = **06 が 00 の限界値と照合 or 02 keepout マスクに織り込む（09 §2-h はマスク側・二重縮小禁止）** ／ ③運用上通ってよい = 02 keepout（10 側の正本は沈黙＝operation mode / 横断ゲートのみ）。深度欠測は `UNKNOWN` であり落下を確定させない（costmap では未観測 = 通行不可のまま）。**未観測 = 通行不可の判定は cliff_scan marking・costmap `noDataObstacle`・coverage → X2 の 3 経路になるため一本化を `OQ-OD4C` で裁定** | [08 §3-1](08-architecture-v2-reference-alignment.md:65)・[08 §5](08-architecture-v2-reference-alignment.md:119)・[09 §2-h](09-external-review-v3-response.md:153)（②の照合者は正本の沈黙 = レーン F #5/#6） |
| 01 coverage・05 Temporal | **05_Mission_and_Route**（レーン F 指摘 F2 = 従来欠落） | 「観測範囲外へ進ませない・帰還の長距離後退禁止・観測範囲の向きの検証」= **05 が所有**（[09 §2-h](09-external-review-v3-response.md:155)）。04 は coverage を出すだけで、2026-09-14 追補「後退・旋回」行は 05 への consume 記述として読む | [09 §2-h](09-external-review-v3-response.md:155)・[08 §2 05 行](08-architecture-v2-reference-alignment.md:44) |
| 02 Objects | 12 Failsafe/MRM・06 Navigation・07 Safety Layer・帯セレクタ（`warehouse_perception` speed_band） | 04 = 位置・速度・不確かさ・最終観測時刻。減速 = 帯セレクタの**入力**（[05 §3-3](05-safety-envelope-and-intervention.md)・ADR-0012 決定 11 = `speed_limit` 単一 publisher。**入力契約は現状 `gesture_events` のみ＝距離→帯の契約は未定（05 の `OQ-OD55`〔GNSS 品質ゲートの帯合流〕と同じ穴。05:110 への同一行追記は 05 担当へ申し送り）**、帯の実装点は 08 §3-4「06」と productization/01「L4 warehouse_perception」で不一致 = レーン F F7）／MRM 判断・停止要求 = 12／通常回避 = 06／幾何反射停止 = 07（**観測できる範囲に限る**・2D LiDAR 走査面外や死角は止められない・CM は安全認証を提供しない）／駆動停止・禁止・保持 = 09（人物分類の成功を幾何停止の前提にしない・追補 ③ #6）。親 §5 の「前方 X m で slow 帯 → 停止」と tree の 04 行は裁定後に同一行修正 | [08 §3-4](08-architecture-v2-reference-alignment.md:83)・[ADR-0012:28](../adr/0012-speed-band-no-l2-best-effort.md:28)・[warehouse_perception/CLAUDE.md:16](../../ws/src/warehouse_perception/CLAUDE.md:16) |
| 03 | 02 crossing registry・10 crossing_gate・11 Remote（承認トークン発行）・05（route event） | registry（`crossing_id`・進入方位・ROI・想定距離）= 02 が正本（[08 §3-1](08-architecture-v2-reference-alignment.md:66)。**置き場は `OQ-OD47`・08 自体は `OQ-OD80` で未裁定**）。04 は registry を consume して「自分の灯器」を検証し観測を出す。進入可否 = 10（GREEN ∧ 一回限りトークン = [09 §2-f](09-external-review-v3-response.md:132)）。ROI 再投影に灯器 3D 位置が要るなら registry へ additive | [08 §3-1](08-architecture-v2-reference-alignment.md:66)・[09 §2-f](09-external-review-v3-response.md:131) |
| 04 Surface | 02 allowed_area / keepout・06・10・12 | 04 = 画素分類の観測と「許可帯との不一致」の観測。**不一致で減速 / 停止するのは 04 ではない**（帯セレクタ入力・12 の MRM 段）。単独権威にしない・Phase 2 | [親 §2](04-perception-sidewalk-and-signals.md:47)・[08 §3-4](08-architecture-v2-reference-alignment.md:84) |
| 05 | 06 local costmap（odom rolling・obstacle 層の marking 履歴）・02 local_terrain・03（ego motion の TF 所有）・X2 | 障害物 marking の履歴 = costmap 1 本（二重化しない）。04 の grid = coverage / 鮮度 / clear 条件の材料。**期限（stale）の判定 = X2（センサ鮮度は 09 §2-b で成立・04 内部 grid の失効は正本の沈黙）**。過去の記憶（「数秒前に床が見えた」）で新しい障害物・落下観測を上書きしない | [08 §2 06 行](08-architecture-v2-reference-alignment.md:45)・[09 §2-b](09-external-review-v3-response.md:72)・[12:547](../architecture/12-infrastructure-common.md:547) |
| 06 Prediction | 06 Navigation（MPPI）・12 | Phase 2。予測結果を消費する 06 側の評価処理まで含めて初めて意味を持つ（Navfn が route edge を理解しないのと同型 = [09 §2-h](09-external-review-v3-response.md:154)）→ `OQ-OD4H` と同型 | [09 §2-h](09-external-review-v3-response.md:154) |
| 07 | 06 costmap・07 CM・X2・10・12 | adapter は 04 に残る（既存 `cliff_scan` / pcl2laser と一致・dual-consumer = [12:547](../architecture/12-infrastructure-common.md:547)・Humble CM は途絶 fail-open = [12 末尾追補](../architecture/12-infrastructure-common.md)）。**崖 marking は別 ObstacleLayer instance（例 `cliff_layer`・source = cliff_scan のみ・clearing なし）に分離し max 合成**（`clearing:false` だけでは同一 layer 内の scan の raytracing から守れない = 追補 ③ #2 [D]。既存 virtual_scan が obstacle 層に同居する現行配線も同じ弱点 → doc12 §virtual_scan へ申し送り）。clear 条件 = 対応する場所に必要な高さ・連続性を満たす支持面の確認。**topic を publish しただけでは Navfn / MPPI は新しい意味を理解しない** → 06 側 costmap layer・評価処理の所有を裁定（`nav2_params.yaml` / launch は nav-traffic 所有 = [12:552](../architecture/12-infrastructure-common.md:552)） | [08 §2](08-architecture-v2-reference-alignment.md:45-46)・[12:547](../architecture/12-infrastructure-common.md:547) |
| 08 | X2 Diagnostics・X1 Observability・09（許可失効）・11（映像鮮度は別監視） | 判定点は X2 の 1 か所（**`/{bot}/scan` の途絶は #679〔origin/main af3550e・2026-09-16〕で Emergency Guardian の `scan_stale`（L1・level・自動解除）に着地済 = [doc12 末尾追補 (3)](../architecture/12-infrastructure-common.md)。cliff_scan / depth の途絶補償は未着地＝本行と `OQ-OD95` の対象。**#680 で `warehouse_safety/sensor_health.py`（`HealthReport(verdicts, healthy, health_epoch)`・source ごとの stale / digest 凍結判定）と `stop_distance.py`（`v·T_total + v²/(2·a_min) + margin`）が純ロジックとして着地（L1 帰属・未配線・R-26 148 unit）**＝04 の depth_validity / coverage の数値はこの module の source 入力候補。#685 で `scan_stale` は Humble 実機相当 harness で live 検証済。経路の記述は X2→12→09 / X2→09 / 二重経路の 3 通り = [08 §2](08-architecture-v2-reference-alignment.md:53)・[09 §2-b](09-external-review-v3-response.md:72)・[05 追補 1](05-safety-envelope-and-intervention.md:131)。実装位置は `OQ-OD95` で一本化。本追補は「判定点は X2 の 1 か所」のみを主張**）。記録は X1。`depth_validity` は数値（有効画素率・凍結検出）を出し判定はしない（レーン F F15）。**消費側（06・10・12）は使用時点で `age = now − source_stamp; 0 ≤ age < max_age` を共通契約で検査**（04 停止時は自己申告も届かない。入力妥当性であり第 2 の停止判定点ではない = 追補 ③ #4） | [09 §2-b](09-external-review-v3-response.md:72)・[09 §7 OQ-OD95](09-external-review-v3-response.md:255) |
| 09 / 10 | 00 dependencies・02 §4・X2（tick jitter）・X1・[doc20](../architecture/20-dev-quality-and-testing.md) | manifest = 04 が artifact として所有（モデル名・重み hash・入力サイズ・RGB/BGR・正規化・リサイズ方法・出力の解釈・ラベル順・opset・TensorRT 版・GPU arch・評価結果 = 1 採用単位）、**版一覧 = 00**（[00 末尾追補](00-mission-and-scope.md)へ forward link・競合させない = レーン F F10）。予算の数値は屋外 S1 の実測後（発明しない）。記録・再生の基盤 = X1（run record に配線済・実走記録は未実施） | [08 §2 00 行](08-architecture-v2-reference-alignment.md:39)・[jetson/03 §3](../jetson/03-build-deploy-run-and-run-records.md) |

不変: P1・P2・減速は安全機構に数えない（[05 §3-3](05-safety-envelope-and-intervention.md)）・NN の「通れそう」で落下検出・深度欠測を解除しない・`UNKNOWN` を `GREEN` に昇格しない（LLM / ER / 随伴 Mac の VLM 含む）。

### 3. NN 候補（レーン A・C 統合・すべて [提案]・参照日 2026-09-16）

#### 3-1. 検出器（02 detector = 歩行者・自転車・灯器候補）

**結論**: 旧追補（同日 ①）§3 の `YOLOX-S`（2021・COCO 40.5）は Apache 縛りでも最善ではない。**本命 = RF-DETR-Nano**（Apache-2.0・COCO 48.4・NMS 不要・Orin Nano 実測報告あり）、**測定器 = YOLO26n/s**（AGPL-3.0・Orin Nano Super の唯一の公式実測）、**保険 = D-FINE-S / RT-DETRv2-S**（Apache-2.0）。YOLO 系（YOLO26 / 11 / v12 / v13 / YOLOE / YOLOE-26 実装）は**すべて AGPL-3.0**、**DEIMv2 は非商用ライセンス**（除外）。Orin Nano Super の公式実測値があるのは YOLO26 だけで、他候補は**自分で測る**。

| 候補 | 公開 | COCO mAP（n / s 級） | NMS-free | TensorRT export | Orin 実測 | ライセンス | 一次情報 |
|---|---|---|---|---|---|---|---|
| **RF-DETR Nano / Small** | ICLR 2026・arXiv 2511.09554 | **48.4 / 53.0**（T4 TRT 10.4 FP16 bs1 2.3 / 3.5 ms） | DETR 系（NMS 不要）| 有（`rfdetr[tensorrt]`・engine はボード上で焼く） | **Orin Nano ≈ 25 FPS**（Roboflow blog 2026-05-06・変種 Nano・JetPack 未記載）| **Apache-2.0**（Nano〜Large・XL/2XL は PML 1.0）| <https://rfdetr.roboflow.com/develop/learn/benchmarks/> [D] / <https://github.com/roboflow/rf-detr> [D] / <https://arxiv.org/abs/2511.09554> [D] / <https://blog.roboflow.com/rf-detr-vs-alternatives/> [D・vendor] |
| **YOLO26 n / s** | 発表 2025-09・正式 2026-01 | 40.9 / 48.6（e2e 40.1 / 47.8） | **Yes**（`nms=False`）| 有 | **Orin Nano Super JP6.1・640・n: FP32 7.53 / FP16 4.57 / INT8 3.80 ms**（前後処理除く）| **AGPL-3.0** / Enterprise | <https://docs.ultralytics.com/models/yolo26/> [D] / <https://docs.ultralytics.com/guides/nvidia-jetson/> [D] |
| **D-FINE N / S** | ICLR 2025 Spotlight | 42.8 / 48.5（T4 TRT 10.4 FP16 2.12 / 3.49 ms）| DETR 系 | 有 | 未報告 | Apache-2.0 | <https://github.com/Peterande/D-FINE> [D] |
| **DEIM（v1）N / S** | CVPR 2025 | 43.0 / 49.0 | DETR 系 | 有 | 未報告 | Apache-2.0（`LICENSE` 実読）| <https://github.com/ShihuaHuang95/DEIM> [D] |
| RT-DETRv2-S | 2024-07 | 48.1 @217 FPS（T4 FP16）| **Yes**（明記）| 有 | 未報告 | Apache-2.0 | <https://github.com/lyuwenyu/RT-DETR> [D] |
| YOLOX-S（旧 §3 基準）| 2021-07 | 40.5 | No | 有 | 未報告 | Apache-2.0 | <https://github.com/Megvii-BaseDetection/YOLOX> [D] → **退役候補**（+8 pt 劣る） |
| YOLOE-26 | arXiv 2602.00168（2026-01-29）| abstract に数値なし | Yes | — | 未報告 | 実装は Ultralytics = AGPL [I] | <https://arxiv.org/abs/2602.00168> [D] |
| ✗ DEIMv2（DINOv3 系）| 2025-09 | S 50.9（最良級）| DETR 系 | TRT ≥ 10.6 推奨 | 未報告 | **非商用のみ**（`LICENSE.md`「No rights are granted for Commercial Use」）| <https://github.com/Intellindust-AI-Lab/DEIMv2> [D] → **除外** |

- 表の ms はほぼ T4 値。Orin Nano Super 公式実測（YOLO26n FP16 4.57 ms）と T4 値（1.7 ms・Ultralytics YOLO26 docs の T4 TensorRT 値）の比 ≈ 2.7 倍を他候補へ外挿するのは推論 [I]。**640 入力・前後処理除外**の条件付きで、親 §4 の「信号は小さいので入力解像度を上げる」方針と衝突する（`OQ-OD4U`）。
- **試す順序（Top-3）**: ① RF-DETR-Nano（ボード上で TRT engine を焼き、自画角・自前 bag で person / bicycle を評価）→ ② YOLO26n/s（同一 bag で ms と検出率を比較し**速度予算の上限を確定**。AGPL ゆえ配布物に載せない）→ ③ D-FINE-S（RF-DETR の TRT 変換が JP6.x / TRT 10.3 で詰まった場合の同精度帯の保険）。Apache 必須なら ① RF-DETR-Nano/Small ② D-FINE-S（代替 DEIM-S）。
- **COCO `traffic light` クラスを同じ検出器で拾えば**灯器候補の検出も 1 本にまとまる（追補 §3-2 系統 1）。

#### 3-2. 歩行者用信号（03）— 候補 8 系統を同一 bag で比較する

| # | 系統 | 入力 → 出力 | 学習 | 低視点適合 | Jetson | ライセンス | 一次情報 |
|---|---|---|---|---|---|---|---|
| 1 | 汎用検出器の `traffic light` クラス + TIER IV ped 分類器 | 枠 → 切抜 224×224 → red / green / unknown | 不要 | 枠は出るが COCO は車両用灯器中心 → 登録 ROI で救う | ○ | 検出器に依存（追補 §3-1）/ 分類器 Apache-2.0 | HF classifier [D] |
| 2 | TIER IV fine detector + ped 分類器（Autoware 2 段）| 登録 ROI → `tlr_car_ped_yolox_s`（BACKGROUND / traffic_light / pedestrian_traffic_light）→ 分類 | 不要 | **日本の灯器で学習済**（ped 21,199 枚・test 99.10 %）。**学習視点（高さ・距離・画角）は非公開** = `OQ-OD4I` | ○ YOLOX-S + MobileNetV2 | Apache-2.0 | <https://huggingface.co/AutowareFoundation/traffic_light_fine_detector> [D] / <https://huggingface.co/AutowareFoundation/traffic_light_classifier> [D] |
| 3 | open-vocab prompt（YOLOE / YOLO-World / Grounding DINO 1.5 Edge）| text "pedestrian traffic light" → 枠（**色は出ない** → 6 か分類器と 2 段）| 不要 | 「歩行者用」を名指しできる | △（`set_classes()` は export 時に焼き込み）| 重み: 記載なし・要確認 | <https://docs.ultralytics.com/models/yoloe/> [D] |
| 4 | LYTNet / ImVisible（PTL）| RGB → 5 クラス（Red / Green / Countdown Green / Countdown Blank / None）+ 横断歩道中心線 | **要（日本で再学習）** | 歩行者視点で収集された唯一の公開系（上海・5,059 枚）| ○ | **MIT** | <https://github.com/samuelyu2002/ImVisible> [D] |
| 5 | VLM（Gemini 2.x / Robotics-ER 2）を**オフラインの評価・ラベリング**に | 画像 → 状態ラベル・枠 | 不要 | 視点非依存 | ✗ 実時間（Batch API = 標準の 50 %・目標 24 h）| Google 規約・課金・映像の外部送信 = `OQ-OD4V` | <https://ai.google.dev/gemini-api/docs/batch-api> [D] |
| 6 | 色 HSV + 形状の古典法（Autoware `classifier_type: 0` 相当）| 枠内 hue ヒストグラム → red / green / unavailable | **不要（学習ゼロ = 基準線）** | NN より低視点ドメインシフトに強い・逆光に弱い | ◎ CPU | Apache-2.0 | Autoware docs [D] / IROS2024（緑 hue 75–100・赤 170–180・単一クラス YOLO + HSV 96.7 %）[D] |
| 7 | 音響式信号の音声検出 | マイク → MFCC 24 / 250 ms → RF → red / green | **要（日本の擬音で再学習）** | **視点非依存 = 遮蔽・逆光に強い** | ◎ CPU（250 ms あたり 4 ms）| 論文実装 未確認 | <https://arxiv.org/abs/2404.19281> [D]（豪州の音で学習・静止 97.0 %・走行中 90.4 %）/ 警察庁 音響信号機 21,644 基（擬音式 21,378・令和 8 年 3 月末）[D] |
| 8 | 自前 fine-tune（最終形）| 自前収集 → 単一クラス PTL 検出 + 自前 5 クラス分類 | 要 | 自車の画角で学習 = 定義上ベスト | ○ | 自社 | — |

- **日本の公開歩行者用信号データセット（青点滅ラベル付き）は見つからず**（2026-09-16）。自前収集が前提: 走行時と同一カメラ・同一取付高さ・**露出固定**（`OQ-OD4L`）・登録横断点のみ・赤→青→青点滅→赤の完全サイクルを 30 fps で連続録画（青点滅区間は間引かない）・音声と RTK を同一 bag に。ラベルは SAM 3 前ラベル → Gemini Batch → **遷移フレームだけ人が全件目視**。クラスは `RED / GREEN / GREEN_FLASHING / OFF_OR_UNLIT / NOT_VISIBLE` の 5（`OFF_OR_UNLIT` を独立させる = `OQ-OD4K`）。
- **点滅を直接クラス化した公開モデルは無い**（TIER IV は 3 クラス・Autoware msg の `FLASHING=3` は分類器が埋めない）→ 時系列ルールで作る（追補 §5）。**音響は AND 側にのみ入れる**（視覚 GREEN を否定できるが肯定できない・`OQ-OD4M`）。
- 比較試験: 1 回の記録 → 1 セットのラベル → 8 系統を同じ bag に再生 → 同一混同行列（P-1 = 「青点滅→青」「赤→青」0 件が必達・満たさない系統は不採用）。**走行判断に使わない観測モード**（追補 §8 順序 4）。

#### 3-3. 歩道セグメンテーション（04）・深度（01）・オフライン ラベリング（10）

| 用途 | Phase 1 | Phase 2 / 評価してから | 一次情報 |
|---|---|---|---|
| 04 sidewalk_segmentation | 使わない | **PIDNet-S（MIT・Cityscapes）** を主候補。YOLOE-26 open-vocab（"sidewalk / road / curb"）は学習なしの検証器として測定器扱い（AGPL）。SegFormer は非商用注意 | PIDNet GitHub API license = MIT [D] / <https://arxiv.org/abs/2602.00168> [D] |
| 01 深度の補完 | OAK ステレオ深度のみ | tiny 単眼深度（DepthART-S: Orin NX 8GB 102 FPS・2026-07）を無テクスチャ舗装・深度欠測の**検証器**に。Depth Anything 3（2025-11・FoundationStereo 由来 metric）は AGX Orin + JP 6.2 + Humble の配備例あり・FPS 未公表。**単眼 metric を安全主判定にしない** | <https://arxiv.org/abs/2607.17099> [D] / <https://wiki.seeedstudio.com/deploy_depth_anything_v3_jetson_agx_orin/> [D] |
| 10 ラベリング（オフライン・Mac 可）| — | SAM 3（2025-11・473.6M〔Meta 記事は 848M と不一致〕・重みは HF で要申請・実時間不可）/ SAM 3.1（2026-03）/ DINOv3（`dinov3-license`・gated）を bag からのマスク生成に。実機には載せない | <https://docs.ultralytics.com/models/sam-3/> [D] / <https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m> [D] |

#### 3-4. 低視点データセットとライセンス境界（レーン A）

| データセット | 視点 | 中身 | ライセンス | 適合 |
|---|---|---|---|---|
| SANPO（Google・WACV 2025）| 人 egocentric（高さ数値なし）| 701 stereo videos・112K frames の dense video panoptic + 深度 | **CC-BY-4.0** | 04 セグの評価に最適。**検出 box ではない** |
| JRDB（Stanford）| 社会ロボ搭載（高さ未記載）| 2D box 2.4M / 3D box 1.8M | **CC BY-NC-SA 3.0（非商用）** | 人検出の評価に最良だが **productization を視野に入れるなら評価にも使わない境界**（`OQ-OD4T`）|
| SidewalkBench（2026-06）| Isaac Sim | 9 モデル × 330 / 800 / 105 シナリオ | CC-BY-4.0 | 歩行者相互作用のシナリオ設計の参照。検出用の実画像注釈は無し |
| PMMA（2026-02）| 屋外（未記載）| 移動補助具 9 クラスの検出データ | 未確認 | 車椅子・歩行器で「人」検出器を叩く |

「歩道低視点で COCO 学習 RGB 検出器の person / bicycle 精度が落ちる」の定量根拠は**未確認**（LiDAR 3D 検出では VRU クラスで 27.5〜40.6 pt の gap の一次情報あり = <https://arxiv.org/abs/2606.25652> [D]）→ **自分で測る**（親 §7 に P-6 を追加する案 = `OQ-OD4T`）。

### 4. OAK on-device 案（レーン B・案 A 推奨・[提案]）

「検出 → 3D 位置」まではカメラ側で完結できる。**追跡・自車移動補償・地形幾何・信号の時系列判定・fail-closed 判定は Orin に残る**。減るのは Orin の演算であって帯域ではない（不確かさと cliff に深度画像が要る）。

| 機能 | RVC2（OAK-D Pro W・現行）| RVC4（OAK 4）| Orin に残すか | 根拠 |
|---|---|---|---|---|
| ステレオ深度 | ✅ on-device | ✅ 800P@60 | カメラ | Luxonis RVC2 / RVC4 docs [D] |
| 物体検出（YOLO）| ✅ blob・FP16。YOLOv6-Nano @416 **67.41 inf/s** | ✅ INT8。同モデル 798.38 inf/s | カメラ可（**RF-DETR は RVC4 のみ** = Luxonis 公式手順）| <https://models.luxonis.com/luxonis/yolov6-nano/face58c4-45ab-42a0-bafc-19f9fee8a034> [D] / <https://docs.luxonis.com/software-v3/ai-inference/integrations/rf-detr> [D] |
| 検出枠 → 3D 位置 | ✅ `YoloSpatialDetectionNetwork` | ✅ | カメラ可 | <https://docs.luxonis.com/software/depthai-components/nodes/yolo_spatial_detection_network/> [D] |
| ROI 深度統計 | 既定 **MEDIAN**・bbox **0.5 倍**・しきい値 mm（ROS 既定 100 / 10000）。**ROS ドライバは統計方式を露出しない** | 同上 | 不確かさが要るなら Orin で深度分布 | depthai-core `SpatialDetectionNetwork.hpp` / `SpatialDetectionNetworkProperties.hpp` [D] |
| 物体追跡 | ⚠ ハードは `ObjectTracker` 可・**depthai-ros に配線なし**（grep 0）| 同上 | **当面 Orin（ByteTrack）** | depthai-ros humble / develop 実読 [D] |
| Neural Depth / セグ / 診断 | Neural depth ✗（RVC2 は throw）・セグ ✅・診断 ✅ | Neural depth ✅・セグ ✗「not supported yet」・診断 ✗「not yet available」 | — | `depthai_ros_driver` develop 3.4.0 [D] |
| 自車移動補償・信号時系列・地形幾何・品質判定 | ✗ | ✗ | **Orin** | — |

- **depthai-ros（Humble）**: 本線 apt = v2 **2.12.2**（`ros-humble-depthai-ros`）／v3 = 3.3.0 は `ros2-testing` 経由（`OQ-OD4P`）。`i_nn_type: spatial` → `~/<nn>/spatial_detections`（`vision_msgs/Detection3DArray`）[D]。**`i_get_base_device_timestamp` の既定は false** → 2026-09-14 追補「元計測時刻の維持」を満たすには屋外プロファイルで **true 必須**（`OQ-OD4P`）。2 台は `i_device_id` で別 container（公式 multicam 例・RVC2 のみ注記）。
- **案 A（推奨）**: OAK-D Pro W ×2（$529・IP66・基本 2.5–3 W・**IR 点灯時最大 15 W / USB3 バス給電 4.5 W** → 日中は IR off）+ on-device YOLO（Apache 系なら YOLOX-Nano/Tiny・`luxonis/tools` が変換対応。YOLOv6-Nano のライセンスは要確認）+ spatial。Orin = ByteTrack・地形・信号分類 TensorRT・時系列・coverage / 品質。
- **案 B（Phase 2 候補）**: OAK 4（QCS8550・NPU 48 INT8 + 12 FP16 TOPS・IP67・−20〜+50 ℃・shop **$949〜1,149**〔発表時から値上がり〕・**平均 10–15 W / ピーク 25 W**）。移せない処理は案 A と同じで利得は検出の GPU 時間のみ。Humble は ros2-testing の v3 前提で RVC4 未対応コメントが 4 か所 → 引き金は「屋外 S1 で GPU が足りない」と確定したときだけ。
- **リスク**: 下向きカメラの **MinZ**（OAK-D Pro W 800P ≈ 70 cm・400P/extended ≈ 20〜40 cm。広角 127° の焦点距離逆算で ≈ 25 cm は本追補の計算 [I]。幾何的帰結 [I]: h = 0.30 m・MinZ 0.70 m なら測れる最近接の地面は水平 √(0.70²−0.30²) ≈ 0.63 m 先＝車輪直前 0.63 m が原理的に死角 → 800P モードは cliff 用に使えない可能性が高く 400P / extended で再計算。`h / tan θ`〔追補 2026-09-14〕とは別量）→ `OQ-OD45` の実測ゲートに MinZ を必須化（`OQ-OD4Q`）／深度生成はクローズド FW（無効深度の検出は Orin で自前実装）／`DEPTHAI_TELEMETRY=0` を屋外既定に・depthai diagnostics を X2 入力に・RVC2 の保守寿命・2 台同期は未確認（`OQ-OD4S`）。

### 5. 青点滅の時系列判定（2 Hz 訂正・レーン C・[提案]）

- 一次情報 [D]（本追補で PDF を実読）: 科警研 横関・森・矢野「歩行者用信号青点滅の明滅周期の違いによる心理的影響」交通工学論文集 5(2) B_17–B_23, 2019 <https://www.jstage.jst.go.jp/article/jste/5/2/5_B_17/_pdf/-char/ja>: 「現在の歩行者用信号青点滅の明滅周期は 0.5 秒…警察庁で定める信号の仕様書によって規定」「（車両用信号の）閃光の点滅周期は約 1 秒、歩行者青点滅の点滅周期は約 0.5 秒とする」（1972 年仕様書）。→ 親 §4 の「~1 Hz」は車両用の値だったため同一行で訂正済。
- 帰結: **10 Hz 判定と 2 Hz 点滅は整数比（5 サンプル/周期）**で位相が回らない。duty 比は一次情報に無く未確定（50 % なら ON は最大 6/10 で 8/10 に届かないが、duty ≳ 80 %・10 Hz からの微小ドリフトによる低周波うなり・分類器が消灯相を GREEN と出す誤り、の 3 経路で崩れる）。多数決だけを防波堤にしない。
- 二段レート方式（`OQ-OD4J`）: レート A = ROI 輝度サンプラ（カメラ fps・エッジ検出なので整数比でも可・CPU・緑 / 赤の hue 面積比 g_t / r_t と露出値 e_t）でヒステリシス二値化 → **立ち上がりエッジ間隔（= 周期）**の中央値が 0.5 s に一致（隣接エッジ間隔は 0.25 s） ∧ ON / OFF 両相あり ∧ 赤が同期して増えていない → `is_flashing`（2 Hz の Goertzel 1 点評価で補強可）。レート B = NN 分類器 10 Hz（TIER IV 3 クラス）で多数決（**この「10 Hz」は罠の説明であって設定値ではない** ── 同一行末と [:306](04-perception-sidewalk-and-signals.md:306) が要求するとおり f_B は点滅周期と**非整数比**にする〔例 9 Hz〕。実装 `signal_temporal_core.py` は 2 Hz の整数倍を構築時に拒否する = [追補 ⑥ `classifier_rate_hz`](04-perception-sidewalk-and-signals.md:630)）。状態決定: `is_flashing` → GREEN_FLASHING／RED 多数決 → RED／GREEN 多数決 ∧ NOT flashing ∧ **窓内の OFF 相 0 件** ∧ stale < 0.5 s → GREEN／それ以外 UNKNOWN。GREEN 離脱は 1 フレーム（親 §4 の非対称を維持）。f_B は点滅周期と**非整数比**にする（例 9 Hz = 4.5 倍。12 Hz や 30 Hz は 2 Hz の整数倍で同じ罠）。窓 W は `source_stamp` 基準の秒数と有効サンプル数の下限で定義しフレーム数では定義しない。`OFF_OR_UNLIT`（消灯相）と `NOT_VISIBLE` / `UNKNOWN`（遮蔽・欠落・鮮度切れ）は別クラスで、UNKNOWN を消灯と同一視しない（追補 ③ #1）。
- 前提: **露出固定**（LED の PWM と自動露出のエイリアシングが偽 OFF を作る）＝`OQ-OD4L`。経過時間表示型は「バーが減っている」を GREEN 継続の否定材料にのみ使う。TIER IV 分類器の `unknown` は消灯相・逆光・遮蔽を潰すので周期性は連続量（レート A）で見る。Autoware 側の低視点に効く既存ノブ: `pedestrian_traffic_light_max_angle_range` 80°・露出しきい値 0.85 / −0.83 で UNKNOWN + conf 0.0 [D]。

### 6. NVIDIA スタックの世代の壁（レーン D・pin 表は [00 末尾追補](00-mission-and-scope.md) が正本）

- ボード実体は **JetPack 6.2.1（L4T 36.4.4）**・Ubuntu 22.04.5・kernel 5.15.148-tegra（[jetson/02](../jetson/02-remote-access-and-dev-link.md:171)）。同梱: CUDA 12.6.10 / cuDNN 9.3.0 / **TensorRT 10.3.0** / VPI 3.2（release notes [D]・ボード上の実測値は未取得 = `OQ-OD4X`）。JP6 系の最新は 6.2.3（L4T 36.5.2）・EOL 明文は未発見。
- **Isaac ROS 3.2 U1**（2025-01-16・Humble・JP 6.2・Orin Nano Super）が唯一の実用経路（3.2 の表は「JetPack 6.1 and 6.2」で **6.2.1 は名指しなし**）。3.2 に cuVSLAM / nvblox / DNN inference / RT-DETR / YOLOv8 は揃う。**4.6.0（2026-08-18）は JetPack 7.2 + Jazzy で「Jetson Orin」が加わった**（Orin Nano 8GB の明記なし・128+ GB NVMe）→ [ADR-0008:40](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md:40) の「Orin がサポート表に載らない」は失効し、同一行で時点付き訂正済。**結論（Humble 維持）は他根拠で維持**（`OQ-OD4W`）。Humble EOL = 2027-05（REP-2000 [D]）。
- **Orin Nano Super に DLA は無い**（NVIDIA 公式 spec に DLA 行なし [D] <https://www.nvidia.com/en-us/autonomous-machines/embedded-systems/jetson-orin/nano-super-developer-kit/>・GPU 1024 CUDA / 32 Tensor・67 INT8 TOPS・7–25 W）→ 推論は GPU 単独。Ultralytics guide の DLA 記述は他モジュール向け。
- Jazzy + JP7.2 へ上げると壊れる資産: HP60C `ascamera` 閉ソース `.so`（不可逆）・ydlidar humble・Nav2 1.1.20 資産と collision_monitor Humble 意味論に合わせた安全設計・depthai-ros Humble バイナリ・py3.10 pin・Orin Nano DevKit の SD イメージ廃止（ISO 再フラッシュ）。
- TensorRT 変換の現実: Ultralytics は JP6/7 とも TensorRT 10.x 前提。RF-DETR の C++/TRT 実装が **Orin NX / TensorRT 10.3.0 / CUDA 12.6**（= JP6.2.x と同一組合せ）でテスト済の報告 [L]。D-FINE / YOLOE の JP6.2 export 実績は未取得。

### 7. 随伴 Mac（レーン E・正本 = [02 末尾追補](02-architecture-split-orin-pc-cloud.md)・`OQ-OD28〜2A`）

- 04 に効く規律だけを写す: 随伴 Mac が返すものは **observation / proposal 型のみ**。`stop_request` / `cmd_vel*` / `speed_limit` の producer にならない。Mac 断 = 助言喪失のみで走行継続（停止させると許可判断点が二重化）。**04_Surface 検証器・06 Prediction 比較実装・10_Evaluation（SAM 3 / DINOv3 ラベリング・回帰）・VLM 助言は Mac 可**、01 Geometry・03 fail-closed 観測・07 adapters・08 の判定（X2）は Orin 常駐。
- 実行手段: PyTorch MPS（macOS 14+）/ MLX（arm64・unified memory）/ CoreML export（YOLOv8 / 11 / 26）[D]。ROS 2 Humble on macOS は REP-2000 で Tier 3・amd64 のみ → Mac は ROS ノードにせず WS クライアント + 推論プロセス。16 GB unified 上の SAM 3 / DINOv3 同時常駐は未実測。

### 8. 実装順序（レビュー 1〜5 → [08 §6](08-architecture-v2-reference-alignment.md) / [09 §4](09-external-review-v3-response.md) の対応・レーン F 指摘 F6 / F12 を訂正）

1. **記録・同期・可視化**（RGB・深度・TF・odom・音声・RTK を同じ場面で再生し重ね合わせを確認）= 08 §6 順序 2（現地でセンサと車体状態を記録）・記録基盤 = run record（jetson/03 §3・配線済・実走記録は未実施）。`i_get_base_device_timestamp: true`・露出固定を記録プロファイルに含める。
2. **幾何認識**（床・段差・欠測を表現し `cliff_scan` / coverage を 06・07・X2 へ接続）= 09 §4 順序 5・3。ゲート = 親 §7 P-2・MinZ 実測（`OQ-OD4Q`）・G-OD-F1 / F5（[09 §5](09-external-review-v3-response.md)）・R-26 unit（coverage 判定・無効深度）。
3. **歩行者の検出・距離・追跡**（RF-DETR-Nano vs YOLO26 vs OAK on-device を同一 bag で・遮蔽・逆光・接近）= ゲート 親 §7 P-4・P-6（案・`OQ-OD4T`）。
4. **登録横断箇所の信号認識**を**走行判断に使わない観測モード**で 8 系統比較 = 09 §4 順序 7 の前・`OQ-OD92` と両立。ゲート 親 §7 P-1（0 件必達）。
5. **ログで不足が確認できた機能**（歩道セグメンテーション・追加学習・学習型予測）= Phase 2。

Orin Nano Super 8GB の配置で足りるかは、カメラ 2 台 + Nav2 + 映像配信を同時に動かして測る（[02 §4](02-architecture-split-orin-pc-cloud.md)）。平均 FPS でなく**撮影から結果が消費されるまでの遅延**と滞留を重視する。下向きカメラの角度は `d_観測確認済み > v·T_total + v²/(2·a_min) + 余裕`（[09 §2-g](09-external-review-v3-response.md)・T_total / a_min は 00 の契約）を車体前端・車輪から見た観測範囲で満たすかで決める（旋回は車体と車輪の通過範囲）。**この不等式の評価点は X2 / 09 の 1 か所**にし、04 は観測済み距離を出すだけにする（レーン F F18。算術は #680 の `warehouse_safety/stop_distance.py` に着地済・未配線）。

### 9. レイヤ annotation の要追記（[productization/01:174](../productization/01-commercial-box-map.md:174)・実装 PR で行う提案・レーン F）

01 `cliff_detector` / `ground_estimator` = 自律走行（安全層外）producer・consumer は L1 CM と L1 Navigation／07 adapters = 出力は L1 Safety の observation／07 coverage = consumer は X2・06／03 `lamp_classifier` / `temporal_state` = L4・許可は L2（10）を同一行に併記／02 `detector` / `tracker` = L4・停止の最終担保は L1（07）／04 `sidewalk_segmentation` = 自律走行（安全層外・裁定待ち）／08 metrics = 横断（観測面）／09 Runtime = 単一 layer に帰属させない・版 pin は 00／10 Evaluation = 横断／既存 L4 行（`warehouse_perception`）に 04 系ノード同居と executor 分離規律。

### 10. OPEN QUESTIONS（本追補・接頭辞 `OQ-OD4*` の続き）

- `OQ-OD4B` 04 内部分解（00〜10 の 11 sub-dir）を責務ラベルとして採るか（ユーザー裁定 2026-09-16: 粒度は問題なし・採用方向。1 dir ≠ 1 node / process・家は `warehouse_perception`）。
- `OQ-OD4C` terrain 出力の三分離（観測 = 04／通行可能形状 = **06 が 00 と照合 or 02 keepout マスク側**〔正本の沈黙・二重縮小禁止〕／運用許可 = 02・10）と `FLOOR_CONFIRMED` の意味限定・段差高・勾配・凹凸・推定誤差の別フィールド化（型は contract PR）。`UNKNOWN` ≠ `DROP_DETECTED`。**未観測 = 通行不可の判定 3 経路（cliff marking / costmap noDataObstacle / coverage → X2）の一本化**。型 v0 は**追補 ④**（`TerrainCoverage` = 段差高 / 勾配 / 凹凸 / 推定誤差を別 field 化・通行可否 field なし。裁定そのものは未決）。
- `OQ-OD4D` 歩行者行の書換: 04 = 位置・速度・不確かさ・最終観測時刻のみ／減速 = 帯セレクタ入力（**距離 → 帯の入力契約は未定〔05 の `OQ-OD55` と同じ穴・05 担当へ申し送り〕・実装点の不一致 08 §3-4 vs productization/01**）／MRM 段 = 12／回避 = 06／停止 = 07（親 §5 と tree v2.1 04 行の同一行修正）。
- `OQ-OD4E` 二重化回避の所有 2 件: 局所観測履歴（04 evidence grid）vs costmap obstacle 層の履歴／品質の自己申告（04）vs 判定（X2 の 1 か所・経路は `OQ-OD95` で一本化。判定 module は #680 `sensor_health.py` として着地済・置き場のみ未決）。
- `OQ-OD4F` 信号 ROI の更新方式: **基本形 = 地図灯器 3D 位置 + カメラ内外パラメータ + 撮影時点の姿勢で候補領域を作り NN（TIER IV tlr YOLOX-S）で補正する併用**（RTK + 方位だけでは不足・再投影は NN の代替ではない = 追補 ③ #3）。NN 無しで足りるかは Phase 1 で実測、許容値（Autoware 既定 ±1° / ±0.5 m 相当）は M1 の実測誤差から決める。registry へ灯器 3D 位置を additive（置き場は `OQ-OD47`。#686 の L3 route schema は横断点レジストリを射程外と明記 = [03 末尾追補](03-localization-gnss-and-ekf.md)）。
- `OQ-OD4G` NN 候補の Humble + JetPack 6.2.1 + TensorRT 10.3.0 での変換・入出力互換・自画角評価（`OQ-OD41` との関係 = 公開重みで開始し自前収集は弱点確認後）。
- `OQ-OD4H` consumer 側実装（06 costmap layer・追跡物体 / 予測の評価処理）を 04 の実装範囲に含めるか（`nav2_params.yaml` / launch は nav-traffic 所有）。 **→ 崖 costmap layer 分は裁定済（2026-09-18）= [追補 ⑩ §1](04-perception-sidewalk-and-signals.md:1013)**: **nav-traffic 所有**（04 は producer のみ）。追跡物体 / 予測の評価処理は未決のまま。
- `OQ-OD4I` TIER IV モデルの学習視点（カメラ高さ・距離・画角）が非公開であることの扱い: そのまま流用して自前データで評価するだけにするか、低視点画像の追加学習を最初から計画に入れるか。
- `OQ-OD4J` 青点滅 0.5 s = 2 Hz の訂正を受け、点滅判定を 10 Hz 多数決から二段レート（輝度サンプラ = カメラ fps + NN 多数決）へ変えるか。NN 側 f_B を点滅周期と非整数比（例 9 Hz）にするか。duty 比を実測で確定するか。「窓内 OFF 相 0 件」を GREEN の直交条件にするか。
- `OQ-OD4K` `OFF_OR_UNLIT` を独立クラスにするか（TIER IV 3 クラスでは消灯相・逆光・遮蔽が `unknown` に潰れる）。
- `OQ-OD4L` 露出の契約: 信号 ROI を見る間は自動露出を禁止しシャッタ速度を固定する（LED PWM とのエイリアシング）。固定値は実測後。
- `OQ-OD4M` 音響（`OQ-OD48` の具体化）: AND 側にのみ入れる（視覚 GREEN を否定できるが肯定できない）でよいか。日本の擬音（異種鳴き交わし・香川は方角対応）で再学習必須。青点滅中の鳴動挙動は資料に無く実測。
- `OQ-OD4N` 02 detector の既定を YOLOX-S → RF-DETR-Nano に置き換えるか。YOLO26 は「速度上限の測定器」に限定し配布物に含めない AGPL 境界を doc に書くか。
- `OQ-OD4O` 推論の置き場: Orin GPU（RF-DETR）か OAK on-device（RVC2 = YOLOX-Nano / YOLOv6n 級・RF-DETR 不可）か。親 §1-2 の「GPU 温存」は深度に限った効果であることを resource_budget の前提に反映するか。
- `OQ-OD4P` on-device spatial の契約: ROS 既定（MEDIAN・bbox 0.5・100 / 10000 mm）のままか Orin で深度分布を取るか／`i_get_base_device_timestamp: true` を屋外プロファイル必須にするか／depthai-ros は本線 v2 2.12.2 か ros2-testing の v3 か。
- `OQ-OD4Q` 下向きカメラの **MinZ 実測**（800P / 400P / extended・広角焦点距離）— `OQ-OD45` の前提条件。測れないなら俯角・高さ・解像度モードを変える。
- `OQ-OD4R` 02 tracker を on-device `ObjectTracker` に寄せるか Orin ByteTrack のままか（depthai-ros に配線なし・`depthai_bridge` の `TrackDetection2DArray` 型は既存）。
- `OQ-OD4S` OAK 運用契約: `DEPTHAI_TELEMETRY=0` を既定に／depthai diagnostics を X2 `sensor_health` の入力に（RVC4 は未対応）／RVC2 の保守寿命（Luxonis へ確認）／2 台のハード同期／IR 給電（USB バス 4.5 W vs Pro 最大 15 W）。
- `OQ-OD4T` 低視点 gap の自 repo 実測（親 §7 に P-6 を追加: COCO 学習 RGB 検出器を h = 0.3〜0.6 m・俯角付きで評価したときの person / bicycle AP 低下）と評価データのライセンス境界（JRDB 非商用を評価にも使わない）。
- `OQ-OD4U` TensorRT engine は GPU arch と TRT 版に固定され可搬でない → 「engine は必ずボード上で焼く」を 09 の契約にし build ≠ deploy ≠ run 記録（jetson/03）で扱う。公式ベンチ（640・前後処理除外）を予算に使わず自画角・自解像度・前後処理込みの実測で置き換える。
- `OQ-OD4V` ラベリングに VLM / SAM を使う際の課金ゲートと走行映像の外部送信の可否（`WAREHOUSE_LIVE_ER` と同型の gate・SAM 3 / DINOv3 は重み取得が gated）。
- `OQ-OD4W` [ADR-0008:40](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md:40) の失効根拠の扱い（時点付き訂正で結論維持 = 本追補で実施済／屋外向けに ADR を再裁定／新 ADR で上書き）。決めない。
- `OQ-OD4X` 依存 pin の空欄の実測（ボード上の CUDA / TensorRT / `/ssd` 空き・`dpkg -l nvidia-jetpack`）・Isaac ROS 3.2 × JetPack 6.2.1 の適合・4.6 の「Jetson Orin」に Orin Nano 8GB が含まれるか・JP6 の EOL・6.2.3 へ上げるか（[00 末尾追補](00-mission-and-scope.md)）。

### 11. References（本追補・参照日 2026-09-16・[D] は実読）

- 検出器: RF-DETR benchmarks <https://rfdetr.roboflow.com/develop/learn/benchmarks/> / <https://github.com/roboflow/rf-detr> / <https://arxiv.org/abs/2511.09554> / Roboflow blog <https://blog.roboflow.com/rf-detr-vs-alternatives/> / YOLO26 <https://docs.ultralytics.com/models/yolo26/> / Jetson guide <https://docs.ultralytics.com/guides/nvidia-jetson/> / D-FINE <https://github.com/Peterande/D-FINE> / DEIM <https://github.com/ShihuaHuang95/DEIM> / DEIMv2 <https://github.com/Intellindust-AI-Lab/DEIMv2> / RT-DETR <https://github.com/lyuwenyu/RT-DETR> / YOLOX <https://github.com/Megvii-BaseDetection/YOLOX> / YOLOE-26 <https://arxiv.org/abs/2602.00168> / Luxonis × RF-DETR <https://docs.luxonis.com/software-v3/ai-inference/integrations/rf-detr> / Ultralytics × Luxonis <https://docs.ultralytics.com/integrations/luxonis/>
- 低視点: SANPO <https://arxiv.org/abs/2309.12172> / JRDB <https://jrdb.erc.monash.edu/> / SidewalkBench <https://arxiv.org/abs/2606.16953> / PMMA <https://arxiv.org/abs/2602.10259> / LiDAR gap <https://arxiv.org/abs/2606.25652>
- OAK: RVC2 <https://docs.luxonis.com/hardware/platform/rvc/rvc2/> / RVC4 <https://docs.luxonis.com/hardware/platform/rvc/rvc4/> / OAK-D Pro W <https://shop.luxonis.com/products/oak-d-pro-w> / OAK 4 <https://www.luxonis.com/oak4> / shop <https://shop.luxonis.com/collections/oak-4> / YoloSpatialDetectionNetwork <https://docs.luxonis.com/software/depthai-components/nodes/yolo_spatial_detection_network/> / ObjectTracker <https://docs.luxonis.com/software/depthai-components/nodes/object_tracker/> / StereoDepth（MinZ）<https://docs.luxonis.com/software/depthai-components/nodes/stereo_depth/> / DepthAI ROS v3 <https://docs.luxonis.com/software-v3/depthai/ros/> / rosdistro humble（depthai-ros 2.12.2 / depthai_ros_v3 3.3.0）<https://raw.githubusercontent.com/ros/rosdistro/master/humble/distribution.yaml> / depthai-ros humble・develop ソース（`spatial_detection.hpp`・`nn_param_handler.hpp`・`TrackDetection2D.msg`）
- 信号: 科警研 2019 <https://www.jstage.jst.go.jp/article/jste/5/2/5_B_17/_pdf/-char/ja> / HF fine_detector <https://huggingface.co/AutowareFoundation/traffic_light_fine_detector> / HF classifier <https://huggingface.co/AutowareFoundation/traffic_light_classifier>（`lamp_labels_ped.txt`）/ Autoware classifier README・schema・map_based_detector docs / `TrafficLightElement.msg` / ImVisible <https://github.com/samuelyu2002/ImVisible> / LYTNet <https://arxiv.org/abs/1907.09706> / Mendeley 809 枚 <https://data.mendeley.com/datasets/9tm59d3nsn/1> / IROS2024 Audio-Visual <https://arxiv.org/abs/2404.19281> / 警察庁 音響信号機 <https://www.npa.go.jp/bureau/traffic/seibi2/annzen-shisetu/hyoushiki-shingouki/onkyou.html> / 香川県警 <https://www.pref.kagawa.lg.jp/police/kokikaku/koutsuuanzen/koutsuu/otonoderusingou.html> / YOLOE <https://docs.ultralytics.com/models/yoloe/> / Gemini Batch <https://ai.google.dev/gemini-api/docs/batch-api> / Gemini Robotics <https://ai.google.dev/gemini-api/docs/robotics-overview>
- NVIDIA: JetPack archive <https://developer.nvidia.com/embedded/jetpack-archive> / downloads <https://developer.nvidia.com/embedded/jetpack/downloads> / 7.2 <https://developer.nvidia.com/embedded/jetpack/downloads/archive-7.2> / 6.2 notes <https://docs.nvidia.com/jetson/jetpack/6.2/release-notes/index.html> / 6.2.1 notes <https://docs.nvidia.com/jetson/jetpack/6.2.1/release-notes/index.html> / Isaac ROS 3.2 <https://nvidia-isaac-ros.github.io/v/release-3.2/getting_started/index.html> / 4.6 <https://nvidia-isaac-ros.github.io/getting_started/index.html> / releases <https://nvidia-isaac-ros.github.io/releases/index.html> / REP-2000 <https://raw.githubusercontent.com/ros-infrastructure/rep/master/rep-2000.rst> / Orin Nano Super spec <https://www.nvidia.com/en-us/autonomous-machines/embedded-systems/jetson-orin/nano-super-developer-kit/>
- 深度・セグ・ラベリング: DepthART <https://arxiv.org/abs/2607.17099> / DA3 on Jetson <https://wiki.seeedstudio.com/deploy_depth_anything_v3_jetson_agx_orin/> / FoundationStereo <https://github.com/NVlabs/FoundationStereo> / SAM 3 <https://docs.ultralytics.com/models/sam-3/> / DINOv3 <https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m> / PIDNet <https://github.com/XuJiacong/PIDNet>
- Mac: PyTorch MPS <https://docs.pytorch.org/docs/2.14/notes/mps.html> / MLX <https://ml-explore.github.io/mlx/build/html/install.html> / CoreML <https://docs.ultralytics.com/integrations/coreml/>

## 【2026-09-16 追補 ③】外部知覚レビュー返信（責務分担・NN 方針の一致確認と修正 6 点）の反映

受領（2026-09-16・追補 ① 時点の設計への返信）: 「04 が認識結果と不確かさを出し、06・10・12 が行動を判断する分離は一致」。NN 候補の数え方（初期 3 本 = 物体 YOLOX-S / 信号用 YOLOX-S / ped MobileNetV2、PIDNet-S で 4、学習型予測で 5。YOLOX-Tiny は S の置換候補で常時 +1 ではない）は追補 ① の理解として正しい。追補 ② で物体検出の本命を RF-DETR-Nano に置き換えたため、現在の数え方は「初期 2 本（RF-DETR-Nano + ped MobileNetV2）・信号用 YOLOX-S は不足時・PIDNet-S で +1・学習型予測は後」。**「YOLO 系 nano の具体化 = YOLOX-S 確定」とは扱わない**（S / Tiny の採否は実測比較。on-device に逃がす場合のみ YOLOX 系が候補に戻る）。`batch_1 / 4 / 6` は同じ学習済モデルのバッチ数違いで、別能力の NN ではない。同じ構造でも重みと用途が違うモデルは別 manifest で管理する。学習型 traversability は具体的 NN として入っていない（深度 → 地形特徴 → 00 の車体限界と照合、で合っている）。

| # | 指摘 | 判定 | 反映（追補 ② の該当行へ同一行注記） |
|---|---|---|---|
| 1 | 8/10 多数決と点滅検出を分ける。`UNKNOWN` を消灯と同一視しない。窓は撮影時刻に基づく秒数と欠落条件で定義 | **採用**（追補 ② §5 の二段レートと同旨・語を強化） | 追補 §5: 窓 W は `source_stamp` の秒数と有効サンプル数の下限で定義しフレーム数では定義しない。`OFF_OR_UNLIT`（消灯相）と `NOT_VISIBLE` / `UNKNOWN` は別クラス。親 §4:85 の「N フレーム」は例示 |
| 2 | `clearing:false` は「その source を消去に使わない」設定で、同一 ObstacleLayer を共有する別 source の raytracing から崖セルは守られない → 06 側に崖専用レイヤを設け分離合成。clear 条件は「必要な高さ・連続性を満たす支持面の確認」 | **採用**。一次情報で確認 [D]（Humble `obstacle_layer.cpp`: 全 clearing observation を loop し `MarkCell(costmap_, FREE_SPACE)` で **layer 内 grid** に自由空間を書く・source 別の保護なし・plugin instance ごとに grid は独立で `updateCosts` が max 合成 <https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_costmap_2d/plugins/obstacle_layer.cpp>） | 07 行・2026-09-14 追補「costmap」行・`OQ-OD4H`: 崖 marking は**別 ObstacleLayer instance（例 `cliff_layer`・source = cliff_scan のみ・clearing なし）**へ分離。既存 `virtual_scan` が obstacle 層に同居する現行配線（[nav2_params.yaml:230](../../ws/src/warehouse_bringup/config/nav2_params.yaml:230)）も同じ弱点を持つ → [doc12 §virtual_scan](../architecture/12-infrastructure-common.md:547) へ申し送り（nav-traffic 所有） |
| 3 | 地図からの ROI 再投影は `roi_refinement` NN の代替ではなく併用（地図・姿勢で候補領域 → NN で補正）。RTK + 方位では足りず灯器 3D 位置・カメラ内部/外部パラメータ・撮影時点の姿勢が要る。±1° / ±0.5 m は M1 の実測誤差から | **採用**（追補 ② の「まず使わない」を「併用が基本形・NN 無しで足りるかは実測」に改める） | 03 行・`OQ-OD4F` |
| 4 | 鮮度判定を X2 だけに集中させない。認識結果を使う 06・10 も共通契約で使用時点の鮮度（`age = now − source_stamp`・`0 ≤ age < max_age`）を検査（04 停止時は「古くなった」自己申告も届かない）。`stale < 0.5 s` は曖昧・`max_age` は用途ごとの時間予算から | **採用**。失効・停止の判定点は X2（`/bot{n}/scan` は #679 Guardian）の 1 系統のまま、**使用時点の age 検査は各消費者の義務**（入力妥当性であり第 2 の停止判定点ではない） | 08 行・親 §4:85・`OQ-OD4E` |
| 5 | 凍結フレームを同一画像だけで確定しない（機器フレーム番号・計測時刻・受信状態と併用）。`UNKNOWN` は視野外・遮蔽・鮮度切れ・変換不成立・証拠不足も含む | **採用** | 2026-09-14 追補「無効深度」行に同一行注記 |
| 6 | 07 の「最終担保」は観測できる範囲に限定（2D LiDAR 走査面外・死角は止められない）。表記 = **07 幾何反射停止 / 12 MRM 判断・停止要求 / 09 駆動停止・禁止・保持**。Nav2 公式も CM は安全認証を提供しないと明記 | **採用** | 02 Objects 行・親 §5:95・HTML |
| — | `model_manifest` にモデル名・重み hash に加え入力サイズ・RGB/BGR・正規化・リサイズ方法・出力の解釈・ラベル順・TensorRT 版・評価結果を残し 1 採用単位に | **採用** | 09 行 |

- レビュー元の URL（本追補で再確認済 [D]）: Autoware classifier <https://huggingface.co/AutowareFoundation/traffic_light_classifier>／map_based_detector <https://autowarefoundation.github.io/autoware_universe/main/perception/autoware_traffic_light_map_based_detector/>／Humble Collision Monitor <https://docs.ros.org/en/humble/p/nav2_collision_monitor/>（「does not provide hard real-time safety certifications」は [09 §8](09-external-review-v3-response.md) で既出）／ByteTrack <https://github.com/FoundationVision/ByteTrack>。
- 申し送り（別担当・未修正）: doc12 §virtual_scan（同一 obstacle 層での clearing 弱点・nav-traffic 所有）。

## 【2026-09-16 追補 ④】04 出力契約 v0（型・fail 方向・消費側の義務）— contract PR

正本 = 本 doc（親 §4・2026-09-14 追補・追補 ② §1/§2/§5・追補 ③）+ [09 §2-i](09-external-review-v3-response.md:159)。本追補は **LaserScan 以外の 04 出力**（地形観測範囲・灯器観測・観測品質の自己申告）と **model_manifest** を**型として凍結**する。家は既存 `warehouse_interfaces`（additive・contract PR = [09 §3](09-external-review-v3-response.md:198)）で、実体は `ws/src/warehouse_interfaces/warehouse_interfaces/perception.py`（新規モジュール・既存 `schemas.py` は 1 文字も変えない）。

**この追補が決めないこと**（発明しない）: ①**数値しきい値**（`max_age`・距離上限・duty 比・有効画素率の下限）— `max_age` は用途ごとの時間予算から消費側が決める（[追補 ③ #4](04-perception-sidewalk-and-signals.md:382)）。②**topic 名・QoS・publish 周期** — `/bot1/terrain/coverage` は案のまま（[:173](04-perception-sidewalk-and-signals.md:173)）、灯器・地形グリッドの topic は「型未凍結」のまま（[:202](04-perception-sidewalk-and-signals.md:202)）。③**判定**（走行許可・停止・通行可否）— 04 は観測と不確かさと品質を出すだけで、判定点は X2 の 1 か所（[:203](04-perception-sidewalk-and-signals.md:203) / [09 §2-b](09-external-review-v3-response.md:72)）、通行可能形状と運用許可は 06 / 02 / 10（三分離 = [:214](04-perception-sidewalk-and-signals.md:214)）。

### 1. 型一覧（全 field・型・範囲・意味・fail 方向・出典）

列「fail 方向」は**その値が不正だったときに契約がどちらへ倒れるか**。`ValidationError` は fail-closed（不正な観測は消費側へ届かない）を意味する。すべての float は**非有限（NaN / inf）を拒否**する（`allow_inf_nan=False`）——NaN / inf は 04 が**検出すべき「品質不成立」の印**であって値ではないため（[:174](04-perception-sidewalk-and-signals.md:174)）。`extra="ignore"` はハブ既存方針（`schemas._Model`）を継承する。

#### 1-1. enum

| 型 | 値（全集合） | 意味 | fail 方向 | 出典 file:line |
|---|---|---|---|---|
| `TerrainState` | `FLOOR_CONFIRMED` / `DROP_DETECTED` / `UNKNOWN` | 地形観測の結果。`FLOOR_CONFIRMED` は**「床を観測できた」に限定**（通行可の意味を持たない）・`UNKNOWN` ≠ `DROP_DETECTED` | 未知値は `ValidationError`（許容的な既定へ落ちない） | [:173](04-perception-sidewalk-and-signals.md:173) / [:196](04-perception-sidewalk-and-signals.md:196) / [09 §2-i](09-external-review-v3-response.md:159) |
| `SignalState` | `GREEN` / `GREEN_FLASHING` / `RED` / `UNKNOWN` | 歩行者用信号の窓判定結果 | `UNKNOWN` は**禁止（fail-closed）**で、LLM / ER は `GREEN` へ昇格できない | [:85](04-perception-sidewalk-and-signals.md:85)〜[:88](04-perception-sidewalk-and-signals.md:88) / [:225](04-perception-sidewalk-and-signals.md:225) |
| `LampEvidence` | `RED` / `GREEN` / `GREEN_FLASHING` / `OFF_OR_UNLIT` / `NOT_VISIBLE` | **1 フレームの証拠**クラス（状態ではない）。`OFF_OR_UNLIT`（消灯相）と `NOT_VISIBLE`（遮蔽・欠落）は別クラス | 両者を潰すと点滅が「青」に見える＝この分離が防波堤。**`UNKNOWN` は窓判定の語彙（`SignalState`）側に置き、per-frame 証拠には持たせない**＝[:261](04-perception-sidewalk-and-signals.md:261) の 5 クラスを正とする（[:307](04-perception-sidewalk-and-signals.md:307) が `NOT_VISIBLE` / `UNKNOWN` を並記するのはこの意味） | [:261](04-perception-sidewalk-and-signals.md:261) / [:307](04-perception-sidewalk-and-signals.md:307) / [追補 ③ #1](04-perception-sidewalk-and-signals.md:379) |
| `ChannelOrder` | `RGB` / `BGR` | manifest の入力チャネル順 | 未知値は `ValidationError` | [追補 ③ 最終行](04-perception-sidewalk-and-signals.md:385) / [:204](04-perception-sidewalk-and-signals.md:204) |

#### 1-2. `ObservationQuality`（08_Quality_Evidence・自己申告）（**v0.1 additive = [追補 ⑧ §3](04-perception-sidewalk-and-signals.md:871)** で平面支持 5 field を追加・既存 field は不変）

X2 の入力型 `warehouse_safety.sensor_health.SourceObservation`（`stamp_s` / `received_monotonic_s` / `valid_fraction` / `digest`）へ**翻訳なしで渡せる**意味に揃える。`received_monotonic_s` は受信側の値なので 04 は持たない。

| field | 型・範囲 | 意味 | fail 方向 | 出典 file:line |
|---|---|---|---|---|
| `valid_fraction` | `float`・`[0,1]`・有限・**必須** | 有効観測の比率 | 範囲外・非有限は `ValidationError`。X2 側では範囲外＝`INVALID` | [:174](04-perception-sidewalk-and-signals.md:174) / [:203](04-perception-sidewalk-and-signals.md:203) / [09 §2-b](09-external-review-v3-response.md:72) |
| `frame_digest` | `str \| None`・既定 `None` | payload **内容**の指紋（凍結フレーム検出用。X2 の `digest` に対応） | `None` = この message では凍結検出を無効化する**明示の選択** | [:174](04-perception-sidewalk-and-signals.md:174) / [追補 ③ #5](04-perception-sidewalk-and-signals.md:383) |
| `device_frame_seq` | `int \| None`・`>= 0`・既定 `None` | **機器側**フレーム番号 | 負は `ValidationError`。`None` = 機器が出さない。同一画像だけで凍結を確定させないための第 2 の材料 | [追補 ③ #5](04-perception-sidewalk-and-signals.md:383) |
| `processing_latency_s` | `float \| None`・`>= 0`・有限・既定 `None` | 撮影 → 出力の経過秒（**04 が測れるのはここまで**） | 負は `ValidationError`（出力が撮影に先行しない＝時計取違え。受け入れると死んだ経路が「速い」に見える）。[:203](04-perception-sidewalk-and-signals.md:203) の「撮影 → **消費**までの遅延」は、消費側が `received − source_stamp` と併せて初めて成立する量 | [:203](04-perception-sidewalk-and-signals.md:203) |

#### 1-3. `TerrainCoverage`（07_Output_Adapters・coverage 出力）

| field | 型・範囲 | 意味 | fail 方向 | 出典 file:line |
|---|---|---|---|---|
| `source_stamp_s` | `float`・有限・**必須** | **元の**計測時刻 | 付け直し禁止。**鮮度判定にこの値を使わない**（受信側の単調時計で判定 = [09 規則 (4)](09-external-review-v3-response.md:67)） | [:177](04-perception-sidewalk-and-signals.md:177) / [09 規則 (4)](09-external-review-v3-response.md:67) |
| `reference` | `str`・非空・**必須** | 距離の基準（**車輪接地点・footprint**。車体中心ではない） | 空・空白のみは `ValidationError`（基準の無い距離は使えない） | [:176](04-perception-sidewalk-and-signals.md:176) |
| `state` | `TerrainState`・**必須** | 観測結果 | 未知値は `ValidationError` | [:173](04-perception-sidewalk-and-signals.md:173) |
| `confirmed_distance_m` | `float \| None`・`>= 0` | `FLOOR_CONFIRMED` が連続する距離 | `None` = **確認なし**（「0 m 確認」ではない）。停止距離の不等式の評価点は X2 / 09 で、04 は観測済み距離を出すだけ | [:175](04-perception-sidewalk-and-signals.md:175) / [:331](04-perception-sidewalk-and-signals.md:331) |
| `nearest_drop_distance_m` | `float \| None`・`>= 0` | 最も近い落下境界までの距離 | `None` = **未検出**（不存在ではない） | [:173](04-perception-sidewalk-and-signals.md:173) / [:176](04-perception-sidewalk-and-signals.md:176) |
| `step_height_m` | `float \| None`・符号あり | 段差高 | 下り段差があるため**符号を拘束しない** | [:196](04-perception-sidewalk-and-signals.md:196) |
| `slope` | `float \| None`・**単位未定** | 勾配 | 単位は docs が沈黙＝`OQ-OD4Y-c`（v0 は無次元のまま運ぶ） | [:196](04-perception-sidewalk-and-signals.md:196) |
| `roughness_m` | `float \| None`・`>= 0` | 凹凸 | 負は `ValidationError` | [:196](04-perception-sidewalk-and-signals.md:196) |
| `estimate_error_m` | `float \| None`・`>= 0` | 推定誤差 | 負は `ValidationError`。**どの量の誤差か・統計的意味**は docs が沈黙＝`OQ-OD4Y-c` | [:196](04-perception-sidewalk-and-signals.md:196) |
| `quality` | `ObservationQuality`・**必須** | 品質の自己申告 | 省略不可（品質を名乗らない観測を許さない） | [:203](04-perception-sidewalk-and-signals.md:203) |
| **（不在）通行可否・許可・costmap 値** | — | **持たない** | 三分離: ②通れる形状 = 06 が 00 の限界値と照合 or 02 keepout／③運用許可 = 02・10 | [:214](04-perception-sidewalk-and-signals.md:214) / [`OQ-OD4C`](04-perception-sidewalk-and-signals.md:340) |

#### 1-4. `TrafficSignalObservation`（03_Traffic_Signals・窓の観測）

| field | 型・範囲 | 意味 | fail 方向 | 出典 file:line |
|---|---|---|---|---|
| `crossing_id` | `str`・非空・**必須** | 事前登録した横断点の id | 空は `ValidationError`。registry の**正本は 02**（04 は consume して「自分の灯器」を検証するだけ） | [:79](04-perception-sidewalk-and-signals.md:79) / [:217](04-perception-sidewalk-and-signals.md:217) |
| `source_stamp_s` | `float`・有限・**必須** | 窓内**最新証拠**の元計測時刻 | 付け直し禁止。消費側が `age = now − source_stamp` を作るための唯一の材料 | [:177](04-perception-sidewalk-and-signals.md:177) / [:85](04-perception-sidewalk-and-signals.md:85) |
| `window_span_s` | `float`・`> 0`・有限・**必須** | 窓 W の**秒数** | 0・負は `ValidationError`。**フレーム数で窓を定義しない** | [:307](04-perception-sidewalk-and-signals.md:307) / [追補 ③ #1](04-perception-sidewalk-and-signals.md:379) |
| `sample_count` | `int`・`>= 0`・**必須** | 窓内の有効サンプル数（窓定義のもう半分＝下限を消費側が検査） | 負は `ValidationError` | [:307](04-perception-sidewalk-and-signals.md:307) / [追補 ③ #1](04-perception-sidewalk-and-signals.md:379) |
| `state` | `SignalState`・**必須** | 窓判定の結果 | `UNKNOWN` は禁止（fail-closed）・昇格不可 | [:85](04-perception-sidewalk-and-signals.md:85)〜[:88](04-perception-sidewalk-and-signals.md:88) |
| `is_flashing` | `bool \| None` | レート A（輝度サンプラの立ち上がりエッジ間隔）の点滅判定 | `None` = **判定不能**であり「点滅なし」ではない。消費側は不在を否定と読まない | [:307](04-perception-sidewalk-and-signals.md:307) |
| `measured_period_s` | `float \| None`・`> 0`・有限 | 実測した明滅周期 | 0・負は `ValidationError`。**0.5 s = 2 Hz は期待値であって検証境界ではない**（しきい値を型に埋めない） | [:305](04-perception-sidewalk-and-signals.md:305) / [:86](04-perception-sidewalk-and-signals.md:86) |
| `off_phase_count` | `int`・`>= 0`・**必須** | 窓内の **OFF 相**の件数 | 消費側が「**窓内 OFF 相 0 件**」を GREEN の直交条件に使う。**二段レート**（レート A = カメラ fps / レート B = 分類器）ゆえ `sample_count` との大小を**拘束しない** | [:307](04-perception-sidewalk-and-signals.md:307) |
| `roi_consistent` | `bool`・**必須** | 登録 ROI との整合 | GREEN の AND 項の 1 つ | [:85](04-perception-sidewalk-and-signals.md:85) |
| `quality` | `ObservationQuality`・**必須** | 品質の自己申告 | 省略不可 | [:203](04-perception-sidewalk-and-signals.md:203) |
| **（不在）`max_age`** | — | **持たない** | 鮮度は**消費側の義務**（使用時点で `0 ≤ age < max_age` を自分の時間予算で検査）。producer が窓を配ると停止判定点が 2 つになる | [追補 ③ #4](04-perception-sidewalk-and-signals.md:382) / [:222](04-perception-sidewalk-and-signals.md:222) |

#### 1-5. `ModelManifest` / `EvaluationRecord`（09_Runtime_and_Models・1 採用単位）

追補 ③ 最終行と追補 ② §1 09 行が挙げる項目を**全部** field にする。版**一覧**は 00 が正本で、ここには複製しない。

| field | 型・範囲 | 意味 | fail 方向 | 出典 file:line |
|---|---|---|---|---|
| `model_name` | `str`・非空 | モデル名 | 空は `ValidationError` | [追補 ③ 最終行](04-perception-sidewalk-and-signals.md:385) |
| `weights_sha256` | `str`・64 桁 hex（**暫定**） | 重み hash | 非 hex・桁違いは `ValidationError`。ただし**正本は「重み hash」としか言っておらず sha256 を指定していない**（[追補 ③ 最終行](04-perception-sidewalk-and-signals.md:385)）＝v0 は **field 名に合わせて**アルゴリズムを固定した暫定であり、裁定は `OQ-OD4Y-k` | [追補 ③ 最終行](04-perception-sidewalk-and-signals.md:385) |
| `input_size` | `tuple[int, int]`・各 `> 0` | 入力サイズ `(width, height)` | 0・負は `ValidationError` | [追補 ③ 最終行](04-perception-sidewalk-and-signals.md:385) |
| `channel_order` | `ChannelOrder` | RGB / BGR | 未知値は `ValidationError` | [追補 ③ 最終行](04-perception-sidewalk-and-signals.md:385) |
| `normalization` | `str`・非空 | 正規化 | 語彙は docs が挙げていないため自由文字列（`OQ-OD4Y-f`） | [追補 ③ 最終行](04-perception-sidewalk-and-signals.md:385) |
| `resize_method` | `str`・非空 | リサイズ方法 | 同上 | [追補 ③ 最終行](04-perception-sidewalk-and-signals.md:385) |
| `output_interpretation` | `str`・非空 | 出力の解釈 | 空は `ValidationError`（出力の読み方を言えない manifest は 1 採用単位にならない）。語彙は未定＝`OQ-OD4Y-f` | [追補 ③ 最終行](04-perception-sidewalk-and-signals.md:385) |
| `label_order` | `list[str]`・非空 | ラベル**順** | 空は `ValidationError`（index 2 が何かを言えない manifest は manifest ではない） | [追補 ③ 最終行](04-perception-sidewalk-and-signals.md:385) |
| `onnx_opset` | `int`・`> 0` | ONNX opset | 0・負は `ValidationError` | [:204](04-perception-sidewalk-and-signals.md:204) |
| `tensorrt_version` | `str` | TensorRT 版 | **空を拒否しない**（engine をまだ焼いていない採用単位＝ONNX 止まりが有りうる。焼いたか否かは `engine_built_on_board` が記録する） | [追補 ③ 最終行](04-perception-sidewalk-and-signals.md:385) |
| `gpu_arch` | `str` | GPU arch | `tensorrt_version` と併せて「engine が可搬でない」理由そのもの。**空を拒否しない**（理由は同上＝engine 未作成時に空が正当） | [:204](04-perception-sidewalk-and-signals.md:204) / [`OQ-OD4U`](04-perception-sidewalk-and-signals.md:358) |
| `license` | `str`・非空 | ライセンス | 空は `ValidationError`（配布可否＝AGPL 境界は manifest と一緒に動く以上、「不明」を黙って通さない） | [`OQ-OD4N`](04-perception-sidewalk-and-signals.md:351) |
| `engine_built_on_board` | `bool` | engine をボード上で焼いたか | `OQ-OD4U`「engine は必ずボード上で焼く」を記録可能にする | [`OQ-OD4U`](04-perception-sidewalk-and-signals.md:358) |
| `evaluation` | `EvaluationRecord`・**必須** | 評価結果 | **省略不可**＝評価の無いモデルは 1 採用単位にならない | [追補 ③ 最終行](04-perception-sidewalk-and-signals.md:385) / [:204](04-perception-sidewalk-and-signals.md:204) |
| `EvaluationRecord.dataset_id` | `str`・非空 | bag / dataset id | 空は `ValidationError`（出所の無い数値は証拠にならない） | [:204](04-perception-sidewalk-and-signals.md:204) |
| `EvaluationRecord.metrics` | `dict[str, float]`・全要素有限 | 指標（混同行列を含む） | 非有限は `ValidationError`。**キー集合は docs 未定**＝`OQ-OD4Y-e`（形を発明せず開いた map のまま） | [:204](04-perception-sidewalk-and-signals.md:204) / [親 §7 P-1](04-perception-sidewalk-and-signals.md:117) |

### 2. 消費側の義務（型に埋めなかったもの）

1. **鮮度**: 使用時点で `age = now − source_stamp_s`・`0 ≤ age < max_age` を**消費側が**検査する（[追補 ③ #4](04-perception-sidewalk-and-signals.md:382)）。`max_age` はその消費者の時間予算から決め、04 は配らない。これは**入力妥当性**であって第 2 の停止判定点ではない。
2. **鮮度の時計**: `source_stamp_s` は**元計測時刻**であり、staleness は**受信側の単調時計**で測る（[09 規則 (4)](09-external-review-v3-response.md:67)）。両者を取り違えると死んだ source が永久に fresh に見える。
3. **`None` の読み方**: `confirmed_distance_m=None` は「0 m 確認」ではなく、`nearest_drop_distance_m=None` は「崖が無い」ではなく、`is_flashing=None` は「点滅していない」ではない。**証拠の不在を否定と読まない**。
4. **GREEN の直交条件**: `off_phase_count == 0` ∧ `roi_consistent` ∧ 鮮度、を消費側（L2 横断ゲート = 10）が AND する（[:307](04-perception-sidewalk-and-signals.md:307) / [:85](04-perception-sidewalk-and-signals.md:85)）。
5. **判定点の一意性**: 品質の**判定**は X2 の 1 か所（[:203](04-perception-sidewalk-and-signals.md:203)）。04 の自己申告値を別の場所で二度判定しない（[`OQ-OD4E`](04-perception-sidewalk-and-signals.md:342)）。
6. **`ValidationError` の読み方**: 本契約の検証失敗は「**観測なし**（`UNKNOWN` 相当・fail-closed）」として扱い、**safety loop の外で捕捉する**。X2 側の `sensor_health` は「データで例外を上げない」方針（不読値は `NaN` として保持し verdict で決定的に分類する＝[`evaluate` は data で raise しない](../../ws/src/warehouse_safety/warehouse_safety/sensor_health.py:187)）なので、例外を安全ループへ持ち込まないのは**呼び出し側の責務**。

### 3. OPEN QUESTIONS（本追補で**発明せずに残した**もの）

- `OQ-OD4Y-a` `ObservationQuality.frame_digest` と X2 `SourceObservation.digest` の**名前差**（意味は同一）。配線時にどちらへ寄せるか（本 v0 は `frame_digest` のまま）。`source_stamp_s` ↔ `stamp_s` も同型。 **→ 裁定済（2026-09-18）= [追補 ⑪ §1](04-perception-sidewalk-and-signals.md:1024)**: **どちらへも寄せない**（両側の field 名を据え置き・改名は破壊的＝[parallel-workflow.md §7.2](../../.claude/rules/parallel-workflow.md:192)）。綴りの差は `warehouse_safety.terrain_health`（アダプタ **1 か所**・L1 純ロジック）が吸収する。`source_stamp_s` ↔ `stamp_s` も同じ扱い。
- `OQ-OD4Y-b` `valid_fraction` を**必須・非 NaN** にしたため、「比率を計算できない producer」の表現が無い（X2 側は NaN を「計算できなかった」として `INVALID` 扱いにできる）。送らない／`0.0` を送る／optional 化のどれを契約にするか。**範囲外値（例 `valid_fraction=1.5`）の扱いも方向が逆**: X2 は「送信者が実際に送った実数」として保持し `INVALID` と判定するが（[`SourceObservation` docstring](../../ws/src/warehouse_safety/warehouse_safety/sensor_health.py:206)）、04 契約は入口で拒否する。どちらを正にするかも同じ裁定に含める。
- `OQ-OD4Y-c` `slope` の**単位**（rad / deg / 比）と `estimate_error_m` が**どの量の誤差**か（距離か高さか）・統計的意味（1σ か最大値か）。docs は field の存在だけを言い単位を pin していない（[:196](04-perception-sidewalk-and-signals.md:196)）。
- `OQ-OD4Y-d` `sample_count` が**どのレートのサンプル**を数えるか（レート A = カメラ fps / レート B = 分類器 / 両方）。二段レート（[:307](04-perception-sidewalk-and-signals.md:307)）ゆえ `off_phase_count` との大小関係を契約にできない。
- `OQ-OD4Y-e` `EvaluationRecord.metrics` の**キー集合**（混同行列をどう平坦化するか。親 §7 P-1 の「青点滅→青 / 赤→青 = 0 件」を機械可読にする形）。
- `OQ-OD4Y-f` `normalization` / `resize_method` / `output_interpretation` を自由文字列のままにするか enum 化するか（実装が 1 本に決まってから）。
- `OQ-OD4Y-g` `ModelManifest.evaluation` を**必須**にしたため、評価前の draft manifest を表現できない（1 採用単位 = [追補 ③ 最終行](04-perception-sidewalk-and-signals.md:385) を優先した）。draft 状態を別型にするか optional へ緩めるか。
- `OQ-OD4Y-h` `reference` の**語彙**（`frame_id` か固定ラベルか）。docs は「車輪接地点・footprint」と言うが値を pin していない（[:176](04-perception-sidewalk-and-signals.md:176)）。
- `OQ-OD4Y-i` **topic 名・QoS・publish 周期**は本追補の射程外（`/bot1/terrain/coverage` は案 = [:173](04-perception-sidewalk-and-signals.md:173)、灯器・地形グリッドは型未凍結 = [:202](04-perception-sidewalk-and-signals.md:202)）。`cliff_scan` の `source_timeout` は別件（[`OQ-OD44`](04-perception-sidewalk-and-signals.md:142)）。 **→ 裁定済（2026-09-18）= [追補 ⑨](04-perception-sidewalk-and-signals.md:926)**（topic 名 `cliff_scan` / `terrain/coverage` を `/bot{n}` 相対名で凍結・QoS depth 10・周期 = depth frame ごとに 1 回。doc03 カタログ行は [03 末尾追補](../architecture/03-software-architecture.md:316)）。灯器・地形グリッドの topic は**依然 未凍結**（本裁定の射程外）。
- `OQ-OD4Y-j` `LampEvidence` は型として置いたが、**per-frame 証拠を topic として外へ出すか**（04 内部に留めるか）は未決。出すなら `TrafficSignalObservation` に窓内の証拠列を additive で足すことになる。
- `OQ-OD4Y-k` **重み hash のアルゴリズムを sha256 に固定するか**（正本は [追補 ③ 最終行](04-perception-sidewalk-and-signals.md:385) で「重み hash」としか言わず、アルゴリズムを指定していない）。v0 は field 名 `weights_sha256` に合わせて **64-hex を強制する暫定**で、blake3 等へ変えるなら field 名ごと contract PR で改める。

> 本追補は**型と fail 方向のみ**を凍結する。`OQ-OD4B`（11 sub-dir）・`OQ-OD4C`（三分離の一本化）・`OQ-OD4E`（二重化回避）・`OQ-OD4H`（consumer 側実装の所有）は**本追補では裁定しない**。用語は [GLOSSARY §12](../GLOSSARY.md)（地形観測範囲 / 灯器観測 / 観測品質の自己申告 / model manifest）を正準とする。

## 【2026-09-17 追補 ⑤】01_Geometry / 07 coverage・cliff 純ロジック v0（実装記録・P1 レーンが記入）

正本 = 本 doc（[§3](04-perception-sidewalk-and-signals.md:49) / [:56](04-perception-sidewalk-and-signals.md:56)・[2026-09-14 追補 :173](04-perception-sidewalk-and-signals.md:173)〜[:181](04-perception-sidewalk-and-signals.md:181)・[追補 ② §1 01_Geometry 行](04-perception-sidewalk-and-signals.md:196) / [§2 01 terrain 行](04-perception-sidewalk-and-signals.md:214) / [§8 順序 2](04-perception-sidewalk-and-signals.md:326) / [:331](04-perception-sidewalk-and-signals.md:331)・[追補 ③ #5](04-perception-sidewalk-and-signals.md:383)）＋ **追補 ④**（型 = `TerrainCoverage` / `ObservationQuality` / `TerrainState`）＋ [09 §2-i](09-external-review-v3-response.md:157) / [09 §4 順序 5](09-external-review-v3-response.md:213) / [09 §5 F1](09-external-review-v3-response.md:221)・[F5](09-external-review-v3-response.md:225)。

本追補が記録するのは **純ロジックの入出力・パラメータ・状態決定規則・fail 方向・残 OQ** だけで、node / topic / QoS / 周期 / しきい値の既定値は**含まない**（含めれば正本に無い数値を発明することになる＝[docs-first](../../.claude/rules/docs-first.md)）。

実体（本スライスで着地）:

- [`ws/src/warehouse_perception/warehouse_perception/terrain_core.py`](../../ws/src/warehouse_perception/warehouse_perception/terrain_core.py) — **rclpy 非依存・numpy 非依存**の純ロジック（CI の python に numpy が無いため `math` と list のみ）。
- [`tests/unit/test_terrain_core.py`](../../tests/unit/test_terrain_core.py) — R-26 unit（`unit` + `safety`・独立オラクル・合成シーンの生成器はテスト側・mutation で赤くなることを確認済 = [doc20 §9](../architecture/20-dev-quality-and-testing.md:131)）。
- produce / consume の記録は [`warehouse_perception/CLAUDE.md`](../../ws/src/warehouse_perception/CLAUDE.md) の P1 節（家は既存 `warehouse_perception` = [09 §3](09-external-review-v3-response.md:200)）。

> **レイヤ注記**: 01_Geometry・07 coverage / cliff_scan = **自律走行（安全層外）の producer**。consumer は L1 costmap / L1 collision_monitor（`cliff_scan`）と X2・09・06（coverage）。本スライスに node・topic・launch・config は無い（**0 node・0 `cmd_vel`**・AST で pin）。停止距離の不等式の評価点は **X2 / 09 の 1 か所**のままで 04 は観測済み距離を出すだけ（[:331](04-perception-sidewalk-and-signals.md:331)）、品質も**自己申告のみ**で判定は X2（[:203](04-perception-sidewalk-and-signals.md:203)）。センサ固有定数を焼かないため、同じ純ロジックが室内 HP60C でも屋外 OAK-D でも使える。

### 1. 段と入出力

| # | 関数 | 入力 | 出力 | 役割 |
|---|---|---|---|---|
| 1 | `depth_validity` | 深度画像（row-major・m） | `DepthValidity`（`valid_pixels` / `valid_fraction` / `frame_digest`） | 画素単位で `None` / `NaN` / `±inf` / `<= 0` / 非数値を無効化（[:174](04-perception-sidewalk-and-signals.md:174)）。`frame_digest` は payload **内容**の sha256 ＝ 凍結フレームの**材料**であって確定ではない（確定は X2 = [追補 ③ #5](04-perception-sidewalk-and-signals.md:383)） |
| 2 | `back_project` | 有効画素 + 内部パラメータ + 取付幾何 | body 系点列（X 前・Y 左・Z 上・原点はカメラ直下の地面） | ピンホール逆投影。光軸は `Z = 0` と `h / tan θ` で交わる（[:181](04-perception-sidewalk-and-signals.md:181) の worked example と一致することを unit で確認） |
| 3 | `ground_estimator` | 点列 | `GroundPlane`（`Z = aX + bY + c` + 支持 + `used_prior`・**v0.1 で `rejected_candidates` 追加** = [追補 ⑧ §3](04-perception-sidewalk-and-signals.md:871)） | RANSAC 平面（[案 B :44](04-perception-sidewalk-and-signals.md:44)）。**事前値 = 取付幾何が言う `Z = 0`**（h・θ は段 2 で既に適用済）で、フィットは観測で上書きする。`ransac_seed` 注入ゆえ決定的 |
| 4 | `classify_cells` | 点列 + 平面 | `TerrainGrid`（cell → `TerrainState` + 証拠数 + 最小残差） | 前方メトリックグリッド（`cell_size_m` 正方）。**横方向は無制限**＝回廊外の崖も `cliff_scan` 経由で costmap へ届く |
| 5 | `terrain_coverage` | grid（**v0.1 で `ground: GroundPlane` が必須 kwarg** = [追補 ⑧](04-perception-sidewalk-and-signals.md:838)。採用平面が無いと事前値フォールバックを申告できないため） | `TerrainCoverage`（凍結契約・追補 ④） | **回廊内だけ**を見る。距離は datum 起点・`cell_size_m` 量子化 |
| 6 | `cliff_ranges` | grid | `CliffScan`（LaserScan 形 dataclass・未 publish） | `DROP_DETECTED` cell **だけ**を方位別 range へ。**`UNKNOWN` は落とさない**（[:173](04-perception-sidewalk-and-signals.md:173)）。方位は cell 中心、**range は cell の手前端**（`hypot(ix × cell_size_m, y_centre)` ＝ 手前端の X と cell 中心の Y までの**斜距離**。方位と整合させるため Y を落とさない）＝誤差は必ずロボット側へ `cell_size_m` 以内（中心を使うと仮想壁が観測した縁より最大で半 cell **奥**に立つ） |
| 7 | `analyze_depth_frame` | 深度画像 | `TerrainObservation` | 1〜6 の順序と `source_stamp_s` / digest の受け渡しを 1 か所に固定（将来の node が包む seam） |

### 2. パラメータ（**全て注入・既定なし**・違反は構築時 `ValueError`）（→ v0.1 で `GroundFitParams` に 2 param 追加 = [追補 ⑧ §2](04-perception-sidewalk-and-signals.md:861)。**本表には行を足さず**差分は末尾 [追補 ⑤-1](04-perception-sidewalk-and-signals.md:907) に置く＝下流 pin の行ズレ回避）

既定を置かないのは、正本が数値を pin していないため（縁石 2 cm は実測ゲート [`OQ-OD45` :143](04-perception-sidewalk-and-signals.md:143)、下向き MinZ は [`OQ-OD4Q` :354](04-perception-sidewalk-and-signals.md:354) / [:301](04-perception-sidewalk-and-signals.md:301) で未実測）。流儀は [`sensor_health.py`](../../ws/src/warehouse_safety/warehouse_safety/sensor_health.py) と同じ（しきい値は constructor 引数・既定なし）。

| dataclass | パラメータ | 単位 | 検証 | 備考・出典 |
|---|---|---|---|---|
| `CameraIntrinsics` | `fx` / `fy` | px | 有限・`> 0` | 除数。カメラ個体の校正値 |
| | `cx` / `cy` | px | 有限・`>= 0` | 主点 |
| `MountingGeometry` | `camera_height_m`（`h`） | m | 有限・`> 0` | [:181](04-perception-sidewalk-and-signals.md:181)。車体未組立・未実測 |
| | `pitch_down_rad`（`θ`） | rad | 有限・`(0, π/2)` 開区間 | `0` では光軸が地面に届かず（`h / tan θ` が発散）、`π/2` では前方距離が潰れる＝両端とも設定の誤り。§2 の「下向き 10〜20°」は案であり実測で決める |
| | `reference_offset_m` | m | 有限（**符号自由**） | カメラ原点 → **車輪接地点 / footprint** までの前方距離。カメラが footprint 前端より前なら負になるため正に拘束しない（[:176](04-perception-sidewalk-and-signals.md:176)） |
| `TerrainGridParams` | `cell_size_m` | m | 有限・`> 0` | 本モジュールが返す全距離の量子 |
| | `corridor_half_width_m` | m | 有限・`> 0` かつ **`cell_size_m / 2` 以上**（= 回廊は**最小 1 bin**。横 bin は「中心が半幅の内側」で選ぶので、最内の中心は `cell_size_m / 2` にある。これを下回ると bin が 0 本になり、永久に `UNKNOWN` しか出せない「確認できない設定」になるため構築時に拒む） | 「今から踏む領域」の半幅（[:175](04-perception-sidewalk-and-signals.md:175)）。footprint の正本は 00 で、04 に複製しない（[:195](04-perception-sidewalk-and-signals.md:195)） |
| | `forward_range_m` | m | 有限・`> 0`・1 cell 以上 | 回廊長 = `floor(forward_range_m / cell_size_m) × cell_size_m`（端数 cell を作らない） |
| | `min_points_per_cell` | 点 | `int >= 1` | `noDataObstacle` の下限（[:56](04-perception-sidewalk-and-signals.md:56)）。`0` は「誰も見ていない床を確認済にする」ため拒否 |
| | `drop_threshold_m` | m | 有限・`> 0`・**`plane_tolerance_m` より大** | `considerDrop` の深さ。`OQ-OD45` は**目標**であって既定にしない。2 帯が重なると同じ点が「床」と「崖」を同時に名乗るため `TerrainParams` で相互検証 |
| | `min_valid_fraction` | 比 | 有限・`(0, 1]` | 有効画素過少（[:174](04-perception-sidewalk-and-signals.md:174)）。`0` は「有効画素ゼロを受け入れる」ため拒否 |
| `GroundFitParams` | `plane_tolerance_m` | m | 有限・`> 0` | RANSAC の inlier 帯 **かつ**「床を観測できた」帯。同じ言明ゆえ **1 本**（2 本目は発明になる＝`OQ-OD4Z-b`） |
| | `ransac_iterations` | 回 | `int >= 1` | |
| | `ransac_seed` | — | `int` | 決定性（[doc20 §9](../architecture/20-dev-quality-and-testing.md:131)） |
| `CliffScanParams` | `angle_min_rad` / `angle_max_rad` | rad | 有限・`max > min` | 室内 VirtualScan の定数（±15°・2.0 m・360 本 = [virtual_scan_logic.py:19](../../ws/src/warehouse_traffic/warehouse_traffic/virtual_scan_logic.py:19)）は **1.8 m ジオラマの値なのでコピーしない** |
| | `ray_count` | 本 | `int >= 1` | `angle_increment = (angle_max − angle_min) / ray_count`・ray `i` は `[angle_min + i·inc, angle_min + (i+1)·inc)` |
| | `range_min_m` / `range_max_m` | m | 有限・`0 < min < max` | |

### 3. 状態決定規則（**暫定**・`OQ-OD4C` の裁定前）

**cell**（残差 `r = Z − plane(X, Y)`・上が正）——この順で評価する:

1. `点数 < min_points_per_cell` → `UNKNOWN`（= `noDataObstacle`・未観測 = 通行不可）。**最初に**見るので、わずかな点で床も崖も確定しない。
2. `r <= -drop_threshold_m` の点を 1 つでも含む → `DROP_DETECTED`（= `considerDrop`）。危険の**正の観測**は平均で薄めない。
3. `|r| <= plane_tolerance_m` の点が `min_points_per_cell` 以上 → `FLOOR_CONFIRMED`。数えるのは**床点**なので、床より高い点しか無い cell（正障害物が床を隠している）は 3 を満たさず 4 へ落ちる（正障害物そのものは `obstacle_scan` 経路で本スライス対象外）。
4. それ以外 → `UNKNOWN`。

**行（forward index）**: 回廊の横 bin（中心が `|y| <= corridor_half_width_m`）を集約し、①どれか 1 つでも `DROP_DETECTED` なら行は `DROP_DETECTED`／②**全部**が `FLOOR_CONFIRMED` のときだけ `FLOOR_CONFIRMED`／③それ以外は `UNKNOWN`。**1 レーンでも未観測なら床は名乗らない**（[:175](04-perception-sidewalk-and-signals.md:175)）。

**coverage**:

- `confirmed_distance_m` = datum から**連続**する `FLOOR_CONFIRMED` 行数 × `cell_size_m`。先頭行が未確認なら `None`（= 確認なし。「0 m 確認」ではない = [追補 ④ :427](04-perception-sidewalk-and-signals.md:427)）。**gap の向こうの床は数えない**（届かない床は確認済ではない）。
- `nearest_drop_distance_m` = 最も近い `DROP_DETECTED` 行の**手前端**（量子化は必ずロボット側へ倒す）。`None` = 未検出であって「崖が無い」ではない。
- `step_height_m` = その行の最小残差（**符号あり**＝下りは負・[:196](04-perception-sidewalk-and-signals.md:196)）。
- `state` = 回廊に落下があれば `DROP_DETECTED`／無ければ `confirmed_distance_m > 0` で `FLOOR_CONFIRMED`／それ以外 `UNKNOWN`。`UNKNOWN` ≠ `DROP_DETECTED` は保つ（一本化は [`OQ-OD4C` :340](04-perception-sidewalk-and-signals.md:340)）。
- `slope` / `roughness_m` / `estimate_error_m` は **v0 では常に `None`**（単位・統計的意味が未定 = `OQ-OD4Y-c`。発明しない）。
- `source_stamp_s` は入力のまま（付け直さない・[:177](04-perception-sidewalk-and-signals.md:177)）。鮮度は受信側の単調時計で測る（[09 規則 (4)](09-external-review-v3-response.md:67)）。`reference` は呼び出し側が与える文字列（語彙は `OQ-OD4Y-h`）。

**有効画素率ゲート**: `valid_fraction < min_valid_fraction` のとき品質不成立（[:174](04-perception-sidewalk-and-signals.md:174)）→ **床の主張だけ**を取り下げる（`confirmed_distance_m = None`・`FLOOR_CONFIRMED` にしない）。**落下の証拠は消さない**——消すと `UNKNOWN` は LaserScan に落ちない（[:173](04-perception-sidewalk-and-signals.md:173)）ため崖が costmap から消える＝fail-open になる。これは 04 自身の**主張**の抑制であって走行許可の判定ではない（判定点は X2 の 1 か所 = [:203](04-perception-sidewalk-and-signals.md:203) / [`OQ-OD4E` :342](04-perception-sidewalk-and-signals.md:342)）。

> **MinZ の帰結**: 下向きカメラの最小測距距離（[`OQ-OD4Q` :354](04-perception-sidewalk-and-signals.md:354) / [:301](04-perception-sidewalk-and-signals.md:301)）で回廊の**先頭**が構造的に見えない取付だと、先頭行が `UNKNOWN` になり `confirmed_distance_m` は `None` のままになる。本モジュールはそれを埋めずにそのまま出す（埋めれば未観測を確認済に化けさせる）。取付を変えるか観測範囲外へ進ませない（[09 §2-h](09-external-review-v3-response.md:155)）のは 05 / 06 側の判断。

### 4. fail 方向

| 事象 | 出方 | 理由 |
|---|---|---|
| 深度が `NaN` / `inf` / `0` / 空 / 非数値 / `bool` | **例外を上げず** `UNKNOWN` ＋ `valid_fraction` 低下（空 payload は `0.0`） | safety loop に data 例外を持ち込まない（[追補 ④ §2-6](04-perception-sidewalk-and-signals.md:482)・[`sensor_health.evaluate`](../../ws/src/warehouse_safety/warehouse_safety/sensor_health.py) と同方針）。F5 = [09 :225](09-external-review-v3-response.md:225) |
| パラメータが非有限・非正・型違い（`bool` 含む） | 構築時に `ValueError` | 呼び出し側の誤りは呼び出し側で落とす。既定で埋めない |
| 証拠不足 cell | `UNKNOWN`（`DROP` ではない） | 未観測 = 通行不可の扱いは下流（[:214](04-perception-sidewalk-and-signals.md:214)）。欠測を落下に格上げしない |
| 正障害物で床が見えない cell | `UNKNOWN` | `FLOOR_CONFIRMED` は「床を観測できた」に限定（[:196](04-perception-sidewalk-and-signals.md:196)） |
| 量子化 | 崖は**手前**へ（`cliff_scan` の range も coverage の距離も cell の**手前端**・誤差 ≤ `cell_size_m`）・床は**短く** | 過小申告側へ倒す |
| `UNKNOWN` cell | `cliff_scan` に**入れない** | [:173](04-perception-sidewalk-and-signals.md:173)。未観測を壁に化けさせない（代わりに coverage で出す） |
| 崖が scan の角度 / 距離窓の外 | `omitted_cell_count` に計上 | 黙って捨てない＝設定不整合を消費側が見られる。ただし **`sensor_msgs/LaserScan` に載る field ではない**ため、node 配線までは wire に出ない（診断へどう出すかは配線時） |
| 品質不成立 | 床の主張のみ取り下げ・落下は保持 | 上記「有効画素率ゲート」 |

### 5. OPEN QUESTIONS（本追補で**発明せずに残した**もの・接頭辞 `OQ-OD4Z-*`）

- `OQ-OD4Z-a` **落下 cell の必要証拠点数**。v0 は「`min_points_per_cell` を満たす cell に `drop_threshold_m` 以深の点が **1 つでも**あれば `DROP_DETECTED`」。正本は「地面推定より下の点」（[:56](04-perception-sidewalk-and-signals.md:56)）としか言わず点数を pin していない。1 点のノイズで仮想壁を撃つ感度を許すか、`min_drop_points` を別に置くかは未決。
- `OQ-OD4Z-b` **`plane_tolerance_m` が RANSAC inlier 帯と「床を観測できた」帯を兼ねる**。正本は両者を区別していない。分けるなら 2 本目のしきい値の出所が要る。
- `OQ-OD4Z-c` **`min_valid_fraction` の正本の置き場**。同名・同義の値が 04 側（自分の主張の抑制）と X2 側（[`SourceThresholds.min_valid_fraction`](../../ws/src/warehouse_safety/warehouse_safety/sensor_health.py)）の 2 か所にある。v0 は「04 は主張の抑制のみ・許可の判定はしない」で役割を分けたが、値の正本と二重化回避は [`OQ-OD4E` :342](04-perception-sidewalk-and-signals.md:342) の射程。
- `OQ-OD4Z-d` **素の RANSAC の多数派が真の地面とは限らない（fail-open の実例を確認）**。回廊の中ほどが欠測した下り段差シーンでは、近傍の床と遠方の下段面を通る**傾いた**平面（実測 `a ≈ −0.044`／m）が水平面より inlier が多くなり、崖が `FLOOR_CONFIRMED` に見える。v0 は [案 B :44](04-perception-sidewalk-and-signals.md:44)「RANSAC 地面」のままで、法線の事前拘束（取付 `θ` からの逸脱上限）・支持の下限・[:182](04-perception-sidewalk-and-signals.md:182) の「地面傾き補正」の具体化は未実装＝しきい値が正本に無いため発明しない。**node 化前に裁定が要る**（親 §7 P-2・[§8 順序 2](04-perception-sidewalk-and-signals.md:326) の実測ゲートと同時に）。**現状 `TerrainCoverage` は平面の品質（傾き・支持）を運ばないため、この誤りは下流 X2 からは観測できない**——裁定時は「取付 `θ` からの傾き上限を注入する」か「平面支持を `ObservationQuality` 側へ additive で自己申告する」かのどちらかが要る（後者は追補 ④ の contract PR になる）。 **→ 裁定済（2026-09-17）= [追補 ⑧](04-perception-sidewalk-and-signals.md:838)**（両方を採る: 傾き / 原点高さの上限を注入する constrained RANSAC ＋ `ObservationQuality` v0.1 additive。値は既定なし・実測待ち）。
- `OQ-OD4Z-e` **回廊長の端数**は切り捨て（`floor`）。切り上げ＝過大申告側は取らないが、正本は端数に沈黙している。
- `OQ-OD4Z-f` **性能**。純 python（CI に numpy が無い）で 1 フレーム全画素を走査する。実解像度・実周期での実行時間は**未計測**。node 化時に numpy 経路を optional で足すか、間引き（`stride`）を注入 param にするかは未決（[02 §4](02-architecture-split-orin-pc-cloud.md) の屋外 S1 実測と同時）。
- `OQ-OD4Z-g` **座標系の契約**。本モジュールは body 系（X 前・Y 左・Z 上・原点はカメラ直下の地面）を内部で定義する。ROS `frame_id` / TF との対応（TF 配信責任 = [03 §2-1](03-localization-gnss-and-ekf.md)）と `reference` の語彙（`OQ-OD4Y-h`）は node 化時に決める。
- `OQ-OD4Z-h` **`cliff_scan` の角度窓・距離窓・publish 周期**は注入のままで pin していない（topic 名 = `OQ-OD4Y-i`、`source_timeout` の扱い = [`OQ-OD44` :142](04-perception-sidewalk-and-signals.md:142)）。
- `OQ-OD4Z-i` **凍結フレームの確定は 04 では行わない**（`frame_digest` と `device_frame_seq` を渡すだけ＝[追補 ③ #5](04-perception-sidewalk-and-signals.md:383)）。04 側で `frame_digest=None`（凍結検出を無効化する明示の選択）を選ぶ条件は未定。
- `OQ-OD4Z-j` **obstacle_geometry / terrain_features は未実装**。追補 ② §1 の 01_Geometry は 5 要素（`depth_validity` / `ground_estimator` / `obstacle_geometry` / `terrain_features` / `terrain_coverage`）だが、本スライスは崖・coverage に必要な 3 つだけを実装した（正障害物は `obstacle_scan` 経路・`terrain_features` は `slope` / `roughness` の単位が未定 = `OQ-OD4Y-c` 待ち）。



<!-- ▲ 上のプレースホルダを埋めるのは担当レーンのみ。以下の空行は隣接プレースホルダとの
     git hunk 分離用（既定 context 3 行 × 2 を超える間隔を確保し、P1/P2/P3 が同時に埋めても
     同一 hunk にならないようにしている）。詰めない・他レーンの節を書き換えない。 -->



## 【2026-09-17 追補 ⑥】03_Traffic_Signals 二段レート時系列判定 純ロジック v0（実装記録・P2 レーンが記入）

実装 = **`ws/src/warehouse_perception/warehouse_perception/signal_temporal_core.py`**（**L4** 知覚・publish-only・**0 actuation**。横断の許可は L2 横断ゲート（10）が `state == GREEN` ∧ 鮮度 ∧ 承認トークンを AND する＝[:85](04-perception-sidewalk-and-signals.md:85) / [追補 ④ §2 項目 4](04-perception-sidewalk-and-signals.md:480)）。契約は本 doc 追補 ④ の `TrafficSignalObservation` / `ObservationQuality` をそのまま出力し、**型は 1 文字も変えていない**（契約変更なし）。R-26 unit = `tests/unit/test_signal_temporal_core.py`。

**本スライスが作らないもの**: カメラ・分類器・ROI 投影・露出固定・node / topic / launch / config。レート A（ROI 輝度サンプラ）とレート B（分類器）の**出力を入力として受けるだけ**の純ロジックで、`rclpy` / `numpy` に依存しない（AST pin）。日本の公開歩行者用信号データセットが無く（[:261](04-perception-sidewalk-and-signals.md:261)）点滅を直接クラス化した公開モデルも無い（[:262](04-perception-sidewalk-and-signals.md:262)）ためオラクルは**テスト側の合成時系列**で、実 bag 比較は P3 評価基盤 + ハード到着後。

#### 1. 入力型（どちらも他段が作る）

| 型 | レート | field | 意味 |
|---|---|---|---|
| `LuminanceSample` | **A**（カメラ fps） | `stamp_s` / `green_ratio` / `red_ratio` / `exposure` | ROI の緑・赤 hue 面積比 g_t / r_t と露出値 e_t（[:307](04-perception-sidewalk-and-signals.md:307)）。`stamp_s` は元計測時刻・付け直し禁止（[:177](04-perception-sidewalk-and-signals.md:177)） |
| `EvidenceSample` | **B**（分類器 f_B） | `stamp_s` / `evidence: LampEvidence` / `roi_consistent` | 1 フレームの証拠クラス（[:261](04-perception-sidewalk-and-signals.md:261) の 5 クラス。**状態ではない**）と登録 ROI との整合（[:85](04-perception-sidewalk-and-signals.md:85)） |

#### 2. パラメータ表（**全て注入・既定なし**・単位付き）

コードに置いてよい数値定数は **`NOMINAL_FLASH_PERIOD_S = 0.5 s`**（[D] 一次情報 = 警察庁 仕様書 / 科警研 2019・[:305](04-perception-sidewalk-and-signals.md:305)）**ただ 1 つ**。これは**期待値であって検証境界ではない**（追補 ④ `measured_period_s` 行）ので、許容幅は `period_tolerance_s` として外から入れる。以下はすべて構築時に検証し、使えない値は `SignalTemporalConfigError`（`ValueError`）で**その場**で落とす（判定の中では落とさない）。

| パラメータ | 単位 | 制約（構築時） | 出典・既定を置かない理由 |
|---|---|---|---|
| `window_span_s` | s | 有限・> 0 | 窓 W は `source_stamp` 基準の**秒数**で定義しフレーム数で定義しない（[:307](04-perception-sidewalk-and-signals.md:307) / [追補 ③ #1](04-perception-sidewalk-and-signals.md:379)）。値は docs 未定 |
| `min_samples` | 件 | >= 1 | 窓定義のもう半分＝有効サンプル数の下限（[:307](04-perception-sidewalk-and-signals.md:307)）。0 を許すと「0 件から GREEN」が成立し fail-open |
| `majority_fraction` | 比 | 0.5 < f <= 1.0 | 親 §4 の「直近 N フレーム（例 10）」が**例示**とされた（[追補 ③ #1](04-perception-sidewalk-and-signals.md:379)）以上、そこに乗る 8/10 も固定値として扱わない（N が例示なら share だけを凍結する根拠が無い）。0.5 以下は多数決ではなく、RED と GREEN が同時に「勝つ」ため排他性が壊れる |
| `classifier_rate_hz` | Hz | 有限・> 0・**2 Hz の整数倍でない** | f_B は点滅周期と非整数比にする（例 9 Hz = 4.5 倍。**10 / 12 / 30 Hz は罠**＝[:307](04-perception-sidewalk-and-signals.md:307) / [:306](04-perception-sidewalk-and-signals.md:306)）。判定に使わず**構築時に拒否**する。判定式は `f_B × NOMINAL_FLASH_PERIOD_S` が整数か（＝定数を 1 つに保つ導出） |
| `period_tolerance_s` | s | 有限・0 < t < 0.5 | 0.5 s 周りの許容半幅。docs は許容幅を pin していない（`OQ-OD4Z-c`）。0.5 以上だと帯が非正の周期に届く |
| `on_threshold` / `off_threshold` | 比 | 0 <= off < on <= 1 | ヒステリシス二値化（[:307](04-perception-sidewalk-and-signals.md:307)）。差＝ヒステリシス幅で、0 以下ではノイズでチャタる。値は露出・ROI 実測後 |
| `exposure_tolerance` | 露出値の幅 | 有限・>= 0 | 窓内で露出が動いたら**判定不能**に倒す。自動露出と LED PWM のエイリアシングが偽 OFF を作るため（露出固定 = [:308](04-perception-sidewalk-and-signals.md:308) / [`OQ-OD4L`](04-perception-sidewalk-and-signals.md:349)）。固定値は実測後 |
| `red_sync_delta` | 比 | 有限・>= 0 | 「赤が同期して増えていない」の許容（[:307](04-perception-sidewalk-and-signals.md:307)）。超えたら全体照度の変動疑いとして点滅判定を withhold |
| `min_rising_edges` | 件 | >= 2 | 中央値を取るのに最低 1 区間＝2 エッジ要る。1 本の GREEN→消灯遷移を点滅と読ませない |
| `min_luminance_samples` | 件 | >= 2 | レート A の証拠不足で点滅判定を走らせない |
| `max_sample_gap_s` | s | 有限・> 0 | **被覆**: レート A の隣接サンプル間・窓端と最寄りサンプルの穴の上限。件数（`min_luminance_samples`）は被覆ではない ── 点灯相に詰まった 45 本は 45 本のまま下限を満たすので、窓の**「秒数」側**（[:307](04-perception-sidewalk-and-signals.md:307) の「秒数 ∧ 有効サンプル数の下限」）を別に見ないと点滅が定常青に化ける（stage-2 レビュー B-1）。値はカメラ fps とドロップ許容から決まり docs 未定 |

#### 3. レート A（`FlashDetector`）の真理表

`is_flashing` は 3 値。**`None` = 判定不能であって「点滅なし」ではない**（[追補 ④ §2 項目 3](04-perception-sidewalk-and-signals.md:479)）。`False` を名乗れるのは**連続点灯**の窓だけ＝「点滅していない」の積極証拠がある場合に限る。

本表は `FlashDetector` 単体と**合成経路（`SignalWindow.evaluate`）の双方**に適用される。合成側は加えて、**レート A 入力に非有限 `stamp_s` が 1 本でもあれば `FlashDetector` を呼ばず `None`** に倒す（stage-2 レビュー B-1。下記 §6 ②）。

| 窓内のレート A 観測 | `is_flashing` | `measured_period_s` | 理由 |
|---|---|---|---|
| サンプル数 < `min_luminance_samples` | `None` | `None` | 証拠不足 |
| ヒステリシス帯の内側だけで 1 相も確定しない | `None` | `None` | 位相が付かない＝二値化が成立していない |
| いずれかの値が非有限（NaN / inf） | `None` | `None` | 部分的に読めた輝度列は本物の消灯相と区別できない（`OQ-OD4Z-d`） |
| 露出の幅 > `exposure_tolerance` | `None` | `None` | 露出固定の前提が崩れ偽 OFF が作られる（[`OQ-OD4L`](04-perception-sidewalk-and-signals.md:349)） |
| **ON 相のみ**（連続点灯）**かつ窓を被覆**（穴 <= `max_sample_gap_s`） | **`False`** | `None` | 「明滅していない」の積極証拠。被覆は必須 ── 点灯相だけを拾った疎な列は「窓ぜんぶ点いていた」を言えない |
| ON 相のみだが**被覆していない**（消灯相が欠落・stamp 不読・連続ドロップ） | `None` | `None` | **stage-2 レビュー B-1 で閉じた fail-open**。ここが `False` だと点滅が `GREEN` になる |
| **OFF 相のみ**（連続消灯） | `None` | `None` | 点滅の消灯相を切り取っただけかもしれない＝否定を主張できない |
| 両相あり・立ち上がりエッジ < `min_rising_edges` | `None` | `None` | 交番はあるが周期を測れない（GREEN→消灯→GREEN の 1 回は点滅ではない） |
| 両相あり・エッジ間隔の中央値が非有限または <= 0 | `None` | `None` | 同時刻エッジ等で周期にならない |
| 両相あり・エッジ間隔の中央値が 0.5 s ± `period_tolerance_s` の**外** | `None` | 実測値 | 交番しているが法定周期でない。数値は隠さず出し、判定だけ withhold |
| 両相あり・帯内・**赤が同期して増えた**（> `red_sync_delta`） | `None` | 実測値 | 全体照度の変動疑い |
| 両相あり・帯内・赤が同期していない | **`True`** | 実測値 | 二段レートの点滅判定成立（[:307](04-perception-sidewalk-and-signals.md:307)） |

#### 4. 状態決定の真理表（`SignalWindow`・[:307](04-perception-sidewalk-and-signals.md:307) の順序どおり）

窓 = `(window_end_s − window_span_s, window_end_s]`。多数決・件数はすべて**レート B** のサンプルを数える（暫定 = [`OQ-OD4Y-d`](04-perception-sidewalk-and-signals.md:489)）。

| # | 条件（上から評価） | `state` |
|---|---|---|
| 1 | `is_flashing is True` | `GREEN_FLASHING` |
| 2 | RED 多数決（share >= `majority_fraction`） | `RED` |
| 3 | GREEN 多数決 ∧ **`is_flashing is False`** ∧ `off_phase_count == 0` ∧ `roi_consistent`（最新証拠）∧ `sample_count >= min_samples` ∧ **最新証拠が `GREEN`** | `GREEN` |
| 4 | 上記以外すべて（**本表の外側の規則**: レート B 入力に非有限 `stamp_s` があれば、**①が成立した窓を除き** `UNKNOWN` = §6 ②） | `UNKNOWN` |

- **③ の `is_flashing is False`（`is not True` ではない）が本実装の裁定点**: docs はこの場合を明示していない（[:307](04-perception-sidewalk-and-signals.md:307) は「NOT flashing」とだけ書く）。**判定不能で GREEN を許すのは証拠の不在を否定と読むこと**（[追補 ④ §2 項目 3](04-perception-sidewalk-and-signals.md:479)）なので fail-closed に `UNKNOWN` へ倒した＝`OQ-OD4Z-a`。
- **GREEN 離脱は最新 1 サンプル**（[:90](04-perception-sidewalk-and-signals.md:90) の非対称）: ③ の最後の AND 項がそれで、**状態を持たない**（履歴で GREEN を開いたままにできない）。
- **鮮度（`max_age`）は判定しない**（[追補 ③ #4](04-perception-sidewalk-and-signals.md:382) / [追補 ④ §2 項目 1](04-perception-sidewalk-and-signals.md:477)）。producer が窓を配ると停止判定点が 2 つになる。
- **`GREEN_FLASHING` 証拠の多数決は GREEN に数えない**（per-frame クラスは別語彙＝[:261](04-perception-sidewalk-and-signals.md:261)）。①が成立しなければ④に落ちる＝`OQ-OD4Z-b`。
- 空窓（レート A / B とも有効 stamp のサンプルが 0 件）は**観測なし＝出力しない**（`None` を返す）。`source_stamp_s` は必須（追補 ④）で捏造できず、偽の鮮度を作らないため。消費側は自分の鮮度検査で禁止側に倒れる。

#### 5. `quality` の定義（暫定）

| field | 本実装の定義 | 備考 |
|---|---|---|
| `valid_fraction` | 窓内レート B のうち **`NOT_VISIBLE` でない**比率（空窓は `0.0`） | **暫定**＝`OQ-OD4Z-f`。`OFF_OR_UNLIT`（消灯相）は「見えた」観測なので**有効側**に数え、遮蔽・欠落だけを無効とする（[:261](04-perception-sidewalk-and-signals.md:261) の分離が前提）。docs は窓観測の分子を定義していない（[:203](04-perception-sidewalk-and-signals.md:203)） |
| `frame_digest` / `device_frame_seq` / `processing_latency_s` | 入力の**受け渡しのみ**（04 の画像段が持つ値） | 使えない値（非 str digest・負の seq・**負の latency**）は例外にせず `None`＝「不在」として運ぶ。契約は負の latency を `ValidationError` で拒むが、その例外を safety loop へ持ち込まないのは呼び出し側の責務（[追補 ④ §2 項目 6](04-perception-sidewalk-and-signals.md:482)）＝`OQ-OD4Z-e` |

> **組合せの注意**: レート B の窓が空でレート A だけが埋まっている窓では、`state = GREEN_FLASHING` でありながら `sample_count = 0` / `valid_fraction = 0.0` / `off_phase_count = 0` になりうる（点滅判定はレート A 単独で立つため）。`valid_fraction` が低いことは状態が弱いことを意味しない ── 消費側は **`sample_count` と `valid_fraction` を `GREEN` の前提条件としてのみ読み、`GREEN_FLASHING` / `RED` の否定材料に使わない**こと。

#### 6. fail 方向（不変条件）

- パラメータ・`crossing_id` の不正 → **構築時**に `SignalTemporalConfigError`。
- **data では例外を上げない**。倒れ方は 3 種類で、**同一視しない**: ①**到着順は無関係**（窓は時刻で定義されるので逆順で渡しても結果は同じ。ただし**同一 stamp が 2 本ある場合だけ**は安定ソートゆえ「最新」が入力順に依存する ── producer 側で同 stamp を出さないこと）。②**判定不能**（レート A の非有限値〔比率・露出・**`stamp_s`**〕/ 露出変動 / 証拠不足 / **被覆不足**）→ `is_flashing=None` → GREEN は出ない。レート B 入力に非有限 `stamp_s` があれば**窓ごと `UNKNOWN`**（どのサンプルが窓内か言えない以上、多数決も OFF 件数も信用できない）── ただし**①`is_flashing is True` が成立した窓を除く**（点滅判定はレート A 単独で立つため、レート B の不読は `GREEN_FLASHING` を壊さない。§4 の真理表①が可読性検査より**先**に評価される = `signal_temporal_core.py` の同 docstring）。③**観測なし**（窓に有効 stamp のサンプルが 0 件・窓端が非有限）→ 出力しない（`None`）。使えない品質値（非 str digest・負の seq・負の latency）は品質 field を**不在**にして運ぶ。
- **非有限 `stamp_s` を「落とすだけ」にしない**（[stage-2 レビュー B-1]）: 落とす実装は**両レートで fail-open** だった ── 点滅の消灯相が stamp を失うと「連続点灯」に見えて `GREEN`、`OFF_OR_UNLIT` 証拠が stamp を失うと GREEN を止めなくなる。比率の非有限は fail-closed なのに stamp の非有限が fail-open、という不整合を閉じた。
- `state == GREEN` を代入する経路は**モジュール内で 1 か所**（AST pin で固定）。
- `cmd_vel` / `stop_request` / `speed_limit` / `stop_state` のいずれにも触れない（L4 publish-only の AST pin）。

#### 7. テスト（R-26・独立オラクル・[doc20 §9](../architecture/20-dev-quality-and-testing.md:131)）

`tests/unit/test_signal_temporal_core.py`（`unit` + `safety` マーカー・**68 本**）。合成生成器はテスト側にあり実装を参照しない。期待値は生成パラメータからの手計算リテラル（窓 (8.0, 10.0]・fps 30 → レート A 60 本・f_B 9 Hz → レート B 18 本・立ち上がりは t = 8.5 / 9.0 / 9.5 / 10.0 ゆえ実測周期は厳密に 0.5 s）。

- 真理表の各行（定常 GREEN / OFF 1 件 → UNKNOWN / 定常 RED / 点滅 duty 50 % → `GREEN_FLASHING` + 周期 0.5 s）。
- **罠**: duty 75 % + 分類器が消灯相を GREEN と出す → レート B は**満票 GREEN**（GREEN の他の AND 項もすべて成立）でも、レート A が勝って `GREEN_FLASHING`。同じレート B をレート A 不在で流すと `UNKNOWN`＝「多数決だけを防波堤にしない」（[:306](04-perception-sidewalk-and-signals.md:306)）を両側から示す。
- 相対多数（12/18 = 0.667）は判定にならない・露出ドリフト → 判定不能・赤の同期 → 判定不能・全消灯窓は「点滅なし」を主張できない・**疎な点灯列は「連続点灯」を主張できない**（被覆項）。
- **stage-2 B-1 の回帰 3 本**（修正前はいずれも `GREEN` だった）: 点滅で消灯相の stamp が NaN／消灯相のサンプルが単に無い（NaN ですらない・件数下限は満たす）／`OFF_OR_UNLIT` 証拠 1 件の stamp が NaN。いずれも `UNKNOWN`。
- GREEN 離脱: 最新 1 件が `RED` / `NOT_VISIBLE`（`off_phase_count` は 0 のまま）/ `OFF_OR_UNLIT` で即 `UNKNOWN`。
- `classifier_rate_hz` = 10 / 12 / 30 / 2 / 4 → `ValueError`、9 / 7.5 / 11 / 13 → OK。
- **P-1 property**（親 §7）: seed 固定・**N = 240** のランダム窓で、真値が点滅または赤を含む窓（**138 件**）の出力が `GREEN` = **0 件**。duty・jitter・分類器の嘘に加え **レート A の欠落**（消灯相の脱落 47 / stamp 不読 47 / 連続ドロップ 60・stage-2 B-1 の系統）も振る。非空虚性も assert（GREEN が出る窓 25 件・`GREEN_FLASHING` 41 件が実際に出る＝常に `UNKNOWN` を返す実装では落ちる）。
- **AST pin**: `rclpy` / `numpy` 非 import・actuation 語彙なし・`SignalState.GREEN` の出現 1 か所・パラメータ dataclass に既定値なし・**モジュール定数の数値は `NOMINAL_FLASH_PERIOD_S = 0.5` のみ**（検査は module-level の `ast.Assign` のみ ── 関数内の `0.5 < f <= 1.0` 等は調整可能なしきい値ではなく §2 に文書化した定義域の境界）。
- **mutation 10/10 で赤**（OFF 相条件の削除／判定不能を GREEN 許可へ／整数比チェックの削除／GREEN 離脱を多数決のみへ／多数決を過半数へ緩和／全消灯窓を「点滅なし」へ／連続点灯を判定不能へ／**被覆項を外す**／**レート A の不読 stamp を黙って落とす**／**レート B の不読 stamp を黙って落とす**。末尾 3 本が stage-2 B-1 の再発検知）。

#### 8. OPEN QUESTIONS（本実装が**発明せずに残した**もの・接頭辞 `OQ-OD4Z`）

- `OQ-OD4Z-a` **`is_flashing is None`（判定不能）で GREEN を許すか**。docs は [:307](04-perception-sidewalk-and-signals.md:307) の「NOT flashing」としか言わない。本実装は fail-closed（`is False` を要求）に倒した。実機で「定常青なのに露出変動や遮蔽で判定不能が頻発し横断できない」場合、緩めるのではなく**レート A の可用性**（露出固定・ROI）側を直す方針でよいかを裁定する。
- `OQ-OD4Z-b` **レート B の `GREEN_FLASHING` 多数決**を `GREEN_FLASHING` へ昇格させるか（現状は④の `UNKNOWN`）。**正本が割れている**: 親 §4 [:86](04-perception-sidewalk-and-signals.md:86) は `GREEN_FLASHING` の条件に「交番、**または明示クラス**」と明示クラス経路を認めるが、後発の訂正である追補 ② §5 [:307](04-perception-sidewalk-and-signals.md:307) の状態決定順序は `is_flashing`（レート A）だけを `GREEN_FLASHING` の源にしている。本実装は**後者に厳密に従い**明示クラスを読まない（[:262](04-perception-sidewalk-and-signals.md:262) が「点滅を直接クラス化した公開モデルは無い」と言う以上、当面その入力は存在しないため）。どちらも禁止側なので安全側の差は無いが、法的意味は違う（青点滅 = 横断中は速やかに終える／`UNKNOWN` = 禁止＝[01 §6](01-legal-envelope-japan.md)）ので、横断中の継続可否で差が出る。明示クラスを出す分類器を採るなら :86 側へ寄せる doc PR が要る。
- `OQ-OD4Z-c` `period_tolerance_s` の値（0.5 s 周りの許容半幅）。duty 比が未確定（[:306](04-perception-sidewalk-and-signals.md:306)）でカメラ fps も未定のため実測待ち。
- `OQ-OD4Z-d` **読めない値の扱いは「窓ごと判定不能」に統一済**（比率・露出・`stamp_s` のいずれも）。**修正前は `stamp_s` だけが例外で、黙って落としていた＝fail-open だった**（stage-2 レビュー B-1。点滅の消灯相が stamp を失う／単に欠落する／`OFF_OR_UNLIT` 証拠が stamp を失う、の 3 形で `GREEN` が出た）。被覆項 `max_sample_gap_s` と合わせて閉じた。**残す OQ = 残る非対称**: 1 本の不読で窓全体を捨てるのは「1 フレーム落ちただけで横断できない」側に厳しすぎるかもしれない ── 緩めるなら「落とした上で被覆を再評価する」形（穴として数える）が候補で、**黙って落とす形には戻さない**。レート B 側を窓ごと `UNKNOWN` にする粒度（1 本の不読 vs 一定割合）も同じ裁定に含める。
- `OQ-OD4Z-e` **負の `processing_latency_s`（時計取違え）を「不在」として運ぶ**（現状）か、品質不成立として `valid_fraction` に反映するか。契約は入口で拒否するが、例外を safety loop に入れられない（[追補 ④ §2 項目 6](04-perception-sidewalk-and-signals.md:482)）ため現状は握り潰している＝異常が見えなくなる方向。
- `OQ-OD4Z-f` `valid_fraction` の分子（本実装は「`NOT_VISIBLE` でない」）。[`OQ-OD4Y-b`](04-perception-sidewalk-and-signals.md:487)（比率を計算できない producer の表現）と同じ裁定に含めるべき。
- `OQ-OD4Z-g` **窓端 `window_end_s` を呼び出し側が渡す**（現状・node が自分のタイマで窓を送れる）か、最新サンプルから導出するか。導出にすると「データが止まった窓」を再送し続ける形になり、鮮度検査の意味が消費側に寄りすぎる。
- 既存 OQ への接続: `sample_count` / `off_phase_count` を**レート B で数えた**のは暫定で、裁定は [`OQ-OD4Y-d`](04-perception-sidewalk-and-signals.md:489)。per-frame 証拠列を topic へ出すかは [`OQ-OD4Y-j`](04-perception-sidewalk-and-signals.md:495)。露出固定は [`OQ-OD4L`](04-perception-sidewalk-and-signals.md:349)、二段レートの採否そのものは [`OQ-OD4J`](04-perception-sidewalk-and-signals.md:347)（本実装は「採る」前提で書いた**検証可能な実体**であって裁定ではない）。
- **未着手（本スライスの外）**: レート A サンプラ・分類器・ROI 投影・露出固定の実装／node・topic・QoS・publish 周期（型未凍結 = [:202](04-perception-sidewalk-and-signals.md:202)）／実 bag での P-1 実測（[追補 ② §8 順序 4](04-perception-sidewalk-and-signals.md:328) の「走行判断に使わない観測モード」）。



<!-- ▲ 上のプレースホルダを埋めるのは担当レーンのみ。以下の空行は隣接プレースホルダとの
     git hunk 分離用（既定 context 3 行 × 2 を超える間隔を確保し、P1/P2/P3 が同時に埋めても
     同一 hunk にならないようにしている）。詰めない・他レーンの節を書き換えない。 -->



## 【2026-09-17 追補 ⑦】09_Runtime model_manifest loader・10_Evaluation 評価基盤 v0（実装記録・P3 レーンが記入）

正本 = 本 doc（親 §7 [:117](04-perception-sidewalk-and-signals.md:117)・追補 ② §1 09/10 行 [:204](04-perception-sidewalk-and-signals.md:204) / [:205](04-perception-sidewalk-and-signals.md:205)・§3-1 [:231](04-perception-sidewalk-and-signals.md:231)・§3-2 [:263](04-perception-sidewalk-and-signals.md:263)・追補 ③ 最終行 [:385](04-perception-sidewalk-and-signals.md:385)・追補 ④ §1-5 [:452](04-perception-sidewalk-and-signals.md:452)）。本追補は **09_Runtime の model_manifest loader / 検証 CLI** と **10_Evaluation の評価指標の純ロジック核**を、`ws/src/warehouse_perception` に **torch / TensorRT / ROS / numpy 非依存の純 python（+ PyYAML + `eval_sdk`）**として実装した記録である。

> **レイヤ注記**（[.claude/rules/layer-annotation.md](../../.claude/rules/layer-annotation.md)）: 09_Runtime_and_Models = **基盤**（単一 layer に帰属させない・版 pin は 00）／10_Evaluation = **観測面（横断・offline）**（[:335](04-perception-sidewalk-and-signals.md:335)）。本スライスは **node 0・topic 0**。停止・許可・駆動のいずれにも関与せず、**走行中に動かさない**（随伴 Mac の位置づけ = [02:145](02-architecture-split-orin-pc-cloud.md:145) / [02:161](02-architecture-split-orin-pc-cloud.md:161)、**Mac は stop producer になれない** = [02:137](02-architecture-split-orin-pc-cloud.md:137)）。

**本スライスに含まれないもの**（未購入・未記録ゆえ発明しない）: 推論 backend（RF-DETR / YOLO26 / D-FINE）の実装・重み・engine・実走 bag。入力は「**他の何かが出した予測列 + 真値列**」であり、その形式は §2 の**例示（未凍結）**として定義する。

### 1. manifest の置き場と読み方（09_Runtime）

| 項目 | 実体 | 根拠 |
|---|---|---|
| 置き場 | `ws/src/warehouse_perception/manifests/<model-slug>.yaml`（package-local・規約は同ディレクトリの `README.md`） | manifest は 04 が **artifact として所有**（[:204](04-perception-sidewalk-and-signals.md:204)） |
| 型の正本 | 凍結契約 `warehouse_interfaces.perception.ModelManifest` / `EvaluationRecord`（追補 ④ §1-5 = [:452](04-perception-sidewalk-and-signals.md:452)） | docs の例示より**凍結契約が優先**（[.claude/rules/docs-first.md](../../.claude/rules/docs-first.md)） |
| 読み方 | `warehouse_perception.model_manifest.load_manifest(path)` = PyYAML `safe_load` → `ModelManifest.model_validate`。**失敗は `ValidationError` をそのまま上げる**（offline ツール＝fail-loud でよい。安全ループへ例外を持ち込まない規律 [:482](04-perception-sidewalk-and-signals.md:482) は、そもそも安全ループの無い本経路には掛からない） | 追補 ④ §1-5 |
| 重み照合 | `sha256_of_file(path)`（`sha256sum` と同値）・`verify_weights(manifest, weights_path)`。**読めないファイルは例外**＝「照合できなかった」を「照合して違った」と同じ顔にしない | `weights_sha256` は 64 hex・アルゴリズム固定は**暫定** = [`OQ-OD4Y-k`](04-perception-sidewalk-and-signals.md:496) |
| CLI | `python3 -m warehouse_perception.model_manifest validate <yaml> [--weights <file>]`（console_script `perception_manifest`）。exit **0 = 妥当 / 1 = 不備** | — |
| 例ファイル | `manifests/example.rf-detr-nano.yaml`。**値はすべて placeholder**（実測値・実 digest を書かない）。`evaluation` は省略不可（[:471](04-perception-sidewalk-and-signals.md:471)）なので「**評価済みの形の例**」として `dataset_id: "example-bag-0000"`（存在しない bag）＋自己整合な合成指標を置く。`"UNEVALUATED"` のようなダミー id は**禁止**——「評価した」と読める嘘になる。`license: "Apache-2.0"` の出典は §3-1 の RF-DETR 行 [D]（[:235](04-perception-sidewalk-and-signals.md:235)） | [:465](04-perception-sidewalk-and-signals.md:465) / [:469](04-perception-sidewalk-and-signals.md:469) / [:471](04-perception-sidewalk-and-signals.md:471) |
| `setup.py` の `data_files` | **載せない**。manifest は repo に残す**記録**であって配布物ではない。`share/` へ install すると「実行時に読む config」に見える | [:204](04-perception-sidewalk-and-signals.md:204) |

- **engine を焼く・読む処理は書かない**。TensorRT engine は GPU arch と TRT 版に固定され可搬でないため**必ずボード上で焼く**（[`OQ-OD4U`](04-perception-sidewalk-and-signals.md:358)）。host tool ができるのは `engine_built_on_board` / `tensorrt_version` / `gpu_arch` に**その事実を記録すること**だけ。engine 未作成（ONNX 止まり）の採用単位は正当で、後 2 者は**空でよい**（[:467](04-perception-sidewalk-and-signals.md:467) / [:468](04-perception-sidewalk-and-signals.md:468)）。例ファイルはその状態を採る。
- **重みのダウンロード・外部 API 呼び出しを行わない**（`requests` / `urllib` 非 import を unit の AST pin で固定）。依存版の一覧は 00 が正本（[00 末尾追補](00-mission-and-scope.md:81)）で、ここに複製しない。

### 2. 評価入力の**例示（未凍結）**スキーマ — JSONL

**この形は契約ではない**。`warehouse_interfaces` に登録しておらず、本 harness 自身の入力形式として contract PR 無しで変えてよい（凍結契約は追補 ④ の 04 **出力**型だけ）。`warehouse_perception.evaluation_core.read_jsonl(path)` が読む。

1 行 = 1 サンプル（JSON object）:

| key | 型 | 必須 | 意味 |
|---|---|:---:|---|
| `stamp_s` | number（有限） | ✅ | そのフレームの**元計測時刻**（秒）。付け直さない（runtime 契約と同じ規律 = [:424](04-perception-sidewalk-and-signals.md:424)） |
| `truth` | 非空 string | ✅ | offline ラベルパスの真値（1 記録 → 1 ラベル） |
| `pred` | 非空 string | ✅ | その系統がそのフレームに対して出した値 |
| `distance_m` | number `>= 0` | — | 対象までの距離。**不在 = 記録なし**（帯を仮定しない） |
| `consumed_s` | number（有限） | — | 結果が**消費された**時刻。撮影 → 消費の遅延を作る第 2 の時刻（04 単独では撮影 → 出力までしか測れない = [:418](04-perception-sidewalk-and-signals.md:418)） |
| `crossing_id` | 非空 string | — | 登録横断点。**registry の正本は 02**（[:217](04-perception-sidewalk-and-signals.md:217)） |

- **未知キーは無視**（ハブの `extra="ignore"` と同方針＝ラベル出力が richer でも読める）・空行はスキップ。
- **必須欠落・型違反・不正 JSON は `<path>:<行番号>: <理由>` の `ValueError`**。これらの file はラベルパスと再生スクリプトが生成するので「どこかの行が壊れている」では動けない。
- 信号の 4 状態（`SignalState`）以外のラベルも運べる（検出器評価では `person` / `none` 等）。`signal_confusion` に渡した時点で `SignalState` に解釈できないラベルは `ValueError`。

### 3. 指標の定義と `EvaluationRecord.metrics` の**キー案**（= [`OQ-OD4Y-e`](04-perception-sidewalk-and-signals.md:490)）

`metrics` は `dict[str, float]` の**開いた map**のまま（キー集合は docs 未定 = [:473](04-perception-sidewalk-and-signals.md:473)）。以下は**本レーンの案**であり、コード側では `evaluation_core` の `METRIC_*` 定数 1 か所に集約してある（裁定時に機械的に改名できる）。**検証はしていない＝契約ではない。**

| キー | 由来 | 定義 |
|---|---|---|
| `signal.confusion.<TRUTH>_to_<PRED>` | 親 §7 P-1（[:117](04-perception-sidewalk-and-signals.md:117)）・[:204](04-perception-sidewalk-and-signals.md:204)「bag id + 混同行列」 | `SignalState` 4×4 の全 16 セル。**0 のセルも必ず出す**（キーの不在を「0 件」と読ませない）。P-1 の 2 セルは常に存在する |
| `signal.p1_violations` | [:117](04-perception-sidewalk-and-signals.md:117) | `GREEN_FLASHING→GREEN` + `RED→GREEN` の件数 |
| `signal.p1_gate_pass` | 同上 | `1.0` / `0.0`。**0 件が必達**（率ではない＝許容値を設けない） |
| `signal.unknown_rate` | 同上（`UNKNOWN` 率は**運用指標**） | 予測が `UNKNOWN` の割合。真値が `UNKNOWN` の行（ラベル不能フレーム）も**分母に残す** |
| `signal.sample_count` | — | 総サンプル数。P-1 合格は「評価した」を意味しない（0 サンプルでも合格する）ので必ず併記する |
| `detect.miss_rate@<band>` / `detect.samples@<band>` | [:205](04-perception-sidewalk-and-signals.md:205) 差別化指標 | 距離帯別の見逃し率と母数。**帯の境界は引数（既定なし）**——docs は指標を名指すが境界を pin していない |
| `latency.capture_to_consume_{p50,p95,max}_s` | [:203](04-perception-sidewalk-and-signals.md:203) / [:331](04-perception-sidewalk-and-signals.md:331)（平均 FPS でなく撮影 → 消費の遅延） | `consumed_s − stamp_s` の分位数。算術は `eval_sdk.stats.percentile`（線形補間） |
| `latency.capture_to_consume_negative_count` / `_sample_count` | [:418](04-perception-sidewalk-and-signals.md:418) | **負の遅延は捨てずに別集計**。分位数からは除く |
| `drive.false_stops_per_km` / `drive.travelled_m` | [:205](04-perception-sidewalk-and-signals.md:205) | 走行距離当たりの誤停止。**走行距離は引数**（サンプル列は走行を持たない。出所は run record = [jetson/03:223](../jetson/03-build-deploy-run-and-run-records.md:223)） |

**設計上の 3 規律**（追補 ④ §2-3「証拠の不在を否定と読まない」の実装側対応）:

1. **`None` は 0 ではない**。母数 0 の率は `None` を返し、**metrics のキーごと落とす**。キーの不在は「計算できなかった」であって「0 だった」ではない。
2. **サンプルを黙って捨てない**。帯に入らない・距離を持たないサンプルは件数として報告し、**負の遅延（時計取違え）は件数を別 key で返す**。分位数へ混ぜれば死んだ経路が「速い」に見え（[:418](04-perception-sidewalk-and-signals.md:418) が値として拒否するのと同じ故障）、黙って捨てれば同じ故障が隠れる。
3. **見逃し（miss）の定義**: 真値が確立しているのに予測が確立しなかった（`pred` が「確立しないラベル」集合に入る）こと。**取り違えは miss ではない**——危険な取り違えは混同行列と P-1 が既に数えている。「確立しないラベル」は既定 `{"UNKNOWN"}`（[:117](04-perception-sidewalk-and-signals.md:117) / [:405](04-perception-sidewalk-and-signals.md:405)）で、検出器評価は自分の語彙を注入する。誤分類も miss に数えるかは §6 の OQ。

### 4. 同一 bag での N 系統比較の手順（10_Evaluation）

[:263](04-perception-sidewalk-and-signals.md:263) の手順をそのまま実行手順にする:

1. **1 回の記録**（走行時と同一カメラ・同一取付高さ・**露出固定** = [`OQ-OD4L`](04-perception-sidewalk-and-signals.md:349)）。記録基盤は run record（[jetson/03:223](../jetson/03-build-deploy-run-and-run-records.md:223)・`ros2 bag record` へ topic は**位置引数** = [jetson/03:291](../jetson/03-build-deploy-run-and-run-records.md:291)）。**実走記録はまだゼロ**。
2. **1 セットのラベル**（Mac で offline = [:271](04-perception-sidewalk-and-signals.md:271)）。
3. **N 系統を同じ bag に再生**し、系統ごとに §2 の JSONL を出す。候補は信号 8 系統（[:248](04-perception-sidewalk-and-signals.md:248)〜）・検出器は試す順序 Top-3（[:245](04-perception-sidewalk-and-signals.md:245)）。
4. **同一の指標関数**で並べる（`evaluation_core.compare`）。**P-1 を満たさない系統は `rejected` として不採用**（[:263](04-perception-sidewalk-and-signals.md:263)）。比較表の行順は入力順＝再現可能。
5. **走行判断に使わない観測モード**で回す（実装順序 4 = [:328](04-perception-sidewalk-and-signals.md:328)）。

**同一 bag であることの担保は呼び出し側の責務**。サンプル列は dataset id を持たず、ここで発明すると「別の bag 同士を 1 つの表で比べる」ことが可能になってしまう。採用時に `to_evaluation_record(dataset_id, metrics)` で **bag id と指標を 1 つの `EvaluationRecord`** に束ね、manifest に載せる（= 1 採用単位・[:471](04-perception-sidewalk-and-signals.md:471)）。

**予算に使わない数値**: 表の ms はほぼ T4 値で、Orin へ外挿するのは推論であり、**640 入力・前後処理除外**の条件付き（[:244](04-perception-sidewalk-and-signals.md:244) / [`OQ-OD4U`](04-perception-sidewalk-and-signals.md:358)）。自画角・自解像度・前後処理込みの実測で置き換えるまで manifest の数値は placeholder に留める。

### 5. ライセンス境界と課金ゲート

- **配布物に載せるモデル**: 本命 RF-DETR-Nano（Apache-2.0）・保険 D-FINE-S / RT-DETRv2-S（Apache-2.0）（[:231](04-perception-sidewalk-and-signals.md:231) / [:235](04-perception-sidewalk-and-signals.md:235)）。**YOLO 系は AGPL-3.0 ＝「速度上限の測定器」に限定し配布物に載せない**（[:245](04-perception-sidewalk-and-signals.md:245) / [`OQ-OD4N`](04-perception-sidewalk-and-signals.md:351)）。**DEIMv2 は非商用ゆえ除外**（[:242](04-perception-sidewalk-and-signals.md:242)）。契約は `license` の空を拒否する（[:469](04-perception-sidewalk-and-signals.md:469)）＝「不明」を黙って通さない。
- **評価データ**: JRDB は CC BY-NC-SA 3.0 で、productization を視野に入れるなら**評価にも使わない**（[:278](04-perception-sidewalk-and-signals.md:278) / [`OQ-OD4T`](04-perception-sidewalk-and-signals.md:357)）。SANPO は CC-BY-4.0（[:277](04-perception-sidewalk-and-signals.md:277)）。
- **課金ゲート**: VLM / SAM によるラベリング（[:256](04-perception-sidewalk-and-signals.md:256) / [:271](04-perception-sidewalk-and-signals.md:271)）と走行映像の外部送信は [`OQ-OD4V`](04-perception-sidewalk-and-signals.md:359) の gate 対象で、`WAREHOUSE_LIVE_ER` と**同型**。**エージェントはこの種の gate を自分で立てない**（有料実行は operator が明示的に行う。[.claude/rules/environments.md](../../.claude/rules/environments.md) §Secrets / [dev/07 §4.5](../dev/07-mode-x-er-live-e2e-runbook.md)）。本スライスのコードは外部 API を一切叩かない（AST pin）。
- **秘密**: 本スライスは `config/<env>/.env` を読まない。manifest に鍵・トークンを書かない（記録は持ち出される = [jetson/03:274](../jetson/03-build-deploy-run-and-run-records.md:274) の redaction と同じ理由）。

### 6. レイヤ annotation 対応表に**行を足さない**理由

[productization/01:174](../productization/01-commercial-box-map.md:174) の対応表は **L4–L0 の 5 行に加えて `L0'`（ホスト側物理安全）と `横断`（観測面）の計 7 行**を持つ（[01:182](../productization/01-commercial-box-map.md:182)〜[01:188](../productization/01-commercial-box-map.md:188)）。09_Runtime_and_Models は「単一 layer に帰属させない基盤」、10_Evaluation は「横断（観測面）」であり（[:335](04-perception-sidewalk-and-signals.md:335)）、**どの L 行にも属さない**。無理に L4 行へ足すと [:189](04-perception-sidewalk-and-signals.md:189) が訂正した「全 sub-dir = L4」の自己矛盾を再導入する。よって**新しい L 行は足さず**、既存の `横断` 行へ**行内 append** した（#712 = [01:188](../productization/01-commercial-box-map.md:188) に `ws/src/warehouse_perception/` の `model_manifest.py` / `evaluation_core.py` を追記し、理由として本節を back-link）。L 行の帰属の整理は [`OQ-OD4B`](04-perception-sidewalk-and-signals.md:339)（11 sub-dir の採否）へ申し送りのまま。本追補の冒頭レイヤ注記と `ws/src/warehouse_perception/CLAUDE.md` の P3 節が、それまでの annotation の所在となる。

### 7. OPEN QUESTIONS（本追補で**発明せずに残した**もの・接頭辞 `OQ-OD4Z`）

- `OQ-OD4Z-a` **`metrics` キー語彙の裁定**（[`OQ-OD4Y-e`](04-perception-sidewalk-and-signals.md:490) の具体化）: §3 の案（`signal.*` / `detect.*` / `latency.*` / `drive.*`・`@<band>` 区切り）を採るか。区切り文字（`.` / `@`）と帯名の命名規則も未定。
- `OQ-OD4Z-b` **JSONL 例示スキーマを凍結するか**。凍結するなら家は `warehouse_interfaces` か harness-local か（追補 ④ は 04 の**出力**型しか凍結していない）。系統ごとの出力を 1 bag 1 ファイルにするか、系統列を 1 ファイルに畳むかも未定。
- `OQ-OD4Z-c` **miss の定義**（§3 規律 3）: 誤分類を miss に数えるか、`UNKNOWN` への落ちだけを数えるか。検出器評価（`person` / `bicycle`）と信号評価で定義を分けるか。
- `OQ-OD4Z-d` **距離帯の境界**。P-2（[:118](04-perception-sidewalk-and-signals.md:118)）と P-6 案（[`OQ-OD4T`](04-perception-sidewalk-and-signals.md:357)）から決まるはずだが、制動距離も低視点 gap も未実測ゆえ本スライスでは**引数のまま**。
- `OQ-OD4Z-e` **「誤停止」の判定**。`drive.false_stops_per_km` は件数を**受け取って割るだけ**で、何を誤停止と数えるか（Guardian の発火か・collision_monitor の STOP か・介入か）は未定。[:205](04-perception-sidewalk-and-signals.md:205) は指標名しか与えていない。
- `OQ-OD4Z-f` **`consumed_s` の出所**。bag 再生から「消費時刻」をどう取るか（下流ノードの受信ログか・再生 harness の計測か）は未決。取れない系統では遅延指標が空になる。
- `OQ-OD4Z-g` **draft manifest**: 評価前のモデルを表現できない（[`OQ-OD4Y-g`](04-perception-sidewalk-and-signals.md:492)）ため、例ファイルは「評価済みの形」を装った placeholder になっている。draft 型を別に置くか optional へ緩めるかの裁定待ち。
- `OQ-OD4Z-h` **regression（回帰）の置き場**。10_Evaluation は `bag_replay / scenario_sets / metrics / regression`（[:205](04-perception-sidewalk-and-signals.md:205)）だが、本スライスは metrics の純関数核のみ。scenario_sets の定義と、bag を要する regression job を CI に載せるか（bag は大きく、CI に GPU も bag も無い）は未決。
- `OQ-OD4Z-i` **`eval_sdk` へ何を降ろすか**。現状は `percentile` だけを借り、指標定義は domain 側（本 package）に置いた（doc21 の層規律と同型）。混同行列のような domain 非依存の算術を `eval_sdk` へ移すかは、消費者が 2 つ目に現れてから。

### 8. 実装（produce / consume は [`ws/src/warehouse_perception/CLAUDE.md`](../../ws/src/warehouse_perception/CLAUDE.md) の P3 節が正本）

- `warehouse_perception/model_manifest.py` — loader / `sha256_of_file` / `verify_weights` / CLI（console_script `perception_manifest`）。
- `warehouse_perception/evaluation_core.py` — `EvalSample` / `read_jsonl` / `signal_confusion` / `p1_violations` / `p1_gate` / `unknown_rate` / `miss_rate_by_distance_band` / `false_stops_per_km` / `capture_to_consume_latency` / `compare` / `*_metrics` / `to_evaluation_record`。
- `ws/src/warehouse_perception/manifests/`（**python パッケージ配下ではなく package root**） — `README.md`（置き場の規約・engine 境界・AGPL 境界）+ `example.rf-detr-nano.yaml`（placeholder）。
- unit: `tests/unit/test_model_manifest.py` / `tests/unit/test_evaluation_core.py`（独立オラクル＋mutation 感度 = [doc20:139](../architecture/20-dev-quality-and-testing.md:139) / [doc20:140](../architecture/20-dev-quality-and-testing.md:140)。P-1 ゲートは `safety` marker 併記。**AST pin**: `rclpy` / `torch` / `tensorrt` / `numpy` / `requests` 非 import・走行系トピック名を含まない・ROS node クラス 0）。

---

## 【2026-09-17 追補 ⑧】`OQ-OD4Z-d` 裁定: 取付事前値からの逸脱上限の注入 + 平面支持の自己申告（契約 v0.1 additive）

正本 = 本 doc（[案 B :44](04-perception-sidewalk-and-signals.md:44)・[:174](04-perception-sidewalk-and-signals.md:174)・[:181](04-perception-sidewalk-and-signals.md:181)・[:182](04-perception-sidewalk-and-signals.md:182)・[:196](04-perception-sidewalk-and-signals.md:196)・[:203](04-perception-sidewalk-and-signals.md:203)・[追補 ④ 1-2](04-perception-sidewalk-and-signals.md:409)・[追補 ⑤ §1 段 3](04-perception-sidewalk-and-signals.md:520)・[追補 ⑤ `OQ-OD4Z-d`](04-perception-sidewalk-and-signals.md:592)）＋ [09 §2-i](09-external-review-v3-response.md:157) / [09 §2-b](09-external-review-v3-response.md:72)。**本追補は [追補 ⑤ :592](04-perception-sidewalk-and-signals.md:592) の `OQ-OD4Z-d`（素の RANSAC の fail-open）だけを閉じる**。ユーザー委任による裁定（2026-09-17）。

> **レイヤ注記**（[:7](04-perception-sidewalk-and-signals.md:7) と同軸）: `terrain_core.py` = **自律走行（安全層外）**の producer（costmap / X2 への入力・actuation 権限なし）。`warehouse_interfaces.perception` = **L2 Contract hub**（additive のみ）。本追補で **node・topic・launch・config は作らない**（0 node）。判定点は X2 の 1 か所のまま（[:203](04-perception-sidewalk-and-signals.md:203) / [09 §2-b](09-external-review-v3-response.md:72)）。

> **注意（接頭辞の衝突）**: `OQ-OD4Z-*` は追補 ⑤ / ⑥ / ⑦ が**それぞれ独立に**使っており、`OQ-OD4Z-d` は 3 か所に別の意味で存在する（⑤ = 本件 RANSAC fail-open [:592](04-perception-sidewalk-and-signals.md:592)／⑥ = 読めない `stamp_s`／⑦ = 距離帯の境界）。本追補が指すのは**⑤ の `OQ-OD4Z-d`** のみ。番号の詰め替えは下流の `04:NNN` pin を割るため行わず、採番規則の整理は §5 の残 OQ に置く。

### 1. 裁定（何を・なぜ）

[追補 ⑤ :592](04-perception-sidewalk-and-signals.md:592) が記録した fail-open は、「回廊の中ほどが欠測した下り段差シーンで、近傍の床と遠方の下段面を通る**傾いた**平面（実測 `a ≈ −0.044`／m）が水平面より inlier を集め、崖が `FLOOR_CONFIRMED` に見える」というもの。同行は裁定の選択肢として「取付 `θ` からの傾き上限を注入する」「平面支持を `ObservationQuality` へ additive で自己申告する」の 2 つを挙げていた。**裁定は両方を採る**——前者が誤りを止め、後者が「止まったこと／止め損ねたこと」を下流から観測可能にする。片方だけでは、①上限のみ＝誤りは減るが X2 は平面品質を見られないまま（[:592](04-perception-sidewalk-and-signals.md:592) の「下流 X2 からは観測できない」が残る）、②自己申告のみ＝X2 が見えても 04 自身は誤った平面で cell を分類し続ける（`cliff_scan` は既に誤っている）。

**裁定 1（04 側 = 観測妥当性の gate ＝ fit の事前拘束）**。[:181](04-perception-sidewalk-and-signals.md:181) の取付幾何（`h`・`θ`）は `back_project` の段で既に適用済であり、body frame における地面の事前値は `Z = 0` である（[追補 ⑤ §1 段 3](04-perception-sidewalk-and-signals.md:520)）。よって RANSAC の候補平面 `Z = a·X + b·Y + c` のうち、

- **傾き** `atan(hypot(a, b))` が `max_plane_tilt_rad` を超えるもの、または
- **原点高さ** `|c|` が `max_plane_offset_m` を超えるもの

は**候補として不採用**とする（constrained RANSAC ＝ 許容円錐の外の候補は**最良比較に参加させない**。inlier を数えてから落とすのではなく、比較の前に外す）。許容内で事前値に勝つ候補が無ければ事前値を保持する（`used_prior = True`）。事前値 `Z = 0` は傾き 0・`|c| = 0` ゆえ**常に許容内**であり、両上限が正である限り「候補ゼロで行き場を失う」ことはない。棄却した候補の件数は捨てずに `GroundPlane.rejected_candidates` として保持する（additive）。これは [:182](04-perception-sidewalk-and-signals.md:182)「前方 depth を 2D へ落とす際は高さしきい値と**地面傾き補正**が要る」の具体化にあたる。

**裁定 2（契約側 = 平面支持の自己申告・v0.1 additive）**。[追補 ④ 1-2](04-perception-sidewalk-and-signals.md:409) の `ObservationQuality` に optional field を足す（§3）。04 は**数値を申告するだけで判定しない**（[:203](04-perception-sidewalk-and-signals.md:203)）。閾値化（「傾きいくつで健康とみなすか」）は X2 の仕事であり、本追補では**決めない**（§5）。

**裁定 3（数値は発明しない）**。`max_plane_tilt_rad` / `max_plane_offset_m` は **`GroundFitParams` に既定なしで注入**する（追補 ⑤ §2 の流儀＝全パラメータ注入・既定なし）。正本は逸脱の上限そのものを持たないためである。値の**根拠候補**は [06 §3](06-hardware-delta-and-base-selection.md:58) の国土交通省基準（歩車道境界の段差 標準 2 cm）と [07:234](07-drivetrain-and-wheel-sizing.md:234) の横断勾配 2 %（= 0.02 ≈ 0.0200 rad）だが、これらは**歩道の構造値**であって「推定平面が取付事前値からどれだけ外れてよいか」ではない [I]。確定は配線時に 00_Platform_Contract 側 config から注入し、実測（[`OQ-OD45` :143](04-perception-sidewalk-and-signals.md:143) / [`OQ-OD4Q` :354](04-perception-sidewalk-and-signals.md:354)）と**同時に**行う。

### 2. パラメータ（`GroundFitParams` へ additive・**既定なし**・違反は構築時 `ValueError`）

| パラメータ | 単位 | 検証 | 意味 | 値の根拠候補（**既定にしない**） |
|---|---|---|---|---|
| `max_plane_tilt_rad` | rad | 有限・`> 0`（`0` / 負 / NaN は `ValueError`） | 事前値 `Z = 0` の法線から候補平面が傾いてよい上限。候補の傾きは `atan(hypot(a, b))` | [07:234](07-drivetrain-and-wheel-sizing.md:234) の横断勾配 2 %（≈ 0.0200 rad）は**横断方向のみ**の構造値。縦断勾配（切下げ・坂）は正本に無い [I] → 実測で決める |
| `max_plane_offset_m` | m | 有限・`> 0`（`0` / 負 / NaN は `ValueError`） | 候補平面の原点高さ `|c|` の上限＝取付高さ `h` の誤差・沈み込みの許容量 | [06 §3](06-hardware-delta-and-base-selection.md:58) の段差 標準 2 cm は**段差**であって取付誤差ではない [I]。`h` は車体未組立・未実測（追補 ⑤ §2） |

- `0` を拒むのは、`0` が「事前値以外の一切の観測を受け付けない」設定＝観測を無効化する構成になるため（`min_valid_fraction = 0` を拒むのと同じ理由・追補 ⑤ §2）。
- 上限は**片側の上界のみ**で、下界（「支持の下限」＝ inlier 率の最小値）は本裁定では**置かない**（2 本目のしきい値の出所が正本に無い＝[`OQ-OD4Z-b`](04-perception-sidewalk-and-signals.md:590) と同型）。支持は §3 の `ground_inlier_fraction` として**申告**し、判定は X2 に委ねる。

### 3. 契約 v0.1（`ObservationQuality` への additive・すべて optional・既定 `None`）

**既存 field（`valid_fraction` / `frame_digest` / `device_frame_seq` / `processing_latency_s`）は 1 文字も変えない**。平面を持たない producer（灯器観測 = `TrafficSignalObservation.quality`）は 5 field すべて `None` のままでよい＝**既存 payload はそのまま通る**（後方互換）。非有限（NaN / inf）は `allow_inf_nan=False` を継承して拒否（[:174](04-perception-sidewalk-and-signals.md:174)）。

| field | 型・範囲 | 意味 | fail 方向 | 出典 file:line |
|---|---|---|---|---|
| `ground_plane_tilt_rad` | `float \| None`・`>= 0`・有限・既定 `None` | 採用した地面平面が事前値 `Z = 0` から傾いている量 `atan(hypot(a, b))` | 負・非有限は `ValidationError`（傾きは大きさ＝符号を持たない）。`None` = 平面を推定しない producer | [:181](04-perception-sidewalk-and-signals.md:181) / [:182](04-perception-sidewalk-and-signals.md:182) / [:592](04-perception-sidewalk-and-signals.md:592) |
| `ground_plane_offset_m` | `float \| None`・**符号あり**・有限・既定 `None` | 採用した平面の原点高さ `c`（body 原点＝カメラ直下の地面） | 符号を拘束しない（沈み込み = 負・浮き = 正の両方が起きる。[:196](04-perception-sidewalk-and-signals.md:196) の `step_height_m` と同じ理由）。非有限は `ValidationError` | [:181](04-perception-sidewalk-and-signals.md:181) / [追補 ⑤ §1 段 3](04-perception-sidewalk-and-signals.md:520) |
| `ground_inlier_fraction` | `float \| None`・`[0,1]`・有限・既定 `None` | 採用平面を支持した点の割合＝**支持の自己申告** | 範囲外・非有限は `ValidationError`。`valid_fraction` と**同じ範囲規約**にして X2 が同型に扱えるようにする | [:203](04-perception-sidewalk-and-signals.md:203) / [:592](04-perception-sidewalk-and-signals.md:592) |
| `ground_from_prior` | `bool \| None`・既定 `None` | `True` = 許容内の候補が事前値に勝てず、**取付幾何の事前値で分類した**（観測が平面を上書きしていない） | `True` は「平面を観測で確認できていない」印であり、X2 はこれを**健康の低下**として扱える。`None` = 平面を推定しない producer | [追補 ⑤ §1 段 3](04-perception-sidewalk-and-signals.md:520) / [:592](04-perception-sidewalk-and-signals.md:592) |
| `ground_rejected_candidates` | `int \| None`・`>= 0`・既定 `None` | 許容円錐の外で**最良比較に参加させなかった**候補の件数（**非有限係数で外した候補を含む**。3 点が XY で共線＝平面が決まらない標本は「外した」のではなく判定対象が無いので**数えない**） | 負は `ValidationError`。`0` = 拘束が一度も効かなかった（＝この frame では素の RANSAC と同じ結果）。**大きい値は「観測が事前値と食い違っている」診断材料**で、それ自体は異常ではない | §1 裁定 1 / [:592](04-perception-sidewalk-and-signals.md:592) |

### 4. fail 方向（何が誤停止側で、何を交換したか）

| 場面 | 裁定前（素の RANSAC） | 裁定後（constrained RANSAC） | 向き |
|---|---|---|---|
| 回廊中ほど欠測 + 下り段差（[:592](04-perception-sidewalk-and-signals.md:592) の実例） | 傾いた平面が勝ち、崖が `FLOOR_CONFIRMED` | 傾き上限で候補が外れ、水平面（事前値）が残り `DROP_DETECTED` | **fail-open → fail-closed**（本裁定の目的） |
| 真に**下り勾配**の路面（事前値で分類） | 平面が勾配に追従し `FLOOR_CONFIRMED` | 残差 `r = Z − 0` が `−drop_threshold_m` 以下へ → `DROP_DETECTED` | **誤停止側**（崖でないものを崖と呼ぶ）= fail-closed |
| 真に**上り勾配**の路面（事前値で分類） | 同上 | 残差が `+plane_tolerance_m` を超え床点にならない → `UNKNOWN`（`DROP_DETECTED` にはならない） | **fail-closed**（未観測 = 通行不可。[:173](04-perception-sidewalk-and-signals.md:173) / `UNKNOWN` ≠ `DROP_DETECTED` = [:196](04-perception-sidewalk-and-signals.md:196)） |
| 取付 `h` が誤っている（例 3 cm 高い） | 観測が事前値を上書きして回復 | `|c|` が `max_plane_offset_m` 内なら**同じく回復**・外なら事前値のまま `UNKNOWN` 側 | 上限の設定次第で fail-closed へ倒れる（値を絞りすぎる危険＝§5） |
| 採用平面の数値そのもの | — | 採用平面は必ず許容内＝`|c|` と傾きが有界・有限 → §3 の自己申告が**契約違反値（NaN / inf）になり得ない** | 副次的な fail-closed（不正値が wire に出ない） |

**交換したもの**: 「崖を床と言う」誤り（人身・車体損傷に直結）を、「勾配を崖／未観測と言う」誤り（誤停止・可用性の低下）へ交換した。後者は**止まる側**なので安全としては正しい向きだが、**上限を絞りすぎると legal な歩道で走れなくなる** [I]。`max_plane_tilt_rad` は「真の路面勾配 < 上限 < fail-open を起こす傾き（本 doc の実例では `atan(0.044) ≈ 0.044` rad）」の窓に入れる必要があり、この窓が実際にどれだけ広いかは**実測でしか分からない**（§5）。データ異常（NaN / 空 / 点数不足）で例外を上げない規律は不変（[:174](04-perception-sidewalk-and-signals.md:174) / 追補 ⑤）。

### 5. OPEN QUESTIONS（本追補で**発明せずに残した**もの）

- `OQ-OD4Z-d1` **2 上限の値**。`max_plane_tilt_rad` / `max_plane_offset_m` は注入のままで pin しない。§4 の「窓」（真の路面勾配 < 上限 < fail-open 傾き）が成立するかは、[`OQ-OD45` :143](04-perception-sidewalk-and-signals.md:143)（縁石 2 cm の分離距離）・[`OQ-OD4Q` :354](04-perception-sidewalk-and-signals.md:354)（MinZ）と**同じ実測**で確かめる。窓が閉じている（真の勾配 ≥ fail-open 傾き）なら、単一のスカラー円錐では足りず縦断／横断を分けるか、事前拘束でなく多重仮説（複数平面の保持）へ設計を変える必要がある [I]。
- `OQ-OD4Z-d2` **傾きの分解**。上限は `atan(hypot(a, b))` の**単一スカラー**で、縦断（`a`）と横断（`b`）を区別しない。正本が持つ構造値は横断勾配 2 %（[07:234](07-drivetrain-and-wheel-sizing.md:234)）**だけ**で縦断勾配の上限は無い。2 本に分けるなら 2 本目の出所が要る（[`OQ-OD4Z-b`](04-perception-sidewalk-and-signals.md:590) と同型の問題）。
- `OQ-OD4Z-d3` **X2 側の閾値化**。§3 の 5 field を X2（`warehouse_safety.sensor_health`）がどう判定に使うか（`ground_from_prior = True` を INVALID とみなすか・`ground_inlier_fraction` の下限・`ground_rejected_candidates` の扱い）は**別レーン**。本追補は申告のみを決め、判定点は X2 の 1 か所のまま（[:203](04-perception-sidewalk-and-signals.md:203) / [09 §2-b](09-external-review-v3-response.md:72)）。`SourceObservation` への写し方は [追補 ④ 1-2](04-perception-sidewalk-and-signals.md:409) の対応表の拡張になる。
- `OQ-OD4Z-d4` **`estimate_error_m` との関係**。`TerrainCoverage.estimate_error_m` は「どの量の誤差か・統計的意味」が未定のまま（[`OQ-OD4Y-c`](04-perception-sidewalk-and-signals.md:488)）で、本追補の `ground_inlier_fraction` / `ground_plane_tilt_rad` は**その空欄を埋めていない**（支持と傾きは誤差の推定量ではない）。両者の関係（平面 fit の残差分布から `estimate_error_m` を出すか）は `OQ-OD4Y-c` の裁定時に決める。
- `OQ-OD4Z-d5` **`rejected_candidates` の意味の安定性**。件数は `ransac_iterations` に比例してスケールし、正規化されていない（率ではない）。X2 が閾値を置くなら率へ直す必要がある [I]。`ransac_iterations` は注入値ゆえ、件数だけを見て機器間・構成間で比較できない。
- `OQ-OD4Z-d6` **`OQ-OD4Z-*` の採番衝突**（本追補 冒頭注記）。追補 ⑤ / ⑥ / ⑦ が同一接頭辞を独立に使っている（⑤ = `-a`〜`-j` [:589](04-perception-sidewalk-and-signals.md:589)〜[:598](04-perception-sidewalk-and-signals.md:598)・⑥ = `-a`〜`-g` [:710](04-perception-sidewalk-and-signals.md:710)〜[:716](04-perception-sidewalk-and-signals.md:716)・⑦ = `-a`〜`-i` [:819](04-perception-sidewalk-and-signals.md:819)〜[:827](04-perception-sidewalk-and-signals.md:827)）。したがって **`-a`〜`-g` が 3 重・`-h` / `-i` が 2 重（⑤ と ⑦）・`-j` は ⑤ のみ**で、一律 3 重ではない。本追補は衝突を避けて `-d1`〜`-d6` を使ったが、恒久的な採番規則（追補ごとの接頭辞分離）は未決。詰め替えは下流 pin を割るため**行わない**。
- `OQ-OD4Z-d7` **`ground_from_prior` の厳格化（`Strict[bool]`）**。本追補は型を `bool | None` とだけ決めており、pydantic の既定（lax）モードの強制変換はそのまま継承する——`1` / `0` / `1.0` / `0.0` / `"yes"` / `"true"` / `"off"` は `bool` として通り、語彙の外（`2` / `0.5` / 任意の文字列）だけが `ValidationError` になる [D]（v0.1 の unit で実測確認）。「`bool` として読み戻せる」ことは保証されるが「`bool` しか書けない」ことは保証されない。2 状態（事前値 / 観測）しか無い flag なので実害は見えていないが、`Strict[bool]` を採るなら**ハブ全体の方針**（`schemas._Model` の `strict` 設定）になり本追補の射程を超える＝別 PR で裁定する。

---

## 【2026-09-18 追補 ⑤-1】追補 ⑤ §2 の v0.1 差分（表末尾 append の代替・行ズレ回避）

正本 = [追補 ⑤ §2](04-perception-sidewalk-and-signals.md:526)。本節は追補 ⑤ §2 のパラメータ表に**行を足さずに**同じ内容を持つ。理由は**行ズレ回避**である ── baseline `9d080cf` で `git grep -oE "04-perception-sidewalk-and-signals\.md[:#][0-9]+" -- '*.md' '*.py' '*.yaml'` を測ると、`:546` 以降（＝追補 ⑥ / ⑦ / ⑧）を指す pin は **28 occurrence・distinct 8 値**（`572` / `590` / `592` / `728` / `813` / `838` / `846` / `871`）あり、**6 ファイル**に分布する（本 doc 自身 14・`terrain_core.py` 8・`warehouse_interfaces/perception.py` 2・`productization/01` 2・`GLOSSARY.md` 1・`manifests/example.rf-detr-nano.yaml` 1）。§2 の表に 1 行でも挿入すると**そのすべてが同時に割れ**、docs だけでなく **3 パッケージの `.py` / `.yaml` まで同一 PR で 1:1 re-pin** することになる（[#165 教訓](../dev/03-retrospectives.md) / [status-maintenance.md :26 末尾追記原則](../../.claude/rules/status-maintenance.md:26)）。

### 1. `GroundFitParams` の v0.1 追加パラメータ（出典 = [追補 ⑧ §2](04-perception-sidewalk-and-signals.md:861)）

追補 ⑤ §2 の表は `GroundFitParams` を 3 param（[`plane_tolerance_m` :543](04-perception-sidewalk-and-signals.md:543) / [`ransac_iterations` :544](04-perception-sidewalk-and-signals.md:544) / [`ransac_seed` :545](04-perception-sidewalk-and-signals.md:545)）でしか書いていないが、[`OQ-OD4Z-d` 裁定（追補 ⑧）](04-perception-sidewalk-and-signals.md:838)で **5 param 必須**になった（実装 = `warehouse_perception/terrain_core.py` の `GroundFitParams`）。追加の 2 本も追補 ⑤ §2 と同じ流儀＝**注入・既定なし**・違反は構築時 `ValueError`:

| dataclass | パラメータ | 単位 | 検証 | 備考・出典 |
|---|---|---|---|---|
| `GroundFitParams`（追加分） | `max_plane_tilt_rad` | rad | 有限・`> 0`（`0` / 負 / NaN は `ValueError`）・**既定なし** | 事前値 `Z = 0` の法線から候補平面が傾いてよい上限（候補の傾き = `atan(hypot(a, b))`）。値は pin しない＝[追補 ⑧ §2](04-perception-sidewalk-and-signals.md:861)・実測は [`OQ-OD4Z-d1`](04-perception-sidewalk-and-signals.md:897) |
| | `max_plane_offset_m` | m | 有限・`> 0`（`0` / 負 / NaN は `ValueError`）・**既定なし** | 候補平面の原点高さ `c` の絶対値の上限＝取付高さ `h` の誤差・沈み込みの許容量。同じく値は pin しない＝[追補 ⑧ §2](04-perception-sidewalk-and-signals.md:861) |

### 2. 数値ガード（追補 ⑤ §2 が書き落としていた定数）

**数値ガード（しきい値ではない）**: 索引 floor/ceil の `1e-9`・退化標本の行列式下限 `1e-12`（`terrain_core.py` `_INDEX_EPS` / `_DEGENERATE_DET`）。浮動小数の丸め対策であり物理量の許容ではない。`_DEGENERATE_DET` は [追補 ⑧ §3](04-perception-sidewalk-and-signals.md:871)「共線標本は `ground_rejected_candidates` に数えない」の適用範囲を実際に定める。

---

## 【2026-09-18 追補 ⑨】07_Output_Adapters node v0（`terrain_publisher`）— `OQ-OD4Y-i` 裁定

正本 = 本 doc（親 §3 [:49](04-perception-sidewalk-and-signals.md:49)〜[:66](04-perception-sidewalk-and-signals.md:66)・2026-09-14 追補 [:173](04-perception-sidewalk-and-signals.md:173)〜[:181](04-perception-sidewalk-and-signals.md:181)・追補 ② [:202](04-perception-sidewalk-and-signals.md:202)・追補 ④ [:390](04-perception-sidewalk-and-signals.md:390)・追補 ⑤ / ⑤-1 / ⑧）+ [09 §2-b / §2-i](09-external-review-v3-response.md:72)。本追補は **[`OQ-OD4Y-i`](04-perception-sidewalk-and-signals.md:494)（topic 名・QoS・publish 周期）を裁定**し、追補 ⑤ の純ロジック `terrain_core.py` を rclpy node として配線した記録を残す。実装 = `ws/src/warehouse_perception/warehouse_perception/terrain_node.py`（rclpy shell）+ `terrain_node_core.py`（rclpy 非依存の marshalling 核）。doc03 カタログ行 = [03 末尾追補](../architecture/03-software-architecture.md:316)。

> **レイヤ注記**（[:7](04-perception-sidewalk-and-signals.md:7) と同軸・[追補 ⑧ :842](04-perception-sidewalk-and-signals.md:842) の `terrain_core.py` と同じ扱い）: `terrain_node.py` / `terrain_node_core.py` = **自律走行（安全層外）の producer**。consumer は L1 costmap / L1 `collision_monitor`（`cliff_scan`）と X2 / 06（`coverage`）。**`cmd_vel` / `stop_request` / `stop_state` / `speed_limit` の producer にならない**（AST pin `tests/unit/test_terrain_node_pins.py`）。GPU 非依存 = P1 原則（[23:37](../architecture/23-perception-and-localization.md:37)）。

### 1. 裁定 1〜9（`OQ-OD4Y-i`・ユーザー委任 2026-09-18）

| # | 項目 | 裁定 | 根拠 |
|---|---|---|---|
| 1 | **topic 名（凍結）** | 相対名 `cliff_scan` / `terrain/coverage` を `/bot{n}` namespace 下で解決（= `/bot1/cliff_scan` / `/bot1/terrain/coverage`）。**絶対名にしない**（namespace を跨ぐ） | 案の凍結 = [:59](04-perception-sidewalk-and-signals.md:59) / [:173](04-perception-sidewalk-and-signals.md:173)。相対名の理由 = [ADR-0012 決定 11](../adr/0012-speed-band-no-l2-best-effort.md) と同型 |
| 2 | **型・frame** | `cliff_scan` = `sensor_msgs/LaserScan`・`header.frame_id = <ns>/base_link`（`warehouse_description.robot_dimensions.BASE_FRAME`＝許可された共有資産）。`terrain/coverage` = `std_msgs/String`（`TerrainCoverage.model_dump_json()`） | [:56](04-perception-sidewalk-and-signals.md:56) VirtualScan 器の流用・[virtual_scan.py:72](../../ws/src/warehouse_traffic/warehouse_traffic/virtual_scan.py:72) と同じ `{robot}/base_link` 形／型 = [追補 ④](04-perception-sidewalk-and-signals.md:390)・JSON over String = [doc16 §3](../architecture/16-repository-and-conventions.md) |
| 3 | **stamp** | 両出力の時刻は**入力 depth Image の `header.stamp`**（`cliff_scan` は `header.stamp`、`coverage` は payload の `source_stamp_s`）。**`now()` を stamp に使わない**（`virtual_scan` は now() を使うが、あれは生成であり本 node は観測） | [:177](04-perception-sidewalk-and-signals.md:177)「変換後も元の計測時刻を維持」 |
| 4 | **QoS** | publisher / subscription とも **depth 10**（既定 RELIABLE / KEEP_LAST）。**新しい数値ではなく既存 producer の踏襲**: [virtual_scan.py:42](../../ws/src/warehouse_traffic/warehouse_traffic/virtual_scan.py:42)（`create_publisher(LaserScan, .../virtual_scan, 10)`）と [speed_band_node.py:73](../../ws/src/warehouse_perception/warehouse_perception/speed_band_node.py:73)（`speed_limit`）が同じ深さ | consumer との advisory 合意規約 = [doc03:78](../architecture/03-software-architecture.md:78)。consumer は本スライスに存在しない（§4 hand-off） |
| 5 | **周期** | **timer なし・depth frame ごとに 1 回**。再 publish しない（古い観測を新しい stamp で流さない）。`cliff_scan` の途絶＝停止は **X2 の義務**であり本 node は沈黙で表現する | [:66](04-perception-sidewalk-and-signals.md:66)（Humble CM は途絶 fail-open）/ [09 §2-b](09-external-review-v3-response.md:72) |
| 6 | **入力** | `depth_topic` / `camera_info_topic`（param・既定 `""` = 購読しない = safe-OFF）。`enabled`（既定 `false`）が true で片方でも空なら**起動時 abort**。encoding は `16UC1`（mm → m）と `32FC1`（m）のみ・それ以外 / `step` 不足 / バッファ不足は **frame 全体を無効**（空 rows → `valid_fraction = 0.0` → 全 `UNKNOWN` → 全 `inf`）で**例外を上げない**。`CameraInfo.k` から `fx = k[0]`・`fy = k[4]`・`cx = k[2]`・`cy = k[5]`。**CameraInfo 未受信・未校正（`fx`/`fy` が正でない）の間は出力しない**（intrinsics 無しに幾何は立たない） | safe-OFF 規約 = [speed_band_node.py:37](../../ws/src/warehouse_perception/warehouse_perception/speed_band_node.py:37)・fail-closed abort = [ADR-0012 決定 4](../adr/0012-speed-band-no-l2-best-effort.md)／無効深度で raise しない = [:174](04-perception-sidewalk-and-signals.md:174) + [追補 ⑤](04-perception-sidewalk-and-signals.md:500) |
| 7 | **サブサンプル** | `pixel_stride`（`int >= 1`・既定なし）で行・列を index 0 から間引き、intrinsics を `fx/s, fy/s, cx/s, cy/s` と**同じ比で縮小**する。pinhole の画像座標は線形ゆえ `(u − cx)/fx == (u/s − cx/s)/(fx/s)`＝**数学的に等価**で、`terrain_core` は不変更。numpy 経路は v0 に入れない | 純 python 性能は**未測**（`OQ-OD4Z-f` / 本追補 `OQ-OD4Y-i4`）。P1 = [23:37](../architecture/23-perception-and-localization.md:37) |
| 8 | **パラメータ** | **全部注入・既定なし**。ROS param の宣言既定は**検証で落ちる sentinel**（`0.0` / `0` / `""`）にし、`terrain_core` の `__post_init__` が `ValueError` → abort。§2 の表が全 21 本。**config キー案 `perception.terrain.*`（`speed_bands.*` と同型）は本 PR で作らない**＝bringup へ hand-off | 既定なしの原則 = [追補 ⑤ §2](04-perception-sidewalk-and-signals.md:526) / [追補 ⑤-1 §1](04-perception-sidewalk-and-signals.md:907)。値の根拠は正本に無い（車体未組立・カメラ未購入） |
| 9 | **hand-off** | nav-traffic / safety-state / bringup の 3 件（§4）。**本 PR では実装しない**（consumer 未配線） | [:214](04-perception-sidewalk-and-signals.md:214) 三分離・[:222](04-perception-sidewalk-and-signals.md:222) 判定点は X2 の 1 か所 |

### 2. パラメータ表（全 21 本・**既定なし**・宣言既定 = 落ちる sentinel）

| 束 | パラメータ | 単位 | 宣言 sentinel | 検証（落ちる理由） | 出典 |
|---|---|---|---|---|---|
| `MountingGeometry` | `camera_height_m` | m | `0.0` | `> 0`（`_positive`） | [:181](04-perception-sidewalk-and-signals.md:181) |
| | `pitch_down_rad` | rad | `0.0` | `(0, π/2)` の開区間 | [:181](04-perception-sidewalk-and-signals.md:181) |
| | `reference_offset_m` | m | `0.0` | 有限のみ（符号を拘束しない）＝**sentinel が正当値**（下記 ⚠） | [:176](04-perception-sidewalk-and-signals.md:176) |
| `TerrainGridParams` | `cell_size_m` | m | `0.0` | `> 0` | [追補 ⑤ §2](04-perception-sidewalk-and-signals.md:526) |
| | `corridor_half_width_m` | m | `0.0` | `> 0` ∧ `>= cell_size_m / 2` | [:175](04-perception-sidewalk-and-signals.md:175) |
| | `forward_range_m` | m | `0.0` | `> 0` ∧ `>= cell_size_m` | [追補 ⑤ §2](04-perception-sidewalk-and-signals.md:526) |
| | `min_points_per_cell` | 個 | `0` | `int >= 1`（0 は「誰も見ていない床」を確認してしまう） | [:56](04-perception-sidewalk-and-signals.md:56) `noDataObstacle` |
| | `drop_threshold_m` | m | `0.0` | `> 0` ∧ `> plane_tolerance_m`（床帯と崖帯は disjoint） | [:196](04-perception-sidewalk-and-signals.md:196) / [`OQ-OD45` :143](04-perception-sidewalk-and-signals.md:143) |
| | `min_valid_fraction` | 比 | `0.0` | `(0, 1]`（0 は有効画素ゼロの frame を受け入れる） | [:174](04-perception-sidewalk-and-signals.md:174) |
| `GroundFitParams` | `plane_tolerance_m` | m | `0.0` | `> 0` | [追補 ⑤ §2](04-perception-sidewalk-and-signals.md:526) |
| | `ransac_iterations` | 回 | `0` | `int >= 1` | [追補 ⑤ §2](04-perception-sidewalk-and-signals.md:526) |
| | `ransac_seed` | — | `0` | `int`（どの値も合法）＝**sentinel が正当値**（下記 ⚠） | [追補 ⑤ §2](04-perception-sidewalk-and-signals.md:526) / [doc20:131](../architecture/20-dev-quality-and-testing.md:131) |
| | `max_plane_tilt_rad` | rad | `0.0` | `> 0` | [追補 ⑧ §2](04-perception-sidewalk-and-signals.md:861) |
| | `max_plane_offset_m` | m | `0.0` | `> 0` | [追補 ⑧ §2](04-perception-sidewalk-and-signals.md:861) |
| `CliffScanParams` | `angle_min_rad` | rad | `0.0` | 有限＝**sentinel が正当値**（対で落ちる・下記 ⚠） | [追補 ⑤ §2](04-perception-sidewalk-and-signals.md:526) |
| | `angle_max_rad` | rad | `0.0` | 有限 ∧ `> angle_min_rad`＝**sentinel が正当値**（対で落ちる） | 同上 |
| | `ray_count` | 本 | `0` | `int >= 1` | 同上 |
| | `range_min_m` | m | `0.0` | `> 0` | 同上 |
| | `range_max_m` | m | `0.0` | `> 0` ∧ `> range_min_m` | 同上 |
| node | `reference` | — | `""` | 非空（datum のラベル。語彙は [`OQ-OD4Y-h`](04-perception-sidewalk-and-signals.md:493) が未決ゆえ既定を発明しない） | [:176](04-perception-sidewalk-and-signals.md:176) |
| node | `pixel_stride` | 画素 | `0` | `int >= 1` | 本追補 裁定 7 |

> ⚠ **sentinel が正当値でもある 4 本**（隠さず記録する＝`OQ-OD4Y-i2`）: `reference_offset_m`・`angle_min_rad`・`angle_max_rad`・`ransac_seed`。`0` に物理的意味があるため単体では検証で落とせない。**「何も注入しない起動」は依然 abort する**（`camera_height_m = 0.0` が即落ちる）し、`angle_min` / `angle_max` は**両方 sentinel なら対として落ちる**、`ransac_seed` は同群の `ransac_iterations = 0` が落とす——よって起動ゲートとしての fail-closed は成立している。塞げないのは「他を全部注入したうえで、この 4 本のうち 1 本だけ注入し忘れる」場合だけで、その集合は unit で**列挙固定**した（`tests/unit/test_terrain_node_core.py`。増えたら赤くなる）。

### 3. fail 方向（何が起きたとき、どちらへ倒れるか）

| 場面 | 挙動 | 向き |
|---|---|---|
| `enabled` 未設定（既定） | subscription 0・publisher 0・timer 0（topic graph に現れない） | **safe-OFF** |
| `enabled:=true` かつ param 未注入 | 起動時 `ValueError` → プロセス abort（推測しない） | **fail-closed** |
| `enabled:=true` かつ `depth_topic` / `camera_info_topic` が空 | 起動時 abort（「有効なのに入力なし」で健康に見えたまま無言になるのを防ぐ） | **fail-closed** |
| namespace が `/`（robot 名なし） | 起動時 abort（frame_id が誰の `base_link` か決まらない） | **fail-closed** |
| CameraInfo 未受信 / 未校正 `k` | **何も publish しない**（警告ログのみ）。推測 intrinsics で幾何を立てない | **fail-closed**（観測の不在＝未観測 = 通行不可） |
| 未対応 encoding・`step` 不足・バッファ不足 | frame 全体を無効化し、`UNKNOWN` / 全 `inf` を**出す**（例外は上げない） | **fail-closed**（未観測として届く） |
| depth frame が来ない（カメラ死亡・USB 断） | 沈黙。本 node は補わない | 途絶の検出は **X2 の義務**（[:66](04-perception-sidewalk-and-signals.md:66) / [09 §2-b](09-external-review-v3-response.md:72)） |
| 出力時刻 − 元計測時刻が**負**（時計取違え） | frame は publish し、`processing_latency_s = None`（証拠の不在として申告） | 自己申告のみ・判定は X2（`OQ-OD4Y-i3`） |
| 崖が scan の方位窓 / 距離窓の外 | `CliffScan.omitted_cell_count` に計上（黙って捨てない・clamp しない） | 設定不備を consumer が見える（[追補 ⑤](04-perception-sidewalk-and-signals.md:500)） |

### 4. hand-off（docs に明記・**本 PR では実装しない**）

1. **nav-traffic**（`track:nav-traffic`・`nav2_params.yaml` / `collision_monitor.yaml` は nav-traffic 所有 = [12:552](../architecture/12-infrastructure-common.md:552)）: `nav2_params.yaml` に **崖専用 ObstacleLayer instance**（例 `cliff_layer`・source は `cliff_scan` のみ・`clearing` なし・max 合成）を足し、`collision_monitor.yaml` の `observation_sources` に `cliff_scan`（type `scan`）を足す。`clearing:false` だけでは**同一 layer 内の別 source の raytracing から守れない**（[追補 ③ #2](04-perception-sidewalk-and-signals.md:380) [D]）。consumer 側 costmap layer の所有裁定は [`OQ-OD4H`](04-perception-sidewalk-and-signals.md:345)、`source_timeout` の扱いは [`OQ-OD44`](04-perception-sidewalk-and-signals.md:142)。
2. **safety-state**（`track:safety-state`・`warehouse_safety/sensor_health.py` は safety-state 所有）: X2 が `/bot{n}/terrain/coverage` を購読し、`TerrainCoverage.quality`（`ObservationQuality`）を `SourceObservation` へ写す。**field 名の差**（`frame_digest` ↔ `digest`・`source_stamp_s` ↔ `stamp_s`）はどちらへ寄せるかが [`OQ-OD4Y-a`](04-perception-sidewalk-and-signals.md:486)、`ground_*` 5 field の閾値化は [`OQ-OD4Z-d3`](04-perception-sidewalk-and-signals.md:899)。鮮度 `age = now − source_stamp_s` の検査は**消費側の義務**（[追補 ④ §2](04-perception-sidewalk-and-signals.md:475)）。
3. **bringup**（`track:skeleton` / bringup 所有・`config/warehouse.base.yaml` = [environments.md](../../.claude/rules/environments.md)）: launch に `terrain_publisher` を足し、§2 の 21 param を **config キー `perception.terrain.*`（案・`speed_bands.*` と同型）**から注入する。値は**車体組立とカメラ取付の実測後**（[`OQ-OD45` :143](04-perception-sidewalk-and-signals.md:143) 縁石 2 cm・[`OQ-OD4Q` :354](04-perception-sidewalk-and-signals.md:354) MinZ・[`OQ-OD4Z-d1`](04-perception-sidewalk-and-signals.md:897) の 2 上限と**同じ実測**）。 **【2026-09-18 配線済 → 追補 ⑫】**

### 5. OPEN QUESTIONS（本追補で**発明せずに残した**もの・接頭辞は [`OQ-OD4Z-d6`](04-perception-sidewalk-and-signals.md:902) の採番衝突を避けて `-i1`〜`-i5`）

- `OQ-OD4Y-i1` **`terrain/coverage` の時刻の運び方**。`std_msgs/String` は `header` を持たないため、時刻は payload の `source_stamp_s` だけにある。`cliff_scan` 側は `header.stamp` と `CliffScan.source_stamp_s` の**二重**になる（同じ値を書いている）。`.msg` 化（Phase 4・[doc16 §3](../architecture/16-repository-and-conventions.md)）の際にどちらを正にするかは未決。
- `OQ-OD4Y-i2` **sentinel が正当値でもある 4 本**（§2 ⚠）。`reference_offset_m` / `angle_min_rad` / `angle_max_rad` / `ransac_seed` は「1 本だけ注入し忘れ」を検出できない。ROS 2 の**型のみ宣言**（`declare_parameter(name, Parameter.Type.DOUBLE)` = 未設定なら `get_parameter` が上げる）へ寄せれば塞げるが、**Humble rclpy での挙動を本レーンでは実機確認できていない**（host に rclpy が無い）ため v0 では採らなかった。ボードで確認したうえで裁定する。
- `OQ-OD4Y-i3` **負の遅延の扱い**。出力時刻 − 元計測時刻が負のとき v0 は `processing_latency_s = None` で frame を出す（契約は非有限・負を拒否するため値としては載せられない）。「時計取違えの frame を**そもそも無効化**する」設計もあり得る。判定点は X2 の 1 か所（[:203](04-perception-sidewalk-and-signals.md:203)）という原則とどちらが整合するかは未決。[09 規則 (4)](09-external-review-v3-response.md:67)（元計測時刻 vs 受信側の単調時計）と同じ裁定に含める。
- `OQ-OD4Y-i4` **実解像度・実周期での性能**。純 python の `struct` 逐次 unpack は**未測**（カメラ未購入・車体未走行）。`pixel_stride` で間引く前提だが、どの解像度・どの stride で何 Hz 出るかはボード実測でしか分からない（[`OQ-OD4Z-f`](04-perception-sidewalk-and-signals.md:594) と同じ穴）。numpy 経路の要否もその後。
- `OQ-OD4Y-i5` **凍結フレームの検出**。[追補 ③ #5](04-perception-sidewalk-and-signals.md:383) は「同一画像は機器側フレーム番号・計測時刻・受信状態と併用して確定」と言うが、`sensor_msgs/Image` は機器フレーム番号を運ばない。v0 は `device_frame_seq=None`（持っていないものを申告しない）とし、`frame_digest` だけを出す。OAK 経由なら `i_get_base_device_timestamp` が時刻契約の実装点（[追補 ② §4](04-perception-sidewalk-and-signals.md:284)）だが、フレーム番号の経路は未定。

### 6. 実装・テスト（本 PR）

- `ws/src/warehouse_perception/warehouse_perception/terrain_node.py` — rclpy shell（`TerrainPublisher(Node)` + `main()`）。**終了 3 規則**（`KeyboardInterrupt` / `ExternalShutdownException` の両捕捉・best-effort・`rclpy.try_shutdown()`）を満たし、`tests/unit/test_node_shutdown_lifecycle.py` の `KNOWN_UNSAFE_STOP_ON_HUMBLE` baseline には**足していない**（最初から合格）。
- `ws/src/warehouse_perception/warehouse_perception/terrain_node_core.py` — **rclpy / numpy 非依存**の marshalling 核（depth decode・intrinsics 抽出と縮小・param 束・LaserScan フィールド・coverage JSON・時刻）。`terrain_core.py` は**1 文字も変えていない**。
- `tests/unit/test_terrain_node_core.py`（`unit` + `safety`）— 期待値は `struct.pack` 合成バッファからの**手計算リテラル**（mm → m・エンディアン・stride）。`tests/unit/test_terrain_node_pins.py` — AST pin（publish は 2 topic のみ・走行 / 停止系 topic 名を持たない・stamp は入力 msg の header 由来・safe-OFF の早期 return が全生成より前・param 宣言集合 == core が読む集合・QoS depth 10・終了 3 規則・numpy / torch 非 import）。
- `setup.py` に console_script `terrain_publisher`、`package.xml` に `sensor_msgs` / `warehouse_description`。produce / consume は [`ws/src/warehouse_perception/CLAUDE.md`](../../ws/src/warehouse_perception/CLAUDE.md)。

## 【2026-09-18 追補 ⑩】nav-traffic consumer: 崖専用 ObstacleLayer instance ＋ `collision_monitor` source `cliff_scan`（実装記録・レーン A が記入）

> **stub（先置き）**: 追補 ⑨ §4 hand-off の実装記録をレーン A（`feat/nav-cliff-layer`）が本節に記入する。先置きは並列 append の hunk 衝突と [#165](../dev/03-retrospectives.md) 行ズレの回避が目的（P0 #707 と同じ手法）。

正本 = 追補 ⑨ §4 hand-off ①（[:990](04-perception-sidewalk-and-signals.md:990)）＋ costmap 行（[:178](04-perception-sidewalk-and-signals.md:178)）＋ 追補 ③ #2（[:380](04-perception-sidewalk-and-signals.md:380) [D]）。producer（`terrain_publisher` / `/bot{n}/cliff_scan`・[doc03:322](../architecture/03-software-architecture.md:322)）は**この PR では一切触らない**。**レイヤ**（[layer-annotation.md](../../.claude/rules/layer-annotation.md)）: costmap 崖 layer = **06 Navigation** の consumer（三分離 ②「この車体が通れる形状」側の入口）、`collision_monitor` の source = **L1** 反射（[:66](04-perception-sidewalk-and-signals.md:66)「L1 が見るのは契約化された LaserScan 型だけ」）。`cmd_vel` 経路・twist_mux 優先度・凍結契約 `warehouse_interfaces` は**不変**。

### 1. 裁定（3 件）

| OQ | 裁定 | 根拠 |
|---|---|---|
| [`OQ-OD4H` :345](04-perception-sidewalk-and-signals.md:345)（consumer 側 costmap layer の所有） | **nav-traffic 所有**（04 = producer のみ）。`nav2_params.yaml` / `collision_monitor.yaml` / `nav2_bringup.launch.py` の編集は `feat/nav-traffic`。 | [doc16:193](../architecture/16-repository-and-conventions.md:193)（`feat/nav-traffic` = `bringup/config/nav2*`）＋ [doc16:126](../architecture/16-repository-and-conventions.md:126)（1 ファイル 1 責務＝別担当は別ファイル）＋ [`warehouse_bringup/CLAUDE.md`](../../ws/src/warehouse_bringup/CLAUDE.md) 編集境界。hand-off ① [:990](04-perception-sidewalk-and-signals.md:990) が既に同じ所有を前提にしている。 |
| [`OQ-OD44` :142](04-perception-sidewalk-and-signals.md:142)（`source_timeout` 契約） | **scan 型（途絶＝停止側）**。per-source override は**置かない**（node-level 1.0 s が効いたまま）。ただし **Humble では CM 内で停止にならない**ので、途絶検出の義務は CM の外（X2 鮮度 [:66](04-perception-sidewalk-and-signals.md:66) / Guardian `scan_stale` [doc12 追補 (3)](../architecture/12-infrastructure-common.md:677)）に残る。 | 崖は「沈黙＝近くに無い」ではなく「沈黙＝見えていない」（[:66](04-perception-sidewalk-and-signals.md:66) [I]）＝`virtual_scan` の 0.0 免除を**流用しない**。Humble 1.1.20 は per-source key を宣言せず途絶は点が消えるだけ（[doc12 追補 (1)](../architecture/12-infrastructure-common.md:677)）／Jazzy 以降は enabled な source の無データ＝`invalid source` STOP（下表）＝崖では**望ましい向き**。 |
| global costmap にも入れるか | **両方に入れる**（local ＋ global）。 | hand-off ① は costmap を限定していない。**plan を崖の上に引かせない**には planner が読む global 側が要る（local だけなら経路は崖を通り、controller が直前で止まるだけ）。代償＝global は rolling でないため崖セルが残り続ける（§4・`OQ-OD4Y-l2`）。 |

### 2. costmap 設定（[nav2_params.yaml:247](../../ws/src/warehouse_bringup/config/nav2_params.yaml:247) local ／ [:293](../../ws/src/warehouse_bringup/config/nav2_params.yaml:293) global・**同値**）

一次情報 = Humble `nav2_costmap_2d`（`humble` ブランチ・**参照日 2026-09-18**・[D]＝原文を取得して照合）:
<https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_costmap_2d/plugins/obstacle_layer.cpp> ／ <https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_costmap_2d/src/observation_buffer.cpp> ／ <https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_costmap_2d/src/costmap_layer.cpp>。

| key | 値 | 出所（値を発明していない根拠） | 効いている**関係** |
|---|---|---|---|
| `plugin` | `nav2_costmap_2d::ObstacleLayer` | 追補 ③ #2 [:380](04-perception-sidewalk-and-signals.md:380) | **別 instance** ゆえ自分の grid と observation buffer を持つ。`obstacle_layer.cpp:440-443` の `raytraceFreespace` は**その layer の** clearing observation だけを回す → 隣の `scan`（`clearing: true`）は崖セルに届かない。`updateCosts` の `combination_method` 既定 1 = `updateWithMax`（`obstacle_layer.cpp:83`・`:553-562`）で master へ **max 合成**。 |
| `enabled` | `true`（無条件） | — | producer が居なくても**無害**: ① `expected_update_rate` 既定 0.0 → `ObservationBuffer::isCurrent()` は常に true（`observation_buffer.cpp:212-216`）＝costmap を "not current" にしない。② 空 layer の grid は local では `FREE_SPACE`（`track_unknown_space` 無し）→ max 合成で何も書かない、global では `NO_INFORMATION` → `costmap_layer.cpp:124-127` が skip。**よって CM と違い追加 gate を置かない**（gate を増やすほど「効いていない安全網」が増える）。 |
| `observation_sources` | `cliff_scan` **のみ** | hand-off ① [:990](04-perception-sidewalk-and-signals.md:990) | 空白区切り**文字列**（`obstacle_layer.cpp:127-131` が `stringstream` で分割）。1 本だけ＝この layer に clearing source が存在しない。 |
| `cliff_scan.topic` | `cliff_scan`（**相対**） | [doc03:322](../architecture/03-software-architecture.md:322) | namespace push 下で `/bot{n}/cliff_scan` に解決。絶対 `/cliff_scan` は**誰も publish しない topic を黙って購読**する（無言の無効化）。 |
| `cliff_scan.data_type` | `LaserScan` | [doc03:322](../architecture/03-software-architecture.md:322)・既定も同じ（`obstacle_layer.cpp:141`） | 非 DROP セルは `inf` のまま届く。`inf_is_valid` は**既定 false**（`:144`）＝`inf` 光線は `range_max` に変換されず投影で落ちる → **非 DROP 光線は何も mark しない**。だから `inf_is_valid` は書かない（true にすると全方位が壁になる）。 |
| `cliff_scan.marking` | `true`（既定と同じ・`:145`） | [:178](04-perception-sidewalk-and-signals.md:178) | DROP セルを `LETHAL_OBSTACLE` に置く。 |
| `cliff_scan.clearing` | `false`（既定と同じ・`:146`） | [:178](04-perception-sidewalk-and-signals.md:178) / [:380](04-perception-sidewalk-and-signals.md:380) | **既定と同値でも明示する**: この 1 行が「この layer には clearing source が無い」＝崖が消えない根拠だから、既定に依存して沈黙しない。 |
| `cliff_scan.obstacle_max_range` | `2.5` | Humble 既定（`obstacle_layer.cpp:147`）＝在ファイル `scan` と同値（[:228](../../ws/src/warehouse_bringup/config/nav2_params.yaml:228)）。**新しい数値ではない** | marking loop は `dist > cellDistance(obstacle_max_range)` の点を**黙って捨てる**（`obstacle_layer.cpp:496-499`）。よって**この値 < producer の `range_max_m` なら、遠方の DROP セルは fail-open で消える**。値そのものではなく **`obstacle_max_range >= range_max_m` という関係**が契約（`range_max_m` は env 注入で YAML は静的 → 整合の検査点は配線時。`OQ-OD4Y-l1`）。 |
| `cliff_scan.max_obstacle_height` | `2.0` | layer 既定（`obstacle_layer.cpp:82`）＝在ファイル `scan` と同値（[:227](../../ws/src/warehouse_bringup/config/nav2_params.yaml:227)）。**新しい数値ではない** | **per-source の既定は `0.0`**（`obstacle_layer.cpp:143`）で、`ObservationBuffer::bufferCloud` は `min <= z <= max` の点しか残さない（`observation_buffer.cpp:143-144`）。省略すると **TF 後に z が厳密に 0.0 の点しか通らない**＝base_link に僅かでも z オフセットが入った日に**崖が全部消える fail-open**。明示はこの罠を塞ぐためで、高さ窓を広げる意図ではない。 |
| `raytrace_*` | **書かない** | — | `clearing: false` の source は clearing buffer に入らず `raytraceFreespace` に渡らない（`obstacle_layer.cpp:440-443`）＝無意味なノブを置かない。 |
| `plugins` 順序 | `[..., "obstacle_layer", "cliff_layer", "inflation_layer"]` | [:216](../../ws/src/warehouse_bringup/config/nav2_params.yaml:216) / [:259](../../ws/src/warehouse_bringup/config/nav2_params.yaml:259) | `inflation_layer` は**最後**＝崖セルも膨張対象（footprint 保護）。2 つの obstacle instance の相対順は max 合成ゆえ結果に影響しないが、inflation の後ろに落ちないよう unit で pin。 |

> **記法と行ドリフト**: 両 layer は**1 行の flow mapping**で書き、直前の空行を消費した（**net-zero**）。理由は `nav2_params.yaml` の行が 11 本の doc / code から `path:NN` で参照されており（`:254`/`:256`/`:258`/`:259`/`:262`/`:274`/`:291`/`:300` ほか）、block 記法（各 layer 11 行）だと**下流参照が一斉にずれる**（[#165](../dev/03-retrospectives.md)）。同ファイルの [`:202`](../../ws/src/warehouse_bringup/config/nav2_params.yaml:202)（ADR-0012 `reset_period`）が同じ理由で net-zero を取った先例。**参照側 doc は本レーンの編集境界外**のため、ずらさない方を選んだ。

### 3. `collision_monitor` の source と arming（[collision_monitor.yaml:76](../../ws/src/warehouse_bringup/config/collision_monitor.yaml:76) ＋ EOF 追記・[nav2_bringup.launch.py:219](../../ws/src/warehouse_bringup/launch/nav2_bringup.launch.py:219)）

- yaml: `observation_sources` に `cliff_scan` を追加（`type: scan` / `topic: cliff_scan`（相対）/ **`enabled: false`**）。**per-source `source_timeout` は無し**（§1 の `OQ-OD44` 裁定）。既存 `scan` / `virtual_scan` の値は不変。**EOF 追記**なので `:22-25`〜`:90` の pinned 行は 1 行も動かない（行数 90 → 110）。
- launch: `parameters=[configured_collision_params, *virtual_scan_timeout_overrides(...), *cliff_sources(load_config())]`。`cliff_sources` は `warehouse_bringup.collision_monitor_distro` の**純関数**で、config `perception.terrain.enabled`（hand-off ③ [:992](04-perception-sidewalk-and-signals.md:992)・**値はレーン C が `config/warehouse.base.yaml` に置く。本レーンは読むだけ**）が真のときだけ `[{"cliff_scan": {"enabled": True}}]` を返す。yaml を「静的な Humble の正」に保ち、**条件付きのものは launch で後置注入**する既存の形（`virtual_scan_timeout_overrides`）と同型。
- **なぜ既定 OFF ＋ launch arming か**（本節の設計核心）: producer は既定 OFF（[:941](04-perception-sidewalk-and-signals.md:941)）で、**dev cockpit（Jazzy + Gazebo）には depth camera が無い**＝publisher が存在しない。Jazzy では enabled な source の無データが `invalid source` → **STOP**（`collision_monitor_node.cpp:437-447`）なので、無条件に足すと cockpit が起動直後に止まる。一方 **enabled false の source は両 distro でその判定より前に skip** される（humble `collision_monitor_node.cpp:357-360` `if (source->getEnabled())` ／ jazzy `:437`）＝既定 OFF なら**どの distro でも完全に不活性**。arming 点は launch 1 か所だけ。
- 一次情報（`humble` / `jazzy` ブランチ・**参照日 2026-09-18**・[D]）: <https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_collision_monitor/src/collision_monitor_node.cpp> ／ <https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_collision_monitor/src/source.cpp>（`getCommonParameters` が読むのは `.topic` / `.enabled` のみ＝`:60-75`）／ <https://raw.githubusercontent.com/ros-navigation/navigation2/jazzy/nav2_collision_monitor/src/collision_monitor_node.cpp> ／ <https://raw.githubusercontent.com/ros-navigation/navigation2/jazzy/nav2_collision_monitor/src/source.cpp>（per-source `source_timeout` は jazzy `:74-78`・`:88` の `!= 0.0` で無効化）。[doc12 追補 (1)/(2)](../architecture/12-infrastructure-common.md:677) の照合結果と矛盾しないことを再確認した。

### 4. fail 方向（何が起きたとき、どちらへ倒れるか）

| 場面 | costmap 側 | collision_monitor 側 | 向き |
|---|---|---|---|
| `perception.terrain.enabled` 未設定（既定・現状） | layer は在るが観測ゼロ＝master に何も書かない | source は `enabled: false` で skip | **safe-OFF**（今日の挙動と bit 等価） |
| producer 有効・正常 | DROP セルが `LETHAL_OBSTACLE`（inflation も乗る） | 崖点が stop polygon に入れば停止 | 意図どおり |
| producer 有効・**途絶**（カメラ死・USB 断） | 既存セルが残る（local は rolling で流れる・global は残留） | **Humble = fail-open**（点が消えるだけ）／**Jazzy+ = STOP** | Humble の穴は CM の外で塞ぐ＝X2 鮮度（[:66](04-perception-sidewalk-and-signals.md:66)）/ Guardian `scan_stale`（[doc12 追補 (3)](../architecture/12-infrastructure-common.md:677)）。**本 PR はこの穴を塞いでいない**（塞いだと書かない） |
| 偽 DROP（RANSAC 誤り等・[`OQ-OD4Z-d` :592](04-perception-sidewalk-and-signals.md:592)） | local: 車体が離れれば rolling window から出て消える／**global: 消えない**（clear する source が無い） | 該当セルに近づけば停止 | **fail-closed**（安全側）だが global は**可用性リスク**＝再計画を恒久に塞ぐ。復旧は Nav2 の `clear_entirely_global_costmap`（`ObstacleLayer::reset()` = `obstacle_layer.cpp:756-762`）＝運用手段はある。`OQ-OD4Y-l2` |
| 崖が producer の角度窓 / 距離窓の外 | 何も来ない（`CliffScan.omitted_cell_count` に計上・[:986](04-perception-sidewalk-and-signals.md:986)） | 同左 | 設定不備は producer 側で見える。costmap 側の `obstacle_max_range` による切り捨ては**見えない**＝`OQ-OD4Y-l1` |

> **[:178](04-perception-sidewalk-and-signals.md:178) の「新しい床面観測でのみ clear」は本 v0 では実装できていない**（隠さず記録）。clear させる唯一の口は同一 layer 内の clearing source だが、`FLOOR_CONFIRMED` は LaserScan に落ちない（`UNKNOWN` と同じく [:173](04-perception-sidewalk-and-signals.md:173) の制約）ので、「床を確認したセルだけを raytrace する scan」を別 topic で出す設計が要る（`terrain/coverage` からの派生など）。v0 の実挙動は上表のとおり **local = 幾何的に忘れる / global = 忘れない**であり、「時間経過で床へ戻す」実装は**入れていない**（それが [:178](04-perception-sidewalk-and-signals.md:178) の禁じている方向だから）。→ `OQ-OD4Y-l2`。

### 5. OPEN QUESTIONS（本追補で**発明せずに残した**もの・接頭辞 `-l1`〜`-l5`。`OQ-OD4Y-l` 系が未使用であることを `grep` で確認済）

- `OQ-OD4Y-l1` **`obstacle_max_range` と producer の `range_max_m` の整合点**。前者は静的 YAML（2.5 = Humble 既定）、後者は注入（[追補 ⑤ §2](04-perception-sidewalk-and-signals.md:526) の `CliffScanParams.range_max_m`・既定なし）。`obstacle_max_range < range_max_m` なら遠方の DROP を**黙って捨てる**（fail-open）が、両者を突き合わせる仕掛けは今どこにも無い。config 側で 1 か所にするか、起動時に検査するか（＝どちらが正本か）は未決。`collision_monitor` 側にはこの窓が無い（`Scan::getData` は `range_min..range_max` をそのまま使う）ので、**同じ崖が CM には見えて costmap には見えない**組合せがありうる。
- `OQ-OD4Y-l2` **崖セルの clear 条件**（[:178](04-perception-sidewalk-and-signals.md:178) の未実装分）。`FLOOR_CONFIRMED` を clearing source に変える設計（床確認セルだけを撃つ別 LaserScan topic）を採るか、global costmap を rolling にするか、運用で `clear_entirely_global_costmap` を叩くか。**偽 DROP 1 個が global を恒久に塞ぐ**ので、実走行の前に裁定が要る。
- `OQ-OD4Y-l3` **高さ窓の下側**。`min_obstacle_height` は per-source / layer とも既定 0.0 のままにした（負値は発明になる）。TF 後の z が僅かに負になる構成では崖点が落ちる（fail-open）。`base_footprint` 導入や IMU 姿勢反映のときに再検討。
- `OQ-OD4Y-l4` **既存 source の同じ罠**（本レーンでは直していない）: `virtual_scan`（local [:230](../../ws/src/warehouse_bringup/config/nav2_params.yaml:230) / global [:282](../../ws/src/warehouse_bringup/config/nav2_params.yaml:282)）と global の `scan`（[:275](../../ws/src/warehouse_bringup/config/nav2_params.yaml:275)）は per-source `max_obstacle_height` を持たない＝既定 0.0 で「z が厳密に 0.0 の点」しか通していない。現状は odom→base_link が z=0 で成立しているだけで、契約ではない。直すのは別 PR（`track:nav-traffic`）＝**本 PR は崖以外の既存挙動を 1 bit も変えない**方針。
- `OQ-OD4Y-l5` **実機・sim での検証が無い**。host に ROS が無いため、本スライスの検証は YAML / AST / 純関数の unit だけで、costmap が実際に崖セルを marking する画は**見ていない**。dev cockpit は depth camera を持たないので、最初の実観測は実機カメラ装着後（[`OQ-OD45` :143](04-perception-sidewalk-and-signals.md:143) の実測ゲートと同時）。**「配線した」とは言えるが「効くことを確認した」とは言わない**（[build-deploy-run.md](../../.claude/rules/build-deploy-run.md) の記録規律と同じ）。

### 6. 実装・テスト（本 PR）

- [`ws/src/warehouse_bringup/config/nav2_params.yaml`](../../ws/src/warehouse_bringup/config/nav2_params.yaml) — `plugins` 2 行（[:216](../../ws/src/warehouse_bringup/config/nav2_params.yaml:216) / [:259](../../ws/src/warehouse_bringup/config/nav2_params.yaml:259)）に `cliff_layer` を挿入 ＋ 両 costmap に `cliff_layer`（[:247](../../ws/src/warehouse_bringup/config/nav2_params.yaml:247) / [:293](../../ws/src/warehouse_bringup/config/nav2_params.yaml:293)）。**行数 342 のまま**（§2 の注記）。
- [`ws/src/warehouse_bringup/config/collision_monitor.yaml`](../../ws/src/warehouse_bringup/config/collision_monitor.yaml) — [:76](../../ws/src/warehouse_bringup/config/collision_monitor.yaml:76) 同一行に `cliff_scan` 追加 ＋ EOF に source 定義（既定 OFF）。既存行は不動。
- [`ws/src/warehouse_bringup/warehouse_bringup/collision_monitor_distro.py`](../../ws/src/warehouse_bringup/warehouse_bringup/collision_monitor_distro.py) — `terrain_enabled(config)` / `cliff_sources(config)` を EOF 追記（ROS 非依存の純関数・既存 `virtual_scan_timeout_overrides` は**1 文字も変えていない**）。同 module に置いたのは、launch の import 行を**増やさず**同一行で済ませるため（launch は `:49`/`:126`/`:166-180`/`:187`/`:248-270` などが 20 行以上から参照されており、1 行の挿入でも下流参照が割れる）。
- [`ws/src/warehouse_bringup/launch/nav2_bringup.launch.py`](../../ws/src/warehouse_bringup/launch/nav2_bringup.launch.py) — collision_monitor Node の `parameters` に `*cliff_sources(load_config())` を追加（＋ import 行 1 本の同一行拡張・上のコメント 4 行 → 3 行で**行数 456 のまま**）。
- `tests/unit/test_cliff_layer_config.py`（`unit` + `safety`）— 純 YAML ＋ AST ＋ 純関数。期待値は**仕様リテラルと上流既定**（実装の読み返しではない）。既存 `tests/unit/test_collision_monitor_config.py` / `test_collision_monitor_distro_params.py` は `observation_sources` の**厳密一致**と「Starred はちょうど 1 個」を pin していたため、意図を保ったまま 3 本目 source ／ 2 本目 override を許す形へ更新した（**この 2 つの assertion 以外は不変**）。

<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->

## 【2026-09-18 追補 ⑪】safety-state consumer: `TerrainCoverage` → X2 `SourceObservation` アダプタ（`OQ-OD4Y-a` 裁定・node 配線は `OQ-OD95` 後）（実装記録・レーン B が記入）

> **（記入済 = PR #722・2026-09-18）**: 追補 ⑨ §4 hand-off ② の実装記録をレーン B（`feat/safety-x2-terrain-adapter`）が本節に記入した。stub 先置き（#721）の目的は並列 append の hunk 衝突と [#165](../dev/03-retrospectives.md) 行ズレの回避（P0 #707 と同じ手法）で、本節は**その stub 区画を置換したもの**＝見出し `:1024` 以前の行は動いていない。

正本 = [追補 ⑨ §4 hand-off ②](04-perception-sidewalk-and-signals.md:991)（safety-state が `TerrainCoverage.quality` を `SourceObservation` へ写す）+ 型は [追補 ④](04-perception-sidewalk-and-signals.md:390)（凍結契約 `warehouse_interfaces.perception`）+ 判定側は `warehouse_safety/sensor_health.py`（#680・L1・未配線）。**レイヤ注記**（[.claude/rules/layer-annotation.md](../../.claude/rules/layer-annotation.md)）: 本節のアダプタは **L1 安全**（`warehouse_safety`・`sensor_health.py` と同じ箱）・**純ロジック・actuation なし**。producer 側（04 の `terrain_node`）は自律走行（安全層外）＝[:189](04-perception-sidewalk-and-signals.md:189)。**本 PR は rclpy 配線をしない**——誰が購読するか（Guardian 拡張か新 node か）は [`OQ-OD95`](09-external-review-v3-response.md:255) が未裁定で、§4 はその裁定資料である。

### 1. 裁定 `OQ-OD4Y-a` — **どちらへも寄せない**（両側据え置き・差はアダプタ 1 か所で吸収）

**裁定（2026-09-18）**: 契約側 `ObservationQuality.frame_digest` / `TerrainCoverage.source_stamp_s` と X2 側 `SourceObservation.digest` / `.stamp_s` の **どちらも改名しない**。名前差は新設 `ws/src/warehouse_safety/warehouse_safety/terrain_health.py`（L1・純ロジック）が吸収する。

1. **改名は破壊的**。`warehouse_interfaces` は凍結契約で、既存 field の削除・改名・型変更は原則禁止＝additive-first（[parallel-workflow.md §7.2](../../.claude/rules/parallel-workflow.md:192)）。X2 側を寄せても同じで、`SourceObservation` は #680 で着地済み＝`sensor_health.py` の docstring・`warehouse_safety/CLAUDE.md`・148 unit がその綴りで書かれている。
2. **買えるものが無い**。[追補 ④ 1-2](04-perception-sidewalk-and-signals.md:411) は 2 型の**意味**を既に揃えてある（「翻訳なしで渡せる意味に揃える」）。残っているのは綴りの差だけで、それを消すために凍結契約と 2 パッケージを同時に動かすのは交換として成立しない。
3. **写す場所はどのみち 1 か所要る**。wire は `std_msgs/String` の JSON（[doc03:323](../architecture/03-software-architecture.md:323)）ゆえ、名前が一致していても「JSON → pydantic 検証 → dataclass」は必要で、その 1 か所に綴りの差も入る＝**名前差の解消は新しい仕事を作らない**。
4. 逆に、`.msg` 化（Phase 4・[doc16 §3](../architecture/16-repository-and-conventions.md)）で改めて綴りを揃える機会はある。そのとき本節の写像表がそのまま移行表になる。

→ [:486](04-perception-sidewalk-and-signals.md:486) に同一行で追記済（行数不変）。

### 2. 写像表（`TerrainCoverage` → `SourceObservation`）

| `TerrainCoverage`（04・凍結契約 v0.1） | `SourceObservation`（X2） | 出典 file:line |
|---|---|---|
| `.source_stamp_s` | `stamp_s`（**元計測時刻**。鮮度には使わない） | [:424](04-perception-sidewalk-and-signals.md:424) / [09 規則 (4)](09-external-review-v3-response.md:67) |
| `.quality.valid_fraction` | `valid_fraction`（同名・同範囲 `[0,1]`） | [:415](04-perception-sidewalk-and-signals.md:415) |
| `.quality.frame_digest` | `digest`（凍結フレーム指紋。`None` は**明示の無効化**） | [:416](04-perception-sidewalk-and-signals.md:416) |
| （04 は持たない） | `received_monotonic_s` ＝ **呼び出し側の単調時計をそのまま通す** | [:411](04-perception-sidewalk-and-signals.md:411) / [09:67](09-external-review-v3-response.md:67) 規則 (4) |

**写さない field と理由**（発明を避けるための明示）:

| 写さない field | 理由 |
|---|---|
| `state`（`FLOOR_CONFIRMED` / `DROP_DETECTED` / `UNKNOWN`） | coverage は**品質の証人**であって停止判断ではない。判定点は X2 の 1 か所（[:203](04-perception-sidewalk-and-signals.md:203) / [:222](04-perception-sidewalk-and-signals.md:222)）で、`SourceObservation` に「床が落ちた」を意味する field は無い。`DROP_DETECTED` は**健康なセンサが世界について報告している**状態であり、これを health に畳むと正常なセンサが故障に見え（誤停止）、逆に沈黙が見えなくなる |
| `quality.ground_*` 5 field | X2 側の閾値化は [`OQ-OD4Z-d3`](04-perception-sidewalk-and-signals.md:899) で**別レーン**。今写せば閾値を発明することになる |
| `quality.device_frame_seq` / `quality.processing_latency_s` | X2 の入力は 4 field ちょうどで、対応物が無い。[追補 ③ #5](04-perception-sidewalk-and-signals.md:383) の「第 2 の材料」としての使い道は `OQ-OD4Y-m*` 未決 |
| `confirmed_distance_m` / `nearest_drop_distance_m` / `step_height_m` / `slope` / `roughness_m` / `estimate_error_m` / `reference` | 幾何であって観測品質ではない。消費者は 06 / 05（[:214](04-perception-sidewalk-and-signals.md:214) / [:215](04-perception-sidewalk-and-signals.md:215)） |

**source 名は持たない**: coverage が `SensorHealthMonitor` のどの source を代表するかは docs が沈黙する（[09:72](09-external-review-v3-response.md:72) は scan / cliff_scan / GNSS / depth を挙げるが coverage はそのどれでもない）。よって module は source 名の定数を**持たず**、呼び出し側（配線レーン）が `monitor.observe(<name>, obs)` で与える。推奨は §5 `OQ-OD4Y-m1`。

### 3. fail 方向

| 場面 | アダプタの挙動 | X2 の verdict | 向き |
|---|---|---|---|
| 正常 payload | 4 field を 1:1 で写す | しきい値次第（`OK` など） | — |
| 壊れた JSON / 空文字 / 必須欠落 / 未知 `TerrainState` / `NaN`・`Infinity` トークン / `valid_fraction` 範囲外 / `reference` 空白 | `parse_terrain_coverage` → `None`。観測は `stamp_s = NaN` / `valid_fraction = NaN` / `digest = None`（**raise しない・log しない**＝純関数） | **`INVALID`** | **fail-closed** |
| payload が `str` / `bytes` / `bytearray` ですらない（`None`・数値・list） | 同上（`ValidationError` は `ValueError` 派生・`TypeError` も捕捉） | **`INVALID`** | **fail-closed** |
| `frame_digest` 省略 / `null` | `digest = None`＝**この message では凍結検出を切る明示の選択**（[:416](04-perception-sidewalk-and-signals.md:416)） | 凍結判定を行わない（`_advance_run` は run を**進めも消しもしない**） | 現状維持（fail-open を作らない） |
| 凍結ストリームに壊れた frame が 1 本混ざる | `digest = None` ゆえ**既存の凍結証拠を消さない** | `FROZEN` のまま | **fail-closed**（合成 digest を返すと run が再開し `INVALID` へ格下げ＝診断が壊れる） |
| `received_monotonic_s` が負 / `now` より未来 / 非数 | **そのまま通す**（アダプタは時計を読まない・補正しない） | `STALE` | **fail-closed**（補正すると時計取違えを検出する規則そのものを潰す） |
| `received_monotonic_s` が `bool` | `SourceObservation` 構築時に `ValueError`（call-site の marshalling バグ） | — | 構築時に落とす（`evaluate` には持ち込まない） |
| `state = DROP_DETECTED` / `UNKNOWN` かつ品質良好 | `state` は写さない | `OK`（＝センサは健康） | 設計どおり（§2 の表） |
| payload が JSON として妥当だが **UTF-8 として不正**（例 `"reference": "a\xff"`） | `parse_terrain_coverage` → `None`（契約が decode 段で拒否） | **`INVALID`** | **fail-closed**（`errors="ignore"` 等で decode すると `"a\xff"` が `"a"` に**黙って修復**され、誰にも読めない message が `OK` になる。R-26 で pin 済＝`invalid-utf8-bytes`） |
| `quality.valid_fraction` が `true` / `false` / `"0.5"` / `"1"` / `1`（**pydantic lax 強制変換**） | `1.0` / `0.0` / `0.5` / `1.0` / `1.0` として**そのまま写す** | しきい値次第（`OK` にもなる）＝**`INVALID` ではない** | 契約が受理した以上アダプタは**判定しない**（判定点は X2 の 1 か所）。`"NaN"` / `"1.5"` は変換後に有限性・範囲の validator が落とす＝`ValidationError`。`bool` が数値 field に化ける問題はハブ全体の `strict` 方針の話で **[`OQ-OD4Z-d7`](04-perception-sidewalk-and-signals.md:903) が未決**（同 OQ の `ground_from_prior` と同型・向きが逆）。**注意**: `source_stamp_s: true` も `1.0` になるため、X2 側 `SourceObservation` の `bool` 拒否は**この経路を見られない**（拒否は call-site の marshalling バグ用で、wire 上の `true` は契約通過時点で `float` になっている）。[D] pydantic 2.13.4 で実測・R-26 で pin 済 |

**鮮度の二重性に注意**: X2 が判定する staleness は**受信側の単調時計**で、`stamp_s` では判定しない（[09:67](09-external-review-v3-response.md:67) 規則 (4)）。したがって [追補 ④ §2 1.](04-perception-sidewalk-and-signals.md:477) が消費側に課す `age = now − source_stamp_s; 0 ≤ age < max_age`（＝**producer が古い計測を新しく見せていないか**）は、本アダプタでは**果たされない**。両者は別の検査（前者＝生存性、後者＝入力妥当性）で、後者の置き場は未決＝§5 `OQ-OD4Y-m2`。

### 4. `OQ-OD95` の裁定資料 — Guardian 拡張（A）か新 node（B）か（**本 PR は実装しない**）

**先例**: `/{bot}/scan` の**途絶**停止は #679 で **Emergency Guardian の新理由 `scan_stale`** に着地した（[doc12 末尾追補 (3)](../architecture/12-infrastructure-common.md:698)）。同追補は **scan の「内容」異常（全 NaN・凍結・有効観測率）は対象外＝Mode Outdoor X2 に残す**と明記し、「本裁定は `OQ-OD95` に対して共通構成側の**先例**を与えるが、X2 の決定は mode-outdoor 側」と断っている（[doc12:709](../architecture/12-infrastructure-common.md:709) ③）。つまり A は「前例に倣う」、B は「前例と分岐する」選択である。

| 観点 | **A: Emergency Guardian 拡張** | **B: 新 node `sensor_health_node`** |
|---|---|---|
| 何が単純になるか | 停止の実行機構（prio100 ゼロ `Twist` の level 再アサート・Nav2 goal cancel・edge-trigger event・`/{bot}/stop_state` feed → L0' 上乗せ）が**既に R-26 で固定済み**。追加は「購読 1 本＋純ブロック 1 個」で、`scan_stale` と同型（[doc12:700](../architecture/12-infrastructure-common.md:700) ①）。時計が 1 本で済む（pose / odom / scan / coverage が同じ `time.monotonic()`＝同 ②）。層の整合＝CM の**入力の生存**を同じ L1 で見る（同 ③④） | **関心の分離**。Guardian は幾何・電池の 50 ms 反射に留まり、source ごとの状態（`(digest, run)`・`health_epoch`）を持つ**別種の判定**を独立させられる。Guardian を再起動しても `health_epoch` が巻き戻らない。[09:67](09-external-review-v3-response.md:67) 規則 (1) が要求する「監視系の生存確認」は、**別プロセスなら観測可能な事実**になる（同居だと「Guardian が生きている＝health も生きている」と暗黙に仮定することになる） |
| 何が二重化するか | 1 プロセス内に**鮮度の語彙が 2 つ**並ぶ: `guard_logic`（閉区間 + `age_is_unknown`・[CLAUDE.md 追記②](../../ws/src/warehouse_safety/CLAUDE.md:115)）と `sensor_health`（`ABSENT/STALE/FROZEN/INVALID`）。どちらも正しいが規則が違うため、**どちらで判定したかを読者が追えなくなる**危険。統一するなら追加の reconcile 作業 | **停止経路が二重化しうる**。新 node に停止権限を与えれば第 2 の stop producer になり、[doc12:701](../architecture/12-infrastructure-common.md:701) が `/operator/stop_request` 互換 producer を**不採用**にしたのと同じ理由（単一 latch・人の意思表示専用）に抵触する。与えなければ health を topic で出すことになり、**その topic 自身の鮮度**を誰かが判定する必要がある（監視者を監視する再帰） |
| fail-active 窓（[doc12:709](../architecture/12-infrastructure-common.md:709) ①）への影響 | **縮まない**。Guardian 単独死の窓は `stop_overlay_enabled: true` の L0' 上乗せ（無信号＝失権）が担う。A では Guardian の死が反射と health 判定を**同時に**奪う（単一障害点）が、それを塞ぐのは permit 側の生存確認であって配置ではない | **縮まない**が失敗の形が変わる。health node の死は反射を奪わない（良い）一方、health が沈黙する＝[09:67](09-external-review-v3-response.md:67) 規則 (1) により消費側が「health 不在＝not healthy」を**自分で実装しテストする**必要が増える（A では L0' の無信号＝失権と同じ形に畳める） |
| 必要な追加契約 | `OQ-OD89`（`health_epoch` の wire）・`OQ-OD91`（モード別必須 source 集合）・`OQ-OD97`（しきい値）。**新 topic は不要**にできる（Guardian が既に出す `/{bot}/stop_state` の additive 拡張に相乗り可能） | 上記 3 つに加えて **node 名・launch エントリ・新 topic + QoS・その topic の鮮度規則**。`OQ-OD89` が「別 topic」に決まるなら、この追加は B では必然、A でも必要になる |
| 実装コスト（本アダプタ着地後） | 購読 1・`SensorHealthMonitor` 1 インスタンス・`evaluate` への additive ブロック 1 | 新 package/node 骨格・終了 3 規則・launch・config・topic 契約・shutdown lifecycle ラチェットへの登録 |

**推奨: A（Emergency Guardian 拡張）**。理由は「同じ fail-open クラス・同じ layer・同じ箱で、repo が既に一度この判断を下しており（[doc12:700](../architecture/12-infrastructure-common.md:700) ①〜④）、その理由がそのまま転用できる」こと。加えて A は**停止権限を 1 か所に保つ**——[doc12:701](../architecture/12-infrastructure-common.md:701) が明示的に不採用にした「機械 producer をもう 1 本立てる」形を避けられる——し、`OQ-OD89` が未裁定のうちに新 topic を切らずに済む。A が抱える費用（1 プロセス内に鮮度の語彙が 2 つ）は**名指しして片付ける作業**であって、プロセスを割る理由ではない。

**この推奨が反転する条件（隠さない）**: `OQ-OD89` が「`health_epoch` は独自 topic で運び、停止ではなく **09 の期限付き許可の失効**として効かせる」に決まる場合、health は stop の producer ではなく permit の入力になる。そのとき B の「独立した監視系の生存を観測可能にする」（[09:67](09-external-review-v3-response.md:67) 規則 (1)）が主論点になり、B が優位になりうる。**したがって `OQ-OD95` は `OQ-OD89` より後に裁定するか、両者を同時に裁定するのが筋**であり、本節は A を推すが、`OQ-OD89` の裁定を待たずに node を書き始めることは推奨しない。

### 5. OPEN QUESTIONS（本追補で**発明せずに残した**もの・接頭辞は [`OQ-OD4Z-d6`](04-perception-sidewalk-and-signals.md:902) の採番衝突を避けて `-m1`〜`-m4`）

- `OQ-OD4Y-m1` **coverage はどの source を代表するか**。`SensorHealthMonitor` は source 名で必須集合を作るが、[09:72](09-external-review-v3-response.md:72) の列挙（scan / cliff_scan / GNSS / depth）に coverage は無い。**推奨（採否は `OQ-OD95` / `OQ-OD91` と一緒に）= `cliff_scan` の品質証人として同じ source 名に入れる**: ① `cliff_scan`（`sensor_msgs/LaserScan`）は `valid_fraction` も指紋も運べず、そのままでは意味のある `SourceObservation` を作れない（[:173](04-perception-sidewalk-and-signals.md:173)）。② 2 本は**同じ depth frame から対で 1 回**出る（[doc03:322](../architecture/03-software-architecture.md:322) / [:323](../architecture/03-software-architecture.md:323)）ので、coverage の到着は cliff_scan の生存の証拠でもある。対立案 = coverage を独立 source として立て、`cliff_scan` は Guardian の `scan_stale` 同型（到着のみ）で見る。**module は名前を持たない**ので、どちらに決まってもアダプタは不変。
- `OQ-OD4Y-m2` **`age = now − source_stamp_s` の置き場**。[追補 ④ §2 1.](04-perception-sidewalk-and-signals.md:477) は消費側に `0 ≤ age < max_age` を課すが、X2 の staleness は**受信側の単調時計**で測る（[09:67](09-external-review-v3-response.md:67) 規則 (4)）ため、本アダプタはこれを**行わない**（§3 末尾）。この検査は (a) 配線 node が別途行う、(b) `SourceObservation` に additive で持ち込む（契約変更）、(c) producer 側の `OQ-OD4Y-i3`（負の遅延）と同じ裁定に含める、のどれか。`max_age` は「その消費者の時間予算」由来ゆえ [`OQ-OD97`](09-external-review-v3-response.md:257) と同じ実測待ち。
- `OQ-OD4Y-m3` **`state` を恒久的に health から外してよいか**。本追補は「`DROP_DETECTED` は健康なセンサの報告」として `state` を写さないと決めたが、`UNKNOWN` が**継続的に**出る（視野外・遮蔽・鮮度切れ・変換不成立＝[:174](04-perception-sidewalk-and-signals.md:174)）状態を「健康」と言い続けてよいかは別問題。`valid_fraction` がその役を担う想定だが、`UNKNOWN` を出しつつ `valid_fraction` が高い frame が有りうるかは実装（`terrain_core`）と実測で確かめる。走行可否としては 05（観測範囲外へ進ませない＝[:215](04-perception-sidewalk-and-signals.md:215)）が既に持っている論点で、**health へ二重に持ち込まない**のが現時点の判断。
- `OQ-OD4Y-m4` **拒否された payload の可観測性**。アダプタは純関数ゆえ log を出さない（[:482](04-perception-sidewalk-and-signals.md:482) の「safety loop の外で捕捉」は満たすが、捕捉した事実を**どこにも記録しない**）。producer が壊れた JSON を出し続けても、下流には `INVALID` verdict としてしか現れず「なぜ INVALID か」が分からない。記録は X1 の担当（[:222](04-perception-sidewalk-and-signals.md:222)）だが、配線 node が parse 失敗を数える／`/emergency/event` の `detail` に載せる／何もしない、のどれを採るかは `OQ-OD95` の配線裁定と同時に決める。

### 6. 実装・テスト（本 PR）

- `ws/src/warehouse_safety/warehouse_safety/terrain_health.py`（新規・**L1**・`rclpy` / `numpy` 非 import・時計を読まない・**しきい値と source 名を持たない**）。公開 IF は 2 つだけ:
  - `parse_terrain_coverage(payload) -> TerrainCoverage | None` — `TerrainCoverage.model_validate_json` で検証し、契約が拒否したものは `None`（raise しない・log しない）。
  - `coverage_observation(payload, received_monotonic_s) -> SourceObservation` — §2 の写像。parse 失敗時は `stamp_s = NaN` / `valid_fraction = NaN` / `digest = None`（§3）。`received_monotonic_s` は素通し。
- 既存 module は**1 文字も変えていない**（`sensor_health.py` / `guard_logic.py` / `emergency_guardian.py` / `stop_distance.py`）。`warehouse_safety/package.xml` は `warehouse_interfaces` の `exec_depend` を**既に持っている**ため変更不要。topic・ROS parameter・launch・`setup.py` エントリの追加は**ゼロ**。
- R-26: `tests/unit/test_terrain_health.py`（**54 unit**・`@pytest.mark.safety` + `unit`）。独立オラクル = **手書き JSON リテラル**（`model_dump_json()` で作らない）と docs の規則。`stamp_s` / `received_monotonic_s` の literal を ~7766 s 離して取り違えが必ず見えるようにし、`state` 違いの 2 payload が同一観測になること・`ground_*` 5 field が観測を動かさないことを等値で pin。UTF-8 不正 bytes（§3）と pydantic lax 強制変換（§3）も pin。構造 pin = import 集合（時計・ROS を持たない）とモジュール定数（`__all__` のみ＝source 名・しきい値を発明していない）。
- **mutation 10 体すべて KILLED**（実ファイル差替・sha256 で復元検証）: `digest` に `state` を入れる／parse 失敗で raise／失敗時 `valid_fraction = 0.0`（NaN でなく）／`stamp_s` に `received_monotonic_s` を入れる／`bytes` を decode しない／**`bytes` を `errors="ignore"` で decode してから検証する**（#722 stage-2 レビュー指摘で追加。それまで 45 unit を素通りしていた＝§3 の UTF-8 行）／失敗時に合成 `digest`（`""`）を返す／`ground_inlier_fraction` を `valid_fraction` の代用にする／受信時刻を `abs()` で補正する／失敗時 `stamp_s` に受信時刻を入れる。
- produce / consume は [`ws/src/warehouse_safety/CLAUDE.md`](../../ws/src/warehouse_safety/CLAUDE.md) 末尾（本 PR で append）。

<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->

## 【2026-09-18 追補 ⑫】bringup consumer: launch `terrain_publisher` ＋ config `perception.terrain.*` 注入（実装記録・レーン C が記入）

> **（記入済 = PR #723・2026-09-18）** レーン C（`feat/bringup-terrain-publisher`）が記入した追補 ⑨ §4 **hand-off ③** の実装記録。

正本 = [追補 ⑨ §4 hand-off ③ :992](04-perception-sidewalk-and-signals.md:992)。本節は **hand-off ③ の実装記録**である。追補 ⑨ が「config キー `perception.terrain.*`（`speed_bands.*` と同型）」を**案**として置いたのを bringup 側で**実体**にした（launch 群 ＋ base config ブロック）。実装 = [`ws/src/warehouse_bringup/launch/nav2_bringup.launch.py`](../../ws/src/warehouse_bringup/launch/nav2_bringup.launch.py)（`_terrain_config` / `_terrain_group` ＋ 型表 3 本）と [`config/warehouse.base.yaml`](../../config/warehouse.base.yaml) の `perception.terrain` ブロック。**値は 1 つも入れていない**（[裁定 8 :943](04-perception-sidewalk-and-signals.md:943) / [:992](04-perception-sidewalk-and-signals.md:992) の実測ゲート）。`warehouse_perception` / `warehouse_interfaces` は**不変更**。

> **レイヤ注記**（[:7](04-perception-sidewalk-and-signals.md:7) と同軸・[追補 ⑨ レイヤ注記 :930](04-perception-sidewalk-and-signals.md:930) と同じ扱い）: launch / config = **配線**であり [productization/01 の正準対応表](../productization/01-commercial-box-map.md:182) に行を持たない（帰属未定・actuation なし。記録付き build tooling と同じ扱い = [build-deploy-run.md](../../.claude/rules/build-deploy-run.md)）。起動する node は**自律走行（安全層外）の producer**（[:189](04-perception-sidewalk-and-signals.md:189)）。本スライスは `cmd_vel` / `stop_request` / `stop_state` / `speed_limit` に**一切触れない**（配線側も AST で pin = §5）。**L2 / L1 / L0' は不変更**、`twist_mux` の入力は 2 本のまま。

### 1. 配線（config キー = node param 名と 1:1・全 23 本）

`config/warehouse.base.yaml` の `perception.terrain.*` を `nav2_bringup.launch.py` の `_terrain_config()` が読み、`_terrain_group(robot, use_sim_time)` が per-bot の `GroupAction`（`PushRosNamespace(robot)` ＋ `SetParameter("use_sim_time", …)` ＋ `Node(package="warehouse_perception", executable="terrain_publisher")`）を組む。呼び出しは `generate_launch_description` の per-robot ループ、`_speed_band_group` の直後。

**型は node 側 1 か所が正本**: rclpy は宣言値から param の型を固定するので、YAML の `ray_count: 360.0`（double）も `reference_offset_m: 0`（int）も declare 時に落ちる。launch は各値を[`terrain_node.py` の `_SENTINELS`](../../ws/src/warehouse_perception/warehouse_perception/terrain_node.py) が宣言した**その型**へ変換する（`_TERRAIN_INT_KEYS` / `_TERRAIN_STR_KEYS` / 残りは double）。この 2 タプルは**型の表であって既定値ではない**——数値は 1 つも書かれていない——し、[追補 ⑨ §2 の表](04-perception-sidewalk-and-signals.md:946)からドリフトしないよう R-26 unit が `_SENTINELS` と機械照合する。

| # | config キー（`perception.terrain.*`） | ROS param 型 | 宣言 sentinel | 出典（正本） |
|---|---|---|---|---|
| 1 | `depth_topic` | str | `""` | [裁定 6 :941](04-perception-sidewalk-and-signals.md:941)（`sensor_msgs/Image`・空 = 購読しない） |
| 2 | `camera_info_topic` | str | `""` | [裁定 6 :941](04-perception-sidewalk-and-signals.md:941)（`sensor_msgs/CameraInfo`） |
| 3 | `camera_height_m` | double | `0.0` | [§2 :950](04-perception-sidewalk-and-signals.md:950) / [:181](04-perception-sidewalk-and-signals.md:181)（`OQ-OD45` [:143](04-perception-sidewalk-and-signals.md:143)） |
| 4 | `pitch_down_rad` | double | `0.0` | [§2 :951](04-perception-sidewalk-and-signals.md:951) / [:181](04-perception-sidewalk-and-signals.md:181) |
| 5 | `reference_offset_m` | double | `0.0` | [§2 :952](04-perception-sidewalk-and-signals.md:952) / [:176](04-perception-sidewalk-and-signals.md:176)（符号自由） |
| 6 | `cell_size_m` | double | `0.0` | [§2 :953](04-perception-sidewalk-and-signals.md:953) / [追補 ⑤ §2 :537](04-perception-sidewalk-and-signals.md:537) |
| 7 | `corridor_half_width_m` | double | `0.0` | [§2 :954](04-perception-sidewalk-and-signals.md:954) / [:175](04-perception-sidewalk-and-signals.md:175) |
| 8 | `forward_range_m` | double | `0.0` | [§2 :955](04-perception-sidewalk-and-signals.md:955) / [追補 ⑤ §2 :539](04-perception-sidewalk-and-signals.md:539) |
| 9 | `min_points_per_cell` | **int** | `0` | [§2 :956](04-perception-sidewalk-and-signals.md:956) / [:56](04-perception-sidewalk-and-signals.md:56) |
| 10 | `drop_threshold_m` | double | `0.0` | [§2 :957](04-perception-sidewalk-and-signals.md:957) / [:196](04-perception-sidewalk-and-signals.md:196)（`OQ-OD45`） |
| 11 | `min_valid_fraction` | double | `0.0` | [§2 :958](04-perception-sidewalk-and-signals.md:958) / [:174](04-perception-sidewalk-and-signals.md:174) |
| 12 | `plane_tolerance_m` | double | `0.0` | [§2 :959](04-perception-sidewalk-and-signals.md:959) / [追補 ⑤ §2 :543](04-perception-sidewalk-and-signals.md:543) |
| 13 | `ransac_iterations` | **int** | `0` | [§2 :960](04-perception-sidewalk-and-signals.md:960) / [追補 ⑤ §2 :544](04-perception-sidewalk-and-signals.md:544) |
| 14 | `ransac_seed` | **int** | `0` | [§2 :961](04-perception-sidewalk-and-signals.md:961)（どの値も合法 = `OQ-OD4Y-i2`） |
| 15 | `max_plane_tilt_rad` | double | `0.0` | [§2 :962](04-perception-sidewalk-and-signals.md:962) / [追補 ⑧ §2 :865](04-perception-sidewalk-and-signals.md:865)（`OQ-OD4Z-d1` [:897](04-perception-sidewalk-and-signals.md:897)） |
| 16 | `max_plane_offset_m` | double | `0.0` | [§2 :963](04-perception-sidewalk-and-signals.md:963) / [追補 ⑧ §2 :866](04-perception-sidewalk-and-signals.md:866)（同上） |
| 17 | `angle_min_rad` | double | `0.0` | [§2 :964](04-perception-sidewalk-and-signals.md:964) / [追補 ⑤ §2 :546](04-perception-sidewalk-and-signals.md:546) |
| 18 | `angle_max_rad` | double | `0.0` | [§2 :965](04-perception-sidewalk-and-signals.md:965) / 同 :546（`> angle_min_rad`） |
| 19 | `ray_count` | **int** | `0` | [§2 :966](04-perception-sidewalk-and-signals.md:966) / [追補 ⑤ §2 :547](04-perception-sidewalk-and-signals.md:547) |
| 20 | `range_min_m` | double | `0.0` | [§2 :967](04-perception-sidewalk-and-signals.md:967) / [追補 ⑤ §2 :548](04-perception-sidewalk-and-signals.md:548) |
| 21 | `range_max_m` | double | `0.0` | [§2 :968](04-perception-sidewalk-and-signals.md:968) / 同 :548（MinZ = `OQ-OD4Q` [:354](04-perception-sidewalk-and-signals.md:354)） |
| 22 | `reference` | str | `""` | [§2 :969](04-perception-sidewalk-and-signals.md:969) / 語彙は `OQ-OD4Y-h` [:493](04-perception-sidewalk-and-signals.md:493) が未決 |
| 23 | `pixel_stride` | **int** | `0` | [§2 :970](04-perception-sidewalk-and-signals.md:970) / [裁定 7 :942](04-perception-sidewalk-and-signals.md:942) |

> 内訳 = **int 5・double 15・str 3**（`enabled` は gate ゆえ本表の外）。**型変換を掛けるのは`PARAM_KEYS` の 21 本だけ**で、入力 topic 2 本は**生のまま**転送する（§2 の null 行）。base config には 23 本すべてが**コメントアウトの placeholder** として並び、各行に単位・検証条件・出典が付く。`enabled: false` だけが実キーである。

### 2. gate と fail 方向（何がどちらへ倒れるか）

| 場面 | 挙動 | 向き |
|---|---|---|
| `perception.terrain.enabled` 未設定 / false（**base の既定**） | `_terrain_group` が `[]` を返す＝`GroupAction` も `Node` も作らない。node プロセスすら起動しない | **safe-OFF** |
| `enabled: true` ＋ 23 本のいずれかが overlay に無い | launch は**そのキーを転送しない**（既定を与えない）→ node が宣言 sentinel のまま起動時 `ValueError` → abort（[§3 :979](04-perception-sidewalk-and-signals.md:979)） | **fail-closed** |
| `enabled: true` ＋ 入力 topic が空 | node が起動時 abort（[§3 :980](04-perception-sidewalk-and-signals.md:980)） | **fail-closed** |
| config に node が知らないキー（typo・将来キー） | launch が**転送しない**（`PARAM_KEYS` ＋ 入力 topic 2 本 ＋ `enabled` 以外は無視）。rclpy は未宣言 param を拒否するため、素通しさせると起動失敗になる | **黙って無視**（起動は守る） |
| `perception` / `perception.terrain` が dict でない | 空 dict に倒す＝OFF（起動失敗の責任は node 側 fail-closed 検証に集約する） | **safe-OFF** |
| `enabled: true` ＋ overlay の値が null / 非数値（`cell_size_m:` / `cell_size_m: auto`） | launch の `float()` / `int()` が **launch description を組んでいる最中**に上げる → `ros2 launch` 全体が起動しない（node だけでなく **両 bot の Nav2 も道連れ**）。`_speed_band_group` の `float(speed_bands[key])`（`nav2_bringup.launch.py` `:441-442`）と**同じ露出**であり、本節で明示しておく | **fail-closed**（ただし巻き添えは大きい） |
| `enabled: true` ＋ 入力 topic が null（`depth_topic:` = YAML null） | 入力 topic は**変換せず生のまま**転送するので、宣言型 STRING が非 str を拒み起動が止まる。`str()` で包むと `str(None) == "None"` が**非空**になり node の空文字ガード（[:980](04-perception-sidewalk-and-signals.md:980) が守る条件）を**すり抜けて** `/bot{n}/None` を購読し「健康に見えたまま無言」になる | **fail-closed**（生転送がその条件） |
| `warehouse_perception` が未ビルド ＋ terrain OFF | `PARAM_KEYS` の import は**有効経路の中だけ**なので Nav2 は従来どおり起動する | **safe-OFF** |

**ON/OFF の真実は 1 つ**: 同じ `perception.terrain.enabled` を、レーン A の consumer 側 gate（`collision_monitor` の `cliff_scan` source）も読む。producer を止めて source だけ残す／その逆、という半端な構成を config 上作れないようにするため（[ADR-0012 決定 3](../adr/0012-speed-band-no-l2-best-effort.md:20) の「真実の源を 2 つにしない」を配線へ適用）。

### 3. dev / stg / prod の扱い

[environments.md](../../.claude/rules/environments.md) の base + overlay に従う。**base（`config/warehouse.base.yaml`）は `enabled: false` のみ**——共通値だけを置き、環境差分は `config/<env>/warehouse.yaml` に書く、という規律そのもの。

- **dev（Mac Docker / Gazebo）**: depth camera が**存在しない**（sim モデルに深度センサが無い）。OFF のまま。
- **stg / prod**: カメラ取付と実測が済んでから overlay に 23 本 ＋ `enabled: true` を書く。**本スライスでは overlay を一切書いていない**（書ける値が無い）。
- 実値の供給は overlay か `WAREHOUSE__*` 環境変数（後勝ち）。`load_config` は `perception` ブロックを**検証しない**（`_validate_safety` は `safety.*` のみ）＝検証は node 側 sentinel に一本化する。`warehouse_interfaces` に perception 用の検証を足していない（契約 hub 不変更）。

### 4. OPEN QUESTIONS（本節で**発明せずに残した**もの・接頭辞は `OQ-OD4Y-i*` との衝突を避けて `-n1`〜`-n5`）

- `OQ-OD4Y-n1` **`use_sim_time` と depth stamp の関係**。`_terrain_group` は `speed_band` 群と同じく `SetParameter("use_sim_time", …)` を push するが、本 node は timer を持たず `get_clock()` を読むのは遅延自己申告（`processing_latency_s`）だけである。sim で depth frame の stamp が `/clock` 系、受信側が system clock、という取り違えが起きたとき「負の遅延」として現れる（[`OQ-OD4Y-i3` :998](04-perception-sidewalk-and-signals.md:998)）。sim に depth camera が入るまで実地で確認できない。
- `OQ-OD4Y-n2` **overlay 値の検証を node 以外にも置くか**。現状「起動して落ちる」まで誤りが分からない（`corridor_half_width_m < cell_size_m / 2` のような**組み合わせ**の誤りは特に）。`speed_bands` 側は base config の値を `tests/unit/test_speed_band_bringup_wiring.py` が「値が入った瞬間に効く」形で検査しているが、terrain は値が **env overlay** に入るため同じ手は届かない（overlay は環境ごとに異なりリポジトリに無い環境もある）。起動前 lint（`check-hermes-live.sh` 相当の診断）に寄せるかは未決。
- `OQ-OD4Y-n3` **int キーの丸め**。launch は `int(value)` で変換するので、overlay に `ray_count: 359.6` と書かれると**黙って 359** になる（`float("abc")` のような型違いは例外になるが、非整数の数値は通る）。「非整数なら起動を止める」ほうが本スライスの fail-closed 方針と揃うが、その検証規則は正本に無いので発明していない。**同じ穴が `reference` にもう 1 つ残る**: 入力 topic を生転送にした後、`str()` を通るのは `reference` だけで、`reference: 3` は**黙って `"3"`** になる（非空なので node の検証も通る）。語彙が [`OQ-OD4Y-h` :493](04-perception-sidewalk-and-signals.md:493) で未決である以上、「どんな文字列なら正しいか」を本レーンでは書けない。
- `OQ-OD4Y-n4` **2 台構成での 1 カメラ**。`_terrain_group` は `ROBOTS` の各要素に同じ config を適用するので、`depth_topic` を絶対名で書くと 2 台が同じカメラを見て `/bot1/cliff_scan` と `/bot2/cliff_scan` に同じ崖が出る。`speed_bands` の `source_topic` が抱えるのと同じ未決（[`TODO(gesture_detector)`](../../ws/src/warehouse_bringup/launch/nav2_bringup.launch.py)）で、単騎運用（[ADR-0006](../adr/0006-single-bot-first.md)）の間は顕在化しない。
- `OQ-OD4Y-n5` **正準対応表の記述の鮮度**。[productization/01 L1 行 :185](../productization/01-commercial-box-map.md:185) は `terrain_node.py` を「launch / config / consumer 側は**未配線**」と書いており、本節（bringup）とレーン A / B（consumer）の land でその一文が古くなる。`docs/productization/**` は本レーンの編集境界の外ゆえ触っていない＝**ラウンド統合側の残件**（同一 PR での対応表追記は「新規 component を実装したら」が条件で、配線は対応表に行を持たない＝[layer-annotation.md](../../.claude/rules/layer-annotation.md)）。

### 5. 実装・テスト（本 PR）

- `ws/src/warehouse_bringup/launch/nav2_bringup.launch.py` — `_terrain_config()`（`load_config()["perception"]["terrain"]`・dict でなければ空）／`_terrain_group(robot, use_sim_time)`（OFF なら `[]`・ON なら per-bot `GroupAction`）／`_TERRAIN_INT_KEYS`・`_TERRAIN_STR_KEYS`・`_TERRAIN_TOPIC_KEYS`。**`_speed_band_group` の後ろに append**し、呼び出し 2 行だけを既存ループに足した（`collision_monitor` 節は不変更＝レーン A の区画）。
- `config/warehouse.base.yaml` — `speed_bands` の**後ろ**に `perception: / terrain:` を append（既存行を動かさない）。`enabled: false` ＋ 23 本のコメントアウト placeholder。
- `tests/unit/test_terrain_bringup_wiring.py`（`unit` + `safety`・**AST と YAML のみ**＝`launch_ros` / `nav2_common` / rclpy 不要で pure CI でも走る）: 配線の存在（per-robot ループから 1 回）・namespace push と `use_sim_time`・OFF 時 `[]` が**生成より前**に返る・config キー経路・転送キー集合 == node の宣言面（`PARAM_KEYS` ＋ `_SENTINELS` と機械照合・**本数を再ハードコードしない**）・欠落キーを埋めない・**数値リテラル 0 個**・int / str 変換表 == `_SENTINELS` の型・`cmd_vel` / `stop_*` / `speed_limit` / `remappings` 不在・base が `enabled: false` 単独・placeholder が 23 本を網羅。
- mutation（実測・**9/9 KILLED**）: `enabled` gate 除去／`ray_count` を double 転送／launch に `pixel_stride: 1` の既定／`camera_info_topic` 不転送／欠落キーを既定で埋める／`_terrain_group` 呼び出し削除／config キー経路 typo／`cliff_scan` を `cmd_vel` へ remap／base の `enabled: true`。
- `ws/src/warehouse_bringup/package.xml` は**不変更**: `warehouse_perception` の `exec_depend` は速度帯スライスで既に宣言済み（同 `:27`）。
