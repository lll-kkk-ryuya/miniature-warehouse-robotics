# warehouse_llm_bridge — 司令官LLMサイクル・排他制御(A+B-3)・キャラLLM

- **担当トラック / ブランチ**: bridge / `feat/llm-bridge`
- **Phase**: 0.5→3
- **ビルド**: ament_python
- **ノード**: llm_bridge
- **編集境界**: このパッケージ配下のみ。共有契約 `warehouse_interfaces` は変更不可（`.claude/rules/parallel-workflow.md` §4）。
- **消費する契約**: StateStore→Situation / Hermes 応答
- **生産する契約 / トピック**: Command 実行・GenStore(current_gen)・/llm/reasoning
- **依存**: warehouse_interfaces（契約は warehouse_interfaces のみ経由・他トラック内部を import しない）
- **テスト**: 偽トピック / 偽 state.json で独立検証（doc16 §11）。安全機構はユニットテスト必須。Ruff(py312/line100) + pytest 緑を維持。
- **設計**: docs/architecture/03・16・17、各トラック設計ドキュメント参照。

> これは #1 契約凍結が用意した雛形。`main()` はスタブ。実装で置き換える。
