# Mode X-ER: Gemini Robotics-ER 視覚タスク司令

作成日: 2026-06-22

> **状態**: 設計提案。Mode X-ER はまだ config / ROS topic / REST API / `warehouse_interfaces` frozen contract を追加しない。最初の実装に入る前に、本ディレクトリの設計を確定し、必要な契約変更を別 PR で凍結する。

## 位置づけ

Mode X-ER は、Gemini Robotics-ER を「音声・画像・状態から倉庫タスクを理解する司令塔」として使う Mode X 系の最初の具体案である。既存 Mode A/B/C の LLM provider 比較とは分け、Google Gemini Robotics-ER 固定で以下を検証する。

- 音声指示の解釈
- 俯瞰カメラ画像からの赤箱 / 青箱などの object target 認識
- `bot1 が赤箱到達後に bot2 が青箱へ` のような task graph 分解
- 既存 MCP / Policy Gate / Nav2 / Open-RMF 経路へ安全に接続できるか

Gemini Robotics-ER は Nav2 / ROS / Jetson / `/cmd_vel` を直接叩かない。ER は「見る・理解する・計画する」層に閉じ込め、実行可能な command 候補への変換は L3 Robotics Planning Core が担う。

L4 は単なる薄い ER adapter ではなく、既存 `warehouse_llm_bridge` を拡張した
Robotics Bridge Super-Box として扱う。Hermes Agent は provider transport / MCP /
vision / voice / plugin などの generic integration の**第一候補**であり、Mode X-ER
では `transport: hermes` を default path として検討する。ただし input context の最終
manifest、timeout、trace ownership、L3 handoff、`action_map`、`gen_id` /
`idempotency_key` 注入、0 dispatch safety は Bridge-owned に残す。詳細は
[`productization/02-l4-robotics-bridge-box.md`](../productization/02-l4-robotics-bridge-box.md)
を正本にする。

## ディレクトリ構成

| ファイル | 内容 |
|---|---|
| [README](README.md) | Mode X-ER の位置づけ、正本ファイル、未凍結事項 |
| [01-architecture-and-flow](01-architecture-and-flow.md) | L4 -> L3 -> L2 -> L1/L0 の data flow、X-lite / X-rmf の切り分け |
| [02-l3-planning-core](02-l3-planning-core.md) | Validator / Visual Resolver / Task Graph Executor / Command Compiler の詳細設計 |
| [03-er-adapter-skeleton](03-er-adapter-skeleton.md) | Gemini Robotics-ER 単体 adapter skeleton と integration gates |
| [04-er-input-modalities-and-stt](04-er-input-modalities-and-stt.md) | ER の入力モダリティ（audio 直受け）と STT の要否、ER 単体時の Fusion 必要性 |
| [05-operator-feedback-and-voice-response](05-operator-feedback-and-voice-response.md) | 拒否/要確認/緊急を「どの箱の・どの理由か」音声で人へ返す **L4 Operator Feedback Box**（新 sub-box）。図解 [`operator-feedback-flow.html`](operator-feedback-flow.html) |

旧 `docs/mode-x/` は互換参照として残す。Gemini Robotics-ER 単体の新規設計・実装判断は本 `docs/mode-x-er/` を正本にする。Gemini Robotics-ER と OpenVLA などの VLA を統合する設計は、同階層の `docs/mode-x-er-vla/` を正本にする。

## 標準フロー

```
operator voice
  -> optional STT / transcript
  -> overhead camera frame
  -> State Cache snapshot
  -> Robotics Bridge
  -> Gemini Robotics-ER Adapter
  -> RoboticsPlan draft
  -> L3 Planning Core
     -> Validator
     -> Visual Resolver
     -> Task Graph Executor
     -> Command Compiler
  -> action_map
  -> MCP / Policy Gate
  -> X-lite: Nav2 Bridge REST
     or X-rmf: Open-RMF Task API / Fleet Adapter
  -> Jetson namespaced Nav2
  -> micro-ROS Agent
  -> ESP32 firmware
  -> motors
```

L3 の output は ROS / Nav2 への直接命令ではない。L3 は **検証済み・target 解決済み・依存関係管理済みの既存 `Command` 候補**を作る。最終的な実行許可は L2 の MCP / Policy Gate が担う。

**戻り（拒否通知）**: 上記いずれかの gate が `rejected / needs_clarification / emergency_stop` になったときは、その decision を **L4 Operator Feedback Box** が拾い、「どの箱の・どの理由か」を音声で operator に返す（motion は持たない）。設計は [05-operator-feedback-and-voice-response](05-operator-feedback-and-voice-response.md)。

## Mode 名の扱い

| 呼称 | 意味 |
|---|---|
| Mode X | 視覚・音声・言語から robot task を作る将来拡張枠 |
| Mode X-ER | Gemini Robotics-ER だけを使う具体案 |
| Mode X-ER-VLA | Gemini Robotics-ER と VLA / OpenVLA を統合する別モード |
| X-lite | Mode X-ER の MVP 実行 profile。MCP / Policy Gate -> Nav2 Bridge REST |
| X-rmf | Mode X-ER の optional 実行 profile。MCP / Policy Gate -> Open-RMF Task API / Fleet Adapter |

Mode X-ER では VLA / OpenVLA を扱わない。VLA が L3 を代行・補助する設計は `docs/mode-x-er-vla/` で扱う。

## 実装フェーズ案

| Phase | 内容 | 完了条件 |
|---|---|---|
| XER0 | docs 設計 | Mode X-ER の data flow、L3 境界、ER adapter skeleton を docs 化 |
| XER1 | offline fixture | 静止画像 + text/audio ref + fake state から `RoboticsPlan draft` を作る。Hermes 経由 request shape と direct fallback request shape が同じ L3 Handoff input に正規化できることを fixture 化する |
| XER2 | Validator | schema / rule / policy を分離し、0 dispatch と `ValidationReport` を出せる |
| XER3 | Visual Resolver | pixel -> map -> known location snap を fixture で検証する |
| XER4 | Task Graph Executor | `after` 依存、ready/running/succeeded/failed を offline で検証する |
| XER5 | Command Compiler | ready task を既存 `Command` に変換し、`warehouse_interfaces` validation を通す |
| XER6 | X-lite E2E | MCP / Policy Gate / Nav2 Bridge まで sim で通す |
| XER7 | X-rmf eval | Open-RMF を使う価値が X-lite を上回るか評価する |

## 未凍結事項

- `RoboticsPlan` schema
- `ValidationReport` schema
- Mode X-ER config key
- calibration artifact の配置と形式
- visual target を coordinate `goal` として MCP / Policy Gate へ通す正式契約
- Gemini Robotics-ER direct adapter と Hermes 経路の扱い（L4 境界は productization/02 に従う）。現方針は Hermes-first、direct / worker は Hermes 非対応 modality / runtime / response shape の fallback
- Hermes Langfuse plugin を trace owner にできるか（productization/02 の HLF-G0〜G5 live spike gate）
- X-rmf の temporary waypoint / task submission seam
- `OperatorNotice` schema と decision_event → Operator Feedback Box の transport（[05](05-operator-feedback-and-voice-response.md) §7。taxonomy 正本 productization/01 への box 登録は owner 調整の別 PR）
