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

- **`direct`**（Gemini REST `POST .../models/<er-model>:generateContent`。provenance: **text+audio の 2-part は PROBE-1 実測 HTTP 200**（§5:145,158）/ **single direct-image は別 spike で実測 HTTP 200**（[`vla-access-and-runtime-spike.md`](../dev/vla-access-and-runtime-spike.md):26・§5:139・Gemini API 直接 call）/ **text+audio+image の 3-part combined direct request は一度も POST しておらず inferred-from-spec**（content part 形は外部 API 仕様＝当方の発明ではない・§5:14））:
  `contents:[{"role":"user","parts":[ {"text": <instruction>}, {"inline_data":{"mime_type":"audio/wav","data":"<base64 ≤20MB>"}}?（audio_ref 時）, {"inline_data":{"mime_type":"image/<fmt>","data":"<base64>"}}?（image_ref 時） ]}]` ＋ `generationConfig:{responseMimeType:"application/json", ...}`。**応答 = direct envelope**（`candidates[].content.parts[].text` の JSON）→ `RawModelOutput(transport="direct")`。
- **`hermes`**（Hermes OpenAI 互換 `POST /v1/chat/completions`・vision provider 必須）:
  `messages:[{"role":"user","content":[ {"type":"text","text": <instruction>}, {"type":"image_url","image_url":{"url":"data:image/<fmt>;base64,<...>"}}?（image_ref 時・PROBE-3 実測 200・§5:13）, {"type":"input_audio","input_audio":{"data":"<base64>","format":"wav"}}?（audio_ref 時） ]}]`。**応答 = hermes envelope**（`choices[].message.content` の JSON）→ `RawModelOutput(transport="hermes")`。
  - **audio-via-Hermes は fork 必須**: unforked Hermes は `input_audio` を **HTTP 400 `unsupported_content_type`**（PROBE-2・§5:12）で弾く。fork（[`deploy/hermes/er-audio-fork/`](../../deploy/hermes/er-audio-fork/)・#357）配備時のみ 200。
- **fallback（凍結）**: `hermes` を選択したが transport が失敗（audio で unforked 400 / gateway 不通 / 非 200）した場合、**`direct` に fail-safe fallback**（audio/image とも direct は一次経路・§5:8,14）。**shipped default の audio は依然 `direct`**（恒久 fallback・doc06:269）。両 transport は同一 `RoboticsPlanDraft` に正規化される（README:86・L3 Handoff の transport 等価性で担保）。

> **transport の分岐スコープ（reconcile）**: `transport` は本節（L4）の **request 組み立て / fail-safe fallback のキー**にはなる — 同一 box の interface 裏で `hermes|direct|worker` を選ぶのは実装選択（[`../productization/01-commercial-box-map.md`](../productization/01-commercial-box-map.md):52）。一方 `Transport` enum docstring（`adapters/enums.py`・`transport.py`・doc03:75）の「**NEVER an execution-branch key**」は **DOWNSTREAM の L3 policy 判断 / L2 safety gate** を指す（safety-gate box の transport は `n/a`・[`01`](../productization/01-commercial-box-map.md):53）。よって L4 の request assembly で transport をキーにするのはこの禁止と矛盾しない（前者＝L4 wire、後者＝L3/L2 gate）。

> 実装: `robotics/adapters/gemini_er.py` `propose_plan` が本節の凍結形で live 送信する（別 PR）。**robotics-grade command 品質 eval と実 gateway 配備・有料 live verify は human gate**（doc04:28 / environments.md `.env` 承認）。

---

## 【2026-08-09 追補】`overhead_image_ref` は改名せず意味を再定義（ADR-0007）

俯瞰カメラ不使用により、`ErTaskRequest.overhead_image_ref`（:43・:104）の実体は**搭載 HP60C のカメラフレーム参照**となる。**フィールド名は改名しない**（`er_task.py`・adapter・test への波及回避）。「Adapter は俯瞰画像を受ける」（:11）は「搭載カメラフレームを受ける」と読み替える。

## 【2026-09-16 追補】共通 instruction text に pixel 座標契約を追加（#699・末尾追記＝行参照非破壊）

[:104](03-er-adapter-skeleton.md:104) の「共通: instruction text = schema 制約（robots / action / target / 禁止項目）＋ transcript」は `detections[].pixel` の座標系に沈黙していた。production adapter `_SCHEMA`（`robotics/adapters/gemini_er.py`）はこれを忠実に写して pixel 節を持たず、live helper（`tests/live/_er_live_client.py`）は「`[u,v]` 0–1000」を独自に足していた。以下を**共通 instruction text の一部として追補**する（[提案]・bridge-local・`RoboticsPlan draft` は未凍結 = [06 §1](06-unfrozen-contract-resolutions.md)）。

- **pixel 節（逐語）**: `detections[].pixel is [u, v]: u = horizontal position from the LEFT edge, v = vertical position from the TOP edge, both normalized to 0-1000 of the provided image (this is NOT the [y, x] order of point outputs); use [0, 0] if unknown.`
- **根拠**: Gemini Robotics-ER の native pointing は `[y, x]` 0–1000 正規化（<https://ai.google.dev/gemini-api/docs/robotics-spatial>・参照日 2026-09-16 [D]・ER 2 でも不変）。draft schema の `pixel` は (u, v) = (x, y) 順（[02:138](02-l3-planning-core.md:138)）なので、**順序を逐語で指定**して軸入れ替えを防ぐ。正規化 0–1000 は model が確実に出せる尺度（raw px を要求すると画像解像度を model が知らないため信頼できない）。
- **単一ソース**: 上記文字列は `gemini_er.py` の `PIXEL_RULE` 定数 1 か所。`_SCHEMA` と live helper 2 本（`_er_live_client.py` / `test_er_handoff_live.py`）は同定数を import（`tests/unit/test_er_pixel_rule_single_source.py` で pin）。
- **消費側との整合**: L3 Visual Resolver は `Detection.pixel` を **calibration artifact の pixel 空間**で消費する（[02 2026-09-16 追補](02-l3-planning-core.md)）。ER の正規化値 → artifact 空間の変換は **artifact の `pixel_space: normalized_0_1000` + `image_size` 宣言を Visual Resolver が homography 直前で適用**する（[02 2026-09-16 追補 ②](02-l3-planning-core.md)・#699 slice 2）。宣言の無い（`raw`）artifact では正規化値がそのまま掛かるため、ER 経路の artifact は必ず宣言する。
- 禁止項目（URL / topic / endpoint / velocity / motor / coordinate goal）は不変。request の part 構成（[:107](03-er-adapter-skeleton.md:107) / [:109](03-er-adapter-skeleton.md:109)）も不変。
