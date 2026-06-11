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
- `errors.py` — `Nav2BridgeError(error_code, detail, http_status)`（doc12a:345-363 の 6 コード ＋ `INVALID_GOAL`(400)＝#223 座標ゴール拡張: destination/goal の両方/両無 or 不正座標）。
- `app.py` — `create_app(core)`：FastAPI 5 endpoint（**fastapi は lazy import**＝core ユニットは非依存）。`Nav2BridgeError`→ `{status,error_code,detail}` + HTTP status を 1 つの exception handler で写像。`navigate` body は `destination`(名前) ＋ additive optional `goal`[x,y] のいずれか。
- `head_on_injector.py` — `HeadOnInjector`（**純 / no rclpy / no FastAPI / cross-track import なし**）：#223 座標スワップ直列化器。`navigator`（duck-typed `.navigate(robot,*,goal=)`）＋ `arbiter`（`SimpleTrafficManager` 形＝`submit_task`/`release_aisle`/`aisle_locks`, traffic_logic.py:159,165,190・**注入・import しない**）を受け、`head_on_goals` DATA を route_A 排他で直列化（先着 navigate→後着は入口待機→`on_goal_reached` で解放→後着 dispatch、doc11a:446/453/§9.3）。
- `nav2_bridge.py` — `BasicNavigatorBackend`（実 `BasicNavigator` ×bot, **lock 直列化**）+ rclpy ノード（200ms timer→ goal_result publish）+ `main()`（rclpy thread + uvicorn main、doc12a:200-219）。**runtime のみ**（unit 非対象）。**座標ゴールは backend `Pose=(x,y)` をそのまま通る（yaw 非対応のまま・`orientation.w=1.0`）**。

## 提供 (produce)
- **REST API** `http://127.0.0.1:8645`（doc12a:222-343。loopback=co-located MCP, safety.md）:
  - `POST /api/v1/navigate {robot, (destination | goal[x,y|x,y,yaw]), via?}` / `POST /api/v1/wait {robot,duration}` / `POST /api/v1/stop {robot}` / `GET /api/v1/status/{robot}` / `GET /health`
  - **#223 additive**: `navigate` は名前 `destination` に加え inline 座標 `goal`（x,y[,yaw]）を受理（XOR・両/両無は `INVALID_GOAL` 400）。座標は `_coord` を通さず backend へ直送・**yaw は drop**（backend `Pose=(x,y)`）。名前ゴールの応答は従来どおり（後方互換）、座標ゴールの応答は `{...,"destination":null,"goal":[x,y]}`。
- **lib**: `head_on_injector.HeadOnInjector`（#223）＝座標スワップ直列化器（produce 詳細は上記モジュール構成）。`scripts/slice3_inject_swap.sh`（live operator wrapper：`head_on_goals` DATA 導出→REST `/navigate` 座標 body→status-poll 直列化）。
- topic: `/nav2_bridge/goal_result`（std_msgs/String JSON `{robot,task_id,result}`）→ State Cache（doc12a:384-392）

## 消費 (consume)
- 契約: `warehouse_interfaces.config.load_config`（`robots` / 凍結 `locations`{x,y} / `nav2_bridge.base_url` の port）。**契約変更なし**（座標は config 値・凍結契約に座標は足さない）。**#223 の座標 `goal` 引数は本パッケージ自身の API の additive optional 引数**＝凍結 `warehouse_interfaces.schemas` / `KNOWN_LOCATIONS` に座標キーを足さない（contract ラベル不要）。injector は `head_on_goals` を **DATA で受ける**（`warehouse_sim` を import しない・cross-track 禁止）／`arbiter` も注入（`warehouse_traffic` を import しない）。
- 速度上限 `MAX_LINEAR_VELOCITY=0.3` は **Nav2 params(`nav2_params.yaml` vx_max:0.3, nav-traffic 所有) + Layer0 が強制**。本パッケージは位置ゴールのみ扱い、速度を持たない。
- pip: `fastapi` + `uvicorn`（lazy・setup.py 宣言。CI pytest は純 core のみ＝非依存）。ROS: `nav2_simple_commander` / `geometry_msgs` / `std_msgs`（package.xml exec_depend、runtime のみ）。

