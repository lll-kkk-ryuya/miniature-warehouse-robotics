# Tier-2 live allocation — synthetic-image ER (no pixel hints)

Proposed operator-approved **≤ 8-call** live batch that exercises the synthetic overhead image
(`gen_overhead_image.py`) instead of `--pixel-hints`, so ER does real perception and the plan
resolves through the frozen `dev-sim-v1` calibration (snap_radius 0.25 m). This is a **proposal**;
the live sends are executed later by the main/operator session, never by the implementer.

Everything below is OFFLINE-verified: `--selftest-image` proves the `data:image/png` part is
attached and byte-exact before a single cent is spent.

## Preconditions (0 charge)

1. Generate both frames: `python gen_overhead_image.py --out out/images`
   (positive ~13.5 KB, negative ~13.5 KB; base64 ≈ 18 KB ≈ 4.5 k tokens — well under 100 k).
2. `./run-live-matrix.sh --check` — Gemini key + gateway bearer present, ER gateway (8644) healthy.
3. Do **not** pass `--pixel-hints` (the image supersedes it; that is the whole point).
4. `FreshStateToolExecutor` is always wired in the harness (refreshes state before each dispatch):
   required because live ER latency (4–6 s observed) exceeds the Policy Gate freshness ceiling
   (2.0 s) — a snapshot taken before the ER call would reject `robot_unavailable`.

## Allocation (5 nominal + 3 reserve = 8 cap)

| # | variant (manifest) | image / mode | reps | live sends | why |
|---|---|---|---|---|
| 1 | `B_in` (variant_b, permissive zone) | `overhead_positive.png` / positive | 3 | 3 | Positive perception path through a **composed, plugin-bearing** run (closest to a real X-lite composition). Permissive `zone_policy` passes an in-zone plan, so a correctly-perceived red→blue plan is not blocked by the plugin — any non-dispatch is attributable to perception/snap, not composition. 3 reps captures ER pixel non-determinism (observed: pixels vary run-to-run). |
| 2 | `A` (variant_a, zero-plugin) | `overhead_negative.png` / negative | 2 | 2 | Fail-closed negative on the **baseline** (no plugins), so 0-dispatch is attributable to the Visual Resolver snap alone, not a plugin reject. 2 reps guards against a one-off model quirk. |
| — | reserve | — | — | 3 | The **only** possible extra send per cycle is a single `hermes`→`direct` fallback (gemini_er.py:231-243) — `HttpErTransportSender.send` has **no retry loop**, and that one extra send **is** counted by `BudgetedSender`. Reserve absorbs fallbacks under the hard cap. |

Budget math (exact): per-rep nominal cost = **1 live send** (cycle 1 is the only live call; cycle 2
replays the cached envelope via `CachingAdapter`). Nominal = 3 + 2 = **5 sends**. The one extra send
each cycle can incur is a `hermes`→`direct` fallback (counted). Worst case (every nominal send falls
back) would want 10, but the cap **clamps at 8** — the reserve is conservative in the safe direction.
Two independent `BudgetedSender`s (caps **5** and **3**) give a combined hard ceiling of **8**. The
cap is a hard **STOP**, not a completion guarantee: if fallbacks exhaust the ledger the matrix halts
with partial results (never over-spends). Per-call image cost is bounded by `MAX_IMAGE_BYTES`
(128 KiB ≈ ~43 k tokens; the generated frames are ~13.5 KB ≈ ~4.5 k tokens).

## Exact commands (two invocations, separate budget ledgers)

```bash
# Positive x3 (B_in), hard cap 5 (3 nominal + 2 fallback headroom)
MWR_LIVE_BUDGET=5 ./run-live-matrix.sh \
  --variants B_in --reps 3 \
  --image out/images/overhead_positive.png --image-mode positive

# Negative x2 (A), hard cap 3 (2 nominal + 1 fallback headroom)
MWR_LIVE_BUDGET=3 ./run-live-matrix.sh \
  --variants A --reps 2 \
  --image out/images/overhead_negative.png --image-mode negative
```

`MWR_LIVE_BUDGET` only NARROWS the cap (runner clamps ≤ 12; harness `APPROVED_CAP` refuses > 12).
Combined hard cap across the two invocations = **8**.

## Pass criteria (assertion tier)

- **Positive (B_in):** PASS if the cycle **dispatches** (destinations ∈ KNOWN_LOCATIONS, action
  NAVIGATE — ideally `shelf_1` then `shelf_2`) **OR** records `empty_command`. Dispatch is the
  success signal; `empty_command` is recorded honestly (the `image_outcome` JSONL row logs which,
  with the resolved destinations). Existing per-variant invariants are unchanged.
- **Negative (A):** PASS **only if 0 dispatch and 0 commit** on both cycles (fail-closed). Any
  dispatch is a real regression and fails the rep.

## Caveats / residuals (honest)

- The negative test assumes ER **grounds detections in the image**. If the model instead invents
  plausible shelf pixels from the transcript ("bot1→red box, bot2→blue box") while ignoring the
  far-placed boxes, a negative rep could dispatch and FAIL — that failure is itself a genuine
  finding (model not image-grounding), not a harness bug. Record it as observed.
- Positive dispatch is **not guaranteed**: ER may still emit pixels the resolver cannot snap even
  with the image (perception is stochastic). That is why `empty_command` is an accepted positive
  outcome; the metric of interest is dispatch *rate* across 3 reps vs the text-only baseline.
- The synthetic image is a clean top-down render (flat floor + grid + two solid boxes), not a
  photoreal camera frame; it validates the wire + calibration geometry, not photoreal robustness.
- A `--image` / `--image-mode` mismatch (e.g. positive frame with `--image-mode negative`) prints a
  **soft, non-blocking WARN** (harness compares the filename stem to the mode). It is advisory only —
  it does not block the run — so double-check the pairing when the WARN appears.
- **No live sends are made by this change.** Positive dispatch and negative 0-dispatch are asserted
  in code but only exercised when the operator runs the commands above with the cost gate armed.
