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
- **凍結リンク名**（doc09 TFツリー / doc16 §9）: `base_link` / `lidar_link` / `imu_link` / `wheel_{front,rear}_{left,right}` / `camera_link` / `gnss_link`
- **`camera_link`**（HP60C・doc23 §4 `docs/architecture/23-perception-and-localization.md:124`・§5-2 `:157-160`）: **名前のみ凍結**。URDF の body/joint は取付実測待ちで `PENDING_URDF_LINKS` に列挙（unit test が「pending は xacro に無いこと」を pin）。**光学 frame 名は未凍結**（doc09 OQ-4 `docs/mode-x-er/09-hand-raise-summon.md:184`）ゆえ `FROZEN_FRAME_IDS` に camera エントリは無い
- **`gnss_link`【2026-09-16 追加・additive contract】**（RTK GNSS アンテナ・屋外）: `FROZEN_LINK_NAMES` **末尾**へ追加（既存名の位置は不変＝additive-first `.claude/rules/parallel-workflow.md` §7.2）。TF `bot{n}/base_link → bot{n}/gnss_link` は `robot_state_publisher`（URDF static）が出す（`docs/mode-outdoor/03-localization-gnss-and-ekf.md:35`・§8 `:207`）。**名前のみ凍結**＝URDF の body/joint はマスト取付**実測待ち**で `PENDING_URDF_LINKS` に列挙（マストは 450〜600 mm の幅・上端プレートは非常停止ボタンと共用・配置自体が `# TODO(設計)`＝`docs/mode-outdoor/06-hardware-delta-and-base-selection.md:182` / 同 `:77`）。**`camera_link` と違い `FROZEN_FRAME_IDS` に `gnss` エントリを持つ**: `03:54` が NavSatFix `header.frame_id` = `gnss_link` の一致を要求する（`navsat_transform_node` はこの frame の TF でアンテナのレバーアームを補正し、名前が食い違うと補正が**無言で**外れる）
- **凍結 frame_id**: `/<ns>/scan`→`<ns>/lidar_link`、imu→`<ns>/imu_link`、odom→`<ns>/odom`（child `<ns>/base_link`）、gnss（NavSatFix）→`<ns>/gnss_link`
- `robot_dimensions.py`: 凍結名タプル + `ROBOT_RADIUS=0.075`(R-42) + `SPAWN_Z`（Python 単一ソース）

## 消費 (consume)
- なし（`warehouse_interfaces` も不使用 — 寸法/名前は自パッケージ単一ソース）

## 前提・未確定 (TODO)
- `# TODO(Phase 1 実測)` 全ハードウェア寸法は暫定（body ~150mm: R-04、`ROBOT_RADIUS=0.075`: R-42）。xacro property と `robot_dimensions.py` を同期（unit test がドリフト検査）
- frame 名は doc09 準拠の `lidar_link`（キックオフ例示の `laser` は不採用）
- `# TODO(R-43)` sim lidar は 360pts/1°（実機 MS200 0.4°/900pts のダウンサンプル想定）

> 雛形(#1)を実装で置換済（feat/sim-gazebo, 環境スパイク GO 後）。
