# Mode X-ER-VLA: Gemini Robotics-ER + VLA 統合モード

作成日: 2026-06-22

> **状態**: 設計提案。Mode X-ER-VLA はまだ config / ROS topic / REST API / `warehouse_interfaces` frozen contract を追加しない。Gemini Robotics-ER と OpenVLA などの VLA を統合する別モードとして、Mode X-ER と同じ階層で設計する。

## 位置づけ

Mode X-ER は Gemini Robotics-ER だけで視覚タスク司令を成立させる設計である。Mode X-ER-VLA はその派生ではなく、Gemini Robotics-ER と VLA を組み合わせる別モードとして扱う。

想定する役割分担:

- Gemini Robotics-ER: 音声指示、自然言語 intent、俯瞰画像の意味理解、task graph の高レベル提案。
- VLA / OpenVLA: 視覚 grounding、action candidate、robot embodiment 寄りの判断、simulation での policy 候補評価。
- Robotics Bridge: ER / VLA の output を統合し、既存 MCP / Policy Gate / Nav2 / Open-RMF 経路へ安全に落とす。

安全境界:

- ER / VLA output を `/cmd_vel`、Nav2 action、Jetson service、ESP32 firmware へ直接流さない。
- model が出した velocity、motor command、low-level action は、そのまま物理実行しない。
- `gen_id` / `idempotency_key` は model に作らせない。
- 最終 actuation は MCP / Policy Gate / Nav2 / Open-RMF / Layer0 safety を通す。
- docs に無い config key、topic、schema、threshold を実装で発明しない。

## ディレクトリ構成

| ファイル | 内容 |
|---|---|
| [README](README.md) | Mode X-ER-VLA の位置づけ、Mode X-ER との差分、未凍結事項 |
| [01-integration-architecture.md](01-integration-architecture.md) | ER + VLA 統合 architecture と data flow |
| [02-openvla-research-plan.md](02-openvla-research-plan.md) | OpenVLA を今回の倉庫 task へ適用できるか調べる観点 |
| [03-simulation-and-safety-gates.md](03-simulation-and-safety-gates.md) | Isaac Sim / offline fixture / 実機接続前 safety gates |
| [04-openvla-use-cases-and-control-flow.md](04-openvla-use-cases-and-control-flow.md) | OpenVLA の用途、L3 による起動タイミング、把持/配置 subtask の制御フロー |

## Mode X-ER との違い

| 観点 | Mode X-ER | Mode X-ER-VLA |
|---|---|---|
| 主 model | Gemini Robotics-ER のみ | Gemini Robotics-ER + VLA / OpenVLA |
| L3 の扱い | deterministic L3 が ER output を compile | VLA が L3 の一部を代行または補助する可能性を検証 |
| 初期 output | `RoboticsPlan draft` | `RoboticsPlan draft` + `VlaGroundingReport` + 統合候補 |
| 実装の近さ | X-lite MVP に近い | research / simulation / integration spike 先行 |
| 実行経路 | MCP / Policy Gate -> Nav2 Bridge or Open-RMF | 原則同じ安全経路。ただし ER/VLA fusion と Safety Compiler を検討 |
| 目的 | ER 単体で視覚タスク司令を成立させる | ER の高レベル理解と VLA の action grounding を統合する価値を検証 |

## 設計ルール

Mode X-ER-VLA では、最初から実装へ入らない。まず以下を docs で確定する。

1. ER と VLA にそれぞれ何を入力するか。
2. ER と VLA の output をどう統合するか。
3. VLA が L3 のどの component を代行するのか。
4. output が plan なのか action candidate なのか trajectory candidate なのか。
5. 既存 MCP / Policy Gate に落とす compile 境界をどこに置くか。
6. Isaac Sim / offline fixture でどこまで検証してから実機へ進むか。
7. OpenVLA の runtime / GPU / Jetson / RunPod / license / dataset 制約をどう扱うか。

## 初期採用判断

移動だけの task は Mode X-ER の `ER -> L3 -> Command -> MCP / Policy Gate
-> Nav2` で扱い、OpenVLA は原則使わない。OpenVLA は、赤い箱へ移動した後に
赤いボールを掴む、トレーへ置く、ドッキングする、近接位置合わせをする、など
Nav2 だけでは表現できない局所操作が入った時に候補にする。

OpenVLA を使う場合も、ER や OpenVLA が実行タイミングを直接握らない。ER は
高レベル task graph を提案し、L3 が State Cache / Nav2 result / task result を
見て `ready` task を進める。VLA subtask は、L3 が到着や前提条件を確認した後に
限定された request として起動する。

## 未凍結事項

- OpenVLA の採用可否
- ER / VLA input contract
- ER / VLA output contract
- ER/VLA fusion strategy
- VLA が代行する L3 component
- Safety Compiler の必要性
- Mode X-ER-VLA の config key
- VLA 実行環境、GPU 要件、ライセンス確認
- X-lite / X-rmf のどちらへ接続するか
