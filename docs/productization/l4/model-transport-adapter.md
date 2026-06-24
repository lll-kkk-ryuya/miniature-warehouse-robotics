# L4 Model Transport / Adapter Sub-Box

作成日: 2026-06-24

> **状態**: 設計提案。Model Transport / Adapter は L4 Robotics Bridge Super-Box
> 内部の sub-box であり、model provider / runtime / modality の差を吸収する。
> 現行 commander LLM の Hermes transport は実装済みだが、ER / VLA / STT registry は
> proposal である。新しい config key、ROS topic、REST API、`warehouse_interfaces`
> frozen contract は追加しない。

## 結論

この sub-box の名前は **Model Transport / Adapter** とする。

理由:

- `Model Adapter` だけだと、Hermes Agent Gateway の provider routing / fallback / MCP / STT / vision などの transport 機能が見えにくい。
- `Model Transport` だけだと、raw output recorder、request template、timeout、L3 handoff への正規化など Bridge-owned の adapter 責務が見えにくい。
- 商用化では「model をどう呼ぶか」と「model output をどう安全な内部表現へ畳むか」を同じ保管単位で扱う必要がある。

現状の実装関係:

| 対象 | 現状 | 実体 / 正本 |
|---|---|---|
| commander LLM | Hermes transport 実装済み | `warehouse_llm_bridge/hermes_client.py` |
| ER adapter | proposal | `docs/mode-x-er/03-er-adapter-skeleton.md` |
| VLA adapter | proposal | `docs/mode-x-er-vla/` |
| STT adapter | proposal | `docs/productization/06-oss-reuse-and-box-small-designs.md` |
| Vision transport | Hermes-first proposal。vision は `provider_type` enum ではなく input modality / transport capability として扱う | `docs/productization/02-l4-robotics-bridge-box.md` |

## sub-box の位置

```text
L4 Robotics Bridge Super-Box
  -> Input Context
     input_refs: audio / transcript / overhead_image / state / calibration
  -> Model Transport / Adapter
     provider_type: llm | er | vla | stt
     transport: hermes | direct | worker
     raw output recorder
     timeout / provider error -> 0 dispatch
  -> L3 Handoff
  -> action_map / MCP / Policy Gate
```

Hermes はこの sub-box の **第一候補 transport** である。Hermes が扱える場合は
`transport: hermes` を使う。Hermes が対象 modality、runtime、response shape、latency
要件を満たせない場合だけ `direct` または `worker` に落とす。

## 詳細図

```text
                 Input Context bundle
 audio_ref / transcript / image_ref / state_ref / calibration_id
                              |
                              v
+-------------------------------------------------------------------+
| L4 Model Transport / Adapter sub-box                              |
|                                                                   |
|  provider_type                                                    |
|    +-- llm  [implemented] commander LLM                           |
|    +-- er   [proposal] Gemini Robotics-ER                         |
|    +-- vla  [proposal] OpenVLA / LeRobot / custom VLA             |
|    +-- stt  [proposal] Whisper / API STT / custom STT             |
|                                                                   |
|  transport                                                        |
|    +-- hermes [first choice]                                      |
|    |      OpenAI-compatible chat/responses                         |
|    |      provider routing / fallback                              |
|    |      vision / STT / MCP entry                                 |
|    |      implemented today for commander LLM via HermesClient      |
|    +-- direct [fallback]                                          |
|    |      provider-specific API when Hermes cannot fit              |
|    +-- worker [fallback]                                          |
|           GPU or separate process runtime                          |
|                                                                   |
|  Bridge-owned controls                                             |
|    request_id / timeout / raw output audit                         |
|    malformed / empty / unsupported_modality handling               |
|    same L3 Handoff input across transports                         |
|    0 dispatch on timeout / provider error                          |
+-------------------------------------------------------------------+
                              |
                              v
                         L3 Handoff
```

## 再利用可能な箇所

| 再利用対象 | 汎用化する内容 | 顧客ごとに差し替える内容 |
|---|---|---|
| adapter interface | `provider_type`、`transport`、request refs、raw output ref、latency / cost hint、adapter report | provider API、model name、region、quota、auth |
| Hermes transport | OpenAI-compatible request、provider fallback、MCP / STT / vision entry、capability check | Hermes config、active provider、fallback chain、tool include / exclude |
| direct adapter | Hermes 非対応 API の request / response envelope を Bridge の adapter interface へ合わせる型 | provider SDK、endpoint、payload schema、retry policy |
| worker adapter | GPU / local process を request queue と artifact refs で呼ぶ型 | RunPod / Jetson / local GPU、container image、runtime timeout |
| raw output recorder | raw model output、response headers、provider latency、parse result、malformed reason | storage path、retention、redaction policy |
| fixture | timeout、provider_error、malformed_response、unsupported_modality、empty_output | site layout、image/audio fixture、calibration profile |
| trace metadata | `run_id`、`gen_id`、`provider_type`、`transport`、`provider`、`mode`、`env` | tenant id、project id、commercial report axis |

