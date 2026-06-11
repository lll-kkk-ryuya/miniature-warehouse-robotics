# 交通管理レイヤー — Mode A/B（LLM単独 / 自作ルールベース）

作成日: 2026-05-22
更新日: 2026-05-25

> 関連ドキュメント: [Mode C（Open-RMF）](../mode-c/11c-traffic-mode-c.md) | [共通インフラ](../architecture/12-infrastructure-common.md)

## 概要

交通管理レイヤーをプラグイン方式で設計する。本ドキュメントではモードA（Claude単独）とモードB（自作ルールベース）を扱う。モードAではClaudeが交通管理も含めた全判断を担当し、モードBでは通路の排他制御を自作SimpleTrafficManagerが即時処理し、Claudeは戦略判断を行う。config.yamlの1行変更でモード切替が可能（YouTube比較検証用）。

---

## 1. TrafficManager共通インターフェース

```python
from abc import ABC, abstractmethod

class TrafficManager(ABC):
    """交通管理の共通インターフェース"""

    @abstractmethod
    def submit_task(self, robot: str, pickup: str, dropoff: str, priority: str = "normal") -> dict:
        """タスクを送信し、調整結果を返す"""
        pass

    @abstractmethod
    def get_traffic_state(self) -> dict:
        """現在の交通状態を返す（Claudeの状況JSONに含める）"""
        pass

    @abstractmethod
    def get_conflicts(self) -> list:
        """進行中の衝突とその対処を返す"""
        pass
```

### config.yaml切替方法

```yaml
# config.yaml
traffic_mode: "none"       # モードA: Claude単独
# traffic_mode: "simple"   # モードB: 自作ルールベース
# traffic_mode: "open-rmf" # モードC: Open-RMF
```

```python
# llm_bridge_node.py
MANAGERS = {
    "none": NoTrafficManager,
    "simple": SimpleTrafficManager,
    "open-rmf": RMFTrafficManager,
}
traffic = MANAGERS[config["traffic_mode"]]()
```

---

## 2. モードA: 交通管理なし（Claude単独）

Claudeの指示をWarehouse MCP Server経由でNav2に送る。交通管理・衝突予測はClaude自身が状況JSONから判断する。Nav2への経路: Hermes → Warehouse MCP Server → BasicNavigator Bridge → Nav2。

```python
class NoTrafficManager(TrafficManager):
    def submit_task(self, robot, pickup, dropoff, priority="normal"):
        # Nav2 Bridge は単一目的地を受け付けるため、dropoff のみ送信する。
        # pickup はロボットの現在地であることを前提とする（Warehouse MCP Server の
        # allocator が pickup に最も近いロボットを選択するため、ロボットは既に
        # pickup 地点またはその近傍にいる）。ロボットが pickup 地点にいない場合は
        # allocator の select_best() で別のロボットが選ばれるか、Claude が
        # 事前に navigate(destination=pickup) を指示してロボットを移動させる。
        self.nav2_bridge.navigate(robot, dropoff)
        return {"status": "sent", "adjustments": None}

    def get_traffic_state(self):
        return {"mode": "none", "aisles": {}, "conflicts": []}

    def get_conflicts(self):
        return []
```

Claudeに渡すtraffic:
```json
{"mode": "none", "aisles": {}, "conflicts": []}
```

---

## 3. モードB: 自作ルールベース交通管理

通路の排他制御（ロック）のみを行う軽量な交通管理。衝突の検出と待機指示は自動、戦略判断はClaudeが行う。Nav2への経路: Hermes → Warehouse MCP Server → BasicNavigator Bridge → Nav2（モードAと同じ）。

