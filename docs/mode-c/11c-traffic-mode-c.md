# 交通管理レイヤー — Mode C（Open-RMF）

作成日: 2026-05-22
更新日: 2026-06-05

> 関連ドキュメント: [Mode A/B（LLM単独 / 自作ルールベース）](../mode-a/11a-traffic-mode-a.md) | [共通インフラ](../architecture/12-infrastructure-common.md)

## 概要

交通管理レイヤーをプラグイン方式で設計する。本ドキュメントではモードC（Open-RMF）を扱う。**主方針はモードC** — 交通管理はOpen-RMFが即時処理し、Claude（LLM）はタスク割当・優先順位・バッテリー管理の戦略判断のみを行う。config.yamlの1行変更でモードA/Bへの切替も可能（YouTube比較検証用）。

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

## 2. モードC: Open-RMF交通管理（主方針）

Open-RMFのTraffic ScheduleとConflict Negotiationを使用する。経路ベースの衝突予測が可能になり、predicted_position_3s（CTRV 外挿）やMulti-Robot Costmap Layer（自作）が不要になる。

**Nav2への経路**: Hermes → Warehouse MCP Server → Open-RMF Task API → Fleet Adapter → Nav2（Fleet Adapterが唯一のNav2制御パス）。

```python
class RMFTrafficManager(TrafficManager):
    def submit_task(self, robot, pickup, dropoff, priority="normal"):
        result = self.rmf_adapter.submit_navigation(robot, pickup, dropoff, priority)
        return {
            "status": result.status,
            "adjustments": result.adjustments,
            "predicted_path": result.predicted_path
        }

    def get_traffic_state(self):
        schedule = self.rmf_adapter.get_traffic_schedule()
        return {
            "mode": "open-rmf",
            "aisles": schedule.aisle_status,
            "conflicts": schedule.active_conflicts,
            "adjustments_since_last": schedule.recent_adjustments,
            # null while RMF is still resolving; 非 null only after retries are
            # exhausted and the case is handed to Claude（司令官 gate = 08c:160）。
            "escalation": self._derive_escalation(schedule),
        }

    def _derive_escalation(self, schedule):
        """Open-RMF が解決できなかった衝突を、司令官 LLM 向けの escalation に変換する。

        エスカレーション階層（§6）の Level 1-3 を司令官に上げる単一フィールド。
        通常時は ``schedule.unresolved_conflict`` が None（このとき該当 conflict の
        ``status`` は ``"in_progress"`` ＝ RMF が調整中）→ escalation も None を返す。
        RMF の交渉 / Nav2 リカバリが retry 上限（3回）に達して解消できなかった衝突
        がある時のみ非 None。司令官 LLM はこの非 None のときだけ介入する（08c:160）。
        """
        failed = schedule.unresolved_conflict   # retry 上限まで調整しても解消不能だった衝突 / None
        if failed is None:
            return None
        return {
            # producer が failed.id から採番する識別子。司令官はこれを escalation_response の
            # escalation_id / start_negotiation の deadlock_or_escalation_id 引数にそのまま渡す
            # （doc15 ツール6/7）。この id を MCP in-memory registry（tools.py:108）へ登録する
            # producer→registry 連携は # TODO(#escalation)、それまで tools.py:357 で
            # unknown_escalation_id 拒否。
            "id": failed.id,
            "level": failed.level,                 # 1: Nav2 stuck→reroute失敗 / 2: RMF調整失敗 / 3: 両方（§6）
            "reason": failed.reason,               # 例 "rmf_negotiation_failed" / "nav2_stuck_reroute_failed"
            "robots": failed.robots,               # 影響を受けるロボット
            "location": failed.location,           # 衝突箇所（aisle/route キー）
            "failed_attempts": failed.attempts,    # RMF が試行した回数（上限=3 で escalation）
            # 司令官への助言ヒント。§6 の level に対応: 1→change_destination /
            # 2→reassign_task / 3→global_reassign。escalation_response の action enum
            # （reassign|cancel|retry、tools.py:40）とは**別物**＝戦略ツールへのマッピング用。
            "suggested_action": failed.suggestion,
        }
```

