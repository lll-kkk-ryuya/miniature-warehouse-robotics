# tests/e2e — slice2/3 統合ハーネス（#156 capstone）

`#156`「AI 司令官が Gazebo 2台を動かす」の **統合（integration）層**。`tests/unit/**`
（各 seam を fake で検証）に対し、ここは **配線（topology）と本番に近い経路** を検証する。
設計正本: `docs/architecture/06-implementation-phases.md:107-110`（Phase 0.5 完了条件）/
`docs/architecture/08-llm-bridge-common.md:121-169`（司令官サイクル・in-process dispatch）/
`docs/mode-a/08a-llm-bridge-mode-a.md:158-173,321-359,387`（action→Nav2 マッピング・デッドロック
検出と**解消シーケンス**＝yield/wait コマンドの正本・retreat LOCATIONS）/
`docs/mode-a/11a-traffic-mode-a.md:153`（検出は LLM の仕事）・`:431-470`（§9＝**別機構**の Mode-B
aisle-lock デモ＝live ≥0.15m 幾何の正本のみ）。

## 実行

```bash
# 自動テスト層（host で動く・ROS/network/Gazebo 不要）
python3.12 -m pytest tests/e2e/ -v            # = 統合配線の回帰
python3.12 -m pytest -m e2e                    # e2e だけ選択
python3.12 -m pytest -m "not e2e"              # e2e を除外
```

`e2e` マーカーは `tests/e2e/conftest.py` の `pytest_configure` で**ローカル登録**
（共有 `pyproject.toml`＝skeleton 所有を触らない。parallel-workflow.md §7.1）。

## このハーネスが証明すること（＝ここにしか無い価値）

`test_slice2_yield_forward.py`（4 tests）。**司令官サイクルを `llm_bridge.py:110-143`
と同一に配線**し、fake は**2つの真の外部境界だけ**＝ LLM の脳（Hermes Gateway）と
Nav2 の HTTP transport。両端は**本番コード**が走る:

| test | 証明すること |
|---|---|
| `test_headon_yield_forwards_both_motions` | **slice2 headline**: 実 head-on `state.json` → 実 `SituationBuilder` → 実 Hermes parser → action_map → 実 `WarehouseTools.dispatch` → forward。**08a:342-347 の正本デッドロック解消**（**bot2=yield→`/api/v1/navigate` retreat_B / bot1=wait→`/api/v1/wait` 5s**）の2 POST を doc 指定の順で**正確に**発火（配線の end-to-end）。 |
| `test_real_situation_builder_enriches_headon` | 実 `SituationBuilder`（Mode A）が head-on 生 snapshot を `obstacle_ahead=True` + CTRV `predicted_position_3s` + 全 traffic フィールドに enrich（commander が見る situation が**実物**＝stub でない）。 |
| `test_valid_json_but_invalid_command_is_ignored_no_forward` | 実 parser が valid-JSON-but-invalid-Command を dict 化 → scheduler の `Command.model_validate` が reject → cycle 無視・**0 forward**・loop 生存（parser↔scheduler を実接続）。 |
| `test_nonjson_reply_is_ignored_no_forward` | 非JSON応答で `decide()` の `ValueError` が出ても、scheduler が cycle ignore として扱い、**0 forward**・loop 生存を保証する（#192）。 |

## ここでは証明しない（責務分界・隠さない）

- **R-26 forward 抑止マトリクス**（accept→1 POST / stale・duplicate・Policy-reject・
  read-only→0 POST / fail-open）は **既に unit 層で凍結済**:
  `tests/unit/test_nav2_forward.py` ＋ `tests/unit/test_bridge_scheduler.py`
  （実 `WarehouseTools` で B-3 / C end-to-end）。**本ハーネスは重複させない**。
- **最接近 ≥0.15m の幾何**（`11a:446`）は **Gazebo 物理計測**＝下記 **slice3 live runbook**
  でのみ証明（host harness は物理を持たない＝WIRING のみ）。
