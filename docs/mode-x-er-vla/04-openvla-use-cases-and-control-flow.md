# OpenVLA Use Cases And Control Flow

作成日: 2026-06-22

> **状態**: 設計提案。本文中の `VlaSubtaskRequest` / `VlaActionCandidate`
> / `ManipulationSkillResult` / `SafetyCompiler` は Mode X-ER-VLA 内部案で
> あり、ROS topic、REST API、config key、`warehouse_interfaces` frozen
> contract を追加するものではない。実装前に contract PR で凍結する。

## 結論

Mode X-ER-VLA で OpenVLA を使う主目的は、移動そのものではなく、移動後の
局所的な物体操作を扱うことである。

| task | 推奨構成 | OpenVLA の扱い |
|---|---|---|
| 赤箱へ移動する | ER + L3 + MCP / Policy Gate + Nav2 | 不要 |
| 赤箱を視覚で見つけて移動する | ER + L3 Visual Resolver + Nav2 | 原則不要。cross-check だけ候補 |
| 赤箱へ行って赤ボールを確認する | ER + L3 + optional VLA grounding | 補助として候補 |
| 赤箱の赤ボールを掴む | ER + L3 + VLA + SafetyCompiler + skill controller | 必要になる可能性が高い |
| 赤ボールをトレーへ置く | ER + L3 + VLA + SafetyCompiler + skill controller | 必要になる可能性が高い |
| 充電器へ戻る / 棚Aへ行く | ER + L3 + Nav2 | 不要 |

したがって、Mode X-ER-VLA の初期判断は次の通りにする。

- 移動だけの MVP は Mode X-ER で扱う。
- OpenVLA は「把持」「配置」「ドッキング」「近接位置合わせ」など、Nav2
  だけでは表現できない局所操作が入った時に検討する。
- OpenVLA を採用しても、OpenVLA は `/cmd_vel`、Nav2 action、Jetson
  endpoint、ESP32 firmware を直接叩かない。
- OpenVLA の raw output は必ず L3 の `SafetyCompiler` 相当を通し、既存
  safety path または明示的に凍結された skill executor へ落とす。

## 役割分担

Mode X-ER-VLA では「制御」という言葉を分解して扱う。

| 層 | 担うこと | 担わないこと |
|---|---|---|
| Gemini Robotics-ER | intent 理解、高レベル task graph、再計画、operator clarification | `/cmd_vel`、Nav2 action、gripper command の直接生成 |
| L3 Planning Core | task graph の状態管理、ready 判定、ER/VLA output の検証、compile 境界 | wheel / arm / gripper のリアルタイム制御 |
| OpenVLA / VLA | 局所画像と言語 subtask から grounding / action candidate を提案 | 高レベル fleet planning、emergency 判定、冪等 key 生成 |
| L2 MCP / Policy Gate | stale / duplicate / battery / emergency / location などの最終実行許可 | model の推論や物体認識 |
| L1 Nav2 / controller | 移動、局所経路追従、controller 実行 | task graph の意味理解 |
| L0 firmware | 速度 clamp、proximity stop、bumper stop など即時安全 | 高レベル判断 |

ここで L3 は motor controller ではない。L3 は **工程制御・状態遷移・実行候補
の安全な変換** を担当する。

## 典型フロー

指示:

```text
赤い箱へ行って、赤い箱の中の赤いボールを掴んで、トレーに置いて。
```

ER が作る高レベル task graph 案:

```json
{
  "task_graph": [
    {"id": "t1", "robot": "bot1", "kind": "navigation", "command_action": "navigate", "target": "red_box_area"},
    {"id": "t2", "robot": "bot1", "kind": "manipulation", "skill": "grasp", "target": "red_ball", "container": "red_box", "after": "t1.completed"},
    {"id": "t3", "robot": "bot1", "kind": "navigation", "command_action": "navigate", "target": "tray_area", "after": "t2.completed"},
    {"id": "t4", "robot": "bot1", "kind": "manipulation", "skill": "place", "target": "tray", "object": "red_ball", "after": "t3.completed"}
  ]
}
```

`command_action` は既存 `Command` へ compile する移動系 action だけに使う。
`skill` は Mode X-ER-VLA 内部の manipulation subtask であり、既存 `Command`
契約へそのまま入れない。

L3 の実行順:

```text
1. Validator
   - robot / kind / command_action / skill / target / graph / state freshness /
     emergency を検証する。

2. Visual Resolver
   - red_box_area / tray_area を known location へ解決する。
   - 未凍結の coordinate goal は compile しない。

3. Task Graph Executor
   - 初回 cycle は t1 だけ ready にする。
   - t2 / t3 / t4 は pending にする。

4. Command Compiler
   - t1 を既存 Command(navigate) へ compile する。

5. L2 / L1
   - MCP / Policy Gate / Nav2 が t1 を実行する。

6. Completion 判定
   - Nav2 result、State Cache pose、task timeout、emergency state を見て
     t1.completed または t1.failed にする。

7. VLA subtask 起動
   - t1.completed 後、L3 が t2 を ready にし、OpenVLA へ限定 subtask を渡す。

8. Manipulation result
   - grasp 成功なら t2.completed。
   - 失敗なら L3 が retry / abort / ER replan を選ぶ。
```

この流れでは、ER が「赤箱に到着した」と主観的に判断して OpenVLA を呼ぶのでは
ない。到着判定は L3 が State Cache / Nav2 result / safety state を使って行う。

## VLA Subtask Request

OpenVLA へ渡す request は、局所作業に必要な最小情報に制限する。

```json
{
  "schema_version": "vla_subtask_request.v0",
  "request_id": "vla_...",
  "parent_plan_id": "plan_...",
  "task_id": "t2",
  "robot": "bot1",
  "skill": "grasp",
  "instruction": "赤い箱の中の赤いボールを掴む",
  "target": {
    "object_id": "red_ball",
    "class_hint": "ball",
    "color_hint": "red",
    "container_hint": "red_box"
  },
  "observation_refs": {
    "camera_frame": "front-or-wrist-camera-frame-ref",
    "state_snapshot": "state-cache-snapshot-ref"
  },
  "constraints": {
    "base_motion": "not_allowed",
    "allowed_output": "action_candidate_only",
    "direct_actuation": "forbidden"
  }
}
```

渡さない情報:

- Nav2 Bridge URL
- ROS topic 名
- `/cmd_vel`
- Jetson service endpoint
- ESP32 / micro-ROS の詳細
- `gen_id` / `idempotency_key`
- battery / emergency を bypass できる権限

## VLA Output

OpenVLA の output は、実装前の調査で分類する。未分類の output は実機へ流さない。

| output class | 内容 | 扱い |
|---|---|---|
| `grounding_report` | 対象物、bbox、confidence、見えている/見えていない | Option A の cross-check に使える |
| `action_candidate` | grasp / place などの候補動作 | SafetyCompiler が必要 |
| `trajectory_candidate` | end-effector trajectory など | sim-only で検証。直接実行禁止 |
| `low_level_velocity` | wheel / motor velocity | reject |
| `unknown` | 型が説明できない output | reject |

内部案:

```json
{
  "schema_version": "vla_action_candidate.v0",
  "task_id": "t2",
  "source_model": "openvla",
  "output_class": "action_candidate",
  "target_status": {
    "visible": true,
    "confidence": 0.0,
    "debug_label": "red_ball"
  },
  "candidate": {
    "skill": "grasp",
    "object": "red_ball",
    "approach_hint": "model-dependent",
    "gripper_hint": "model-dependent"
  },
  "safety_notes": [
    "raw model output; not executable until validated"
  ]
}
```

`confidence` の閾値、座標系、trajectory 表現、controller 接続方式は未凍結である。
数値や wire schema は実装前に docs / config / contract で決める。

## SafetyCompiler

OpenVLA が action candidate を出す場合、既存 `Command Compiler` だけでは足りない
可能性がある。`Command` は現状 navigate / wait / stop / yield / charge を中心にした
移動命令であり、grasp / place などの manipulation は別の compile 境界が必要になる。

`SafetyCompiler` の責務案:

- VLA output class を分類し、未対応型を reject する。
- 対象物が ER の task graph と一致しているか確認する。
- container / workspace / robot pose の前提が満たされているか確認する。
- base motion が禁止された subtask で base motion 相当の出力がないか確認する。
- trajectory / velocity を直接実行しない。
- skill executor に渡せる抽象 skill call へ変換する。
- result を `ManipulationSkillResult` として Task Graph Executor へ戻す。