```python
class SimpleTrafficManager(TrafficManager):
    def __init__(self):
        self.aisle_locks = {}  # {"route_A": "bot1", "route_B": None}

    def submit_task(self, robot, pickup, dropoff, priority="normal"):
        route = self.plan_route(pickup, dropoff)
        for aisle in route:
            occupant = self.aisle_locks.get(aisle)
            if occupant and occupant != robot:
                return {
                    "status": "waiting",
                    "reason": f"{aisle} occupied by {occupant}",
                    "wait_for": aisle
                }
        for aisle in route:
            self.aisle_locks[aisle] = robot
        self.nav2_bridge.navigate(robot, dropoff)  # 単一目的地をNav2 Bridgeに送信
        return {"status": "sent", "adjustments": None}

    def release_aisle(self, bot, aisle):
        """ロボットが通路を通過したらロックを解放"""
        if self.aisle_locks.get(aisle) == bot:
            self.aisle_locks[aisle] = None

    # ロック解放トリガー（Phase 3実装時に確定）:
    #   候補A: Nav2のゴール到達コールバックで解放
    #   候補B: ロボット位置が通路外に出たことをposition監視で検出
    #   候補C: タイムアウト（一定時間後に自動解放、デッドロック防止）
    #   → A + C を推奨（正常時=A, 異常時=C）。#125 デモの確定値は §9（一般 planner は Phase 3）

    def get_traffic_state(self):
        return {
            "mode": "simple",
            "aisles": {
                aisle: {"status": "occupied" if robot else "free", "robot": robot}
                for aisle, robot in self.aisle_locks.items()
            },
            "conflicts": self.get_conflicts()
        }
```

Claudeに渡すtraffic:
```json
{
  "mode": "simple",
  "aisles": {
    "route_A": {"status": "occupied", "robot": "bot1"},
    "route_B": {"status": "free"}
  },
  "conflicts": []
}
```

---

## 4. 必要な自作コンポーネント一覧

- **Multi-Robot Costmap Layer**（Nav2レベルの衝突回避、モードA/B共通）
- **predicted_position_3s**（CTRV 外挿、モードA/B共通）
- **デッドロック検出ロジック**（LLM Bridge内、モードAで必須）
- **SimpleTrafficManager本体**（Python、モードBで必須）

---

## 5. Multi-Robot Costmap Layer 詳細設計

### 方式: 仮想 LaserScan 注入

相手ロボットの位置を仮想 LaserScan メッセージとして自ロボットの Nav2 obstacle_layer に注入する方式を採用する。C++ Costmap Plugin 方式は開発コストが高いため不採用（Python で十分な性能）。

### アーキテクチャ

```
State Cache Node / AMCL
  ├── /bot1/amcl_pose ──→ VirtualScanNode (for bot2)
  │                         └── /bot2/virtual_scan publish
  │                              └── Nav2 bot2 obstacle_layer が購読
  │
  └── /bot2/amcl_pose ──→ VirtualScanNode (for bot1)
                            └── /bot1/virtual_scan publish
                                 └── Nav2 bot1 obstacle_layer が購読
```

各ロボットに対して1つの VirtualScanNode を起動する（計2ノード）。各ノードは相手の位置を購読し、自ロボット用の仮想 LaserScan を生成する。

### VirtualScanNode 実装

