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
- # TODO(Phase 2, footprint_padding) **sim 実測知見（本ラウンド）**: 走行中 costmap が `computeCircumscribedCost: inflation radius (0.085) < circumscribed radius (0.089142)` を警告。`robot_radius:0.075` ＋ Nav2 既定 `footprint_padding:0.01`（nav2_params.yaml で未設定＝既定）が 16-gon 円 footprint の対角頂点を `(0.075/√2+0.01)·√2 = 0.089142` まで外側にパディングするため。**効果**: (a) 計画性能警告、(b) 実効 footprint(0.089) が doc の inscribed 単一ソース 0.075（11a §9.4）を黙って上回る、(c) 200mm 隘路の余裕を縮める。**候補修正**: `footprint_padding:0.0` で footprint を doc どおり 0.075 円に一致＋警告解消＋隘路余裕回復。**本ラウンドでは未変更**（footprint は kickoff 安全ガードレール「速度・footprint・inscribed に触れない」の対象＝要明示判断＋実測検証 → 次の sim 検証セッション / orchestrator 判断）。
- **E2E sim 試行（本ラウンド・tiryoh）**: full stack（gz-sim8 + 2台 spawn + ros_gz_bridge + 共有 map_server + 2×Nav2(MPPI) + twist_mux + VirtualScan×2）が **build→launch→全 lifecycle active→TF map→bot1/base_link 正配置（berth_A (0.2,0.8)@-90°）** まで到達。global planner は隘路を貫く有効経路（distance_remaining 0.779m）を算出、ロボットは前進開始。**ただし決定論 traversal benchmark は本コンテナで不成立**: ①2-stack で MPPI 20Hz 制御ループ（batch 1000×30）が software-GL gz レンダリングと競合し維持不能（compute_path/follow_path action タイムアウト＝`Failed to get result for follow_path in node halt!`）、②lidar が software GL で ~3.5Hz（公称10Hz）＝AMCL 誤定位（単機走行で odom 実測 map≈(0.16,0.28) vs AMCL 推定 (0.53,0.28)＝x 誤差 ~0.37m）。単機（bot2 Nav2 kill）では ~0.48m 前進＋15 recovery 後に planner `Failed to create plan with tolerance of: 0.100000`(NO_VALID_PATH) で ABORT。**いずれも環境性能/知覚劣化であり config/tuning 欠陥ではない**。**結論**: MPPI critic/inflation の経験的検証は GPU/Jetson 級ホスト（doc09:180＝Jetson 67TOPS は本用途にサイズ済、Mac software-GL コンテナは過小）を要するフォローアップ＝gated。本ラウンドは nav2_params 値を変更せず（#67/#125 で検証済の現値を維持）。

## ラウンド検証記録（#8, round 2026-06-06）— MPPI/manager/VirtualScan 再照合 ＋ 安全 unit ＋ sim 試行
- **MPPI tuning**: nav2_params.yaml の critic 重み・inflation(0.085/10)・footprint(0.075)・vx_max(0.3) を 11a §9.4 / R-42(`07:252`) / R-49(`07:264`) / doc09:167-191 と**再照合 → 値変更なし**（現値は #67 E2E + #125 yield≥0.15m で検証済。sim で改変を検証不能＝docs-first/de-risk で投機的変更しない）。観測知見＝上記 footprint_padding / E2E 試行。
- **manager 最終化**: `NoTrafficManager`(11a:64-85) / `SimpleTrafficManager`(11a:89-133 + §9 yield) が doc と一致を確認。`make_traffic_manager` の `route_planner`/`nav2_bridge` 透過を `tests/unit/test_traffic_manager.py::test_factory_passes_bridge_and_route_planner_to_simple` で固定（#4 が build する公開 IF の回帰防止）。`status=="blocked"` 不使用（#128）を維持。
- **VirtualScan**: `virtual_scan_logic` 定数（`ANGULAR_WIDTH=0.26`/`MAX_RANGE=2.0`/`SUPPRESSION_RANGE=1.0`/`NUM_RAYS=360`）が 11a:305-312 と一致を確認。`observation_sources: scan virtual_scan`（nav2_params.yaml:221,274・`clearing:false`:234,286）既配線を確認＝再配線せず。
- **安全 unit（R-26）**: 新規 `tests/unit/test_nav2_params_safety.py`（`@pytest.mark.safety`・pure-YAML＝CI 実行可）で nav2_params 静的不変量を固定: 全線形 vx 上限 ≤ `MAX_LINEAR_VELOCITY`(0.3, safety.py:18)、`robot_radius == ROBOT_RADIUS`(0.075) 両 costmap、`inflation_radius ≥ inscribed`。値は凍結契約から import（直書きせず）。`tests/unit/test_nav2_bringup_launch.py` の launch 側 vx_max クランプ（0.9→0.3）と相補。
- **ゲート**: host `ruff`/`pytest`(unit+safety 全緑)/`check_consistency.py`=0 ERROR、container `colcon build`(9 pkg)＋launch-introspection＋全 pytest 54 緑（skip 0）。

> #1 雛形の `traffic_manager` スタブを実装で置換済（#8）。
