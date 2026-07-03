# ER-in-Hermes を標準 transport として採用する（fork gateway 8644 一本で全 modality / direct=緊急 fallback / Langfuse Pattern A 現行・Pattern B は gate 後）

**Status**: accepted（**決定**）／実装は **TARGET**（下記「状態（TARGET / CURRENT）」参照＝wire・fork ship 未着地）

Mode X-ER（Gemini Robotics-ER 司令塔）の L4 transport を、**ER-in-Hermes（全 modality を 1 本の fork gateway で Hermes 経由に運ぶ）を標準**とし、`direct` を緊急 fail-safe に格下げし、Langfuse は Bridge 所有 trace（Pattern A）を現行標準・Hermes plugin 所有（Pattern B）は gate 後 follow-up とする、という設計方針（DESIGN）を決めた。**本 ADR は方針＝TARGET であって現稼働ではない**（audio の shipped reality は依然 `direct`。wire=#389 と fork ship が main 着地するまで CURRENT のまま）。

## Context / 背景

- unforked Hermes の OpenAI 互換 API server は `input_audio` content part を透過せず **HTTP 400 `unsupported_content_type`** を返す（PROBE-2 実測。[`../mode-x-er/06-unfrozen-contract-resolutions.md`](../mode-x-er/06-unfrozen-contract-resolutions.md):159）。このため ER の audio leg は `direct` ER に固定され、text/image は lean Hermes(8643) 経由・audio は direct と、**observation が経路分断**していた（audio direct leg は Hermes plugin が観測できない。[`../mode-x-er/07-implementation-status.md`](../mode-x-er/07-implementation-status.md):20）。
- 2026-06-27、hermes-agent v0.15.1 の **2-file transport-only fork**（`input_audio` を受理し Gemini native `inlineData{mimeType:"audio/wav"}` に map）で **native audio が Hermes を通ること**を live 実証（`/v1/chat/completions` に `input_audio` POST → **HTTP 200**・ER が音声中にのみ存在する語の transcript を返却＝STT 経由でなく native）。fork は input_audio を**足すだけ**で orchestration / safety / text / image は不変（[`../../deploy/hermes/er-audio-fork/run-er-gateway.sh`](../../deploy/hermes/er-audio-fork/run-er-gateway.sh):3-4,23-34 / [`../mode-x-er/06-unfrozen-contract-resolutions.md`](../mode-x-er/06-unfrozen-contract-resolutions.md):267-269）。fork productionization = issue #357、plugin 所有 Langfuse trace（Option-D）scaffolding = #360。

## Evidence（2026-07-03 live 実測・operator 承認・課金）

2026-07-03 に audio-in-Hermes を live 計測し、本決定を裏づけた（同一 TTS 音声で unforked / forked / direct を比較）:

- **unforked Hermes + `input_audio` → HTTP 400 `unsupported_content_type`**（**0.003s**・ER 到達前に reject＝無課金）。→ **audio-through-Hermes には fork が必須**であることを live 再確認（PROBE-2 と一致。[`../mode-x-er/06-unfrozen-contract-resolutions.md`](../mode-x-er/06-unfrozen-contract-resolutions.md):159 は不変）。
- **forked gateway + `input_audio` → HTTP 200**: ER が音声指示を理解し**正しい 2-task plan**（「bot1→赤箱／bot1 退出後 bot2→青箱」・**611 tok**）を返却。→ **audio-in-Hermes（fork 経由）は ER 呼び出しまで end-to-end で実働**する（demonstrated が確定）。
- **latency（n=1・同一音声）**: fork-Hermes **5.49s** vs direct **4.61s**＝comparable。ER 推論（~4-5s・thoughts-token 支配）が支配的で transport overhead は小。先行 docs の n=4（fork 3.69s / direct 4.24s。[`../../deploy/hermes/er-audio-fork/run-er-gateway.sh`](../../deploy/hermes/er-audio-fork/run-er-gateway.sh):30-31）とは**順序が逆＝noise 内**（どちらが速いかは確定しない）。
- **決定への含意**: audio-in-Hermes は **fork 必須 かつ 実測で稼働** → **fork を維持して標準に採用する**（「fork をやめる／drop する」方向ではない。Decision #1・#5 と一致）。
- **運用 finding（Lane C #401 で解消予定）**: fork / unforked launcher が現状 `HERMES_HOME` / `.env` / port を共有し**衝突**する（同時起動不可）＝分離が必要。修正は #401。
- **honest scope**: この live は**手動 probe**であって shipped pipeline ではない。CURRENT（shipped）audio は依然 `direct`（wire=#389・fork ship 未着地）＝本 ADR は **TARGET** のまま。

