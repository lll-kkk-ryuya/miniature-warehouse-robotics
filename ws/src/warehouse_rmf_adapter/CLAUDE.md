# warehouse_rmf_adapter — Mode C 案A 自作 EasyFullControl Fleet Adapter（offline core 実装済 / RMF 配線は GATE-前）

> **状態: RMF/EasyFullControl/rclpy 配線・live・sim・HW は R-38 メモリゲート（#187 OPEN）で引き続き
> BLOCKED（docs/mode-c/11c:273 §3.5 D）。** 一方、**RMF/rclpy に依存しない offline コア**
> （routing / namespacing / single-writer = `nav2_router` / `robot_driver` / `fleet`）は **GATE-前に
> host 実装・unit 済**（#180、ユーザー指示で先行）。`fleet_adapter.py` の EasyFullControl shell は
> 引き続き `NotImplementedError`（RMF 登録・action client 実体化＝**11c:279 残未決1 の end-to-end は
> #187 ゲート後**）。`colcon build`・apt は依然 GATE 後。

- **担当トラック / ブランチ**: nav-traffic / `feat/rmf-adapter`（worktree `mwr-rmf-adapter`・track #180）
- **Phase**: 3 後半（Open-RMF 導入。docs/architecture/06-implementation-phases.md:215-221）
- **ビルド**: ament_python（**GATE 前はビルドしない**。CI は colcon を回さず ruff/pytest/consistency のみ）
- **編集境界**: このパッケージ配下のみ ＋ docs/mode-c/11c-traffic-mode-c.md（§3.5 関連の **末尾 EOF 追記のみ**）。
  共有契約 `warehouse_interfaces` は変更不可（`.claude/rules/parallel-workflow.md` §4）。`warehouse_nav2_bridge`
  （案B・llm-bridge 所有）/ nav2 launch・params（別ファイル新規優先・GATE 後）は触らない。
- **設計正本**:
  - docs/mode-c/11c-traffic-mode-c.md:203-296（§3.5 R-44 評価・案A/案B・Go/No-Go・残未決 D、§4 は :299）／ `:63`（不変条件）／ `:197-199`（RMF 機能の使用/無効化）／ `:252`（3 コールバック + NavigateToPose action client）
  - docs/shared/07-research-notes.md:254（R-44 結論）／ `:243`（R-38 ゲート＝blocker）
  - docs/architecture/03-software-architecture.md:97（`/bot{n}/goal_pose` PoseStamped, Fleet Adapter 発行）／ `:77`（odom）／ `:94`（amcl_pose）
  - docs/mode-c/12c-integration-mode-c.md:142（`NAV2_BRIDGE_MODES`）／ `:202`（既存フォールバック「直接 ROS 2 Action Client」＝案A の実体）
  - ws/src/warehouse_nav2_bridge/CLAUDE.md:18-20（案B REST 経路の実在）

## モジュール構成
**offline コア（実装済・host unit 済・RMF/rclpy 非 import）**:
- `warehouse_rmf_adapter/nav2_router.py` — `Nav2Goal`（dataclass）/ `LocationResolver`（凍結
  `KNOWN_LOCATIONS` で名キー検証 → config 座標で `Nav2Goal` 解決・未登録座標は raise）/ `namespace_for`
  / `nav2_action_name`（`/bot1/navigate_to_pose`, 11c:252）/ `MAP_FRAME="map"`（robot_dimensions.py:7）。
- `warehouse_rmf_adapter/robot_driver.py` — `Nav2ActionPort`（Protocol＝注入される rclpy ActionClient の
  seam）/ `RobotDriver`（1 namespace = 1 port = 唯一の writer 11c:63、navigate=resolve→send / stop=cancel、
  port namespace 不一致を拒否）。
- `warehouse_rmf_adapter/fleet.py` — `WarehouseFleet`（config `robots` から 1 プロセス 2 namespace を構築
  ＝11c:280 残未決2 の core、navigate/stop を namespace 振り分け、重複 namespace 拒否、`writers()` で
  namespace ごと厳密 1 writer を表明）。

