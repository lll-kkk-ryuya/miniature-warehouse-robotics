# 交通管理レイヤー — Mode C（Open-RMF）

作成日: 2026-05-22
更新日: 2026-05-25

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

Open-RMFのTraffic ScheduleとConflict Negotiationを使用する。経路ベースの衝突予測が可能になり、predicted_position_3s（線形外挿）やMulti-Robot Costmap Layer（自作）が不要になる。

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
            "adjustments_since_last": schedule.recent_adjustments
        }
```

Claudeに渡すtraffic:
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
  ]
}
```

---

## 3. Open-RMF導入要件

| 項目 | 内容 |
|------|------|
| ライセンス | Apache 2.0（無料） |
| ROS 2対応 | Jazzyブランチあり（rmf_ros2） |
| Jetson動作 | 可能（ROS 2ノードの集合体、GPU不要） |
| 追加開発 | Fleet Adapter自作（free_fleetベース、3-5日） |
| 地図 | Navigation Graph（通路2-3本、手動定義で1日） |

### Open-RMFから使用する機能と無効化する機能

| 機能 | 使用 | 理由 |
|------|------|------|
| Traffic Schedule | **使用** | 経路予測・衝突検出の核心 |
| Conflict Negotiation | **使用** | 衝突時の自動調整 |
| Task Dispatcher | **無効化** | Claudeがタスク割当を担当（競合防止） |

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
  - blocked > 10秒 / バッテリー < 10% → 強制停止
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
  - free_fleet ベースの Fleet Adapter 作成
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
