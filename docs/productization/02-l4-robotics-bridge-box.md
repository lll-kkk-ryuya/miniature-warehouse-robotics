# L4 Robotics Bridge Super-Box

作成日: 2026-06-22

> **状態**: 設計提案。L4 Robotics Bridge Super-Box は既存 `warehouse_llm_bridge` を商用再利用の上位 orchestration box として再定義する。新しい config key、ROS topic、REST API、`warehouse_interfaces` frozen contract は追加しない。

## 結論

LLM Bridge は商用 box に含める。Mode X-ER / Mode X-ER-VLA では、これを **Robotics Bridge Super-Box** として拡張する。

理由:

- 既存採用実装は Bridge 仲介 dispatch であり、Bridge が `action_map` により MCP tool call へ変換し、`gen_id` / `idempotency_key` を注入する。
- Langfuse trace と run id は model adapter ではなく Bridge が所有する方が、判断と実行結果を 1 本の trace に結合しやすい。
- ER / VLA を直接 MCP、Nav2、ROS、Jetson、ESP32 へ接続させないためには、Bridge が model 呼び出しと L3 接続の両方を管理する必要がある。

### L4 見直し判断（2026-06-23）

Mode X-ER の L4 は、単なる Gemini Robotics-ER adapter ではなく、
**Robotics Bridge Super-Box + Hermes-managed transport area + optional direct adapter**
として扱うべきである。

理由:

- Nous Research 公式 docs 上、Hermes Agent は OpenAI 互換 API Server、MCP 接続、provider routing / fallback、vision input、voice / TTS、plugin、memory / skills を持つ。
- これらは L4 の provider / transport / generic tool integration を担えるため、商用再利用 box では Hermes-managed area として切り出す価値がある。
- 一方で、robotics 固有の input context、L3 handoff、`action_map`、`gen_id` / `idempotency_key` 注入、0 dispatch safety、trace / Eval join は Hermes に移さず Bridge-owned に残す必要がある。
- したがって「Hermes で担う部分が増える」は正しいが、それは **robot motion の所有権を Hermes に移す**という意味ではない。

実装候補は、既存 `warehouse_llm_bridge` 内に L4 sub-box を増やし、Hermes は transport / control-plane / generic integration として使う形である。Hermes plugin に寄せる場合も、motion tool の採用経路は Bridge-mediated dispatch のままにする。

### Hermes-first transport 方針（2026-06-24）

Mode X-ER / Mode X-ER-VLA の L4 実装では、model / modality / MCP 接続が
Hermes Agent の公式機能で扱える限り **`transport: hermes` を既定（default）**にする（第一候補でなく default・末尾補足参照）。
direct adapter / worker は、Hermes が対象 API、audio / image modality、GPU runtime、
response shape、または latency 要件を満たせない場合の fallback として扱う。

Hermes-first に寄せる対象:

- LLM / ER / STT / Vision の model transport。
- provider routing / fallback。ただし Phase 4 の比較 run では provider 固定を優先し、
  fallback を使う場合は比較条件として別 leg に分ける。
- MCP 接続と tool include / exclude。robot motion tool の採用判定は引き続き
  Bridge / Governance 側で行う。

Hermes-first でも Bridge-owned に残す対象:

- input bundle の最終 manifest と stale / missing / secret 混入判定。
- request id、cycle、timeout 後の 0 dispatch。
- L3 handoff、`action_map`、`gen_id` / `idempotency_key` 注入。
- motion tool の accepted path、Policy Gate、Eval join。
- Langfuse root trace ownership。これは下記の Langfuse plugin spike gate を満たすまで
  Bridge-owned を採用する。

## L4 の責務

```
operator voice / text / WMS task
  -> Input Context Builder
  -> Robotics Bridge Super-Box
     -> Trace start
     -> ER / VLA / STT Adapter call
     -> raw output audit
     -> L3 Planning Core
     -> Command candidate
     -> existing action_map / MCP / Policy Gate
```

