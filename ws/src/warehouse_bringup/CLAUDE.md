# warehouse_bringup — launch + config の単一ソース（Nav2/AMCL/SLAM/twist_mux/footprint/速度上限）

- **担当トラック / ブランチ**: ros2 / `feat/nav-traffic / skeleton`
- **Phase**: 0.5
- **ビルド**: ament_python
- **ノード**: （ノードなし: データ/launch パッケージ）
- **編集境界**: このパッケージ配下のみ。`launch/bringup.launch.py`（統合ルート）は **skeleton 所有**（doc16:184）／`config/**` は **nav-traffic 所有**（doc16:193）・`launch/nav2_bringup.launch.py` も nav-traffic 所有（ファイル冒頭で自己宣言 nav2_bringup.launch.py:1-7。doc16:193 は文言上 config のみ列挙）。skeleton は `bringup.launch.py` から nav-traffic の `nav2_bringup.launch.py` を **include 合成済**（#75）＝nav-traffic ファイルは参照のみで編集しない。共有契約 `warehouse_interfaces` は変更不可（§4）。
- **消費する契約**: 起動時のみ — nav2_*（amcl/controller/planner/behaviors/bt_navigator/map_server/lifecycle_manager）・twist_mux・`warehouse_traffic`（virtual_scan）。**#156 で追加（launch 参照のみ・コード import なし）**: `warehouse_sim`(sim.launch.py)・`warehouse_state`(state_cache)・`warehouse_safety`(emergency_guardian)・`warehouse_nav2_bridge`(nav2_bridge)・`warehouse_llm_bridge`(llm_bridge)。コード import はしない（launch 合成のみ）。**slice3 live host scripts の runtime consume（凍結 `warehouse_interfaces` のみ）**: `KNOWN_LOCATIONS`・`config.load_config`・`StateSnapshot`・`FileStateStore`（precheck/seed が stack と同一 store 経路で読む, doc12:262）。**外部 daemon（合成しない）**: Hermes Gateway :8642 / Nav2 Bridge REST :8645（後者は full-stack で本 launch が in-process 合成＝外部起動禁止・`--live` /health で確認）。seed は sim の `head_on_spawn_poses` DATA export を sanctioned hand-off として読む（package import ではない, scenarios.py:17-21）。
- **生産する契約 / トピック**: 全ノードの launch / config（`config/` 1ファイル1責務, doc16:123）。実体（#68）:
  - `config/nav2_params.yaml` — Nav2 MPPI 全ブロック（DWB→MPPI, R-49）。footprint 0.075 / obstacle_layer `scan virtual_scan`。`<robot_namespace>` を launch の ReplaceString で per-bot 置換。**vx_max は launch が config 駆動化**（#125）: 在ファイル値 `vx_max: 0.3` は安全 default（=hard cap）で、launch の RewrittenYaml `vx_max` param_rewrite が config `safety.max_linear_velocity` で上書き（常に ≤0.3）。goal_checker.xy_goal_tolerance / planner GridBased.tolerance は #125/#67 で 0.10 へ（inflation_radius と協調・`# TODO(Phase 2 実測)`）。**inflation_radius 0.10→0.085 + cost_scaling_factor 3→10**（#125 round3 / R-42, doc07:252）: 0.10 では 200mm 隘路の中央(各壁から0.10m)まで cost 飽和し NavFn が経路を出せず bot が inscribed セルで停止 → 壁直近のみ高コスト化で**隘路通過を実証**（live 2台: berth→隘路中央 0.44,0.21 goal SUCCEEDED）。**未達**: 2台 head-on の最接近は 0.074m（<0.15m＝衝突域）。200mm 隘路は2台同時不可＝≥0.15m には yield/retreat 交通制御（R-49/doc11a）が要る → 継続 follow-up（#125 OPEN 継続）。
  - `config/twist_mux.yaml` — emergency(prio100) > nav2(prio10)（#40 で safety から移設）。
  - `config/collision_monitor.yaml` — **#126 R-39**: per-bot `nav2_collision_monitor` reflex stop（Mode A/B）。**consume**: `cmd_vel/nav2_raw`（controller remap先）+ `/bot{n}/scan`（doc03:78）+ `/bot{n}/virtual_scan`（**dual-consumer**＝costmap obstacle_layer `nav2_params.yaml:221,274` と共有・移設なし, doc12:547）。**produce**: 出力 `cmd_vel/nav2`（**既存 twist_mux prio10 入力・不変**＝emergency prio100 を迂回しない, doc12:545②/R-26）。`<robot_namespace>` frame を launch ReplaceString で per-bot 置換。**暫定値**: PolygonStop circle `radius 0.09`（ROBOT_RADIUS 0.075=`warehouse_description.robot_dimensions` 起点・R-42 隘路 200mm ＋ #156 ≥0.15m head-on demo と協調要＝Open ②）。**source_timeout**: 実 scan=node-level 1.0（lidar 途絶→stop, R-39 / doc12:546）／**virtual_scan=per-source 0.0**（>1.0m で無送信＝条件付き publisher ＝ stale-stop 無効化。PR#229 review MAJOR 修正＝さもないと通常走行で両 bot が "invalid source" STOP）。node-level 1.0 自体は暫定（Open ③）。**凍結契約変更なし**。**【2026-09-16 訂正】Humble 1.1.20 では `source_timeout` は点を落とすだけ（fail-open・per-source key 不読・`state_topic`／`min_points` も inert）＝yaml コメント是正済・値不変（doc12 末尾【2026-09-16 追補】）。****【config PR `feat/collision-monitor-humble-config`】`state_topic`／`min_points`／`virtual_scan.source_timeout` を撤去し `max_points: 3` を明示（Humble/Jazzy とも ≥4 点・行数 90 不変）。legacy Jazzy dev コンテナ向けの `virtual_scan.source_timeout: 0.0` は **`nav2_bringup.launch.py` が `ROS_DISTRO`（humble／iron 以外）で後置注入**（`warehouse_bringup.collision_monitor_distro.virtual_scan_timeout_overrides`・doc12 追補 (2) 追記・R-26 `tests/unit/test_collision_monitor_distro_params.py`）＝ローカル戻し不要（tests/e2e/README.md:242）。**
  - `launch/nav2_bringup.launch.py` — 共有 map_server + per-bot Nav2 スタック + twist_mux + VirtualScan×2（`traffic_mode==open-rmf` で gating off）。**nav-traffic 所有**。launch arg `max_linear_velocity`（default = `load_config().safety.max_linear_velocity`、`_operating_vx_max()` で ≤`MAX_LINEAR_VELOCITY` にクランプ）を RewrittenYaml で MPPI `vx_max` に注入（#125）。**#126**: controller_server の `cmd_vel` remap を **mode-conditional**（Mode A/B=`cmd_vel/nav2_raw`→collision_monitor→`cmd_vel/nav2` / Mode C=直接 `cmd_vel/nav2`＝monitor gating off で raw topic 無消費を防ぐ, doc12:543,550）。collision_monitor Node ＋ 専用 gated `lifecycle_manager_collision_monitor`（`traffic_mode != open-rmf` IfCondition＝VirtualScan と同型）。behavior_server は `cmd_vel/nav2` 直行＝**Open ⑥ bypass 暫定**（doc12:552⑥）。**【2026-09-16】collision_monitor Node の `parameters` = `[configured_collision_params, *virtual_scan_timeout_overrides(os.environ.get("ROS_DISTRO"))]`（yaml = Humble 正・Jazzy+ だけ `virtual_scan.source_timeout: 0.0` を後置注入。pure CI は AST pin、ROS コンテナは introspection test `test_collision_monitor_launch.py`）。**
  - `launch/bringup.launch.py`（**skeleton 所有**）— top-level entrypoint。**#156 slice1 で Phase 0.5 フルスタック合成**（TODO(#1) を解消）: ① `sim`（warehouse_sim/sim.launch.py を include、`sim:=true` gate・lazy FindPackageShare・**録画ノブ `scenario`/`rviz_config` を sim へ pass-through forward**＝#156 slice3。既定 `default`/`minicar`＝sim 既定と一致で back-compat、`scenario:=head_on rviz_config:=record` が sim に届かず録画が berth 横並びになる demo-breaking gap を解消, sim.launch.py:66-91。`tests/unit/test_bringup_launch.py` が pass-through を pin）② `nav2_bringup.launch.py`（include、`use_sim_time`/`autostart`/`params_file`/`map`/`traffic_mode` pass-through）③ `state_cache`・`emergency_guardian`（Node、常時＝core infra）④ `nav2_bridge`（Node、Mode A/B のみ＝正 allowlist `traffic_mode in {none,simple}`＝llm_bridge.py:75 NAV2_BRIDGE_MODES と一致・未知/typo mode は fail-closed, #166 ∧ `llm`）⑤ `llm_bridge`（Node、`llm:=true`）⑥ `character_llm`（Node、bridge/Slice 2 キャラLLM交渉レイヤ doc14。Mode A/B のみ＝`llm:=true` ∧ 正 allowlist `traffic_mode in {none,simple}`＝nav2_bridge ④ と同型・Mode C 交渉は Phase 4 doc14:255・typo は fail-closed。publish-only=no-actuation 稟議制 doc14:38。`test_bringup_launch.py` が gating を pin）。`map` 既定＝warehouse_sim 同梱 map（lazy・空 "" の map_server stall を回避）、`traffic_mode` 既定＝config（llm_bridge と Mode 判定一致）。各ノードは self-sequencing（TimerAction 不使用＝nav2_bridge は waitUntilNav2Active、state_cache は欠落入力に寛容、llm_bridge は Hermes 不達で Nav2-only fallback）。**MCP server は非合成**（採用 S1 = in-process WarehouseTools.dispatch / 標準形は Hermes stdio 子, doc15:50,80-94）、**Hermes Gateway は外部 daemon**（:8642, doc12a:409）。**#44 解消済（#160）**: sim.launch.py が合成 `sim_battery_publisher`（既定 `battery:=true`）で `/bot{n}/battery` を publish（config `safety.battery_percentage_scale` 単一スケール・doc03:79,82）→ pure sim でも State Cache が bot を emit（doc12:293）→ LLM が両 bot を見える。本 launch は sim include に `battery:=` を渡さず #160 既定 ON を継承。残: 実機スケール計測=Phase 1（#44 OPEN 継続）。
  - `config/m1_wheel_plain150.yaml` — **Mode M1 150 mm 通常輪の運用値（L0' `m1_driver` 向け param・本パッケージ所有の新規 1 ファイル）**。docs/mode-m1/02 追補③ が指定した注入先（doc02:135「運用値 … は bringup/launch の param 注入で入れる（bringup 所有・別 PR）」の着地）。中身は **5 キーちょうど**（07:252-256 の「150 mm での値」列）= `wheel_scale` 1.875（= D/80mm・07:252）/ `wheel_diameter_m` 0.150 / `lateral_enabled` false / `odom_enabled` true / `counts_per_rev` 2464.0（**float 必須**＝declared type が DOUBLE。`2464` は起動時に型不一致で落ちる）。`yaw_scale`（07:253 ＝ 値でなく「実測 G-W4/G-W9」・1.0 は driver 既定の逐語コピーで、書くと実測既定が入った日に**静かに 1.0 へ引き戻す**）/ `track_m` / `wheel_signs` / 共分散は **意図的に不在**（driver 既定に残す・doc02:126,130-131）。**safety param（`cmd_vel_timeout_s` / `stop_overlay_enabled` / `stop_state_max_validity_s`）は絶対に書かない**＝車輪の事実を書くファイルであり W-1/停止権限の再定義点ではない（unit がキー集合を**等値**で pin）。**launch から自動注入しない**＝明示 `--params-file` でのみ効き、素の `ros2 run warehouse_m1_driver m1_driver`（docs/mode-m1/03:103）は今日と bit 等価（driver 起動＝車輪通電なので teleop launch にも載せない＝W-4 doc02:65）。**適用条件 = 150mm を物理装着したら直ちに**（未適用のまま 150mm で走るのが 1.875 倍の fail-open）。**07 §10 G-W ゲートは適用条件ではなく odom の使用制限**（通過まで観測のみ・航法に使わない。G-W4 は k を校正する試験なので適用が前提）。node key は `"/**"`（twist_mux.yaml:21-29 と同じ理由＝namespace 非依存）。pin = `tests/unit/test_m1_wheel_plain150_profile.py`（全キーが `driver_node.py` の `declare_parameter` 実体に存在することを AST 走査で検証＝ROS 2 は未宣言キーで起動失敗する / k と径の整合 / 実 `M1DriverCore`・`WheelOdometry` 受理 / mutation 12/12 KILLED）。**凍結契約変更なし**（`MAX_LINEAR_VELOCITY` 不変・clamp は実単位のまま先に効く）。
  - **slice3 live tooling（host scripts／新規 frozen 契約なし＝additive host artifact のみ・runbook 正本 `tests/e2e/README.md:54`）**:
    `scripts/slice3_live_precheck.sh`（`--offline`=e2e回帰+WAREHOUSE_TASKS seed検証+scenario/rviz_config forward 静的確認+seed/snapshot namespace 一致 / `--live`=Hermes:8642・Nav2 Bridge:8645 /health + state.json freshness）・
    `scripts/slice3_seed_initialpose.sh`（`/bot{n}/initialpose` を **SCENARIO に整合** seed＝`default`=berths / `head_on`=sim `head_on_spawn_poses` 導出で AMCL 誤定位を防ぐ。`DRY_RUN` print）・
    `scripts/slice3_record.sh`（noVNC/x11grab 録画ラッパ・実キャプチャ=人間ゲート）。