- **§9 の実トポロジ**は再現しない: §9.2/§9.5（`11a:435,453,455`）の実デモは**座標ゴール**
  （x≈0.45,y≈0.12）＋ `route_A`/`route_B`＝**Mode-B aisle ロックキー**（route_planner が注入・
  `KNOWN_LOCATIONS` に**無い**＝navigate 宛先ではない）を使う（南側 named 地点は #144 で到達不能）。
  本 harness は **Mode-A `yield` コマンドの forward 配線**を、契約有効な `retreat_A/B`+`shelf_1`
  の**代理キー**で pin するだけ（座標ゴール・route ロックは slice3 live が扱う）。
- **デッドロック検出**は LLM 側推論（`11a:153`）。ここでは fake commander が代行。
  **Mode-A プロンプト + デモ用 `pending_tasks` seed は #181 で配線済み**。live で本物の
  yield 判断を出す検証は slice3。

## slice3 live demo runbook（実 Hermes / RViz / noVNC 録画）

> **= 3段リリース第1段の素材**（sim 録画版が最初の公開/営業送付可成果物・round 戦略 2026-06-06）。
> **着手の前提**: ① **#181 land 済み**（task 注入 + Mode-A system prompt）
> ② **#192 land 済み**（非JSON応答を cycle ignore）
> ③ **L6 のサイクル長確認**（API p95>2.5s なら 3秒→4-5秒・デモ尺の作り直しを断つ）。
> ①②は host harness で回帰確認、③は live Hermes/provider で測る。

tiryoh コンテナ（host py3.12 では ROS/launch/Gazebo 不可＝`reference_local_gate_execution`）で:

