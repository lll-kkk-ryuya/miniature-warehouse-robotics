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
3. **M1 外接円半径 184mm vs 通路 280mm（nvblox 以前の既存矛盾・優先度高）**: 円形 footprint（直径 368mm）では通路に入れない。非円形 footprint polygon への移行（`consider_footprint` 再有効化・`nav2_params.yaml:171-179` の #67 教訓に注意）が事実上必須で、**nvblox 統合より先**。担当トラックの確定が要る。 → **解決済み（2026-08-17）: (c) ハイブリッド採用**（非円形 footprint polygon を主機構とし、レイアウト側制約を明文化）・担当 = nav-traffic（[Issue #519](https://github.com/lll-kkk-ryuya/miniature-warehouse-robotics/issues/519)）。決定と Slice 1 実装計画は本 doc 末尾【2026-08-17 追補】**F 系列**が正。**上記本文は履歴として残す**。
4. **Yahboom 工場イメージの ekf 設定の実体**: [ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md) の一行のみが根拠。`robot_localization` の ekf.yaml か独自実装か、実機到着後に確認するまで「流用できる」を前提にしない。
5. **ZUPT の実装場所と所有トラック**: 静止判定→0速度 Odometry 注入ノードの新設（robot_localization 標準機能に無い想定・要確認）。
6. **pose_stale 時の nvblox integration 停止**: TSDF 汚染（§3）への対策として Guardian→nvblox の信号が要るか。Guardian を policy 層に留める方針（#126）との整合は所有トラック判断。
7. **`camera_link` contract PR の切り方**（**解決済**: camera_link の**名前のみ**を単独先行で land し、C-1（ROBOT_RADIUS 改訂・OQ-3 依存）は分離した。URDF body/joint と光学 frame 名は実測・OQ-4 待ちで据え置き）。 → **依存元の OQ-3 も解決済み（2026-08-17・末尾 F 系列）**。C-1 の最終形は **F-6**（`ROBOT_RADIUS` は値・意味とも据え置き、`FOOTPRINT_POLYGON` / `CIRCUMSCRIBED_RADIUS` を additive 追加）＝「ROBOT_RADIUS を 184mm へ改訂」という前提自体が撤回された。
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

  **2026-08-17 追記②（sim ゲート実走・P1 PASS / P2 FAIL → 速度項の撤去）**: 上記スライスの受け入れ条件である sim full-stack を Gazebo で実走した（証跡: `scratchpad/sim-gate/evidence/`＝ローカル成果物・リポジトリ未追跡）。結果は**二分**した。
  - **P1（駐機）PASS**: 16 分駐機で `pose_age ≈ 960s` に達しても `pose_stale` estop は **0 件**。同時に `/amcl_pose` の沈黙（AMCL が motion-gated である事実）も実測で確認＝**OQ-11 の前提と変位ゲートの駐機側は sim で実証**。
  - **P2（走行/出発）FAIL**: 実装が A-5③ から逸脱し、変位項に **`|速度| > pose_freshness_speed_epsilon` を OR** していたことが根本原因。① **出発デッドロック**——駐機明けの最初の運動 tick（実測 変位 3.2mm・odom 34ms fresh）で速度項がゲートを開き、`pose_age`(960s) > 1.0s で即 estop → 速度 0 → AMCL の `update_min_d`(0.05m) に届かず pose が来ない → 無限反復（nav goal **5/5 キャンセル**）。② **ラッチ破れ**——AMCL 死亡下で速度項が自ら止めた速度と共に上下し、累積変位 0.023m（< `motion_epsilon` 0.10m）のまま **12 秒で estop 立ち上がり 17 回**。
  - **A-5③ は元からこの形を禁じている**（本節上部 :349「速度のみのゲート＝estop→速度0→ゲート閉の fail-open limit cycle」＝許容は**変位ゲート形のみ**）。したがって修正は docs 側ではなく**コードを docs に合わせる**（速度項と config キー `pose_freshness_speed_epsilon` を削除。同キーは #524 で追加され他に消費者無し）。ゲートは `変位 > motion_epsilon ∨ |Δyaw| > angular_epsilon ∨ fail-closed` のみとなる。
  - **受け入れられたトレード**: 速度項が担っていた「`v_eps` 以下の匍匐前進の発火上限 5.0s」は失われ、遅延は `motion_epsilon / v`＝**v→0 で非有界**になる（例 2mm/s で約 50s）。ジオラマ 1.8m×0.9m で「localizer 喪失下の 0.10m 走行」は有界かつ小さい一方、速度項はロボットを走行不能にした——ゆえに変位のみを採る。検出は**遅れるが失われない**（変位は単調＝必ずいつか超える）。R-26 unit で出発（estop 0 件）・ラッチ（立ち上がり 1 回のみ）・クリープ遅延を pin 済。
  - **未達（PENDING）**: 本修正後の **sim gate 全体の再実走が最終受け入れ**。本 PR が主張するのは offline R-26 unit までで、P2 の sim 実証は再実走まで保留。

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

### B-13.【2026-08-17 追記】sim gate 完走記録 — A-10 受け入れ二部構成のクローズ

PR #525（速度項除去）後の Gazebo sim 再実行で **A-10 の受け入れ（999 全削除 ＋ 既定 `pose_freshness_timeout: 1.0` のまま full-stack 完走）を PASS** した。実測（コンテナ内コードは main `5c8e439` と byte 一致を検証済み・証跡は run ログ）:

- **駐機免疫**: 135s〜16min 駐機・AMCL 完全沈黙（`topic hz` 120s 無サンプル）・pose_stale **0 件**。
- **発進**: 駐機 pose_age 273s からの発進×3 + 別 bot 含む計6回、すべて pose_stale 0 で離脱。AMCL は **26.6mm（直線）/ 0.24rad** で republish ＝ ゲート閾値 100mm / 0.4rad に対し**両軸で約2倍マージンが実測どおり成立**（回転主導の離脱では `update_min_a 0.2rad` 側が先に効く）。
- **陽性対照**: AMCL 停止後、**経路長 100.4mm でちょうど1回** estop（修正前: 3.2mm で 17 edges/12s のバタつき）。ラッチ健全。

**新規 OQ（sim 実測由来・未修正の残差）**:

