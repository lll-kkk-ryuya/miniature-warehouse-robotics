# Humble Guardian live test — `scan_stale`（Gazebo なし・偽 sensor publisher）

> **目的**: ROS 2 **Humble** の rclpy 上で Emergency Guardian（L1・`warehouse_safety`）の `scan_stale`
> 経路（[doc12 末尾【2026-09-16 追補】(3)](../../../docs/architecture/12-infrastructure-common.md)）を
> **実ノードとして**動かし、`/bot1/scan` の途絶で estop が出ること・再開で解除されることを実測する。
> Gazebo（Humble）は [ADR-0008 §Open](../../../docs/adr/0008-ros2-distro-humble-for-rosmaster-m1.md)
> （Fortress の ARM64 再スパイク待ち）で repo に無いため、sensor は `ros2 topic pub` の偽 publisher で代替する。
> Guardian は topic を購読するだけなので、検証対象（QoS 適合・鮮度判定の timing・event JSON・
> `stop_state` 伝播・level 解除）は Gazebo の有無に依存しない。
>
> layer: **L1**（Guardian）。dev tooling（[deploy/dev/README.md](../README.md)）。実機・Gazebo は起動しない。

## 前提

- Docker（arm64 可）。ベースイメージ `ros:humble-ros-base`（Docker Hub 公式・arm64 圧縮 264 MB）。
- repo は **read-only** で `/ws` にマウントし、`colcon` の build/install/log base は**コンテナ内 `/opt/mwr`** に出す
  （host の `ws/build`・`ws/install`＝Jazzy cockpit が再利用する成果物を汚さない）。
- Guardian の config は `WAREHOUSE_CONFIG_DIR=/ws/config WAREHOUSE_ENV=dev`（secrets 不要・`.env` 不読）。

## 手順（すべて host で実行・repo ルートから）

```bash
docker pull ros:humble-ros-base
docker run -d --name mwr-humble-guardian \
  -v "$PWD:/ws:ro" -v "$PWD/deploy/dev/humble-guardian-livetest:/scratch/live" \
  ros:humble-ros-base sleep infinity
docker exec mwr-humble-guardian bash -lc '
  apt-get update -qq && apt-get install -y -qq python3-pip &&
  pip3 install -q "pydantic>=2" pyyaml &&
  source /opt/ros/humble/setup.bash && cd /ws/ws &&
  colcon --log-base /opt/mwr/log build --symlink-install --packages-up-to warehouse_safety \
    --build-base /opt/mwr/build --install-base /opt/mwr/install'
docker exec mwr-humble-guardian bash /scratch/live/harness.sh
```

`harness.sh` はログを `/scratch/live/{harness,recorder,guardian}.log` に書く（上記マウントなら本ディレクトリに出る。
**結果ログはコミットしない**＝`results-2026-09-16.log` のように日付付きで残す場合のみ手動でコピーする）。
再実行は `docker start mwr-humble-guardian` → `docker exec ... harness.sh`。

## Phase と期待値（`harness.sh` の順）

| Phase | 操作 | 期待（doc12 追補 (3) の規則） |
|---|---|---|
| P0 | Guardian 起動 → `/bot1/odom` のみ 20 Hz（scan 無し） | odom 受信（bot 生存の証人）後に **`scan_stale`**（detail `scan_age: null`）・prio100 zero Twist 20 Hz・`stop_requested=true`。odom 前は無音 |
| P1 | `/bot1/scan` 10 Hz 開始 | 次 tick で解除（`stop_requested=false`・zero 停止） |
| P2 | 5 s 保持 | event なし |
| P3 | scan publisher を kill | **最終受信から 1.0 s 超の最初の tick**で `scan_stale`（`scan_age` ≈ 1.0〜1.05 s） |
| P4 | scan 再開 | 解除。全 phase で bot2（不在）は無音 |

`recorder.py` は `/emergency/event`（JSON）・`/bot1/cmd_vel/emergency`（zero stream の開始/終了と本数）・
`/bot1/stop_state`（`stop_requested` の遷移）・`/bot2/cmd_vel/emergency`（出たら UNEXPECTED）を壁時計付きで記録する。

## 結果（2026-09-16・Humble 1.1.20 / rclpy・`ros:humble-ros-base`・M4 Mac Docker arm64）

- P0: odom 開始 marker の 1.04 s 後に `scan_stale`（`scan_age: null`）→ zero Twist 開始・`stop_requested=true`
  （遅れは `ros2 topic pub` CLI の起動時間≈1 s で、Guardian 側は odom 初回受信の次 tick）。odom 前は無音。
- P1: scan 開始 marker の 1.08 s 後に `stop_requested=false`・zero stream 終了（4.05 s で 81 本 ≈ 20 Hz）。
- P2: event なし。
- P3: kill の 0.79 s 後（**最終 scan 受信からは `scan_age` = 1.0347 s**）に `scan_stale`・zero stream 再開・`stop_requested=true`。
- P4: 再開 marker の 0.73 s 後に解除（zero stream 4.95 s で 99 本 ≈ 20 Hz）。bot2 の Twist/event は 0 件。
- 生ログ: [results-2026-09-16.log](results-2026-09-16.log)（`harness.log`＋`recorder.log`＋`guardian.log` の連結）。
  末尾の `ESTOP_STREAM_START` は teardown 順（scan を先に kill → Guardian に SIGINT）による正常な再発火。

## 未実施・注意

- **Gazebo（Humble）**: 未実施（ADR-0008 §Open）。`deploy/dev/Dockerfile` は Jazzy ベース（Gazebo Harmonic）。
- **実機（ROSMASTER M1）**: 未実施。T-mini Plus driver は未配線で、配線時は Guardian が購読する契約 topic
  `/bot1/scan`（doc03:78）の namespace に publish／remap すること（素の `/scan` だと odom 到着後に
  `scan_stale` で止まり続ける＝fail-closed）。配線順序（LiDAR→`/bot1/scan` を `odom_enabled: true` より先）の正本 = [docs/mode-m1/03 末尾【2026-09-16 追補②】](../../../docs/mode-m1/03-joystick-teleop-bringup.md)。
- `stop_overlay_enabled`（m1_driver・既定 false）が OFF の構成では、`scan_stale` は Nav2 経路（twist_mux prio100）
  だけを止め、twist_mux を経ない joystick 直 publish は止まらない（mode-m1/05 §4 の設計どおり）。