- **依存**: launch-time exec_depend に nav2_*（#126 で `nav2_collision_monitor` 追加）/ twist_mux / warehouse_traffic（package.xml）。**+ `warehouse_interfaces`**（#125: `nav2_bringup.launch.py` が `load_config` / `MAX_LINEAR_VELOCITY` を import し vx_max を config 駆動化。共有契約パッケージなので許可・`warehouse_sim/sim.launch.py` と同型。契約自体は変更しない §4）。他トラック内部は import しない。
- **テスト**: config は YAML parse + 値検証、launch は colcon build + ノード import（py3.12 / tiryoh）で text 検証。`bringup.launch.py` の合成は **`tests/unit/test_bringup_launch.py`**（launch-introspection: ちょうど1つの `IncludeLaunchDescription` が `nav2_bringup.launch.py` を指し traffic_mode を転送）で検証。`launch`/`launch_ros` は ROS 同梱で pure-CI 非導入のため `pytest.importorskip` で skip→コンテナで実行（doc16 §11）。実 nav2 launch / 2台 Gazebo E2E は **#67**（前提: sim `/clock`+map #76・launch 合成 #75・コンテナ nav2 導入）。**#156 slice2 統合ハーネス = `tests/e2e/**`（新設・`@pytest.mark.e2e`・host で動く＝ROS/network/Gazebo 不要）**: 司令官サイクルを `llm_bridge.py:110-143` と**データ経路で同一に配線**し（fake は2つの真の外部境界＝Hermes 脳 + Nav2 HTTP だけ・両端は本番コード。tracer→NoopTracer / `/llm/*` publish→\_noop は落とす＝観測境界）、**08a:337-359 の正本デッドロック解消シーケンス**で **実 state.json→実 SituationBuilder→実 Hermes parser→action_map→実 WarehouseTools.dispatch→forward** を end-to-end 検証（**bot2=yield→`/api/v1/navigate` retreat_B / bot1=wait→`/api/v1/wait` 5s** の2 POST・08a:170,172,346-347）。**R-26 forward 抑止マトリクスは重複させない**（unit `test_nav2_forward.py`/`test_bridge_scheduler.py` が凍結済）。**≥0.15m 幾何・live LLM yield 判断・RViz/noVNC 録画は slice3 live**（`tests/e2e/README.md` runbook・前提=#181 land 済み + #192 land 済み + L6 サイクル長確認）。**#192 fixed**: 非JSON応答で `HermesClient.decide()` の `ValueError` が出ても scheduler が cycle ignore として扱い、`test_nonjson_reply_is_ignored_no_forward` が 0 forward・loop 生存を pin。**slice3 live 前 host 回帰（新規・全て host）**: `test_slice3_initialpose_seed.py`（seed↔`head_on_spawn_poses` 完全一致＝head_on で berth 座標を seed して AMCL 誤定位するデモ事故を pin）・`test_slice3_task_seed_forward.py`（#181 `WAREHOUSE_TASKS`→`parse_seed_tasks`→`pending_tasks`→実 SituationBuilder→commander navigate forward & consume を e2e 化＝従来 unit のみ）・`test_slice3_precheck_offline.py`（precheck seed バリデータ拒否クラス5種を subprocess で pin）。Ruff(py312/line100) + pytest 緑を維持。
- **設計**: docs/shared/09（Nav2/AMCL/コストマップ）・mode-a/11a（TrafficManager/VirtualScan）・architecture/03・16（§5 config 単一ソース・§9 所有）・17。

