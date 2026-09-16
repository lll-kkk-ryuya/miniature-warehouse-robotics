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
| `GREEN` | 直近 N フレーム（例 10 = 1 s）の**時間窓多数決が 8/10 以上** かつ ROI 整合 かつ 検出 stale < 0.5 s | 横断開始を許可（承認トークンと AND） |
| `GREEN_FLASHING` | 窓内で GREEN/OFF が交番（**明滅周期 0.5 s = 2 Hz**・警察庁「交通信号灯器仕様書」由来＝科警研 横関ら, 交通工学論文集 5(2) B_17-B_23, 2019 [D 2026-09-16]。**2026-09-16 訂正**: 旧「~1 Hz」は車両用閃光の値。判定は末尾追補 §5 の二段レート＝10 Hz 多数決だけに頼らない）、または明示クラス | **新規横断の開始は禁止**（横断中は継続＝[01 §6](01-legal-envelope-japan.md)） |
| `RED` | 多数決 RED | 禁止 |
| `UNKNOWN` | **上記いずれも成立しない全ての場合**（検出ゼロ・票割れ・stale・ROI 外・カメラ断・クラウド断） | **禁止（fail-closed）**。LLM / ER は `UNKNOWN` を `GREEN` に昇格できない |

「青点滅 → 青」「赤 → 青」の誤りをゼロにするため、**GREEN への遷移のみ閾値を非対称に厳しく**する（GREEN 判定は 8/10・GREEN 離脱は 1 フレームで即）[I]。

## 5. 歩行者（進路を譲る）