L4 が持つもの:

- cycle / request id / timeout / cancellation
- State Cache snapshot の取得
- audio / transcript / image / calibration id の bundle 化
- model adapter registry
- Hermes 経路または direct adapter 経路の選択
- Langfuse trace / observation / metadata
- raw output と L3 result の audit
- L3 への入力 contract

L4 が持たないもの:

- Nav2 action の直接発行
- ROS topic publish
- MCP tool の server-side 即時実行
- `gen_id` / `idempotency_key` を model に作らせること
- velocity / trajectory / motor command の採用

## Box の保管場所

ER / VLA / STT は、商用資産として **L4 Robotics Bridge Super-Box の adapter registry 配下に保管する**。

```
L4 Robotics Bridge Super-Box
  -> context builders
  -> adapter registry
     -> gemini_er adapter
     -> vla adapter
     -> stt adapter
  -> tracing / Langfuse observation policy
  -> raw output audit
  -> L3 handoff
```

ここでいう「Hermes Bridge 内に保管」は、Hermes server runtime に model 本体や VLA runtime を入れるという意味ではない。Hermes は対応 model への transport 候補であり、実際の model が外部 API、GPU worker、別 process のどれで動いてもよい。商用 box として保存する対象は、呼び出し interface、request / response template、fixture、trace policy、audit policy、L3 handoff contract である。

この置き方にすると、LLM / ER / VLA を同じ L4 観測単位で扱える。

```
model_call observation
  provider_type: llm | er | vla | stt
  transport: hermes | direct | worker
  input_refs: audio/image/state/calibration
  output_ref: raw_model_output
  handoff: robotics_plan_draft | grounding_report | action_candidate
```

## Hermes Agent に寄せる範囲

Hermes Agent は、Robotics Bridge Super-Box の transport / provider / generic tool integration を支える。2026-06-23 時点で Nous Research 公式 docs から確認できる範囲では、以下を Hermes-managed area に置ける。

> **読み方**: 下表は **box の境界ではない**。各行は「Hermes-managed＝`transport: hermes` の実装」と「Bridge-owned＝所有する box / seam」へ分解した実装ノートである（taxonomy 正本は `01` §Box 種別と分類規則）。左列の `（種別）` が確定分類で、`Sub-Box` 列名は誤解を招くため改めた。Hermes-vs-direct は box interface 裏の `transport` 選択であって、箱を Hermes 列で割らない。

| 機能（確定種別） | Hermes-managed（`transport: hermes` 実装） | Bridge-owned（所有 box / seam） |
|---|---|---|
| API Server / Gateway（demoted → Super-Box cycle） | OpenAI 互換 `/v1/chat/completions` / `/v1/responses`、inline image input、health / capabilities、runs API | robotics cycle、request id、timeout 後の 0 dispatch、run stop を安全担保にしない判断、final output の採用判定 |
| Model Transport（demoted → Model Adapter transport） | provider 切替、custom endpoint、OpenAI 互換 endpoint、provider fallback | robotics request id、Bridge-owned Langfuse trace、L3 handoff、transport 失敗時の fail-open / 0 dispatch policy |
| Provider Routing（plugin → Model Adapter） | OpenRouter 経由の routing（OpenRouter 内の sub-provider 選択）、別機能の fallback chain（provider 跨ぎ） | 比較 run の provider 固定、mode tag、trace metadata、比較公平性 guard |
| STT（sub-box → Model Adapter `provider_type:stt`） | Local Whisper、Groq/OpenAI Whisper、custom command provider、Python plugin provider | transcript を state snapshot / image / calibration / known locations と束ねる context builder（Input Context） |
| Basic Vision（demoted → Model Adapter transport + Input Context） | vision-capable model への image input、汎用 vision analysis | camera calibration、map frame、object-to-location resolution、L3 Visual Resolver への入力 |
| MCP Connection（seam → MCP dispatch / Governance） | stdio / HTTP MCP、OAuth、tool include/exclude、resources/prompts wrapper 制御 | motion tool の accepted path、Policy Gate の reject 理由、Bridge mint の `idempotency_key` |
| Plugin Extension（plugin・Hermes 拡張機構） | custom tool、hook、model provider、STT/TTS、MCP 連携の plugin 化 | robotics safety policy、ER/VLA contract、L3 handoff、Audit/Eval join |
| Memory / Skills / Session Search（demoted・operator path） | Mode A 演出や operator-facing workflow の記憶・手順化 | Phase 4 比較 run の強制 OFF、交通制御 skill 禁止、Hermes config 側 OFF と Bridge intent guard |

