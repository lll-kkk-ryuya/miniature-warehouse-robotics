# 知覚・自己位置スタック（nvblox / MOLA-LO / EKF / Nav2 costmap）— 単騎構成 TARGET 設計

作成日: 2026-08-07
Status: **DRAFT / TARGET 設計提案**（CURRENT 構成は変更しない。スパイクゲート S1/S2 が緑になるまで `nav2_params.yaml` の `plugins` リストは触らない）
前提: [ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)（Humble + Isaac ROS 3.x + M1 メカナム + Orin Nano Super 8GB）・[ADR-0006](../adr/0006-single-bot-first.md)（単騎構成）

> 参考実装: ZED2i + nvblox + Nav2 の公開事例（Qiita motoms・Orin NX 16GB・Humble）を参照した。**同記事の実測値（メモリ 10-12GB 等）はハード・構成が異なるためそのまま持ち込まない**（§7 S1）。

## 0. 位置づけと read-order

本 doc は **Isaac ROS nvblox を Nav2 costmap 層として統合し、robot_localization EKF + MOLA-LO（2D LiDAR odometry。旧候補 cuVSLAM は blocked-by-hardware＝末尾 B-1）で自己位置を強化する TARGET 構成**を定義する。現行（CURRENT）＝ SLAM Toolbox + AMCL + 2D costmap は [09-navigation-internals](../shared/09-navigation-internals.md) が引き続き正本であり、本 doc はそれを置換しない。

| 参照 | 何の正本か |
|---|---|
| [shared/09-navigation-internals](../shared/09-navigation-internals.md) | AMCL / Nav2 / 2D costmap（**CURRENT の正本**） |
| [03-software-architecture](03-software-architecture.md) | トピック契約カタログ（:113 = plumbing 除外規約） |
| [12-infrastructure-common](12-infrastructure-common.md) | 安全レイヤー L0-L3・cmd_vel 挿入トポロジ（:526-541） |
| [shared/02-hardware-design](../shared/02-hardware-design.md) | M1 実寸・HP60C・T-mini Plus・給電 |
| [ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md) | Humble / Isaac ROS 3.x / Gazebo 未決 |

## 1. レイヤ対応と設計原則

**結論: nvblox / MOLA-LO / EKF は「自律走行（Hard-RT）／安全層外」に座る。安全レイヤー L0/L1/L2 のいずれにも属さない。** 根拠は [12:103,109](12-infrastructure-common.md)「Nav2/AMCL/SLAM は Hard-RT の自律走行スタックだが『安全レイヤー』には属さない（＝安全層が守る対象）」。nvblox は Nav2 の costmap を賢くする側、MOLA-LO/EKF は AMCL と同じ自己位置の側で、いずれも SLAM Toolbox / AMCL と同じ箱に入る。

```
役割層          時間階層     安全層     構成要素                              本 doc の新要素
─────────────────────────────────────────────────────────────────────────────
戦略            Non-RT 3-5s  Layer 3   LLM Bridge / Hermes / MCP             （無関係）
調整            Soft-RT      Layer 2   State Cache / VirtualScan / Policy    （無関係）
自律走行        Hard-RT      （層外）  Nav2 / AMCL / SLAM Toolbox            ★ nvblox / Nvblox Layer
                                        + costmap                             ★ robot_localization EKF
                                                                              ★ MOLA-LO（TARGET-1）
緊急監視        Hard-RT      Layer 1   Emergency Guardian / collision_monitor（nvblox 非依存を維持）
物理安全        即時          Layer 0   STM32 firmware / L0' host clamp       （不変）
```

### 原則 P1: 反射安全経路（L1）に GPU 依存を持ち込まない

`nav2_collision_monitor` は PointCloud2 を取れるため技術的には depth/nvblox を入れられるが、**入れない**。L1 の存在意義は「単純だが速い・確実」（[12:97](12-infrastructure-common.md)）。depth→CUDA→TSDF→ESDF→slice は GPU スケジューリング・CUDA context に最悪応答時間が依存し、HP60C driver は閉ソース `.so`（[02 §残課題](../shared/02-hardware-design.md)）＝障害時に修正手段が無い。**collision_monitor の observation source は `/bot{n}/scan` + `/bot{n}/virtual_scan` のまま不変。** nvblox は planning 側（costmap）にのみ入る。

### 原則 P2: cmd_vel 経路は 1 バイトも変えない

nvblox / MOLA-LO / EKF は `cmd_vel` を publish しない。[12:526-541](12-infrastructure-common.md) の挿入トポロジ（controller→`cmd_vel/nav2_raw`→collision_monitor→`cmd_vel/nav2`(prio10)→twist_mux←`cmd_vel/emergency`(prio100)）と **twist_mux emergency prio100 = FROZEN safety contract** は無傷。影響は「costmap のセル値」と「TF の品質」に閉じる。

## 2. Nav2 構成（TARGET）と採否

```
Nav2 (per-bot, namespace /bot{n})
├── BT Navigator
├── Planner Server ── GridBased (NavfnPlanner)          ← CURRENT 維持（§2-3）
├── Controller Server ── FollowPath (MPPI)              ← CURRENT 維持（nav2_params.yaml:115）
├── Behavior Server ── spin / backup / drive_on_heading / wait
├── Waypoint Follower
├── Collision Monitor ── scan + virtual_scan のみ       ← GPU 非依存（原則 P1）
└── Costmap2D
    ├── Global Costmap  (global_frame: map, resolution 0.01)
    │   ├── Static Layer   … /map（既知構造。decay しない）
    │   ├── Nvblox Layer   … nvblox ESDF slice ★ NEW（T4 で追加。§5-2 移行順）
    │   ├── Obstacle Layer … scan + virtual_scan
    │   └── Inflation Layer
    └── Local Costmap   (rolling 3x3m, resolution 0.01)
        ├── Nvblox Layer   … ★ NEW（local こそ主戦場。T3）
        ├── Obstacle Layer … scan + virtual_scan
        └── Inflation Layer
```

採否の整理（構成候補に挙がったが**採らない**もの）:

| 候補 | 判定 | 理由 |
|---|---|---|
| **Voxel Layer** | **不採用**（retreat plan として保持） | nvblox と同機能の CPU 版。両方載せると同一 depth から 2 系統の 3D 表現＝8GB ユニファイドメモリ（[06:100](06-implementation-phases.md)）の二重消費・チューニング面が倍。**nvblox が S1 で落ちた場合の縮退先**としてのみ採用する |
| **SmacPlannerHybrid** | **不採用** | 非ホロノミック曲率制約のプランナ。M1 はメカナムでその場回転可能＝曲率制約が本質でない。`tolerance`/`xy_goal_tolerance` の協調（`nav2_params.yaml:100-110,301-304`）は #125/#67 の live 実証値であり、プランナ交換はこれを壊す。**nvblox 統合とプランナ交換は独立の決定＝同一スライスで混ぜない** |
| **Local Costmap の Static Layer** | 不採用 | rolling window に static は無意味（CURRENT も持たない） |
| **nvblox dynamic 層** | **不採用** | dynamic 分離は people segmentation マスク前提（**要外部裏取り**）。ジオラマに人はいない。動く物体＝相手ロボットは既存 **VirtualScan 契約**（[12:547](12-infrastructure-common.md) dual-consumer）が担当し depth 観測より正確。static TSDF only ＝ segmentation モデル分の GPU メモリを丸ごと節約（S1 に直接効く） |

**Static Layer と Nvblox Layer の役割分離**: Static = 設計上の既知構造（周壁・棚・隘路壁。decay しない）／Nvblox = 観測された 3D 障害物の 2D 投影（LiDAR 面に映らない棚の張り出し・低い荷物・段差＝[02 §決定（2026-08-05: Superior 版 + HP60C 要件化）](../shared/02-hardware-design.md) が要件化済み。観測で消える）。混ぜると「既知の壁が観測されないから消える」事故が起きる。`track_unknown_space: true`（`nav2_params.yaml:258`）下で Nvblox Layer が未観測領域を `NO_INFORMATION` で塗ると Static の FREE を潰しうるため、**Nvblox Layer は marking 方向のみ（cost を上げる方向にのみ寄与）を既定**とする（plugin 実装の `updateCosts` が overwrite か max かは要外部裏取り＝§8 OQ-8）。

## 3. データフロー（TARGET）

```
┌─ 知覚（新規・GPU）──────────────────────────────────────────────┐
│ Nuwa HP60C (USB, ascamera driver・閉ソース .so)                 │
│   ├─ /bot{n}/camera/depth/image_raw   (sensor_msgs/Image)       │
│   ├─ /bot{n}/camera/depth/camera_info (sensor_msgs/CameraInfo)  │
│   └─ /bot{n}/camera/color/image_raw   (任意・既定 OFF)          │
│             │        TF: map→odom→base_link→camera_link ★NEW    │
│             ▼                                                   │
│   nvblox_node (Isaac ROS 3.x, CUDA)  ← TF を「配信しない」      │
│     static TSDF/ESDF → 2D slice      （pose は TF に完全従属）  │
│             │ /bot{n}/nvblox/map_slice                          │
└─────────────┼───────────────────────────────────────────────────┘
              ▼
  /map ──▶ [Static][Nvblox][Obstacle]◀── /bot{n}/scan + /bot{n}/virtual_scan
                    └──┬──┘
                 [Inflation Layer]
              Global / Local Costmap
                       │
        Planner(Navfn) → Controller(MPPI 20Hz)
                       │ /bot{n}/cmd_vel/nav2_raw
        collision_monitor（scan+virtual_scan・GPU 非依存）
                       │ /bot{n}/cmd_vel/nav2 (prio10)
        twist_mux ◀── /bot{n}/cmd_vel/emergency (prio100・FROZEN)
                       │ /bot{n}/cmd_vel
        L0' ホストシリアルドライバ送信直前クランプ ≤0.3 m/s
                       ▼
        STM32（メカナム逆運動学）→ モータ
```