```bash
# -1) host precheck（ROS/network/Gazebo 不要）
scripts/slice3_live_precheck.sh --offline
#   → tests/e2e 回帰 + WAREHOUSE_TASKS seed 検証 + launch command 表示。
#     Hermes/Nav2 Bridge を起動済みなら `--live` で /health も確認する。

# 0) 外部 daemon は Hermes Gateway :8642 のみを別途起動
#    （launch では合成しない＝bringup.launch.py:38「DELIBERATELY NOT launched」, :45。
#     dev キー疎通済＝memory project_api_keys_dev_setup）。
#    ⚠ Nav2 Bridge :8645 は外部 daemon ではない＝本 launch が in-process Node 合成する
#      （bringup.launch.py:226 nav2_bridge=Node / :233 gate＝llm:=true ∧ traffic_mode∈{none,simple}）。
#      step 2-4（llm:=true）では別途起動しないこと＝:8645 を二重 bind して起動失敗/競合する
#      （nav2_bridge.py:41 DEFAULT_PORT=8645 / :166 uvicorn.run）。standalone Nav2 Bridge が要るのは
#      llm:=false の nav2-only 実験時のみ（その時は llm_bridge も nav2_bridge も合成されない）。
#    ⚠ tiryoh container 内から host の Hermes に届かせるには loopback ではなく:
#      export WAREHOUSE__HERMES__BASE_URL=http://host.docker.internal:8642
#      （config override 機構 config.py:28,48-66 が hermes.base_url を上書き）。
#      precheck --live も同様に HERMES_BASE_URL=http://host.docker.internal:8642 を渡して確認する
#      （precheck は container を自動検知し loopback 設定時に host.docker.internal を WARN 提示）。

# 1) slice1 health（upstream 不要・今すぐ可能。DoD step1）
export WAREHOUSE_CONFIG_DIR=/ws/config
export WAREHOUSE_ENV=dev
# sim 録画限定: AMCL が初期 pose 以外を継続 publish しないことがあるため freshness を緩和する。
export WAREHOUSE__SAFETY__POSE_FRESHNESS_TIMEOUT=999
ros2 launch warehouse_bringup bringup.launch.py llm:=false sim:=true
#   → Nav2 lifecycle 全 bot active / state_cache が state.json を 100ms 書出 /
#     guardian 50ms reflex / llm:=false でも起動（Hermes 無し fallback）を確認。
#   → 別 shell で ROS setup を source し、Nav2 lifecycle active 後に:
#     cd /ws && scripts/slice3_seed_initialpose.sh
#     State Cache が両 bot の pose を取り込むことを確認。

# 2) slice2/3 full stack（#181/#192 land 後）。scenario:=head_on で 200mm 隘路の正面対向 spawn、
#    rviz_config:=record で録画用俯瞰 cfg を選択（bringup が両 arg を sim へ forward＝slice3。
#    無いと berth 横並びを録画してしまう＝デモの核が映らない）。
# ⚠ headline 必須: 司令官に goal を持たせる WAREHOUSE_TASKS seed を **launch 前に export** する。
#   scenario:=head_on は spawn の向きを作るだけで goal は注入しない（head_on_goals は consumer ゼロ）。
#   goal は WAREHOUSE_TASKS→pending_tasks（llm_bridge.py:119,165 が起動時に parse_seed_tasks）のみ。
#   無いと pending_tasks=[] で両 bot は spawn 位置に静止し、睨み合い/yield が起きない（駐機2台を録画）。
#   この seed は precheck の print_next_steps / validate_tasks が emit・検証するものと同一。
export WAREHOUSE_TASKS='[{"id":"task_1","from":"berth_A","to":"shelf_1"},{"id":"task_2","from":"berth_B","to":"shelf_3"}]'
ros2 launch warehouse_bringup bringup.launch.py sim:=true llm:=true traffic_mode:=none rviz:=true scenario:=head_on rviz_config:=record
#   sim+nav2+state+safety+nav2_bridge(正 allowlist traffic_mode∈{none,simple}, #166)+llm を合成（#162）。
#   Nav2 Bridge :8645 は↑が in-process 合成する＝step 0 の通り別途起動しない（二重 bind 回避）。
#   full stack の lifecycle active 後、head_on spawn に合わせて初期 pose を seed:
#     cd /ws && SCENARIO=head_on scripts/slice3_seed_initialpose.sh
#     （head_on は 2 台を通路A 中心線に対向 spawn＝berth と座標が違う。SCENARIO 無指定だと berth
#       座標を publish して AMCL が誤定位し録画が崩れる。座標は sim の head_on_spawn_poses から導出）。
#
#   ▼ 今 達成可能な live デモ（headline）: head_on spawn の正面睨み合いを LLM 司令官が検出し、
#     08a:337-359 の yield+wait で解消する（bot2=yield→navigate retreat_B / bot1=wait 5s →
#     MCP → nav2_bridge → Nav2）。retreat_A/B は KNOWN_LOCATIONS＝named navigate で配線済
#     （nav2_bridge core.py:110-117）＝録画可能。decision の決定論版は #153 で実証済。
#   ▼ NOW WIRED（#223 座標ゴール injector）: 11a:446「相互通過時の最接近 ≥0.15m」の睨み合い→swap を
#     実走可能化する座標ゴール経路を配線済。`nav2_bridge.navigate` が inline 座標 `goal=(x,y[,yaw])` を
#     受理（core.py・additive・両/両無は INVALID_GOAL・yaw drop）＝KNOWN_LOCATIONS に無い隘路整列座標
#     （11a:455）を発行できる。`head_on_injector.HeadOnInjector` が head_on_goals DATA を route_A 排他で
#     直列化（先着 swap→後着は入口待機→解放→後着、11a:446/453/§9.3）。live 操作は
#     `scripts/slice3_inject_swap.sh`（下記「slice3 swap injection + ≥0.15m gate」節）。
#   ▼ DEFERRED（user docker のみ・PENDING）: ≥0.15m の **物理計測**は Gazebo 実走（host harness は物理を
#     持たない＝README:43）。`tests/e2e/test_min_separation_harness.py` が gate ロジックを host 検証し
#     （負例＝同時隘路進入 ~0.07m で FAIL＝teeth あり）、live-recorded `/bot{n}/amcl_pose` stream
#     （`WAREHOUSE_MINSEP_STREAMS`）でのみ ≥0.15m を判定する。

# 3) 録画: rviz:=true ∧ rviz_config:=record で warehouse_sim/rviz/record.rviz（両 footprint+
#    scan+占有 map の俯瞰 cfg。既定 minicar.rviz は最小レイアウト, sim.launch.py:66-75）を選択し、
#    `scripts/slice3_record.sh start` / `... stop` で noVNC/screen-capture をラップ録画する
#    （実キャプチャ＝人間ゲート）。record.rviz は sim 所有＝レイアウト不足は L2(sim) へ予告
#    （本レーンは cfg 自体を変更しない）。

# 4) 4 provider 切替（DoD: fairness）: Hermes config の active_provider を
#    Claude→GPT→Gemini→Grok で切替え各々 slice2 を確認。
#    比較 run は Memory/session_search OFF（#103 fairness・llm_bridge.py:100-102 起動ガード）。
```

