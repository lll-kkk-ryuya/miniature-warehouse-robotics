# warehouse_sim — Gazebo Harmonic world・ros_gz_bridge・sim 起動（ジオラマ寸法は単一定数）

- **担当トラック / ブランチ**: sim / `feat/sim-gazebo`
- **Phase**: 0.5
- **ビルド**: ament_python
- **ノード**: （ノードなし: データ/launch パッケージ）
- **編集境界**: このパッケージ配下のみ。共有契約 `warehouse_interfaces` は変更不可（`.claude/rules/parallel-workflow.md` §4）。
- **消費する契約**: warehouse_description
- **生産する契約 / トピック**: Gazebo 上の /bot{n}/odom,scan,cmd_vel
- **依存**: （rclpy のみ / なし）（契約は warehouse_interfaces のみ経由・他トラック内部を import しない）
- **テスト**: 偽トピック / 偽 state.json で独立検証（doc16 §11）。安全機構はユニットテスト必須。Ruff(py312/line100) + pytest 緑を維持。
- **設計**: docs/architecture/03・16・17、各トラック設計ドキュメント参照。

## 提供 (produce)
- topic（ros_gz_bridge 橋渡し済・doc03 契約）: `/bot{n}/odom`(nav_msgs/Odometry)、`/bot{n}/scan`(sensor_msgs/LaserScan, frame_id `bot{n}/lidar_link`)
- sub : `/bot{n}/cmd_vel`(geometry_msgs/Twist)
- topic: `/clock`(rosgraph_msgs/msg/Clock, GZ→ROS 単方向)。gz の sim time を bridge。`use_sim_time:=true` の全 Nav2 ノードが消費（無いと stall）。`bridge.py` `_CLOCK`。# TODO(#67): コンテナで `gz topic -l` 実確認（`/world/warehouse/clock` フォールバック, ros_gz #341）
- 静的占有マップ: `maps/map.pgm`(P5) + `maps/map.yaml`（resolution 0.01, origin world 角 `[0,0,0]`, 180×90, 壁+棚のみ占有・marker 除外）。Nav2 map_server が `nav2_bringup map:=<share>/maps/map.yaml` で消費（doc09:255-257）。`map_generator.py`（pure）で `layout` から生成・committ、再生成は `python3 -m warehouse_sim.map_generator`
- launch arg: `sim.launch.py` / `description.launch.py` の `use_sim_time`（default true, consumer `nav2_bringup.launch.py:187` と一致）。robot_state_publisher / bridge / rviz に thread
- launch: `sim.launch.py`（headless `gz sim -s -r`、bot1/bot2 spawn、ros_gz_bridge、`rviz:=true` 任意）
- `layout.py`: `WORLD_X=1.8`/`WORLD_Y=0.9` + 棚/通路/バース寸法（単一定数、Phase 5 Isaac 参照）
- `world_generator.build_world_sdf()` / `bridge.bridge_pairs()` / `map_generator.build_map()`（純関数・テスト可能）
- スパイク成果物 `spike/`（環境スパイク GO の再現コード・証跡）

## 消費 (consume)
- `warehouse_interfaces.config.load_config`（locations / robots — 座標/台数の単一ソース）
- `warehouse_description`: `robot_description`（spawn）、`robot_dimensions.SPAWN_Z`、凍結フレーム名

## 前提・未確定 (TODO)
- 環境スパイク= **GO**（gz-sim8 8.11 + gpu_lidar/ogre2 software GL + ros_gz_bridge, `--memory=6g`）。`spike/RESULT.md`
- `# TODO(Phase 2)` location 座標は暫定。軸対応: `loc.x→world +X(長辺1.8m)` / `loc.y→world +Y(短辺0.9m)`
- `# TODO(Phase 2)` 200mm ボトルネック通路は設計目標（`AISLE_BOTTLENECK_WIDTH`）。暫定座標では実ギャップが異なる
- `# TODO(R-43)` sim lidar 360pts/1°（実機 MS200 0.4°/900pts ダウンサンプル想定）
- 周期(50/100ms)は Mac Docker では非検証（ロジックのみ）。実測は Jetson 段階2（doc16 §11:212）

> 雛形(#1)を実装で置換済（feat/sim-gazebo, 環境スパイク GO 後）。