- **OQ-16: odom ギャップ >0.5s で駐機偽陽性が fail-closed 経由で再発** — 完全静止中に計算負荷で `/odom` が 0.69s 途切れ、`odom_freshness_timeout: 0.5` の fail-closed がゲートを開いて pose_stale 発火（2 bot 同ミリ秒）。仕様どおりの fail-closed だが、OQ-11 の駐機免疫は「odom が 0.5s 以内に届き続ける限り」に条件づけられる。解の候補: 静止確定後の odom gap 猶予・timeout の実測ベース調整（緩和は restrict-only 裁定要）・odom publisher の QoS/負荷対策。
- **OQ-17: ゲートの距離は経路長・AMCL の `update_min_d` は直線距離** — 振動・微動の累積で「経路長 99.3mm / 直線 2.3mm / Δyaw 0.014rad」が実測され、AMCL が理論上 republish できない量でゲートが開き得る（除去済み速度項と同クラス・ただし遥かに軽微: 直線離脱では 50mm 直線で AMCL が先に発火する）。加えて累積器は pose 到着でのみリセットされるため駐機を跨いで primed される（実測: 34.4mm 持ち込み）。解の候補: 累積を直線変位 max に変更 or 閾値の余裕拡大（tighten 側でない変更は A-5 restrict-only 裁定要）。

（gate と独立の発見: `KNOWN_LOCATIONS` の shelf_*/charging_station が `map.pgm` の障害物セル内＝Nav2 到達不能。nav-traffic/bringup 所有の別課題として起票する）
---

## 【2026-08-17 追補】OQ-3 解消: 非円形 footprint 移行の決定（F 系列）

Status: **決定の記録 ＋ Slice 1 実装計画**（実装なし。CURRENT の `nav2_params.yaml` / `collision_monitor.yaml` / `robot_dimensions.py` は本追補では**変更しない**）。数値・polygon 座標・config キーはすべて **TARGET 例示であり凍結契約ではない**（凍結側は `warehouse_description` の実体が正＝[docs-first.md](../../.claude/rules/docs-first.md)）。§8 項目 3（OQ-3）・項目 7 の「→ 末尾 F 系列」参照の実体が本節。

> **系列記号の注記**: 本追補は **F 系列**（F-1, F-2 …）を使う。**C 系列は欠番**——[shared/02:355-372](../shared/02-hardware-design.md) の M1 採用時作業項目 C-1〜C-8 と衝突するため（C-1/C-2/C-3 は本追補が参照する既存記号として温存する）。A 系列＝【2026-08-10 追補】、B 系列＝【2026-08-17 追補】MOLA-LO。

### F-1. 決定（2026-08-17・Issue #519）: (c) ハイブリッド採用