**注入手段の現状**:
対向タスクは恒久 producer ではなく **`WAREHOUSE_TASKS` env のデモ用 seed** で注入する（#181）。
`current_task` は dispatch 受理後に scheduler が set し、受理 navigate の `to` 一致で
`pending_tasks` を消費する。恒久 producer は将来 Warehouse Orchestrator #6 / task queue 側で
決める。本 harness は**配線の正しさ**までを証明し、live provider の yield 判断は slice3 で確認する。

## FIXED（#192）非JSON応答 robustness

`HermesClient.decide()` は**非JSON/散文ラップ応答**で `ValueError` を投げる（その明文契約
「malformed body → ignore this cycle」＝`llm_client.py:36-44` / `hermes_client.py:55-70`）。
`scheduler.run_cycle` は `decide()` 由来の `ValueError` / `TypeError` も
`Command.model_validate` 由来の不正 schema と同じ `_on_invalid_response` に流し、cycle ignore とする。
`test_nonjson_reply_is_ignored_no_forward` が **0 forward・loop 生存・Nav2-only 不遷移**を pin する。

## slice3-live host 回帰（live 前にデモ事故を防ぐ・全て host で動く＝ROS/Gazebo 不要）

実 live は人間ゲート（鍵注入・録画・有料）だが、**そこへ持ち込む前の配線**を host で pin する:

- **`test_slice3_initialpose_seed.py`** — `slice3_seed_initialpose.sh` の `DRY_RUN` 出力が
  scenario と整合することを pin: `SCENARIO=default` → berth_A/berth_B（config locations）・両者南向き、
  `SCENARIO=head_on` → sim の `head_on_spawn_poses` と**完全一致**（中心線同一 x・対向 y・bot2 は北向き）。
  これにより「head_on で berth 座標を seed → AMCL 誤定位」のデモ事故を回帰で塞ぐ（上記 step 2 の seed）。
- **`test_slice3_task_seed_forward.py`** — `WAREHOUSE_TASKS`→`parse_seed_tasks`→`pending_tasks`→実
  `SituationBuilder`→commander→`navigate` forward & consume の #181 連鎖を slice2 と同じ本番シームで
  end-to-end 検証（従来 unit のみ＝`test_bridge_scheduler.py`）。司令官が見る situation に seed が surface
  し、受理 navigate が `to` 一致タスクを消費することを pin。
- **`test_slice3_precheck_offline.py`** — `slice3_live_precheck.sh` の seed バリデータ拒否クラス
  （<2 task / KNOWN_LOCATIONS 外 / 重複 id / from==to / 宛先<2＝二台同目的）を subprocess で pin
  （従来 pytest 未カバー＝人間が走らせて初めて分かる退行を CI 化）。