> #1 雛形（空の `bringup.launch.py`）に #68 が config/launch を追加 → #75 で skeleton が nav2_bringup の include 合成（nav2-only）→ **#156 slice1 でフルスタック合成（sim+nav2+state+safety+nav2_bridge+llm_bridge、MCP は in-process／Hermes は外部）。`tests/unit/test_bringup_launch.py` を full-stack 形（include×2・Node×4・mode/llm/sim gating）に更新**。

## 【2026-09-18 追記】生産する契約 追補 — `config/nav2_params.yaml` 崖専用 layer ＋ `collision_monitor.yaml` source `cliff_scan`（レーン A `feat/nav-cliff-layer` が記入・stub 先置き）

**produce（追加）**: `config/nav2_params.yaml` の **`cliff_layer`**（`nav2_costmap_2d::ObstacleLayer` の**別 instance**・local `:247` / global `:293`・`plugins` は `:216` / `:259`）＝ source は `cliff_scan` 1 本のみ・`marking: true` / `clearing: false`・`obstacle_max_range: 2.5`・`max_obstacle_height: 2.0`（**どちらも Humble 既定 / 在ファイル `scan` と同値＝新しい数値ではない**。per-source `max_obstacle_height` の既定 0.0 は「TF 後に z が厳密 0.0 の点」しか通さない fail-open ゆえ明示）。`inflation_layer` は最後のまま＝崖セルも膨張対象。**別 instance にする理由**＝`clearing:false` だけでは同一 layer 内の別 source の raytracing から崖を守れない（[04:380](../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:380) [D]・instance ごとに grid 独立 → `updateCosts` が max 合成）。／ `config/collision_monitor.yaml` の **source `cliff_scan`**（`type: scan`・`topic: cliff_scan`（相対）・**`enabled: false` 既定**・per-source `source_timeout` **無し**＝`OQ-OD44` 裁定 = scan 型）。／ `warehouse_bringup.collision_monitor_distro.cliff_sources(config)`（純関数・`[]` か `[{"cliff_scan": {"enabled": True}}]`）。

