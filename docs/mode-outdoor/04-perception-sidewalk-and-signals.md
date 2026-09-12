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
| 自作 cliff detector → 仮想 LaserScan | 可 | **本命**。下向き深度から「期待地面高より低い / 点が返らない」領域を検出し、その方位・距離に**仮想的な壁**を撃つ |

**推奨: 既存 VirtualScan パターンの流用（`/bot1/cliff_scan`・additive 提案・未凍結）**。本プロジェクトは「実在しない障害物を `sensor_msgs/LaserScan` で注入し、costmap と collision_monitor の **dual-consumer** で共有する」契約を既に持つ:

- 契約: [doc12:547](../architecture/12-infrastructure-common.md:547)「virtual_scan は dual-consumer（移設ではない）・costmap から外さない」[D]
- costmap 側: [nav2_params.yaml:221](../../ws/src/warehouse_bringup/config/nav2_params.yaml:221) / [:230](../../ws/src/warehouse_bringup/config/nav2_params.yaml:230)（`marking: true` / `clearing: false`＝inf 光線が実障害物を消さない）/ [:274](../../ws/src/warehouse_bringup/config/nav2_params.yaml:274) [D]
- collision_monitor 側: [collision_monitor.yaml:27-28](../../ws/src/warehouse_bringup/config/collision_monitor.yaml:27) / [:76](../../ws/src/warehouse_bringup/config/collision_monitor.yaml:76) / [:81-86](../../ws/src/warehouse_bringup/config/collision_monitor.yaml:81)（条件付き publisher ゆえ `source_timeout` を 0.0＝沈黙は故障でない）[D]
- 生成側の形: [virtual_scan_logic.py:19](../../ws/src/warehouse_traffic/warehouse_traffic/virtual_scan_logic.py:19)（±15°）/ [:24](../../ws/src/warehouse_traffic/warehouse_traffic/virtual_scan_logic.py:24)（10 Hz）[D]

**P1 を壊さない理由**: collision_monitor の observation source は **LaserScan のまま**。GPU / 深度は cliff detector の**内部**に閉じ、L1 が見るのは契約化された LaserScan 型だけ。ただし **`source_timeout` の扱いは virtual_scan 型（沈黙可）ではなく scan 型（途絶＝停止）に寄せる**べき: 崖は「近づいたときだけ現れる」のではなく「センサが死んだら見えなくなる」ものだから、沈黙を安全と解釈してはいけない [I]（`OQ-OD44`）。

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
| `GREEN_FLASHING` | 窓内で GREEN/OFF が交番（周期 ~1 Hz）、または明示クラス | **新規横断の開始は禁止**（横断中は継続＝[01 §6](01-legal-envelope-japan.md)） |
| `RED` | 多数決 RED | 禁止 |
| `UNKNOWN` | **上記いずれも成立しない全ての場合**（検出ゼロ・票割れ・stale・ROI 外・カメラ断・クラウド断） | **禁止（fail-closed）**。LLM / ER は `UNKNOWN` を `GREEN` に昇格できない |

「青点滅 → 青」「赤 → 青」の誤りをゼロにするため、**GREEN への遷移のみ閾値を非対称に厳しく**する（GREEN 判定は 8/10・GREEN 離脱は 1 フレームで即）[I]。

## 5. 歩行者（進路を譲る）

- 検出: 前方 OAK-D の RGB で人物検出（信号検出器と同じ TensorRT 経路・別クラス）。深度で距離。
- 挙動: 前方 X m 以内に歩行者 → `slow` 帯（best-effort）→ さらに近ければ **停止**（L1 collision_monitor の stop polygon が最終担保＝pose 非依存の床）。「進路を譲る」＝停止して待つ（法 14 条の 2）。退避は Phase 2。
- L2 は関与しない（位置 goal の許可には影響しないため）。

## 6. costmap 統合（Humble・Phase 1 は P1/P2 と 8 GB を守る）

| 層 | Phase 1 | 根拠 |
|---|---|---|
| Obstacle Layer ← `/bot1/scan` | 維持 | T-mini Plus 60 kLux・既存契約 |
| Obstacle Layer ← `/bot1/cliff_scan`（新） | **追加**（`marking: true` / `clearing: false`） | §3。既存 virtual_scan と同じ書き方（[nav2_params.yaml:230](../../ws/src/warehouse_bringup/config/nav2_params.yaml:230)） |
| Obstacle Layer ← 前方深度 → `pointcloud_to_laserscan` | **追加**（Humble バイナリ有 [L]） | 3D を 2D に潰すだけ＝GPU 0。段差・張り出しは cliff 経路で別に拾う |
| `/map`（旧 Static Layer） | **再定義**: RTK 歩道ポリゴンをラスタ化した走行可能領域マップ（§2 案 C）。単独 publisher・decay しない性質はそのまま | global costmap の static_layer を外すか再定義するかは [03 §3-4](03-localization-gnss-and-ekf.md) #2 と同期 |
| Nvblox Layer | **Phase 2 へ延期** | Isaac ROS **3.2 Update 1 で JetPack 6.2 + Orin Nano Super（Humble）対応**・3.2 で「Nav2 costmap 統合修正」[L]。ただし屋外は pose 焼き付き（[23:79](../architecture/23-perception-and-localization.md:79)）+ 歩行者（室内で dynamic 層不採用とした前提が逆転）+ GPU 予算。**4.6 系（Jazzy）の資料を誤参照しない**（`OQ-OD46`） |
| Voxel Layer / STVL | 不採用（retreat plan） | [23:71](../architecture/23-perception-and-localization.md:71) の判断を踏襲 |
| collision_monitor observation | **`scan` + `cliff_scan` のみ** | **P1 不変 = LaserScan のみ**（[23:37](../architecture/23-perception-and-localization.md:37)）。polygon 寸法は速度 4 倍で再設計 |

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
| Inflation Layer | **変更（値のみ）** | 0.085 は 200 mm 隘路の live 実証値（#125）。歩道幅・144 mm 輪・4.5 km/h で再調整 |
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