- **precheck `--offline` の追加静的チェック** — `bringup.launch.py` が sim include に
  `scenario`/`rviz_config` を forward していること、seed と snapshot チェックの bot namespace が
  一致することを grep で確認（録画前の最後の砦・unit pin の belt-and-suspenders）。

## slice3 swap injection + ≥0.15m min-separation gate（#223 座標ゴール injector）

slice3-live runbook の DEFERRED「相互通過時の最接近 ≥0.15m」（doc11a:446 §9）を実走可能化する。
**座標ゴール経路**（`warehouse_nav2_bridge`）＝ `nav2_bridge.navigate(robot, goal=(x,y))`（additive・
`INVALID_GOAL`）＋ `head_on_injector.HeadOnInjector`（head_on_goals DATA を route_A 排他で直列化）。
実測 ≥0.15m は **Gazebo 物理＝user docker 専有**（README:43）。本節は WIRING のみ。

```bash
# 前提: step 2 の full stack（sim:=true llm:=true ... scenario:=head_on）が up かつ head_on で seed 済
#       （Nav2 Bridge :8645 は bringup が in-process 合成＝別途起動しない、step 0 の二重 bind 注意）。

# A) swap を発行（head_on_goals DATA を導出→座標 goal を REST /api/v1/navigate へ・status-poll で直列化）
#    DRY_RUN=1 は API を叩かず導出ゴールと計画リクエストを print（host で確認可・ros2/curl 不要）。
DRY_RUN=1 scripts/slice3_inject_swap.sh         # → bot1/bot2 の swap 座標と POST body を表示
scripts/slice3_inject_swap.sh                    # live: 先着 bot を発行→通路クリア待ち→後着 bot を発行
#   ▼ 直列化（fail-closed）: bot1 の goal が **succeeded**（南端到達＝隘路を抜けた）まで bot2 を出さない
#     ＝両機が隘路に同時進入しない＝ ≥0.15m が成立する唯一の幾何（11a:446）。bot1 が **failed**（Nav2 abort
#     ＝隘路内で停止しうる）/ timeout なら bot2 を出さず exit 2（#218 B1）。in-process の lock 版は
#     HeadOnInjector（unit 実証）。

# B) ≥0.15m を計測（user docker のみ）: swap 中に両 bot の /amcl_pose を JSON へ記録し gate にかける。
#    記録形式: {"bot1": [[x,y],...], "bot2": [[x,y],...]}（時刻同期した (x,y) サンプル列）。
#    例（別 shell で swap と並行に echo を JSON 整形して保存する等、録画オペレータが用意）:
export WAREHOUSE_MINSEP_STREAMS=/tmp/warehouse/minsep_streams.json
python3.12 -m pytest tests/e2e/test_min_separation_harness.py::test_live_recorded_swap_meets_min_separation -v
#   → 最接近距離 ≥0.15m を assert（doc11a:446）。host では stream 未指定＝skip（gate ロジックは host 検証済）。
```

**注**: 座標ゴールは `/bot{n}/goal_pose` を **LLM Bridge が直接 publish しない**（doc03:97 / doc08:464）方針と整合
＝発行主体は nav2_bridge（Mode A/B の MCP 内部経路に相当）。raw goal_pose publisher は新設しない。

## collision_monitor 2-bot 近接停止 PoC runbook（live · 人間 Docker ゲート）

`#229`（`bce4853`）で配線した `nav2_collision_monitor`（per-bot・twist_mux prio10 上流・Mode A/B）の
**物理近接反射**（R-39, `doc07:249`）を 2台 Gazebo で実走確認し、`#233` の **live PoC ゲート**（#126 は CLOSED 済・#233 で追跡）
（Open ②③ の live tune ＋ Go/No-Go）を満たすための runbook。設計正本:
`docs/architecture/12-infrastructure-common.md:522-552`（トポロジ）＋ 同 §「Open 項目の確定（#233）」
（⑤⑥① の確定）。実走＝**Gazebo 物理＝user docker 専有**（README:43）＝本節は手順のみ・観測値は実走後に
転記（**fabricate しない**＝docs-first「発明しない」）。