Hermes Agent に寄せないもの:

- ER/VLA disagreement と confidence fusion
- `RoboticsPlan draft` / `VlaGroundingReport` から L3 input への正規化
- `Command` から MCP tool call への `action_map`
- `gen_id` / `idempotency_key` の発行と注入
- timeout / validation failure 時の 0 dispatch 保証
- Langfuse root trace と Eval score の join policy

この分担により、Hermes は provider / tool transport の実装差分を吸収し、Robotics Bridge Super-Box は robotics 固有の安全境界を維持する。

## Hermes 経由か direct adapter か

原則は **Bridge managed adapter** である。adapter の transport は二択にできる。

| 経路 | 使う条件 | 守るべきこと |
|---|---|---|
| Hermes 経由 | **既定（default）**。Hermes が対象 model、OpenAI 互換 request、image input、voice/STT/TTS provider、provider fallback を扱える | server-side motion tool execution を使わず、Bridge が final output を受けて L3 へ渡す。比較 run では provider 固定 / fallback 条件を trace metadata に残す |
| Direct adapter / worker | **explicit fallback**。ER/VLA runtime、GPU、audio/image API、latency 要件、response shape が Hermes 経由に合わない | trace、timeout、audit、L3 接続、secret 管理を Bridge 側に残す。Hermes と同じ L3 Handoff input を返す fixture を必須にする |

重要なのは、Hermes か direct かではなく、**Bridge が orchestration owner であり続けること**である。

## Langfuse の置き場所

Langfuse 統合は L4 Robotics Bridge Super-Box に置く。

Model Adapter は provider call の観測 span を追加できるが、trace の root ownership、run id、mode tag、provider tag、L3 result、MCP accepted/rejected、Eval score との join は Bridge / Eval 側が持つ。

```
Robotics Bridge trace
  ├─ input_context observation
  ├─ model_call observation
  │   ├─ gemini_er
  │   └─ vla optional
  ├─ l3_validation observation
  ├─ command_compile observation
  ├─ mcp_dispatch observation
  └─ eval score link
```

この形にすると、商用案件ごとに provider を変えても trace の見方が変わらない。

### Hermes Langfuse plugin の再評価 gate

現採用は **Opt C**（Bridge-owned now / Hermes-owned は HLF gate 後・末尾補足参照）。Bridge-owned trace（`langfuse.openai` + `base_url=Hermes`）である。これは
`run_id:gen_id` から決定的な `trace_id` を作り、Bridge の model generation、MCP span、
Warehouse Orchestrator の score を 1 本に結合するためである。Hermes 側 Langfuse plugin
を同時に有効化すると、同じ model call が Bridge wrapper と Hermes plugin の両方で
generation として記録され、token / cost / latency / error count が二重計上される可能性がある。

ただし、Hermes plugin を使う方針を恒久的に否定しない。以下の live spike gate をすべて
満たせる場合のみ、`trace_owner: hermes` への切替を再検討する。

