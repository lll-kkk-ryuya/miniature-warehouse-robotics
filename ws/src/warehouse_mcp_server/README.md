# warehouse_mcp_server

- **ビルド**: ament_python
- **責務**: Warehouse MCP Server（7ツール + Policy Gate + gen_id 検証）。Hermes Gateway の stdio 子プロセス（`python -m warehouse_mcp_server`、Hermes ネイティブ/外部 MCP client 用）。**commander サイクルでは `warehouse_llm_bridge` が `WarehouseTools().dispatch` を同一トラック in-process 呼出し（S2-PR2 HALF B / #81 / doc08:166-168）**。
- **主担当**: bridge / **Phase**: 0.5

> doc16 §2 準拠。rclpy 非依存（純 Python + MCP SDK）。Policy Gate は known_locations/battery/stale/重複/rate-limit を検証（速度は検証しない＝Layer 0/Nav2 の責務）。Mode A/B は受理された motion tool を Nav2 Bridge REST へ転送（`nav2_client.py`・受理時のみ・R-26、`docs/mode-a/12a-integration-mode-a.md:198-363`）。
