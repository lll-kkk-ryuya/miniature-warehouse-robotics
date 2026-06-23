# L4 Robotics Bridge Super-Box

作成日: 2026-06-22

> **状態**: 設計提案。L4 Robotics Bridge Super-Box は既存 `warehouse_llm_bridge` を商用再利用の上位 orchestration box として再定義する。新しい ROS topic、REST API、`warehouse_interfaces` contract は追加しない。

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

| Sub-Box | Hermes-managed にできる内容 | Bridge-owned に残す内容 |
|---|---|---|
| API Server / Gateway | OpenAI 互換 `/v1/chat/completions` / `/v1/responses`、inline image input、health / capabilities、runs API | robotics cycle、request id、timeout 後の 0 dispatch、run stop を安全担保にしない判断、final output の採用判定 |
| Model Transport | provider 切替、custom endpoint、OpenAI 互換 endpoint、provider fallback | robotics request id、Bridge-owned Langfuse trace、L3 handoff、transport 失敗時の fail-open / 0 dispatch policy |
| Provider Routing | OpenRouter / Nous Portal 経由の routing、fallback chain | 比較 run の provider 固定、mode tag、trace metadata、比較公平性 guard |
| STT Adapter | Local Whisper、Groq/OpenAI Whisper、custom command provider、Python plugin provider | transcript を state snapshot / image / calibration / known locations と束ねる context builder |
| Basic Vision Transport | vision-capable model への image input、汎用 vision analysis | camera calibration、map frame、object-to-location resolution、L3 Visual Resolver への入力 |
| MCP Connection | stdio / HTTP MCP、OAuth、tool include/exclude、resources/prompts wrapper 制御 | motion tool の accepted path、Policy Gate の reject 理由、Bridge mint の `idempotency_key` |
| Plugin Extension | custom tool、hook、model provider、STT/TTS、MCP 連携の plugin 化 | robotics safety policy、ER/VLA contract、L3 handoff、Audit/Eval join |
| Memory / Skills / Session Search | Mode A 演出や operator-facing workflow の記憶・手順化 | Phase 4 比較 run の強制 OFF、交通制御 skill 禁止、Hermes config 側 OFF と Bridge intent guard |

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
| Hermes 経由 | Hermes が対象 model、OpenAI 互換 request、image input、voice/STT/TTS provider、provider fallback を扱える | server-side motion tool execution を使わず、Bridge が final output を受けて L3 へ渡す |
| Direct adapter | ER/VLA runtime、GPU、audio/image API、latency 要件が Hermes 経由に合わない | trace、timeout、audit、L3 接続、secret 管理を Bridge 側に残す |

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

- Hermes が Gemini Robotics-ER の audio / image API を扱えるか。
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