## Decision / 決定（5点）

1. **ER-in-Hermes を標準 transport とする**。**1 本の fork gateway（port 8644）が全 modality（text + image_url + input_audio）を担う**。fork は `input_audio` を追加するだけで text/image を保持するため、従来の lean text/image gateway（8643）は 8644 に**統合し retire** する。
2. **`direct` = 緊急 fail-safe 専用に格下げ**。send 時の HERMES→DIRECT fallback（`_live_send`。[`../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/adapters/gemini_er.py`](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/adapters/gemini_er.py):222-243）と、config 既定の恒久 fallback（`er_gateway` 両キー OFF＝direct。[`../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/transport.py`](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/transport.py):29-36 / [`../../config/warehouse.base.yaml`](../../config/warehouse.base.yaml):77-80）を**維持**する。
3. **Langfuse は Pattern A（Bridge 所有 trace）を現行標準**とする。**Pattern B（Hermes plugin 所有・Bridge `langfuse.openai` wrapper 除去）は HLF-G0〜G5 gate を全て PASS した後の follow-up**（[`../productization/02-l4-robotics-bridge-box.md`](../productization/02-l4-robotics-bridge-box.md):177-199）。Pattern A ＝ productization/02 の **Opt C の「Bridge-owned now」**、Pattern B ＝ その「Hermes-owned は HLF gate 後」（`trace_owner: hermes`）に対応する（owner 解決 seam＝[`../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/hermes_client.py`](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/hermes_client.py):97-119、既定 `bridge`・fail-safe）。
4. **audio format は wav 優先**、後で拡張する（fork の現 map 先＝`audio/wav`）。
5. **fork メンテ負債を現時点で受容**し、upstream PR は後で出す。

## Why / なぜ

- **observation の一元化**：全 modality が Hermes を通れば Langfuse trace が 1 経路に集まり、audio が別経路（direct）で trace から漏れる分断を解消できる。
- **wire が 1 本**：transport 選択が単純化する。どの transport でも L3 Handoff に渡る input shape は同一で、L3 以降は transport 非依存（[`../mode-x-er/01-architecture-and-flow.md`](../mode-x-er/01-architecture-and-flow.md):167）。
- **Hermes plugin が auto-trace できる**（Pattern B＝gate 後の前提）。

## トレードオフ / Trade-offs（なぜこの形か）

- **fork メンテ負債 vs upstream PR**：fork は hermes-agent の upstream 追従コストを負う（当面受容・後で upstream 化）。
- **単一 server-side active model**：Hermes は per-request の provider routing をしない。Mode A/B/C の 4-provider 比較は request field ではなく **per-provider gateway**（config + restart）で切替える（F5。[`../../deploy/hermes/er-audio-fork/run-er-gateway.sh`](../../deploy/hermes/er-audio-fork/run-er-gateway.sh):32-34）。
- **native-google-provider 限定**：ER 用 gateway は Gemini native provider のみ（`model.provider:"google"`）。
- **Pattern B は managed-prompt link を失いうる**：Bridge `langfuse.openai` wrapper を除去すると、Bridge 側で結合していた model generation / MCP span / Orchestrator score の単一 trace 化を Hermes plugin 側で満たす必要がある（HLF-G0〜G3 が確認軸。gate 未通過なら Pattern A 維持）。
- **TARGET までは CURRENT が shipped**：wire（live-send seam＝#389・main 未マージ）と fork ship（8644 を default 配備）が main 着地するまで、**audio は `direct` が shipped**（fork は demonstrated だが未 productionize）。本 ADR は「そこへ向かう標準方針」であり現稼働の記述ではない。