**GATE-時 shell（未実装・`NotImplementedError`）**:
- `warehouse_rmf_adapter/fleet_adapter.py` — `WarehouseRmfFleetAdapterDesign`。EasyFullControl の
  `navigate` / `stop` / `execute_action` + `update_robot_state` の設計骨子。GATE 後に RMF を import し
  上記 offline コアへ委譲する（docstring に委譲先 file を明記）。RMF/rclpy/nav2_msgs を **import しない**。

## 消費 (consume)
- 契約（offline コアが実 import）: `warehouse_interfaces.locations.is_known_location` / `KNOWN_LOCATIONS`
  （locations.py:23/26）で destination 名キーを検証。座標は呼び出し側が渡す config dict（`locations` セクション）
  から読み、GATE-時は `warehouse_interfaces.config.load_config`（config.py）で `robots` / `locations` を供給。
  **凍結なのは location 名キーの正準集合** `KNOWN_LOCATIONS`（locations.py:23, `frozenset[str]`）。
  **座標 {x,y} は凍結ではない**＝`load_config` 経由で `config/warehouse.base.yaml:35-44` から解決し、同 `:34` の
  とおり **Phase 2 実測で確定する暫定値**。**契約変更なし**（座標を凍結契約に足さない）。
  RMF Navigation Graph の waypoint/lane を凍結 `locations`（名キー契約）に**発明しない**（要れば別 contract PR。11c:283 残未決5）。
- topic（consume・GATE 後）: `/bot{n}/amcl_pose`（doc03:94 PoseWithCovarianceStamped）・`/bot{n}/odom`
  （doc03:77 Odometry）・`/bot{n}/battery` — `RobotState` 反映用に **読むのみ**（producer ではない）。
- RMF core: `rmf_traffic` schedule / negotiation（adapter の背後・11c:256）。配線負荷は未定量（11c:282 残未決4）。

## 生産する契約 / トピック (produce)
- offline 型: `Nav2Goal`（`nav2_router`・パッケージ内部 dataclass）— GATE-時に `NavigateToPose.Goal`
  （PoseStamped, doc03:97）へ写す中間表現。**凍結契約ではない**（`warehouse_interfaces` に足さない）。
- topic: `/bot{n}/goal_pose`（doc03:97 `geometry_msgs/PoseStamped`,「モードC: Fleet Adapter 発行」）の
  **唯一の writer**（不変条件 11c:63）。実機構は namespace 毎 Nav2 `NavigateToPose` action goal（11c:252）。
  topic ↔ action のどちらで確定するかは GATE 後 impl（doc03:97 を変えず両者の対応のみ記述。発明しない）。
- RMF Navigation Graph（通路 2-3 本・手動定義）— **契約外**（`warehouse_interfaces` には足さない）。
- **契約変更の有無（GATE-前）: なし**（凍結 `warehouse_interfaces` 無編集・新トピック/型/閾値を発明しない）。

## 依存
- `warehouse_interfaces` のみ（凍結契約）。他トラック内部を import しない（parallel-workflow.md §2.1）。
- GATE 後に追加（apt）: `rmf_fleet_adapter` / `rmf_fleet_adapter_python` / `rmf_task_ros2` ＋ `nav2_msgs` /
  `action_msgs`（package.xml に TODO コメントで宣言済）。

## テスト
- **offline コア: host unit 済（RMF/ROS 無し・doc16 §11）** — `tests/` 配下に収集（pytest testpaths=["tests"]、
  conftest が `ws/src/<pkg>` を sys.path に追加）:
  - `tests/unit/test_rmf_adapter_router.py` — 名前解決 / namespace・action 名 / 凍結 location 検証 /
    未登録座標 raise / 全 9 凍結 location が base config に座標を持つ（config↔contract 被覆）。
  - `tests/unit/test_rmf_adapter_fleet.py` — 1 プロセス 2 namespace 構築 / **唯一 writer**（namespace ごと 1 port）/
    navigate は対象 namespace のみ駆動 / stop は対象のみ cancel / **不正 destination は何も actuate しない（fail-closed）** /
    重複 namespace・port 不一致を拒否（`@pytest.mark.safety`）。
  - `tests/unit/test_rmf_adapter_offline_imports.py` — offline 3 module + shell が rclpy/rmf_*/nav2_msgs を
    import しない AST ガード（host-runnable 不変条件）。