- 検出: 前方 OAK-D の RGB で人物検出（信号検出器と同じ TensorRT 経路・別クラス）。深度で距離。
- 挙動: 前方 X m 以内に歩行者 → `slow` 帯（best-effort）→ さらに近ければ **停止**（L1 collision_monitor の stop polygon が最終担保＝pose 非依存の床）。「進路を譲る」＝停止して待つ（法 14 条の 2）。退避は Phase 2。（⚠ 2026-09-16 書換候補 = 末尾追補 ② `OQ-OD4D`: 04 は位置・速度・不確かさ・最終観測時刻のみを出し、減速は帯セレクタの入力・MRM 段は 12・回避は 06・停止は 07 が決める）
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
- `OQ-OD44` cliff detector の `source_timeout` 契約: virtual_scan 型（沈黙可）か scan 型（途絶＝停止）か。
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
| 無効深度 | NaN / inf / 0 / 空点群 / 有効画素過少 / 凍結フレーム（新しい stamp でも同一画像）を検出し「品質不成立」にする |
| 走行許可 | 今から踏む領域 + 停止までに必要な領域（[05 追補 5](05-safety-envelope-and-intervention.md) の停止距離式）が**観測済み**であること |
| 距離 | cliff 端までの距離は車体中心でなく**車輪接地点・footprint** で評価 |
| 時刻 | 変換後も元の計測時刻を維持（古い depth に現在時刻を付け直さない） |
| costmap | cliff 用 marking は分離し、別 scan の自由空間 raytracing で崖を消さない。時間経過・未観測で安全な床へ戻さず、**新しい床面観測でのみ clear** |
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
| 01_Geometry | depth_validity / ground_estimator / obstacle_geometry / terrain_features / terrain_coverage | 無効深度（追補）・RANSAC 床面（親 §2 案 B）・FLOOR / DROP / UNKNOWN（追補・09 §2-i）・`h / tan θ` | **`FLOOR_CONFIRMED` = 床を観測できた**に限定し、段差高・勾配・凹凸・推定誤差を別フィールド。`UNKNOWN` ≠ `DROP_DETECTED`（`OQ-OD4C`）。下向きカメラの **MinZ**（OAK 800P ≈ 70 cm）の実測が前提（`OQ-OD4Q`） |
| 02_Objects | detector / depth_association / tracker / uncertainty | 親 §5（前方 RGB 検出 + 深度で距離） | 検出器の既定を YOLOX-S から **RF-DETR-Nano** へ（追補 §3-1・`OQ-OD4N`）／推論の置き場 = Orin GPU か OAK on-device か（追補 §4・`OQ-OD4O`）／枠内深度の単純平均を避ける（OAK 既定 = MEDIAN・bbox 0.5 倍）／ByteTrack + odom で自車移動補償／「人は検出・距離 UNKNOWN」の出力（`OQ-OD4D`） |
| 03_Traffic_Signals | target_association / roi_refinement / lamp_classifier / temporal_state / observation_validity | 親 §4（登録 ROI・4 状態 fail-closed・時間窓多数決 8/10・非対称閾値・stale 0.5 s） | **青点滅は 0.5 s 周期 = 2 Hz**（親 §4 同一行訂正済・追補 §5）／候補 8 系統を同一 bag で比較（追補 §3-2）／ROI ずれの更新方式（`OQ-OD4F`）／出力に `crossing_id`・対応付けの確度・最終観測時刻 |
| 04_Surface_Semantics | sidewalk_segmentation / surface_attributes | 親 §2 案 A（Phase 2・検証器・単独権威にしない） | PIDNet-S（MIT）を Phase 2 主候補・YOLOE-26 は測定器（追補 §3-3）。**不一致の観測を出すのは 04、減速 / 停止は帯セレクタ・12**（レーン F 指摘 F3） |
| 05_Temporal_State | local_evidence_grid / ego_motion_compensation / expiry_and_reset | 追補「costmap」（新しい床面観測でのみ clear） | 局所観測履歴は costmap obstacle 層と二重化しない（`OQ-OD4E`）。物体履歴は 02・信号履歴は 03 が所有 |
| 06_Prediction | motion_baseline / learned_prediction | — | Phase 2（等速モデル + 広がる不確かさ → Trajectron++ は困る場面をログで確認してから） |
| 07_Output_Adapters | obstacle_scan / cliff_scan / terrain_grid / objects_signals | 親 §3 `cliff_scan`・親 §6 `pointcloud_to_laserscan`・coverage（2026-09-14 追補） | 追跡物体は depthai-ros の `vision_msgs/Detection3DArray`（spatial）を出発点に型を決める（追補 §4）／地形特徴グリッド・灯器観測の topic（型未凍結・contract PR）／consumer 側 costmap layer の所有（`OQ-OD4H`） |
| 08_Quality_Evidence | observation_metrics / processing_metrics | [09 §2-b](09-external-review-v3-response.md)（X2 sensor_health） | 04 は自己申告 producer・判定点は X2 の 1 か所（`OQ-OD4E`）。撮影 → 消費までの遅延・滞留を指標に（新規 compute 指標。既存 `OQ-OD87` は Guardian tick jitter であり別物） |
| 09_Runtime_and_Models | inference_backends / model_manifest / resource_budget | [02 §4](02-architecture-split-orin-pc-cloud.md)（屋外 S1 差分測定）・依存版の一覧は 00（[00 末尾追補](00-mission-and-scope.md)） | model_manifest（重み hash・前処理・ラベル・ONNX opset・TensorRT 版・GPU arch）= 04 が artifact として所有し、版一覧は 00 へ forward link（競合させない）。**engine はボード上で焼く**（`OQ-OD4U`）。配置案 OAK = 深度（+ 検出）／GPU = 検出・信号分類／CPU = 追跡・地形・点滅判定 |
| 10_Evaluation | bag_replay / scenario_sets / metrics / regression | 親 §7 P-1〜P-5・記録基盤 = run record（[jetson/03 §3](../jetson/03-build-deploy-run-and-run-records.md)・`mwr_run_record.py` が `ros2 bag record` を配線済。実走記録は未実施） | 差別化指標（走行距離当たりの誤停止・介入回数・危険検出時点の停止余裕・対象灯器の取り違え・認識結果の遅延）を additive。P-6（案）= 低視点 gap の実測（`OQ-OD4T`） |

### 2. 他箱との所有裁定（原則: 04 は観測 + 不確かさ + 品質の自己申告だけ。判定・履歴の正本・車体定数・記録は既存の箱に残し、同じ情報を 2 か所で判定しない）

列「重なる箱」は v2.1 の箱番号（00〜12・X1・X2）、列「正本」は doc 番号（`mode-outdoor/NN`）で区別する（レーン F 指摘 F13）。

