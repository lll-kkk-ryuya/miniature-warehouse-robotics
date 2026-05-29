# warehouse_state

- **ビルド**: ament_python
- **責務**: State Cache Node（100ms周期、`/tmp/warehouse/state.json` への atomic write + `/state_cache/snapshot` トピック publish）
- **主担当**: bridge / **Phase**: 0.5

> doc16 §4 の共有ファイルパス規約に従う。ファイル(LLM Bridge/MCP が読む)＋トピック(キャラLLM)の2系統配信。
