"""Seed the commander system prompts into Langfuse Prompt Management (idempotent upsert).

One-time / repeatable upsert so the commander prompts are MANAGED + versioned in Langfuse
(doc08 §Langfuse Prompt Management 方針) rather than hardcoded. The prompt TEXT comes from
:func:`warehouse_llm_bridge.prompts.commander_fallback_text` — the SAME source the runtime
falls back to — so the seeded prompt and the code fallback cannot drift (guarded by
``tests/unit/test_prompts.py``).

Default is a DRY-RUN (print what would be upserted, no network). A real upsert (``--commit``)
needs ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` / ``LANGFUSE_HOST`` (config/<env>/.env,
commit-forbidden per safety.md) and is a human-run step — live verification is Phase 3 #88
(doc13 §7.5 ④).

Usage::

    python -m warehouse_llm_bridge.seed_prompts            # dry-run (default): print only
    python -m warehouse_llm_bridge.seed_prompts --commit   # real upsert to Langfuse
"""

import argparse
import sys

from warehouse_llm_bridge.prompts import (
    DEFAULT_PROMPT_LABEL,
    PROMPT_NAME_MODE_AB,
    PROMPT_NAME_MODE_C,
    commander_fallback_text,
)

# Stored alongside each prompt version (Langfuse ``config`` dict). Hermes routes the provider
# server-side (``model="hermes-agent"``, doc13:171), so these are advisory metadata travelling
# WITH the prompt version — NOT an enforced runtime model/temperature selection.
_PROMPT_CONFIG = {"model": "hermes-agent", "temperature": 0.3}


def seed_specs() -> list[dict]:
    """The prompts to upsert. Text is the code fallback (= doc08a/08c source) so seed==fallback.

    Mode A/B (``none``) composes base+rules; Mode C (``open-rmf``) is the standalone prompt
    (doc08 §Langfuse Prompt Management 方針). Each is labelled ``production`` (the version pin
    compared runs share, doc08 §比較検証ログ).
    """
    return [
        {
            "name": PROMPT_NAME_MODE_AB,
            "prompt": commander_fallback_text("none"),
            "labels": [DEFAULT_PROMPT_LABEL],
            "config": _PROMPT_CONFIG,
        },
        {
            "name": PROMPT_NAME_MODE_C,
            "prompt": commander_fallback_text("open-rmf"),
            "labels": [DEFAULT_PROMPT_LABEL],
            "config": _PROMPT_CONFIG,
        },
    ]


def _print_dry_run(specs: list[dict]) -> None:
    for spec in specs:
        print(f"# === {spec['name']} (labels={spec['labels']}, config={spec['config']}) ===")
        print(spec["prompt"])
        print()
    print(f"# DRY-RUN: {len(specs)} prompt(s) would be upserted. Re-run with --commit to apply.")


def seed(commit: bool) -> int:
    """Print the prompts (dry-run, default) or upsert them to Langfuse (``commit=True``)."""
    specs = seed_specs()
    if not commit:
        _print_dry_run(specs)
        return 0
    try:
        from langfuse import get_client
    except ImportError as exc:  # pragma: no cover - exercised only with --commit + no extra
        print(f"langfuse not installed (needed for --commit): {exc}", file=sys.stderr)
        return 2
    client = get_client()
    try:
        for spec in specs:
            client.create_prompt(
                name=spec["name"],
                type="text",
                prompt=spec["prompt"],
                labels=spec["labels"],
                config=spec["config"],
            )
            print(f"upserted prompt {spec['name']!r} (labels={spec['labels']})")
    finally:
        # short-lived process: always flush queued upserts before exit, even on a partial
        # failure mid-loop, so already-sent create_prompt requests are not dropped.
        client.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry: default DRY-RUN (print only); ``--commit`` upserts to Langfuse."""
    parser = argparse.ArgumentParser(description="Seed commander prompts into Langfuse.")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="upsert to Langfuse (default: dry-run, print only). Needs LANGFUSE_* creds.",
    )
    args = parser.parse_args(argv)
    return seed(commit=args.commit)


if __name__ == "__main__":
    raise SystemExit(main())
