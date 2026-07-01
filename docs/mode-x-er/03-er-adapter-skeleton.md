# Mode X-ER Gemini Robotics-ER Adapter Skeleton

作成日: 2026-06-22

> **状態**: 設計スケルトン。Gemini Robotics-ER の adapter seam を定義するが、特定 model API、config key、ROS topic、`warehouse_interfaces` contract はまだ凍結しない。

## 目的

Mode X-ER は Gemini Robotics-ER のみを使う設計である。OpenVLA などの VLA と統合する設計は `docs/mode-x-er-vla/` に分ける。

Gemini Robotics-ER Adapter は、音声 / transcript / 俯瞰画像 / state snapshot / calibration metadata を受け取り、`RoboticsPlan draft` を提案する。提案はそのまま実行せず、必ず L3 Planning Core に渡す。

## Adapter 境界

```
Audio / transcript / image / state
  -> GeminiErAdapter
  -> RoboticsPlan draft
  -> L3 Planning Core
```

内部 interface 案:

```python
class GeminiErAdapter:
    name: str = "gemini-robotics-er"

    async def propose_plan(
        self,
        request: ErTaskRequest,
    ) -> RawModelOutput:
        ...
```

## ErTaskRequest 案

```json
{
  "request_id": "turn_...",
  "mode": "mode-x-er",
  "instruction_audio_ref": "audio-ref",
  "transcript": "optional transcript",
  "overhead_image_ref": "frame-ref",
  "state_snapshot_ref": "state-ref",
  "calibration_id": "calib-YYYYMMDD",
  "known_robots": ["bot1", "bot2"],
  "known_locations": ["shelf_1", "shelf_2", "charging_station"],
  "allowed_actions": ["navigate", "wait", "stop", "yield", "charge"],
  "output_contract": "robotics_plan_draft.v0"
}
```

この request は Gemini Robotics-ER へ送る情報の上限である。Nav2 Bridge URL、ROS topic、Jetson service、MCP internal tool name は渡さない。ER request assembly（transport 別の凍結形）は本書末尾「## ER request assembly（transport 別・凍結）」を参照。

## RoboticsPlan Draft 最小形

```json
{
  "schema_version": "robotics_plan_draft.v0",
  "plan_id": "plan_...",
  "source_model": "gemini-robotics-er",
  "input_refs": {
    "audio": "audio-ref",
    "image": "frame-ref",
    "state": "state-ref"
  },
  "transcript": "...",
  "interpreted_intent": "...",
  "detections": [],
  "task_graph": [],
  "operator_clarification_required": false
}
```

`source_model` は audit 用であり、下流の実行分岐に使わない。L3 の policy は `source_model` ではなく、plan 内容、state、calibration、profile で判断する。

## 責務分離

| 層 | Gemini Robotics-ER が担うこと | Gemini Robotics-ER に任せないこと |
|---|---|---|
| L4 | 音声・画像・state を読んで intent / detections / task_graph を提案する | ROS topic / Nav2 action / Jetson endpoint の直接呼び出し |
| L3 Validator | なし。model output は検証対象 | 自己採点だけで dispatch 可否を決めること |
| L3 Resolver | なし。pixel / bbox は入力になる | camera calibration / map frame の最終責任 |
| L3 Task Graph | 依存関係の提案 | completion 判定、二重 dispatch 防止 |
| L3 Compiler | なし | 既存 `Command` / MCP tool call の生成 |
| L2 | なし | Policy Gate、冪等性、battery、emergency 判定 |

## Integration Gates