内部案:

```text
VlaActionCandidate
  -> SafetyCompiler
  -> ManipulationSkillCall
  -> skill executor / arm controller
  -> ManipulationSkillResult
  -> Task Graph Executor
```

`ManipulationSkillCall` をどの package / topic / REST endpoint で表現するかは未凍結。
この設計段階では、OpenVLA から controller への直結は禁止する。

## 状態遷移

L3 は task ごとに状態を持つ。

```text
pending -> ready -> dispatching -> running -> succeeded
                                      -> failed
                                      -> blocked
                                      -> cancelled
```

移動 task:

```text
ready
  -> Command Compiler
  -> MCP / Policy Gate
  -> Nav2
  -> Nav2 result + State Cache
  -> succeeded / failed
```

VLA task:

```text
ready
  -> VlaSubtaskRequest
  -> VLA Adapter
  -> VlaActionCandidate
  -> SafetyCompiler
  -> skill executor
  -> ManipulationSkillResult
  -> succeeded / failed / blocked
```

VLA task の起動条件:

- parent task の `after` 依存が満たされている。
- robot が expected location にいる。
- state snapshot が fresh と判定される。
- emergency active ではない。
- target が unresolved ではない、または VLA grounding で確認できる。
- fixture / sim gate で同じ output class が検証済みである。

## ER に戻す条件

通常の retry は L3 が扱う。次のような場合だけ ER へ戻して再計画する。

| condition | L3 の初期対応 | ER に戻す理由 |
|---|---|---|
| `target_not_visible` | camera 再取得 / 視点変更 request | そもそも対象が存在しない可能性 |
| `multiple_matching_targets` | operator clarification か ER replan | 赤い物体が複数あり intent が曖昧 |
| `grasp_failed_repeatedly` | retry 上限後に blocked | 別戦略が必要 |
| `object_moved` | latest observation を保存 | task graph の前提が崩れた |
| `unsafe_candidate` | candidate reject | 別 subtask または中止判断が必要 |
| `emergency_active` | immediate 0 dispatch | safety 優先。再開判断は状態回復後 |

ER へ渡す再計画 context 案:

```json
{
  "original_instruction": "赤い箱の中の赤いボールを掴んでトレーに置く",
  "completed_tasks": ["t1"],
  "failed_task": "t2",
  "failure_code": "target_not_visible",
  "robot_state_ref": "state-cache-snapshot-ref",
  "latest_observation_ref": "camera-frame-ref",
  "allowed_actions": ["retry_observation", "ask_operator", "abort", "replan"]
}
```

ER はこの context から、新しい task graph、operator clarification、または abort を提案する。
ただし、再提案も L3 Validator / SafetyCompiler / MCP / Policy Gate を通す。

## OpenVLA を使わない判断

次の条件では、OpenVLA を使わない方がよい。

- task が named location への移動だけで完結する。
- Nav2 / Open-RMF の既存経路で解ける。
- target が known location に snap でき、把持や配置が不要である。
- VLA runtime / license / GPU 要件が未確認である。
- VLA output class を説明できない。
- fixture / sim で reject path が確認できていない。

この場合は Mode X-ER の `ER -> L3 -> Command -> MCP / Policy Gate -> Nav2`
を採用する。

## OpenVLA を使う判断

次の条件が揃う場合に OpenVLA を候補にする。

- 移動後に grasp / place / align / dock などの局所操作が必要である。
- 対象物の位置や姿勢が毎回変わる。
- scripted controller だけでは対象物の見え方の変化を扱いにくい。
- OpenVLA output を `grounding_report` または `action_candidate` として分類できる。
- SafetyCompiler または equivalent validator で reject できる。
- offline fixture と simulation gate を通過している。

## MVP 方針

Mode X-ER-VLA の MVP は、OpenVLA の能力を最大限使うことではなく、安全な接続境界を
確認することである。

1. 移動のみの scenario は Mode X-ER で完結させる。
2. OpenVLA は offline fixture で output class を観察する。
3. 最初は `grounding_report` cross-check として扱う。
4. action candidate は sim-only で replay する。
5. grasp / place の実機接続は human gate と contract freeze 後にする。

この順序なら、OpenVLA の価値が低い task では採用を defer でき、価値が高い
manipulation task だけに限定して評価できる。