**consume（追加）**: `/bot{n}/cliff_scan`（`sensor_msgs/LaserScan`・producer = `warehouse_perception.terrain_publisher`・[doc03:322](../../../docs/architecture/03-software-architecture.md:322)）を **costmap `cliff_layer` と `collision_monitor` の dual-consumer** として購読。／ config キー **`perception.terrain.enabled`**（**レーン C が `config/warehouse.base.yaml` に置く。本パッケージは読むだけ・値を定義しない**）— `nav2_bringup.launch.py` が `load_config()` 経由で読み、真のときだけ CM source を arming する。

**なぜ既定 OFF＋launch arming か**: producer は既定 OFF で、dev cockpit（Jazzy + Gazebo）には depth camera が無い。Jazzy は **enabled な source の無データ = `invalid source` STOP**（jazzy `collision_monitor_node.cpp:437-447`・参照日 2026-09-18）なので無条件に足すと cockpit が即停止する。**disabled な source は両 distro でその判定より前に skip**（humble `:357-360` / jazzy `:437`）＝既定 OFF は完全に不活性。costmap 側は gate 不要（`expected_update_rate` 既定 0.0 → `isCurrent()` 常時 true・空 grid は max 合成で無影響）＝**追加 gate を増やさない**。

**残件（隠さない）**: 崖セルを clear する経路が無く（`FLOOR_CONFIRMED` は LaserScan に落ちない）**global costmap の偽 DROP は残り続ける**（local は rolling で流れる）＝`OQ-OD4Y-l2`。`obstacle_max_range` と producer `range_max_m` の整合点は未決＝`OQ-OD4Y-l1`。**ROS 無しの host 検証のみ**（YAML / AST / 純関数 unit）で、costmap が崖を marking する画は未観測＝`OQ-OD4Y-l5`。設計・裁定・一次情報の正本 = [04 追補 ⑩](../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:1009)。**テスト**: `tests/unit/test_cliff_layer_config.py`（`unit`+`safety`）。**凍結契約変更なし**。

