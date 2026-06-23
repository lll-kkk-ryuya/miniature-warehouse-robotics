# Mode X: Gemini Robotics-ER 視覚タスク司令

作成日: 2026-06-22

> **互換参照**: 新規設計判断の正本は `docs/mode-x-er/` または `docs/mode-x-er-vla/`。本ファイルは初期 Mode X メモとして残す。

> **状態**: 設計提案。Mode X-ER はまだ config / ROS topic / interface / frozen contract を追加しない。最初の実装に入る前に、`docs/mode-x-er/` の設計を確定し、必要な契約変更を別 PR で凍結する。

## 位置づけ

Mode X は、Gemini Robotics-ER を「視覚と言語からタスクを理解する司令塔」として使うモードである。既存の Mode A/B/C と違い、LLM provider 比較を目的にしない。Google Gemini Robotics-ER 固定で、赤い箱・青い箱の認識、音声指示の解釈、複数 bot のタスク分解を検証する。

既存モードとの差分:

| モード | 主目的 | 入力 | 交通管理 | LLM/モデル比較 |
|---|---|---|---|---|
| Mode A | LLM 単独交通管理の動画デモ | state JSON + task seed | 司令官 LLM | あり |
| Mode B | 簡易ルール交通管理 + LLM | state JSON + task seed | SimpleTrafficManager | あり |
| Mode C | LLM + Open-RMF 実用検証 | state JSON + task seed | Open-RMF | あり |
| Mode X | Gemini Robotics-ER による視覚タスク司令 | 音声 + 俯瞰画像 + state JSON | MVP では Nav2 Bridge / optional で Open-RMF | なし |

## 基本方針

Gemini Robotics-ER は Nav2 / ROS / Jetson を直接叩かない。ER は「見る・理解する・計画する」層に置き、実行は Robotics Bridge が既存の MCP / Policy Gate / Nav2 / Open-RMF 経路へ変換する。

直接実行を避ける理由:

- モデル出力をそのまま actuation に使うと、古い応答・重複応答・誤検出が直接ロボット動作になる。
- 既存の gen_id / idempotency_key / Policy Gate / Emergency Guardian / Nav2 safety の防御線を維持する必要がある。
- Gemini Robotics-ER の出力は、物体検出・タスク分解・軌道候補などの「提案」であり、ROS 2 実行契約ではない。
- Mode X でも最終的な物理停止は Layer 0 / Layer 1 / Nav2 safety 側に残す。

## 呼称

既存の LLM Bridge は Mode X では **Robotics Bridge** として扱う。既存パッケージ名 `warehouse_llm_bridge` をすぐ変更する必要はないが、責務上は以下へ拡張される。

- 旧: state JSON を LLM に送り、Command JSON を受け取る。
- 新: 音声 / transcript / 俯瞰画像 / state JSON を Gemini Robotics-ER に送り、視覚タスク計画を既存 Command / MCP / Nav2 / Open-RMF 経路へ変換する。

## Mode X の標準フロー

```
operator voice
  -> optional STT / transcript
  -> overhead camera frame
  -> State Cache snapshot
  -> Robotics Bridge
  -> Gemini Robotics-ER Adapter
  -> RoboticsPlan
  -> Visual Task Resolver
  -> Command Compiler
  -> MCP / Policy Gate
  -> Nav2 Bridge or Open-RMF Fleet Adapter
  -> Jetson Nav2
  -> micro-ROS Agent
  -> bot1 / bot2
```

詳細な layer 別の流れは、新規設計では [mode-x-er/01-architecture-and-flow](../mode-x-er/01-architecture-and-flow.md) を正本とする。本 `mode-x/` 配下の [08x-robotics-bridge-mode-x](08x-robotics-bridge-mode-x.md) は初期 Mode X メモの互換参照であり、未移管の論点確認にだけ使う。

## ER 出力から Jetson へ届く変換経路

Gemini Robotics-ER の出力は、直接 ROS topic や REST endpoint へ流さない。まず内部表現 `RoboticsPlan` に正規化する。

例:

```json
{
  "transcript": "bot1は赤の箱へ。bot1が到達したらbot2は青の箱へ。",
  "interpreted_intent": "bot1 red_box first, then bot2 blue_box",
  "detections": [
    {"id": "red_box", "color": "red", "pixel": [420, 310], "confidence": 0.92},
    {"id": "blue_box", "color": "blue", "pixel": [810, 280], "confidence": 0.89}
  ],
  "task_graph": [
    {"id": "t1", "robot": "bot1", "action": "navigate", "target": "red_box"},
    {"id": "t2", "robot": "bot2", "action": "navigate", "target": "blue_box", "after": "t1.completed"}
  ]
}
```

Robotics Bridge はこの `RoboticsPlan` を次の順に処理する。

