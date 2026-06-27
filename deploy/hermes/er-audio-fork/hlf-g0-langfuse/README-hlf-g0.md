# HLF-G0 — does the Hermes Langfuse plugin honor an INBOUND trace_id?

> **RUN BY A HUMAN / MAIN SESSION ONLY.** This gate makes **real network calls**
> to a running plugin-ON forked ER gateway **and** to the Langfuse API. It needs
> `HERMES_LANGFUSE_*` + `API_SERVER_KEY` creds. An agent/subagent must **not**
> run it (no creds, no live gateway/Langfuse). The files here are **build-only**;
> the live run is sequential, done after the user supplies the creds.

## Why this gate exists (the Pattern B decision)

We want to test the user's **Pattern B instinct**: drop the Bridge-side
`from langfuse.openai import AsyncOpenAI` wrapper
(`ws/src/warehouse_llm_bridge/warehouse_llm_bridge/hermes_client.py` +
`tracing.py`) and turn the **Hermes built-in Langfuse plugin ON** instead.

The deciding factor is **HLF-G0**:

> Does the Hermes Langfuse plugin honor an **inbound** `trace_id` (passed by the
> Bridge in the request) so the **Warehouse Orchestrator (#6)** can later attach
> outcome scores (SR / SPL / collision / deadlock) to the **same** trace?

- **PASS** → plugin-ON + no-wrapper is clean. The Bridge owns the `trace_id`,
  the plugin writes the generation under it, and #6 scores the same trace.
- **FAIL** → the plugin mints its **own** `trace_id`; a fork tweak or the
  wrapper is needed — **unless** the deterministic-seed workaround below holds.

## What the plugin source actually does (verified by reading it)

`~/.hermes/hermes-agent/plugins/observability/langfuse/__init__.py` mints the
`trace_id` **server-side, always**. The only creation site is `_start_root_trace`:

```python
trace_id = client.create_trace_id(
    seed=f"{session_id or 'sessionless'}::{task_id or task_key}")   # ~line 544
```

There is **no** code path that reads `trace_id`, `metadata.trace_id`,
`langfuse_trace_id`, or any inbound correlation id from the request. The gateway
`/v1/chat/completions` handler
(`~/.hermes/hermes-agent/gateway/platforms/api_server.py:1671-1785`) reads only
`messages`, `model`, `stream`, `body.id`/`body.session_id`, and the headers
`X-Hermes-Session-Id` / `X-Hermes-Session-Key`. **It does not read a request
`metadata` object at all** — a Langfuse/OpenAI-style
`{"metadata":{"trace_id":...}}` body field is silently dropped.

> **Structural verdict (from source, before any live run): stock HLF-G0 = FAIL.**
> The probe still runs live to (1) prove audio works with the plugin ON
> (HTTP 200), (2) empirically confirm the FAIL, (3) check usage/cost (#42306),
> and (4) test the one real workaround knob.

## The one workaround — deterministic seed via `X-Hermes-Session-Id`

`langfuse.create_trace_id(seed)` is **deterministic**
(`langfuse/_client/client.py:1759`): `sha256(seed)[:16].hex()`. For
`/v1/chat/completions` the gateway sets `effective_task_id = session_id`
(`api_server.py:3464`), so with a stable `X-Hermes-Session-Id == H` the plugin
sees **both** `session_id == H` **and** `task_id == H` (non-empty → the
`_trace_key` `session:` fallback at `__init__.py:226` is never reached), and its
seed becomes:

```
seed = f"{session_id}::{session_id}"   # == "H::H"
```

So if the Bridge sends a **stable** `X-Hermes-Session-Id`, the Orchestrator can
**re-derive the plugin's trace_id offline** — no inbound trace_id, no wrapper:

```python
expected = create_trace_id(seed=f"{sid}::{sid}")
```

The probe reports **WORKAROUND-VIABLE** when the observed trace_id equals this
re-derivation (offline-checked: for `sid="hlf-g0-spike"` both the real SDK and
the probe produce `63f9559692ee224c4157979f640f0b49`). The earlier
`H::session:H` recipe assumed `task_id==""` and was **wrong** for this path —
corrected per source trace (workflow w3ub33nlh / claim C4).

> **Honest caveat:** WORKAROUND-VIABLE couples to the plugin's **internal** seed
> recipe (`session_id` + `task_key` shape), which is plugin-version-specific and
> could change upstream. It means "works on **this** clone (v0.15.1)", not a
> stable contract. A true PASS (inbound trace_id honored) would need a small
> fork patch to the plugin; otherwise keep the `langfuse.openai` wrapper.

## Files

| file | role |
|---|---|
| `hlf_g0_probe.py` | the probe (stdlib + isolated `langfuse`). POST → assert 200 → query Langfuse → PASS/FAIL verdict. |
| `run-hlf-g0.sh` | wrapper: isolated `langfuse` install, sources creds (never printed), runs the probe. `set -euo pipefail`, refuses personal paths. |
| `README-hlf-g0.md` | this file. |

## Prerequisites — **THE USER MUST DO THESE**

1. **Add `HERMES_LANGFUSE_*` to the gateway's isolated `.env`**
   (`~/.hermes-mwr-er-lean/.env` — the same isolated `HERMES_HOME` the ER gateway
   uses; **never** `~/.hermes`). `API_SERVER_KEY` is already there from the ER
   gateway setup. Append (real values; the file is gitignored, never committed):

   ```dotenv
   # Langfuse project the Hermes plugin writes to (and the probe reads from)
   HERMES_LANGFUSE_PUBLIC_KEY=pk-lf-...
   HERMES_LANGFUSE_SECRET_KEY=sk-lf-...
   # optional (defaults to https://cloud.langfuse.com)
   HERMES_LANGFUSE_BASE_URL=https://cloud.langfuse.com
   ```

2. **Run the forked ER gateway with the Langfuse plugin ON and `langfuse`
   importable by the gateway's interpreter.** This is the subtle part:

   - The plugin needs the `langfuse` SDK in the **gateway's** Python. The
     personal venv (`~/.hermes/hermes-agent/venv`) **does not** have it, so the
     plugin is **inert** there (it fail-opens to no-op) — no trace is written and
     the probe would report `INCONCLUSIVE`.
   - **Do not** `pip install` into the personal venv. Instead, launch the gateway
     with the isolated `langfuse` dir on its `PYTHONPATH` and the plugin enabled
     against the **isolated** `HERMES_HOME`, e.g.:

     ```bash
     # one-time: isolated langfuse (same dir the probe uses)
     python3.12 -m pip install --target /tmp/mwr-hlf-g0-langfuse-libs 'langfuse>=4.9,<5'

     # enable the plugin against the ISOLATED home (never ~/.hermes)
     HERMES_HOME=~/.hermes-mwr-er-lean \
       ~/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main \
       plugins enable observability/langfuse

     # launch the forked ER gateway with langfuse importable + plugin ON.
     # Prepend the isolated langfuse to PYTHONPATH on top of the fork worktree:
     PYTHONPATH=/tmp/mwr-hlf-g0-langfuse-libs \
       ./run-er-gateway.sh          # (run-er-gateway.sh adds the patched worktree itself)
     ```

     > `run-er-gateway.sh` already sets `PYTHONPATH=$WORKTREE_DIR` for the fork;
     > exporting `PYTHONPATH` before it runs **prepends** the langfuse dir
     > (`launch_gateway()` appends the worktree with `${PYTHONPATH:+:$PYTHONPATH}`).
     > Confirm the plugin actually loaded (gateway log mentions the langfuse hook
     > / no "langfuse SDK missing"); otherwise the probe will be `INCONCLUSIVE`.

3. Keep everything **isolated**: never touch `~/.hermes` or its venv; never
   commit a filled-in `.env`; never print secret values.

## Run it

```bash
cd deploy/hermes/er-audio-fork/hlf-g0-langfuse

# base URL is read from $HERMES_HOME/.env (API_SERVER_HOST:PORT); override if needed
./run-hlf-g0.sh
#   or: HLF_G0_BASE=http://127.0.0.1:8644 ./run-hlf-g0.sh
#   or with an explicit clip:  ./run-hlf-g0.sh --wav /path/to/clip.wav
```

The wrapper:

1. installs `langfuse>=4.9,<5` into an **isolated** dir
   (`/tmp/mwr-hlf-g0-langfuse-libs`, override with `LANGFUSE_LIBS`) — **never**
   the personal venv;
2. `source`s `~/.hermes-mwr-er-lean/.env` (exports `HERMES_LANGFUSE_*` +
   `API_SERVER_KEY`; **never echoes** them);
3. runs `hlf_g0_probe.py` with the isolated `langfuse` ahead on `PYTHONPATH`,
   pointed at the running gateway.

The probe will:

1. POST `input_audio` (a generated `say`+`afconvert` wav, or `--wav`; falls back
   to a text-only message if no wav tooling) to the plugin-ON gateway and
   **assert HTTP 200** (audio still works with the plugin ON);
2. wait for the plugin's async flush, then **query the Langfuse API** by
   `session_id` / tag / recency to find the generation the plugin wrote;
3. print a clear **PASS / FAIL / INCONCLUSIVE** verdict, plus whether usage/cost
   was attached (Issue #42306) and the observed trace_id vs the inbound and the
   re-derived ids.

### Exit codes

| code | meaning |
|---|---|
| `0` | **HLF-G0 PASS** — plugin honored the inbound trace_id |
| `2` | gateway POST did not return 200 (plugin ON broke the request path) |
| `3` | **HLF-G0 FAIL** — plugin minted its own trace_id (check the verdict text for `WORKAROUND-VIABLE` vs hard fail) |
| `4` | **HLF-G0 INCONCLUSIVE** — no generation found (plugin inert / wrong project / flush lag) |

## How to read the result → next step

- **PASS** → drop the wrapper, turn the plugin ON. The Bridge owns the trace_id.
- **FAIL + `WORKAROUND-VIABLE`** → plugin minted its own id, but it equals
  `create_trace_id(seed=f"{sid}::{sid}")` (H::H — corrected; see workaround note
  above). You **can** go plugin-ON +
  no-wrapper **if** the Bridge always sends a stable `X-Hermes-Session-Id` and #6
  re-derives the trace_id offline. Accept the version-coupling caveat, or patch
  the plugin to honor an inbound id for a true PASS.
- **FAIL (hard)** → neither inbound nor session-seed id matched. Keep the
  `langfuse.openai` wrapper (or patch the fork) for #6 correlation.
- **INCONCLUSIVE** → the plugin probably wrote nothing. Verify `langfuse` is on
  the **gateway's** `PYTHONPATH`, the plugin is enabled against the isolated
  `HERMES_HOME`, and `HERMES_LANGFUSE_*` point at the **same** project the probe
  reads.

## Notes & boundaries (honest)

- **Independent of the verdict:** `eval_sdk` owns the outcome scores (SR/SPL/
  collision/deadlock) regardless — Hermes never sees robot results. HLF-G0 only
  decides **which trace** those scores attach to (the Bridge's vs the plugin's).
- The probe constructs its Langfuse client with the **same** `HERMES_LANGFUSE_*`
  keys/base_url the plugin uses, so it reads the same project the plugin wrote to.
- **Verified build-time (this session, offline):** probe compiles under py3.12;
  `plugin_rederived_trace_id()` matches the real `langfuse 4.9.0`
  `create_trace_id()` for the plugin's seed recipe; the `trace.list` /
  `trace.get` signatures and the `usage` / `usage_details` /
  `calculated_total_cost` / `cost_details` / `total_cost` fields the probe reads
  all exist in 4.9.0.
- **Not verified (human-gated, by design):** the live HTTP 200 with the plugin
  ON, the actual Langfuse write, the real trace_id the plugin mints, and the
  usage/cost roll-up. Those require the running gateway + creds and are the
  point of the live run.
