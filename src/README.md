# src/ — ROS 2 ワークスペース

colcon ビルド対象の ROS 2 パッケージ群。設計ドキュメントは `docs/` を参照。

ビルドは Docker（`tiryoh/ros2-desktop-vnc:jazzy`）内 or Jetson 上で `colcon build`。

## パッケージ構成と責務

| パッケージ | 責務 | 主担当agent | 設計ドキュメント |
|-----------|------|------------|-----------------|
| `warehouse_msgs` | 共通 msg/srv/action 定義 | ros2-developer | `docs/architecture/03-software-architecture.md` |
| `warehouse_bringup` | launch・統合起動・systemd・config集約 | ros2-developer | `docs/mode-a/12a-*`, `docs/mode-c/12c-*` |
| `warehouse_llm_bridge` | LLM Bridge Node（Hermes Gateway連携・Provider切替・situation JSON・排他制御） | llm-bridge-developer | `docs/architecture/08-llm-bridge-common.md`, `13-hermes-setup.md` |
| `warehouse_mcp_server` | Warehouse MCP Server（7ツール・Policy Gate・gen_id・gen_store） | llm-bridge-developer | `docs/architecture/15-mcp-platform.md` |
| `warehouse_safety` | Emergency Guardian（50ms）・twist_mux・State Cache | hardware-integrator / ros2-developer | `docs/architecture/12-infrastructure-common.md` |
| `warehouse_nav` | Nav2/SLAM/AMCL param・Multi-Robot Costmap Layer・MPPI | ros2-developer | `docs/shared/09-navigation-internals.md`, `docs/mode-a/11a-*` |
| `warehouse_traffic` | TrafficManager（none/simple）・RMF Fleet Adapter（open-rmf） | ros2-developer | `docs/mode-a/11a-*`, `docs/mode-c/11c-*` |
| `warehouse_orchestrator` | WO Bridge（REST↔ROS2）・KPI計測・可視化 | orchestrator-architect | `docs/architecture/06-implementation-phases.md` Phase 4 |

> ⚠️ 各パッケージは Phase 0.5 以降に `package.xml` / `setup.py`（ament_python）または `CMakeLists.txt`（ament_cmake）を追加して実体化する。現状は構成の骨組み（README プレースホルダ）のみ。