## Considered Options / 却下・保留した案

- **two-path を恒久維持**（audio=direct 固定 / text+image=Hermes）：observation 分断が残るため標準としては却下。ただし fork productionize 前の **CURRENT はこの two-path**（[`../mode-x-er/06-unfrozen-contract-resolutions.md`](../mode-x-er/06-unfrozen-contract-resolutions.md):162）。
- **Pattern B を今すぐ採用**：Bridge wrapper と Hermes plugin の併用で generation が二重計上され token/cost/latency/error が二重になるリスク（HLF-G5）と、HLF gate が human-gated live 未通過のため、**gate 後に延期**（[`../productization/02-l4-robotics-bridge-box.md`](../productization/02-l4-robotics-bridge-box.md):182-199）。

## 状態（TARGET / CURRENT）と帰結 / Consequences

- **CURRENT（shipped reality・2026-07-03）**：ER audio leg = `direct`（unforked Hermes は input_audio に 400）。text/image は lean Hermes(8643) 経由も可。transport の「選択」＋ offline L3 全チェーンは実装済み、「実行」（live-send）は #389（main 未着地）＝ live path は `NotImplementedError`（[`../mode-x-er/07-implementation-status.md`](../mode-x-er/07-implementation-status.md):14,22）。
- **TARGET（本 ADR の標準）**：fork gateway 8644 一本で全 modality／`direct`=緊急 fallback／Langfuse Pattern A 現行・Pattern B は HLF gate 後。
- wire（Lane B）と fork ship（Lane C）が main 着地したら、CURRENT→standard の移行を [`../mode-x-er/07-implementation-status.md`](../mode-x-er/07-implementation-status.md) / [`STATUS`](../STATUS.md) に反映し、本 ADR の TARGET を解消する。
- 本 ADR は transport 正本索引（[`../mode-x-er/README.md`](../mode-x-er/README.md) Transport index）・[`GLOSSARY`](../GLOSSARY.md) §7・[`06 §5 補遺`](../mode-x-er/06-unfrozen-contract-resolutions.md)・[`01`](../mode-x-er/01-architecture-and-flow.md) 末尾補足・[`environments.md`](../../.claude/rules/environments.md):26 から back-link される。

## References（`origin/main` で検証済み file:line）

- 正本 doc: [`mode-x-er/06-unfrozen-contract-resolutions.md`](../mode-x-er/06-unfrozen-contract-resolutions.md)（§5 PROBE-1/2/3・:159 400・:162 two-path・:263-271 fork 補遺）/ [`mode-x-er/07-implementation-status.md`](../mode-x-er/07-implementation-status.md):20 / [`productization/02-l4-robotics-bridge-box.md`](../productization/02-l4-robotics-bridge-box.md):177-199（HLF gate）
- code seam（凍結・検証側）: [`robotics/transport.py`](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/transport.py):29（`resolve_audio_transport`）/ [`robotics/adapters/gemini_er.py`](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/adapters/gemini_er.py):222-243（HERMES→DIRECT fail-safe）/ [`hermes_client.py`](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/hermes_client.py):97-119（Langfuse owner）/ [`config/warehouse.base.yaml`](../../config/warehouse.base.yaml):77-80（`er_gateway`）
- fork 成果物: [`deploy/hermes/er-audio-fork/run-er-gateway.sh`](../../deploy/hermes/er-audio-fork/run-er-gateway.sh):3-4,30-36（LIVE 200・8644）
- ルール / format: [`ADR-FORMAT`](../../.claude/skills/domain-modeling/ADR-FORMAT.md) / [`docs-first.md`](../../.claude/rules/docs-first.md) / retrospectives = [`docs/dev/03-retrospectives.md`](../dev/03-retrospectives.md)
- Issue: 標準化 = #336 / fork productionization = #357 / Option-D Langfuse trace-owner = #360 / live-send seam = #344・#389
