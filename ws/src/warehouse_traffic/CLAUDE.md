# warehouse_traffic — TrafficManager IF（None=ModeA / Simple=ModeB）+ VirtualScan（相手ロボ注入）

- **担当トラック / ブランチ**: ros2 / `feat/nav-traffic`
- **Phase**: 0.5→3
- **ビルド**: ament_python
- **ノード**: `traffic_manager`（既定=薄い診断ラッパ／`scenario:=yield_aisle_a` で #125 yield デモ駆動）, `virtual_scan`（相手ロボ注入, bot 毎に1）
- **編集境界**: このパッケージ配下のみ。共有契約 `warehouse_interfaces` は変更不可（`.claude/rules/parallel-workflow.md` §4）。
- **依存**: `warehouse_interfaces` / `warehouse_description`（ROBOT_RADIUS 単一ソース・読取専用共有, parallel-workflow §2.1）/ `sensor_msgs` / `geometry_msgs`。他トラック内部は import しない。
- **テスト**: rclpy 非依存ロジック（`traffic_logic.py` / `virtual_scan_logic.py`）を `tests/unit/test_traffic_manager.py`・`tests/unit/test_virtual_scan.py`（`@pytest.mark.unit`）で ROS 無し検証。Ruff(py312/line100) + pytest 緑を維持（**host py3.7 では走らない→ py3.12 / tiryoh コンテナで実行**）。
- **設計**: `docs/mode-a/11a-traffic-mode-a.md`（TrafficManager IF / VirtualScan）・`docs/mode-c/11c-traffic-mode-c.md`（Mode C 境界）・`docs/shared/09-navigation-internals.md`（Nav2/AMCL/コストマップ）・`docs/architecture/03`（トピック契約）・16・17。

## 提供 (produce)
- **lib**: `traffic_logic.TrafficManager`(ABC) + `NoTrafficManager`(Mode A) + `SimpleTrafficManager`(Mode B) + `make_traffic_manager(mode, nav2_bridge=None, route_planner=None)`（11a:14-145, 47-54）。LLM Bridge(#4) が `MANAGERS` レジストリで consume する想定。**#125 yield 追加**: `table_route_planner({(pickup,dropoff):[keys]})`（11a §9.2 デモ topology・注入）／`SimpleTrafficManager(lock_timeout_s=)` + `submit_task(now=)` + `expired_locks(now)`（lock 齢 timeout=フォールバック C, `AISLE_LOCK_TIMEOUT_S=30` 暫定 11a §9.3・後方互換: `now` 無しは齢非追跡）／`release_aisle`=トリガ A。`status=="blocked"` 不使用（#128）。
- **lib**: `virtual_scan_logic`（rclpy 非依存幾何: `quat_to_yaw`/`relative_distance_bearing`/`should_publish`/`build_ranges` + 定数）。
- **topic**: `/bot{n}/virtual_scan`（`sensor_msgs/LaserScan`, frame `bot{n}/base_link`）。相手ロボを仮想障害物として自機 Nav2 `obstacle_layer` に注入（11a:166-321, doc03:93）。
- **node param**: `traffic_manager.{traffic_mode (none|simple|open-rmf), scenario ("" |yield_aisle_a)}`, `virtual_scan.{own_robot,other_robot}`。`yield_aisle_a` は NavigateToPose(`/bot{n}/navigate_to_pose`) で待機 bot の goal 保持/解放（nav2_msgs/action_msgs exec_depend 追加）。

## 消費 (consume)
- **契約**: なし（TrafficManager IF / traffic dict は doc11a の**例示**で凍結 pydantic ではない。`warehouse_interfaces.schemas` に traffic 型は無い）。`Situation` への traffic 注入が要れば**追加 optional フィールドの contract-PR**（#4 所有 / parallel-workflow §4）。
- **定数**: `warehouse_description.robot_dimensions.ROBOT_RADIUS`(=0.075, R-42)。VirtualScan / Nav2 footprint の単一ソース。直書き禁止。
- **topic**: `/bot{n}/amcl_pose`（`PoseWithCovarianceStamped`, AMCL 出力, doc03:93）, `/bot{n}/scan`（実 MS200, sim/実機が produce）。
- **collaborator(injected)**: `warehouse_nav2_bridge`（duck-typed `.navigate(robot, dropoff)`, bridge トラック所有）。import せず注入し、テストは fake（doc16 §11）。

## 前提・未確定 (TODO / 設計の空白)
- **#125 で デモぶん確定（11a §9）**: route→隘路 topology（`route_A`/`route_B` = `layout.AISLES` a/b・注入・frozen `KNOWN_LOCATIONS` には入れない）＋ 解放トリガ型（主A=goal `SUCCEEDED`≒隘路退出 / 副C=lock 齢 timeout）。**一般の route planner と timeout 実測値は Phase 3 のまま**（11a:99/118-122・T8/R-28・`AISLE_LOCK_TIMEOUT_S=30` は暫定 `# TODO(Phase 3)`）。`plan_route` は `table_route_planner` でデモ pairs を写像。
- # TODO(Phase 2, R-49 / doc09:171 note / 07:264) MPPI チューニングは要再調整（`warehouse_bringup/config/nav2_params.yaml`）。「yaml 1行」は不正確。
- # TODO(Phase 1/2) VirtualScan `scan.header.frame_id = bot{n}/base_link`（11a:242）と実 scan の `bot{n}/lidar_link`（xacro:113）は別フレーム。obstacle_layer が両 source を TF 変換する前提。実機で確認。
- # TODO(Phase 2, R-39) `amcl_pose` は 5-10Hz（実効 100-200ms stale）。VirtualScan の遅延幻影を要評価（freshness ガード候補）。
- # TODO(Phase 4) Mode C（open-rmf）= VirtualScan 非起動 + `observation_sources` から virtual_scan 除外（11a:317）。RMFTrafficManager は Mode C トラック（11c:59-83）。
- **E2E 未実施**: 2-bot Gazebo Nav2 E2E はフォローアップ Issue（tiryoh コンテナ必須）。本スライスは code+config+launch + unit/text 検証まで。

> #1 雛形の `traffic_manager` スタブを実装で置換済（#8）。