| Gate | 確認内容 | 採用条件 |
|---|---|---|
| HLF-G0 trace id passthrough | Hermes plugin が外部指定 `trace_id` または同等の correlation id を尊重できるか | `run_id:gen_id` から #4 / #6 が同じ trace に到達できる |
| HLF-G1 metadata | `gen_id` / `run_id` / `provider` / `mode` / `env` / prompt 情報を確実に trace metadata / tags に載せられるか | Phase 4 比較と Eval join に必要な軸が欠落しない |
| HLF-G2 score join | Bridge 外の Warehouse Orchestrator が同じ trace に `create_score` できるか | score が orphan にならず同一 trace に表示される |
| HLF-G3 span shape | MCP tool span と model generation が同じ trace に入り、accepted / rejected / error を区別できるか | L4 -> L2 の decision funnel を Langfuse 上で追える |
| HLF-G4 fail-open | Hermes plugin / Langfuse sink 障害時も robot 制御が 0 dispatch または既存 fail-open 方針で継続するか | observability failure が motion path を止めない |
| HLF-G5 no double generation | Bridge wrapper と Hermes plugin を併用せず、generation が一度だけ記録される構成にできるか | token / cost / latency / error count が二重計上されない |

HLF-G0〜G5 は Hermes と Langfuse の実サービスが必要な human-gated live 検証である。
offline docs / unit だけでは採用判断しない。切替する場合は、Bridge の `langfuse.openai`
wrapper を無効化するか、Hermes plugin を無効化するかのどちらか一方に統一する。

## ER / VLA adapter の扱い

### ER adapter

ER adapter は、音声、transcript、俯瞰画像、state、calibration metadata を受け取り、`RoboticsPlan draft` を返す。

ER adapter に渡してよいもの:

- instruction audio ref
- transcript
- overhead image ref
- state snapshot ref
- calibration id
- known robots
- known locations
- allowed actions
- output contract name

ER adapter に渡さないもの:

- Nav2 Bridge URL
- ROS topic
- Jetson service endpoint
- MCP internal tool name
- `/cmd_vel`
- ESP32 / motor command

### VLA adapter

VLA adapter は、Mode X-ER-VLA の optional box として扱う。

初期導入は以下の順に進める。

1. Sim / offline fixture で ER+VLA output を観察する。
2. VLA を grounding / confidence cross-check に使う。
3. VLA が action candidate を安定して出せる場合のみ、L3 の一部代行を検討する。

VLA output は直接実行しない。必ず Fusion Validator / Safety Compiler / L3 Planning Core を通す。

## 推奨 module 構成案

現時点ではコード追加しないが、商用化時の保管単位は以下が自然である。

```text
robotics_bridge/
  context/
    input_context_builder.py
    media_refs.py
  adapters/
    base.py
    gemini_er.py
    vla.py
    stt.py
  tracing/
    trace_context.py
    langfuse_observer.py
  audit/
    raw_model_output_store.py
  orchestration/
    robotics_bridge.py
    timeout_policy.py
```

**実体対応（成熟度）**: `context/`＝Input Context sub-box（実装あり: `situation.py` の `SituationBuilder`）/ `tracing/`＝Bridge-owned trace root（実装あり: `tracing.py`）/ `orchestration/`＝Super-Box cycle・0 dispatch（実装あり: `scheduler.py`）+ MCP dispatch seam（実装あり: `executor.py` の `DispatchToolExecutor`→`tools.dispatch`）+ action_map seam（実装あり: `action_map.py`、`gen_id`/`idempotency_key` mint）。`adapters/`（gemini_er / vla / stt）と `audit/` は **未実装案＝proposal**（ws/src に実体なし）。`transport: hermes|direct|worker`（§Box の保管場所の `model_call observation`）は docs 例示で、コード enum は未実装＝未凍結。

既存 `warehouse_llm_bridge` からいきなり分離しない。まず同 repo 内で adapter seam と fixture を固め、利用者 2 件目が出たら product package へ分離する。

## Acceptance Gates

