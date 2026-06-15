# 環境スパイク結果（doc16 §10）— **GO**

実行日: 2026-05-30 / ホスト: MacBook Pro M4 16GB, macOS, Docker 29.3.1 (desktop-linux)

`tiryoh/ros2-desktop-vnc:jazzy`（ARM64）上で **headless `gz sim`（Gazebo Harmonic）+ LiDAR
センサ + `ros_gz_bridge`** が成立。Phase 0.5 は **Mac 単体で完結可能**。退避（Linux/x86・
クラウド GPU）は不要。可視化は RViz2 想定（gz GUI は不使用）。

## 結果: GO（全基準クリア・headless・`--memory=6g` で OOM なし）
| 項目 | 結果 |
|---|---|
| Image | `tiryoh/ros2-desktop-vnc@sha256:47c24611686a3bc5729676277485fb4040be56dcae64c4e6aa0740890827508d` (arm64/linux, 8.3GB) |
| Docker | 29.3.1, context desktop-linux |
| gz | Gazebo Sim **8.11.0**（Harmonic / gz-sim8） |
| ros_gz | `ros-jazzy-ros-gz` **1.0.22-1noble** |
| LiDAR path | **A: `gpu_lidar` + ogre2、software GL（llvmpipe）/ EGL headless（`--headless-rendering`）**。ogre1 フォールバック(B)不要 |
| TCC bind-mount | OK（`~/Desktop` worktree を `docker run -v` で書込/読出可） |
| `/bot1/scan` | **~9.7 Hz**、ranges 非空（障害物 3.0m に対し ~2.83–2.92m の有限値） |
| `/bot1/odom` | **~29 Hz** |
| `/bot1/cmd_vel` → 移動 | linear.x=0.2 m/s(≤0.3 cap) を ~3.5s → odom x: **0 → 0.965 m** |
| メモリ | sim+bridge+検証 同時で **1.02 GiB / 6 GiB (17%)**、OOM-kill なし |

## 再現
`ws/src/warehouse_sim/spike/run_spike.sh {setup|probe|verify A|clean}`
（`min_lidar.sdf` + `config/bridge.yaml`、証跡は `logs/`）。
※ `verify` は手動の `docker exec -d`（sim/bridge を detached 起動）で実証。`run_spike.sh verify`
の `set -e` ＋ detached exec は要修正（GO-path で対応）。

## メモ（GO-path 実装へ持ち越し）
- SDF の `<gz_frame_id>` は当 sdformat で未定義警告 → 実 URDF では正規の frame 指定方式で
  `frame_id=bot{n}/lidar_link` を確定する。
- scan は 360 samples / 1°（実機 MS200 0.4°/900pts のダウンサンプル想定、R-43）。
- 本結果は Mac Docker 上の**ロジック成立性**の確認。周期実測は Jetson 段階2（doc16 §11:214）。

## 結論
**#8 nav-traffic を解錠**（critical-path ゲート通過）。本実装ブランチ `feat/sim-gazebo` の
URDF/world/launch/tests を解放。