<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->
<!-- spacer: 並列 append の hunk 衝突回避（区画間 6 行超） -->

## 【2026-09-18 追記】生産する契約 追補 — `nav2_bringup.launch.py` `terrain_publisher` 群 ＋ `config/warehouse.base.yaml` `perception.terrain.*`（レーン C `feat/bringup-terrain-publisher` が記入・stub 先置き）

**レイヤ**: launch / config = **配線**（正準対応表に行を持たない＝帰属未定・actuation なし。
[layer-annotation.md](../../../.claude/rules/layer-annotation.md)）。起動する node は
**自律走行（安全層外）の producer**。**L2 / L1 / L0' 不変更**・`twist_mux` 入力 2 本のまま。
正本 = `docs/mode-outdoor/04-perception-sidewalk-and-signals.md` の **追補 ⑫**
（hand-off ③ = [追補 ⑨ §4 :992](../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:992) の実装記録。
追補 ⑫ 自体には行 pin を張らない＝同 doc 末尾はレーン A / B の stub 充填で動くため）。

### 提供 (produce)

- `launch/nav2_bringup.launch.py` に **`_terrain_config()` / `_terrain_group(robot, use_sim_time)`**
  （`_speed_band_group` の後ろに append・呼び出しは per-robot ループの `_speed_band_group` 直後）。
  per-bot で `GroupAction([PushRosNamespace(robot), SetParameter("use_sim_time", …),
  Node(package="warehouse_perception", executable="terrain_publisher",
  name="terrain_publisher", parameters=[…])])` を 1 本組む。`collision_monitor` 節
  （nav-traffic / レーン A の区画）は**不変更**。
