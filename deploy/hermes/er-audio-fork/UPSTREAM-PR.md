# UPSTREAM PR (PREPARED — NOT SUBMITTED)

> **STATUS: NOT submitted.** Submitting a PR to an external repo (`NousResearch/hermes-agent`)
> is an outward action that requires explicit user approval. This file is a ready-to-file draft
> only. Filing it, and any subsequent upstream merge, would **retire the local deploy overlay**
> (the 2-file fork below), so it must not happen automatically.
>
> Target repo: `NousResearch/hermes-agent`
> Base revision this patch was authored against: Hermes `v0.15.1` (`v2026.5.29-692-gbd12b3c23`,
> commit `bd12b3c23`), verified live 2026-06-27.
> Patch artifact: [`0001-input_audio-passthrough.patch`](./0001-input_audio-passthrough.patch)
> (same directory as this file).
>
> Provenance / design source of truth: PR #355 (docs) · issue #356 ·
> `docs/mode-x-er/06-unfrozen-contract-resolutions.md` §5 + §5 補遺.

---

## Title

`feat(gateway): accept OpenAI input_audio content parts and pass them through to the Gemini-native adapter as inlineData`

---

## Problem statement

The OpenAI-compatible endpoint `POST /v1/chat/completions` rejects audio content parts.
When a request carries a content part of type `input_audio`:

```jsonc
"content": [
  {"type": "text", "input_text": "..."},
  {"type": "input_audio", "input_audio": {"data": "<base64>", "format": "wav"}}
]
```

the gateway's content normalizer treats it as an unsupported part and the request fails with
**HTTP 400 `unsupported_content_type`** — the same path that already rejects non-image `data:`
URLs and uploaded files.

However, the underlying Gemini-native transport can accept audio natively. Google's
`gemini-robotics-er-1.6-preview` lists **audio** among its supported inputs (Gemini API
"robotics-overview" docs, retrieved 2026-06-25), and the Gemini REST `generateContent` schema
accepts inline audio via `inline_data { mime_type: "audio/wav", data: <base64> }`. So the
modality is available at the provider; it is only the gateway's content normalizer and the
Gemini-native adapter that drop it before it reaches the model.

Net effect today: callers who want to send native audio to a Gemini-native model through the
Hermes OpenAI-compatible surface cannot — they get a hard 400 — even though the provider would
accept the same audio.

---

## Solution

A small, additive, **transport/input-layer-only** passthrough across two files. It adds
`input_audio` as a recognized content-part type and maps it to the Gemini-native `inlineData`
shape. It does not introduce a new schema — both `input_audio` (OpenAI side) and `inlineData`
(Gemini side) are existing external API shapes.

### 1. `gateway/platforms/api_server.py`

- New part-type set: `_AUDIO_PART_TYPES = frozenset({"input_audio"})`.
- `_normalize_multimodal_content(...)`: recognize `input_audio` parts, validate that an
  `input_audio` object with non-empty base64 `data` is present (raising a clear
  `invalid_content_part:...` `ValueError` otherwise), default `format` to `wav`, and emit a
  normalized `{"type": "input_audio", "input_audio": {"data": ..., "format": ...}}` part —
  instead of falling through to the `unsupported_content_type` rejection.
- `_content_has_visible_payload(...)`: count an `input_audio` part as a visible payload, so an
  audio-only (or text+audio) message is not treated as empty.

### 2. `agent/gemini_native_adapter.py`

- `_extract_multimodal_parts(...)`: for `ptype == "input_audio"`, read `data`/`format` and
  append a Gemini-native part `{"inlineData": {"mimeType": "audio/<format>", "data": <base64>}}`
  (format normalized to lower-case, default `wav`). Malformed/empty audio is skipped rather than
  emitted.

The full diff is in [`0001-input_audio-passthrough.patch`](./0001-input_audio-passthrough.patch)
(paths `a/gateway/platforms/api_server.py` and `a/agent/gemini_native_adapter.py`).

---

## Scope / non-goals

