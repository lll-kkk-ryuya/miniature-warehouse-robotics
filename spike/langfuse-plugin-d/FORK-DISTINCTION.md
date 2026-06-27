# Two forks, do NOT conflate: input_audio fork (KEPT) vs Langfuse-plugin fork (AVOIDED by D)

> Option D = "make the outcome-score join work with the Hermes Langfuse plugin **ON**, with
> **NO Langfuse-plugin fork**." It is **AUDIO-FIRST**: we verify the join on the real audio path.
> The word "no-fork" in Option D refers ONLY to the **Langfuse plugin**, never to the audio fork.

## The crisp distinction

| | **input_audio fork** | **Langfuse-plugin fork (OPTION A)** |
|---|---|---|
| **What it forks** | Hermes Gateway **input/transport layer**: 2 files (`gateway/platforms/api_server.py` + `agent/gemini_native_adapter.py`) | The Hermes **observability/langfuse plugin** (`plugins/observability/langfuse/__init__.py`) |
| **What it adds** | A new input **MODALITY**: accept OpenAI `input_audio` content parts and map them to Gemini native `inlineData{mimeType: audio/wav}` | Make the plugin **read an inbound `trace_id`/`metadata`** so the Bridge can own/seed the trace through the plugin |
| **Why** | **Unforked Hermes cannot carry audio** → POST `input_audio` → HTTP **400 `unsupported_content_type`** (PROBE-2, 2026-06-27). Robotics-ER natively supports audio; the only missing step is Hermes passing the part through. | Without it the plugin mints its own trace id and ignores inbound → Pattern A's Bridge-owned trace cannot pass through a plugin-ON gateway. |
| **Status under Option D** | **KEPT.** This is what makes AUDIO reach ER through Hermes. | **AVOIDED.** Option D's whole point is to NOT need this fork. |
| **File** | `mwr-hermes-er-fork/deploy/hermes/er-audio-fork/0001-input_audio-passthrough.patch` (2-file patch, applied by `apply-fork.sh`, idempotent, refuses to touch personal `~/.hermes`) | *does not exist* — deliberately not created |

## Why the two never overlap (different layers, different concerns)

- The **input_audio fork is a transport/input concern**: it changes how a *request body* is
  normalized (audio content part → Gemini inlineData). It touches **nothing** in
  orchestration/safety/observability (no `action_map`, no Policy Gate, no timeout-0-dispatch,
  no eval_sdk scores). It is the same shape as the existing `image_url` passthrough.
- The **Langfuse plugin is an observability concern**: it runs **AFTER** ER has received the
  input and produces the trace/generation/score telemetry. **This telemetry is the SAME
  regardless of modality** — whether the user input arrived as text or as `input_audio`, the
  trace/score join behaves identically (the modality difference is fully absorbed by the
  input_audio fork upstream of the plugin).

So: **audio needs the input_audio fork; the Langfuse/score-join does NOT.** Option D adds **no
Langfuse fork** because the plugin's trace id is **deterministic from a seed** —
`create_trace_id(seed=f"{session_id or 'sessionless'}::{task_id or task_key}")`
(`~/.hermes/.../observability/langfuse/__init__.py:544`) — so the Bridge can **re-derive** that
id instead of forcing the plugin to read an inbound one. The Bridge pins
`session_id = H = seed_for(run_id, gen_id)` via the `X-Hermes-Session-Id` header; the response
echoes it (`api_server.py:1515 effective_session_id = result.get("session_id")`); on the
stateless chat path `task_id` defaults to `session_id`, so the plugin seed collapses to
`f"{H}::{H}"` and the scorer re-derives the identical id via
`eval_sdk.seed.derive_plugin_trace_id` — **with no plugin change**.

## How D verifies (audio-first, on the already-forked + plugin-ON gateway)

The live-verify gateway is **one** gateway that already does BOTH things, isolated from the
personal install:

```
mwr-hermes-er-fork/deploy/hermes/er-audio-fork/hlf-g0-langfuse/run-er-gateway-langfuse.sh
```

- **(a)** applies `0001-input_audio-passthrough.patch` (the **audio fork**) to the worktree
  Hermes source so `input_audio` is accepted (audio reaches ER), and
- **(b)** enables the Hermes built-in **Langfuse plugin** in an **isolated** env (langfuse
  installed via `pip --target <throwaway dir>` and put on `PYTHONPATH` alongside the worktree
  patch) so it **never touches personal `~/.hermes`**.

D is then verified by POSTing **`input_audio`** (the real path) and checking the
plugin-minted trace id equals `derive_plugin_trace_id(run_id, gen_id)` and that the
outcome scores join onto it. **Modality = audio; observability mechanism = the same seed-join
that would hold for text** — we just prove it on the real (audio) path.

## One-line summary

- **input_audio fork** = audio modality passthrough → **KEPT** (audio cannot reach ER without it).
- **Langfuse-plugin fork (OPTION A)** = make the plugin read inbound trace_id → **AVOIDED by D**
  (D re-derives the plugin's deterministic seed-minted id instead → **no Langfuse fork**).
