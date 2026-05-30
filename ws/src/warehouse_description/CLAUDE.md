# warehouse_description — minicar の URDF/xacro・meshes（リンク名・センサ frame_id・footprint を固定）

- **担当トラック / ブランチ**: sim / `feat/sim-gazebo`
- **Phase**: 0.5
- **ビルド**: ament_python
- **ノード**: （ノードなし: データ/launch パッケージ）
- **編集境界**: このパッケージ配下のみ。共有契約 `warehouse_interfaces` は変更不可（`.claude/rules/parallel-workflow.md` §4）。
- **消費する契約**: —
- **生産する契約 / トピック**: robot_description（sim と実機が共有）
- **依存**: （rclpy のみ / なし）（契約は warehouse_interfaces のみ経由・他トラック内部を import しない）
- **テスト**: 偽トピック / 偽 state.json で独立検証（doc16 §11）。安全機構はユニットテスト必須。Ruff(py312/line100) + pytest 緑を維持。
- **設計**: docs/architecture/03・16・17・09(TFツリー)、各トラック設計ドキュメント参照。

## 提供 (produce)
- `robot_description`（URDF/xacro, sim+実機 共有）: `urdf/minicar.urdf.xacro`、`launch/description.launch.py`（namespace 毎に robot_state_publisher, `frame_prefix=<ns>/`）
- **凍結リンク名**（doc09 TFツリー / doc16 §9）: `base_link` / `lidar_link` / `imu_link` / `wheel_{front,rear}_{left,right}`
- **凍結 frame_id**: `/<ns>/scan`→`<ns>/lidar_link`、imu→`<ns>/imu_link`、odom→`<ns>/odom`（child `<ns>/base_link`）
- `robot_dimensions.py`: 凍結名タプル + `ROBOT_RADIUS=0.075`(R-42) + `SPAWN_Z`（Python 単一ソース）

## 消費 (consume)
- なし（`warehouse_interfaces` も不使用 — 寸法/名前は自パッケージ単一ソース）

## 前提・未確定 (TODO)
- `# TODO(Phase 1 実測)` 全ハードウェア寸法は暫定（body ~150mm: R-04、`ROBOT_RADIUS=0.075`: R-42）。xacro property と `robot_dimensions.py` を同期（unit test がドリフト検査）
- frame 名は doc09 準拠の `lidar_link`（キックオフ例示の `laser` は不採用）
- `# TODO(R-43)` sim lidar は 360pts/1°（実機 MS200 0.4°/900pts のダウンサンプル想定）

> 雛形(#1)を実装で置換済（feat/sim-gazebo, 環境スパイク GO 後）。
