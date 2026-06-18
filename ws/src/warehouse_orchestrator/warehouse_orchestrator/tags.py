"""Langfuse 観測 taxonomy — the single source for the Phase-4 comparison discriminators.

Project goal = compare Claude / GPT / Gemini / Grok as warehouse commanders. To tell the four
providers (and the three traffic modes) apart in Langfuse, each *turn* trace and each KPI
*score* carries the same discriminators. Those discriminators live in two different places
with two different owners — Langfuse v4 **scores carry no tags** (doc08:367):

* **trace** (owned by #4 Bridge, ``warehouse_llm_bridge/tracing.py``): per-turn ``name`` =
  ``turn`` int (doc08:329,340), ``session_id`` = ``run_{mode}_{provider}_{scenario}_{ts}``
  (doc08:383), ``langfuse_tags=[provider, mode]`` + ``gen_id`` metadata (doc08:377), id from
  ``create_trace_id(seed=f"{run_id}:{gen_id}")`` (doc13:516).
* **score** (owned by #6 wo, :func:`~warehouse_orchestrator.score_send.build_score_metadata`):
  no tags, so every label rides in the metadata ``{run_id, mode?, provider?, gen_id?}``
  (doc08:489) + ``robot`` per efficiency leg (doc08:369).

This module is the *vocabulary* both halves agree on — the metadata key names and the trace
tag list shape — so the two sides (and the tests) cannot drift. The full taxonomy reference
(which discriminator lives where, owner, and the Phase-4 query patterns) is consolidated in
``docs/architecture/20-dev-quality-and-testing.md`` §8; doc08/doc13 remain the authority and
doc20 is the cross-cutting map. Pure stdlib (no rclpy, no langfuse) → unit-testable, doc16 §11.

**Ownership note**: the *live* trace-tag emission is #4's — ``tracing.py`` attaches
``[provider, mode]`` (doc08:377). Cross-lane imports are forbidden (parallel-workflow §2.1), so
#4 does not import this module; :func:`provider_tags` is the taxonomy single-source + test
anchor — **inert**, mirroring the reserved ``SCORE_*`` names in :mod:`langfuse_sink`. wo itself
sends scores only (no tags), so it consumes :data:`TAG_KEYS`, not :func:`provider_tags`.
"""

# ── score metadata keys (doc08:489 {run_id, mode?, provider?, gen_id?}) ───────────────────
# build_score_metadata loads exactly these; centralised here so the trace side, the score side
# and the tests share one spelling (doc08:360,363 example / 採用実装 :369).
TAG_KEY_RUN_ID = "run_id"  # always present — trace-seed half f"{run_id}:{gen_id}" (#73 / doc13:519)
TAG_KEY_MODE = "mode"  # A/B/C ↔ traffic none/simple/open-rmf (open question — doc20 §8.4)
TAG_KEY_PROVIDER = "provider"  # claude/openai/google/xai — env WAREHOUSE_PROVIDER (doc08:367)
TAG_KEY_GEN_ID = "gen_id"  # the executed generation / per-cycle id (doc08:183 / doc13:519)

# The full optional-aware key set build_score_metadata assembles: ``run_id`` mandatory, the rest
# only when set. ``robot`` is NOT here — the efficiency leg adds it per-robot (doc08:369, below).
TAG_KEYS = (TAG_KEY_RUN_ID, TAG_KEY_MODE, TAG_KEY_PROVIDER, TAG_KEY_GEN_ID)

# Added per efficiency leg by the score send (doc08:369); also embedded in the score NAME on the
# metadata-less fallback (``efficiency_bot1``, doc08:367 / langfuse_sink._name_with_robot).
TAG_KEY_ROBOT = "robot"


def _clean(value: str | None) -> str | None:
    """Normalise a label: ``None`` / blank / whitespace-only → ``None``, else stripped.

    Matches the "empty/whitespace is unset" rule the score send already applies to ``provider``
    (``score_send.resolve_provider`` / doc08:367) so a stray blank never rides as an empty tag.
    """
    if value is None:
        return None
    return value.strip() or None


def provider_tags(provider: str | None, mode: str | None) -> list[str]:
    """The Langfuse **trace** tag list ``[provider, mode]`` (doc08:377), blanks omitted.

    Order is fixed — provider first, mode second — to match the Bridge's
    ``langfuse_tags=[provider, mode]`` (``warehouse_llm_bridge/tracing.py``; doc08:377). A
    ``None`` / blank entry is dropped so the list never carries an empty tag.

    **Taxonomy reference only.** wo emits *scores*, which carry no tags (doc08:367) — the live
    trace tags are emitted by #4. This function freezes the membership and ordering so they
    cannot drift between the trace side, the doc20 §8 taxonomy and the tests.
    """
    return [tag for tag in (_clean(provider), _clean(mode)) if tag is not None]