| Gate | 内容 | 失敗時 |
|---|---|---|
| G0 offline parse | fixture raw output が `RoboticsPlan draft` に正規化できる | adapter 修正 |
| G1 validator | invalid robot/action/target/confidence/stale/emergency を 0 dispatch にできる | L3 policy 修正 |
| G2 visual resolver | fixture image の red/blue target が known location へ snap できる | calibration / resolver 修正 |
| G3 task graph | `after` 依存が守られる | executor 修正 |
| G4 command compile | ready task が既存 `Command` validation を通る | compiler 修正 |
| G5 X-lite sim | MCP / Policy Gate / Nav2 Bridge まで sim で通る | L2 接続修正 |
| G6 X-rmf eval | X-rmf が X-lite より有利なタスクで価値を示す | X-rmf defer |

## ER request assembly（transport 別・凍結）

`ErTaskRequest` → provider request の組み立てを **transport 別に凍結**する。transport 選択は `robotics/transport.py` `resolve_audio_transport`（`direct` 既定・恒久 fallback / forked gateway 設定時のみ `hermes`。#388・[README「Transport (index)」](README.md)）。content part の形（`inline_data` / `input_audio` / `image_url`）は **外部 API 仕様であって当方の発明ではない**（doc06 §5:14）。実測根拠は **closed probe #344**（[`06-unfrozen-contract-resolutions.md` §5:11-14](06-unfrozen-contract-resolutions.md)・[`04`:28-29](04-er-input-modalities-and-stt.md)）。既存 direct-image 経路を壊さない **additive**（新 transport は enum 追加・doc06 §5:16）。

**共通**: instruction text = schema 制約（robots ∈ `known_robots`・action ∈ `allowed_actions`・target = detection id・URL/topic/velocity/coordinate 禁止）＋ `transcript`（あれば）。`instruction_audio_ref` / `overhead_image_ref` は **base64 化された bytes の参照**（値は request 組み立て時に解決）。

- **`direct`**（Gemini REST `POST .../models/<er-model>:generateContent`・PROBE-1 実測 HTTP 200・§5:11）:
  `contents:[{"role":"user","parts":[ {"text": <instruction>}, {"inline_data":{"mime_type":"audio/wav","data":"<base64 ≤20MB>"}}?（audio_ref 時）, {"inline_data":{"mime_type":"image/<fmt>","data":"<base64>"}}?（image_ref 時） ]}]` ＋ `generationConfig:{responseMimeType:"application/json", ...}`。**応答 = direct envelope**（`candidates[].content.parts[].text` の JSON）→ `RawModelOutput(transport="direct")`。
- **`hermes`**（Hermes OpenAI 互換 `POST /v1/chat/completions`・vision provider 必須）:
  `messages:[{"role":"user","content":[ {"type":"text","text": <instruction>}, {"type":"image_url","image_url":{"url":"data:image/<fmt>;base64,<...>"}}?（image_ref 時・PROBE-3 実測 200・§5:13）, {"type":"input_audio","input_audio":{"data":"<base64>","format":"wav"}}?（audio_ref 時） ]}]`。**応答 = hermes envelope**（`choices[].message.content` の JSON）→ `RawModelOutput(transport="hermes")`。
  - **audio-via-Hermes は fork 必須**: unforked Hermes は `input_audio` を **HTTP 400 `unsupported_content_type`**（PROBE-2・§5:12）で弾く。fork（[`deploy/hermes/er-audio-fork/`](../../deploy/hermes/er-audio-fork/)・#357）配備時のみ 200。
- **fallback（凍結）**: `hermes` を選択したが transport が失敗（audio で unforked 400 / gateway 不通 / 非 200）した場合、**`direct` に fail-safe fallback**（audio/image とも direct は一次経路・§5:8,14）。**shipped default の audio は依然 `direct`**（恒久 fallback・doc06:269）。両 transport は同一 `RoboticsPlanDraft` に正規化される（README:86・L3 Handoff の transport 等価性で担保）。

> 実装: `robotics/adapters/gemini_er.py` `propose_plan` が本節の凍結形で live 送信する（別 PR）。**robotics-grade command 品質 eval と実 gateway 配備・有料 live verify は human gate**（doc04:28 / environments.md `.env` 承認）。