| 04 の sub-dir | 重なる箱（v2.1） | 裁定案 | 正本（doc・file:line） |
|---|---|---|---|
| 00 | 00_Platform_Contract・01_Sensing・`warehouse_interfaces` | 04 = consume 宣言のみ。車体・校正・T_total・a_min = 00／時刻付与・OAK 内深度生成・`pointcloud_to_laserscan` の「変換」= 01 の責務定義と重なるため 07 の adapter は「変換の所有は 01・意味付け（cliff / coverage）は 04」と読む／型 = contract PR | [08 §2](08-architecture-v2-reference-alignment.md:39-40)・[09 §2-l](09-external-review-v3-response.md:186)・[09 §3](09-external-review-v3-response.md:198) |
| 01 terrain | 06 Navigation（costmap）・02 Map & Route（keepout）・10 Governance | **三分離**: ①床が見えた = 04 ／ ②この車体が通れる形状 = **06 が 00 の限界値と照合 or 02 keepout マスクに織り込む（09 §2-h はマスク側・二重縮小禁止）** ／ ③運用上通ってよい = 02 keepout（10 側の正本は沈黙＝operation mode / 横断ゲートのみ）。深度欠測は `UNKNOWN` であり落下を確定させない（costmap では未観測 = 通行不可のまま）。**未観測 = 通行不可の判定は cliff_scan marking・costmap `noDataObstacle`・coverage → X2 の 3 経路になるため一本化を `OQ-OD4C` で裁定** | [08 §3-1](08-architecture-v2-reference-alignment.md:65)・[08 §5](08-architecture-v2-reference-alignment.md:119)・[09 §2-h](09-external-review-v3-response.md:153)（②の照合者は正本の沈黙 = レーン F #5/#6） |
| 01 coverage・05 Temporal | **05_Mission_and_Route**（レーン F 指摘 F2 = 従来欠落） | 「観測範囲外へ進ませない・帰還の長距離後退禁止・観測範囲の向きの検証」= **05 が所有**（[09 §2-h](09-external-review-v3-response.md:155)）。04 は coverage を出すだけで、2026-09-14 追補「後退・旋回」行は 05 への consume 記述として読む | [09 §2-h](09-external-review-v3-response.md:155)・[08 §2 05 行](08-architecture-v2-reference-alignment.md:44) |
| 02 Objects | 12 Failsafe/MRM・06 Navigation・07 Safety Layer・帯セレクタ（`warehouse_perception` speed_band） | 04 = 位置・速度・不確かさ・最終観測時刻。減速 = 帯セレクタの**入力**（[05 §3-3](05-safety-envelope-and-intervention.md)・ADR-0012 決定 11 = `speed_limit` 単一 publisher。**入力契約は現状 `gesture_events` のみ＝距離→帯の契約は未定（05 の `OQ-OD55`〔GNSS 品質ゲートの帯合流〕と同じ穴。05:110 への同一行追記は 05 担当へ申し送り）**、帯の実装点は 08 §3-4「06」と productization/01「L4 warehouse_perception」で不一致 = レーン F F7）／MRM 段 = 12／通常回避 = 06／停止 = 07 の scan polygon（人物分類の成功を幾何停止の前提にしない）。親 §5 の「前方 X m で slow 帯 → 停止」と tree の 04 行は裁定後に同一行修正 | [08 §3-4](08-architecture-v2-reference-alignment.md:83)・[ADR-0012:28](../adr/0012-speed-band-no-l2-best-effort.md:28)・[warehouse_perception/CLAUDE.md:16](../../ws/src/warehouse_perception/CLAUDE.md:16) |
| 03 | 02 crossing registry・10 crossing_gate・11 Remote（承認トークン発行）・05（route event） | registry（`crossing_id`・進入方位・ROI・想定距離）= 02 が正本（[08 §3-1](08-architecture-v2-reference-alignment.md:66)。**置き場は `OQ-OD47`・08 自体は `OQ-OD80` で未裁定**）。04 は registry を consume して「自分の灯器」を検証し観測を出す。進入可否 = 10（GREEN ∧ 一回限りトークン = [09 §2-f](09-external-review-v3-response.md:132)）。ROI 再投影に灯器 3D 位置が要るなら registry へ additive | [08 §3-1](08-architecture-v2-reference-alignment.md:66)・[09 §2-f](09-external-review-v3-response.md:131) |
| 04 Surface | 02 allowed_area / keepout・06・10・12 | 04 = 画素分類の観測と「許可帯との不一致」の観測。**不一致で減速 / 停止するのは 04 ではない**（帯セレクタ入力・12 の MRM 段）。単独権威にしない・Phase 2 | [親 §2](04-perception-sidewalk-and-signals.md:47)・[08 §3-4](08-architecture-v2-reference-alignment.md:84) |
| 05 | 06 local costmap（odom rolling・obstacle 層の marking 履歴）・02 local_terrain・03（ego motion の TF 所有）・X2 | 障害物 marking の履歴 = costmap 1 本（二重化しない）。04 の grid = coverage / 鮮度 / clear 条件の材料。**期限（stale）の判定 = X2（センサ鮮度は 09 §2-b で成立・04 内部 grid の失効は正本の沈黙）**。過去の記憶（「数秒前に床が見えた」）で新しい障害物・落下観測を上書きしない | [08 §2 06 行](08-architecture-v2-reference-alignment.md:45)・[09 §2-b](09-external-review-v3-response.md:72)・[12:547](../architecture/12-infrastructure-common.md:547) |
| 06 Prediction | 06 Navigation（MPPI）・12 | Phase 2。予測結果を消費する 06 側の評価処理まで含めて初めて意味を持つ（Navfn が route edge を理解しないのと同型 = [09 §2-h](09-external-review-v3-response.md:154)）→ `OQ-OD4H` と同型 | [09 §2-h](09-external-review-v3-response.md:154) |
| 07 | 06 costmap・07 CM・X2・10・12 | adapter は 04 に残る（既存 `cliff_scan` / pcl2laser と一致・dual-consumer = [12:547](../architecture/12-infrastructure-common.md:547)・Humble CM は途絶 fail-open = [12 末尾追補](../architecture/12-infrastructure-common.md)）。**topic を publish しただけでは Navfn / MPPI は新しい意味を理解しない** → 06 側 costmap layer・評価処理の所有を裁定（`nav2_params.yaml` / launch は nav-traffic 所有 = [12:552](../architecture/12-infrastructure-common.md:552)） | [08 §2](08-architecture-v2-reference-alignment.md:45-46)・[12:547](../architecture/12-infrastructure-common.md:547) |
| 08 | X2 Diagnostics・X1 Observability・09（許可失効）・11（映像鮮度は別監視） | 判定点は X2 の 1 か所（**`/{bot}/scan` の途絶は #679〔origin/main af3550e・2026-09-16〕で Emergency Guardian の `scan_stale`（L1・level・自動解除）に着地済 = [doc12 末尾追補 (3)](../architecture/12-infrastructure-common.md)。cliff_scan / depth の途絶補償は未着地＝本行と `OQ-OD95` の対象。経路の記述は X2→12→09 / X2→09 / 二重経路の 3 通り = [08 §2](08-architecture-v2-reference-alignment.md:53)・[09 §2-b](09-external-review-v3-response.md:72)・[05 追補 1](05-safety-envelope-and-intervention.md:131)。実装位置は `OQ-OD95` で一本化。本追補は「判定点は X2 の 1 か所」のみを主張**）。記録は X1。`depth_validity` は数値（有効画素率・凍結検出）を出し判定はしない（レーン F F15） | [09 §2-b](09-external-review-v3-response.md:72)・[09 §7 OQ-OD95](09-external-review-v3-response.md:255) |
| 09 / 10 | 00 dependencies・02 §4・X2（tick jitter）・X1・[doc20](../architecture/20-dev-quality-and-testing.md) | manifest = 04 が artifact として所有（重み hash・前処理・ラベル・opset・TensorRT 版・GPU arch）、**版一覧 = 00**（[00 末尾追補](00-mission-and-scope.md)へ forward link・競合させない = レーン F F10）。予算の数値は屋外 S1 の実測後（発明しない）。記録・再生の基盤 = X1（run record に配線済・実走記録は未実施） | [08 §2 00 行](08-architecture-v2-reference-alignment.md:39)・[jetson/03 §3](../jetson/03-build-deploy-run-and-run-records.md) |

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
- 二段レート方式（`OQ-OD4J`）: レート A = ROI 輝度サンプラ（カメラ fps・エッジ検出なので整数比でも可・CPU・緑 / 赤の hue 面積比 g_t / r_t と露出値 e_t）でヒステリシス二値化 → **立ち上がりエッジ間隔（= 周期）**の中央値が 0.5 s に一致（隣接エッジ間隔は 0.25 s） ∧ ON / OFF 両相あり ∧ 赤が同期して増えていない → `is_flashing`（2 Hz の Goertzel 1 点評価で補強可）。レート B = NN 分類器 10 Hz（TIER IV 3 クラス）で多数決。状態決定: `is_flashing` → GREEN_FLASHING／RED 多数決 → RED／GREEN 多数決 ∧ NOT flashing ∧ **窓内の OFF 相 0 件** ∧ stale < 0.5 s → GREEN／それ以外 UNKNOWN。GREEN 離脱は 1 フレーム（親 §4 の非対称を維持）。f_B は点滅周期と**非整数比**にする（例 9 Hz = 4.5 倍。12 Hz や 30 Hz は 2 Hz の整数倍で同じ罠）。
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