- **In scope:** Gemini-native transport only. The change adds an **input modality** (audio) to
  the OpenAI-compatible surface for requests routed to the Gemini-native adapter.
- **Non-goals:**
  - No other provider/transport is touched. Adapters that do not implement audio passthrough are
    unaffected; their behavior for `input_audio` is unchanged by the adapter-side hunk.
  - No orchestration, routing, or safety logic is changed. This is strictly the
    transport/input layer.
  - No new request/response schema is invented; both `input_audio` and `inlineData` are
    pre-existing external shapes.

---

## Backward compatibility

**Additive and backward-compatible.** Before this change, an `input_audio` content part was a
**hard 400 `unsupported_content_type`** — i.e. there was no prior accepted behavior to break.
This change turns that previously-rejected input into an accepted one for the Gemini-native path.

- Existing `text` / `image_url` / `input_image` handling is unchanged.
- Existing rejection of non-image `data:` URLs and uploaded `file`/`input_file` parts is
  unchanged.
- No existing field is removed, renamed, or re-typed.

---

## Test note

Verified **live on 2026-06-27** against an isolated source worktree of the Hermes clone (base
`bd12b3c23`) with a lean Gemini-native gateway (provider `google`, model
`gemini-robotics-er-1.6-preview`, zero tools, memory off):

- `POST /v1/chat/completions` with an `input_audio` content part → **HTTP 200** (previously 400).
- The model understood the **native audio**: the response reflected the spoken words contained
  only in the audio clip (no transcript was provided in the text part).
- Observed cost of carrying the audio: **≈ +408 prompt tokens per call**.
- Latency was comparable to the direct Gemini path in a small sample (lean gateway median
  **3.69 s** vs direct **4.24 s**, n=4; the difference is within noise and confounded by
  ER "thinking" time — not presented as a benchmark).

This was an isolated-worktree verification, not a change to any production install, and not run
against this repo's CI. **No upstream CI results are claimed here.**

---

## Reproduction (isolated, non-destructive)

The change can be exercised without modifying any in-place install. Create an **isolated source
worktree** of a Hermes clone, apply the patch there, and run the gateway against that worktree
via `PYTHONPATH` (so the patched modules win over the editable install while reusing the existing
venv's dependencies):

```sh
# never modify the clone in place — only an isolated worktree of it
git -C <HERMES_CLONE> worktree add <WORKTREE_DIR> -b er-audio-fork HEAD
git -C <WORKTREE_DIR> apply <THIS_DIR>/0001-input_audio-passthrough.patch
PYTHONPATH=<WORKTREE_DIR> <HERMES_CLONE>/venv/bin/python \
  -m hermes_cli.main gateway run --accept-hooks
```

See `apply-fork.sh` / `run-er-gateway.sh` in this directory for the parameterized, idempotent
form of the above (they refuse to touch any clone in place and never print secrets).

---

## Relationship to the downstream project (context for reviewers)

This patch originates from the Miniature Warehouse Robotics (MWR) "Mode X-ER" line of work, where
Gemini Robotics-ER is evaluated as an embodied-reasoning commander. The fork is purely the
**transport/input layer**: it adds an audio input modality and **does not** alter any of the
downstream safety/orchestration invariants — `action_map` idempotency minting, the Policy Gate,
0-dispatch-on-timeout, or the `eval_sdk` outcome scores (result / SR / SPL / collision /
deadlock).

Note for context: Hermes serves a **single server-side active model** (no per-request provider
routing), so a multi-provider comparison still requires one gateway per provider (separate config
+ restart). That is orthogonal to this patch.

**Downstream consequence of upstreaming:** in the MWR project this passthrough is the
productionization that enables "default = Hermes for audio" (the target stated in issue #356 and
`docs/mode-x-er/06-unfrozen-contract-resolutions.md` §5 補遺). Until such a change is deployed,
the downstream audio path remains **direct Gemini REST** as the permanent fallback. If this PR
were accepted upstream, the downstream **local deploy overlay (this 2-file fork) would be
retired** in favor of the upstream implementation — which is exactly why submission is gated on
explicit user approval and is **not** performed automatically.
