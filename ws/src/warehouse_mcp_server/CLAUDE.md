# warehouse_mcp_server — 7ツール + Policy Gate + gen_id 検証（Hermes stdio 子・純Python）

- **担当トラック / ブランチ**: bridge / `feat/llm-bridge`
- **Phase**: 0.5
- **ビルド**: ament_python
- **ノード**: warehouse_mcp_server
- **編集境界**: このパッケージ配下のみ。共有契約 `warehouse_interfaces` は変更不可（`.claude/rules/parallel-workflow.md` §4）。
- **消費する契約**: MCP tool 呼出 / warehouse_interfaces(schemas,locations,GenStore)
- **生産する契約 / トピック**: Nav2 Bridge への検証済みコマンド
- **依存**: warehouse_interfaces, pydantic（契約は warehouse_interfaces のみ経由・他トラック内部を import しない）
- **テスト**: 偽トピック / 偽 state.json で独立検証（doc16 §11）。安全機構はユニットテスト必須。Ruff(py312/line100) + pytest 緑を維持。
- **設計**: docs/architecture/03・16・17、各トラック設計ドキュメント参照。

> これは #1 契約凍結が用意した雛形。`main()` はスタブ。実装で置き換える。