**nvblox は AMCL の pose 品質に完全従属する**（TF 経由で pose を取る）。AMCL がロストした状態で depth を積分すると誤位置の障害物が TSDF に**焼き付く**（LiDAR obstacle_layer は rolling で自然回復するが TSDF は積分型）。pose_stale（[12 §freshness guard](12-infrastructure-common.md):506-513・既定 1.0s）時に nvblox の integration を止める機構の要否は §8 OQ-6。

**参考実装の「Nav2 起動 8s 遅延」は持ち込まない**: 本プロジェクトは共有 map_server が transient_local で `/map` を即配信する（`nav2_params.yaml:262`）ため不要。bringup は self-sequencing 原則（TimerAction 不使用）を維持する。

## 4. トピック契約

新規（[03 §トピック契約](03-software-architecture.md) へ land 時に追記する）:

| トピック | 型 | Pub→Sub | 備考 |
|---|---|---|---|
| `/bot{n}/camera/depth/image_raw` | `sensor_msgs/Image` | ascamera→nvblox | encoding（`16UC1` mm / `32FC1` m）は **S2 で実機確定**（単位取り違えは致命的） |
| `/bot{n}/camera/depth/camera_info` | `sensor_msgs/CameraInfo` | ascamera→nvblox | depth と同 frame_id・同期タイムスタンプが nvblox の前提 |
| `/bot{n}/camera/color/image_raw` | `sensor_msgs/Image` | ascamera→(任意) | Mode X-ER 実カメラ入力（[02 §決定（2026-08-05: Superior 版 + HP60C 要件化）](../shared/02-hardware-design.md)）。既定 OFF |
| `/bot{n}/nvblox/map_slice` | `nvblox_msgs/DistanceMapSlice` 等 | nvblox→Nvblox Layer | **`/map` と名前を分ける**（共有 map_server の `/map` 単独 publisher 契約を守る） |
| `/bot{n}/odometry/filtered` | `nav_msgs/Odometry` | ekf_node→(診断) | EKF 出力（§5） |
| TF `base_link → camera_link` | static | robot_state_publisher | ✅ **`camera_link` は contract PR で `FROZEN_LINK_NAMES` に landed**（名前のみ凍結。URDF の body/joint は取付実測待ち＝`PENDING_URDF_LINKS`。光学 frame は未凍結＝OQ-4/OQ-7） |

既存契約で**不変を明示するもの**: `/map`（共有 map_server 単独 publisher。nvblox に出させない）・`/bot{n}/scan`・`/bot{n}/virtual_scan`（dual-consumer 維持）・`/bot{n}/cmd_vel*` 一式（原則 P2）・`/bot{n}/amcl_pose`（Guardian の freshness guard 入力。§5-4）。

## 5. 自己位置推定（Localization / State Estimation）

現行 docs には `robot_localization` / EKF の記述が無く、**実機側の `odom → base_link` 配信者が未定義**（[07 T6](../shared/07-research-notes.md):85 が「TF ツリー未文書化」を残件化）。本節がその回答となる。

### 5-1. 分類表（採否）

| 分類 | 技術 | 採否 | 根拠 |
|---|---|---|---|
| ローカル Odometry | Wheel Odometry（STM32 auto-report 40ms/25Hz） | **採用（CURRENT）** | [02 §残課題5](../shared/02-hardware-design.md) |
| ローカル Odometry | IMU（ICM20948 9軸・拡張ボード実装） | **採用（CURRENT）** | [02 §確認済み表](../shared/02-hardware-design.md) |
| ローカル Odometry | cuVSLAM（Visual Odometry） | **blocked-by-hardware**（2026-08-17・→ 末尾 B-1） | HP60C がステレオ IR を出さず Isaac ROS 3.2 は RGB-D 非対応＝§8 OQ-1 を negative で解決 |
| グローバル | 2D LiDAR SLAM（SLAM Toolbox・地図生成のみ） | **採用（CURRENT）** | [09:111-115](../shared/09-navigation-internals.md) |
| グローバル | AMCL | **採用（CURRENT）** | [09:45-71](../shared/09-navigation-internals.md) |
| グローバル | GNSS / RTK-GNSS | **対象外** | 屋内設置・RTK 実用精度（cm オーダ）は resolution 0.01m の地図でセル数個分＝通路幅 200-280mm の隘路では分解能不足・`KNOWN_LOCATIONS` はジオラマローカル座標系で UTM 接続不要・予算外。**分類表には理由付きで残す**（実倉庫スケール一般化の説明に要る） |
| Sensor Fusion | robot_localization（EKF） | **採用（CURRENT で `odom→base_link` の唯一の配信者）** | 新規レイヤ（既存 docs に記述なし） |
| ローカル Odometry | 2D LiDAR odometry（**MOLA-LO**・車載 T-mini Plus の `/scan`） | **採用（TARGET-1・2026-08-17 ユーザー決定）** | → 末尾 B-2。真の ICP covariance ＋ `pose_quality` を出す唯一の候補で、§5-4 の弱点（旋回スリップ＝yaw）に直答する |

### 5-2. TF フレームツリーと配信責任（単騎でも namespace `bot1/` は維持）

凍結フレーム名の単一ソースは `robot_dimensions.py:17-27`。1台構成でも `bot1/` namespace を維持する（`nav2_params.yaml` の namespace 置換・State Cache の per-bot 構造・凍結契約への波及を避けるため＝本 doc の設計判断。単騎構成の前提は [ADR-0006](../adr/0006-single-bot-first.md)）。

```
map ───────────── AMCL (nav2_amcl, tf_broadcast: true = nav2_params.yaml:59)
 │                ※ SLAM Toolbox（地図生成時）とは同時起動しない
 ▼
bot1/odom ─────── robot_localization ekf_node（★ 唯一の odom→base_link 配信者）
 │                入力: odom0=/bot1/odom(wheel), imu0=/bot1/imu
 │                TARGET-1 追加: odom1=MOLA-LO（旧 cuVSLAM・TF は出させない）
 ▼
bot1/base_link ── robot_state_publisher（URDF static）
 ├── bot1/lidar_link（凍結）
 ├── bot1/imu_link（凍結）
 └── bot1/camera_link ★ NEW（contract PR landed。名前のみ凍結・URDF は実測待ち。§4）
```

**TF 配信責任の一意化ルール（本節の中核契約）**: `map→odom` は AMCL（または SLAM Toolbox）**1ノードだけ**。`odom→base_link` は ekf_node **1個だけ**（M1 ドライバに odom TF を出させない＝二重配信の温床。sim 側の既存経路との調停含む）。`base_link→sensor_*` は robot_state_publisher のみ。**launch レベルで排他化し、unit テストで pin する。**

最初から EKF を TF 所有者に置く理由: [09 Phase 2c](../shared/09-navigation-internals.md):117-120 の「IMU 融合（余裕があれば）」を後付けにせず融合器を最初から通しておくと、TARGET-1（MOLA-LO 追加。旧 cuVSLAM）で**入力を1本足すだけ**で済み TF 所有者が変わらない。

### 5-3. 段階的採用パス

| 段 | 構成 | map→odom | odom→base_link | ゲート |
|---|---|---|---|---|
| **CURRENT** | wheel + IMU を EKF 融合 ＋ SLAM Toolbox（地図）/ AMCL（実行時） | AMCL | ekf_node | M1 到着・自前 `m1_driver` で `/odom` `/imu` が出ること。V1-V8 |
| **TARGET-1** | ＋ cuVSLAM を **EKF の第2 odometry 入力**に追加（cuVSLAM は TF を出さない） | AMCL（不変） | ekf_node（不変） | §8 OQ-1 の外部裏取り。V9/V10 |
| **TARGET-alt** | 参考実装風: VSLAM 内部補正済み姿勢を odom とし **AMCL 省略** | 静的（校正） | VSLAM/ekf | **既定パスにしない**（下記） |

**TARGET-1 で cuVSLAM に TF を出させず EKF 入力にする理由**: TF 単一所有が保たれ、VSLAM がロストしても wheel+IMU で縮退継続（冗長化）。VSLAM のループクロージャ補正が EKF にジャンプとして入る問題は `differential` 設定で扱う（実装時検討）。

**TARGET-alt を既定にしない理由（2つの blocker）**: ① **Emergency Guardian の pose freshness guard が `/amcl_pose` を直接購読**しており（[12:506-513](12-infrastructure-common.md)）、AMCL を消すとこの安全ガードが恒久沈黙する（代替 pose 源への差し替えは safety-state トラック所有の契約変更）。② VSLAM の odom 原点は起動時姿勢＝毎ブートで `map.pgm`（原点 [0,0,0]・[09:318-331](../shared/09-navigation-internals.md)）とズレ、絶対座標の `KNOWN_LOCATIONS` 9 キーが即タスク失敗になる。TARGET-1 完了後に V10 実測で予算が浮くと示せた場合のみ再評価（その際も blocker ② は残る＝【2026-08-10 追補】A-1）。

