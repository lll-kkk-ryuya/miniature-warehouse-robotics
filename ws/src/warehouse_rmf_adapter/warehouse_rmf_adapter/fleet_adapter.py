"""warehouse_rmf_adapter.fleet_adapter — GATE-前 設計スキャフォールド（実装なし）.

Mode C 案A（R-44 採用方針）の自作 EasyFullControl Fleet Adapter の **設計骨子のみ**。
本モジュールは docstring + シグネチャ + ``NotImplementedError`` スタブであり、実装・ビルド・
live 駆動は **R-38 メモリゲート（#187, docs/shared/07-research-notes.md:243）通過後** に行う
（docs/mode-c/11c-traffic-mode-c.md:273 §3.5 D / docs/architecture/06-implementation-phases.md:215-221）。

設計正本（たどれる file:line）:
  - 採用方針・案A 詳細・Go/No-Go: docs/mode-c/11c-traffic-mode-c.md:243-265（§3.5 B/C）
  - 3 コールバック（navigate/stop/execute_action）+ RobotState 更新: 11c:252
  - 不変条件「Fleet Adapter が唯一の Nav2 制御パス」: 11c:63
  - Nav2 直駆動トピック契約 /bot{n}/goal_pose（PoseStamped, Fleet Adapter 発行）:
    docs/architecture/03-software-architecture.md:97
  - RMF Traffic Schedule / Conflict Negotiation = 使用 / Task Dispatcher = 無効化:
    11c:197-199
  - jazzy 在中（rmf_fleet_adapter 2.7.2）: 11c:253
  - 残未決 6 点（実装 spike で要確認）: 11c:278-286 ＋ 11c 末尾「付録: §3.5 GATE-前 ステータス」

GATE-前の制約（本レーン feat/rmf-adapter / #180）:
  - RMF（rmf_fleet_adapter / EasyFullControl）も rclpy も **import しない**（apt は GATE 後）。
    実型（NavigateToPose.Goal / EasyFullControl / RobotUpdateHandle / Destination 等）は
    docstring 内に明記し、Python のシグネチャは stdlib 型で表す。
  - 凍結契約 warehouse_interfaces は **変更しない**。waypoint/lane を発明しない（11c:283）。
"""

from __future__ import annotations

# GATE-前は実 import を行わない（rmf_fleet_adapter / rclpy / nav2_msgs は GATE 後に apt）。
_GATE_MSG = (
    "warehouse_rmf_adapter は GATE-前 設計スキャフォールド（実装なし）。"
    "実装は R-38 メモリゲート（#187）通過後（11c:273 §3.5 D / #180 GATE後）。"
)