```python
import math
import numpy as np
from rclpy.node import Node
from sensor_msgs.msg import LaserScan
from geometry_msgs.msg import PoseWithCovarianceStamped


class VirtualScanNode(Node):
    """相手ロボットの位置を仮想LaserScanとして自ロボットのNav2に注入"""

    ROBOT_RADIUS = 0.075    # ロボット半径 (m)。R-42 で 0.075 に確定（旧 0.1=直径200mm は車体~150mmと矛盾）。単一ソース: warehouse_description.robot_dimensions.ROBOT_RADIUS。Phase 1 実測で最終確定
    ANGULAR_WIDTH = 0.26    # 相手ロボット方向の ±15度 (rad)
    MAX_RANGE = 2.0         # 最大検出距離 (m)
    SUPPRESSION_RANGE = 1.0 # この距離以上離れていたら発行しない (m)
    NUM_RAYS = 360          # LaserScan のレイ数（1度刻み）

    def __init__(self, own_robot: str, other_robot: str):
        super().__init__(f'virtual_scan_{own_robot}')
        self.own_robot = own_robot
        self.other_robot = other_robot
        self.own_pose = None
        self.other_pose = None

        # 購読: 自分と相手の AMCL 位置
        self.create_subscription(
            PoseWithCovarianceStamped,
            f'/{own_robot}/amcl_pose',
            lambda msg: setattr(self, 'own_pose', msg.pose.pose), 10)
        self.create_subscription(
            PoseWithCovarianceStamped,
            f'/{other_robot}/amcl_pose',
            lambda msg: setattr(self, 'other_pose', msg.pose.pose), 10)

        # 発行: 仮想 LaserScan
        self.scan_pub = self.create_publisher(
            LaserScan, f'/{own_robot}/virtual_scan', 10)

        # 10Hz タイマー
        self.create_timer(0.1, self.generate_virtual_scan)

    def generate_virtual_scan(self):
        if self.own_pose is None or self.other_pose is None:
            return

        # 1. 相対位置計算
        dx = self.other_pose.position.x - self.own_pose.position.x
        dy = self.other_pose.position.y - self.own_pose.position.y
        distance = math.sqrt(dx * dx + dy * dy)

        # 距離が遠い場合は発行しない（不要なcostmap汚染防止）
        if distance > self.SUPPRESSION_RANGE:
            return

        # 2. 自ロボット基準の角度（base_link座標系）
        own_yaw = self.get_yaw(self.own_pose.orientation)
        angle_to_other = math.atan2(dy, dx) - own_yaw

        # 3. LaserScan メッセージ生成
        scan = LaserScan()
        scan.header.stamp = self.get_clock().now().to_msg()
        scan.header.frame_id = f'{self.own_robot}/base_link'
        scan.angle_min = -math.pi
        scan.angle_max = math.pi
        scan.angle_increment = 2 * math.pi / self.NUM_RAYS
        scan.range_min = 0.05
        scan.range_max = self.MAX_RANGE
        scan.ranges = [float('inf')] * self.NUM_RAYS

        # 4. 相手ロボット方向の ±15度 に障害物を設定
        center_idx = int((angle_to_other + math.pi) / scan.angle_increment) % self.NUM_RAYS
        half_width = int(self.ANGULAR_WIDTH / scan.angle_increment)

        for i in range(-half_width, half_width + 1):
            idx = (center_idx + i) % self.NUM_RAYS
            scan.ranges[idx] = max(distance - self.ROBOT_RADIUS, scan.range_min)

        self.scan_pub.publish(scan)

    @staticmethod
    def get_yaw(orientation):
        """Quaternion → yaw 変換"""
        q = orientation
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        return math.atan2(siny_cosp, cosy_cosp)
```

### Nav2 設定変更

各ロボットの Nav2 パラメータファイルに `virtual_scan` ソースを追加する:

```yaml
# bot1_nav2_params.yaml
local_costmap:
  local_costmap:
    ros__parameters:
      plugins: ["obstacle_layer", "inflation_layer"]
      obstacle_layer:
        observation_sources: scan virtual_scan
        scan:
          topic: /bot1/scan
          data_type: LaserScan
          marking: true
          clearing: true
        virtual_scan:
          topic: /bot1/virtual_scan
          data_type: LaserScan
          marking: true
          clearing: false  # 仮想スキャンのinf rayで実障害物を消去しないためfalse
          obstacle_max_range: 2.0
          raytrace_max_range: 2.0

global_costmap:
  global_costmap:
    ros__parameters:
      plugins: ["static_layer", "obstacle_layer", "inflation_layer"]
      obstacle_layer:
        observation_sources: scan virtual_scan
        # （同様の設定）
```

### パラメータ一覧

| パラメータ | 値 | 説明 |
|-----------|-----|------|
| `ROBOT_RADIUS` | 0.075m | ロボット半径（R-42 で 0.075 確定。単一ソース=`warehouse_description.robot_dimensions`。Phase 1 実測で最終確定） |
| `ANGULAR_WIDTH` | ±15度 (0.26rad) | 相手ロボットを仮想障害物として表現する角度幅 |
| `MAX_RANGE` | 2.0m | LaserScan の最大レンジ |
| `SUPPRESSION_RANGE` | 1.0m | この距離以上離れている場合は仮想スキャンを発行しない |
| `NUM_RAYS` | 360 | LaserScan のレイ数（1度刻み） |
| publish 周期 | 10Hz | Nav2 obstacle_layer の更新に十分な頻度 |

### 無効化条件