> **【2026-08-17】本節 5-3 の cuVSLAM 前提は superseded**: OQ-1 が negative で解決し（HP60C にステレオ IR が無く Isaac ROS 3.2 は RGB-D 非対応＝末尾 B-1）、**TARGET-1 の中身は MOLA-LO（2D LiDAR odometry）へ差し替わった**（末尾 B-2・ユーザー決定 2026-08-17）。上表 TARGET-1 行と直前段落の cuVSLAM 記述（`differential` でループクロージャのジャンプを扱う、等）は**末尾 B-2/B-4/B-9 を正として読み替える**（B-9 は当該記述が前提から誤りであった点も記録する）。TF 単一所有・EKF 第2入力・冗長縮退という**構造は不変**で、入れ替わったのは第2入力の実体だけである。

### 5-4. メカナム由来の劣化と covariance 設計

メカナム逆運動学は STM32 側（[02 §残課題6](../shared/02-hardware-design.md)）で、既定は `linear.y = 0` 固定＝劣化は旋回スリップに限定。EKF の `odom0_config` は位置でなく**速度（vx, vy）**を採り、yaw はエンコーダより IMU を信頼する（旋回スリップはヨー角を直撃）。omni 化（`linear.y ≠ 0`）する場合は AMCL の `robot_model_type`（`nav2_params.yaml:52` = Differential）切替と C-8 ベクトル速度クランプが前提条件として連鎖するため、**localization と safety を同一 PR で扱うことを要件化**する。

### 5-5. 固定 RPLiDAR A1 の役割変更: 補正入力 → ground truth 取得装置（**提案**・doc09 所有トラック承認待ち）

現行の「外部トラッキング補正（オプション）」（[09:86-91](../shared/09-navigation-internals.md)）から、**オフラインの ground truth 取得装置へ付け替えることを提案する**（doc09 本文の改訂は nav トラック所有＝本 doc からは提案に留める）。理由: 常時補正には固定 LiDAR 点群→ロボット姿勢の推定器を自作する必要があり、1台構成では AMCL + EKF で足り工数に見合わない。一方 §6 の受け入れ条件を測るには**推定系と独立した外部観測**が必須（AMCL 出力で AMCL を評価できない）。TF ツリーには入れず、別プロセスで rosbag 記録しオフラインで突き合わせる。2台復帰時に補完用途を再評価。

## 6. 検証（受け入れ条件・ミニチュアスケール暫定値）

前提: 走行領域 1,820×910mm・resolution 0.01m・最大速度 0.3 m/s・M1 外接円半径 ≈184mm。数値は**暫定・実測で確定**。ground truth は §5-5 の固定 RPLiDAR A1 か俯瞰カメラ+マーカ（推定系と独立のもの）から取る。

| # | 試験 | 受け入れ条件（暫定） |
|---|---|---|
| V1 | 静止ドリフト（5分静置） | 位置 < 10mm・yaw < 1.0° |
| V2 | ZUPT 有無比較 | ZUPT 有効時に V1 が有意に減少（robot_localization に標準機能は無い想定→静止判定→0速度 Odometry 注入の薄いノード。§8 OQ-5） |
| V3 | 直進 odom スケール（1.5m×5往復） | 実測比誤差 < 2% |
| V4 | 旋回スリップ（360°×5周） | wheel 単独 yaw 誤差 < 15°・EKF 後 < 5° |
| V5 | ループ一周復帰誤差 | map 系で位置 < 30mm・yaw < 5°（Nav2 の `xy_goal_tolerance` より厳しいこと） |
| V6 | AMCL 収束 | 初期シードから < 5s |
| V7 | pose 鮮度ガード非誤発火（通常走行10分） | `pose_stale` estop 0 件 |
| V8 | localization ロスト縮退（/scan 遮蔽） | 1.0-1.5s 以内に `pose_stale` estop → 遮蔽解除で自動復帰 |
| V9 | （TARGET-1）反復テクスチャ耐性 | 同一形状棚の通路往復で VSLAM ロスト時も EKF 全体が飛ばない（wheel+IMU 縮退継続）。**【2026-08-17】MOLA 置換後は「MOLA の ICP 縮退時も EKF が飛ばない」へ読み替え（`pose_quality` を併記録）** |
| V10 | （TARGET-1）メモリ/レイテンシ予算 | 全スタック同時起動で残 RAM ≥ 500MB（R-38 と同基準）・swap 無し・controller 20Hz 維持 |
| V11a | **【2026-08-17 追加】MOLA-LO 単独** | 小空間パラメータで単独 dead-reckoning・`pose_quality` logging・6Hz vs 12Hz 比較（→ 末尾 B-5） |
| V11b | **【2026-08-17 追加】MOLA-LO EKF 融合** | `differential:false`・`odom1_pose_rejection_threshold` 込みで V5 相当ループ・covariance の実測 seed（→ 末尾 B-5） |

## 7. スパイクゲート

### S1 — Orin Nano Super 8GB で nvblox が成立するか（Go/No-Go）

参考実装の「10-12GB / 16GB」を丸ごと持ち込まない。内訳を分解すると、大半は CUDA ランタイム＋ZED SDK（Neural depth）＋people segmentation の**固定コスト**であり、本構成は後2者を最初から持たない（HP60C はハードウェア深度・dynamic 層不採用）。ジオラマ 1.8×0.9×0.3m の TSDF は voxel 0.01m でも概算 ~486k voxel ≈ **数 MB〜10MB 台**（概算。実ブロック確保単位は要外部裏取り）。**S1 の目的は固定コスト（CUDA context 等）の実測**。

- ダイエット案（優先順）: dynamic 層 OFF（決定済）→ mesh 生成 OFF（撮影時のみ ON）→ map 範囲をジオラマ実寸に制限 → color integration OFF → ヘッドレス起動。
- **voxel_size 推奨 0.02m**（初期値）: 隘路 ≈280mm で 14 voxel・T-mini Plus/MS200 系の測距誤差 ±1-3cm（R-41）と同等。0.05m は壁位置 ±25mm の設計誤差を持ち込むため不採用。0.01m はメモリ可だが ESDF GPU 負荷 8 倍＝まず 0.02 で測る。
- 合格ライン: 残 RAM ≥ 500MB **かつ Open-RMF（Mode C）分の余地が残ること**（§8 OQ-2）。`tegrastats` 10分負荷で throttle 無し。
- 測定順: idle → nvblox 単体 → +Nav2 → +Hermes/MCP（**各段の差分**を記録）。

### S2 — HP60C（ascamera）→ nvblox 入力互換性

| 項目 | 判定 |
|---|---|
| Humble / aarch64 / JetPack 6.x で ascamera がビルド・起動（2017 GCC5.4 閉ソース .so） | Go/No-Go |
| depth encoding（16UC1/32FC1）と depth_scale | 設定値確定 |
| camera_info が depth と同 frame_id・同期 stamp | Go/No-Go |
| **最小測距距離（min range）** — 0.3m 級だと 280mm 隘路で常時無効＝nvblox が何も見えない | **Go/No-Go（最重要・要外部裏取り。リポジトリ内に一次情報なし）** |
| FOV・解像度・fps（棚の張り出しが視野に入る取付高さか） | 取付設計・要外部裏取り |
| rclcpp_components 登録の有無（Component Container 同居可否。無ければ depth が DDS シリアライズ経由＝CPU 消費増） | 性能設計 |

retreat plan: RealSense / Orbbec への置換（[ADR-0008 §却下](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md) が記録済み）。

### S3 — sim での nvblox 検証可否 → **場を分ける**

Mac に CUDA 無し（[jetson/01 F4](../jetson/01-fidelity-and-validation.md):55）・Gazebo は Humble ペア未決（ADR-0008 Open）・Isaac Sim は Phase 5 のカット可能オプション、の3重障壁により **sim での nvblox 検証は構造的に不可に近い**。方針: **「sim（Mac/Gazebo）は CURRENT（2D costmap）の回帰を守る場、nvblox は Jetson + rosbag 再生の場」と分離する**。実機で HP60C depth を rosbag に録り、以後の回帰は rosbag→nvblox で回す（録画に実機が要るのは 1 回だけ）。`nav2_params.yaml` に Nvblox Layer を「plugins 未登録の状態で」記述しておく T0 設計により、sim CI は CURRENT のまま緑を維持できる。

### 移行順（前段が緑でないと進めない）

T0 土台（camera_link contract PR・plugins 未登録記述・回帰ゼロ）→ T1=S2 → T2=S1 → T3 **Local Costmap のみ**結合（Global 不変＝#67 の絶対 `/map` 教訓に触れない）→ T4 Global 追加（OQ-8 確認後）→（2台復帰フェーズ）T5 2台同時。**T3 で止める判断も正当**（[02 §決定（2026-08-05: Superior 版 + HP60C 要件化）](../shared/02-hardware-design.md) の要件は Local だけで達成でき、0.3 m/s に対し rolling 3m は約 10 秒先まで見える）。

## 8. OPEN QUESTIONS（未決・要裏取り）