| Gate | 内容 |
|---|---|
| L4-G0 | offline fixture で input bundle を再現できる |
| L4-G1 | ER adapter raw output を保存できる |
| L4-G2 | Langfuse trace に input/model/L3/MCP の観測が残る |
| L4-G3 | timeout 時に 0 dispatch で終わる |
| L4-G4 | Hermes 経由 / direct adapter の両方で同じ L3 interface に渡せる |
| L4-G5 | ER/VLA に ROS / Nav2 / MCP internal endpoint を渡していないことを fixture で検査する |

## 未凍結事項

- Hermes が Gemini Robotics-ER の audio / image API を扱えるか（→ 末尾「2026-06-27 補足」の「解決状況」で解決済: image/text=200・audio=unforked 400＋2-file fork 200 実証だが未 ship）。
- OpenVLA runtime を Bridge process 内に置くか、別 process / GPU worker にするか。
- `RoboticsPlan draft` を `warehouse_interfaces` に昇格するか。
- Langfuse trace taxonomy に Mode X-ER / Mode X-ER-VLA 固有 tag を追加するか。

## 参考URL

Nous Research / Hermes Agent 公式 docs。参照日: 2026-06-23。

- [Hermes Agent Documentation](https://hermes-agent.nousresearch.com/docs/)
- [API Server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server)
- [AI Providers](https://hermes-agent.nousresearch.com/docs/integrations/providers)
- [MCP (Model Context Protocol)](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)
- [Provider Routing](https://hermes-agent.nousresearch.com/docs/user-guide/features/provider-routing)
- [Fallback Providers](https://hermes-agent.nousresearch.com/docs/user-guide/features/fallback-providers)
- [Vision & Image Paste](https://hermes-agent.nousresearch.com/docs/user-guide/features/vision)
- [Voice & TTS](https://hermes-agent.nousresearch.com/docs/user-guide/features/tts)
- [Plugins](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins)
- [Persistent Memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory)
- [Skills System](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)
- [Configuration Guide](https://hermes-agent.nousresearch.com/docs/user-guide/configuration)

## 2026-06-27 補足 — Hermes transport=default / audio fork / Langfuse Opt C（末尾追記・行参照非破壊）

> 上の各節（§Hermes-first transport 方針・§Hermes Langfuse plugin の再評価 gate〔HLF-G0〜G5＝`02:177-199`〕・§未凍結事項）の行参照を動かさないため末尾に追記する（#165）。

**Findings 凡例（F1–F6・この L4 transport doc 群に閉じたローカル記号）**: **F1**=transport 既定（`provider_type ∈ {llm,er,vla,stt}` → `hermes` default・`direct`/`worker` は explicit fallback）／**F2**=PROBE 実測（unforked Hermes audio=`400`・image/text=`200`）／**F3**=2-file fork demonstration（audio `200`・demonstrated だが未 ship）／**F4**=CURRENT（audio=direct）と TARGET（fork ship 後に Hermes 既定）の切り分け／**F5**=provider routing（Hermes は server-side 単一 active model → per-provider gateway）／**F6**=Langfuse Opt C（CURRENT=Bridge-owned trace・FUTURE=Hermes-owned は HLF-G0〜G5 後）。**[`docs/jetson/01-fidelity-and-validation.md`](../jetson/01-fidelity-and-validation.md):52-57 の fidelity tier `F1–F6`（ROS 論理／config overlay／2台 Gazebo E2E／GPU・CUDA／実時間性 jitter／micro-ROS WiFi UDP）とは別体系**＝記号は重なるが意味は無関係。

**transport（F1）**: `provider_type ∈ {llm, er, vla, stt}` の既定（default）= `hermes`。`direct`/`worker` は Hermes が modality/runtime/response/latency を満たせない（または下記 audio fork 未デプロイ）時の **explicit fallback**。

**provider 選択の現実（F5）**: Hermes は server-side 単一 active model で per-request の provider 選択フィールドが無い。4-provider 比較（Mode A/B/C）は **per-provider gateway**（config + restart）で実現する。比較 run の「provider 固定」はこの per-provider gateway 起動を指す。

**audio carve-out（measured 2026-06-27）**: text/image は Hermes 既定で成立（commander LLM=Hermes shipped〔`hermes_client.py`〕・ER image/text=PROBE-3 で HTTP 200）。**unforked Hermes は `/v1/chat/completions` の `input_audio` で HTTP 400 `unsupported_content_type`**（PROBE-2 実測）。よって audio は CURRENT/TARGET を分ける — **TARGET**=全 modality を Hermes 既定（audio は fork 経由）/ **CURRENT（running state）**=audio は `transport: direct`（fork が productionize/ship されるまで・かつ恒久 fallback）。**「audio が現在 Hermes に default する」とは書かない**（target であって shipped でない）。

**fork demonstration（F3）**: hermes-agent v0.15.1 の 2-file fork — patch1 `gateway/platforms/api_server.py`（`_normalize_multimodal_content` が `input_audio` 受理＋`_content_has_visible_payload`＋新 `_AUDIO_PART_TYPES`）/ patch2 `agent/gemini_native_adapter.py`（`_extract_multimodal_parts` が `input_audio → inlineData{mimeType:audio/wav}`）。**LIVE**: HTTP 200・ER が native audio 理解・lean latency 中央値 3.69s vs direct 4.24s（n=4・ER-thinking 交絡）・+~408 prompt tok/call。**demonstrated だが未 ship**＝default-Hermes-audio は fork の productionize が前提。

**Langfuse（F6・Opt C）**: §「Hermes Langfuse plugin の再評価 gate」の置き方は **Opt C**。**CURRENT = Bridge-owned trace**（`create_trace_id(seed=run_id:gen_id)`・managed-prompt link・4-provider 単一 codepath fairness・double-count 回避で Hermes plugin disabled）。**FUTURE TARGET = Hermes-owned plugin** だが **HLF-G0〜G5（`02:177-199` の gate 表）をすべて満たした場合に限り** `trace_owner: hermes` へ切替（[`architecture/13`](../architecture/13-hermes-setup.md):551-570）。**flip しても outcome score（result/SR/SPL/collision/deadlock）は eval_sdk 所有のまま**（Hermes は robot result を見ない・score join は Bridge/Eval 側・HLF-G2 が「Bridge 外 emitter が同一 trace に `create_score` できるか」を確認するのはこのため）。

**未凍結事項「Hermes が ER の audio/image API を扱えるか」の解決状況**: image/text は **解決済**（PROBE-3 200・F2）/ audio は **unforked 400**（PROBE-2・F2）＋**2-file fork で 200 実証済だが未 ship**（F3）。残課題＝**fork の productionize**（default-Hermes-audio の前提）・それまで current は direct（F4）。

**実装 pointer（#388・main / F1・F4 の realize）**: 上記 F1/F4（audio=HERMES-iff-forked, 他は恒久 direct）の wire 判定は bridge-local `resolve_audio_transport`（[`../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/transport.py`](../../ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/transport.py):29）が realize する＝`base_url` 非空 **かつ** `audio_input_audio_supported is True` のみ `Transport.HERMES`、他は fail-safe `Transport.DIRECT`（`transport.py:56-58`）。`Transport`/`ProviderType` は observation-only audit tag として bridge-local `adapters/enums.py:23,32` に着地（§「Hermes 経由か direct adapter か」の transport 選択＝箱境界でなく wire 選択、の実装）。**live 送信 seam**（`gemini_er.propose_plan` の live path）は #344 で defer＝**pending #344/#389（main 未マージ）**。運用手順 = operator runbook [`../dev/07-mode-x-er-live-e2e-runbook.md`](../dev/07-mode-x-er-live-e2e-runbook.md)。