- **距離 > 1.0m**: 仮想 LaserScan を発行しない。Nav2 のコストマップに不要なコストが残ることを防止
- **Mode C（Open-RMF）使用時**: VirtualScanNode 自体を起動しない。`traffic_mode: "open-rmf"`（`config/warehouse.base.yaml:6`）のとき **launch-time の `IfCondition`** で起動を抑止する（`nav2_bringup.launch.py:252`）。prod では systemd（`deploy/jetson/systemd/warehouse-nav2.service:29`）が `traffic_mode:=` を launch に渡すだけで、gating 自体は launch が行う（systemd がノードを直接制御するのではない）。

### Mode C との関係

Mode C（Open-RMF）では Multi-Robot Costmap Layer は**不要**。Open-RMF の Traffic Schedule Database が経路衝突を事前に解消し、Fleet Adapter 経由で各 Nav2 にゴールを送るため、Nav2 レベルでの相手ロボット認識が不要。Phase 3 後半で Mode C に移行した場合、VirtualScanNode を停止し、Nav2 の `observation_sources` から `virtual_scan` を削除する。

---

## 6. ClaudeとTrafficManagerの通信ルール

### ルール1: Claudeは「何をするか」、TrafficManagerは「どう実現するか」

```
Claude（WHAT）: 「Bot2はshelf_2へ行け」
TrafficManager（HOW）: 「通路Aが混雑。Bot2は5秒待機してから通路A経由で」
Nav2（EXECUTE）: 待機後に経路追従・速度制御
```

### ルール2: Claudeは進行中のTrafficManager調整に介入しない

TrafficManagerが衝突を検出して調整中（conflicts.status = "in_progress"）の場合、Claudeはその調整が完了するまで見守る。

Claudeが介入すべき場面:
- TrafficManagerの調整が3回失敗してエスカレーションされた場合
- 調整ではなく「タスク自体の変更」が必要な場合（別の仕事をさせる等）

### ルール3: Claudeの指示はTrafficManagerを経由する（直接Nav2に送らない）

```
✓ 正しい流れ: Claude → LLM Bridge → TrafficManager → Nav2
✗ 誤った流れ: Claude → LLM Bridge → Nav2（TrafficManagerをバイパス）
```

TrafficManagerが衝突を検出したらClaudeの指示を調整して安全に実行する。Claudeが強制的にTrafficManagerを無視する仕組み（override_rmf等）は設けない。物理的安全はNav2が最終保証する。

---

## 7. Claudeの責任範囲（モードA/B）

| 判断内容 | モードA | モードB |
|---------|--------|--------|
| タスク割当 | Claude | Claude |
| タスク優先順位変更 | Claude | Claude |
| バッテリー管理 | Claude | Claude |
| デッドロック解消 | Claude | Claude |
| 渋滞の予防 | Claude | 自動ロック |
| 迂回ルート指示 | Claude | Claude |
| 経路選択 | Claude | Claude |
| 待機時間の決定 | Claude | 自動（即時） |

---

## 7.5 エスカレーション階層（モードA/B）

Mode A/B では Open-RMF を使わず、交通管理を Claude（Mode A）または SimpleTrafficManager（Mode B）が担う。そのため Mode C と比べて階層が1段浅くなり、Claude の負担が大きい構造になる。

```
Emergency Guardian（50ms, 横串） / Nav2（50ms）→ TrafficManager（数十ms）→ Claude（1-3秒）

レベル0: Emergency Guardian（50ms周期、LLM非経由、全レベル横串）
  常時監視し、危険検知時は即時に物理停止を実行する。
  - 2台が0.3m以内に接近 → Nav2 cancel + cmd_vel=0
  - blocked > 10秒 / バッテリー ≤ 10% → 強制停止
  検知事象は /emergency/event で次サイクルの situation JSON に付加。
  上位への問い合わせではなく即時介入である点に注意。
  詳細: ../architecture/12-infrastructure-common.md の安全レイヤー設計

レベル1: Nav2が物理的にstuck
  Nav2リカバリー（Spin, BackUp）を3回試行
  → 失敗 → TrafficManagerに報告
  - Mode A (NoTrafficManager): 即座にClaudeへエスカレーション
  - Mode B (SimpleTrafficManager): 自動ロック・待機を試行
    → 失敗 → Claudeへエスカレーション（目的地変更を判断）

レベル2: TrafficManagerの調整が失敗（Mode Bのみ該当）
  SimpleTrafficManagerが3回調整を試行
  → 失敗 → Claudeにエスカレーション（タスク変更を判断）
  ※ Mode A では TrafficManager 層が存在しないため、
    交通系の問題はレベル1から直接Claudeへ上がる

レベル3: 複合障害（複数Botが同時にstuck等）
  → 安全優先: まずNav2が全ロボットを停止
  → TrafficManager（Mode Bのみ）が状況を整理
  → Claudeが全体状況を見てタスク再割当

各レイヤーは「自分で解決できない問題」だけを上位に投げる。
レベル0のEmergency Guardianは階層と並行して常時稼働する。
```