- **GATE 後（R-26）**: EasyFullControl 登録・action client 実体化を含む安全機構（停止・速度・stale）はユニット必須。
  2 台 Open-RMF E2E / Jetson メモリ実測（cgroup 計測）は tiryoh/実機（docs/architecture/16-repository-and-conventions.md:211-215 のテスト戦略 §11）。

## 前提・未確定 (TODO / 残未決) — 11c:278-286 が出所
- **offline 先行実装（#180・ユーザー指示）**: RMF 非依存の routing / namespacing / single-writer ロジックを
  GATE-前に host 実装・unit 済（上記モジュール構成）。**de-risk したのは周辺ロジックのみ**であり、Mode C の
  live 成立可否（メモリ・RMF 配線・2台 E2E）は **依然 #187 ゲート後**。No-Go なら本コアごと不要になりうる。
- **着手可否（live 部分）は未決**: R-38 #187 が OPEN（Go/No-Go 未確定）。No-Go なら Mode B 格下げ /
  Open-RMF 別マシン offload に分岐し、本 adapter 自体が不要になりうる（07:243）。
- `# TODO(R-38 GATE後 / #187)` 実装項目（11c:279-284。〔offline 被覆〕は今回の host 実装範囲）:
  1. EasyFullControl + namespace 毎 in-process Nav2 action client の end-to-end 実証（**最大の未証明前提・未着手**。
     〔offline 被覆: action 名生成 / goal 構築 / 注入 seam 経由の send・cancel まで。実 RMF/action server は GATE 後〕）。
  2. 1 プロセスから `/bot1` `/bot2` 両駆動の namespacing（integrator 実装）。〔offline 被覆: `WarehouseFleet` が
     config から 2 namespace 構築・振り分け・単一 writer を表明。live の 2-namespace 駆動は GATE 後〕。
  3. `ros-jazzy-rmf-fleet-adapter` バイナリ版 ↔ jazzy ブランチ source の API pin（実 apt は GATE 後・**未着手**）。
  4. `rmf_traffic` schedule/negotiation 配線負荷（Navigation Graph / traffic profile / footprint）の定量（**未着手**）。
  5. RMF Navigation Graph ↔ 9 locations / Gazebo 地図の整合（waypoint/lane を発明しない）。〔offline 被覆: 全 9
     凍結 location が base config に座標を持つことを unit で検証。RMF Navigation Graph の lane/waypoint は GATE 後〕。
  6. 200mm 隘路（#124）・≤0.3 m/s で RMF デコンフリクトが有効か sim 検証（**未着手・人手 Docker**）。
- **governance（本レーン編集境界外＝orchestrator 調整・tracked: #221）**:
  - **④ CI 越境 import チェックの `tracks` map（`.github/workflows/ci.yml`）= 完了済**（#222 / `a0ce17a` で
    `warehouse_rmf_adapter` を nav-traffic に登録。本 PR 着地まで inert）。
  - 残 doc16 **3 箇所**（#221・行挿入が inbound file:line 参照をシフトするためクロストラック扱い・別 PR）:
    ① §1 ディレクトリツリー（docs/architecture/16-repository-and-conventions.md:24-51・`warehouse_traffic/` の隣）
    ② §2 パッケージ命名・責務一覧（`:60-77`・`warehouse_traffic`/`warehouse_nav2_bridge` 行の隣）
    ③ §9 ブランチ表（`:182-191`・§9 見出し `:178`・`feat/nav-traffic` 行に担当ディレクトリ追記）
    現状 `warehouse_interfaces` のみ依存（実 import は `from __future__` のみ）で CI は通る（④登録済でも import 検査は inert）。①〜③は #221 で nav-traffic として揃える。
  - #180 の `Blocked by` が「R-38（issue 無し）」表記 → #187 へ張替推奨。
  - issue #180 本文の worktree タグは `mwr-modec-fleet` / `feat/modec-fleet-adapter`（本レーンの実名
    `mwr-rmf-adapter` / `feat/rmf-adapter` と不一致）→ orchestrator で統一要。