OQ-3（[:244](23-perception-and-localization.md) M1 外接円 184mm vs 通路 280mm）に対し、**(c) ハイブリッド = 「(a) 非円形 footprint への移行を主機構」＋「レイアウト側制約の明文化」** を採用する。担当 = **nav-traffic**（[Issue #519](https://github.com/lll-kkk-ryuya/miniature-warehouse-robotics/issues/519)）。

- **主機構 (a)**: 両 costmap の `robot_radius: 0.075`（[nav2_params.yaml:215](../../ws/src/warehouse_bringup/config/nav2_params.yaml)（local）/ [:257](../../ws/src/warehouse_bringup/config/nav2_params.yaml)（global））を **矩形 `footprint:`（M1 実寸 231.4 × 284.4mm）** へ置換し、MPPI `CostCritic.consider_footprint` を **`true` へ戻す**（現行 `false`＝[:179](../../ws/src/warehouse_bringup/config/nav2_params.yaml)）。**余裕（margin）は footprint に盛らない**——実寸 polygon を置き、余裕は inflation_layer と collision_monitor 側で持つ（余裕を二重計上すると通路 24.3mm/側 が消える）。
- **⚠️ #67 教訓（最重要・同一 PR 制約）**: `footprint:` と `consider_footprint: true` は **必ず同一 PR で同時に flip** する。片方だけだと costmap が footprint polygon を publish せず、`controller_server` が configure に失敗（"Considering footprint ... but no robot footprint provided"）して **lifecycle bringup ごと abort** する（[nav2_params.yaml:171-179](../../ws/src/warehouse_bringup/config/nav2_params.yaml) の既存コメント＝#67 E2E ゲートでの実観測。同コメント末尾の `# TODO(Phase 2)` が指示する手順そのもの）。
- **distro 整合（裏取り済・参照日 2026-08-17）**: MPPI は Humble へ backport 済（[navigation2 PR #3439](https://github.com/ros-planning/navigation2/pull/3439)・Humble バイナリ 1.1.19/1.1.20）で、navigation2 `humble` ブランチ README は ObstaclesCritic / CostCritic **両方に `consider_footprint`（default false）** を記載する。したがって [ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)（Jazzy→Humble pin）後も本決定はそのまま成立する。

### F-2. 幾何（数値の出所・すべて既存 doc から）

| 量 | 値 | 出所 |
|---|---|---|
| M1 全幅 × 全長 | **231.40 × 284.40mm** | [shared/02](../shared/02-hardware-design.md)（実寸正本）/ [shared/04:115](../shared/04-diorama-layout.md) |
| 外接円半径（対角 ÷ 2） | ≈**184mm**（対角 ≈367mm） | [shared/02:357](../shared/02-hardware-design.md)（C-1）/ [shared/04:130](../shared/04-diorama-layout.md) |
| **内接半径**（幅 ÷ 2） | **115.7mm** | 231.4 ÷ 2（本追補で導出） |
| すれ違い不可通路（渋滞誘発用） | ≈**280mm** | [shared/04:128](../shared/04-diorama-layout.md)（車体幅 231.4 + 余裕 50） |
| 直進クリアランス | (280 − 231.4)/2 ≈ **24.3mm/側** | 本追補で導出 |

**24.3mm/側 は実証済み水準**: CURRENT は Ø150 車体を 200mm 通路で走らせ **25mm/側** で 2 台 live 走行に成功している（#125・[nav2_params.yaml:239-246](../../ws/src/warehouse_bringup/config/nav2_params.yaml) の inflation コメント）。**M1 での 24.3mm/側 は横方向クリアランスとしては既に通した難度と同じ**——これが (a) を主機構にできる根拠である。塞がるのは**直進**ではなく**その場回転**（対角 367mm > 280mm）。
**⚠️ ただし等価なのは横方向のみ（円形に無かった新制約 = 方位余裕）**: 円形車体はどの yaw でも掃引幅が不変だが、矩形の有効掃引幅は `231.4·cosθ + 284.4·sinθ` で増える——yaw 誤差 1° で有効幅 ≈236mm（クリアランス 21.8mm/側）、2° で ≈241mm（19.4mm/側）、**~10° で 280mm に達しクリアランス 0**。`consider_footprint: true` 下では yaw の逸れた軌道が即 lethal になり、#125 で観測した「経路を commit できず停止」の再現リスクがある。許容 yaw 誤差と MPPI の方位追従は **OQ-19**（F-8）で sim 実測する。

### F-3. (b)（通路を ≥420mm へ拡幅するだけ）を採らない理由

- **盤面が持たない**: 280mm 時点で「棚3列 + 縦通路2本 + 横断通路1本 + バース2 + 出荷/充電ステーション」が 1800×900mm に収まるか **未計算のまま**（[shared/04:132](../shared/04-diorama-layout.md) の `# TODO(発注前〜Phase 1)`）。拡幅は未解決の収まり問題を悪化させる。
- **実幅 231mm の車体を 368mm 幅として扱う浪費**: 円形近似のまま拡幅すると、通路も交差点も外接円基準で設計することになり盤面面積を二重に食う。
- **公正な記録（(b) でも保てたもの）**: 420mm でも **2 台すれ違いには足りない**（すれ違い可 ≈510mm＝[shared/04:129](../shared/04-diorama-layout.md)）。つまり「すれ違い不可通路で渋滞を誘発する」という**デモの意味論は (b) でも成立していた**。(b) を退けたのは意味論の破綻ではなく**盤面収まりと設計の無駄**が理由である。
- **(a) 側の工数が小さい**: 主機構 (a) は既存 config の書き換え（`footprint:` ＋ `consider_footprint` ＋ inflation 再調整＝F-5）で閉じ、横方向クリアランス 24.3mm/側 は #125 で**同難度を live 実証済み**（F-2）。盤面の物理再設計を伴う (b) とは工数の桁が違う。
- **⚠️ 誤読防止（(b) 却下 ≠ 一切拡幅しない）**: ここで退けたのは「**通路を一律 ≥420mm へ拡幅するだけで済ませる**」という (b) 単独案である。**交差点／転回ポケットに限った部分的な拡幅（≥ ~420mm 角）は採用する**——対角 367mm > 280mm で通路内回転が不可能である以上、回転を許す場所は別途要るためである（本追補 F-4-2 / [shared/04](../shared/04-diorama-layout.md) F-L2）。**この「(a) 主機構 ＋ 部分拡幅」の組み合わせが (c) ハイブリッドの中身**であり、F-3 と F-L2 は矛盾しない。

### F-4. レイアウト側制約（正本は shared/04・本 doc は forward link のみ）

(c) の「レイアウト側」半分。**数値の正本は [shared/04](../shared/04-diorama-layout.md) 末尾 §【2026-08-17 追補】OQ-3 決定に伴う通路制約（(c) ハイブリッド・レイアウト側）の F-L1〜F-L5**（同一ラウンドで land・行 pin なし）。**以下は読解用の要約**であり、値の改訂は shared/04 側の PR で行う（本節と shared/04 が食い違えば shared/04 が正）。

1. **280mm 通路 = 直進専用**。対角 367mm > 280mm ゆえ **その場回転（in-place rotation）は幾何学的に不可能**。
2. **交差点 / 転回ポケットは ≥ ~420mm 角**。[shared/04:130](../shared/04-diorama-layout.md) の「≈367mm 角」は対角円がちょうど収まる値＝**余裕ゼロ**なので、実運用には上乗せが要る（≈420mm 角は本追補の TARGET 例示・shared/04 側で確定させる）。
3. **通路端の goal は「回転不要な向き」で置く**。通路内で最終姿勢合わせの回転が必要な goal を置かない（KNOWN_LOCATIONS の向き設計の制約）。

### F-5. Slice 1 実装計画（params・TARGET 例示）

1. **両 costmap の `footprint:` 化** — [nav2_params.yaml:215](../../ws/src/warehouse_bringup/config/nav2_params.yaml) / [:257](../../ws/src/warehouse_bringup/config/nav2_params.yaml) の `robot_radius: 0.075` を、`base_link` 中心の矩形へ:
   ```yaml
   footprint: "[[0.1422, 0.1157], [0.1422, -0.1157], [-0.1422, -0.1157], [-0.1422, 0.1157]]"
   ```
   （半値は 284.4/2 = **0.1422**・231.4/2 = **0.1157** をそのまま使う。**丸めるなら常に外側（切り上げ）**——切り捨ては実車体を包含しない under-cover を作る。）
   **`# TODO(Phase 1 実測)`: 上記は「車体中心 = base_link 原点」の前後対称仮定**。M1 の実際の回転中心オフセットは実機で実測し、非対称なら前後の値を分けて確定する。
2. **MPPI `CostCritic.consider_footprint: true`** — [:179](../../ws/src/warehouse_bringup/config/nav2_params.yaml) を反転。**1. と同一 PR**（F-1 の #67 教訓）。コメントも同時更新: `consider_footprint` の「円形前提」説明（:171-179）に加え、**ファイル冒頭の「FOOTPRINT/INFLATION: robot_radius = 0.075 m — single source」ヘッダ（:27-29）と両 costmap の `= ROBOT_RADIUS (R-42)` 行内コメント（:215 / :257）も footprint 化で虚偽になる**ため、全て現行構成の説明へ書き換える。
3. **inflation_layer 再調整** — 内接半径が 0.075 → **0.1157** に上がるため、CURRENT の `inflation_radius: 0.085`（[:245](../../ws/src/warehouse_bringup/config/nav2_params.yaml)）は内接未満になり無意味化する。#125 で確立したパターン（**inflation_radius = 内接 + 約 0.010**・steep `cost_scaling_factor: 10.0`＝[:239-246](../../ws/src/warehouse_bringup/config/nav2_params.yaml)）を踏襲すると **≈0.125–0.126**。280mm 通路では中心が壁から 0.140m なので中心付近に **±14mm の低コスト帯**が残る（#125 の 200mm 通路 / 0.085 では ±15mm＝**同水準**）。`cost_scaling_factor` は帯幅がこれだけ薄いため live で再調整する。
4. **Spin recovery の通路内抑止** — 通路内でその場回転が不可能（F-4-1）である以上、behavior_server / BT の Spin・BackUp 系 recovery を通路内で発火させない設計が要る（**Phase 2 例示**。BT xml か behavior 設定かは Slice 1 で決める）。
5. **対象 distro** — [ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)（Humble）整合。F-1 の裏取りにより **CostCritic 構成はそのまま持ち越し可**（Jazzy→Humble で書き換え不要）。
6. **R-26 safety unit の更新（同一 PR・レビュー対象）** — `tests/unit/test_nav2_params_safety.py` は両 costmap の `robot_radius == ROBOT_RADIUS` を固定しており（:113-118）、`robot_radius` キー消滅で **KeyError で必ず赤**になる。また `inflation_radius >= ROBOT_RADIUS` ガード（:124-129）は footprint 化後は**内接半径（0.1157）との比較**でなければ検出力を失う。両テストを「polygon 頂点 == `FOOTPRINT_POLYGON`」「`inflation_radius >= 内接半径`」の不変へ書き換える。**安全 unit の書き換えは R-26（独立オラクル・mutation で赤）そのものがレビュー対象**（[.claude/rules/safety.md](../../.claude/rules/safety.md) / [doc20 §9](20-dev-quality-and-testing.md)）。

### F-6. 隣接 Slice（本追補では言及のみ・所有トラック判断）

- **collision_monitor C-3（L1）** — `PolygonStop` は現在 `type: "circle"` / `radius: 0.09`（[collision_monitor.yaml:63-68](../../ws/src/warehouse_bringup/config/collision_monitor.yaml)）。M1 では内接 0.1157 を下回り**車体内部で発火しない**ため要改訂（[shared/02:359](../shared/02-hardware-design.md) C-3）。**L1 は反射経路＝別 PR・安全レビュー必須**（L2 の costmap 変更と混ぜない）。
- **`warehouse_description` 定数（contract PR・additive）** — `ROBOT_RADIUS = 0.075`（[robot_dimensions.py:66](../../ws/src/warehouse_description/warehouse_description/robot_dimensions.py)）は**値も意味も変えない**（旧 ~150mm 車体の内接半径・PROVISIONAL のまま据え置き）。**名称を据え置いたまま「外接円」へ意味だけ読み替えることはしない**——0.075 は M1 の内接 0.1157 すら下回り外接円になり得ないし、live consumer が**内接前提**で読んでいる（`warehouse_traffic/virtual_scan_logic.py`（相手機の仮想障害物化）・`traffic_manager.py`（`0.15m = 2*ROBOT_RADIUS` no-collision margin）・`warehouse_sim/scenarios.py`・unit tests の `== 0.075` pin）ため、意味の差し替えは**1 行も編集せず 5 箇所を壊す** silent semantic break になる。代わりに **`FOOTPRINT_POLYGON`（矩形実寸）と `CIRCUMSCRIBED_RADIUS`（外接円 ≈0.184）を additive に追加**し、C-3（collision_monitor）等の保守的用途は `CIRCUMSCRIBED_RADIUS` を消費する。既存 consumer の M1 値への移行は**2台復帰フェーズで消費箇所ごとに明示的に**行う（一括りの意味変更をしない）。旧 C-1 の「外接円半径 ≈184mm へ改訂」（2026-08-05 版・履歴は [shared/02](../shared/02-hardware-design.md) 末尾追補 §2 の対比表）は、本追補の (c) 採用により **単純な値差し替えではなく additive 化**に読み替える。**contract ラベル + 依存トラック予告が必要**（[parallel-workflow.md §4](../../.claude/rules/parallel-workflow.md)）。

### F-7. 検証観点

- **sim 回帰が本命の場**: 本件は **2D costmap 側の変更**であり、nvblox（[§7 S3:232-234](23-perception-and-localization.md)＝「sim は CURRENT 2D costmap の回帰を守る場・nvblox は Jetson + rosbag の場」）とは**場が分かれている**。すなわち OQ-3 の検証は **Mac/Gazebo の sim で回帰確認できる**（GPU 不要）。
- 最低限の受け入れ: ① lifecycle bringup が abort しない（#67 の failure mode が再発しない）② costmap が footprint polygon を publish している ③ 280mm 相当通路の直進 goal が plan され走破する ④ 交差点でのみ回転が起きる ⑤ **F-5-6 で更新した safety unit** を含む 2D costmap 系テストが緑（更新前の `robot_radius` pin テストは F-5-6 の通り必ず赤になる＝「既存テストが緑のまま」ではない）。
- **③ が落ちた場合の縮退（Slice 1 を止めないための逃げ道・いずれも docs 改訂を先行させる）**: 通路を 300mm 級へ微増（F-3 の (b) を部分適用）／ inflation の非対称化／ 通路区間のみ判定を緩める——のいずれかを **OQ-19/OQ-21**（方位余裕・横位置追従）の実測結果とともに shared/04 側で再裁定する。
- **⚠️ 残る不整合（Slice 1 の着手判断に必要）**: sim の URDF / ジオラマは依然 **~150mm 車体前提**（`robot_dimensions.py` の PROVISIONAL 群・[shared/04:112](../shared/04-diorama-layout.md) の暫定通路幅）。params だけを M1 実寸へ倒すと **sim の車体と costmap footprint が食い違う**。Slice 1 では「sim 側も M1 実寸へ同時に倒す」か「sim は現行値のまま params を config 差分で切替える」かを**先に決める**こと（本追補では決めない）。
- **⚠️ W3: 走行目標点 9 点も M1 では全点再設計（別スライス）** — PR #528 が確定した `locations` 9 点は **円形 `robot_radius` 0.075m 基準**の検証であり、M1 実寸では **9 点すべてが外接 184mm 未満（実測クリアランス 95.1〜125.1mm）＝その場回転不可**、うち **6 点は内接 115.7mm 未満＝goal として不成立**。M1 実機マップ取得後に F-4 / F-L1〜F-L3 の制約下で再設計し、`tests/unit/test_known_locations_navigable.py` の内接ゲートを **footprint パラメータ化**（F-6 の additive 定数を消費）する。実測値と詳細は **[shared/04](../shared/04-diorama-layout.md) 末尾 F-L3 の W3 注記が正本**（双方向）。

### F-8. 新規 OQ（本追補由来）

> **採番の注記（2026-08-17）**: 本追補の起草時点では OQ-16 / OQ-17 が空き番だったが、**同日 land した B-13（本 doc 直前節・PR #527）が OQ-16（odom ギャップ）/ OQ-17（経路長 vs 直線距離）を先に確定**させたため、本節は **OQ-18 以降**を使う（重複採番の回避）。以下は昇順。

- **OQ-18: sim 車体と実寸の同時移行可否** — F-7 の残る不整合。所有トラック（sim / nav-traffic）の調整事項。
- **OQ-19: 280mm 通路での許容 yaw 誤差と MPPI 方位追従** — F-2 の方位余裕（矩形化で新たに生じた制約: yaw ~10° でクリアランス 0）。`consider_footprint: true` 下で MPPI が通路内の方位を保てるか・AMCL の yaw 分散がどこまで許容されるかを sim で実測（OQ-21 の横位置と対になる縦軸）。
- **OQ-20: `base_link` の前後非対称オフセット** — F-5-1 の前後対称仮定は実機実測で確定（Phase 1）。
- **OQ-21: ±14mm 低コスト帯での MPPI 追従性** — F-5-3 の帯幅で MPPI が通路中心を維持できるか（`cost_scaling_factor` 再調整の要否）を sim で確認。

### F-9. References（双方向）

- [shared/02-hardware-design.md](../shared/02-hardware-design.md) — M1 実寸の正本 / C-1（:357 `ROBOT_RADIUS`）・C-2（:358 costmap ×2）・C-3（:359 collision_monitor）。本追補は C-1 を **additive 化**に読み替える（F-6）。**同 doc 末尾 §【2026-08-17 追補】OQ-3 決定に伴う C-1 / C-2 の改訂（非円形 footprint への移行）が C-1/C-2 側の正本**（本追補と双方向・行 pin なし＝同一ラウンド並行編集のため）。
- [shared/04-diorama-layout.md](../shared/04-diorama-layout.md) — **レイアウト側制約の正本**（:115 実寸 / :119,:130 交差点対角円 / :128 280mm / :129 510mm / :132 盤面収まり未計算）。F-4 の値の改訂は shared/04 側で行う。**同 doc 末尾 §【2026-08-17 追補】OQ-3 決定に伴う通路制約（(c) ハイブリッド・レイアウト側）（F-L1〜F-L5）が F-4 の実体＝正本**（本追補と双方向・行 pin なし）。
- `ws/src/warehouse_bringup/config/nav2_params.yaml` — CURRENT の引用は行 pin（:171-179 #67 教訓 / :215・:257 `robot_radius` / :239-246 #125 inflation）だが、**Slice 1（F-5）がまさにこれらの行を書き換える**ため、実装後は **キー名（両 costmap の `robot_radius`→`footprint:`・`FollowPath.CostCritic.consider_footprint`・`inflation_layer.inflation_radius`）で指す**こと（[session-orchestration.md §8](../../.claude/rules/session-orchestration.md) impl-target は契約で指す）。
- [mode-a/11a-traffic-mode-a.md](../mode-a/11a-traffic-mode-a.md) §9.4「安全不変」 — 「`inscribed_radius = ROBOT_RADIUS`（=0.075 R-42）は不変」「本機構は速度・footprint・inflation を変えない」という**旧・不変宣言を F 系列が上書きする**（M1 footprint 化で内接は 0.1157 へ。doc11a 側にも末尾に forward pointer を追記済み・同一 PR）。
- `ws/src/warehouse_bringup/config/collision_monitor.yaml`:63-68 — L1 `PolygonStop`（F-6）。
- `ws/src/warehouse_description/warehouse_description/robot_dimensions.py`:66 — 凍結側 `ROBOT_RADIUS`（PROVISIONAL）。
- [ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md) — distro pin（F-1 の Humble 整合）。
- [docs/GLOSSARY.md §11](../GLOSSARY.md) — **非円形 footprint（footprint polygon）** の正準定義（本追補と同時追加・双方向）。
- [Issue #519](https://github.com/lll-kkk-ryuya/miniature-warehouse-robotics/issues/519) — 本決定の起票元（Slice 0 = 本追補 / Slice 1 = F-5 の params 実装）。
- 外部一次情報（参照日 2026-08-17）: [navigation2 PR #3439](https://github.com/ros-planning/navigation2/pull/3439)（MPPI の Humble backport）/ navigation2 `humble` ブランチ MPPI README（ObstaclesCritic・CostCritic の `consider_footprint`・default false）。

---

## 【2026-08-18 追補】部屋スケール運用による F 系列の scope 限定（G 系列）

Status: **scope 注記のみ**（決定の本体は [ADR-0009](../adr/0009-m1-room-scale-operation.md)）。本追補は **F 系列を revert しない**——footprint polygon 採用は部屋でも生存する。CURRENT の config / コードは本追補では**変更しない**。既存 §番号・§8 の項目番号は**改めず末尾に足す**（[#165 教訓](../dev/03-retrospectives.md)・本 doc :288 / :511 と同じ扱い）。

> **系列記号の注記**: 本追補は **G 系列**を使う。A 系列＝【2026-08-10 追補】、B 系列＝【2026-08-17 追補】MOLA-LO、F 系列＝【2026-08-17 追補】OQ-3。**C は欠番**（[shared/02](../shared/02-hardware-design.md) の C-1〜C-8 と衝突・:569 と同じ理由）、D / E は未使用。

### G-1. 何が変わったか（前提の差し替え）

オペレーター決定（2026-08-18）により、**M1 は部屋（room scale）を走り、ミニチュアジオラマは M1 フェーズでは走行に使わない**（凍結保存・sim 回帰環境としては現状維持）。F 系列は「M1 をジオラマの 280mm 通路に通す」という問題設定の解であったため、**解そのものは生きるが、前提から出た数値は宛先を失う**。以下 G-2 で二分する。

### G-2. F 系列の scope 二分（生存 / ジオラマ限定の歴史記録）

| F 項目 | 内容 | 部屋での扱い |
|---|---|---|
| F-1 | (c) ハイブリッド採用＝**非円形 footprint polygon を主機構**にする決定 | **生存**（部屋にもドア開口・家具の隙間はあり、矩形車体を矩形として扱う価値は失われない） |
| F-2 上段 | M1 実寸 231.4×284.4mm / 外接 ≈184mm / **内接 115.7mm** | **生存**（車体の性質＝robot-intrinsic） |
| F-2 下段 | 280mm 通路 / 24.3mm 側クリアランス / 方位余裕 ~10° で 0 | **ジオラマ限定**。ただし掃引幅の式 `231.4·cosθ + 284.4·sinθ`（:590）は車体の性質として残り、**部屋で狭い開口を通す際に同じ形で再計算**する |
| F-3 | (b)（通路を ≥420mm へ一律拡幅）を採らない理由 | **ジオラマ限定の歴史記録** |
| F-4 / [04](../shared/04-diorama-layout.md) F-L1〜F-L5 | レイアウト側制約（直進専用・≥420mm 角ポケット・goal は回転不要な向き） | **ジオラマ限定の歴史記録**（ジオラマ復帰フェーズで再有効化） |
| F-5-1 / F-5-2 | `footprint:` 化と `consider_footprint: true` の**同一 PR 制約**（#67 教訓） | **生存**（環境非依存の実装制約） |
| F-5-3 | inflation を**内接 0.1157 基準へ再調整する手法** | **手法は生存**／ **±14mm 低コスト帯という具体値はジオラマ限定**（280mm 通路から導出） |
| F-5-4 | 通路内 Spin recovery の抑止 | **【訂正 2026-08-18】手法は生存・発火条件は再評価**（旧「ジオラマ限定」は誤分類。幾何的な発火理由〔通路内で回転不可〕は消えるが、**部屋では「人の脚の横でその場回転する」という新しい抑止理由が立つ**＝G-10） |
| F-5-6 | R-26 safety unit の書き換え（polygon 頂点・内接比較へ） | **生存** |
| F-6 | **2 部構成**: ①`FOOTPRINT_POLYGON` / `CIRCUMSCRIBED_RADIUS` の **additive 追加**（`ROBOT_RADIUS` は値・意味とも据え置き） ②**C-3 collision_monitor（L1）の改訂** | **①②とも生存**（①は contract PR + 依存トラック予告が従来どおり必要／**②は部屋運用の前提条件**＝現行 `radius: 0.09` は M1 内接 0.1157 未満で車体内部発火＝機能しない。詳細は G-8） |
| F-7 W3 | ジオラマ 9 点の全点再設計（別スライス） | **中止（cancelled）**＝G-4 |

### G-3. OQ の再スコープ（F-8 の 4 件）

- **OQ-18（sim 車体と実寸の同時移行可否）— 【訂正 2026-08-18】降格ではなく LIVE。** 「sim 回帰の忠実度だけの問題へ降格」は誤り——`nav2_params.yaml` は env overlay を持たない**単一ソース**であり、M1 footprint 化はジオラマ map の sim 回帰も同時に変える。したがって本 OQ は「**Slice 1 が land できるか・sim ゲートが緑を保てるか**」を握る**構成問題として LIVE** である（config 二重化の詳細＝G-7）。所有トラック（sim / nav-traffic）の判断事項である点は不変。
- **OQ-19（280mm 通路での許容 yaw 誤差と MPPI 方位追従）— ジオラマ限定・凍結。** 280mm という盤面数値から出た問い。部屋では発火しない（背後の幾何的事実は G-2 のとおり残る）。
- **OQ-20（`base_link` の前後非対称オフセット）— そのまま LIVE。** 車体の回転中心という **robot-intrinsic** な量で、環境を変えても消えない。Phase 1 実機実測で確定（:641 のまま）。
- **OQ-21（±14mm 低コスト帯での MPPI 追従性）— ジオラマ限定・凍結。** 280mm 通路 × inflation から導出された帯幅のため。

### G-4. W3（走行目標点 9 点の再設計）は中止し、宛先を差し替える

:633（F-7 W3）と [04](../shared/04-diorama-layout.md) F-L3 の W3 注記が予告した「M1 実機マップ取得後にジオラマ 9 点を再設計する別スライス」は、**ジオラマを走らないため中止**する。**`KNOWN_LOCATIONS` の 9 キー自体は凍結のまま（改名しない）**で、**値**が Phase 1 の部屋 SLAM 地図取得後に実測 waypoint へ差し替わる（[ADR-0009](../adr/0009-m1-room-scale-operation.md) Decision 5）。`tests/unit/test_known_locations_navigable.py` の内接ゲートを **footprint パラメータ化**する必要（F-6 の additive 定数を消費）は**部屋でもそのまま残る**。

**⚠️ 別件・重要度が上がる残件（OQ-20 ではない）**: 座標ゴールは yaw を落としている——`nav2_bridge.py` が `orientation.w = 1.0` を固定する（`warehouse_nav2_bridge/CLAUDE.md` の「#223 残 ②」・impl は行 pin せずキーで指す＝[session-orchestration.md §8](../../.claude/rules/session-orchestration.md)）。ジオラマでは F-L3「通路端の goal は回転不要な向きで置く」で回避できていたが、**部屋では召喚の到達姿勢（人の方を向いて止まる）が絵の質に直結する**ため重要度が上がる。yaw 対応は別変更・所有トラック判断。

### G-5.【訂正】:562 の「別課題として起票する」は解決済み

B-13 末尾 :562 の括弧書き（`KNOWN_LOCATIONS` の shelf_*/charging_station が `map.pgm` の障害物セル内＝Nav2 到達不能・「nav-traffic/bringup 所有の別課題として起票する」）は、**[PR #528](https://github.com/lll-kkk-ryuya/miniature-warehouse-robotics/pull/528) で既に解決済み**（走行目標点への改訂＝棚前 docking 点化・[04:142-159](../shared/04-diorama-layout.md)）。**:562 の本文は行安定のため編集せず、本項を訂正の正とする**（:511 と同じ扱い）。なお #528 が確定した座標は sim（ジオラマ）側では有効なまま——本 ADR-0009 が supersede するのは**実機（部屋）側の値**であり、sim 回帰用の `map.pgm` × 9 点は不変である（G-2 の「sim はジオラマのまま」）。

### G-6. 関連リンク（双方向）

- **決定正本**: [ADR-0009 M1 フェーズは部屋スケールで運用する](../adr/0009-m1-room-scale-operation.md)（本追補はその知覚・Nav2 側の scope 注記）
- [shared/04-diorama-layout.md](../shared/04-diorama-layout.md) 末尾【2026-08-18 追補】— レイアウト側の scope 限定（F-L 系列の凍結）。**本追補と双方向・行 pin なし**（同一 PR）
- [mode-x-er/09-hand-raise-summon.md](../mode-x-er/09-hand-raise-summon.md) 末尾【2026-08-18 追補】— ジェスチャ幾何前提の部屋での再検証（カメラ仰角・安全論証）
- [architecture/06-implementation-phases.md](06-implementation-phases.md) 末尾追記 — Phase 1 の部屋 SLAM 地図取得
- [docs/GLOSSARY.md §11](../GLOSSARY.md) — **部屋スケール運用（room-scale operation）** の正準定義（同一 PR で追加）
- HTML companion（[perception-localization-flow.html](perception-localization-flow.html) / [robot-architecture-tree.html](robot-architecture-tree.html)）への反映は**本 PR ではスコープ外**（ADR-0009 Open）

---

## 【2026-08-18 追補②】二重監査の反映（G-7〜G-10・G-2/G-3 の補訂）

Status: **scope 注記・訂正のみ**（CURRENT の config / コードは本追補でも**変更しない**）。同日の【2026-08-18 追補】G 系列（:659-）に対する二重独立監査（FIX-FIRST）の指摘を反映する。**既存 §番号・行は動かさず末尾に足す**（[#165 教訓](../dev/03-retrospectives.md)）。G-2 表の F-5-4 / F-6 行と G-3 の OQ-18 は**同一行内で訂正済**（行数不変）で、その根拠が本節である。

### G-7. sim / 実機の config 二重化は未決 OQ（G-3 OQ-18 の実体）

**部屋でも `footprint:` 化そのものは実施する**（G-2 の F-1 / F-5-1・robot-intrinsic）。しかし「どの環境の config に入れるか」が未決である。

**① `nav2_params.yaml` は env overlay を持たない単一ソース**
- 実体は [`ws/src/warehouse_bringup/config/nav2_params.yaml`](../../ws/src/warehouse_bringup/config/nav2_params.yaml) 1 本で、`config/dev|stg|prod/warehouse.yaml`（[environments.md](../../.claude/rules/environments.md) の base + overlay）は **Nav2 params を上書きしない**（overlay に `robot_radius` / `footprint` / nav2 系キーは存在しない・2026-08-18 実査）。
- したがって **M1 footprint 化（`robot_radius`（local [:215](../../ws/src/warehouse_bringup/config/nav2_params.yaml) / global [:257](../../ws/src/warehouse_bringup/config/nav2_params.yaml)）→ `footprint:` ＋ `consider_footprint`（[:179](../../ws/src/warehouse_bringup/config/nav2_params.yaml)）の flip** は、**実機（部屋）と sim（ジオラマ map）の両方を同時に変える**。sim はジオラマのまま維持する（G-2）以上、これは「~150mm 車体 × ジオラマ」を前提にした 2D costmap 回帰の環境を M1 実寸へ倒すことを意味する。
- **同居方法は未決**: (a) Nav2 params に env overlay を新設する / (b) sim 専用 params ファイルを分ける / (c) sim も M1 実寸化する（sim URDF・`robot_dimensions.py` の PROVISIONAL 群まで倒す）——いずれも所有トラック（sim / nav-traffic / bringup）の判断事項。**本追補では決めない。**

**② `locations` も単一ソース＝テスト oracle が committed ジオラマ map に束縛されている**
- 9 座標の実体は [`config/warehouse.base.yaml:47-56`](../../config/warehouse.base.yaml) のみ（env overlay に `locations` は無い・同上実査）。
- [`tests/unit/test_known_locations_navigable.py:24-29`](../../tests/unit/test_known_locations_navigable.py) は `KNOWN_LOCATIONS` × `load_config()` の base 値を **committed な `warehouse_sim/maps/map.pgm`（ジオラマ）** に対して検証する。**M1 内接 0.1157m を当てると 9 点中 6 点が不成立**（[04:197](../shared/04-diorama-layout.md) の実測 95.1〜125.1mm）。
- つまり **実機値（部屋の実測 waypoint）と sim 値（ジオラマ）を分離する手段が未決**であり、**決め方によってこのテストのオラクル定義そのものが変わる**（何を「正しい 9 点」とするか＝どの map に対して検証するか）。値だけを差し替えると sim ゲートが赤くなる。

**③ 以上より OQ-18 は「sim 回帰の忠実度」問題ではなく構成問題**（G-3 で訂正済）。**Slice 1（F-5）の着手判断に直接効く**ため、Slice 1 の最初のステップは「①②の同居方法を決める docs 改訂」である。

**④ 新規 OQ として登録**:
- **OQ-22: sim / 実機の config 二重化の方式** — `nav2_params.yaml`（footprint）と `config/warehouse.base.yaml`（`locations`）の実機値 / sim 値の分離手段を決める。`test_known_locations_navigable.py` のオラクル定義（対象 map・内接半径の出どころ）を同時に確定させること。所有＝ sim / nav-traffic / bringup の調整事項。優先度 **最高**（Slice 1 の前提）。

### G-8. F-6 の後半（C-3 collision_monitor 改訂）は部屋運用の前提条件

G-2 の F-6 行は当初 additive 定数（[F-6](23-perception-and-localization.md) 前半）だけを「生存」と書いていたが、**F-6 は 2 部構成**である。後半＝**C-3 collision_monitor（L1）の改訂**が抜けていた。

- CURRENT の `PolygonStop` は [`collision_monitor.yaml:63-68`](../../ws/src/warehouse_bringup/config/collision_monitor.yaml) で `type: "circle"` / **`radius: 0.09`**。これは旧 ~150mm 車体の内接 0.075 + 余裕という暫定値である。
- **M1 では内接 115.7mm（0.1157）を下回る**ため、停止ポリゴンが**車体の内部に収まる**——障害物が車体に接触してからでないと polygon 内に入らず、**L1 反射が実質機能しない**。
- L1 の正しい振る舞いは「実車体を必ず包含する保守側の円」（＝外接 `CIRCUMSCRIBED_RADIUS` ≈0.184 + 反応余裕。[02 §3 C-3 は外接円ベースのままで妥当](../shared/02-hardware-design.md)）であり、この改訂は **L1 = 反射経路ゆえ別 PR・安全レビュー必須**（L2 の costmap 変更と混ぜない＝F-6 の当初方針どおり）。
- **⚠️ 部屋では前提条件になる**: ジオラマでは走行面上に人がおらず L1 の主対象は壁・棚・相手機だったが、**部屋では人が走行面上に立つ**（[ADR-0009](../adr/0009-m1-room-scale-operation.md) 帰結 ⑦）。安全論証が「L1 collision_monitor が生きている」ことに依存する以上（[09 R-3](../mode-x-er/09-hand-raise-summon.md) 柱 3）、**C-3 改訂の完了は部屋運用の前提条件**である。改訂前に部屋で走らせると、多層防御の 1 枚が名目上だけ存在する状態になる。

### G-9. nvblox dynamic 層「不採用」の根拠が消滅＝再評価 OQ

- [:74](23-perception-and-localization.md)（§2 表）の不採用理由は「**ジオラマに人はいない**」であり、[:278](23-perception-and-localization.md)（【2026-08-09 追補】）でこれを「**走行面上に動的障害物が無い**」＝人は盤外ゆえ結論不変、と言い換えていた。
- **部屋では人が走行面上に立つ**ため、**この根拠は成立しない**。「結論は不変」という言い換えの前提そのものが消えた。
- **再評価すべき対立**: (i) people segmentation を入れる＝**GPU メモリの固定コストが増える**（S1 の 8GB 予算に直撃。[:210](23-perception-and-localization.md) が「本構成は people segmentation を最初から持たない」ことを S1 の前提にしている）vs (ii) static TSDF only のままだと**歩いた人が static TSDF に焼き付く**（[:278](23-perception-and-localization.md) が挙げた人物 bbox depth マスク案は、人が盤外にいる前提での「視野に入る」対策であって、**走行面上を歩き回る人**には足りるか未検証）。
- **メモリ試算も桁が変わる**: [:212-214](23-perception-and-localization.md) の TSDF 概算（ジオラマ 1.8×0.9×0.3m ＝ voxel 0.01m で ~486k voxel ≈ 数 MB〜10MB 台）は**ジオラマ体積前提**。部屋（例 4×4×2.5m 級）では体積が 2 桁増える。「map 範囲をジオラマ実寸に制限」というダイエット案（同 :211）も宛先を失う。
- **新規 OQ として登録**:
  - **OQ-23: 部屋での nvblox dynamic 層の再評価とメモリ再試算** — (a) people segmentation の GPU コスト vs (b) static TSDF に人が焼き付く問題、の裁定。**併せて部屋体積での TSDF/ESDF メモリ再試算**（:212-214 のジオラマ前提の置き換え）と、ダイエット案（:211）の宛先の再定義を含む。**S1 の Go/No-Go 判定条件（残 RAM ≥ 500MB）に直接効く**。所有＝知覚（本 doc）。優先度 **高**。
  - なお [09 OQ-12](../mode-x-er/09-hand-raise-summon.md) 等が参照する「dynamic 層不採用の結論は不変」（:278）は、**本 OQ-23 の結論が出るまで保留**として読むこと。

### G-10. F-5-4（通路内 Spin recovery 抑止）の再分類

G-2 表の当初分類「ジオラマ限定」は誤りで、**手法は生存・発火条件が差し替わる**（同表は訂正済・行数不変）。

- **旧・発火理由（ジオラマ限定）**: 280mm 通路では対角 367mm ゆえ**その場回転が幾何学的に不可能**（F-4-1）。だから通路内で Spin / BackUp recovery を発火させてはならなかった。**この理由は部屋では消える**（一般に回転できる広さがある）。
- **新・発火理由（部屋固有・より強い）**: **人の脚の横でその場回転する**のは、幾何的に可能でも運用上・安全上望ましくない。メカナムのその場回転は車体四隅が掃引円を描き（外接 ≈184mm）、**立っている人の足元との距離が読めないまま旋回する**ことになる。加えて recovery は「詰まった＝周囲が近い」状況でしか発火しないため、**部屋では「人が近くにいる」場面と相関しやすい**。
- **したがって抑止の手法（behavior_server / BT で Spin・BackUp を抑える＝F-5-4）はそのまま生存**し、**どの条件で抑えるか**（通路内 → 人の近傍 / recovery 全般）を Slice 1 で再設計する。**L1 反射（collision_monitor）と混同しない**——これは recovery 挙動の設計であり、反射停止の代替ではない（[layer-annotation.md](../../.claude/rules/layer-annotation.md): 本項は **L2**（Nav2 behavior）の話）。

### G-11. Issue #519（Slice 1）への申し送り

**[Issue #519](https://github.com/lll-kkk-ryuya/miniature-warehouse-robotics/issues/519) の Slice 1（F-5 の params 実装）は継続**する（footprint polygon 採用は部屋でも生存＝G-2）。ただし**着手前に本 doc の G 系列（G-1〜G-12）を必読**とし、次の差分を前提にすること: **F-5-3 の具体値（inflation ≈0.125-0.126 / ±14mm 低コスト帯）は 280mm 通路由来＝ジオラマ限定で無効**（手法＝内接 0.1157 基準への再調整のみ生存）・**F-5-4（Spin recovery 抑止）は発火条件が「通路内で回転不可」から「人の脚の横で回転しない」へ差し替わる**（G-10）・**G-7 の config 二重化（OQ-22）が Slice 1 の最初のステップ**。

### G-12. 本追補の関連リンク（双方向）

- **決定正本**: [ADR-0009](../adr/0009-m1-room-scale-operation.md)（末尾【2026-08-18 追補②】に本節と対になる Open 追加項目）
- 安全論証側: [mode-x-er/09](../mode-x-er/09-hand-raise-summon.md) 末尾【2026-08-18 追補②】R-7〜R-9（召喚レグの snap 免除・C-3 前提・scan 面の実論拠）
- CURRENT 実体（impl-target は行 pin せずキーで指す＝[session-orchestration.md §8](../../.claude/rules/session-orchestration.md)）: `nav2_params.yaml` の両 costmap `robot_radius` / `FollowPath.CostCritic.consider_footprint` / `collision_monitor.yaml` の `PolygonStop.radius` / `config/warehouse.base.yaml` の `locations` / `tests/unit/test_known_locations_navigable.py` の内接ゲート

### G-13. G-2 表の補完: F-5-5（対象 distro = Humble 整合）

G-2 の二分表は F-5-1/2・F-5-3・F-5-4・F-5-6 を挙げたが、**F-5-5（対象 distro = [ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md) Humble 整合。MPPI CostCritic 構成は Jazzy→Humble で書き換え不要＝F-1 の裏取り済）を落としていた**。**F-5-5 は環境非依存＝生存**する——distro の選択は車体（ROSMASTER M1 の閉ソース driver）と Isaac ROS 側の制約から出ており、走行環境がジオラマか部屋かに一切依存しない。したがって Slice 1 は**部屋でもそのまま Humble 前提で実装**してよい（G-11 の申し送りに含む）。
