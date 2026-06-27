# ER Audio Leg — Transport-Flip Implementation PLAN (design only)

> **Status: PLAN / design only. NO code is written by this document on any branch.**
> The actual adapter code change lands on `feat/mode-x-er` (a separate branch); this file lives
> in the fork-productization package (`deploy/hermes/er-audio-fork/`) and specifies *what* the
> change must be and *what it depends on*. Mark of honesty: every "TARGET" below is the
> post-deploy goal, not current behavior.
>
> **Worktree tag (for any future PR built from this plan):**
> `[worktree: mwr-hermes-er-fork | branch: feat/hermes-er-audio-fork | track: #356]`
>
> **設計正本（docs-first source of truth):**
> - `docs/mode-x-er/06-unfrozen-contract-resolutions.md` §5 + §5 補遺（実測結果 2026-06-26）
>   — PR #355 (docs). Read on `origin/feat/mode-x-er`.
> - issue #356 (productionize the forked input_audio gateway).
> - `docs/mode-x-er/04-er-input-modalities-and-stt.md` (audio modality / STT out-of-band).

---

## 0. One-paragraph summary

Today the ER **audio** leg is hard-wired to `direct` (Gemini REST `inline_data`) because vanilla
Hermes v0.15.1 returns `400 unsupported_content_type` for `input_audio` content parts
(`docs/mode-x-er/06-unfrozen-contract-resolutions.md:159`, PROBE-2 ✅否定確定). The 2-file fork in
this directory (`0001-input_audio-passthrough.patch`) makes a **forked** Hermes gateway accept
OpenAI `input_audio` parts and map them to Gemini `inlineData{mimeType:audio/wav}`. **This plan
flips the ER audio leg's default transport to `hermes` *only when* a forked input_audio gateway
is configured, and keeps `direct` as the permanent fallback** (per PR #355 / doc06 §5 補遺:162
"音声 = direct ER … or direct … transport 非依存"). The flip is a *transport/input-layer* change
only — it never touches orchestration, the Policy Gate, `action_map` idempotency mint,
0-dispatch-on-timeout, or `eval_sdk` outcome scores.

---

## 1. Current state — verified by reading `origin/feat/mode-x-er`

All citations below were read live via `git show origin/feat/mode-x-er:<path>` on 2026-06-27.

