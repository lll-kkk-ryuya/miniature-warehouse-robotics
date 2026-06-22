# Mode X-ER-VLA Simulation And Safety Gates

作成日: 2026-06-22

> **状態**: 設計スケルトン。ER + VLA を実機へ接続する前の simulation / offline gate を定義する。具体的な Isaac Sim scene、topic、config、threshold は未凍結。

## 方針

Mode X-ER-VLA では、ER と VLA の両方が候補を出す可能性がある。そのため、実機接続前に simulation と offline fixture を必須にする。ER/VLA output を直接 actuation へ接続しない。

## Gate

| Gate | 内容 | 完了条件 |
|---|---|---|
| G0 fixture | recorded image + text instruction + fake state を ER/VLA に入力する | ER/VLA response を保存できる |
| G1 output classification | VLA response が grounding / action / trajectory / unknown のどれか分類する | docs に output shape を記録 |
| G2 fusion decision | ER と VLA の output をどう統合するか分類する | Cross-check / L3 candidate / Safety Compiler を選ぶ |
| G3 offline validator | invalid robot/action/target/stale/emergency を 0 dispatch にできる | reject reason を `ValidationReport` 相当で残す |
| G4 sim-only replay | Isaac Sim または offline evaluator で ER/VLA candidate を replay する | 実機なしで failure を再現できる |
| G5 compiler decision | `Command Compiler` で足りるか、`SafetyCompiler` が必要か判断する | docs に判断を残す |
| G6 MCP path | command candidate が MCP / Policy Gate を通る | bypass なし |
| G7 robot-gated | 実機接続を人間 gate にする | Jetson / ESP32 / safety の確認後のみ |

## 実機接続前に禁止すること

- VLA output の velocity を `/cmd_vel` に直接 publish する。
- VLA output の trajectory を Nav2 action に直接送る。
- Jetson 上の service / REST endpoint を ER/VLA へ教える。
- ESP32 firmware の速度 clamp を前提にして上位安全を省略する。
- fixture / sim で未検証の output shape を実機に流す。

## VLA 起動前の L3 条件

VLA subtask は、ER が出した高レベル plan から直接起動しない。L3 が次の条件を
満たした時だけ、限定された `VlaSubtaskRequest` 相当を作る。

- `after` 依存が満たされている。
- State Cache / Nav2 result / task result で前段 task の完了を確認できる。
- robot が expected location にいる。
- emergency active ではない。
- VLA output class が fixture / sim で分類済みである。
- VLA response を reject できる deterministic validator または SafetyCompiler がある。

移動のみの task では VLA を起動しない。把持、配置、ドッキング、近接位置合わせ
などの局所操作だけを VLA 起動候補にする。

## Isaac Sim で見る観点

- overhead camera と robot-mounted camera のどちらを VLA 入力にするか。
- ER の object detection / task graph と VLA の grounding が一致するか。
- 赤箱 / 青箱 / robot pose / aisle / shelf を識別できるか。
- sim の object label と real camera の見え方の差分。
- latency が task cycle に収まるか。
- ER/VLA disagreement がどの程度起きるか。
- failure case を golden fixture にできるか。

## 実機へ進む条件

ER + VLA を実機 path に近づけるには、最低限以下を満たす。

- offline fixture で ER/VLA output shape が説明できる。
- sim-only replay で危険な output を再現し、reject できる。
- ER/VLA output を既存 `Command` または明示的な `ActionCandidate` へ正規化できる。
- deterministic Validator / SafetyCompiler が存在する。
- MCP / Policy Gate を bypass しない。
- Emergency Guardian / Layer0 safety の優先順位を変えない。