**Mode C との違い**: Mode C では Open-RMF が経路衝突予測まで担当するためClaudeへのエスカレーションは「タスク変更が必要な抽象的問題」に限られる。Mode A/B では交通管理判断もClaudeに上がるため、サイクル遅延（Mode A=3秒サイクル、応答遅延最大2.5秒）が安全性に影響しやすい。詳細比較は `../mode-c/11c-traffic-mode-c.md` のエスカレーション階層を参照。

---

## 8. 実装スケジュール

```
Phase 2: Multi-Robot Costmap Layer を実装
  - Multi-Robot Costmap Layer（モードA/B共通の衝突回避基盤）
  - ※ TrafficManager統合はPhase 3で実施（Phase 2では2台同時走行の基盤確立を優先）
  工数: 2-3日

Phase 3前半: TrafficManager + LLM Bridge + Claude統合
  - TrafficManager インターフェース定義
  - NoTrafficManager（モードA）実装
  - SimpleTrafficManager（モードB）実装
  - config.yaml での切り替え機構
  - LLM Bridge + Claude + TrafficManager の結合
  - trafficセクションをClaudeの状況JSONに含める
  - モードA/BでClaude動作検証
  工数: Phase 3本来の工数内
```

---

## 9. #125 yield: デモ用 route→隘路トポロジと lock 解放トリガ（#125 デモぶん確定・一般は Phase 3）

> §3（モードB）の `SimpleTrafficManager`（`aisle_locks` / `submit_task` 待機 / `release_aisle`）は実装済（`ws/src/warehouse_traffic/warehouse_traffic/traffic_logic.py:119-160`）。本節は **#125 yield デモ（真200mm隘路で2台 head-on → 一方が入口で待機 → 最接近 ≥0.15m）** に必要な**最小トポロジと解放タイミング**を、§3:118-122・T8（`docs/shared/07-research-notes.md:87`）・R-28（`07-research-notes.md:187`）の**デモ範囲ぶん**として確定する。**一般の route planner と timeout 実測値は Phase 3**（§3:99,118-122）— 本節はそれを置き換えず、risk register T8/R-28 は一般解として Phase 3 のまま残す。
>
> ロック/route キーは**凍結契約に入れない**: `warehouse_interfaces` の `KNOWN_LOCATIONS`（`locations.py:11-23`・9地点）に無く、`traffic_logic.py:17-21` の通り `route_planner` で**注入**する（コードで凍結キーを発明しない）。

### 9.1 ロックキー（200mm隘路 = 2本）

`aisle_locks` のキーは §3 illustrative の `route_A` / `route_B`（doc11a:96,139-142）をそのまま使い、物理隘路（単一ソース `warehouse_sim.layout.AISLES`）へ対応づける（新名称は発明しない）:

| ロックキー（`aisle_locks`） | 物理隘路（layout.AISLES タグ） | 隣接 shelf | 中心列 x | すれ違い |
|---|---|---|---|---|
| `route_A` | 通路A（タグ `"a"`） | shelf_1↔shelf_2 | ≈0.45 | **不可**（幅200mm・車体直径 = `2×ROBOT_RADIUS`。live 最接近 0.074m, #144） |
| `route_B` | 通路B（タグ `"b"`） | shelf_2↔shelf_3 | ≈0.95 | 不可 |

各隘路は shelf 行（y∈[0.15,0.45]）を貫く **N↔S の単線通路**。2台同時進入は物理的に回避不能（#144 live 実証）→ **排他ロックで直列化**し、後着は隘路**入口（open な北側）で待機**する＝ ≥0.15m が成立する唯一の幾何。