Claudeに渡すtraffic（**通常時** — RMF が調整中。`escalation` は `null`）:
```json
{
  "mode": "open-rmf",
  "aisles": {
    "route_A": {"status": "occupied", "robot": "bot1", "eta_clear_s": 4.2},
    "route_B": {"status": "free"}
  },
  "conflicts": [
    {
      "robots": ["bot1", "bot2"],
      "location": "route_A",
      "rmf_resolution": "bot2_wait_5s",
      "wait_remaining_s": 3.1,
      "status": "in_progress"
    }
  ],
  "adjustments_since_last": [
    {
      "robot": "bot2",
      "type": "wait",
      "reason": "route_A occupied by bot1",
      "duration_s": 5
    }
  ],
  "escalation": null
}
```

`escalation` は通常 `null`。RMF の交通調整が retry 上限（3回）に達して**解消できなかった衝突**が出たときのみ非 `null` になり、司令官 LLM が介入する（§6 Level 1-3 / `08c:160` の gate）。司令官はこの非 `null` を受けてタスク再割当などの戦略判断のみを行い、経路・待機には引き続き関与しない。

Claudeに渡すtraffic（**エスカレーション発生時** — RMF が route_A の衝突を解消できず Claude へ委譲。`conflicts[].status` は `"escalated"`、`rmf_resolution` は `"failed"`）:
```json
{
  "mode": "open-rmf",
  "aisles": {
    "route_A": {"status": "blocked", "robot": "bot1", "eta_clear_s": null},
    "route_B": {"status": "free"}
  },
  "conflicts": [
    {
      "robots": ["bot1", "bot2"],
      "location": "route_A",
      "rmf_resolution": "failed",
      "wait_remaining_s": null,
      "status": "escalated"
    }
  ],
  "adjustments_since_last": [],
  "escalation": {
    "id": "esc-20260615-0001",
    "level": 2,
    "reason": "rmf_negotiation_failed",
    "robots": ["bot1", "bot2"],
    "location": "route_A",
    "failed_attempts": 3,
    "suggested_action": "reassign_task"
  }
}
```

> **キー集合（`traffic`）**: 両例とも top-level は `mode / aisles / conflicts / adjustments_since_last / escalation` の5キー。これは `08c` の situation 例（`08c §入力`）の `traffic` ブロックと**同一キー集合**であり、producer（本 `get_traffic_state()`）の戻り値そのもの。`escalation` が非 null のときの内部キーは `id / level / reason / robots / location / failed_attempts / suggested_action`。`id` は producer（`_derive_escalation` が `failed.id` から採番）が付与する識別子で、司令官が `escalation_response`(`escalation_id`)/`start_negotiation`(`deadlock_or_escalation_id`) に渡す（doc15 ツール6/7）。この id を MCP in-memory registry（`tools.py:108`）へ登録する producer→registry 連携は未実装（`tools.py:343 TODO(#escalation)`）＝現状 registry 未登録 id は `tools.py:357` で `unknown_escalation_id` 拒否。`escalation` の派生は §6 エスカレーション階層に対応する。

---

## 3. Open-RMF導入要件

| 項目 | 内容 |
|------|------|
| ライセンス | Apache 2.0（無料） |
| ROS 2対応 | Jazzyブランチあり（rmf_ros2） |
| Jetson動作 | 可能（ROS 2ノードの集合体、GPU不要） |
| 追加開発 | Fleet Adapter 自作（**§3.5 / R-44 で free_fleet 不採用＝EasyFullControl 直駆動に判断**。工数は Phase 3 で再見積り） |
| 地図 | Navigation Graph（通路2-3本、手動定義で1日） |

### Open-RMFから使用する機能と無効化する機能

| 機能 | 使用 | 理由 |
|------|------|------|
| Traffic Schedule | **使用** | 経路予測・衝突検出の核心 |
| Conflict Negotiation | **使用** | 衝突時の自動調整 |
| Task Dispatcher | **無効化** | Claudeがタスク割当を担当（競合防止） |

---

## 3.5 free_fleet ⇔ micro-ROS/ESP32 適合性評価と Fleet Adapter 経路判断（R-44 / Go-No-Go）

