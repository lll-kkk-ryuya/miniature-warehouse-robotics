# ws/src — ROS 2 colcon ワークスペース

`16-repository-and-conventions.md` を正とするモノレポ構成。**ドメイン固有の ROS 2 パッケージは `ws/src/warehouse_*` に集約**し、`colcon build` 1コマンドでビルドできる状態を維持する。シミュレーション資産（URDF/world）もトップレベルには置かず、`warehouse_description` / `warehouse_sim` に収める。**唯一の例外が `eval_sdk`**（doc21）＝倉庫に依存しない再利用可能な評価コアであることを名前で示すため**意図的に非 `warehouse_*` 命名**（`ROS`/`warehouse_*` import ゼロ・pip 化可能）。

ESP32 ファームウェア（PlatformIO、colcon 対象外）はリポジトリルートの `firmware/`、デプロイ/設定資産は `deploy/` `config/` に置く。

## パッケージ一覧（doc16 §2）

| パッケージ | ビルド | 責務 | Phase |
|-----------|--------|------|-------|
| `warehouse_interfaces` | ament_python | 契約コード化: pydantic schemas / Store IF / 共有パス（Phase4 で .msg 導入時に ament_cmake へ） | 0.5 |
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
| `warehouse_rmf_adapter` | ament_python | Mode C 案A EasyFullControl Fleet Adapter（offline core: routing/namespacing/single-writer） | 3 |
| `eval_sdk` | ament_python | ドメイン非依存 embodied-AI 評価コア（`seed`/`tracer`/`sink`/`stats`/`cost`）。**意図的に非 `warehouse_*`**＝ROS/warehouse 依存ゼロ・langfuse は optional pip extra（doc21） | 0.5→4 |

> 各パッケージは `package.xml` / `setup.py` を備えて実体化済み（`colcon build` 対象）。Phase 4 で `warehouse_interfaces` に構造化 `.msg` を導入する際に同パッケージのみ ament_cmake へ移行（doc16 §2/§3）。
> 生成物 `ws/build` `ws/install` `ws/log` は `.gitignore` 対象。