> **前提（false-pass 警告）**: collision_monitor は **Mode A/B でのみ起動**（`traffic_mode:=none|simple`）。
> Mode C（`open-rmf`）は monitor を **gate OFF**（`nav2_bringup.launch.py:126` / `collision_monitor.yaml:22-25`）
> ＝`open-rmf` で「近接でも止まらない」を **pass と誤読しない**（monitor がそもそも居ない）。

```bash
# Step 0: tiryoh コンテナ（ROS/colcon/Gazebo）・--memory=6g・ROS_DOMAIN_ID 隔離。host py3.7 では不可。
export WAREHOUSE_CONFIG_DIR=/ws/config
export WAREHOUSE_ENV=dev

# Step 1: 2台フルスタックを Mode A/B で bring-up（LLM 不要＝近接反射は司令官非依存）。
ros2 launch warehouse_bringup bringup.launch.py sim:=true llm:=false traffic_mode:=none rviz:=true
#   ▼ head_on 幾何で当てたい場合は scenario:=head_on を足し、別 shell で seed（AMCL 誤定位回避）:
#     SCENARIO=head_on scripts/slice3_seed_initialpose.sh   （berth 座標 seed → AMCL 誤定位の事故を塞ぐ）

# Step 2: 近接停止を観測（別 shell）。collision_monitor の state / polygon / 出力 0 化を見る。
ros2 topic echo /bot1/collision_monitor_state    # PolygonStop breach で state が立つ（state_topic, yaml:56）
#   RViz: collision_monitor/polygon_stop（polygon_pub_topic, collision_monitor.yaml:72）を表示。
#   ▼ 2台を polygon 内へ近づける（VirtualScan は >1.0m で無送信＝近接時のみ相手を見る）。breach 中は
#     controller 出力（cmd_vel/nav2_raw）が monitor で 0 化され cmd_vel/nav2 が zero-Twist になる:
ros2 topic echo /bot1/cmd_vel/nav2               # breach 中は linear/angular = 0

# Step 3 (Open ②): PolygonStop circle radius（暫定 0.09, collision_monitor.yaml:68）を live tune。
#   制約: R-42 200mm 隘路（壁 ~0.1m）で壁に trip しない ∧ #156 ≥0.15m head-on で相手表面 ~0.075m を
#   潰して avoidance footage を mask しない（0.15m centres で 0.075m は 0.09 polygon 内＝要調整）。
#   forward-biased polygon（円→前方寄せ）も選択肢。値変更は collision_monitor.yaml を編集し再 launch。

# Step 4 (Open ③ + source_timeout sanity): /scan 鮮度の停止挙動を 2 方向で確認（「正しく silent」と
#   「monitor 死亡」を区別する）。
#   (a) 実 /scan 途絶 → STOP 発火: /bot1/scan の publisher（sim lidar bridge）を止め、node-level
#       source_timeout 1.0（yaml:58）経過後に cmd_vel/nav2 が止まることを確認（lidar 途絶 stop, R-39）。
#   (b) virtual_scan の >1.0m 無送信 → STOP 非発火: 2台を 1.0m 超に離し、通常走行で誤 STOP しないことを
#       確認（per-source source_timeout 0.0, yaml:90 ＝「無送信＝相手居ない」を fault 扱いしない）。
```