> **本節の位置づけ**: [R-44](../shared/07-research-notes.md)（`docs/shared/07-research-notes.md:254`）の**文献ベース評価**と Go/No-Go を記録する。**スコープは docs（設計判断）のみ**。Open-RMF を Jetson/実機に立てる **PoC（メモリ実測・2台 E2E）は R-38（`docs/shared/07-research-notes.md:243`）のメモリゲート未通過のため BLOCKED ＝ Phase 3 後半の別 Issue へ defer**（本節 D）。本評価は §2 `get_traffic_state()` / §6 エスカレーション階層（#163 land 分）の設計を**変更しない**（別節）。

### 結論サマリ

| 項目 | 判断 |
|------|------|
| **free_fleet をそのまま採用** | **No-Go**（理由 A） |
| **第一候補（採用方針）** | **案A: `rmf_fleet_adapter` の EasyFullControl API で自作 Fleet Adapter が `/bot{n}` Nav2 を直接駆動（zenoh ブリッジ無し）** |
| **縮退フォールバック** | 案B: Open-RMF 不使用・既存 Nav2 Bridge REST（`:8645`）でタスク割当（RMF 交通管理＝Traffic Schedule + Conflict Negotiation を放棄） |
| **不変条件**（§2 冒頭「Fleet Adapter が唯一の Nav2 制御パス」, `docs/mode-c/11c-traffic-mode-c.md:63`） | 案A=**保持**／案B=intent は保持するが Mode C（RMF）を出る |
| **最終実証（メモリ・動作）** | R-38 ゲート通過後の **Phase 3 後半・別 Issue（defer, 本節 D）** |

### A. free_fleet が本構成に不適合な理由（No-Go）

**前提（文献）**: 現行 free_fleet は「**EasyFullControl Fleet Adapter（Python 実装）＋ ロボット毎の zenoh ブリッジ**」である。