| Fact | Evidence (file:line / symbol on `feat/mode-x-er`) |
|---|---|
| ER adapter default transport = `DIRECT` | `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/adapters/gemini_er.py:53` → `transport: Transport = Transport.DIRECT` in `GeminiErAdapter.__init__` |
| ER adapter has **no live transport at all** yet | `gemini_er.py:60-66` — `propose_plan` raises `NotImplementedError("live Gemini Robotics-ER transport is deferred to #344 …")` unless an `offline_payload` is injected. The class only stamps `transport=self._transport.value` onto `RawModelOutput` (`gemini_er.py:70-75`); it does **not** open any socket. |
| `Transport` is an **observation-only audit tag, NOT a branch key** | `ws/src/.../robotics/adapters/enums.py` module docstring + `class Transport(StrEnum)`: `HERMES`/`DIRECT`/`WORKER`. Docstring: "NEVER used as an execution-branch key" (mirrors `03-er-adapter-skeleton.md:75`). |
| L4 *owns* transport selection (so the flip belongs in L4, not L3) | `ws/src/.../robotics/__init__.py:3-4` — "L4 owns input context, **transport selection**, timeout, trace and the L3 handoff — but NOT model judgement, execution permission…". |
| Handoff normalization is **envelope-shape driven, transport-agnostic** | `ws/src/.../robotics_planning_core/handoff.py:57-66` `extract_plan_content`: `"choices" in payload` → OpenAI/Hermes branch; `"candidates" in payload` → Gemini direct branch. `RawModelOutput.transport` does **not** branch normalization (`handoff.py:6-8`). |
| Current frozen audio decision = direct | `docs/mode-x-er/06-unfrozen-contract-resolutions.md:159,162` — PROBE-2 否定確定 → "Hermes は音声を運べない＝音声 direct 固定"; two-path: 音声 = direct ER (Hermes 不可). |
| Bridge already reads a Hermes base_url from config | `ws/src/.../warehouse_llm_bridge/llm_bridge.py:78` `DEFAULT_HERMES_BASE_URL = "http://localhost:8642"`; `:101` `base_url = hermes.get("base_url") or DEFAULT_HERMES_BASE_URL`. |
| **No ER-gateway / `mode_x_er` config key exists yet** | `git grep -nE 'mode_x_er|MWR_ER|er.*gateway' origin/feat/mode-x-er -- ws/src config` → zero hits. The forked-gateway config (§3) is **net-new**. `config/warehouse.base.yaml:50-51` has only the generic `hermes:` block. |
| Live test file already exists (extend, don't create) | `tests/live/test_er_handoff_live.py` — already has direct / OpenAI-compat / dedicated-gateway / **audio-direct** / two-lane probes. **None asserts the forked-Hermes `input_audio` path** (the gap §4 fills). |

> **Path-existence check (as requested):** both `gemini_er.py` and `enums.py` **DO exist** on
> `feat/mode-x-er` at the paths given. `tests/live/test_er_handoff_live.py` **DOES exist** (at
> repo root, not under the package). No requested path was missing.

> **Critical design constraint that shapes this whole plan:** because `Transport` is audit-only
> (`enums.py`) and `propose_plan` is still offline-only (`gemini_er.py:60`), "flip the default to
> `transport=hermes`" is **NOT** a one-line enum-default edit. The enum default only changes the
> *Langfuse tag*. The real behavioral change is **which wire the live audio leg uses** — and that
> live wire does not exist yet (it is the `#344` transport seam). This plan therefore specifies
> the change at the layer that will own it (L4 transport selection), and is explicit that it
> *co-lands with or after* the `#344` live-transport seam (§5 dependencies).

---

## 2. The change — flip default to `hermes` WHEN a forked gateway is configured, else `direct`

### 2.1 Where the flip lives (L4 transport selection, not the enum default)

The selection is a **resolver in L4**, evaluated when the live ER transport seam (`#344`) is
wired into `GeminiErAdapter.propose_plan`. The enum default in `gemini_er.py:53` stays
`Transport.DIRECT` as the *safe audit default*; the resolver overrides it for the audio leg only
when a forked gateway is configured and healthy.

Proposed resolver (pseudocode — **design only, not committed**):

```
# L4 transport selection (robotics/__init__.py:3 — "L4 owns transport selection")
def resolve_audio_transport(cfg: ErGatewayConfig) -> Transport:
    # default = DIRECT (gemini_er.py:53 audit default; doc06 §5 補遺:162 permanent fallback)
    if cfg.forked_audio_gateway_configured:   # base_url set AND fork capability declared (§3)
        return Transport.HERMES               # TARGET (doc06 §5:148 "凍結方針(probe 後・additive)")
    return Transport.DIRECT                    # permanent fallback (PR #355)
```

Rules the resolver must obey (all docs-grounded, nothing invented):

1. **Audio leg only.** Image/text already have a lean Hermes path
   (`06:162` "text+image = lean Hermes gateway … or direct"). This plan changes the **audio**
   leg specifically; image/text selection is unchanged.
2. **`direct` is a permanent fallback, not a transitional one** (PR #355). The resolver returns
   `DIRECT` whenever the forked gateway is **not configured**. A future hardening pass MAY also
   fall back to `DIRECT` on a forked-gateway health-check failure, but **that runtime failover is
   NOT frozen by this plan** — mark it `(発明/要確定)` until a doc PR specifies the health probe.
   The MVP behavior is: *configured ⇒ `hermes`; not configured ⇒ `direct`.*
3. **additive-only.** Adding `HERMES` as the audio default value does not remove or rename
   `DIRECT`; existing direct-audio behavior is reachable by leaving the gateway unconfigured
   (`06:150` "additive 互換 … 既存 direct image 経路を壊さない"; `06:148`).
4. **`Transport` stays observation-only** (`enums.py`). The resolver's output is used to (a) pick
   the wire in the live seam and (b) stamp the Langfuse audit tag — **never** as a key that
   branches Policy-Gate / safety / handoff logic (`enums.py` docstring; `03:75`). Handoff stays
   envelope-driven (`handoff.py:57-66`): a forked-Hermes audio response returns a `choices`
   envelope and flows through the **existing** OpenAI/Hermes branch with no handoff change.

### 2.2 What does NOT change (load-bearing invariants — assert in tests, never edit)

- `action_map` idempotency mint, Policy Gate, 0-dispatch-on-timeout — untouched (fork is
  transport/input only; `apply-fork.sh` WHAT/WHY header; `06` §5 補遺:162 "観測は本線外・fail-open").
- `eval_sdk` outcome scores (result / SR / SPL / collision / deadlock) — untouched.
- `warehouse_interfaces` — **not touched** (`06`:51,70 ErTaskRequest/Transport stay bridge-local;
  `06`:64 "warehouse_interfaces に入れない"). No `contract` label is required for the audio-flip
  itself; the `transport` enum promotion is a *separate, later* contract PR (`06`:152 step 3 /
  `06`:208 roadmap).

---

## 3. Where to read the forked-gateway config

**Net-new** (verified zero existing keys, §1). The config must answer two questions the resolver
asks: (a) *is a forked audio gateway configured?* and (b) *how do I reach it?* It reuses the
existing `hermes:` config-block pattern (`config/warehouse.base.yaml:50-51`,
`llm_bridge.py:101` `hermes.get("base_url")`) rather than inventing a parallel mechanism.

### 3.1 Proposed config shape `(発明/要確定)` — schema is NOT frozen by this plan

```yaml
# config/<env>/warehouse.yaml  (overlay; base stays env-agnostic per environments.md)
robotics:                 # Mode X-ER L4 sub-tree (bridge-local; NOT warehouse_interfaces)
  er_gateway:
    # When set + audio_input_audio_supported: true, the ER AUDIO leg defaults to transport=hermes.
    # When absent/empty, audio stays direct (permanent fallback, PR #355).
    base_url: ""                       # e.g. http://127.0.0.1:8644 (lean ER gateway, .env.example PORT)
    audio_input_audio_supported: false # the FORK CAPABILITY FLAG: true only for a gateway that
                                       # ran 0001-input_audio-passthrough.patch (this dir). Unforked
                                       # Hermes = false (it returns 400; doc06 §5:159).
```

Reading rules (docs-first, no hardcoding — `environments.md`):

1. The bridge reads this from config (`base + overlay + env`), **never** hardcodes the endpoint or
   the capability flag (`environments.md` 必須: "接続先 URL … はすべて config から読む").
2. The auth token for the forked gateway is the gateway's `API_SERVER_KEY` (the bridge sends
   `Authorization: Bearer <API_SERVER_KEY>`; `.env.example` of this dir). It lives in
   `config/<env>/.env` / the gateway's isolated `HERMES_HOME/.env` — **never** committed, never
   echoed (`safety.md`, `environments.md`).
3. **`audio_input_audio_supported` is the productionization gate.** It exists precisely because
   "Hermes" alone is not enough — only a *forked* gateway carries audio. Setting it `true` against
   an unforked gateway is operator error that would yield `400`; the resolver MUST treat
   absent/false as `direct` (fail-safe to the permanent fallback).

> **Honesty marker:** this config sub-tree (`robotics.er_gateway.*`) does **not** exist on
> `feat/mode-x-er` today (§1 grep). It is proposed here and must be added by the implementing PR;
> the key names are `(発明/要確定)` until that PR (the *values* — base_url, bearer auth — are
> docs/`.env.example`-grounded, the *block naming* is the invented packaging).

---

## 4. Live test addition (`tests/live/test_er_handoff_live.py` — extend, don't create)

The existing file already proves audio-direct (`test_live_er_audio_direct_flows_through_l3_handoff`)
and the dedicated-gateway text path (`test_live_er_via_hermes_gateway_flows_through_l3_handoff`).
**The gap = no test sends `input_audio` through a *forked* Hermes gateway.** Add **one** env-gated
function in the same module, following the existing skip/gate idiom exactly.

### 4.1 New function: `test_live_er_audio_via_forked_hermes_gateway`

- **Gate (additive to module skip):** only runs when the forked-gateway envs are present, so it
  never runs in CI/unit (module already top-skips unless `WAREHOUSE_LIVE_ER=1`):
  - `MWR_ER_FORK_GATEWAY_URL` — base_url of the **forked** lean ER gateway (e.g. `http://127.0.0.1:8644`).
  - `MWR_ER_FORK_GATEWAY_KEY` (or reuse `HERMES_API_KEY` / `API_SERVER_KEY`) — the gateway bearer.
  - `MWR_ER_AUDIO` — a spoken-instruction wav (same env the audio-direct probe already uses).
  - `pytest.skip(...)` with an actionable message when any is missing (match the existing
    `test_live_er_via_hermes_gateway_flows_through_l3_handoff` skip pattern).
- **Action:** `POST <url>/v1/chat/completions` with an OpenAI body whose `content` array carries
  a `{"type":"input_audio","input_audio":{"data":<base64 wav>,"format":"wav"}}` part (the exact
  shape PROBE-2 used, `06`:146; the shape the patch parses, `0001-input_audio-passthrough.patch`
  `_AUDIO_PART_TYPES`). Bearer auth via `Authorization: Bearer <key>`.
- **Assertions (3, matching the task ask):**
  1. **HTTP 200** — proves the forked gateway *accepted* `input_audio` (vs the unforked `400`;
     `06`:159). On `400`/`HTTPError`, `pytest.fail` with the body prefix (≤300 chars, no secrets).
  2. **Native audio understanding** — feed the `choices` envelope into
     `to_robotics_plan_draft(RawModelOutput(transport="hermes", provider="er", source_model=MODEL,
     payload=response))` and assert a valid `RoboticsPlanDraft` with a non-empty `task_graph`,
     plus `draft.transcript` reflects the spoken words (the audio-direct probe already asserts the
     spoken transcript content; mirror it). This proves the gateway forwarded audio to Gemini
     *natively* and ER understood it — not STT.
  3. **Latency** — measure wall-clock around the POST and `print` the median over N calls
     (`-s`, `capsys.disabled()`), as a **report line, not a hard threshold** (the live result
     3.69s lean vs 4.24s direct has an ER-thinking confound, GROUNDED FACTS / `06` notes
     observation is out-of-band). If a regression guard is wanted later, gate it behind a separate
     opt-in env so a slow network never red-fails the probe — mark `(発明/要確定)`.
- **Invariant assertions (reuse existing transport-equivalence intent):** assert the draft from
  the forked-Hermes audio leg is **structurally equivalent** to the audio-direct draft for the
  same clip (same `task_graph` shape) — this is the "transport 非依存" guarantee (`06`:162,
  `handoff.py:6-8`). Transport differs only in the audit tag (`hermes` vs `direct`), proving the
  flip is observation-only at the handoff boundary.
- **Secrets discipline:** never print the bearer or API key; print only model id, token counts,
  latency, transcript prefix (the existing functions' `capsys.disabled()` summaries are the model
  to copy).

> **Honesty marker:** this test is **NOT yet written/verified**. The forked-gateway audio path was
> verified manually (GROUNDED FACTS: POST input_audio → 200, native understanding, ~3.69s lean
> median, +~408 prompt tokens/call) but is **not yet captured as a committed pytest**. Writing it
> is part of the implementing PR on `feat/mode-x-er`, gated on §5 dependency (a forked gateway
> deployed).

---

## 5. Dependency — forked gateway deployed (this package)

The flip's hard prerequisite is **a deployed forked input_audio gateway** — owned by *this*
package (`deploy/hermes/er-audio-fork/`). Until that gateway is running and its config flag is
set, the resolver (§2.1) returns `direct` and nothing in `feat/mode-x-er` changes behavior.

Productionization chain (what "deployed" means, reusing the **verified run mechanics** — invent
no new mechanism):

1. **Isolated source worktree of the personal clone** (NEVER touch `~/.hermes/hermes-agent` in
   place — ABSOLUTE RULE):
   `git -C ~/.hermes/hermes-agent worktree add <DIR> -b <BRANCH> HEAD`
   (no venv/node_modules; gitignored). This package's `apply-fork.sh` already **refuses** if
   `HERMES_SRC` resolves to the personal clone.
2. **Apply the patch** into that worktree: `HERMES_SRC=<DIR> ./apply-fork.sh` (idempotent;
   `--check` dry-run; `--revert` to reverse). The applied marker is `_AUDIO_PART_TYPES`.
3. **Run the lean ER gateway** from the patched worktree **reusing the personal venv** without
   touching personal source (PYTHONPATH override is proven to load the patched modules):
   `PYTHONPATH=<DIR> ~/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway run --accept-hooks`
   with the lean config (`HERMES_HOME=~/.hermes-mwr-er-lean`: provider `google`, model
   `gemini-robotics-er-1.6-preview`, `api_server` port 8644, memory off,
   `platform_toolsets.api_server: []`). Secrets are **sourced** from `$HERMES_HOME/.env`
   (`GOOGLE_API_KEY` + `API_SERVER_KEY` + `API_SERVER_HOST/PORT`), never echoed.
4. **Point the bridge config** `robotics.er_gateway.base_url` at that gateway (§3) and set
   `audio_input_audio_supported: true`.

A **deployment runbook script** (`run-er-gateway.sh`, referenced by `.env.example`) is the
parameterized, idempotent entrypoint for steps 1–3. **Honesty marker:** as of this plan the dir
contains `apply-fork.sh` / patch / `.env.example` / `UPSTREAM-PR.md`; a
single end-to-end `run-er-gateway.sh` that creates the worktree, applies, and launches with the
lean config is the **remaining deploy artifact** to ship in this package (mark NOT-yet-shipped).
It must: `set -euo pipefail`; be idempotent; be parameterized via env (`HERMES_SRC`,
`HERMES_HOME`, `API_SERVER_PORT` with sane defaults); **refuse to touch `~/.hermes/hermes-agent`
in place**; **source** (never print) `$HERMES_HOME/.env`; and carry a usage header.

Note (4-provider comparison): Hermes is a **single server-side active model** (no per-request
provider routing, GROUNDED FACTS / `06`:160a). The ER forked gateway is therefore a *dedicated*
gateway; comparing 4 providers needs per-provider gateways (config + restart) — out of scope for
the audio flip, recorded so the dependency isn't mistaken for "any Hermes will do".

---

## 6. DoD (完了ゲート) — for the implementing PR on `feat/mode-x-er`

A PR built from this plan is "done" only when **all** hold (docs-first close-gate,
`parallel-workflow.md §1.1` / `docs-first.md`):

1. **Resolver + config wired** (§2, §3): audio leg defaults to `hermes` iff
   `robotics.er_gateway` configured with `audio_input_audio_supported: true`; else `direct`.
   `direct` reachable as permanent fallback (config absent). `Transport` stays observation-only
   (no safety/Policy-Gate/handoff branch keyed on it).
2. **Invariants untouched** (§2.2): `action_map` mint / Policy Gate / 0-dispatch-on-timeout /
   `eval_sdk` scores / `warehouse_interfaces` unchanged. **No `contract` label** needed for the
   audio flip (transport enum promotion is a *separate* later PR, `06`:152,208).
3. **Live test added** (§4): one env-gated `test_live_er_audio_via_forked_hermes_gateway` asserting
   HTTP 200 + native-audio `RoboticsPlanDraft` (non-empty `task_graph`, spoken transcript) +
   latency report line + transport-equivalence vs audio-direct. Skips cleanly without forked-
   gateway envs; never runs in CI; never prints secrets.
4. **Forked gateway deployable** (§5): `apply-fork.sh --check` clean against an isolated worktree;
   the deploy runbook reuses PYTHONPATH-override + personal venv and refuses the personal clone.
5. **docs-first close-gate**:
   - Update `docs/mode-x-er/06 §5 補遺` so the "音声 = direct ER (Hermes 不可)" line records the
     productionized exception: *forked* gateway carries audio (`hermes`), unforked stays `400`
     (`06`:159 stays true for *unforked*). Keep "transport 非依存 → 同一 RoboticsPlanDraft"
     (`06`:162). Cite this package.
   - Record produce/consume in `ws/src/warehouse_llm_bridge/CLAUDE.md` (new `robotics.er_gateway`
     config consume; audio transport selection produce).
   - `python3 scripts/check_consistency.py` → **0 ERROR**.
   - `/consistency-audit` for semantic / cross-doc drift (esp. `06` §5 line refs after edit; the
     `tests/live` file:line refs in `06`:156).
   - `colcon build` green; safety units (R-26) green; PR flow ①submit → ②CI green → ③separate-step
     merge (no same-turn self-merge).
   - List any remaining `(発明/要確定)` openly in the PR body: `robotics.er_gateway.*` key names;
     runtime health-failover to `direct`; latency regression threshold.

---

## 7. Open items / honest "not-yet" list

| Item | State |
|---|---|
| Forked-Hermes audio path captured as committed pytest | NOT yet (manually verified only — §4 honesty marker) |
| `robotics.er_gateway.*` config block | Net-new, NOT on `feat/mode-x-er` (§1 grep); key names `(発明/要確定)` |
| Live ER transport seam in `gemini_er.propose_plan` | NOT yet — deferred to `#344` (`gemini_er.py:60`); the flip co-lands with / after it |
| One-shot `run-er-gateway.sh` deploy entrypoint | NOT yet shipped in this dir (§5) — only `apply-fork.sh` / patch / `.env.example` / `UPSTREAM-PR.md` present |
| Runtime failover `hermes → direct` on health failure | NOT frozen by this plan (§2.1 rule 2) `(発明/要確定)` |
| Latency hard threshold | Report-only for now; confound noted (GROUNDED FACTS) |
| `transport` enum promotion to a contract | Separate later contract PR, not this one (`06`:152 step 3, `06`:208 roadmap) |

---

## References (all read live on `origin/feat/mode-x-er`, 2026-06-27)

- `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/adapters/gemini_er.py:53,60-66,70-75`
- `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/adapters/enums.py` (`class Transport`)
- `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/__init__.py:3-4` (L4 owns transport selection)
- `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/transcription.py:4-6` (audio direct; Hermes can't carry audio — 400)
- `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics_planning_core/handoff.py:6-8,57-66` (envelope-driven, transport-agnostic)
- `ws/src/warehouse_llm_bridge/warehouse_llm_bridge/llm_bridge.py:78,101` (config-driven Hermes base_url)
- `docs/mode-x-er/06-unfrozen-contract-resolutions.md:135-162` (§5 + §5 補遺: PROBE-1/2/3 results, two-path decision, additive)
- `tests/live/test_er_handoff_live.py` (existing audio-direct / dedicated-gateway probes to mirror)
- This package: `0001-input_audio-passthrough.patch` (`_AUDIO_PART_TYPES`), `apply-fork.sh`, `.env.example` (port 8644, `~/.hermes-mwr-er-lean`)
- PR #355 (docs) / issue #356.
