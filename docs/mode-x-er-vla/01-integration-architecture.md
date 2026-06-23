# Mode X-ER-VLA Integration Architecture

作成日: 2026-06-22

> **状態**: 設計候補。ここでは ER + VLA 統合時の大枠を比較する。特定 model API、ROS topic、config key、`warehouse_interfaces` contract はまだ凍結しない。

## 目的

Mode X-ER-VLA は、Gemini Robotics-ER と VLA を同時に使う場合の architecture を検討する。焦点は、VLA が Mode X-ER の L3 Planning Core をどこまで補助・代行できるかである。

移動だけの task では OpenVLA を必須にしない。OpenVLA の主用途は、Nav2 だけでは
表現できない把持、配置、ドッキング、近接位置合わせなどの局所操作である。用途と
L3 による起動タイミングは
[04-openvla-use-cases-and-control-flow.md](04-openvla-use-cases-and-control-flow.md)
を正本にする。

## Option A: ER Primary + VLA Cross-Check

ER が `RoboticsPlan draft` を作り、VLA は visual grounding / target confidence の cross-check に使う。

```
audio / transcript / overhead image / state
  -> Gemini Robotics-ER
  -> RoboticsPlan draft

image / state / candidate target
  -> VLA Adapter
  -> VlaGroundingReport

RoboticsPlan draft + VlaGroundingReport
  -> Fusion Validator
  -> Visual Resolver
  -> Task Graph Executor
  -> Command Compiler
  -> MCP / Policy Gate
```

利点:

- Mode X-ER の設計を壊しにくい。
- VLA を安全な cross-check として導入できる。
- 低 confidence / disagreement を operator clarification に戻しやすい。

弱点:

- VLA の action 能力をあまり使わない。
- ER と VLA の disagreement policy が必要。

## Option B: ER Planner + VLA L3 Candidate Generator

ER が high-level task graph を作り、VLA が Visual Resolver / action candidate を補助する。

```
ER: voice + image + state -> task_graph / intent
VLA: image + state + task_graph -> grounded target / action candidate

ER task_graph + VLA candidate
  -> Validator
  -> Safety Compiler
  -> Command candidate
  -> MCP / Policy Gate
```

利点:

- ER は自然言語・高レベル task、VLA は視覚 grounding / action という役割分担にできる。
- L3 の一部代行というユーザー意図に近い。
- OpenVLA の出力が action 寄りでも扱いやすい。

弱点:

- Safety Compiler の設計が必要になる可能性が高い。
- VLA output を既存 `Command` へ落とす境界を慎重に設計する必要がある。

## Option C: Sim-First ER+VLA Policy Evaluation

ER + VLA を実機 command path へ接続する前に、Isaac Sim / offline fixture で policy candidate として評価する。

```
recorded image / sim camera / text instruction / fake state
  -> ER + VLA
  -> integrated candidate
  -> simulator-only evaluator
  -> score / failure analysis
  -> later: Fusion Validator / Safety Compiler
```

利点:

- 実機へつなぐ前に ER/VLA の相互作用を観察できる。
- OpenVLA の runtime / GPU / latency / input-output shape を検証しやすい。
- 失敗例を golden fixture 化できる。

弱点:

- 実機 E2E まで遠い。
- Isaac Sim の scene / camera / object labels / robot model 整備が前提になる。

## 初期判断

初期は **Option C -> Option A -> Option B** の順に進める。

1. まず sim / offline fixture で ER+VLA の input-output を観察する。
2. VLA が target confidence / grounding の補助に使えるなら Option A。
3. VLA が action candidate 生成に使えるなら Option B。
4. どの option でも、ER/VLA から直接 Nav2 / ROS / Jetson / ESP32 を叩かせない。

移動のみの scenario は Mode X-ER で先に完結させ、Mode X-ER-VLA では
manipulation subtask を含む scenario を追加評価する。