> "The `free_fleet_adapter` implements the Easy Full Control fleet adapter API, and communicates with individual robots over Zenoh bridges."
> — [OSRF, Free Fleet Adapter](https://osrf.github.io/ros2multirobotbook/integration_free_fleet_adapter.html)

> "It uses `zenoh` as a communication layer between each robot and the fleet adapter, allowing access and control over the navigation stacks of the robots."
> — [open-rmf/free_fleet README](https://github.com/open-rmf/free_fleet)

**理由1（主・決定打＝アーキテクチャ不整合）**: free_fleet の zenoh ブリッジは「**各ロボットが自前の（非 namespaced な）Nav2 を持ち、ロボット側に置いた zenoh ブリッジで橋渡しする**」分散構成を前提とする。

> "Each robot's navigation stack is expected to be non-namespaced, while its `zenoh` bridge is expected to be set up with it's robot name as the namespace."
> — [open-rmf/free_fleet README](https://github.com/open-rmf/free_fleet)

本プロジェクトは逆に、**2台分の Nav2/AMCL/SLAM を中央の単一 Jetson 上で `/bot1` `/bot2` の namespace に分離**して動かす（`docs/mode-c/12c-integration-mode-c.md:137` の構成図・`:180-181` の `nav2_bot1/2.service`）。したがって free_fleet の zenoh transport が解く問題（マシン跨ぎ伝送・ドメイン分離・帯域フィルタ）は**いずれも本構成に存在せず**、かつ free_fleet が期待する「非 namespaced な onboard Nav2」は本構成の「中央 namespaced Nav2」と**逆**である。free_fleet をここに載せると、既に namespaced な単一 ROS グラフに対して zenoh ブリッジを中央で回し namespace を付け替える＝**設計意図に逆らった冗長運用**になる（free_fleet が解決対象とするのは ROS 配布版・ナビ・通信プロトコルが**異なる分散・異種フリート**であり、本構成は該当しない）。

**理由2（副・補強。決定打ではない）**: ロボットの MCU は ESP32 micro-ROS であり、これは XRCE-DDS クライアント（full ROS 2 ノードではない）でホスト側 Agent に橋渡しされる。zenoh ブリッジや Nav2 のような full ROS 2 プロセスを ESP32 に載せることはできない。

> micro-ROS は「low resource devices（XRCE Clients）が Agent を介して DDS Global-Data-Space に参加する」構成。
> — [micro-ROS, Micro XRCE-DDS](https://micro.vulcanexus.org/docs/concepts/middleware/Micro_XRCE-DDS/)

ただし本構成では Nav2 自体が Jetson 中央にあるため、そもそも**ロボット側に橋渡し対象の Nav2 が無い**。よって ESP32 制約は No-Go を補強するが、決定打は理由1（中央 namespaced 構成との不整合）である。

**R-44 原文の精緻化**: `docs/shared/07-research-notes.md:254` の「free_fleet client の置き場が無い（ロボットが ESP32）」は**真だが決定打ではない**。決定打は理由1。また現行 free_fleet の旧 client/server 世代は **deprecated**（"This legacy implementation is no longer being supported" — [OSRF, Legacy free fleet](https://osrf.github.io/ros2multirobotbook/integration_free-fleet.html)）であり、R-44 を「free_fleet_client vs EasyFullControl」と表現しない（現行 free_fleet 自体が EasyFullControl の上に立つため、両者は同じ Full Control 階層・**実装モデルが異なる**だけ）。

### B. 代替設計2案（既存 docs と整合）

#### 案A（第一候補）: 自作 EasyFullControl Fleet Adapter が `/bot{n}` Nav2 を直接駆動

EasyFullControl は `rmf_fleet_adapter` の一級 API であり（free_fleet が内部で使うのと同じ API）、**zenoh transport を外して**中央の Nav2 namespace を直接呼ぶ自作 adapter を書ける。

> `class EasyFullControl` — "An easy to initialize full_control fleet adapter."（namespace `rmf_fleet_adapter::agv`）
> — [rmf_ros2 EasyFullControl.hpp](https://github.com/open-rmf/rmf_ros2/blob/main/rmf_fleet_adapter/include/rmf_fleet_adapter/agv/EasyFullControl.hpp)

- **統合面**: 自作 adapter は `navigate` / `stop` / `execute_action` の3コールバックと `RobotState` 更新を実装する（[Fleet Adapter Tutorial](https://osrf.github.io/ros2multirobotbook/integration_fleets_adapter_tutorial.html)）。`navigate()` を **namespace 毎の Nav2 `NavigateToPose` action client**（`/bot1/navigate_to_pose` 等）に接続し、in-process で駆動する（zenoh ブリッジ無し）。Python/C++ どちらも可。
- **Jazzy 在中（確認済）**: `EasyFullControl.hpp` は rmf_ros2 の `jazzy` ブランチ `rmf_fleet_adapter/include/rmf_fleet_adapter/agv/` に存在（[jazzy agv/ ディレクトリ](https://github.com/open-rmf/rmf_ros2/tree/jazzy/rmf_fleet_adapter/include/rmf_fleet_adapter/agv)）。Jazzy doc も `EasyFullControl::FleetConfiguration`（rmf_fleet_adapter 2.7.2 = Jazzy）を公開（[docs.ros.org/jazzy](https://docs.ros.org/en/jazzy/p/rmf_fleet_adapter/generated/classrmf__fleet__adapter_1_1agv_1_1EasyFullControl_1_1FleetConfiguration.html)）。
- **不変条件（`docs/mode-c/11c-traffic-mode-c.md:63`）**: 自作 adapter が唯一の Nav2 writer になる → **保持**。
- **既存 docs との整合**: 既存フォールバック「直接 ROS 2 Action Client に切替」（`docs/mode-c/12c-integration-mode-c.md:202`）は、まさに zenoh を外して Nav2 action を直接呼ぶ＝**案A の実体**。よって案A は `docs/shared/07-research-notes.md:254` の推奨（EasyFullControl 直駆動）と 12c:202 の既存フォールバックを統合・常用化したもの（新方式の発明ではない）。
- **RMF 交通管理は不変**: Traffic Schedule / Conflict Negotiation は RMF core（fleet adapter インターフェースの背後）にあり、adapter が free_fleet か自作 EasyFullControl かに依存しない（[RMF Core Overview](https://osrf.github.io/ros2multirobotbook/rmf-core.html)）。よって §3 の「Traffic Schedule=使用 / Conflict Negotiation=使用」（`docs/mode-c/11c-traffic-mode-c.md:197-198`）は案A でそのまま成立する。

#### 案B（縮退フォールバック）: Open-RMF 不使用・Nav2 Bridge REST 経由

Open-RMF を立てず、既存 `warehouse_nav2_bridge`（REST `:8645`、`POST /api/v1/navigate|wait|stop` — `ws/src/warehouse_nav2_bridge/CLAUDE.md:18-20`）でタスク→Nav2 ゴールを割り当てる。

- **失うもの（核心）**: RMF の **Traffic Schedule（事前の経路デコンフリクト）＋ Conflict Negotiation（衝突時の自動交渉）** を全て放棄する。2台は **Nav2 ローカル回避のみ**に縮退し、共有スケジュールも交差点・隘路でのデッドロック保証解消も無い（200mm 真隘路の渋滞デモ＝#124 の前提が崩れる）。RMF の **Read-Only 相当**（位置は見えるが経路制御・調整はできない — [OSRF, Read-Only Fleets](https://osrf.github.io/ros2multirobotbook/integration_read-only.html)）であり、Mode C の主役機能そのものを失う。
- **不変条件（`docs/mode-c/11c-traffic-mode-c.md:63`）**: RMF Fleet Adapter は存在しなくなるが、REST ブリッジが唯一の Nav2 writer になる → 「単一制御パス」という **intent は保持**。ただし RMF トポロジを出る＝厳密には Mode C ではなく Mode A/B 寄りのフォールバック。
- **実装方針（契約でない）**: 現状 Mode C では Nav2 Bridge REST forwarder は非注入（`NAV2_BRIDGE_MODES = {none, simple}` — `docs/mode-c/12c-integration-mode-c.md:142`）。案B 採用には Mode C へこの forwarder を拡張する必要がある。これは**実装方針であって凍結契約の変更ではない**（`warehouse_interfaces` は無編集・新トピック/型/閾値を発明しない）。

### C. Go/No-Go

- **free_fleet: No-Go**（理由 A）。
- **採用方針: 案A（自作 EasyFullControl 直駆動）を第一候補**、案B（REST 縮退）をフォールバック。
- これは `docs/shared/07-research-notes.md:254` の R-44 結論（「`rmf_fleet_adapter` EasyFullControl で自作 adapter が Nav2 namespace を直接駆動する方が素直。free_fleet 採用是非を Phase3 冒頭で判断」）と整合する。
- **最終確定**（メモリ実測・2台動作）は R-38 ゲート通過後（**D 参照、defer**）。

### D. PoC/実機検証は BLOCKED（R-38 ゲート依存 ＝ defer）

本節の成果は**文献評価＋設計判断のみ**。下記は **R-38 メモリゲート（`docs/shared/07-research-notes.md:243`）通過後の Phase 3 後半（`docs/architecture/06-implementation-phases.md:215-221`）の別 Issue** で実施する＝本レーンでは実装しない:

- 自作 EasyFullControl adapter の実装、2台 Open-RMF E2E、Jetson メモリ実測。
- **残未決（実装 spike で要確認。文献では確証できなかった点）**:
  1. **「EasyFullControl + in-process Nav2 action client（namespace 毎）」の end-to-end 実例は未確認**（最大の未証明前提）。canonical な EasyFullControl 例（`rmf_demos_fleet_adapter`）は外部 fleet manager を REST で駆動し、Nav2 action を直接叩く例は free_fleet の `nav2_robot_adapter.py`（ただし zenoh 経由）のみ。両者を合成（rmf_demos の足場 + free_fleet の `NavigateToPose` 構築ロジック − zenoh）して自作する必要がある。
  2. **1プロセスから `/bot1` `/bot2` 両方を駆動する namespacing は integrator 実装**（first-party doc に turnkey の記載なし）。
  3. **`ros-jazzy-rmf-fleet-adapter` バイナリ版が jazzy ブランチ source と同一 API か未 pin**（依存ピン時に確認）。
  4. **EasyFullControl 下での rmf_traffic schedule/negotiation 配線負荷（Navigation Graph / traffic profile / footprint）が未定量**（工数の隠れコスト。effort 見積り前に要確認）。
  5. **RMF Navigation Graph と本プロジェクトの 9 locations / Gazebo 地図の整合**は設計依存（凍結 `warehouse_interfaces.locations` に waypoint/lane 契約を**発明しない**＝必要なら別途 contract PR）。
  6. **1.8×0.9m・200mm 隘路（#124）・≤0.3 m/s で RMF デコンフリクトが有効か**は sim 検証待ち。

> なお本節の適合判断は、Open-RMF/micro-ROS 各々の**文書化された前提**からの**工学的推論**であり、「この分割トポロジ（薄い ESP32 アクチュエータ + 中央 namespaced Nav2）」を名指しで是認した一次ソースは存在しない。維持者による本構成の保証ではない点に注意（推論として提示）。

### E. cross-doc 整合（本レーン編集境界外＝別 PR で追従）

本評価により Fleet Adapter の実体は free_fleet ではなく**自作 EasyFullControl adapter**となる。下記は本レーンの**編集境界外（読むのみ）**のため、Phase 3 実装 Issue の別 PR で反映する（本 PR では触らない）:

- `docs/mode-c/12c-integration-mode-c.md:129`「Fleet Adapter（free_fleet + battery拡張）」→ EasyFullControl 自作 adapter（+ battery 拡張）へ。
- `docs/architecture/06-implementation-phases.md:217`「free_fleet ベースの Fleet Adapter 作成」→ EasyFullControl 自作へ。
- `docs/shared/07-research-notes.md:179`（R-13）「Open-RMF + free_fleet + zenoh + Nav2 の統合チェーン」→ 「Open-RMF + EasyFullControl 直駆動 + Nav2」へ（free_fleet/zenoh をチェーンから除外）。
- （本 11c 内の §3 表・§7 スケジュールの「free_fleet ベース」表記は本 PR で §3.5 への pointer に更新済み。）

---

## 4. ClaudeとTrafficManagerの通信ルール

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

## 5. Claudeの責任範囲（モードC）

| 判断内容 | モードC（主方針） |
|---------|-----------------|
| タスク割当 | **Claude** |
| タスク優先順位変更 | **Claude** |
| バッテリー管理 | **Claude** |
| デッドロック解消 | **Open-RMF**（失敗時Claude） |
| 渋滞の予防 | **Open-RMF** |
| 迂回ルート指示 | **Open-RMF** |
| 経路選択 | **Open-RMF** |
| 待機時間の決定 | **Open-RMF** |

---

## 6. エスカレーション階層（モードC）

```
Emergency Guardian（50ms, 横串） / Nav2（50ms）→ Open-RMF（即時）→ Claude（1-3秒）

レベル0: Emergency Guardian（50ms周期、LLM非経由、全レベル横串）
  常時監視し、危険検知時は即時に物理停止を実行する。
  - 2台が0.3m以内に接近 → Nav2 cancel + cmd_vel=0
  - blocked > 10秒 / バッテリー ≤ 10% → 強制停止
  検知事象は /emergency/event で次サイクルの situation JSON に付加。
  上位への問い合わせではなく即時介入である点に注意。
  詳細: ../architecture/12-infrastructure-common.md の安全レイヤー設計

レベル1: Nav2が物理的にstuck
  → Nav2リカバリー3回試行 → 失敗
  → Open-RMFに報告 → 別経路を計算 → 失敗
  → Claudeにエスカレーション（目的地変更を判断）

レベル2: Open-RMFの交通調整が失敗
  → Open-RMFが3回調整を試行 → 失敗
  → Claudeにエスカレーション（タスク変更を判断）

レベル3: 両方同時に発生
  → 安全優先: Nav2が全ロボットを停止
  → Open-RMFが状況を整理
  → Claudeが全体状況を見てタスク再割当

レベル0は階層と並行して常時稼働する（他レベルの状態に依存しない）。
```

---

## 7. 実装スケジュール

```
Phase 3後半: モードC（Open-RMF）追加
  - RMFTrafficManager 実装
  - Fleet Adapter 作成（EasyFullControl 自作・§3.5 / R-44。free_fleet 不採用）
  - Navigation Graph 定義
  - Claude + Open-RMF の統合テスト
  工数: 1-2週間

Phase 4: YouTube比較検証
  パターン1: Nav2のみ（TrafficManager無効、Claude無効）→ デッドロック頻発
  パターン2: Claude単独（モードA）→ 柔軟だが3秒遅延
  パターン3: Claude + 自作ルール（モードB）→ 即時の排他制御あり
  パターン4: Claude + Open-RMF（モードC）→ フル交通管理
  → config.yaml 1行変更で切り替え撮影
```

---

## 8. 技術的価値

- TrafficManagerインターフェースの設計 → 交通管理を抽象化する設計パターン
- Open-RMFとLLMの統合 → 2026年時点でOpen-RMF + LLMの組み合わせ事例はほぼない
- 4パターン比較 → 「ルールベース vs LLM vs 併用」の定量比較データ
- ポートフォリオ → 「Open-RMFを理解して統合できる」スキルの証明

---

## References

- [Open-RMF — GitHub](https://github.com/open-rmf/rmf) — 参照日: 2026-05-22
- [Open-RMF rmf_ros2 Jazzy branch — GitHub](https://github.com/open-rmf/rmf_ros2/tree/jazzy) — 参照日: 2026-05-22
- [Open-RMF rmf-web Dashboard — GitHub](https://github.com/open-rmf/rmf-web) — 参照日: 2026-05-23
- [Free Fleet — GitHub](https://github.com/open-rmf/free_fleet) — 参照日: 2026-05-22
- [Programming Multiple Robots with ROS 2](https://osrf.github.io/ros2multirobotbook/) — 参照日: 2026-05-22
- [Open-RMF + Nav2 Integration — Atomic Loops](https://www.atomicloops.com/technologies/industrial-automation-and-robotics/coordinate-heterogeneous-robot-fleets-with-nav2-and-open-rmf) — 参照日: 2026-05-22
- [NVIDIA Isaac Mission Dispatch — GitHub](https://github.com/nvidia-isaac/isaac_mission_dispatch) — 参照日: 2026-05-22

### §3.5（R-44 評価）で追加

- [OSRF — Mobile Robot Fleet Integration（Full Control / Easy Full Control）](https://osrf.github.io/ros2multirobotbook/integration_fleets.html) — 参照日: 2026-06-05
- [OSRF — Fleet Adapter Tutorial（navigate/stop/execute_action）](https://osrf.github.io/ros2multirobotbook/integration_fleets_adapter_tutorial.html) — 参照日: 2026-06-05
- [OSRF — Read-Only Fleets（Read-Only 階層の限界）](https://osrf.github.io/ros2multirobotbook/integration_read-only.html) — 参照日: 2026-06-05
- [OSRF — Free Fleet Adapter（v2: EasyFullControl + ロボット側 zenoh ブリッジ）](https://osrf.github.io/ros2multirobotbook/integration_free_fleet_adapter.html) — 参照日: 2026-06-05
- [OSRF — Legacy free fleet（旧 client/server, deprecated・異種フリート向け）](https://osrf.github.io/ros2multirobotbook/integration_free-fleet.html) — 参照日: 2026-06-05
- [OSRF — RMF Core Overview（Traffic Schedule / Conflict Negotiation は adapter の背後）](https://osrf.github.io/ros2multirobotbook/rmf-core.html) — 参照日: 2026-06-05
- [rmf_ros2 — EasyFullControl.hpp（API クラス・コールバック）](https://github.com/open-rmf/rmf_ros2/blob/main/rmf_fleet_adapter/include/rmf_fleet_adapter/agv/EasyFullControl.hpp) — 参照日: 2026-06-05
- [rmf_ros2 — jazzy ブランチ agv/（EasyFullControl の Jazzy 在中確認）](https://github.com/open-rmf/rmf_ros2/tree/jazzy/rmf_fleet_adapter/include/rmf_fleet_adapter/agv) — 参照日: 2026-06-05
- [rmf_fleet_adapter EasyFullControl::FleetConfiguration（Jazzy doc, 2.7.2）](https://docs.ros.org/en/jazzy/p/rmf_fleet_adapter/generated/classrmf__fleet__adapter_1_1agv_1_1EasyFullControl_1_1FleetConfiguration.html) — 参照日: 2026-06-05
- [free_fleet — nav2_robot_adapter.py（zenoh 経由 NavigateToPose・案A で模倣する配線, zenoh 抜き）](https://github.com/open-rmf/free_fleet/blob/main/free_fleet_adapter/free_fleet_adapter/nav2_robot_adapter.py) — 参照日: 2026-06-05
- [micro-ROS — Micro XRCE-DDS（client/agent split: ESP32 は full ROS 2 不可）](https://micro.vulcanexus.org/docs/concepts/middleware/Micro_XRCE-DDS/) — 参照日: 2026-06-05