## Hermes に任せる範囲

Hermes Agent Gateway に寄せる範囲:

- OpenAI 互換 model transport。
- provider routing / fallback。
- vision-capable model への image input entry。
- STT / voice provider entry。
- MCP connection / tool filter / plugin extension。
- provider の差を隠す gateway としての request routing。

ただし、Hermes に任せるのは transport / plugin の実装差分であり、box の所有権ではない。
Model Transport / Adapter sub-box の商用所有境界は Robotics Bridge 側に置く。

## Bridge-owned に残す範囲

以下は Hermes に移さない。

- Input Context の最終 manifest 確定。
- stale / missing / secret 混入判定。
- `request_id`、cycle、cancellation、timeout 後の 0 dispatch。
- raw output artifact の保存方針。
- provider response から L3 Handoff input への正規化。
- `gen_id` / `idempotency_key` の発行と注入。
- motion tool の accepted path と Policy Gate。
- Langfuse root trace ownership。Hermes plugin は HLF-G0〜G5 通過後に再評価。

## 開発時の注意点

1. **transport と provider_type を混ぜない**
   `provider_type=er` でも `transport=hermes` / `direct` / `worker` のどれもあり得る。
   `provider_type=stt` でも Hermes STT と offline Whisper fallback の両方を想定する。

2. **Hermes fallback と比較 run を混ぜない**
   4社比較では勝手な fallback が公平性を壊す。固定 provider leg と fallback-enabled leg を分ける。

3. **response shape を L3 Handoff へ正規化する**
   Hermes / direct / worker のどれを使っても、L3 に渡る内部 shape は同じにする。
   transport ごとの差を L3 に漏らさない。

4. **raw output は捨てない**
   parse 後の正規化結果だけでなく、provider の raw output と parse report を残す。
   商用 PoC では「なぜ止めたか」を後から説明できることが価値になる。

5. **model output に実行権限を渡さない**
   ER / VLA / STT / LLM が Nav2、Open-RMF、micro-ROS、`/cmd_vel`、motion MCP tool を直接呼ばない。
   server-side MCP execution は read-only / operator-facing tool に限定する。

6. **Langfuse の二重 generation を避ける**
   Bridge wrapper と Hermes plugin を同時に trace owner にしない。Hermes plugin 採用は
   HLF-G0〜G5 を live で満たしてから切り替える。

7. **secret と customer data を input bundle に入れない**
   API key、endpoint、customer private data、raw media の retention は adapter config と artifact store 側で管理する。

## 商用化の注意点

| 観点 | 注意点 | Gate |
|---|---|---|
| provider lock-in | provider SDK に依存した request / response shape を L3 に漏らさない | Hermes / direct の golden fixture が同じ L3 Handoff input を返す |
| cost | STT / vision / VLA は token 以外の課金軸がある | provider_latency_ms / token_or_cost_hint / media duration を記録する |
| privacy | audio / image / state は顧客現場情報を含む | artifact retention / redaction / access control を site profile に書く |
| reliability | fallback は便利だが比較公平性と説明責任を壊し得る | fallback-enabled run を別 session / metadata に分ける |
| latency | L4 は Non-RT だが、timeout 後に motion を出してはいけない | timeout / provider error は 0 dispatch fixture で検証する |
| audit | raw output がないと商用 report で説明不能になる | raw output ref と adapter_report_ref を必須 artifact にする |
| safety | model が出す低レベル action は採用不可 | forbidden endpoint / low_level_action_present を reject する |

## acceptance gate

| Gate | 内容 |
|---|---|
| L4M-G0 | commander LLM は既存 Hermes path の regression を壊さない |
| L4M-G1 | `provider_type` と `transport` を直交 field として fixture 化する |
| L4M-G2 | Hermes / direct / worker の valid response が同じ L3 Handoff input に正規化される |
| L4M-G3 | timeout / provider_error / malformed_response / empty_output / unsupported_modality は 0 dispatch になる |
| L4M-G4 | raw output、adapter report、latency、transport、provider metadata が artifact 化される |
| L4M-G5 | provider fallback は fixed-provider comparison run と混在しない |
| L4M-G6 | Langfuse plugin 採用時は HLF-G0〜G5 を全て満たし、二重 generation がない |

## 将来の module skeleton

現時点ではコード追加しない。実装に進む場合は、以下のような保管単位が自然である。

```text
ws/src/warehouse_llm_bridge/warehouse_llm_bridge/
  adapters/
    __init__.py
    base.py                  # provider_type / transport interface
    hermes_transport.py       # commander LLM の既存 HermesClient を一般化
    direct_transport.py       # provider-specific fallback
    worker_transport.py       # GPU / separate process fallback
    gemini_er.py              # provider_type=er
    stt.py                    # provider_type=stt
    vla.py                    # provider_type=vla
  audit/
    model_output_recorder.py
  context/
    manifest.py
  handoff/
    l3_handoff.py
```

この skeleton はまだ frozen contract ではない。実装前に owner docs と tests を先に追加する。