- `config/warehouse.base.yaml` に **`perception.terrain.*`**（`speed_bands` の後ろに append）:
  実キーは **`enabled: false` のみ**。続く **23 キーはコメントアウトの placeholder**
  （入力 topic 2 本 ＋ ROS param 21 本 = `PARAM_KEYS`）で、各行に単位・検証条件・出典 file:line
  を持つ。**実値は 1 つも無い**（カメラ未購入・車体未組立＝
  [04:992](../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:992) の実測ゲート。
  実値は env overlay `config/<env>/warehouse.yaml`＝[environments.md](../../../.claude/rules/environments.md)）。
- **`perception.terrain.enabled` は ON/OFF の単一の真実**: producer（本 launch 群）と
  consumer 側 gate（`collision_monitor` の `cliff_scan` source・レーン A）が同じキーを読む。

### 消費 (consume)

- `warehouse_interfaces.config.load_config`（既存依存・`perception` ブロックは**検証されない**＝
  `_validate_safety` は `safety.*` のみ。検証は node 側 sentinel に一本化。契約 hub 不変更）。
- `warehouse_perception.terrain_node_core.PARAM_KEYS`（rclpy 非依存・**21 param 名の単一ソース**。
  本数もキー名も launch 側に再列挙しない）。import は **`enabled` が真の経路の中だけ**＝
  perception 未ビルドでも terrain OFF なら Nav2 は起動する。