**注**: 本 PoC は **物理反射（C++・amcl 非律速）** の検証であり、Guardian の policy 層（battery / event /
`pose_stale`(#152) / blocked）と twist_mux emergency prio100（estop）は **対象外・不変**。collision_monitor
出力は `cmd_vel/nav2`（prio10）**のみ**で `cmd_vel/emergency`（prio100）を**書かない**
（`tests/unit/test_collision_monitor_config.py` で凍結）。event surfacing（⑤）は defer、Guardian
`near_collision` 撤去（①）は本 PoC **Go 後の safety-state 判断**（doc12 §「Open 項目の確定（#233）」）。

### Go/No-Go（PENDING — 実走後に転記・fabricate しない）

| 観測 | 期待 | 値 | 判定 |
|---|---|---|---|
| ① R-39 reflex 成立 | 近接（polygon breach）で `cmd_vel/nav2` が zero-Twist | — | — |
| ② R-42 隘路 非誤発火 | 200mm 隘路の壁（~0.1m）で STOP しない（通行可） | — | — |
| ③ #156 head-on 非mask | ≥0.15m head-on で avoidance footage を polygon が潰さない | — | — |
| ④ source_timeout 正 | 実 /scan 途絶→STOP ∧ virtual_scan >1.0m 無送信→非STOP | — | — |

**GO** ＝ ①∧②∧③∧④ 全充足 → `doc12` / `doc03` / `doc07 R-39` に Go/No-Go と採用形（polygon 確定寸法・
source_timeout 確定値）を反映する **follow-up PR**（#126 は CLOSED 済＝`Refs #126`）。いずれか **No** → polygon 再寸法（②）／
source_timeout 再調整（③）／forward-bias 化を tune し再走（値が定まらなければ defer を記録）。

### #233 node-isolated 中間結果（L4 operator, 2026-06-15）— full-sim 2台は PENDING のまま

> 上の Go/No-Go 表（:252-263）は **full-sim 2台 PoC** の転記欄。**fabricate しない**ため空欄を維持。
> 以下は **node-isolated**（実 `nav2_collision_monitor` 1.3.11 + 実 `collision_monitor.yaml` を controlled
> scan/cmd で駆動）で **config + node logic** を先行検証した中間結果＝full-sim 幾何（virtual_scan 自動生成・
> AMCL・接近動力学）は含まない。表の ①〜④ は引き続き human gate。詳細: #233 コメント。

- **① reflex（config 層）= GO**: breach（stop polygon 内 ≥`min_points` 4）→ `cmd_vel/nav2` 0.000 + `CollisionMonitorState{PolygonStop, action=1}`。
- **④ source_timeout（config 層）= GO（両方向）**: 実 `/scan` 途絶 → `invalid source` STOP at dropout + ~1.0s（実測 0.99–1.04s ＝ node `source_timeout 1.0`）∧ `virtual_scan` 無送信（per-source `0.0`）→ 非 STOP（passthrough 0.200）。
- **②③ = PENDING（full-sim + RViz human gate）**: ③ は `radius 0.09` 円で幾何 No-Go（:234-236）。
  **Open ② 採用候補 = forward-biased polygon**（`type:"polygon"` / y 半幅 0.06 / x_far live-tune knob）を #233 で起案し、node-isolated で「前方=止／側方=通」を実証。`collision_monitor.yaml` は nav-traffic 所有（doc16:193）→ 予告＋合意の上で反映。

#### 残作業（#233 を閉じるまでのチェックリスト）
- [ ] nav-traffic owner 合意 → forward-biased polygon を `collision_monitor.yaml` に反映（形のみ・凍結契約不変）。
- [ ] full-sim 2台で ②③ を live tune（x_far 確定）＋ ①④ を full-sim で再確認 → 上の Go/No-Go 表（:256-259）を **実走後に転記**。
- [ ] GO 確定後: doc12（Open ②③ 確定）/ doc03（cmd_vel topology）/ doc07 R-39 に採用形を反映。
- [ ] 注: **#126 は既に CLOSED**（2026-06-11 COMPLETED）＝「Closes #126」は不要。live PoC は #233 で追跡。