1. JSON schema / confidence / allowed action を検証する。
2. `detections[].pixel` を俯瞰カメラの calibration で map 座標へ変換する。
3. 既知 location に寄せられる場合は `destination` 名へ snap する。
4. 既知 location に寄せられない場合は、coordinate `goal` 経路の採用可否を Policy Gate で検証する。
5. `task_graph` の依存関係を保持し、ready な command だけを発行する。
6. 既存の Command / MCP tool call に変換し、Bridge が `gen_id` と `idempotency_key` を注入する。
7. MCP / Policy Gate が受理した motion tool だけを Nav2 Bridge または Open-RMF へ渡す。

このため、ER の返答が誤っていても、未解決 target / 低 confidence / stale state / duplicate / Policy reject は robot actuation へ届かない。

## STT 方針

Mode X の音声処理は二段階で進める。

| 段階 | 方針 | 理由 |
|---|---|---|
| MVP | 音声を Gemini Robotics-ER に渡し、出力に `transcript` と `interpreted_intent` を必須化する | 実装が軽く、音声 + 視覚を同じ model turn で評価できる |
| 安定運用 | 明示 STT を前段に置き、`audio + transcript + overhead image + state JSON` を ER に渡す | 監査、再現、UI 修正、Langfuse 記録、比較しやすさを確保する |

STT を置く場合でも、音声原本は ER 入力に残してよい。transcript は監査用の anchor、音声はイントネーションや聞き間違い補正の補助として扱う。

## Open-RMF 採否の初期判断

Mode X MVP では Open-RMF を必須にしない。まずは `X-lite` として、Gemini Robotics-ER + Robotics Bridge + MCP / Policy Gate + Nav2 Bridge で成立させる。

理由:

- Mode X の新規性は交通管理ではなく、視覚タスク理解と object target 解決にある。
- 赤箱 / 青箱への順序付き移動は、Robotics Bridge の task graph executor で表現できる。
- Open-RMF を入れると、視覚 object target を RMF waypoint / task API にどう渡すかという別問題が増える。
- 既存の Mode C で Open-RMF は交通管理の主方針だが、Mode X は provider 比較ではないため、まず単純な実行経路で検証する。

ただし、次の条件を満たす場合は `X-rmf` を再評価する。

- 2台が同時に狭い通路や交差点を通るタスクが増える。
- `bot2 は bot1 到達後に出発` だけでなく、複数タスクの優先度変更や予約制御が必要になる。
- visual target を安定した waypoint / temporary waypoint として RMF 側に登録できる。
- Mode C の Fleet Adapter 経路が実機または Gazebo で十分に検証済みになる。

### X-lite と X-rmf の切り分け

| profile | 実行経路 | 使う場面 | 採用状態 |
|---|---|---|---|
| `X-lite` | Robotics Bridge → MCP / Policy Gate → Nav2 Bridge REST → Nav2 | 赤箱/青箱の視覚認識、順序付き移動、単純な2台制御 | MVP 採用 |
| `X-rmf` | Robotics Bridge → MCP / Policy Gate → Open-RMF Task API → Fleet Adapter → Nav2 | 複数台の予約制御、狭路・交差点の交通交渉、RMF waypoint 化できる visual target | 再評価候補 |

`X-rmf` は「Gemini Robotics-ER をやめる」案ではない。ER は WHAT（何をしたいか、どの object target か）を出し、Open-RMF は HOW（どの経路・どの順番・どの待機で衝突なく動かすか）を担う。

`X-rmf` を実装する前提条件:

- Visual Task Resolver が object target を `known_location` または stable `temporary_waypoint` へ変換できる。
- Mode C の custom Fleet Adapter 経路が live / sim で成立している。
- temporary waypoint を RMF Navigation Graph / task API に渡す方式が docs-first で決まっている。
- X-lite で ER の認識精度、task graph、到達判定の基本 E2E が確認済みである。

## 実装フェーズ案

| Phase | 内容 | 完了条件 |
|---|---|---|
| X0 | docs 設計 | Mode X の責務、変換経路、Open-RMF 採否基準を docs 化 |
| X1 | offline fixture | 静止画像 + テキスト指示 + fake state から `RoboticsPlan` を作る |
| X2 | visual resolver | 赤/青箱 pixel を map 座標または known location に変換する |
| X3 | command compiler | `RoboticsPlan` を既存 Command / MCP tool call へ変換する |
| X4 | audio MVP | 音声入力を渡し、`transcript` / `interpreted_intent` を必須出力にする |
| X5 | stable STT | 明示 STT + ER の二重入力にする |
| X6 | X-rmf eval | Open-RMF を使う価値が X-lite を上回るか評価する |

## 未凍結事項

- `RoboticsPlan` schema
- visual target を coordinate `goal` として MCP / Policy Gate へ通す正式契約
- calibration file の配置と形式
- Mode X の config key
- Gemini Robotics-ER の direct Google API 経路と Hermes 経路の扱い
- Open-RMF `X-rmf` の task submission seam