## テスト
- `tests/unit/test_nav2_bridge_core.py`（**py3.12**）：5 endpoint・全エラーコード（INVALID_ROBOT/LOCATION/VIA/DURATION・ALREADY_NAVIGATING 409・NAV2_NOT_READY 503）・stop 冪等・status・health・`poll_results`（nav 完了/失敗・wait 期限）・`from_config` ＋ **#223 座標ゴール**（accept / yaw drop / via 前置 / 両|両無=INVALID_GOAL / 不正座標 / busy / not-ready / `@pytest.mark.safety` 位置のみ・速度なし R-26）。`FakeNavigatorBackend` + 注入 clock で **ROS/FastAPI 非依存**。
- `tests/unit/test_head_on_injector.py`（#223）：route_A 直列化（先着 dispatch・後着 waiting → `on_goal_reached` で解放→後着 dispatch）・非競合は両 dispatch・yaw drop・`@pytest.mark.safety` 位置のみ（速度なし R-26）。`FakeArbiter`（`SimpleTrafficManager` 形・import せず）＋ 実 `Nav2BridgeCore`+`FakeNavigatorBackend`。
- `tests/e2e/test_min_separation_harness.py`（#223）：`min_separation`/≥0.15m gate を host 検証（負例＝同時隘路進入 ~0.07m で gate FAIL＝teeth あり）。live 計測は **user-docker-gated**（`WAREHOUSE_MINSEP_STREAMS` 指定時のみ）。
- ruff(py312/line100/double-quote) + ruff format 緑。**colcon build / launch-introspection は tiryoh コンテナ（host py3.7 不可）＝user-gated**。

## 前提・未確定 (TODO / seam)
- **part 2（実 tool dispatch）**: `warehouse_llm_bridge` の log スタブ executor を実 backend に差し替え。**#81 で同一トラック import が許可**（ci.yml track-aware）されたため in-process `WarehouseTools.dispatch` 注入で確定・採用（#104）。キャンセル手段は **#54 解決**（明示 Hermes `/v1/runs/{id}/stop` 撤回・Layer A は client-side cancel のみ・主担保 B-3+C）に整合。
- **part 3（MCP→nav2_bridge 配線）**: `warehouse_mcp_server` の `dispatch_task`/`cancel_task` が Policy Gate accept 後に nav2_bridge REST（`/navigate`・`/stop`）へ POST（HTTP client を注入しテストは stub）。境界に `warehouse_mcp_server` 追加（承認済）。**安全クリティカル経路**のため part 1 とは別 commit で慎重に。
- **Phase 3 実機 verify**: 2× `BasicNavigator` 同居の namespace/singleton 危険（doc12:459）と FastAPI↔rclpy スレッド競合（backend lock で緩和）、readiness（`waitUntilNav2Active`）、feedback の progress/eta は **実 sim/実機で確認**（#67 E2E）。
- **via/WAYPOINTS**: doc12a:351 の WAYPOINTS 辞書は未凍結。現状 `via` は凍結 `locations` で検証。専用 waypoint 契約が要るなら contract PR（発明しない）。
- **#223 残（DEFERRED / 暫定）**: ① **≥0.15m live 計測は user docker PENDING**（Gazebo 物理＝tests/e2e README:43,117-122。harness は WIRING のみ・`WAREHOUSE_MINSEP_STREAMS` で live-recorded stream を gate）。② **yaw drop 暫定**（backend `Pose=(x,y)`・`orientation.w=1.0`。yaw 活用は `_pose` の quaternion 化＝別変更）。③ **座標範囲チェックなし**（map 範囲は本パッケージが所有しない＝Nav2 planner が到達不能を弾く。発明しない）。④ **doc12a の REST 仕様表は名前 `destination` のみ記載＝座標 `goal` variant が未記載**（doc12a は本レーンの編集境界外＝surface 留め・follow-up doc PR 候補。設計意図は doc11a:455 / e2e README に既出）。