class WarehouseRmfFleetAdapterDesign:
    """Mode C 案A 自作 EasyFullControl Fleet Adapter の設計骨子（GATE-前・未実装）.

    責務（11c:251-256 §3.5 B 案A）:
      1. ``rmf_fleet_adapter`` の ``EasyFullControl`` を 1 プロセスで初期化し、本プロジェクトの
         2 ロボット（``/bot1`` ``/bot2``）を **同一プロセス内** の namespace 毎 Nav2
         ``NavigateToPose`` action client で駆動する（zenoh 無し, 11c:252）。
      2. EasyFullControl の 3 コールバック ``navigate`` / ``stop`` / ``execute_action`` と
         ``RobotState`` 更新を実装する（11c:252）。
      3. **不変条件**: 本 adapter が ``/bot{n}`` Nav2 の **唯一の writer**（11c:63）。Nav2 ゴールは
         doc03:97 の ``/bot{n}/goal_pose``（``geometry_msgs/PoseStamped``,「Fleet Adapter 発行」）
         契約に対応し、実体は ``NavigateToPose`` action goal で送る（topic 契約 ↔ action 機構の
         関係は下記「設計メモ」参照。doc03:97 を変更せず、warehouse_interfaces に action 型を
         足さない）。
      4. RMF 交通管理（Traffic Schedule / Conflict Negotiation）は adapter の背後の RMF core が
         担い、Task Dispatcher は無効化（Claude がタスク割当）— 11c:197-199。本 adapter は
         その配線先であって交通管理ロジックは持たない。

    最大の未証明前提（11c:279 残未決1）:
      「EasyFullControl + namespace 毎 in-process Nav2 action client」の end-to-end 実例は文献上
      未確認。canonical 例（``rmf_demos_fleet_adapter``）は外部 fleet manager を REST で駆動し、
      Nav2 action 直叩きは free_fleet の ``nav2_robot_adapter.py``（ただし zenoh 経由）のみ。両者を
      合成（rmf_demos の足場 + free_fleet の ``NavigateToPose`` 構築ロジック − zenoh）して自作する。

    設計メモ（topic 契約 ↔ action 機構）:
      doc03:97 の契約は「Fleet Adapter が ``/bot{n}/goal_pose`` を発行」（PoseStamped）。一方
      11c:252 の機構は ``NavigateToPose`` **action**（feedback/result が EasyFullControl の
      CommandExecution に必要）。どちらでも「adapter が唯一の Nav2 writer」（11c:63）は満たす。
      goal_pose topic か NavigateToPose action かの最終確定は GATE 後の impl で行う（本スキャフォールド
      では契約を変えず両者の対応のみ記述。発明しない）。

    本クラスは **設計の置き場** であり、全メソッドは ``NotImplementedError`` を送出する。実装
    （action client 配線・2 namespace 駆動・RMF Navigation Graph 整合）は R-38 GATE 後。
    """

    def __init__(self) -> None:
        """GATE-前は初期化しない（RMF/rclpy を持ち込まない）.

        GATE 後の設計意図:
          - ``EasyFullControl.FleetConfiguration`` を構築（jazzy: rmf_fleet_adapter 2.7.2, 11c:253）。
          - ロボット ``bot1`` / ``bot2`` を登録し、各々の ``RobotUpdateHandle`` を保持。
          - 各 namespace の Nav2 action client（``/bot1/navigate_to_pose`` 等）を生成
            （1 プロセス 2 namespace の駆動は integrator 実装＝11c:280 残未決2）。
        """
        raise NotImplementedError(_GATE_MSG)

    def navigate(self, robot_name: str, destination: str) -> None:
        """EasyFullControl ``navigate`` コールバック（設計骨子・GATE 後実装）.

        RMF が算出した行き先を受け取り、当該ロボット namespace の Nav2 ``NavigateToPose`` action
        goal（``geometry_msgs/PoseStamped``）に変換して送る（11c:252）。

        - 実型（GATE 後）: ``destination`` は RMF の ``Destination``（pose / waypoint index）。本
          設計骨子では location 名キー（str）で表し、座標解決は ``warehouse_interfaces.config.load_config``
          （config.py:114）に委ねる。**凍結なのは名キーの正準集合** ``KNOWN_LOCATIONS``（locations.py:23）で
          あり、座標 {x,y} は凍結でなく ``config/warehouse.base.yaml`` の暫定値（Phase 2 実測で確定）。
          waypoint/lane を発明しない（11c:283 残未決5）。
        - 唯一の Nav2 writer 制約（11c:63）を満たす（他経路から goal を出さない）。
        - 模倣元: free_fleet ``nav2_robot_adapter.py`` の ``NavigateToPose`` 構築（zenoh 抜き,
          11c:279-280）。
        """
        raise NotImplementedError(_GATE_MSG)

    def stop(self, robot_name: str) -> None:
        """EasyFullControl ``stop`` コールバック（設計骨子・GATE 後実装）.

        当該 namespace の Nav2 ``NavigateToPose`` goal を cancel する（in-process action client）。
        本 adapter は RMF 起点の停止のみを扱う。物理安全停止の最終保証は ESP32 Layer0 /
        Emergency Guardian であり別系統（docs/architecture/12-infrastructure-common.md）。
        """
        raise NotImplementedError(_GATE_MSG)

    def execute_action(self, robot_name: str, category: str, description: dict) -> None:
        """EasyFullControl ``execute_action`` コールバック（設計骨子・GATE 後実装）.

        navigate 以外の RMF アクション（dock / wait / custom）のフック。本プロジェクトは Task
        Dispatcher を無効化し Claude がタスク割当を担当する（11c:199）ため、GATE 後に必要最小
        （待機など）のみ実装する想定。現時点は設計の置き場。
        """
        raise NotImplementedError(_GATE_MSG)

    def update_robot_state(self, robot_name: str) -> None:
        """``RobotState`` 更新（設計骨子・GATE 後実装）.

        ``/bot{n}/amcl_pose``（doc03:94 PoseWithCovarianceStamped）/ ``/bot{n}/odom``
        （doc03:77 Odometry）/ battery を読み、RMF の ``RobotUpdateHandle`` に位置・バッテリを
        反映する（RMF Traffic Schedule が経路デコンフリクトに使用, 11c:197-198）。本 adapter は
        これらトピックの **consumer**（読むだけ）であり producer ではない。
        """
        raise NotImplementedError(_GATE_MSG)