Orin Nano Super 8GB の配置で足りるかは、カメラ 2 台 + Nav2 + 映像配信を同時に動かして測る（[02 §4](02-architecture-split-orin-pc-cloud.md)）。平均 FPS でなく**撮影から結果が消費されるまでの遅延**と滞留を重視する。下向きカメラの角度は `d_観測確認済み > v·T_total + v²/(2·a_min) + 余裕`（[09 §2-g](09-external-review-v3-response.md)・T_total / a_min は 00 の契約）を車体前端・車輪から見た観測範囲で満たすかで決める（旋回は車体と車輪の通過範囲）。**この不等式の評価点は X2 / 09 の 1 か所**にし、04 は観測済み距離を出すだけにする（レーン F F18）。

### 9. レイヤ annotation の要追記（[productization/01:174](../productization/01-commercial-box-map.md:174)・実装 PR で行う提案・レーン F）

01 `cliff_detector` / `ground_estimator` = 自律走行（安全層外）producer・consumer は L1 CM と L1 Navigation／07 adapters = 出力は L1 Safety の observation／07 coverage = consumer は X2・06／03 `lamp_classifier` / `temporal_state` = L4・許可は L2（10）を同一行に併記／02 `detector` / `tracker` = L4・停止の最終担保は L1（07）／04 `sidewalk_segmentation` = 自律走行（安全層外・裁定待ち）／08 metrics = 横断（観測面）／09 Runtime = 単一 layer に帰属させない・版 pin は 00／10 Evaluation = 横断／既存 L4 行（`warehouse_perception`）に 04 系ノード同居と executor 分離規律。