1. **cuVSLAM の入力要件（最重要・要外部裏取り）**: ステレオ（左右 rectified + camera_info）必須か、RGB-D 単眼で成立するか。**リポジトリ内一次情報ゼロ**。HP60C が左右 IR 画像を publish できるかも未確認（`ascamera` の実トピックは実機で `ros2 topic list` が最速）。満たせない場合の代替は RGB-D SLAM 系（RTAB-Map 等・例示に留め採否は決めない）。
2. **nvblox と Open-RMF（Mode C）の 8GB 食い合い**: R-38 の段階1（Mac Docker 6GB）は Open-RMF 実体も GPU も未搭載。両方は載らない可能性があり、その場合「Mode C を諦めるか nvblox を諦めるか」は**主方針に関わるユーザー判断**。S1 測定順に Open-RMF を含める。
3. **M1 外接円半径 184mm vs 通路 280mm（nvblox 以前の既存矛盾・優先度高）**: 円形 footprint（直径 368mm）では通路に入れない。非円形 footprint polygon への移行（`consider_footprint` 再有効化・`nav2_params.yaml:171-179` の #67 教訓に注意）が事実上必須で、**nvblox 統合より先**。担当トラックの確定が要る。
4. **Yahboom 工場イメージの ekf 設定の実体**: [ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md) の一行のみが根拠。`robot_localization` の ekf.yaml か独自実装か、実機到着後に確認するまで「流用できる」を前提にしない。
5. **ZUPT の実装場所と所有トラック**: 静止判定→0速度 Odometry 注入ノードの新設（robot_localization 標準機能に無い想定・要確認）。
6. **pose_stale 時の nvblox integration 停止**: TSDF 汚染（§3）への対策として Guardian→nvblox の信号が要るか。Guardian を policy 層に留める方針（#126）との整合は所有トラック判断。
7. **`camera_link` contract PR の切り方**（**解決済**: camera_link の**名前のみ**を単独先行で land し、C-1（ROBOT_RADIUS 改訂・OQ-3 依存）は分離した。URDF body/joint と光学 frame 名は実測・OQ-4 待ちで据え置き）。
8. **nvblox costmap plugin の `NO_INFORMATION` 書き込み挙動**（overwrite か max か・要外部裏取り）。overwrite なら Global 追加（T4）は不可＝T3 止めが現実解。
9. **車載 LiDAR 型番の既存ドリフト**: M1 は T-mini Plus（[02 §確認済み表](../shared/02-hardware-design.md)）だが doc03/doc09/`nav2_params.yaml:55`（`laser_max_range: 12.0 # MS200`）は MS200 のまま。AMCL のスキャンパラメータは LiDAR 依存＝V 系試験の前に反映が要る。
10. **EKF 周波数と STM32 auto-report 25Hz の整合**（EKF `frequency` は 25Hz 近傍に合わせるのが妥当か、実測で決める）。
11. **駐機時の `pose_stale` 誤発火** → 本 doc 末尾【2026-08-10 追補】A-10（OQ-11）参照。
12. **OQ-1 は解決済み（negative）／TARGET-1 の中身は MOLA-LO へ** → 本 doc 末尾【2026-08-17 追補】B-1（cuVSLAM の二重 blocker）・B-2（差し替え先）参照。**上記 1. の本文は履歴として残す**（結論は B-1 が正）。

## References

