# ws/src — ROS 2 colcon ワークスペース

`16-repository-and-conventions.md` を正とするモノレポ構成。**全 ROS 2 パッケージは `ws/src/warehouse_*` に集約**し、`colcon build` 1コマンドでビルドできる状態を維持する。シミュレーション資産（URDF/world）もトップレベルには置かず、`warehouse_description` / `warehouse_sim` に収める。

ESP32 ファームウェア（PlatformIO、colcon 対象外）はリポジトリルートの `firmware/`、デプロイ/設定資産は `deploy/` `config/` に置く。

## パッケージ一覧（doc16 §2）

| パッケージ | ビルド | 責務 | Phase |
|-----------|--------|------|-------|
| `warehouse_interfaces` | ament_cmake | カスタム msg/srv 定義 | 0.5 |
| `warehouse_bringup` | ament_python | launch・config 単一ソース | 0.5 |
| `warehouse_description` | ament_python | minicar URDF/xacro・meshes | 0.5 |
| `warehouse_sim` | ament_python | Gazebo world・ros_gz_bridge | 0.5 |
| `warehouse_state` | ament_python | State Cache Node | 0.5 |
| `warehouse_safety` | ament_python | Emergency Guardian・twist_mux | 0.5 |
| `warehouse_traffic` | ament_python | TrafficManager IF（None/Simple）+ VirtualScan | 0.5→3 |
| `warehouse_teleop` | ament_python | キーボード teleop | 1 |
| `warehouse_nav2_bridge` | ament_python | Mode A/B: REST→BasicNavigator | 0.5 |
| `warehouse_llm_bridge` | ament_python | LLM Bridge Node（司令官+キャラ） | 0.5→3 |
| `warehouse_mcp_server` | ament_python | Warehouse MCP Server（7ツール+Policy Gate+gen_id） | 0.5 |
| `warehouse_orchestrator` | ament_python | KPI Collector・分析 | 0.5→4 |

> 各パッケージは現状 README プレースホルダのみ。Phase 0.5 以降に `package.xml` / `setup.py`（または `CMakeLists.txt`）を追加して実体化する。
> 生成物 `ws/build` `ws/install` `ws/log` は `.gitignore` 対象。
