# 03 — 自己位置: RTK-GNSS + navsat_transform + 2 段 EKF（屋外差分）

作成日: 2026-09-12
Status: **記載済（設計値・一次情報つき・実装未）**。2026-09-12 のエージェントチーム（自己位置・RTK レーン）の報告を統合し、repo の pin は執筆時に実 Read、外部一次情報は URL + 参照日で残した（[D]=一次情報/実ファイル確認・[L]=調査レーン報告で URL あり（執筆者未再読）・[I]=推論）。室内 TARGET（[architecture/23](../architecture/23-perception-and-localization.md)）を置換しない。**屋外で反転・追加になる差分だけ**を本 doc に置く。

> 正本ルート: [mode-outdoor/README](README.md)。室内の EKF 単一所有・TF 配信責任は [23 §5-2](../architecture/23-perception-and-localization.md:145)、分類表は [23 §5-1](../architecture/23-perception-and-localization.md:141)。図解は [outdoor-localization-perception-flow.html](outdoor-localization-perception-flow.html) §3。
> レイヤ注記: 本 doc の構成要素（navsat_transform・EKF ×2・MOLA-LO）は**自律走行（Hard-RT・安全層外）**＝[23 §1](../architecture/23-perception-and-localization.md:21) の「Nav2/AMCL/SLAM は安全レイヤーに属さない（安全層が守る対象）」をそのまま継承する。GNSS 品質ゲートは **L1 Safety**（[05 §3](05-safety-envelope-and-intervention.md)）。

## 0. 位置づけ

- 室内 TARGET の**構造**（`odom→base_link` は ekf_node 1 個・MOLA-LO は第 2 入力・TF は 1 エッジ 1 ノード＝[23:163](../architecture/23-perception-and-localization.md:163)）は屋外でも生かす。
- 変わるのは **global の作り方**: 室内は AMCL（地図あり）、屋外は **GNSS を global EKF に入れる**（地図なし・datum 固定の局所直交座標系）。
- 経路は特定の固定経路（teach-and-repeat・ユーザー決定 2026-09-12）。**走行時に緯度経度を解決する必要は構造的に無い**（§3-2）。

## 1. 分類表の反転