- console_script `terrain_publisher = warehouse_perception.terrain_node:main`
  （`warehouse_perception/setup.py`）。`package.xml` の `<exec_depend>warehouse_perception</exec_depend>`
  は速度帯スライスで宣言済み（`:27`）＝**本スライスで package.xml は不変更**。
- 型の正本は `terrain_node.py` の `_SENTINELS`（**21 本の内訳 = int 5 / double 15 / str 1**。
  23 本の面で str が 3 本になるのは入力 topic 2 本を足した数）。rclpy は宣言値から param 型を
  固定するため、launch は **`PARAM_KEYS` の 21 本だけ**をその型へ変換する（`_TERRAIN_INT_KEYS` /
  `_TERRAIN_STR_KEYS` / 残りは double）。**これは型の表であって既定値ではない**。
- **入力 topic 2 本は変換せず生のまま転送する**。`str(None) == "None"` は**非空**文字列なので、
  `str()` で包むと overlay の `depth_topic:`（YAML null）が node の空文字ガード
  （`terrain_node.py:134`）を**すり抜け** `/bot{n}/None` を購読し「健康に見えたまま無言」になる。
  生で渡せば宣言型 STRING が非 str を拒み起動が止まる（fail-closed）。unit が pin。

### 前提・未確定 (TODO)

- `# TODO(実測)` **23 キーの値は 1 つも決まっていない**。overlay を書けるのは
  `OQ-OD45`（縁石 2 cm の分離距離）/ `OQ-OD4Q`（MinZ）/ `OQ-OD4Z-d1`（2 上限）の実測後。
- `# TODO(dev sim)` dev（Gazebo）に depth camera が無いので dev overlay も未記入
  （OFF のまま）。`use_sim_time` と depth stamp の関係は `OQ-OD4Y-n1`。
- `# TODO(OQ-OD4Y-n2)` overlay 値の検証は「起動して落ちる」まで分からない
  （組み合わせ条件は特に）。起動前 lint に寄せるかは未決。
- `# TODO(OQ-OD4Y-n3)` int キーは `int(value)` 変換ゆえ非整数値が**黙って丸まる**。
- `# TODO(OQ-OD4Y-n4)` 2 台構成で 1 カメラを絶対名で指すと両 bot に同じ崖が出る
  （`speed_bands.source_topic` と同型の未決）。
- `# TODO(OQ-OD4Y-n5)` [productization/01 L1 行](../../../docs/productization/01-commercial-box-map.md:185)
  の「launch / config / consumer 側は未配線」が本スライスで古くなる（編集境界外＝統合側の残件）。

### テスト（レーン C）

- `tests/unit/test_terrain_bringup_wiring.py`（marker `unit` + `safety`・**AST + YAML のみ**＝
  `launch_ros` / `nav2_common` / rclpy 不要で pure CI でも走る。
  `test_speed_band_bringup_wiring.py` と同型）: 配線の存在（per-robot ループから 1 回）／
  namespace push と `use_sim_time`／OFF 時 `[]` が**生成より前**に返る／config キー経路
  `perception` → `terrain`／転送キー集合 == node の宣言面（`PARAM_KEYS` と `_SENTINELS` に
  機械照合・本数を再ハードコードしない）／欠落キーを埋めない／**数値リテラル 0 個**／
  int・str 変換表 == `_SENTINELS` の型／`cmd_vel`・`stop_*`・`speed_limit`・`remappings` 不在／
  base が `enabled: false` 単独／placeholder が 23 本を網羅。
- mutation **9/9 KILLED**（`enabled` gate 除去・`ray_count` を double 転送・launch に
  `pixel_stride: 1` の既定・`camera_info_topic` 不転送・欠落キーを既定で埋める・
  `_terrain_group` 呼び出し削除・config キー経路 typo・`cliff_scan` を `cmd_vel` へ remap・
  base の `enabled: true`）。

## 【2026-09-18 追記】生産する契約 追補 — `config/nav2_params.yaml` 全 observation source の `max_obstacle_height` 明示（`OQ-OD4Y-l4` 解決・`feat/nav-costmap-height-window`・PR #726）

