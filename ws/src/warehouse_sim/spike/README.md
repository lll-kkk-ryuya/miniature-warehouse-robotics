# 環境スパイク（doc16 §10）— throwaway probe

最優先ゲート。**`tiryoh/ros2-desktop-vnc:jazzy`（ARM64）上で headless `gz sim`
（Gazebo Harmonic / gz-sim8）+ LiDAR センサ + `ros_gz_bridge` が成立するか**を最小構成で判定する。
ここは**使い捨ての検証コード**であり実機能ではない（実機能は `warehouse_sim` / `warehouse_description` 本体）。

成立（GO）すれば Phase 0.5 を Mac 単体で完結できる。不成立（NO-GO）なら Linux/x86 または
クラウド GPU の Gazebo に退避（**Isaac ではない** — Isaac は Phase 5）。可視化は RViz2。

## 成果物
- `worlds/min_lidar.sdf` — 地面 + 障害物 + 1台（bot1, 360° gpu_lidar, DiffDrive）。
  凍結名（`base_link`/`lidar_link`、frames `bot1/odom`・`bot1/base_link`・`bot1/lidar_link`、
  topics `/bot1/{scan,odom,cmd_vel}`）を使用 → bridge/sensor 設定が本実装へ流用可能。
- `worlds/min_lidar_cpu.sdf` — `setup` 時に `min_lidar.sdf` から sed 生成（render_engine ogre2→ogre1, Path B）。
- `config/bridge.yaml` — 3トピックの `parameter_bridge` 設定（型は doc03 凍結契約に一致）。
- `run_spike.sh` — 再実行可能ドライバ（setup / probe / verify A|B / clean）。
- `logs/` — 証跡（apt.log, render_probe_ogre2.log, sim_*.log, bridge_*.log）。git 追跡不要。

## 手順
```bash
cd ws/src/warehouse_sim/spike
./run_spike.sh setup        # pull（数GB）→ --memory=6g コンテナ → ros_gz 導入 → gz/bridge 版数
./run_spike.sh probe        # ★決定的: ogre2+EGL が software GL で初期化するか（最速の失敗ポイント）
# probe ログで分岐:
./run_spike.sh verify A     # 成功: ogre2（min_lidar.sdf）で /scan・/odom・/cmd_vel を検証
./run_spike.sh verify B     # ogre2 不可: ogre1 フォールバック（min_lidar_cpu.sdf）で検証
./run_spike.sh clean        # コンテナ削除
```

## 判定基準
**GO（全て成立・headless・`--memory=6g` で OOM-kill なし）**:
1. TCC bind-mount 書込/読出 OK。
2. `gz sim` Harmonic（gz-sim8）が tiryoh イメージ内で headless 起動。
3. `ros_gz_bridge` が3トピックを凍結型で橋渡し。
4. `/bot1/scan` が ~目標レートで publish、ranges が非空（all-inf でない）。Path A/B のどちらかを記録。
5. `/bot1/odom` が tick、`/bot1/cmd_vel`（≤0.2 m/s）publish で odom 位置が変化。
6. 6g で sim+bridge+検証を同時実行して OOM-kill なし。

**GO-degraded（#8 は解錠するが要注記）**: Path B（ogre1）のみ成立、または scan レートが目標を大きく下回る
（sim ロジックには許容。実時間精度は Jetson 段階2 へ送る = doc16 §11:214）。

**NO-GO**: gpu_lidar（A）も ogre1（B）も headless で非空 `/scan` を出せない／6g で回避不能な OOM／
TCC が回避不能。→ 退避先（Linux/x86 or クラウド GPU Gazebo、可視化 RViz2、Isaac でない）を文書化し #8 に通知。

## GO/NO-GO 記録テンプレート（Issue #7 へコメント。先頭タグ必須）
```
[worktree: mwr-sim-gazebo | branch: feat/sim-gazebo | track: #7]

SPIKE RESULT: GO | GO-degraded | NO-GO
- Image     : tiryoh/ros2-desktop-vnc:jazzy @<digest> ARM64 / Docker <ver> desktop-linux
- gz / ros_gz: Harmonic gz-sim8 <ver> / ros-jazzy-ros-gz <ver>
- LiDAR path : A(ogre2/llvmpipe) | B(ogre1) | none
- /bot1/scan : <hz>, ranges 非空（サンプル添付） ; /bot1/odom : <hz> ; cmd_vel→odom 変位: yes/no
- Memory     : --memory=6g --memory-swap=6g で OOM-kill なし
- 結論       : #8 nav-traffic を解錠 / 退避先=<...>
```