| 分類 | 技術 | 室内（[23 §5-1](../architecture/23-perception-and-localization.md:141)） | 屋外（本 doc） | 理由 |
|---|---|---|---|---|
| グローバル | GNSS / RTK-GNSS | **対象外**（屋内設置・cm 精度は 0.01 m 地図でセル数個分・ジオラマ座標系・予算外） | **採用（必須・ユーザー決定）** | 「対象外」の理由が屋外で**すべて消える**。doc23 本文は書き換えず、doc23 末尾追補（2026-09-12）から本 doc へ forward link |
| グローバル | AMCL / SLAM Toolbox | 採用（CURRENT） | **屋外主経路から外す** | 地図が無い。AMCL の `tf_broadcast: true`（[nav2_params.yaml:59](../../ws/src/warehouse_bringup/config/nav2_params.yaml:59)）は global EKF と `map→odom` を**二重配信**するため、屋外 launch では AMCL を起動しない |
| ローカル | wheel（25 Hz）+ IMU + MOLA-LO | 採用 | **流用**（local EKF は室内構成のまま） | [23 B-3](../architecture/23-perception-and-localization.md:463) の状態割当を継承。MOLA-LO は屋外で縮退しうる（§7） |
| Sensor Fusion | robot_localization EKF | 1 個（`odom→base_link`） | **2 個（local / global）** | Nav2 公式 GPS チュートリアルと同じ dual-EKF 形（[References](#references)） |

AMCL 直購読の Guardian pose 鮮度 guard（[23 §5-3 blocker ①](../architecture/23-perception-and-localization.md:177)）の代替は §5。

## 2. 構成（TF・EKF・navsat_transform）

### 2-1. TF 配信責任（室内の「1 エッジ 1 ノード」規律をそのまま移植）

| TF エッジ | 室内 CURRENT | 屋外 | 根拠 |
|---|---|---|---|
| `map → bot1/odom` | AMCL（[23:150](../architecture/23-perception-and-localization.md:150)） | **global `ekf_node`（`world_frame: map`）** | AMCL 不在。`map→odom` を出すのは **global EKF 1 個だけ** |
| `bot1/odom → bot1/base_link` | local `ekf_node`（唯一） | **local `ekf_node`（不変）** | [23:163](../architecture/23-perception-and-localization.md:163) |
| `utm/cartesian → map` | — | **配信しない**（`broadcast_cartesian_transform: false`） | `navsat_transform_node` は `odometry/gps` を出すだけで TF を出さない構成にする＝TF 単一所有を守る [L] |
| `bot1/base_link → bot1/gnss_link` | — | `robot_state_publisher`（URDF static） | `FROZEN_LINK_NAMES`（[robot_dimensions.py:41-47](../../ws/src/warehouse_description/warehouse_description/robot_dimensions.py:41)）末尾への **additive contract PR**＝`camera_link` と同じ手順 [D] |

### 2-2. 入力割当（[23 B-3](../architecture/23-perception-and-localization.md:463) の屋外版）

| 状態 | wheel `odom0` | IMU `imu0` | MOLA-LO `odom1` | GNSS `odometry/gps`（global のみ） |
|---|---|---|---|---|
| x, y | × | × | ○（local） | **○（global EKF のみ）** |
| yaw | × | ×（§4-3） | ○（local） | ×（`odometry/gps` の姿勢は使わない） |
| vx, vy | ○ | × | × | × |
| vyaw | ○ | ○ | × | × |

- local EKF ＝ 室内構成の**そのまま**。global EKF ＝ local と同じ 3 入力 ＋ `odometry/gps`。
- **N−1 differential ルール**（[23 B-4](../architecture/23-perception-and-localization.md:477)）の屋外適用: global EKF の絶対姿勢ソースが MOLA-LO と GNSS の 2 本になる → どちらか 1 本を `differential: true`。**推奨は MOLA-LO を differential 化**（GNSS が絶対原点の唯一の権威）[I]（`OQ-OD35`）。**robot_localization 公式は「`navsat_transform_node` 経由の GPS は `_differential: false`」と明示**し、N−1 は目安で「共分散を十分大きくする」代替も併記する（`differential` は位置と姿勢の両方を差分化）[D]（[08 §4 #5](08-architecture-v2-reference-alignment.md))。local 側は 1 本のままなので `differential: false` 維持。
- ジャンプの防御は differential ではなく `odomN_pose_rejection_threshold`（Mahalanobis）＝[23 B-4 anti-pattern](../architecture/23-perception-and-localization.md:480) を屋外でも守る。

### 2-3. `navsat_transform_node` の主要パラメータ（robot_localization humble-devel・[L]）

| パラメータ | 屋外推奨 | 注記 |
|---|---|---|
| `datum: [lat, lon, yaw]` + `wait_for_datum: true` | **config 固定** | 起動ごとに原点が動かない（本 doc の要求）。route ファイル（§3-2）と**単一ソース**。**`datum` は `wait_for_datum: true` のときだけ宣言される**（単独指定は無効）[D]。レバーアームは NavSatFix の `frame_id` の TF で自動補正されるため **`gnss_link` = NavSatFix `frame_id` を一致させる**（一致しないと補正が無言で効かない）[D]（[08 §4 #6](08-architecture-v2-reference-alignment.md)） |
| `use_local_cartesian` | **true 推奨** | UTM zone 跨ぎ・歪みを避け、局所 ENU 原点を使う（`OQ-OD34`） |
| `broadcast_cartesian_transform` | **false** | TF 単一所有（§2-1）。旧名 `broadcast_utm_transform` は非推奨警告つきで受理 |
| `zero_altitude` | true | `two_d_mode` と整合 |
| `magnetic_declination_radians` / `yaw_offset` | 磁気 yaw を使う場合のみ実値（西偏＝負・ENU 基準） | §4-3 |
| `use_odometry_yaw` | §4-3 の裁定次第 | true なら IMU 不使用・odom の yaw を採用 |
| `publish_filtered_gps` | true | 記録・可視化。**humble-devel はコード既定 true・rst は false と記載**（上流ドリフト）[L] |
| サービス | `FromLL` / `ToLL` / `SetDatum` | humble-devel に実在（`srv/`）[L]。`FromLL` の使い方は §3-2 |

Humble での可用性: `robot_localization` / `ublox` / `ntrip_client` / `rtcm_msgs` / `nmea_navsat_driver` は rosdistro humble に release 済 [L]。

## 3. Humble 制約と代替（waypoint 実行・経路ファイル・Nav2 差分）

### 3-1. `FollowGPSWaypoints` は使えない → 既存 bridge の座標 goal を使う

- Nav2 公式チュートリアルは `FollowGPSWaypoints` を **Iron 以降**の機能と明記（[References](#references)）。本プロジェクトは Humble（[ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)）。
- 既存 `nav2_bridge` の `go_to` は **1 点なら `goToPose`、複数なら `goThroughPoses`** に分岐する（[nav2_bridge.py:87-95](../../ws/src/warehouse_nav2_bridge/warehouse_nav2_bridge/nav2_bridge.py:87)）[D]＝`NavigateThroughPoses` 相当が既に実装済。L3 で変換した x,y 列を渡すだけで動く。
- **前提条件（屋外の必須 additive 拡張）**: 現行 seam は `orientation.w = 1.0` を**ハードコード**し（[nav2_bridge.py:84](../../ws/src/warehouse_nav2_bridge/warehouse_nav2_bridge/nav2_bridge.py:84)）、`_coord_from_goal` は yaw を検証後に捨てる（[core.py:106-119](../../ws/src/warehouse_nav2_bridge/warehouse_nav2_bridge/core.py:106)）[D]。歩道の waypoint は進行方位を要し、`yaw_goal_tolerance: 0.10`（[nav2_params.yaml:110](../../ws/src/warehouse_bringup/config/nav2_params.yaml:110)）と衝突する → **yaw を通す additive 拡張**（`OQ-OD3A`）。

### 3-2. `fromLL` 変換の置き場 — 推奨 = L3 compile 段（teach 時に一度だけ）

| 案 | 置き場 | 長所 | 短所 | 判定 |
|---|---|---|---|---|
| A | L1 bridge（走行時に `fromLL` を毎回呼ぶ） | Nav2 公式 `FollowGPSWaypoints` と同形 | 安全経路に外部サービス依存を増やす。`navsat_transform` 未 ready で goal が落ちる。bridge は REST 応答と rclpy timer が同一 backend を共有（[nav2_bridge.py:18](../../ws/src/warehouse_nav2_bridge/warehouse_nav2_bridge/nav2_bridge.py:18) の注記）[D] | △ |
| **B** | **L3 compile 段（teach 時に一度だけ lat/lon → map へ変換し、経路ファイルに x,y を焼く）** | `datum` 固定なら変換は**決定論的**。既存の座標 goal seam に**そのまま**乗る。R-26 unit でオラクル可能 | `datum` 変更時に再 compile（＝再現性としては利点） | **◎ 推奨** [I] |
| C | B ＋ 起動時に `toLL` で逆変換照合 | datum ドリフトを起動時に検出 | 実装量 | ○（B の検証） |
| **D** | **Nav2 Route Server（`nav2_route` 1.1.20・humble backport #5359）**に経路網（node/edge・route operations）を持たせ、B の compile 出力を graph ファイルにする | 経路網・区間イベント（横断・狭路）が標準機能。Waypoint Follower より固定経路に向く | Humble backport の成熟度・`nav2_bridge` seam との接続が未検証 | ○（`OQ-OD3D` / [08 §3-1](08-architecture-v2-reference-alignment.md)） |

固定経路（teach-and-repeat）ゆえ走行時に緯度経度を解決する必要が構造的に無い。**裁定は `OQ-OD33`**（B か B+D か）。**外部レビューの「Route Server は Humble に無い」は誤り**（index.ros.org で humble 1.1.20 released・[08 §4 #4](08-architecture-v2-reference-alignment.md)）。

### 3-3. teach-and-repeat 経路ファイル（additive 提案・未凍結）

```yaml
route:
  id: "route_A_to_B"
  datum: {lat: 35.xxxxxxx, lon: 139.xxxxxxx, yaw: 1.5708}   # navsat_transform の datum と単一ソース
  recorded_at: "2026-09-12T10:00:00+09:00"
  frame: "map"                                              # compile 後の焼き込み先
  waypoints:
    - {seq: 0,  lat: ..., lon: ..., yaw: 1.53, x: 0.00, y: 0.00, speed_band: normal, tags: []}
    - {seq: 12, lat: ..., lon: ..., yaw: 1.55, x: 8.41, y: 0.22, speed_band: slow,   tags: [narrow]}
    - {seq: 20, lat: ..., lon: ..., yaw: 0.02, x: 14.9, y: 0.40, speed_band: stop,   tags: [crossing_approach, crossing_id=X1]}
    - {seq: 21, ...,                                         speed_band: cross,  tags: [crossing_enter,    crossing_id=X1]}
```

| フィールド | 役割 | 接続先 |
|---|---|---|
| `lat / lon` | teach 時の生記録（**RTK fix のみ採用**） | 監査・再 compile の原本 |
| `x / y / yaw` | L3 compile で `fromLL` 変換して焼いた結果 | bridge の座標 goal seam（§3-1） |
| `speed_band` | `normal / slow / stop / cross` | 既存 runtime speed limiter（[mode-m1/04](../mode-m1/04-runtime-speed-limiter.md)・[ADR-0012](../adr/0012-speed-band-no-l2-best-effort.md)）。**帯は安全機構ではない**（[05 §3](05-safety-envelope-and-intervention.md)） |
| `tags` | `crossing_approach / crossing_enter / narrow / geofence_exit` | [05 §4](05-safety-envelope-and-intervention.md) 横断ゲート状態機械 |
| `datum` | route と `navsat_transform` の**単一ソース** | datum 変更＝route 再 compile のトリガ |

waypoint 間隔 vs rolling global costmap: 一辺 `W` の costmap 外に goal が出ると planner が失敗するため **間隔 ≤ W/3** を目安（例: W = 40 m なら 10 m）[I]（`OQ-OD3B`）。

### 3-4. Nav2 差分（Humble・現行 `nav2_params.yaml` の変更対象行＝**未編集・実 Read 済**）

| # | 現行 | 行 | 屋外での扱い | 理由 |
|---|---|---|---|---|
| 1 | `amcl:` ブロック | [:35](../../ws/src/warehouse_bringup/config/nav2_params.yaml:35)〜[:59](../../ws/src/warehouse_bringup/config/nav2_params.yaml:59) | **ノードを起動しない**（param は残置） | `tf_broadcast: true` が global EKF と `map→odom` を奪い合う |
| 2 | `global_costmap.plugins` に `static_layer` | [:259](../../ws/src/warehouse_bringup/config/nav2_params.yaml:259) | **`static_layer` を外す**（歩道ポリゴンを持つなら [04 §2](04-perception-sidewalk-and-signals.md) の再定義 `/map` で代替） | `/map` が無い。static_layer 未ロードで planner が "Costmap timed out" → #67 教訓の再発 |
| 3 | global に `rolling_window` 無し（全域固定） | [:254-258](../../ws/src/warehouse_bringup/config/nav2_params.yaml:254) | **`rolling_window: true` + `width/height`** | 歩道を端から端まで地図化しない |
| 4 | `global_costmap.resolution: 0.01` | [:256](../../ws/src/warehouse_bringup/config/nav2_params.yaml:256) | **0.05 級へ緩和** | 0.01 で 50 m 角 rolling は 5000×5000 セル＝非現実 [I] |
| 5 | `global_frame: "map"` / `robot_base_frame` | [:254-255](../../ws/src/warehouse_bringup/config/nav2_params.yaml:254) | **不変** | global EKF が `map` を出す |
| 6 | local 3×3 m / 0.01 | [:211-214](../../ws/src/warehouse_bringup/config/nav2_params.yaml:211) | **6〜10 m 角・0.05 級** | 0.3 m/s で 10 秒先だった前提が 1.25 m/s では 2.4 秒（[04 §8](04-perception-sidewalk-and-signals.md)） |
| 7 | `track_unknown_space: true` | [:258](../../ws/src/warehouse_bringup/config/nav2_params.yaml:258) | 要再検討 | 地図なし＝全域 unknown 始まり（`OQ-OD3B`） |
| 8 | `virtual_scan` observation source | [:221](../../ws/src/warehouse_bringup/config/nav2_params.yaml:221) / [:274](../../ws/src/warehouse_bringup/config/nav2_params.yaml:274) | **残置・用途差替**（[04 §3](04-perception-sidewalk-and-signals.md) の cliff scan） | dual-consumer 契約を壊さない |
| 9 | `inflation_radius: 0.085` / `cost_scaling_factor: 10.0` | [:245-246](../../ws/src/warehouse_bringup/config/nav2_params.yaml:245) / [:291](../../ws/src/warehouse_bringup/config/nav2_params.yaml:291) | **再調整** | 200 mm 隘路向けの #125 実証値 |
| 10 | MPPI `FollowPath` | [:113](../../ws/src/warehouse_bringup/config/nav2_params.yaml:113)〜 | **構造は再利用。`vx_max`（[:122](../../ws/src/warehouse_bringup/config/nav2_params.yaml:122)）・`wz_max`（[:125](../../ws/src/warehouse_bringup/config/nav2_params.yaml:125)）・`time_steps`（[:116](../../ws/src/warehouse_bringup/config/nav2_params.yaml:116)）・critic 重みのみ** | `vx_max` は契約 `MAX_LINEAR_VELOCITY` の再 pin（[07 §9](07-drivetrain-and-wheel-sizing.md)・`OQ-OD71`）に従う |
| 11 | `reset_period: 1.0` | [:202](../../ws/src/warehouse_bringup/config/nav2_params.yaml:202) | 不変 | 帯が消える経路（[ADR-0012](../adr/0012-speed-band-no-l2-best-effort.md:14)） |

## 4. RTK（受信機・品質・方位）

### 4-1. 受信機クラス（決定: RTK は必ず入れる・選定は `OQ-OD37`）

| クラス | 補正の経路 | Humble ドライバ | 品質フィールド | 通信断との関係 |
|---|---|---|---|---|
| u-blox ZED-F9P 系 + NTRIP | LTE で NTRIP（善意の基準局 / ichimill / docomo / JENOBA） | `ublox`（`ublox_gps`）[L] | `ublox_msgs/NavPVT`: `fix_type`・`flags`（`CARRIER_PHASE_FLOAT / FIXED`）・`num_sv`・`h_acc`・`p_dop` [L] | **LTE 断＝補正断**（float 縮退）＝[05 §3](05-safety-envelope-and-intervention.md) リンク断 watchdog と同じ故障源 |
| QZSS CLAS 対応機（例: ビズステーション Drogger） | **衛星経由**（通信契約・NTRIP 不要） | ROS 2 専用ドライバ無し → **NMEA 経由**（`nmea_navsat_driver`）[L] | NMEA GGA `quality`（4 = fix / 5 = float）・HDOP・衛星数 | **LTE 断と故障モードが独立**＝屋外で価値大 [I] |

**`sensor_msgs/NavSatFix` 単体では fix と float を区別できない**（float も `STATUS_FIX` に潰れる）[L] → 品質ゲートは `NavSatFix` だけを見ない。

### 4-2. 品質ゲートの入力契約（additive 提案・未凍結・`OQ-OD38`）

| topic | 型 | producer | consumer | 備考 |
|---|---|---|---|---|
| `/bot1/gps/fix` | `sensor_msgs/NavSatFix` | 受信機ドライバ | `navsat_transform` | 標準型。covariance を EKF が使う |
| `/bot1/ublox/navpvt`（または NMEA 由来） | 受信機固有 | 受信機ドライバ | **`gnss_quality_gate`** | fix / float / single・衛星数・DOP・`h_acc` の単一ソース |
| **`/bot1/gnss/quality`（新規）** | `warehouse_interfaces` に `GnssQuality`（案） | **`gnss_quality_gate`（L1 Safety）** | Emergency Guardian・遠隔卓 | `level`（FIX/FLOAT/SINGLE/NONE）・`h_acc_m`・`num_sv`・`hdop`・`age_s`・`heading_valid`。**受信機非依存の正規化層**＝受信機を替えても Guardian の契約が動かない |

これは室内の「Guardian 監視プロファイル」（監視 topic + 型 + 鮮度 + 自己申告を 1 プロファイルに束ねる＝[23 追補 A-5](../architecture/23-perception-and-localization.md:332)）と同型。

### 4-3. 方位（yaw）の初期化

`navsat_transform` は odom 原点と GNSS 原点の対応を作るのに **heading** を要する。heading が無い / 誤っていると `map→odom` は「正しい距離だけ誤った方向」にずれ（回転誤差）、走るほど開く [I]。

| 手段 | 実現 | 評価 | 判定 |
|---|---|---|---|
| IMU 磁気（ICM-20948 内蔵 AK09916） | `magnetic_declination_radians` + `yaw_offset` | 金属シャーシ・モータ電流・街灯柱・マンホールで擾乱 | △ 主系にしない（`OQ-OD31`） |
| 2 アンテナ GNSS heading（ZED-F9P moving base） | `NavRELPOSNED` の heading [L] | 静止でも真方位。受信機 2 台 + アンテナ 2 本 | ◎ 精度要求が上がれば本命（`OQ-OD36`） |
| GNSS 速度由来 heading | `use_odometry_yaw` / NavPVT `heading` | 無料。**動かないと収束しない** | ○（併用） |
| **記録経路からの初期方位** | teach 時の A 地点方位を route の `datum.yaw` に焼く | 無料・決定論的・再現性◎ | **◎ 固定経路の第一手** |

**推奨**: 「datum yaw 焼き込み ＋ 走行開始後に GNSS 速度由来で補正」を既定にし、2 アンテナは `OQ-OD36`。**fix 取得 ＋ heading 収束までは発進禁止**を [05 §3](05-safety-envelope-and-intervention.md) の GNSS 品質ゲートの段として置く。

## 5. Guardian の pose 鮮度 guard の代替（AMCL 不在で壊れるもの）

| 箇所 | 事実 | pin |
|---|---|---|
| 購読 | `/{bot}/amcl_pose` を**ハードコード**購読 | [emergency_guardian.py:143-148](../../ws/src/warehouse_safety/warehouse_safety/emergency_guardian.py:143) [D] |
| 閾値 | `pose_freshness_timeout: 1.0` | [emergency_guardian.py:73](../../ws/src/warehouse_safety/warehouse_safety/emergency_guardian.py:73) / [warehouse.base.yaml:21](../../config/warehouse.base.yaml:21) [D] |
| 判定 | `pose_age` が閾値超 **かつ** 変位ゲート開 → `pose_stale` estop | [guard_logic.py:169](../../ws/src/warehouse_safety/warehouse_safety/guard_logic.py:169) / [:233](../../ws/src/warehouse_safety/warehouse_safety/guard_logic.py:233) [D] |
| 破綻の形 | `pose_age` は最初の pose まで `None`（[guard_logic.py:29](../../ws/src/warehouse_safety/warehouse_safety/guard_logic.py:29)）で `is not None` ガードがある → AMCL が居ないと **estop は一度も発火せず、ガードは恒久沈黙**（誤発火ではなく無音化） | [D]→[I] |

屋外の pose 源候補:

| 候補 | 鮮度が loss 検出になるか | 判定 |
|---|---|---|
| `navsat_transform` の `/odometry/gps` | fix を失えば止まるが、**float でも出続ける** | △ 補助 |
| **global EKF `/bot1/odometry/filtered`（map）** | **ならない**（GNSS が落ちても wheel + IMU で外挿し続ける＝MOLA-LO と同じ「出続ける」性質） | **○ pose 源の本命・鮮度単独では不可** |
| `/bot1/gnss/quality`（§4-2） | なる（自己申告） | **◎ 主判定** |

**推奨**: 屋外版 guard は「pose 源 = global EKF、鮮度 = 補助、主判定 = `/bot1/gnss/quality` の `level` と `h_acc`」の 2 入力構成。室内で MOLA-LO について出した結論（鮮度は補助・主判定は `pose_quality` 閾値＝[23 追補 A-7](../architecture/23-perception-and-localization.md:367)）と同型で、restrict-only（floor より締める方向のみ）と operational / protective stop の分離（[23 追補 A-3](../architecture/23-perception-and-localization.md:308)）を継承する。floor 値は `OQ-OD39`。

## 6. 受け入れ条件・ゲート（設計値・実測未）

| # | 試験 | 合格の目安 | 備考 |
|---|---|---|---|
| L-1 | 静止 60 s の位置分散（fix 時） | 水平 σ < 0.05 m | 受信機仕様の確認 |
| L-2 | 直線 50 m 走行の横ずれ（teach 軌跡との差） | < 0.15 m（歩道幅の 1/10 級）[I] | 車輪スケール k（[07 §10 G-W4](07-drivetrain-and-wheel-sizing.md)）と同時 |
| L-3 | ループ復帰誤差（A→B→A） | < 0.2 m [I] | global EKF の整合 |
| L-4 | 経路全長の fix 維持率（teach 時に走査） | fix 率と float 区間の地図化 | 樹木・建物の影（`OQ-OD3C`）。float 区間は route に `slow` 帯 |
| L-5 | heading 収束時間（発進から） | < 5 s [I] | §4-3。収束前は発進禁止 |
| L-6 | GNSS 断（アンテナ遮蔽）注入 | 品質ゲートが減速→停止へ遷移・自動復帰しない | [05 §3](05-safety-envelope-and-intervention.md) |

## 7. MOLA-LO の屋外（`OQ-OD30` への提案回答 = conditional yes）

- 開けた歩道では 2D スキャンマッチングの幾何拘束が弱く**縮退する**が、MOLA-LO は縮退しても出力を止めない（[23 追補 A-6](../architecture/23-perception-and-localization.md:355) の性質）。
- 屋外での価値は **GNSS 劣化時の短時間 dead-reckoning 補完**。**global EKF の絶対姿勢ソースにはしない**（§2-2 の differential 化）。代替候補（KISS-ICP 2D 経路なし・rf2o zero-covariance・Cartographer 保守停止）は室内で評価済（[23:455](../architecture/23-perception-and-localization.md:455)）で新たな有力候補は無い。GPLv3（[23:457](../architecture/23-perception-and-localization.md:457)）も不変。
- → **local EKF に限定・`pose_quality` ゲート必須**の条件付き採用を提案（`OQ-OD30` の解）。

## 8. 再利用 / 変更 / 新規 / 廃止（[perception-localization-flow.html](../architecture/perception-localization-flow.html) §3 と §1 の自己位置部）

| ノード / エッジ | 分類 | 理由 |
|---|---|---|
| AMCL（`map→odom`） | **廃止（屋外）** | 地図なし・TF 二重配信 |
| SLAM Toolbox | **廃止（屋外）** | teach 軌跡で代替 |
| local `ekf_node`（`odom→base_link`） | **流用** | 入力も TF 責任もそのまま |
| global `ekf_node`（`map→odom`） | **新規** | AMCL の座を継ぐ唯一のノード |
| `navsat_transform_node` | **新規** | WGS84 → 直交・`FromLL/ToLL`。TF は出さない |
| 受信機ドライバ（`ublox_gps` / NMEA） | **新規** | `NavSatFix` + 品質フィールド |
| `ntrip_client` | **新規**（CLAS なら不要） | RTCM を受信機へ |
| `gnss_quality_gate`（L1 Safety） | **新規** | [05 §3](05-safety-envelope-and-intervention.md) |
| `robot_state_publisher` | **変更（additive）** | `gnss_link` を `FROZEN_LINK_NAMES` 末尾へ（contract PR） |
| MOLA-LO（`odom1`） | **変更** | local 限定・global では differential |
| `m1_driver`（`/odom` `/imu`） | **流用** | odom TF を出さない契約も不変 |
| Emergency Guardian（pose 鮮度） | **変更** | 購読先を `/amcl_pose` → global EKF + `/gnss/quality`（R-26 unit 必須） |
| `/bot{n}/odom` 変位ゲート | **流用** | wheel odom は屋外でも独立 witness |
| `nav2_bridge` 座標 goal seam | **変更（additive）** | yaw を通す拡張（§3-1） |
| collision_monitor / twist_mux / L0' クランプ | **流用（不変の床）** | pose 非依存（[23 追補 A-2](../architecture/23-perception-and-localization.md:296)） |

## 9. OPEN QUESTIONS（接頭辞 `OQ-OD3*`）

- `OQ-OD30` MOLA-LO は屋外で成立するか → **提案: conditional yes**（§7）。
- `OQ-OD31` IMU 絶対 yaw（磁気）を屋外で使うか → §4-3 の推奨は「使わない（主系にしない）」。
- `OQ-OD32` fix ロスト時の挙動（減速か停止か）を品質ゲートのどの段に置くか → **float = 減速（best-effort）／ロスト・非有限 = 停止／heading 未収束 = 発進禁止** の段分けを提案（[05 §3](05-safety-envelope-and-intervention.md)）。
- `OQ-OD33` `fromLL` 変換の置き場を **L3 compile 段**で確定してよいか（§3-2 案 B）。
- `OQ-OD34` `use_local_cartesian: true`（局所 ENU）か UTM か。
- `OQ-OD35` global EKF で differential にするのは MOLA-LO か GNSS か（N−1 ルール）。
- `OQ-OD36` 方位初期化の既定を「datum yaw 焼き込み + GNSS 速度補正」でよいか。2 アンテナは予算・OQ 送りでよいか（ユーザー）。
- `OQ-OD37` 受信機クラス: NTRIP（通信依存）vs QZSS CLAS（衛星補強・LTE 断に強い・ROS 2 ドライバ無し）（ユーザー）。
- `OQ-OD38` `/bot1/gnss/quality` を `warehouse_interfaces` に足すか（contract PR）、既存 diagnostics で代用するか。
- `OQ-OD39` Guardian の屋外 pose 源の restrict-only floor（室内 floor 1.0 s を継承するか。EKF は出続けるので鮮度 floor の意味が変わる）（safety-state トラック）。
- `OQ-OD3A` `nav2_bridge` の goal seam に yaw を通す additive 拡張を屋外の前提条件にするか。
- `OQ-OD3B` rolling global costmap の一辺 `W` と waypoint 間隔（暫定 ≤ W/3）・`resolution`（0.01 → 0.05）・`track_unknown_space` の扱い。
- `OQ-OD3C` 経路上の fix 維持率（urban canyon）の事前走査と、float 区間の帯付け。
- `OQ-OD3D` Nav2 Route Server（humble 1.1.20）を経路正本に採るか（§3-2 案 D・[08 §7 OQ-OD81](08-architecture-v2-reference-alignment.md) と同一）。

## References

docs 内（file:line は執筆時に実 Read）:

- [architecture/23-perception-and-localization.md](../architecture/23-perception-and-localization.md)（[:21](../architecture/23-perception-and-localization.md:21) 安全層外 / [:141](../architecture/23-perception-and-localization.md:141) GNSS 対象外 / [:150](../architecture/23-perception-and-localization.md:150) AMCL map→odom / [:163](../architecture/23-perception-and-localization.md:163) TF 単一所有 / [:177](../architecture/23-perception-and-localization.md:177) blocker ① / [:296](../architecture/23-perception-and-localization.md:296) A-2 床 / [:308](../architecture/23-perception-and-localization.md:308) A-3 / [:332](../architecture/23-perception-and-localization.md:332) A-5 / [:355](../architecture/23-perception-and-localization.md:355) A-6 / [:367](../architecture/23-perception-and-localization.md:367) A-7 / [:455](../architecture/23-perception-and-localization.md:455) 代替 / [:457](../architecture/23-perception-and-localization.md:457) GPLv3 / [:463](../architecture/23-perception-and-localization.md:463) B-3 / [:477](../architecture/23-perception-and-localization.md:477) B-4 / [:480](../architecture/23-perception-and-localization.md:480) anti-pattern）
- [nav2_params.yaml](../../ws/src/warehouse_bringup/config/nav2_params.yaml)（§3-4 の各行）/ [nav2_bridge.py:18,84,87-95](../../ws/src/warehouse_nav2_bridge/warehouse_nav2_bridge/nav2_bridge.py:84) / [core.py:106-119](../../ws/src/warehouse_nav2_bridge/warehouse_nav2_bridge/core.py:106) / [robot_dimensions.py:41-47](../../ws/src/warehouse_description/warehouse_description/robot_dimensions.py:41)
- [emergency_guardian.py:73,143-148](../../ws/src/warehouse_safety/warehouse_safety/emergency_guardian.py:143) / [guard_logic.py:29,169,233](../../ws/src/warehouse_safety/warehouse_safety/guard_logic.py:233) / [config/warehouse.base.yaml:21](../../config/warehouse.base.yaml:21)
- [ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md) / [ADR-0012](../adr/0012-speed-band-no-l2-best-effort.md) / [mode-m1/04](../mode-m1/04-runtime-speed-limiter.md) / [05 安全包絡](05-safety-envelope-and-intervention.md) / [04 知覚](04-perception-sidewalk-and-signals.md) / [06 ハード差分](06-hardware-delta-and-base-selection.md) / [07 駆動系](07-drivetrain-and-wheel-sizing.md)

一次情報（参照日 2026-09-12・[L] は調査レーン報告）:

- Nav2 Docs「Navigating using GPS Localization」<https://docs.nav2.org/jazzy/tutorials/general_tutorials/navigation2_with_gps/navigation2_with_gps/>（`FollowGPSWaypoints` は Iron 以降・dual EKF・`navsat_transform`・RTK 推奨）[D]
- robot_localization humble-devel `src/navsat_transform.cpp` <https://github.com/cra-ros-pkg/robot_localization/blob/humble-devel/src/navsat_transform.cpp> / `srv/` <https://github.com/cra-ros-pkg/robot_localization/tree/humble-devel/srv> / `doc/navsat_transform_node.rst` [L]
- rosdistro humble `distribution.yaml` <https://raw.githubusercontent.com/ros/rosdistro/master/humble/distribution.yaml>（robot_localization / ublox / ntrip_client / rtcm_msgs / nmea_navsat_driver）[L]
- ublox ROS 2: `NavPVT.msg` <https://github.com/KumarRobotics/ublox/blob/ros2/ublox_msgs/msg/NavPVT.msg> / README（moving base）<https://github.com/KumarRobotics/ublox/blob/ros2/README.md> [L]
- QZSS CLAS 受信機（ビズステーション Drogger）<https://www.bizstation.jp/ja/drogger/rtk_rcv_index.html> / <https://qzss.go.jp/info/archive/bizstation_221226.html> / RTK 基準局方式 <https://drogger.hatenadiary.jp/entry/RTK_GUIDE> [L]