### 10. OPEN QUESTIONS（本追補・接頭辞 `OQ-OD4*` の続き）

- `OQ-OD4B` 04 内部分解（00〜10 の 11 sub-dir）を責務ラベルとして採るか（ユーザー裁定 2026-09-16: 粒度は問題なし・採用方向。1 dir ≠ 1 node / process・家は `warehouse_perception`）。
- `OQ-OD4C` terrain 出力の三分離（観測 = 04／通行可能形状 = **06 が 00 と照合 or 02 keepout マスク側**〔正本の沈黙・二重縮小禁止〕／運用許可 = 02・10）と `FLOOR_CONFIRMED` の意味限定・段差高・勾配・凹凸・推定誤差の別フィールド化（型は contract PR）。`UNKNOWN` ≠ `DROP_DETECTED`。**未観測 = 通行不可の判定 3 経路（cliff marking / costmap noDataObstacle / coverage → X2）の一本化**。
- `OQ-OD4D` 歩行者行の書換: 04 = 位置・速度・不確かさ・最終観測時刻のみ／減速 = 帯セレクタ入力（**距離 → 帯の入力契約は未定〔05 の `OQ-OD55` と同じ穴・05 担当へ申し送り〕・実装点の不一致 08 §3-4 vs productization/01**）／MRM 段 = 12／回避 = 06／停止 = 07（親 §5 と tree v2.1 04 行の同一行修正）。
- `OQ-OD4E` 二重化回避の所有 2 件: 局所観測履歴（04 evidence grid）vs costmap obstacle 層の履歴／品質の自己申告（04）vs 判定（X2 の 1 か所・経路は `OQ-OD95` で一本化）。
- `OQ-OD4F` 信号 ROI の更新方式: 固定登録 ROI／地図灯器 3D 位置 + カメラ姿勢の再投影（registry へ additive・置き場は `OQ-OD47`）／roi_refinement NN（TIER IV tlr YOLOX-S）。
- `OQ-OD4G` NN 候補の Humble + JetPack 6.2.1 + TensorRT 10.3.0 での変換・入出力互換・自画角評価（`OQ-OD41` との関係 = 公開重みで開始し自前収集は弱点確認後）。
- `OQ-OD4H` consumer 側実装（06 costmap layer・追跡物体 / 予測の評価処理）を 04 の実装範囲に含めるか（`nav2_params.yaml` / launch は nav-traffic 所有）。
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
