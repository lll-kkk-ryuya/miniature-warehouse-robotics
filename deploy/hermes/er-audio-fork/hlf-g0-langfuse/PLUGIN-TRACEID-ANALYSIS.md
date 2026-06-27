# HLF-G0 — Hermes Langfuse plugin: inbound `trace_id` honoring (static analysis)

> **目的（GOAL, GROUNDED FACT）**: ユーザの Pattern B instinct を検証する。Bridge 側の
> `from langfuse.openai import AsyncOpenAI` wrapper を外し、Hermes 内蔵 Langfuse plugin を ON にして
> 「LLM 呼び出しの trace 化」を Hermes に任せられるか。決め手 = **HLF-G0**: Hermes Langfuse plugin が
> request metadata で渡した **INBOUND `trace_id` を尊重する**か。尊重するなら、Warehouse Orchestrator (#6) /
> eval_sdk が **同じ trace** に outcome score（SR / SPL / collision）を後付けできる → plugin-ON + no-wrapper が clean。
> 尊重しないなら fork tweak か wrapper が必要。
>
> **本書は静的解析（build-only）**。live gateway / Langfuse へのアクセスは一切していない。
> live HLF-G0 は creds 投入後に **main session が逐次実行**する（本書末尾の PREDICTION を検証する）。

---

## 0. 結論（TL;DR）

| 問い | 回答 | 根拠（file:line） |
|---|---|---|
| plugin は INBOUND `trace_id` を request metadata から読むか？ | **NO** | plugin `__init__.py:544` は **常に自前生成** `client.create_trace_id(seed=...)`。inbound を読む経路が**存在しない** |
| では trace_id はどう決まるか？ | `create_trace_id(seed=f"{session_id or 'sessionless'}::{task_id or task_key}")` の **決定論的ハッシュ** | plugin `__init__.py:544` |
| inbound を運べる request field はあるか？ | **無い**。core が hook に渡す kwargs に `trace_id` も汎用 `metadata` も無い | `agent/conversation_loop.py:1257-1281`（pre_api_request 呼び出し kwargs 一覧） |
| usage / cost を attach するか？（Issue #42306 関連） | **YES**。generation observation に `usage_details` / `cost_details` を付ける | plugin `__init__.py:480-539`, `:907-913`, `:629-632` |
| trace level か generation level か？ | trace_id は **trace（root observation）level** で固定、usage/cost は **generation level** | trace: `:544,:556,:567-595` / usage: generation `:784-798` を `:907-913` で end |
| **HLF-G0 verdict** | **FAIL（unforked では inbound trace_id を honor しない）** → fork tweak か wrapper が要る | §4 / §6 |

> **要約**: unforked Hermes Langfuse plugin は **trace_id を一方的に自生成**する。Bridge / Orchestrator が
> 渡した trace_id を**読む経路は plugin にもまだ core hook payload にも無い**。よって「plugin-ON + no-wrapper だけで
> Bridge の trace に Orchestrator の outcome score を相乗りさせる」は **そのままでは不可**。
> ただし `create_trace_id` が **seed から決定論的**である点が脱出口になる（§6 の最小 fork、または seed 同期）。

---

## 1. trace_id はどこで決まるか（=自生成、inbound を読まない）

plugin のトレース起点は `_start_root_trace()`。trace_id を作る**唯一の行**は:

```
# plugins/observability/langfuse/__init__.py:544
trace_id = client.create_trace_id(seed=f"{session_id or 'sessionless'}::{task_id or task_key}")
```

- **`create_trace_id(seed=...)` を呼んでおり、自前生成**である（「inbound を読む」ではない）。
- seed の素材は `session_id` と `task_id`（無ければ `task_key`）のみ（`__init__.py:544`）。
  `task_key` は `_trace_key(task_id, session_id)`（`__init__.py:222-227`）＝ `task_id` → `session:<session_id>` →
  `thread:<ident>` の順のフォールバック。**どれも「呼び出し側が渡した trace_id」ではない**。
- 生成した trace_id は `trace_context={"trace_id": trace_id}` として root observation に渡される
  （`__init__.py:556`, `:567-595`）＝ **trace（root）level** に置かれる。
- 以後この trace_id は `TraceState.trace_id`（`__init__.py:45,:603`）に保持されるだけで、
  **どの hook も外部から上書きしない**。

### 「inbound を読む経路が無い」ことの裏取り（plugin 全文 grep）

plugin 内で `trace_id` / `trace_context` / `create_trace_id` / `propagate_attributes` が現れる全行:

```
37  from langfuse import Langfuse, propagate_attributes        # import
45  trace_id: str                                             # TraceState フィールド宣言
544 trace_id = client.create_trace_id(seed=...)               # ★唯一の生成（自生成）
556 trace_ctx: Dict[str, Any] = {"trace_id": trace_id}        # 生成値を context へ
560-595 propagate_attributes(...) / start_as_current_observation(trace_context=trace_ctx)  # 適用
602-603 _debug(...) / return TraceState(trace_id=trace_id, ...)
```

→ **inbound（hook kwargs / request metadata）由来の trace_id を読み取る行は 0 件**。
plugin の hook シグネチャ（`on_pre_llm_request` `__init__.py:729-749`, `on_pre_llm_call` `:689-692`,
`on_post_llm_call` `:801-807`, tool 系 `:921-948`）にも `trace_id=` / `trace_context=` パラメータは存在しない。
全 hook は `**_: Any` で未知 kwarg を**捨てる**ため、仮に core が将来 `trace_id` を渡しても**現状の plugin は無視する**。

---

## 2. inbound を運べる request field はあるか（=core が hook に渡す kwargs を調査）

plugin が見るのは Hermes core が hook に渡す kwargs だけ。`pre_api_request`（plugin の `on_pre_llm_request` に bind、
`__init__.py:999`）を core が発火する箇所:

```
# agent/conversation_loop.py:1257-1281  _invoke_hook("pre_api_request", ...)
task_id=effective_task_id,         turn_id=turn_id,         api_request_id=api_request_id,
session_id=agent.session_id or "", user_message=...,        conversation_history=list(messages),
platform=..., model=..., provider=..., base_url=..., api_mode=..., api_call_count=...,
request_messages=..., message_count=..., tool_count=..., approx_input_tokens=...,
request_char_count=..., max_tokens=..., started_at=..., request=_request_payload,
```

- **`trace_id` も汎用 `metadata` も渡していない**。trace_id を運べる専用 field は存在しない。
- `request=_request_payload`（`conversation_loop.py:1256,:1280`）の中身は `_api_request_payload_for_hook`
  （`run_agent.py:2003-2014`）＝ `{"method":"POST","body": <api_kwargs minus timeout/http_client>}`。
  `body` は **provider への素のリクエスト（messages / model / tools / max_tokens 等）**であり、
  **trace_id を入れる場所ではない**（OpenAI `metadata` field を Bridge が積んでも、ここは provider 呼び出し用で、
  plugin はこの body から trace_id を読んでいない＝§1 の通り plugin は body を trace 化に使わない）。
- 唯一 seed に効くのは `task_id` / `session_id`:
  - `task_id` ← `effective_task_id = task_id or str(uuid.uuid4())`（`conversation_loop.py:432`）。
    `run_conversation(task_id: str = None, ...)`（`conversation_loop.py:351,:356,:367`）の引数で、
    **HTTP リクエスト body の field では受けていない**（呼び出しは CLI / web_server 経路、§3）。
  - `session_id` ← `agent.session_id`（`conversation_loop.py:1262`）＝ agent/session の状態であって request metadata ではない。

> **したがって「どの request field が inbound trace_id を運ぶか」への答えは "現状どれも運ばない"**。
> 強いて間接経路を挙げれば **`task_id` + `session_id`**（seed の素材）だが、これは「trace_id を渡す」のではなく
> 「seed を一致させれば trace_id が決定論的に一致する」**間接ルート**（§6 OPTION B）。

---

## 3. gateway 経路の確認（proxy は hook を焚かない）

`~/.hermes/hermes-agent/hermes_cli/proxy/server.py` は **credential ローテーションの passthrough proxy**
（`server.py:4,:34,:107 handle_proxy`, `:141 _filter_request_headers`）であり、**conversation loop を回さない**＝
**Langfuse hook を一切発火しない**。Langfuse trace を作るのは `run_conversation`（`conversation_loop.py:351`）を
回す CLI / `hermes_cli/web_server.py` 経路。よって「gateway に X-Trace-Id ヘッダを足せば plugin が拾う」も**不成立**
（proxy はヘッダを upstream へ素通しするだけで、trace 化はしない／conversation 経路の hook payload にヘッダは入らない）。

> ⚠️ **未検証（human-gated）**: lean forked ER gateway（`run-er-gateway.sh`）が `/v1/chat/completions` を
> どの内部経路（proxy passthrough か conversation loop か）で捌くかは、本静的解析では plugin/core 側からのみ確認した。
> live で plugin が trace を出すこと自体（=conversation 経路を通ること）は HLF-G0 live run で確認が要る（§5 PREDICTION 参照）。

---

## 4. usage / cost を attach するか（Issue #42306 関連）

**YES、attach する。** ただし **generation observation level** であって trace level ではない。

- `_usage_and_cost(response, ...)`（`__init__.py:480-539`）が `agent.usage_pricing` の
  `normalize_usage` / `estimate_usage_cost` / `get_pricing_entry`（`__init__.py:488,:508,:519`）から
  `usage_details`（`input` / `output` / `cache_read_input_tokens` / `cache_creation_input_tokens` /
  `reasoning_tokens`、`:498-507`）と `cost_details`（per-type or `total`、`:516-535`）を組む。
- post 経路（`on_post_llm_call` `__init__.py:801`）で response からも、summary `usage` dict からも算出
  （`:841-899`）。
- それを **generation を end するとき**に付与:
  `_end_observation(generation, output=..., usage_details=..., cost_details=..., ...)`
  （`__init__.py:907-913`）→ `observation.update(usage_details=..., cost_details=...)`（`:629-632`）。

> **#42306 関連の含意**: cost/usage は **generation（子 observation）に乗る**。trace（root）レベルの集計は
> Langfuse 側が generation を合算して出す前提（`__init__.py:491-497,:516-517` のコメントが「dashboard が input/output を
> 合算」と明記）。**Orchestrator が後付けする outcome score（SR/SPL/collision）は trace level に付けたい**ので、
> 「同じ trace_id を共有できるか」が本丸であり、cost/usage の有無は HLF-G0 の合否を左右しない（attach 自体は OK）。

---

## 5. PREDICTION — live HLF-G0 の予測結果（human-gated・本書は予測のみ）

creds 投入後に main session が live 実行したとき、静的解析から予測する結果:

1. **plugin-ON で Hermes が "Hermes turn" trace を Langfuse に出す**: 予測 **YES**（creds が本物 pk-lf-/sk-lf- で、
   `langfuse` SDK が PYTHONPATH 上にあれば。`__init__.py:140-219` の init ゲート＋`:36-40` の SDK import ゲートを通る前提）。
   - ⚠️ 前提1: **personal venv に langfuse 未インストール**（GROUNDED FACT / 本セッションで再確認: personal venv に langfuse 無し）。
     → ISOLATED_DIR への `pip install --target` ＋ PYTHONPATH 前置が必須（personal venv/home は不可）。
   - ⚠️ 前提2: forked ER gateway が conversation-loop 経路を通ること（§3 の未検証点）。
2. **その trace の trace_id は Bridge / Orchestrator が指定した値に一致しない**: 予測 **YES（不一致）**。
   理由は §1: trace_id は `create_trace_id(seed=f"{session_id}::{task_id}")` の**自生成**で、inbound を読まないから。
   - 系として **Orchestrator が「Bridge が作った（はずの）trace_id」へ outcome score を後付けしても、Hermes 側 trace は別 id** になり、
     **score が宙に浮く**（別 trace か trace 無し）。
3. **usage / cost は generation に乗って表示される**: 予測 **YES**（§4。`agent.usage_pricing` に当該 model の pricing entry が
   あれば per-type、無ければ `total`、`:532-535`）。
4. **HLF-G0 verdict 予測 = FAIL**（inbound trace_id を honor しない）。
   → Pattern B（plugin-ON + no-wrapper）を「Orchestrator が同一 trace に score を相乗り」要件込みで満たすには、
   **§6 の最小 fork（OPTION A）か、seed 同期（OPTION B）か、wrapper 残置（OPTION C）のいずれか**が必要。

> 反証条件（PREDICTION が外れる兆候）: live で「Bridge 指定の trace_id と一致する trace_id」が Langfuse に出たら、
> 本静的解析の前提（core が trace_id を hook へ渡していない）が誤り → その場合は core 側に未把握の経路がある。
> 現状の `conversation_loop.py:1257-1281` の kwargs 一覧からは**その経路は無い**と判断する。

---

## 6. もし honor しないなら（=その通り）— 最小 fork tweak

**音声 fork（`0001-input_audio-passthrough.patch`、2 ファイル: `gateway/platforms/api_server.py` +
`agent/gemini_native_adapter.py`、applier=`apply-fork.sh`）と同型**に、**personal clone を触らない overlay** として足す。
HLF-G0 用の最小変更は **plugin 1 ファイル（`plugins/observability/langfuse/__init__.py`）**で済む。

### OPTION A（推奨・最小）: plugin が inbound trace_id を「あれば優先」で読む

**対象関数 / 行**: `_start_root_trace()`、**`__init__.py:544`**（trace_id 生成行）。
**変更**: 自生成の前に、Hermes core が（後述 A2 で）渡す `incoming_trace_id` kwarg を優先する 1 行ガードを足す。

```python
# __init__.py:544 を、おおむね下記へ（seed 自生成は fallback として残す＝後方互換 / fail-open）
trace_id = incoming_trace_id or client.create_trace_id(
    seed=f"{session_id or 'sessionless'}::{task_id or task_key}"
)
```

これに伴い `_start_root_trace(...)` のシグネチャ（`__init__.py:542-543`）に
`incoming_trace_id: Optional[str] = None` を追加し、呼び出し 2 箇所
（`on_pre_llm_call` 内 `__init__.py:714-724` と `on_pre_llm_request` 内 `:768-778`）から
hook kwargs 由来の値を渡す。hook 側は `**_` で受けているので、`on_pre_llm_request(..., trace_id: str = "", **_)` の
ように **named param を 1 つ生やす**だけ（既存呼び出しは壊れない＝additive / 後方互換）。

> ⚠️ **A2（前提）**: §2 の通り **core は今 hook に trace_id を渡していない**。よって OPTION A を本当に効かせるには、
> **core 側の 1 行追加（`agent/conversation_loop.py:1257-1281` の `_invoke_hook("pre_api_request", ...)` に
> `trace_id=<inbound値>,` を足す）も必要**。すると音声 fork と同じ **「core 1 ファイル + plugin 1 ファイル」＝2 ファイル overlay** になる。
> ただし `<inbound値>` を **どの request field から取るか**は要設計判断（OpenAI 互換 `metadata.trace_id` を採るのが自然だが、
> それは `api_kwargs`／`request["body"]` 経由で core まで運ぶ配線が要る）→ **docs を先に確定すべき空白**。
> 安易に発明せず、`docs/mode-x-er/06`（unfrozen-contract-resolutions）に **HLF-G0 解決として追記してから**実装する。

### OPTION B（fork 不要・運用で回避）: seed を一致させる

`create_trace_id(seed=...)` が**決定論的**（`__init__.py:544`、同一 seed → 同一 id）である性質を使う。
Bridge / Orchestrator が **Hermes へ渡す `session_id` / `task_id` を自分でも持ち**、同じ式
`f"{session_id or 'sessionless'}::{task_id or task_key}"` ＋ Langfuse の `create_trace_id(seed=...)` で
**trace_id を再計算**すれば、Hermes 自生成 trace と**同じ trace_id** を得て score を後付けできる。

- 長所: **plugin / core を一切 fork しない**。音声 fork の overlay 不要。
- 短所: seed 式（`__init__.py:544`）と `task_key` フォールバック（`:222-227`）に**密結合**し、Hermes 更新で式が変われば破綻
  （脆い・要 pin / 要 live 検証）。`task_id` を Bridge が決定論的に固定できる必要がある
  （`conversation_loop.py:432` は未指定なら `uuid4()`＝**Bridge が必ず task_id を渡す**運用が前提）。

### OPTION C（fork 不要・現状維持）: Bridge wrapper を残す

`from langfuse.openai import AsyncOpenAI`（Bridge `hermes_client.py`）＋ `LangfuseTracer`（Bridge `tracing.py`）が
**Bridge 側で trace を所有**し、Orchestrator/eval_sdk が**その trace_id を確実に握る**現行構成を維持。
plugin は OFF のまま。Pattern B の「Hermes に観測を寄せる」狙いは捨てるが、**outcome score 相乗りは確実**。

### 推奨

- **HLF-G0 の本来の狙い（観測を Hermes に寄せる＋score 相乗り）を取るなら OPTION A**（音声 fork と同じ 2 ファイル overlay 思想・
  additive・fail-open）。ただし **inbound trace_id の運搬 field を docs で先に確定**（A2）。
- **早く確実に回すなら OPTION C 維持**、または **B（seed 同期）を pin 付きで暫定採用**。
- いずれも **live HLF-G0 の verdict（§5 PREDICTION の検証）後に確定**。本書は静的解析の予測どまり。

---

## 7. 引用一覧（再検証可能な file:line）

Plugin（`~/.hermes/hermes-agent/plugins/observability/langfuse/__init__.py`）:
- `:36-40` langfuse SDK import ゲート（無ければ hook inert）
- `:140-219` `_get_langfuse()` init ゲート（creds / placeholder 検査 `:173-190`）
- `:222-227` `_trace_key()` フォールバック（task_id → session:<sid> → thread:<ident>）
- `:480-539` `_usage_and_cost()`（usage_details / cost_details 構築）
- `:542-603` `_start_root_trace()`（**:544 trace_id 自生成**, :556 trace_ctx, :567-595 適用）
- `:606-616` `_start_child_observation()`（generation 等の子）
- `:619-637` `_end_observation()`（:629-632 usage/cost を `observation.update`）
- `:689-692` `on_pre_llm_call` シグネチャ / `:729-749` `on_pre_llm_request` シグネチャ（trace_id param 無し）
- `:784-798` generation observation 生成 / `:801-918` `on_post_llm_call`（:907-913 usage/cost 付与）
- `:995-1004` `register()`（hook ↔ 関数 bind）

Core（`~/.hermes/hermes-agent/`）:
- `agent/conversation_loop.py:432` `effective_task_id = task_id or str(uuid.uuid4())`
- `agent/conversation_loop.py:351,:356,:367` `run_conversation(task_id: str = None, ...)`
- `agent/conversation_loop.py:1234,:1257-1281` `_invoke_hook("pre_api_request", ...)`（kwargs に trace_id / metadata 無し）
- `run_agent.py:2003-2014` `_api_request_payload_for_hook()`（request.body = api_kwargs、trace_id 用 field 無し）
- `hermes_cli/proxy/server.py:4,:34,:107,:141` credential-rotating passthrough proxy（hook 焚かない）

Plugin meta:
- `plugin.yaml:8-14` 登録 hook（pre/post_api_request, pre/post_llm_call, pre/post_tool_call）
- `README.md:19-27` 必要 creds（HERMES_LANGFUSE_PUBLIC_KEY / SECRET_KEY / BASE_URL）

---

## 8. 検証ステータス（正直な区分）

| 主張 | 区分 |
|---|---|
| plugin は trace_id を `create_trace_id(seed=...)` で自生成・inbound を読まない | **検証済**（plugin 全文 Read + grep、`__init__.py:544` 唯一生成） |
| core は pre_api_request hook に trace_id / 汎用 metadata を渡さない | **検証済**（`conversation_loop.py:1257-1281` 実 Read） |
| usage/cost は generation level に attach される | **検証済**（`__init__.py:480-539,:907-913,:629-632`） |
| proxy は hook を焚かない | **検証済**（`proxy/server.py` は passthrough） |
| forked ER gateway が conversation-loop 経路を通り plugin が実際に trace を出す | **未検証 / human-gated**（live HLF-G0 で確認・§3/§5） |
| live で trace_id が Bridge 指定値と不一致になる | **予測のみ**（§5 PREDICTION、live 未実行） |
| personal venv に langfuse 未インストール | **検証済（本セッション）**（ISOLATED_DIR install 必須） |

> 本書は **build-only の静的解析**。live gateway 起動・Langfuse 送信は行っていない。
> live HLF-G0 は creds 投入後に main session が逐次実行し、§5 PREDICTION を検証する。
