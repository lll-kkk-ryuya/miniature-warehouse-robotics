# Option D — managed-prompt link decision (propagate name/version, do NOT silently regress)

> **Context**: Option D leaves the **Hermes Langfuse plugin ON** so it mints the trace +
> generation server-side (no Langfuse-plugin fork; see [`FORK-DISTINCTION.md`](FORK-DISTINCTION.md)).
> The default ships **Pattern A** unchanged; Option D is **OPT-IN** (`WAREHOUSE_LANGFUSE_OWNER=
> hermes_plugin`) and **CONTINGENT on the live audio D-verify PASS**.
> **Design source of truth**: [`docs/architecture/08-llm-bridge-common.md` §Langfuse Prompt
> Management 方針](../../docs/architecture/08-llm-bridge-common.md) (doc08:522-533) +
> [doc13 §7.5 (trace 所有)](../../docs/architecture/13-hermes-setup.md) (doc13:517).

## The caveat (stated, not silent)

Under **any plugin-ON option**, the managed-prompt **LINK** is lost.

- **Pattern A (default, doc08:533 / doc13:517)**: the Bridge calls through
  `from langfuse.openai import AsyncOpenAI` and passes
  `langfuse_prompt=<ResolvedPrompt.langfuse_prompt>` on `.create(...)`. The langfuse.openai
  wrapper attaches the **managed prompt object** to the generation, so Langfuse's
  prompt-level analytics (which prompt VERSION produced which output, cost, latency) work
  natively. Wiring:
  `warehouse_llm_bridge/prompts.py:88-91,181-188` (`ResolvedPrompt.langfuse_prompt` set only
  for a genuine managed fetch) → `hermes_client._decide_bridge_owned` (`langfuse_prompt=`).
- **Option D (plugin-ON)**: the plugin owns the generation and has **NO `prompt=` path** —
  it wraps the LLM call generically and never receives a Langfuse prompt object (verified:
  the plugin's only trace/generation surface is
  `~/.hermes/.../observability/langfuse/__init__.py:544` `create_trace_id(seed=...)` +
  generation `:784-798`; there is no prompt-link kwarg in the `pre_api_request` hook kwargs,
  `agent/conversation_loop.py:1257-1281`). So `hermes_client._decide_plugin_owned` **drops**
  `langfuse_prompt=` (it would be ignored at best, and the plugin would not honour it). The
  **native prompt-link is therefore unavailable on the D path**. This is the known regression.

## Decision: PROPAGATE prompt name/version via Langfuse metadata/tags (do NOT accept a silent regression)

**Recommendation — propagate, do not regress.** The prompt **identity we need for Phase-4
fairness analysis is already computed by the Bridge and already rides the trace as opaque
metadata/tags under Pattern A** — and it does **not** depend on the langfuse.openai wrapper.
Reuse exactly that channel on the D path:

- `warehouse_llm_bridge/prompts.py` resolves, for every cycle (any owner):
  - `ResolvedPrompt.name` — the Langfuse prompt name (encodes the mode: `-mode-ab` / `-mode-c`),
    `prompts.py:44-46,142`;
  - `ResolvedPrompt.version` — the managed prompt VERSION (`None` for the code fallback),
    `prompts.py:97,186`;
  - `is_fallback` → `prompt_source` (`langfuse` vs `code`), `prompts.py:98,179-188`.
- doc08:533 already mandates putting these on the trace as
  `tags=[provider, mode, "prompt:<name>", env=<v>]` and
  `metadata={prompt_name, prompt_version, prompt_source, mode_label}` **via the domain-free
  `eval_sdk.LangfuseTracer` `extra_tags`/`extra_metadata`** — NOT via `langfuse_prompt=`. So the
  **filter/group-by discriminator (which prompt name+version was used) survives without the
  native link**.

**What is genuinely lost vs propagated:**

| Capability | Pattern A | Option D (propagated) |
|---|---|---|
| Filter/group traces by prompt **name** | ✅ tag `prompt:<name>` | ✅ same tag, on the plugin trace |
| Record prompt **version** for fairness pinning | ✅ metadata `prompt_version` | ✅ same metadata |
| Native Langfuse **prompt-level analytics** (prompt→generation link in the Prompt UI, prompt cost/latency rollups) | ✅ via `langfuse_prompt=` | ❌ **lost** (no plugin `prompt=` path) |

So we **accept the loss of the native prompt↔generation LINK** (a UI/rollup convenience) but
**refuse to lose the prompt name+version DISCRIMINATOR** (the load-bearing Phase-4 fairness
signal). The discriminator is propagated as metadata/tags; the regression is bounded and
documented, not silent.

## How to land the propagation on the D trace (the open seam)

Pattern A puts the tags/metadata on the **Bridge-owned** trace via
`LangfuseTracer.turn(gen)` (`ws/src/eval_sdk/eval_sdk/tracer.py`). Under Option D the Bridge
swaps `LangfuseTracer` → `NoopTracer` (the plugin owns the trace, so the Bridge must not open a
second one — `llm_bridge.py`), which means the Bridge no longer writes those tags itself.
Three ways to still attach the prompt name/version to the **plugin's** trace, in order of
preference:

1. **Post-hoc enrich by trace id (preferred, no fork).** The Bridge already knows the plugin's
   trace id deterministically: `derive_plugin_trace_id(run_id, gen_id)`
   (`eval_sdk/seed.py:108-126`). After the cycle, attach `prompt_name`/`prompt_version`/
   `prompt_source` (+ `mode_label`, `env=<v>`) to that trace via the Langfuse client
   (`update`/`create_event` keyed by `trace_id`) — same metadata/tag values doc08:533 lists,
   just written by id instead of by the langfuse.openai wrapper. Fail-open (any error → skip,
   never raise into the cycle, doc08:333). **No plugin fork** (consistent with Option D's
   no-fork promise).
2. **Carry it inbound as request metadata** if/when the plugin echoes/propagates a generic
   `metadata` field — **rejected for now**: the verified plugin does **not** read inbound
   metadata (`PLUGIN-TRACEID-ANALYSIS.md`: "plugin は INBOUND trace_id/metadata を読まない",
   `conversation_loop.py:1257-1281` kwargs have no metadata), so this would require a plugin
   fork (the OPTION A we are avoiding).
3. **Accept regression** (name/version only on the audit/score side, not on the trace) —
   **rejected**: doc08:533 makes the prompt discriminator a trace requirement, and #1 achieves
   it without a fork.

> **Net**: choose **#1** — re-derive the plugin trace id (`derive_plugin_trace_id`) and write
> the prompt name/version/source as trace metadata/tags post-hoc, fail-open. Keep the
> `langfuse_prompt=` native link as a **Pattern-A-only** feature. The score side already joins
> via the same id (`score_send.py` `pattern_d`), so name/version land on the one trace the
> outcome scores attach to.

## Status / scope

- The **drop** of `langfuse_prompt=` on the D path is implemented and tested
  (`hermes_client._decide_plugin_owned`; `tests/unit/test_hermes_client_option_d.py`
  `test_plugin_path_drops_langfuse_prompt_even_if_set`).
- The **post-hoc enrich (#1)** is the recommended follow-up wiring; it is OUT OF SCOPE of the
  minimal opt-in bridge change and is gated, like all of Option D, on the **live audio D-verify
  PASS** before it ships as anything but opt-in.
