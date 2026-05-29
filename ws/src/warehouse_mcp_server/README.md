# warehouse_mcp_server

- **ビルド**: ament_python
- **責務**: Warehouse MCP Server（7ツール + Policy Gate + gen_id 検証）。Hermes Gateway の stdio 子プロセス（`python -m warehouse_mcp_server`）
- **主担当**: bridge / **Phase**: 0.5

> doc16 §2 準拠。rclpy 非依存（純 Python + MCP SDK）。Policy Gate は known_locations/battery/stale/重複/rate-limit を検証（速度は検証しない＝Layer 0/Nav2 の責務）。
