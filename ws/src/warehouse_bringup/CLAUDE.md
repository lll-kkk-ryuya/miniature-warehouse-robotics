# warehouse_bringup — launch + config の単一ソース（Nav2/AMCL/SLAM/twist_mux/footprint/速度上限）

- **担当トラック / ブランチ**: ros2 / `feat/nav-traffic / skeleton`
- **Phase**: 0.5
- **ビルド**: ament_python
- **ノード**: （ノードなし: データ/launch パッケージ）
- **編集境界**: このパッケージ配下のみ。ただし `launch/bringup.launch.py`（統合ルート）は **skeleton 所有**（doc16:183）＝nav-traffic は触らず、新規 launch を別ファイルで追加し skeleton が include する（#75）。共有契約 `warehouse_interfaces` は変更不可（§4）。
- **消費する契約**: 起動時のみ — nav2_*（amcl/controller/planner/behaviors/bt_navigator/map_server/lifecycle_manager）・twist_mux・`warehouse_traffic`（virtual_scan）。コード import はしない（launch 合成のみ）。
- **生産する契約 / トピック**: 全ノードの launch / config（`config/` 1ファイル1責務, doc16:118-119）。実体（#68）:
  - `config/nav2_params.yaml` — Nav2 MPPI 全ブロック（DWB→MPPI, R-49）。footprint 0.075 / vx_max ≤0.3 / obstacle_layer `scan virtual_scan`。`<robot_namespace>` を launch の ReplaceString で per-bot 置換。
  - `config/twist_mux.yaml` — emergency(prio100) > nav2(prio10)（#40 で safety から移設）。
  - `launch/nav2_bringup.launch.py` — 共有 map_server + per-bot Nav2 スタック + twist_mux + VirtualScan×2（`traffic_mode==open-rmf` で gating off）。`bringup.launch.py` は未編集。
- **依存**: launch-time exec_depend に nav2_* / twist_mux / warehouse_traffic（package.xml）。他トラック内部を import しない。
- **テスト**: config は YAML parse + 値検証、launch は colcon build + ノード import（py3.12 / tiryoh）で text 検証。実 nav2 launch / 2台 Gazebo E2E は **#67**（前提: sim `/clock`+map #76・launch 合成 #75・コンテナ nav2 導入）。Ruff(py312/line100) + pytest 緑を維持。
- **設計**: docs/shared/09（Nav2/AMCL/コストマップ）・mode-a/11a（TrafficManager/VirtualScan）・architecture/03・16（§5 config 単一ソース・§9 所有）・17。

> #1 雛形（空の `bringup.launch.py`）に #68 が config/launch を追加。`bringup.launch.py` 本体の wiring は skeleton 所有のため #75 で hand-off。
