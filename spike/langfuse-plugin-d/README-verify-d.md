# Option D — AUDIO live verify (Langfuse plugin ON, NO Langfuse-plugin fork)

> **Build-only artifact.** The live run is a **human gate** (real ER gateway +
> real Langfuse + real Google ER quota). An agent/subagent must NOT run it.
>
> Design正本: `docs/architecture/08-llm-bridge-common.md`（Langfuse / commander
> observability）・`.claude/rules/llm-observability-testing.md`（live Langfuse =
> human gate）・`docs/architecture/21-eval-sdk-extraction.md` §3/§8（trace-id
> join key）。

---

## What this verifies (Option D, AUDIO-first)

**Goal:** make the outcome-score **join** work with the Hermes built-in Langfuse
plugin **ON**, **without forking the Langfuse plugin** (we avoid OPTION A). We
verify it on the **real path: AUDIO** (`input_audio`) reaching
`gemini-robotics-er` through the **already-forked** + **plugin-ON** gateway.

**Fork distinction (do NOT conflate):**

- The **`input_audio` fork** (2-file passthrough patch) is **KEPT** — it is what
  makes **AUDIO** reach ER through Hermes (without it, audio ⇒ HTTP 400).
- Option D's **"no fork"** means **NO fork of the Langfuse plugin**. The
  Langfuse / score-join is observability **after** ER receives the input — it is
  the **same regardless of modality** — but we **verify it with AUDIO** (the real
  path) on the already-forked + plugin-ON gateway.

The five live assertions (`verify_d_audio.py`):

1. `H = f"{run_id}:{gen_id}"` (e.g. `"verifyrun:1"`) — the cross-lane join key
   (`eval_sdk.seed.seed_for`).
2. Generate a WAV (`say`+`afconvert`, or `--wav`) and **POST it as an
   `input_audio` content part** to the forked + plugin-ON gateway
   `/v1/chat/completions` **with header `X-Hermes-Session-Id: H`** — the REAL
   audio path (`gemini-robotics-er`, native audio via the fork). **Assert HTTP
   200** (audio reached ER).
3. Read the **echoed `session_id`** from the response and **assert `== H`**
   (drift-detect; on the live Bridge a drift means skip the cycle's score,
   never raise).
4. Query Langfuse for the plugin-minted trace and **assert `trace_id ==
   langfuse.create_trace_id(seed=f"{H}::{H}")`** — i.e. the **D prediction =
   `eval_sdk.derive_plugin_trace_id(run_id, gen_id)`** — the score-join works for
   the AUDIO call.
5. Confirm a **generation + usage/cost** present on that trace.

**PASS / FAIL / INCONCLUSIVE** exit codes (`0 / 1 / 2`).

### Verified D mechanism (cited)

- Plugin mints the trace at
  `~/.hermes/hermes-agent/plugins/observability/langfuse/__init__.py:544`:
  `trace_id = client.create_trace_id(seed=f"{session_id or 'sessionless'}::{task_id or task_key}")`.
- `create_trace_id` is **pure**: `sha256(seed)[:16].hex` (32-hex, no dash)
  (`langfuse/_client/client.py:1759`).
- Plugin fires via the **`pre_api_request`** hook (`__init__.py:999` register;
  invoked `agent/conversation_loop.py:1258`).
- `X-Hermes-Session-Id` sets `session_id`; the gateway **echoes** it back
  (`api_server.py:1515` `effective_session_id = result.get("session_id")`).
- On the agent chat path the gateway sets `effective_task_id = session_id or uuid`
  (`api_server.py:3464` / `:3706`) and `conversation_loop.py:432` keeps it, so the
  hook receives **`task_id == session_id == H`** ⇒ `_trace_key` (`__init__.py:222`)
  returns `H` ⇒ **both seed halves equal `H`** ⇒ seed = **`"H::H"`**.

> **Honest caveat (handled, not silenced):** a predecessor probe
> (`../../../mwr-hermes-er-fork/.../hlf-g0-langfuse/hlf_g0_probe.py`) used the
> **stock stateless recipe** where `task_id == ""` ⇒ seed `f"{H}::session:{H}"`.
> Which recipe fires depends on the live gateway routing. `verify_d_audio.py`
> predicts **`H::H`** (the D contract) as **primary**, and if that trace is
> absent it also checks the **stock** id; a stock-only match yields
> **INCONCLUSIVE** with the matched recipe named — never a silent FAIL/PASS.

