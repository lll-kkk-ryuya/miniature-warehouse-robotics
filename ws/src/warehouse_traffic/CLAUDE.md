# warehouse_traffic — TrafficManager IF（None=ModeA / Simple=ModeB）+ VirtualScan（相手ロボ注入）

- **担当トラック / ブランチ**: ros2 / `feat/nav-traffic`
- **Phase**: 0.5→3
- **ビルド**: ament_python
- **ノード**: traffic_manager
- **編集境界**: このパッケージ配下のみ。共有契約 `warehouse_interfaces` は変更不可（`.claude/rules/parallel-workflow.md` §4）。
- **消費する契約**: config traffic_mode / 両機位置
- **生産する契約 / トピック**: 待機/通路排他の指示（Mode A/B）
- **依存**: warehouse_interfaces（契約は warehouse_interfaces のみ経由・他トラック内部を import しない）
- **テスト**: 偽トピック / 偽 state.json で独立検証（doc16 §11）。安全機構はユニットテスト必須。Ruff(py312/line100) + pytest 緑を維持。
- **設計**: docs/architecture/03・16・17、各トラック設計ドキュメント参照。

> これは #1 契約凍結が用意した雛形。`main()` はスタブ。実装で置き換える。
