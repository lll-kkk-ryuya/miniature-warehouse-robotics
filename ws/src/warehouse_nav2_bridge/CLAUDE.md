# warehouse_nav2_bridge — REST → Nav2 BasicNavigator（Mode A/B のアクション実行先）

- **担当トラック / ブランチ**: bridge / `feat/llm-bridge`
- **Phase**: 0.5（S2-PR2）
- **ビルド**: ament_python
- **ノード**: nav2_bridge（FastAPI :8645 + rclpy）
- **編集境界**: このパッケージ配下のみ。共有契約 `warehouse_interfaces` は変更不可（`.claude/rules/parallel-workflow.md` §4）。
- **設計正本**: docs/mode-a/12a-integration-mode-a.md:149-449（REST 仕様/エラーコード/200ms monitor）・docs/mode-a/08a-llm-bridge-mode-a.md:398-405・docs/architecture/16:72,187。

## モジュール構成（part 1 = nav2_bridge パッケージ）
- `core.py` — `Nav2BridgeCore`（**純 / no rclpy / no FastAPI**）：validate → backend.go_to、task_id 採番、`active_tasks`（threading.Lock）、`poll_results`（200ms 完了監視, 注入 clock で wait 期限も判定）。location→coord は**凍結 `locations`**（config==`warehouse_interfaces.locations.KNOWN_LOCATIONS`）。`via` は **WAYPOINTS 契約が未凍結のため同じ凍結 `locations` で検証**（doc12a:351 の WAYPOINTS 辞書は発明しない＝docs-first）。
- `backend.py` — `NavigatorBackend`(ABC) seam + `FakeNavigatorBackend`（テスト用）。core は rclpy/nav2 を import しない。
- `errors.py` — `Nav2BridgeError(error_code, detail, http_status)`（doc12a:345-363 の 6 コード）。
- `app.py` — `create_app(core)`：FastAPI 5 endpoint（**fastapi は lazy import**＝core ユニットは非依存）。`Nav2BridgeError`→ `{status,error_code,detail}` + HTTP status を 1 つの exception handler で写像。
- `nav2_bridge.py` — `BasicNavigatorBackend`（実 `BasicNavigator` ×bot, **lock 直列化**）+ rclpy ノード（200ms timer→ goal_result publish）+ `main()`（rclpy thread + uvicorn main、doc12a:200-219）。**runtime のみ**（unit 非対象）。

## 提供 (produce)
- **REST API** `http://127.0.0.1:8645`（doc12a:222-343。loopback=co-located MCP, safety.md）:
  - `POST /api/v1/navigate {robot,destination,via?}` / `POST /api/v1/wait {robot,duration}` / `POST /api/v1/stop {robot}` / `GET /api/v1/status/{robot}` / `GET /health`
- topic: `/nav2_bridge/goal_result`（std_msgs/String JSON `{robot,task_id,result}`）→ State Cache（doc12a:384-392）

## 消費 (consume)
- 契約: `warehouse_interfaces.config.load_config`（`robots` / 凍結 `locations`{x,y} / `nav2_bridge.base_url` の port）。**契約変更なし**（座標は config 値・凍結契約に座標は足さない）。
- 速度上限 `MAX_LINEAR_VELOCITY=0.3` は **Nav2 params(`nav2_params.yaml` vx_max:0.3, nav-traffic 所有) + Layer0 が強制**。本パッケージは位置ゴールのみ扱い、速度を持たない。
- pip: `fastapi` + `uvicorn`（lazy・setup.py 宣言。CI pytest は純 core のみ＝非依存）。ROS: `nav2_simple_commander` / `geometry_msgs` / `std_msgs`（package.xml exec_depend、runtime のみ）。

## テスト
- `tests/unit/test_nav2_bridge_core.py`（23 ケース, **py3.12**）：5 endpoint・全エラーコード（INVALID_ROBOT/LOCATION/VIA/DURATION・ALREADY_NAVIGATING 409・NAV2_NOT_READY 503）・stop 冪等・status・health・`poll_results`（nav 完了/失敗・wait 期限）・`from_config`。`FakeNavigatorBackend` + 注入 clock で **ROS/FastAPI 非依存**。ruff(py312/line100/double-quote) + ruff format 緑。

## 前提・未確定 (TODO / seam)
- **part 2（実 tool dispatch）**: `warehouse_llm_bridge` の log スタブ executor を実 backend に差し替え。**#81 で同一トラック import が許可**（ci.yml track-aware）されたため in-process `WarehouseTools.dispatch` 注入で確定・採用（#104）。キャンセル手段は **#54 解決**（明示 Hermes `/v1/runs/{id}/stop` 撤回・Layer A は client-side cancel のみ・主担保 B-3+C）に整合。
- **part 3（MCP→nav2_bridge 配線）**: `warehouse_mcp_server` の `dispatch_task`/`cancel_task` が Policy Gate accept 後に nav2_bridge REST（`/navigate`・`/stop`）へ POST（HTTP client を注入しテストは stub）。境界に `warehouse_mcp_server` 追加（承認済）。**安全クリティカル経路**のため part 1 とは別 commit で慎重に。
- **Phase 3 実機 verify**: 2× `BasicNavigator` 同居の namespace/singleton 危険（doc12:459）と FastAPI↔rclpy スレッド競合（backend lock で緩和）、readiness（`waitUntilNav2Active`）、feedback の progress/eta は **実 sim/実機で確認**（#67 E2E）。
- **via/WAYPOINTS**: doc12a:351 の WAYPOINTS 辞書は未凍結。現状 `via` は凍結 `locations` で検証。専用 waypoint 契約が要るなら contract PR（発明しない）。