> **Managed-prompt caveat:** the `langfuse_prompt=` link is **LOST** under any
> plugin-ON option (the plugin has no `prompt=` path). On the D bridge path,
> propagate prompt name/version via Langfuse **metadata/tags**, or accept the
> regression — **not silently**. (Not exercised by this harness; flagged for the
> Bridge change.)

---

## PREREQUISITE (exact)

The Langfuse plugin keys **must already be** in the **ISOLATED** home's `.env`
(NOT the personal `~/.hermes`):

```
# ~/.hermes-mwr-er-lean/.env       (the isolated LEAN ER home)
HERMES_LANGFUSE_PUBLIC_KEY=pk-lf-...
HERMES_LANGFUSE_SECRET_KEY=sk-lf-...
HERMES_LANGFUSE_BASE_URL=https://cloud.langfuse.com    # or your self-host host
API_SERVER_KEY=...                                     # gateway auth (already present)
```

- The real `.env` is **gitignored** (`**/.env`). Commit only placeholders. The
  scripts **source** it and **never echo** the values.
- Without `HERMES_LANGFUSE_*` the gateway still serves `input_audio` (HTTP 200),
  but the plugin **fails open** (no traces) ⇒ steps 4–5 are **INCONCLUSIVE**.
- The `langfuse` SDK is supplied via the launcher's isolated
  `pip install --target /tmp/mwr-hlf-g0-langfuse-libs` + `PYTHONPATH` — **never**
  the Hermes venv, **never** the personal home.

---

## Run (human / main session, after creds)

```bash
cd spike/langfuse-plugin-d
./run-verify-d-audio.sh                         # run_id=verifyrun gen_id=1 (H="verifyrun:1")
./run-verify-d-audio.sh --run-id myrun --gen-id 7
./run-verify-d-audio.sh --wav /path/to/clip.wav # non-macOS host (no say/afconvert)
```

The wrapper:

1. **starts** the forked + plugin-ON LEAN ER gateway in the background via the
   single-sourced launcher
   `../../../mwr-hermes-er-fork/deploy/hermes/er-audio-fork/hlf-g0-langfuse/run-er-gateway-langfuse.sh`
   (applies the `input_audio` patch **and** enables `observability/langfuse` in an
   isolated env);
2. **waits** for `/health`;
3. **sources** creds from `$HERMES_HOME/.env` (`set -a`; never echoed) and
   prepends the isolated langfuse libs to `PYTHONPATH`;
4. **runs** `verify_d_audio.py`;
5. **stops** the gateway via the launcher `--stop` (kills by port; removes the
   isolated worktree) on every exit path.

### Exit codes

| code | verdict | meaning |
|---|---|---|
| `0` | **PASS** | audio 200 + echo==H + landed `trace_id == create_trace_id(f"{H}::{H}")` + a generation with usage/cost. |
| `1` | **FAIL** | hard contradiction (audio ≠ 200, echo drifts breaking the join, langfuse recipe drift, or trace `.id` mismatch). |
| `2` | **INCONCLUSIVE** | could not complete (no creds/SDK, no WAV, trace not flushed, usage/cost absent, **or** only the stock `H::session:H` recipe matched — a human decides). |

---

## Knobs (env)

| var | default | meaning |
|---|---|---|
| `GATEWAY_LAUNCHER` | the `hlf-g0-langfuse/run-er-gateway-langfuse.sh` path | single-sourced launcher (audio fork + plugin ON). |
| `HERMES_HOME` | `~/.hermes-mwr-er-lean` | **isolated** ER home (refuses personal `~/.hermes`). |
| `LANGFUSE_LIBS` | `/tmp/mwr-hlf-g0-langfuse-libs` | isolated langfuse `--target` install (shared with the launcher). |
| `PORT` | `8644` | base launcher default. |
| `HEALTH_TIMEOUT_S` | `90` | `/health` wait budget. |

`verify_d_audio.py` flags: `--run-id` (or `$WAREHOUSE_RUN_ID`), `--gen-id`,
`--base`, `--wav`, `--timeout`, `--trace-attempts`, `--trace-delay`.

---

## R-26 / scope

`verify_d_audio.py` + `run-verify-d-audio.sh` are a **spike** (this dir only).
They touch **no** `warehouse_*` runtime and **no** frozen contract. The actual
Bridge D change (drop the `langfuse.openai` wrapper, send
`extra_headers={"X-Hermes-Session-Id": H}`, fail-open drift-detect) is
**OPT-IN / feature-flagged** (Pattern A stays default) and **contingent on this
live AUDIO D-verify PASSing** — it must preserve timeout→0-dispatch, fail-open,
Mode C no-actuation, and the 3-layer exclusion (commander-cycle observability for
ALL modes A/B/C, obs-only).