**レイヤ**: costmap 設定 = **06 Navigation** の consumer 側 config（[layer-annotation.md](../../../.claude/rules/layer-annotation.md)）。
`cmd_vel` 経路・twist_mux 優先度・L2 / L1 / L0'・凍結契約 `warehouse_interfaces` は**不変**。
正本 = [04 追補 ⑩ §5 `OQ-OD4Y-l4`](../../../docs/mode-outdoor/04-perception-sidewalk-and-signals.md:1069)（同一行に解決注記）。
一次情報 = Nav2 `humble`（参照日 2026-09-18・[D] 原文取得）: `obstacle_layer.cpp:143`（per-source 既定 **0.0**）/
`:82`（layer 既定 2.0＝別フィルタ）/ `observation_buffer.cpp:143-144`（TF 後に `min <= z <= max` だけ残す）。

### 提供 (produce)

- `config/nav2_params.yaml`: `nav2_costmap_2d::ObstacleLayer` の**全 instance × 全 source**が per-source
  `max_obstacle_height: 2.0` を明示する。追加 3 本 = global `scan` `:280` / local `virtual_scan` `:235` /
  global `virtual_scan` `:288`（既存の local `scan` `:227` と `cliff_scan` `:247` / `:293` は元から明示）。
  値は Humble layer 既定（`obstacle_layer.cpp:82`）＝在ファイル `:227` と同値で**新数値なし**。高さ窓を広げる
  意図ではなく、「省略 → per-source 既定 0.0 → TF 後 z が厳密に 0.0 の点しか通らない」罠（`lidar_link` の
  取付高さ > 0 なら全点消失 = fail-open）を塞ぐだけ。
- **記法**: global `virtual_scan` は 1 行 flow mapping ＋説明コメント 5 行（`:283-288`）、local は inflation
  コメントの再折返し（6→5 行・`:240-244`）で **net-zero（342 行のまま）**＝`:245` / `:291` / `:293` / `:300`
  ほかの下流 pin は不変（#165・同ファイル `:202` / `:247` / `:293` と同じ手法）。動いた pin は `:282`→`:288`
  （04:1071）と `:239-246`→`:240-246`（doc23:589）の 2 か所のみ＝同 PR で再 pin 済。
- flow mapping が rcl で読める根拠は `:247` / `:293` と同じ: `rcl_yaml_param_parser/src/parse.c:834`
  （`YAML_MAPPING_START_EVENT` に style 判定なし・humble・参照日 2026-09-18）。

### 消費 (consume)

- 変更なし（topic / 型 / frame は不変: `/bot{n}/scan` = `bot{n}/lidar_link`、`/bot{n}/virtual_scan` =
  `bot{n}/base_link`（`virtual_scan.py:72`）、`/bot{n}/cliff_scan` = `base_link`）。

### テスト

- `tests/unit/test_costmap_observation_height_window.py`（`unit` + `safety`・純 YAML・ROS 不要）:
  `plugins` に列挙された ObstacleLayer instance を**source 名を列挙せず**掃引し、全 source に (a) キーの明示
  (b) 値 == 2.0（layer 既定＝独立オラクル・実装読み返しでない） (c) `> 0.0`（per-source 既定の罠） (d) YAML
  float（rclcpp は宣言型 double を強制＝int リテラルは起動時に落ちる）を pin。掃引が空にならないことを
  別 test で pin（`OQ-OD4Y-l4` が対象にした 4 source を含む）。mutation **5/5 KILLED**（global
  `virtual_scan` のキー削除・local `virtual_scan` = 0.0・global `scan` = `2`（int）・local `virtual_scan`
  = 1.5・global `observation_sources` から `virtual_scan` を落とす）。

### 前提・未確定 (TODO)

- `# TODO(実測)` global の実 `scan` が**修正前に**全点を落としていたか（`lidar_link` 取付高さ > 0 ＝
  04:1070 の推定）は TF 実値で未確認。本 PR は罠を塞いだだけで「以前は落ちていた」とは言わない。
- `# TODO(OQ-OD4Y-l3)` 下側 `min_obstacle_height`（既定 0.0）は据え置き。TF 後の z が負になる構成
  （IMU 姿勢反映・`base_footprint` 導入）では別途裁定。