### 9.2 route → ロックキー写像（最小）

`plan_route(pickup, dropoff)` は route が貫くロックキー列を返す（`RoutePlanner = Callable[[str, str], list[str]]`, `traffic_logic.py:43-46`）。最小デモ規則:

- route の2端点が **shelf 行を南北に挟み**、かつ隘路 X の中心列を通るなら、その route は `[<key_X>]` を含む。
- **#125 デモ**: 北側ステージング（y≈0.8）↔ 通路A 南端（x≈0.45, y≈0.12）の**対向2タスクはともに `["route_A"]`** を返す ＝ 同一ロックを争う。先着が確保し、後着の `submit_task` は `{"status":"waiting","wait_for":"route_A"}` を返す（§3:103-107）。

> **ゴール到達性（#144 live 知見）**: 凍結 `KNOWN_LOCATIONS` の南側地点（`shipping_station` / `charging_station`, y=0.1）は shelf 直下で **robot 中心が置けず到達不能**。デモの南端ゴールは**隘路整列座標**（x≈0.45/0.95, y≈0.12・名前付き location では無い）を使う。名前付き地点の再配置は location 座標所有（skeleton / #124）の Phase-2 survey 案件で、本トラックは触らない。

### 9.3 lock 解放トリガ（A 主 + C 副 = §3:118-122 の推奨を本デモで確定）

| 区分 | トリガ | 判定 |
|---|---|---|
| **主（A）** | occupant が隘路を**通過完了** | occupant の Nav2 goal が `SUCCEEDED`（goal を隘路遠端の先に置く＝ **goal 到達 ≒ 隘路退出**）。補強の位置監視（候補B）: occupant 中心が隘路の**遠側 y 境界**（進入と反対側の shelf 行端 ∓ `ROBOT_RADIUS`）を越えたら退出とみなし即解放してよい |
| **副（C）** | **timeout** フォールバック | ロック取得から `AISLE_LOCK_TIMEOUT_S` 経過で自動 `release_aisle`。**暫定デモ既定値 = 30s**（期待通過 ~3-5s = 列長 ≈0.7m / `vx ≤ 0.3` ＋ 計画・回避 を十分上回り、異常時のデッドロックのみ解く）。**値は未凍結**＝ `# TODO(Phase 3)`: 実測で確定・config 化（T8=07:87 / CLAUDE.md:25「timeout 値未定」を本デモぶん暫定化） |

- §3:122「A + C 推奨」のトリガ**型**を本デモで確定（**正常時=A** 通過完了で解放 / **異常時=C** timeout で強制解放）。timeout の**値**は暫定（上記）。
- 解放後、待機 bot を **`submit_task` 再投入**（Node が保持していた goal を再発行）。
- 待機の物理停止/再開は **twist_mux 経由**: 待機 = その bot の Nav2 goal を保持（未発行 or cancel）＝ prio-10 nav2 入力が出ない。緊急 prio-100（Emergency Guardian）とは独立（`warehouse_bringup/config/twist_mux.yaml`）。timeout は**ロック齢**で判定し、退役した `status=="blocked"` 述語には依存しない（#128 / `docs/shared/10-system-qanda.md:307`）。

### 9.4 安全不変（不変）

本機構は**速度・footprint・inflation を変えない**。ハード速度上限 `MAX_LINEAR_VELOCITY = 0.3 m/s`（`warehouse_interfaces/safety.py:18`・config `safety.max_linear_velocity` は `config.py` が ≤cap 検証）・`inscribed_radius = ROBOT_RADIUS`（`warehouse_description.robot_dimensions`, =0.075 R-42）は不変。yield は「先着優先で後着が**入口で待つ**」だけの**論理層**で、物理的な壁クリアランス保証（inscribed）と独立。`warehouse_interfaces` 契約変更なし（ロック/route キーは注入・本節冒頭）。

---

## References

- [Open-RMF — GitHub](https://github.com/open-rmf/rmf) — 参照日: 2026-05-22
- [Programming Multiple Robots with ROS 2](https://osrf.github.io/ros2multirobotbook/) — 参照日: 2026-05-22
