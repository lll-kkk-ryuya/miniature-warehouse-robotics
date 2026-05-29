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

> これは #1 契約凍結が用意した雛形。`main()` はスタブ。実装で置き換える。