- [shared/09-navigation-internals.md](../shared/09-navigation-internals.md)（CURRENT 正本・TF ツリー :259-273・Phase 2 実装順 :117-120）
- [architecture/12-infrastructure-common.md](12-infrastructure-common.md)（安全レイヤー :72-93,103-110・cmd_vel トポロジ :526-547・freshness guard :506-513）
- [shared/02-hardware-design.md](../shared/02-hardware-design.md)（M1 実寸・HP60C・T-mini Plus・L0'・§ROSMASTER M1 採用検討時の残課題）
- [shared/04-diorama-layout.md](../shared/04-diorama-layout.md)（通路幅 M1 試算）
- [shared/07-research-notes.md](../shared/07-research-notes.md)（T6 :85・R-38 :243・R-41 :251）
- [architecture/06-implementation-phases.md](06-implementation-phases.md)（8GB ユニファイドメモリ :93-100）
- [jetson/01-fidelity-and-validation.md](../jetson/01-fidelity-and-validation.md)（F4 :55・G4 :103）
- [ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md) / [ADR-0006](../adr/0006-single-bot-first.md)
- [docs/GLOSSARY.md §11](../GLOSSARY.md)（nvblox / TSDF・ESDF / cuVSLAM / EKF の正準用語）
- HTML 図解 companion: [perception-localization-flow.html](perception-localization-flow.html)（本 doc のデータフロー・costmap 層・TF ツリー・移行段・Localization Health 監視〔【2026-08-10 追補】〕の図式化。正本は本 md）
- 全体地図: [robot-architecture-tree.html](robot-architecture-tree.html)（01-08 機能 Tree。本 doc の射程は 01-05。06=doc12/02-hardware・07=productization/05,07・08=mode-x-er/09 が各正本）
- `ws/src/warehouse_bringup/config/nav2_params.yaml`（:52,59,100-110,115,171-179,214-216,256-262,300）
- `ws/src/warehouse_description/warehouse_description/robot_dimensions.py`（:16-24,33 凍結フレーム）
- 参考実装: [ZED2i + nvblox + Nav2 事例（Qiita motoms）](https://qiita.com/motoms/items/b87d08448cdaddb24c35) — 参照日: 2026-08-07（数値は本プロジェクトに直接適用しない）

---

## 【2026-08-09 追補】ジェスチャ司令（ADR-0007）との接続

[ADR-0007](../adr/0007-no-overhead-camera-gesture-via-onboard-nn.md)（俯瞰カメラ不使用・搭載 HP60C + ローカル骨格 NN でジェスチャ認識）に伴う本 doc への影響:

- **§2 の「nvblox dynamic 層 不採用」の理由を言い換える**: 「ジオラマに人はいない」→「**走行面上に動的障害物が無い**」。ジェスチャ導入で人は（盤外に）存在するが、走行面上の障害物にはならないため**不採用の結論は不変**。人が搭載カメラ視野に入ることで static TSDF に焼き付く懸念は、人物 bbox 領域の depth マスク（骨格 NN の bbox 流用・追加モデル不要）を第一候補として実装時に検討する。
- **§7 S1 の測定順に `+gesture NN` を 1 段追加**: `idle → nvblox 単体 → +Nav2 → +gesture NN → +Hermes/MCP`。骨格 NN（第1候補 MediaPipe = CPU 推論）は GPU/VRAM を食わない選定だが、RAM/CPU の差分は測る。
- **カメラ取付角の競合（新規 OQ）**: nvblox は下向き寄り（棚の張り出し・低い荷物）・ジェスチャは水平〜上向き寄り。**Phase 1 は水平固定**で妥協し、最終値は S2 実測と同一セッションで決める（[mode-x-er/09 §3](../mode-x-er/09-hand-raise-summon.md)）。パン/チルト雲台は camera_link の static TF 契約（§5-2）を破るため不採用。
- §6 V 系の ground truth 候補「俯瞰カメラ+マーカ」は**計測装置**であり、ADR-0007 の射程（ER/知覚入力）の外＝選択肢として残る。
- 新トピック `/perception/gesture_events` と新ノード `gesture_detector`（L4 知覚・publish-only）の設計正本は [mode-x-er/09](../mode-x-er/09-hand-raise-summon.md)。cmd_vel 経路・L1 反射（原則 P1/P2）への影響はゼロ。

---

## 【2026-08-10 追補】Localization Health 監視と入れ替え方針（Guardian 監視プロファイル・TARGET）

Status: **TARGET 設計の記録のみ**（実装なし）。CURRENT の `warehouse_safety`（`emergency_guardian.py` / `guard_logic.py`）と `config/warehouse.base.yaml` は本追補では**変更しない**。本追補は §5・§8 の既存 §番号を改めず、末尾に足す（[#165 教訓](../dev/03-retrospectives.md)）。

### A-1. 前提: 本プロジェクトは実験段階である

現フェーズは単騎 ROSMASTER M1 構成（[ADR-0006](../adr/0006-single-bot-first.md)）での**商品化前の実装可能性検証**である。したがって localization・監視・知覚のコンポーネント選定は「一度決めて凍結する」のではなく、**精度が良い方を採る（accuracy-first）**方針で、実験を高速に回して都度入れ替える。§5-1 の分類表が AMCL / cuVSLAM / EKF を並べて採否を段階化しているのは、この入れ替え前提の表現である。

**Guardian の pose 監視は、この入れ替えを妨げない設計にする**——これが本追補の中心命題である。CURRENT の Guardian は購読先が `f"/{bot}/amcl_pose"` にハードコードされており（`ws/src/warehouse_safety/warehouse_safety/emergency_guardian.py:99`）、localization を差し替えると安全ガードが恒久沈黙する（§5-3 TARGET-alt を既定にしない blocker ① と同根）。攻めの姿勢を安全に成立させるには、この結合を config へ逃がす必要がある。なお、本追補が blocker ① を解いても **§5-3 TARGET-alt（AMCL 省略）の不採用は変わらない**——blocker ②（VSLAM odom 原点が毎ブートで `map.pgm` 原点とズレ、絶対座標の `KNOWN_LOCATIONS` 9 キーが即失敗する）は監視プロファイルでは解けない。

### A-2. 不変の床（安全ストッパー）— 攻めてよい範囲の境界

攻めが許されるのは**安全層の外側に限る**。以下は **pose 非依存の物理安全チェーン**であり、localization がどれだけ間違っていても機能する。**入れ替え・実験の対象外＝凍結維持**とする。

| 段 | 実体 | pose 依存 | 位置づけ |
|---|---|---|---|
| L1 反射 | `nav2_collision_monitor`（observation source = `/bot{n}/scan` ＋ `/bot{n}/virtual_scan`） | **なし**（生センサ） | 原則 P1（§1）で GPU 非依存を明記済。observation source は不変 |
| L1 調停 | `twist_mux` ← `/bot{n}/cmd_vel/emergency` prio100 | **なし** | **FROZEN safety contract**（[12:526-541](12-infrastructure-common.md) / 原則 P2） |
| L0' | ホストシリアルドライバ送信直前クランプ ≤0.3 m/s | **なし** | [02 §決定（2026-08-05: Superior 版 + HP60C 要件化）](../shared/02-hardware-design.md)・凍結値は `warehouse_interfaces/safety.py` の `MAX_LINEAR_VELOCITY` |

この3段が「不変の床」である。**床の上（localization 手法・知覚コンポーネント）は accuracy-first で高速に入れ替えてよい**が、床自体を実験対象にしてはならない（[.claude/rules/safety.md](../../.claude/rules/safety.md)）。ただし **Guardian（pose 監視ロジック）は pose 依存だが L1 安全機構（§1 のレイヤ表）であり、「自由に入れ替える」対象ではない**——単騎化でも安全レイヤは1枚も減らさない（[ADR-0006 Decision 5](../adr/0006-single-bot-first.md)）。Guardian の購読先・判定の変更は A-9 の手順（R-26 ＋ safety-state トラック Issue）に従う。床の唯一の例外は**保証を維持するための床の強化**（例: omni 化の前提となる C-8 ベクトル速度クランプ＝§5-4 / [02 §C-8](../shared/02-hardware-design.md)）で、撤去でなく強化ゆえ許されるが **contract PR ＋ R-26** を必ず経る（§5-4 の「localization と safety を同一 PR」要件はこの C-5/C-8 連鎖に限る）。逆に言えば、床と Guardian があるからこそ localization 側で攻めた実験ができる。

### A-3. 業界標準との同型性: localization ロスト = operational stop であって protective stop ではない

「床は pose 非依存・localization は床の外」という切り分けは本プロジェクト固有の発明ではなく、産業用 AMR の標準的な切り分けと同型である。

| 一次情報 | 確認できたこと |
|---|---|
| ISO 3691-4:2023（無人搬送車の安全要求）公式 preview | 安全要求条項（Clause 4）の目次に **localization を扱う条項が見当たらない**（**preview で確認できた範囲**＝ToC / Clause 3。規格全文は未確認）。安全は非接触防護装置・速度・制動側で規定される |
| MiR250 Technical Guide | localization を **navigation プロセスに分類**し、safety PLC が監視する stop に **operational stop を含めない** |
| OTTO 1500 Operating Manual | 「Lost」を **Emergency Stop とは別の独立した状態**として定義 |

すなわち **localization ロスト＝ operational stop（運用停止：走行タスクを止める）であって、protective stop（安全停止：防護装置による停止）ではない**、が業界標準の位置づけである。Guardian の `pose_stale` estop（[12:506-513](12-infrastructure-common.md)）は「protective stop の代替」ではなく **operational stop を安全側に倒した precautionary な実装**として読むべきで、protective stop の責務は上表の3段（床）が持ち続ける。用語定義は [GLOSSARY §11](../GLOSSARY.md)。

### A-4. 集約ノード（Health Aggregator）を置かない

検討して**不採用**とした案: 各 localizer の健全性を集約し統一トピック（例 `/localization/status`）を出す単一ノードを新設し、Guardian はそれだけを見る形。

不採用理由:

- **安全経路に新しい SPOF を作る**。集約ノードは非冗長な単一ノードであり、それが落ちれば Guardian は「異常なし」と「観測不能」を区別できない。
- `diagnostic_aggregator` には**未起動ノードを確実に報告できない既知バグ**があり（ros/diagnostics issue #65）、「沈黙＝正常」に見える failure mode を持つ。これは localization ロスト検出とまさに同じ穴である。
- **Guardian は仲介を挟まず直接購読する**のが既存原則（`/amcl_pose` を State Cache 経由でなく直購読＝[12:508](12-infrastructure-common.md) / [GLOSSARY §5](../GLOSSARY.md)）。集約ノードはこの原則の逆行。

代わりに採るのが次節の **Guardian 監視プロファイル**である（抽象化を「新ノード」でなく「config」で行う）。

### A-5. Guardian 監視プロファイル（TARGET・config 切替）

Guardian 自身の**購読先を config（YAML）で切替**える。設定単位は topic 名だけでは足りない——ソースごとに「何が heartbeat か」「静止中に沈黙するか」が違うため、**監視プロファイルとして束ねる**。

| # | プロファイル項目 | 何を決めるか | なぜ topic 名だけでは足りないか |
|---|---|---|---|
| ① | 監視 topic ＋ 型 | 何を購読するか | AMCL は `PoseWithCovarianceStamped`、VSLAM は `Odometry` 等で型が違う |
| ② | heartbeat 信号の選択 | 鮮度を測る対象（pose topic か TF か status topic か） | AMCL の実 heartbeat は pose ではなく TF（A-6） |
| ③ | 静止ゲートの要否 | 「静止中は沈黙が正常」を許すか | motion-gated な localizer だけに要る（A-6） |
| ④ | stale 閾値 | 何秒の沈黙を異常とするか | AMCL は秒級、cuVSLAM は 300ms 級（A-6。※cuVSLAM は現 blocked＝履歴例。MOLA 行は B-8） |
| ⑤ | **startup_timeout** | 起動後 N 秒で初回 pose が来なければ異常とする | **CURRENT の穴の根治**（下記） |
| ⑥ | localizer 自己申告フラグの有無 | status topic / diagnostics を併用するか | ソースにより有無が違う（A-7） |

さらに **`localization_profile`（例: `indoor_2d`）** を1つのキーとして持ち、**Nav2 が使う localization ソース = Guardian が監視するソース**の一致を config で保証する。両者が別々に設定できると「Nav2 は VSLAM で走り、Guardian は死んだ AMCL を見ている」という最悪の不整合が起こりうる。

**プロファイルは restrict-only（床値より締める方向のみ・fail-closed）**: ③静止ゲート・④stale 閾値・⑤startup_timeout は「緩め得るノブ」でもあるため、[ADR-0004](../adr/0004-l2-restrict-only-policy-profile.md) の裁定（凍結値を floor とし**締める/止めるのみ**・緩い profile 値は**起動拒否 = fail-closed**。同 ADR:15-16,36）を本プロファイルにもそのまま適用する。基準 floor は CURRENT の `safety.pose_freshness_timeout: 1.0`（`config/warehouse.base.yaml:21`）。**config だけでガードを実質無効化できる形**（例: stale 閾値 60s・静止ゲート常時 ON）は起動拒否で弾く——A-1 が塞ごうとしている「安全ガードの沈黙」を、profile という新しい経路で再導入しないため。floor は**その時点の base 値**とする（CURRENT は 1.0s＝暫定。doc12 の Phase-2 実測 TODO で確定した後はその確定値が floor。base 値や startup_timeout の床自体の変更は A-9 経由。config での tighten-only 前例は `policy_gate` sub-tree＝ADR-0004:36 / PR#427）。

**③静止ゲートの許容境界（restrict-only との両立条件）**: 静止ゲートは「緩めるノブ」としては認めない。許容するのは**変位ゲート形のみ**——独立ソース（wheel odom）による**前回 pose 到着時からの累積変位 > ε でゲート開**（変位は単調非減少＝ラッチ内蔵）・**odom 不明/stale/非有限は fail-closed でゲート開**・走行中（変位・速度あり）の発火時刻が CURRENT と一致する**非緩和性を R-26 unit で機械的に証明できる**こと。この3条件を満たさない静止ゲート（例: 速度のみのゲート＝estop→速度0→ゲート閉の fail-open limit cycle を作る）は採用しない。**実装順の制約**: restrict-only の起動拒否は、下記 A-10 の 999 回避策の削除（＝静止ゲート実装）が**先行**しなければ全 runbook が起動拒否になるため、必ず静止ゲートの後に入れる。

> ⑤ **startup_timeout が塞ぐ穴**: CURRENT は初回 pose 受信前（`pose_age is None`）を stale としない（`ws/src/warehouse_safety/warehouse_safety/guard_logic.py:128-145`・[12:506-513](12-infrastructure-common.md)）。これは「まだ localize していない停止中ロボットを誤 estop しない」ための正しい判断だが、**localizer が最初から一度も起動しなかった場合、免除が永遠に続いて沈黙する**。起動後 N 秒の期限を切れば「初回が来ない」も異常として捕まえられる。なお **AMCL の初回 publish は motion-gated ではない**（`set_initial_pose: true` 起動では最初の scan 到着で無条件に1回 publish する＝upstream `!first_pose_sent_` 分岐。要実測 A-10 M 系）ため、「健全なまま駐機起動した AMCL」が startup_timeout に誤検出される構造は無い。また本項の fault action は **estop でなく loud diagnostic event（非 estop・`blocked_timeout`→`recovery` と同じ low-harm クラス）を既定**とする——未 localize の停止ロボットへの estop は物理的に無意味で、L2 側は snapshot 不完全（`unknown_robot`）により dispatch を既に拒否している。

> 上記のキー名（`localization_profile` / `indoor_2d` / `startup_timeout`）は**本追補の TARGET 提案＝例示**であり、凍結契約でも既存 config キーでもない。実装時は `safety.pose_freshness_timeout`（`config/warehouse.base.yaml:21`・既定 1.0s）との関係を含め additive に設計する。

### A-6. ソース別の鮮度セマンティクス（監視プロファイルが必要な理由）

| ソース | 出力の出方 | 鮮度で loss を検出できるか | 実 heartbeat / 補助信号 | 閾値の目安 |
|---|---|---|---|---|
| **AMCL**（`/amcl_pose`・CURRENT） | **motion-gated**: `update_min_d` / `update_min_a` を超える移動時のみ resample → publish | **できない**。完全静止中は無期限沈黙（駐機で偽陽性）・かつ delocalized でも発行を続ける（**迷子でも鮮度は正常に見える**） | 毎スキャン再発行される **map→odom TF** | 秒級 ＋ **静止ゲート必須** |
| **cuVSLAM**（`isaac_ros_visual_slam`・**旧** TARGET-1 候補＝B-1 で blocked・**履歴行**） | tracking loss で **odometry / TF 出力が完全停止** | **できる**（鮮度が真の loss 検出器になる） | `/visual_slam/status` の `vo_state`（1=Success / 2=Failed）＝カメラレートの正の heartbeat・`/diagnostics` の `localized_in_exist_map` | **300ms 級に締める**（下記の惰性窓） |

- AMCL の motion-gated 挙動は upstream `nav2_amcl` の `amcl_node.cpp` で確認した。本プロジェクトの `update_min_d` は 0.05（`ws/src/warehouse_bringup/config/nav2_params.yaml:58`）。
- cuVSLAM は tracking loss 時に出力が止まることを `visual_slam_impl.cpp` で確認した。ただし **`vo_state=1`（Success）のまま IMU / 等速外挿で約 0.5〜1s 惰性追従する窓**があり（upstream 未解決 issue #148）、この間は自己申告も鮮度も「正常」に見える。ゆえに（cuVSLAM を将来復活させる場合は）odometry の鮮度閾値を 300ms 級に締め、惰性窓を跨がせない。

**この表が「監視プロファイル」を必要とする証拠**である。同じ「pose が来ない」でも、AMCL では正常（駐機）、cuVSLAM では致命（loss）を意味する。閾値だけを config 化しても足りず、②③⑥ を含めて束ねなければソースを入れ替えられない。

### A-7. 推奨形: 自己申告（速い経路）＋ watchdog（遅い経路）の二重化

Autoware 系の localization 健全性監視の議論と同様、**localizer の自己申告（status / diagnostics）を速い経路、外部 watchdog timeout を遅い経路**として二重に持つ形を推奨形とする。自己申告はノードが生きていないと来ないため単独では不十分、watchdog は検出が遅いため単独では不十分——互いの弱点を埋める。

**covariance 閾値の単独採用は避ける**。covariance は「発散後に誤った場所で再収束する（自信満々に間違う）」ケースを見逃す（community 報告）。covariance は補助シグナルに留め、終端判定を任せない。

### A-8. 上流（Nav2 / ROS 2）に「localization lost」検出器は存在しない

調査した範囲で、上流にこの穴を埋める標準機構は無い。

| 機構 | 実際に見ているもの | localization ロストを検出するか |
|---|---|---|
| `bond`（lifecycle） | プロセスの生存 | しない（delocalized なノードは生きている） |
| `collision_monitor` の `source_timeout` | **センサ**（`/scan`）の鮮度 | しない（[12:513,535,546](12-infrastructure-common.md) の通り Guardian の pose 鮮度とは別系統） |
| BT 条件ノード | goal / path / battery 等 | localization 健全性の条件ノードは無い |
| 標準パッケージ | — | この穴を埋める既製パッケージは見当たらない |

つまり Guardian の `pose_stale` 鮮度ガード（#126）は、**業界共通の空白を自作で埋めたもの**である。監視プロファイル化はその延長線上にあり、上流機能の再発明ではない。

### A-9. 実装上の制約（R-26・CURRENT 不変）

- Guardian は**安全機構**であり、改修には **R-26**（独立オラクルから期待値を取る unit ＋ mutation で赤くなること）が必須（[.claude/rules/safety.md](../../.claude/rules/safety.md) / [architecture/20 §9](20-dev-quality-and-testing.md)）。監視プロファイルは純ロジック（`guard_logic.py` 側）と rclpy 配線（`emergency_guardian.py` 側）に分けて、判定部を rclpy 非依存のまま保つ（[12:506-513](12-infrastructure-common.md) の既存方針を踏襲）。
- 本追補では **CURRENT 実装を変更しない**。`emergency_guardian.py:99` の `f"/{bot}/amcl_pose"` ハードコードと `guard_logic.py:128-145` の `pose_stale` 判定はそのまま維持する（TARGET 設計の記録のみ）。
- Guardian の編集境界は safety-state トラック所有。実装スライスはそのトラックの Issue 経由で切る（[.claude/rules/parallel-workflow.md](../../.claude/rules/parallel-workflow.md) §7.1）。

### A-10. OQ 追加（§8 には見出し番号 11 のみ追加済み・詳細は本節が正本）

- **OQ-11: 駐機時の `pose_stale` 誤発火（検証中）** — A-6 の通り AMCL は motion-gated であり、`update_min_d: 0.05`（`ws/src/warehouse_bringup/config/nav2_params.yaml:58`）未満の静止が続くと `/amcl_pose` が沈黙する。一方 Guardian の `pose_freshness_timeout` は 1.0s（`config/warehouse.base.yaml:21`）。**駐機が 1.0s を超えると `pose_stale` estop が誤発火する疑い**がある（§6 V7「通常走行10分で `pose_stale` estop 0 件」は走行中の条件であり、駐機を測っていない）。検証タスクは**未起票**（本セッションで検証候補として提案済み・Issue 番号未付与。着手時に safety-state トラックで起票する）。誤発火が確認された場合の解は A-5 ③（静止ゲート＝変位ゲート形）または ②（TF を heartbeat にする）で、閾値を緩める方向には解かない（緩和は走行中の見逃しを増やす）。

  **2026-08-10 追記（設計調査の結果・OQ-11 は sim では実質確認済みへ昇格）**:
  1. **fail-open 経路が既に存在する**: sim / live の全実行経路が回避策 `WAREHOUSE__SAFETY__POSE_FRESHNESS_TIMEOUT=999` を注入しており（`tests/e2e/README.md:88`・`deploy/dev/README.md:67`・`deploy/dev/run-mode-a-live.sh:282`・`scripts/slice3_live_precheck.sh:455`・`docs/dev/05-session-handoff.md:29,82`＝**5ファイル6箇所**。ほか `.claude/local-memory.md` のメモ2箇所も削除時に更新）、**freshness guard は現状事実上無効**。この回避策の存在自体が OQ-11 の sim 側の証拠（実機は未測）。第1実装スライスの受け入れ条件は **999 回避策の全削除（5ファイル6箇所）＋ 既定 1.0s のまま sim full-stack 完走**とする。
  2. **誤発火は自己ラッチする**: estop が運動を禁じ、運動が無いと AMCL は pose を出さず、回復証拠が構造的に得られない（doc12:511「pose 鮮度が回復すれば自動解除」は motion-gated ソースには成立しない。doc12 側の当該記述の訂正は実装スライスで行う）。
  3. **L2 Policy Gate は防衛線にならない**: L2 の 0.5s/2.0s は `StateSnapshot` の**書込 age**であり pose の**到着 age**ではない（State Cache が 100ms 毎に timestamp を更新するため、AMCL が死んでも snapshot は新鮮に見える）。**駐機・走行中の pose 途絶に対する防衛線は Guardian のみ**（未 localize だけは snapshot 不完全→`unknown_robot` で L2 が止める）。

  **2026-08-17 追記（第1実装スライス着地・OQ-11 は「実装で塞いだ／sim 完走は未検証」へ）**: A-5③ の変位ゲートを `warehouse_safety` に実装し、上記 ① の **999 回避策を 5ファイル6箇所すべて削除**した（`.claude/local-memory.md` の2箇所は orchestrator 所有につき別途）。したがって**上記 ① の file:line は履歴**であり、現在の各行にその export は無い。実装の CURRENT 契約は [12-infrastructure-common.md 末尾【2026-08-17 追補】「freshness guard の変位ゲート」](12-infrastructure-common.md) が正本（新 config キー4つ・fail-closed・ラッチ内蔵・非緩和性の R-26 証明）。**未達 = 受け入れ条件の後半「既定 1.0s のまま sim full-stack 完走」は human gate（Docker/Gazebo 実走）で未実施**——本スライスが主張するのは offline R-26 unit（非緩和性・駐機・クリープ・fail-closed・ラッチ・境界＋手動 mutation 10/10 redden）までで、**sim 実走での確認は保留**。② の自己ラッチは変位の単調性で構造的に解消、③（L2 は防衛線でない）は不変。

### A-11. Sources（外部一次情報・参照日 2026-08-10）

- ISO 3691-4:2023 公式 preview（Clause 4 に localization 条項なし）: <https://cdn.standards.iteh.ai/samples/83545/a3d9d057a08d4f9c8e8e87cdc947583c/ISO-3691-4-2023.pdf>
- MiR250 Technical Guide（localization = navigation プロセス・safety PLC の stop に operational stop を含めない）: <https://download.astor.com.pl/dokumentacja/Mobile%20Industrial%20Robots/Roboty%20MIR/MiR250/Podr%C4%99czniki/mir250-technical-guide-10-en.pdf>
- OTTO 1500 Operating Manual（「Lost」を Emergency Stop と別の独立状態として定義）: <https://pmsql01.perfectionmachinery.com/pisweb/OMM-000003_E.pdf>
- `diagnostic_aggregator` の未起動ノード報告バグ（ros/diagnostics issue #65）: <https://github.com/ros/diagnostics/issues/65>
- `nav2_amcl` の motion-gated publish（`amcl_node.cpp`）: <https://github.com/ros-navigation/navigation2/blob/main/nav2_amcl/src/amcl_node.cpp>
- cuVSLAM の tracking loss 時 出力停止（`visual_slam_impl.cpp`）: <https://github.com/NVIDIA-ISAAC-ROS/isaac_ros_visual_slam/blob/main/isaac_ros_visual_slam/src/impl/visual_slam_impl.cpp>
- cuVSLAM の `vo_state=1` のまま惰性追従する窓（未解決 issue #148）: <https://github.com/NVIDIA-ISAAC-ROS/isaac_ros_visual_slam/issues/148>
- localizer 自己申告 ＋ watchdog の二重化（Autoware 系）: <https://arxiv.org/pdf/2504.12813>
- covariance 単独判定の見逃し（community 報告）: <https://answers.ros.org/question/298190/>

### A-12. 関連リンク

- [architecture/12-infrastructure-common.md](12-infrastructure-common.md) — Emergency Guardian 本体（:181）・freshness guard（:506-513）・collision_monitor 委譲と cmd_vel トポロジ（:522,:526-541）。**本追補は doc12 の Guardian 設計の TARGET 拡張であり、CURRENT の正本は doc12**（doc12 末尾に本追補へのポインタあり）。
- [productization/01-commercial-box-map.md](../productization/01-commercial-box-map.md) — 監視プロファイルの商品化（将来 L1 Safety Box / L2 候補）は同 doc 末尾の「将来 box 候補」に記載。正本は本追補。
- [docs/GLOSSARY.md §11](../GLOSSARY.md) — **Guardian 監視プロファイル** / **operational stop（運用停止）** の正準定義。
- [.claude/rules/safety.md](../../.claude/rules/safety.md) / [architecture/20-dev-quality-and-testing.md §9](20-dev-quality-and-testing.md) — R-26（独立オラクル unit ＋ mutation）。

---

## 【2026-08-17 追補】TARGET-1 の中身確定: MOLA-LO 2D LiDAR odometry（cuVSLAM は blocked-by-hardware へ）

Status: **TARGET 設計の記録 + OQ-1 の解決**（実装なし。CURRENT の `nav2_params.yaml` / launch / EKF config は本追補では変更しない）。§5-1 表・§5-3 読み替え注記・§8 項目 12 の「→ 末尾 B-x」参照の実体が本節。数値・トピック名・config キーはすべて**TARGET 例示であり凍結契約ではない**。外部一次情報の参照日は 2026-08-10〜2026-08-17。

### B-1. OQ-1 解決（negative・二重 blocker）

cuVSLAM は本プロジェクトの現行ハード＋現行 distro pin では成立しない。**カメラ側とソフト側の独立した2つの blocker** が両側で閉じており、remap・ドライバ改修・パラメータでは埋まらない。

1. **HP60C 側（ステレオを出せない）**: `ascamera` ドライバの HP60C 分岐は **depth×2 + rgb×2 + points の5 publisher のみ**を生成し、IR は1本も publish しない（兄弟機種 NUWA_HP60/HP60V は単眼 IR を持つが HP60C は別 enum 分岐。redistribution ソース `CameraPublisher.cpp:227-241` で確認・実機 `ros2 topic list` の community 報告とも一致）。SDK 構造体は単数 `irImg` で**左右ペアの API 自体が存在せず**、閉ソース .so（GCC 5.4/2017）ゆえ改修不能。さらに HP60C は構造化光方式＝**投光パターンを消せない**（消すと depth が死ぬ）ため、仮に IR が取れても NVIDIA が RealSense で要求する「emitter off」を満たせない。
2. **ソフト側（Humble 用 3.2 に depth 入力経路が無い）**: ADR-0008 が pin する Isaac ROS release-3.2 の cuVSLAM ノードは `visual_slam/image_{i}` + `camera_info_{i}`（**MONO8/RGB8 の生ステレオペア**・ペア内同期 ±100µs・≥30Hz）のみを購読し、depth 処理コードが 3.x 系に存在しない。RGB-D モード（`tracking_mode: RGBD`）は **2026-02-02 の Isaac ROS 4.1 で追加＝Jazzy + Jetson Thor 専用**（Orin は 4.x でサポート外）。
3. **記録しておく抜け道（不採用・将来 option）**: standalone **PyCuVSLAM の RGBD モード**は Jetson Orin / JetPack 6.x / cp310 wheel が公式配布されており、非 ROS 経路でなら本実機で動く。ただし ROS ノード自作・covariance 出力なし・Orin Nano Super の Mono-Depth は 424×240@30fps でようやく安定、のコストを伴う。

§8 OQ-1 の本文は履歴として残し（行安定）、結論は本節が正。

### B-2. TARGET-1 の新しい中身 = MOLA-LO（ユーザー決定 2026-08-17）

`ros-humble-mola-lidar-odometry`（**Humble arm64 バイナリあり**・v3.1.0 2026-08-06・IJRR 2025）を `pipelines/lidar2d.yaml` で、**車載 T-mini Plus の `/bot{n}/scan`** を入力に採用する。選定理由（順位つき）:

1. **真の ICP covariance を publish する唯一の候補**。対比: rf2o は `nav_msgs/Odometry` の covariance を**一度も代入せず全ゼロで publish** し、robot_localization は 0 分散を ε=1e-6 に置換する仕様のため「ほぼ完璧なセンサ」として filter を乗っ取る（source 検証済みの罠）。
2. **`pose_quality`（Float32）+ 段階 diagnostics（"ICP quality degraded/critically low"）を publish** — 【2026-08-10 追補】A-7 の「自己申告の速い経路」がそのまま埋まる（他候補に無い）。
3. **CPU-side / GPU-free** — S1 の nvblox 8GB 予算と無競合。
4. **バイナリ配布** — `ydlidar_ros2_driver` / `ascamera` で経験済みの aarch64 ビルドリスクなし。
5. **yaw を最も補強する class** — §5-4 の「M1 の弱点は旋回スリップ＝yaw」に直答する。なお従来の §5-1 候補表は 2D LiDAR を「地図生成のみ」に分類し **laser odometry を odometry 候補として一度も評価していなかった**（構造的盲点）。§5-1 と §5-4 が互いを見ずに書かれていた点も本ラウンドの教訓として記録する。

却下した代替: **Kinematic-ICP**（MIT・車輪ロボット特化で思想は最適だが、EKF の TF を motion prior に要求＝フィードバックループ、かつ運動モデルが差動駆動前提でメカナム横移動を表現不可。**license-clean fallback として保持**）／ rf2o（zero-cov・ベースライン計測のみ）／ Cartographer（上流保守停止）／ scan_tools（Humble 版なし）／ KISS-ICP（2D 経路なし）／ slam_toolbox 転用（map→odom 用でジャンプ混入＝構造不適）。

⚠️ **License = GPLv3**（商用ライセンス別途）。productization トラックへ通知必須（[productization/01](../productization/01-commercial-box-map.md) 末尾に flag 済み）。

### B-3. EKF 融合設計（状態割当・多変量）

原則（robot_localization 公式）: **同一 raw ソースの派生量を二重投入しない／独立センサによる同一状態の重複観測は歓迎する**。

| 状態 | wheel (STM32 25Hz) | IMU (ICM20948) | MOLA-LO | 備考 |
|---|---|---|---|---|
| x, y | × | × | **○** | LiDAR scan matching の pose 拘束 |
| yaw | × | ×（下記） | **○** | |
| vx, vy | **○** | × | × | encoder 由来は速度のみ（x,y を併入すると同源二重投入） |
| vyaw | **○** | **○** | × | **独立2センサの「良い重複」**＝covariance 融合が EKF の本領 |

- **IMU の絶対 yaw は入れない**。実態が gyro 積分なら vyaw と同源の二重投入になる。§5-4 の「yaw はエンコーダより IMU を信頼」は **vyaw 経由で満たす**と読み替える。
- `two_d_mode: true`。EKF は 15 状態の**多変量ガウス推定**（full 状態共分散 P＝非対角に状態間相関を保持）であり、各センサ側も full R（例: MOLA の x-y-yaw 3×3 異方性）を渡せる。「どのセンサが勝つか」でなく**変数ごと・時刻ごとに Kalman gain で寄与が変わる**。

### B-4. differential / relative の裁定

**初期構成は3入力とも `differential: false` / `relative: false`。**

- 根拠: 公式ルール「絶対姿勢ソースが N 本なら N−1 本を differential に」。B-3 の割当（IMU=vyaw のみ）では**絶対姿勢ソースは MOLA 1本** → differential 不要。
- **切替条件（凍結）**: 将来 IMU の絶対 yaw を融合する場合、**同一 PR で MOLA を `differential: true` に切替**える（N−1 ルール）。
- relative 不要の根拠: MOLA は既存 map 無し起動で **identity (0,0,0) から開始**（公式仕様）＝EKF と同時起動すれば odom 原点と一致する。旧 blocker ②（起動時原点ズレ）はこの構成では発生しない。
- **anti-pattern**: differential でジャンプ（loop closure / 再初期化）を「隠す」のは誤り（Δpose/Δt が巨大速度 measurement になる）。正しい防御は `odom1_pose_rejection_threshold`（Mahalanobis ゲート）。
- **edge case（→ B-10 OQ）**: MOLA だけを実行中に再起動すると原点が現在位置にリセットされ pose が跳ぶ。rejection threshold で吸収するか EKF 併再起動とするかは実装時判断。

### B-5. 統合上の必須制約

1. **MOLA の TF 出力は `odom→base_link`・`map→odom` の両方を明示無効化**（default 両方配信。前者は ekf_node 単一所有 §5-2、後者は AMCL と衝突）。§5-2 の launch 排他 unit テストに pin する。
2. **MOLA は LiDAR 単独で走らせる**（wheel odometry 補助入力を有効化しない）。有効化すると encoder 情報が EKF へ2経路混入し、robot_localization はセンサ間クロス共分散を設定できないため filter が encoder を過信する。
3. `/scan` subscriber は **SensorDataQoS（BEST_EFFORT）必須**（RELIABLE subscriber は無受信になる）。`/scan` 消費者は AMCL・collision_monitor・costmap×2 に MOLA を加えて5本（帯域 ≈16kB/s/本＝無視可）。
4. **`/bot{n}/virtual_scan` を絶対に入力しない**（合成障害物＝スキャンマッチング毒）。
5. covariance 初期値は V 系実測から seed する。段階ゲート（§6 への追加提案）: **V11a** MOLA 単独 dead-reckoning + `pose_quality` logging（6Hz vs 12Hz 比較込み）→ **V11b** EKF 融合（`odom1_pose_rejection_threshold` 込み）→ **V10** full-stack 予算。

### B-6. チューニング既知リスク

- **MOLA default は屋外スケール**: `absolute_minimum_observation_radius: 20.0`（→ keyframe 間隔 0.4m ＝盤面に3-4個）・`MOLA_SIGMA_INITIAL 0.5m`（＝盤面短辺の半分超）。未調整の初回試行は「動かない」ように見える——第1の罠。環境変数で override 可能。
- **scan rate トレードオフ**: T-mini Plus は 4000Hz 測距 ÷ 走査 6Hz＝0.54°/667点（12Hz なら 1.08°/333点）。0.3m/s 走行時、6Hz では **1回転あたり 50mm の motion skew**（系統誤差 20mm の 2.5 倍）。6Hz vs 12Hz は V11a で実測して決める。
- 棚がスキャン面（LiDAR **上面** 147.5mm 基準の暫定値＝[02:302](../shared/02-hardware-design.md)。上面 ≠ スキャン平面）と交差するかは**未確認**（交差しなくても閉矩形の壁4面で解は拘束されるが、棚由来特徴は消える）。
- **bot2 復帰時**（ADR-0006 は single-bot *first*）: 近接の大型移動剛体はスキャンマッチング毒。復帰時に再評価（→ B-10 OQ）。
- **価値の見積り（正直に）**: AMCL が `update_min_d 0.05` ＝盤面1走行あたり約36回補正する環境で、第3ソースの精度ゲインは V5 予算 30mm に対し数 mm。2026 年の 720-run ベンチマーク（Sensors）は「幾何学的に反復的な屋内では補正ソース追加が性能を悪化させ得る」と報告している。**真の価値は (a) omni 化時の横滑り観測性**（横滑りは wheel にも IMU にも観測不能・観測できる唯一の CPU 側センサ）**と (b) 回転スリップ transient のロバスト性**（V4）にある。精度が既に予算内なら急がない、が定量的な結論。

### B-7. 3D LiDAR 裁定（closed・幾何学的不成立）

廉価 360° 3D パック（Livox Mid-360 = 垂直 −7°〜+52°・Unitree L2 ≈ −6°）は**上向き偏重の垂直 FOV** を持ち、床が見えない盲円錐の半径 ≈ **搭載高 × 8〜9**。M1 の現実的搭載高 0.10〜0.20m で盲円錐半径 0.81〜1.9m ＞ **盤面半対角 1.02m** ＝盤面全域が不可視となり、LiDAR は周囲の**部屋**に対して自己位置推定する（板がズレると全座標が静かに狂う＝silent failure）。価格（L2 $419〜）・重量（230-265g）・電力（6.5-10W・バッテリ直タップ可）・Class 1 アイセーフティ・演算（FAST-LIO2 ≈1コア）は**すべて非 blocker** であり、誤った理由で closed にしない。条件付き経路（L2 を 35-40° 前傾・360° 放棄・EKF odom1 限定）は記録のみ・非推奨。

### B-8. 監視上の帰結（A-6 表への第3行・A-5 プロファイルの MOLA 行）

スキャンマッチャは cuVSLAM と**逆**で、**縮退時も固定レートで publish し続ける**（迷子でも鮮度は正常に見える＝AMCL 型の failure mode）。ただし MOLA は `pose_quality` + diagnostics の**自己申告**を持つため、A-7 二重化の具体形が定義できる:

| ソース | 鮮度で loss 検出 | 速い経路（自己申告） | 遅い経路（watchdog） |
|---|---|---|---|
| MOLA-LO | **不可**（縮退時も publish 継続） | `pose_quality` 閾値 + diagnostics level | topic 途絶（プロセス死のみ検出） |

A-6 の既存表は行安定のため編集せず、本行を追補として扱う。

### B-9. cuVSLAM 記述の訂正記録（将来復活時のため）

将来ステレオカメラ購入等で cuVSLAM を復活させる場合、§5-3 の旧記述には次の誤りがあることを確認済み（source 検証）:

1. 「loop closure 補正が EKF にジャンプとして入る問題は `differential` で扱う」＝**前提誤り**。loop closure 補正は SLAM 側（map→odom / slam_path）にのみ乗り、odometry topic には**乗らない**。SLAM 無効（`enable_localization_n_mapping: false`）なら loop closure 自体が存在しない。実際のジャンプ源は tracking 再初期化（upstream #183/#206）で、防御は `odom1_pose_rejection_threshold`。
2. `/visual_slam/tracking/odometry` の covariance は**直近10姿勢の軌跡ばらつき**（不確かさではない。静止≈0＝過信・loss 直後は identity）。実 covariance は `/visual_slam/tracking/vo_pose_covariance` 側。
3. **`publish_map_to_odom_tf` は SLAM 無効でも default true のまま identity の map→odom を配信し続け AMCL と衝突**する。両 TF フラグの明示 false が必須。
4. 起動時原点オフセットの正解は `odom1_relative: true`（差分化＝differential より安い）。

### B-10. 新規 OQ（本追補由来）

- **OQ-13: MOLA 途中再起動の原点リセット** — 実行中に MOLA だけ再起動すると pose が現在位置 =(0,0,0) へ跳ぶ。rejection threshold での吸収可否を V11b で確認。
- **OQ-14: bot2 復帰時のスキャンマッチング毒** — 2台目導入時に MOLA の品質劣化を再評価（`pose_quality` で観測可能なはず）。
- **OQ-15: 棚とスキャン面（LiDAR 上面 147.5mm 基準・暫定）の交差** — 実機で確認（S2 と同時に可能）。

### B-11. Sources（外部一次情報・参照日 2026-08-10 / 2026-08-17）

- ascamera driver source（HP60C 分岐に IR publisher 無し）: <https://github.com/BhavyaPatel9/ascamera> ／ 実機 topic list: <https://github.com/Harishtamilselvan/ascamera_ros2>
- Isaac ROS release-3.2 cuVSLAM ノード（ステレオのみ・depth 経路なし）: <https://github.com/NVIDIA-ISAAC-ROS/isaac_ros_visual_slam/blob/release-3.2/isaac_ros_visual_slam/src/visual_slam_node.cpp> ／ カメラ要件表: <https://nvidia-isaac-ros.github.io/v/release-3.2/repositories_and_packages/isaac_ros_visual_slam/index.html>
- RGBD は 4.1+（2026-02-02・Jazzy/Thor）: <https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_visual_slam/index.html> ／ PyCuVSLAM: <https://github.com/nvidia-isaac/cuVSLAM> ／ Orin Nano Super 実測: <https://arxiv.org/html/2506.04359v3>
- MOLA-LO: <https://index.ros.org/p/mola_lidar_odometry/> ／ ROS 2 API（identity 起動・TF）: <https://docs.mola-slam.org/latest/ros2api.html> ／ 2D pipeline: <https://github.com/MOLAorg/mola_lidar_odometry/pull/43>
- rf2o zero-covariance（source 検証）: <https://raw.githubusercontent.com/MAPIRlab/rf2o_laser_odometry/ros2/src/CLaserOdometry2DNode.cpp>
- robot_localization（ε=1e-6 置換・N−1 ルール・differential/relative 定義）: <https://raw.githubusercontent.com/cra-ros-pkg/robot_localization/humble-devel/doc/preparing_sensor_data.rst> ／ <https://raw.githubusercontent.com/cra-ros-pkg/robot_localization/humble-devel/doc/configuring_robot_localization.rst>
- Kinematic-ICP: <https://github.com/PRBonn/kinematic-icp>
- T-mini Plus datasheet: <https://akizukidenshi.com/goodsaffix/YDLIDAR%20T-mini%20Plus%20Data%20Sheet_V1.1%20(240131).pdf>
- 第3ソース追加の悪化リスク（720-run ベンチ）: <https://doi.org/10.3390/s26134264>
- 3D LiDAR: Mid-360 specs <https://www.livoxtech.com/mid-360/specs> ／ Unitree L2 <https://www.unitree.com/mobile/L2/> ／ FAST-LIO2 <https://arxiv.org/pdf/2107.06829>

### B-12. 関連リンク（双方向）

- [12-infrastructure-common.md](12-infrastructure-common.md) — Guardian/freshness guard（CURRENT 正本）。監視プロファイルの MOLA 行は B-8。
- [docs/GLOSSARY.md §11](../GLOSSARY.md) — **MOLA-LO** / **状態割当（EKF state allocation）** の正準定義（本追補と同時追加）。
- [ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md) — 末尾追記「Humble pin の新たに判明したコスト」（B-1 のソフト側 blocker の ADR 側記録）。
- [productization/01-commercial-box-map.md](../productization/01-commercial-box-map.md) — MOLA-LO GPLv3 flag（末尾）。
- HTML companions: [perception-localization-flow.html](perception-localization-flow.html) / [robot-architecture-tree.html](robot-architecture-tree.html) — 本追補と同一ラウンドで更新。
