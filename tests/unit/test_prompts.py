"""Tests for the commander prompt provider (Langfuse Prompt Management + code fallback).

The provider (warehouse_llm_bridge.prompts) is pure / network-free: langfuse access is
isolated behind ``prompts._get_client`` so these tests patch it deterministically (no live
Langfuse call). Two invariants matter: (1) the code fallback equals the historical
``build_system_prompt(mode)`` so behaviour is preserved when Langfuse is absent (fail-open,
doc08:333); (2) the seeding script upserts that SAME text (no drift, doc08 §Langfuse Prompt
Management 方針).
"""

import pytest
from warehouse_llm_bridge import prompts, seed_prompts
from warehouse_llm_bridge.hermes_client import build_system_prompt
from warehouse_llm_bridge.prompts import (
    PROMPT_NAME_MODE_AB,
    PROMPT_NAME_MODE_C,
    commander_fallback_text,
    prompt_name,
    resolve_commander_prompt,
)


class _FakePrompt:
    """Stand-in for a langfuse TextPromptClient (.prompt / .is_fallback)."""

    def __init__(self, text: str, *, is_fallback: bool = False) -> None:
        self.prompt = text
        self.is_fallback = is_fallback


class _FakeClient:
    """Records get_prompt calls and returns a preset prompt object."""

    def __init__(self, prompt_obj: _FakePrompt) -> None:
        self._p = prompt_obj
        self.calls: list[tuple] = []

    def get_prompt(self, name, *, label=None, fallback=None, cache_ttl_seconds=None):
        self.calls.append((name, label, fallback, cache_ttl_seconds))
        return self._p


# ── fallback text == build_system_prompt (behaviour preserved) ───────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["none", "simple", "open-rmf"])
def test_fallback_text_matches_build_system_prompt(mode: str) -> None:
    assert commander_fallback_text(mode) == build_system_prompt(mode)


@pytest.mark.unit
@pytest.mark.parametrize(
    "mode,name",
    [
        ("none", PROMPT_NAME_MODE_AB),
        ("simple", PROMPT_NAME_MODE_AB),
        ("open-rmf", PROMPT_NAME_MODE_C),
    ],
)
def test_prompt_name_per_mode(mode: str, name: str) -> None:
    assert prompt_name(mode) == name


# ── source == "code": Langfuse untouched, verbatim fallback ──────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["none", "simple", "open-rmf"])
def test_source_code_does_not_touch_langfuse(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    # source=code must NOT even touch langfuse. A *throwing* _get_client would be swallowed by
    # the production `except Exception` (a false green), so instead COUNT invocations and assert
    # zero — an assertion the production except cannot mask.
    calls: list[int] = []
    monkeypatch.setattr(prompts, "_get_client", lambda: calls.append(1))
    resolved = resolve_commander_prompt(mode, {"hermes": {"prompts": {"source": "code"}}})
    assert calls == []  # proof of the early return (not a swallowed error)
    assert resolved.text == build_system_prompt(mode)
    assert resolved.langfuse_prompt is None
    assert resolved.is_fallback is True


# ── source == "langfuse": fetched prompt is used + linked ────────────────────────────


@pytest.mark.unit
def test_langfuse_source_uses_and_links_fetched_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_prompt = _FakePrompt("MANAGED PROMPT TEXT", is_fallback=False)
    fake_client = _FakeClient(fake_prompt)
    monkeypatch.setattr(prompts, "_get_client", lambda: fake_client)
    cfg = {
        "hermes": {
            "prompts": {"source": "langfuse", "label": "production", "cache_ttl_seconds": 42}
        }
    }
    resolved = resolve_commander_prompt("none", cfg)
    assert resolved.text == "MANAGED PROMPT TEXT"
    assert resolved.langfuse_prompt is fake_prompt  # linked for prompt-level analytics
    assert resolved.is_fallback is False
    # fetched by the configured name / label / ttl, with the code constant as fallback=.
    name, label, fallback, ttl = fake_client.calls[0]
    assert name == PROMPT_NAME_MODE_AB
    assert label == "production"
    assert fallback == build_system_prompt("none")
    assert ttl == 42


@pytest.mark.unit
def test_langfuse_fallback_object_text_used_but_not_linked(monkeypatch: pytest.MonkeyPatch) -> None:
    # When the SDK returns a fallback object (Langfuse unreachable), the code uses that object's
    # .prompt text (which in prod == our fallback) but must NOT link it. Use a DISTINCT sentinel
    # (≠ build_system_prompt) so the assertion proves the SDK text is returned — not a vacuous
    # equality against the same string the fake was built from.
    sentinel = "SDK_FALLBACK_SENTINEL_本文"
    monkeypatch.setattr(
        prompts, "_get_client", lambda: _FakeClient(_FakePrompt(sentinel, is_fallback=True))
    )
    resolved = resolve_commander_prompt("open-rmf", {"hermes": {"prompts": {"source": "langfuse"}}})
    assert resolved.text == sentinel  # SDK-returned text is used
    assert resolved.langfuse_prompt is None  # but a fallback object is NOT linked
    assert resolved.is_fallback is True


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["none", "open-rmf"])
def test_langfuse_failure_falls_open_to_code(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    # Any error (SDK absent / not-found / auth / network) must fall open to the code
    # constant and never raise (doc08:333).
    def boom() -> object:
        raise RuntimeError("langfuse unavailable")

    monkeypatch.setattr(prompts, "_get_client", boom)
    resolved = resolve_commander_prompt(mode, {"hermes": {"prompts": {"source": "langfuse"}}})
    assert resolved.text == build_system_prompt(mode)
    assert resolved.langfuse_prompt is None
    assert resolved.is_fallback is True


@pytest.mark.unit
def test_default_config_is_behaviour_preserving(monkeypatch: pytest.MonkeyPatch) -> None:
    # No hermes.prompts block -> defaults to source=langfuse; with langfuse unavailable it
    # falls open to a valid prompt (the historical behaviour).
    monkeypatch.setattr(prompts, "_get_client", lambda: (_ for _ in ()).throw(ImportError))
    resolved = resolve_commander_prompt("none", {})
    assert resolved.text == build_system_prompt("none")
    assert resolved.is_fallback is True


# ── seed script: text == code fallback (no drift) + dry-run ──────────────────────────


@pytest.mark.unit
def test_seed_specs_text_matches_code_fallback() -> None:
    by_name = {s["name"]: s for s in seed_prompts.seed_specs()}
    assert by_name[PROMPT_NAME_MODE_AB]["prompt"] == build_system_prompt("none")
    assert by_name[PROMPT_NAME_MODE_C]["prompt"] == build_system_prompt("open-rmf")
    # every seeded prompt is labelled production (the fairness version pin, doc08 §比較検証ログ)
    for spec in seed_prompts.seed_specs():
        assert "production" in spec["labels"]


@pytest.mark.unit
def test_seed_dry_run_prints_specs_and_returns_zero(capsys: pytest.CaptureFixture) -> None:
    rc = seed_prompts.main([])  # default = dry-run (no --commit)
    assert rc == 0
    out = capsys.readouterr().out
    assert PROMPT_NAME_MODE_AB in out
    assert PROMPT_NAME_MODE_C in out
    assert "DRY-RUN" in out


# ── names override + mode-specific fallback (fetch wiring) ───────────────────────────


@pytest.mark.unit
def test_names_override_is_used_for_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeClient(_FakePrompt("X"))
    monkeypatch.setattr(prompts, "_get_client", lambda: fake_client)
    cfg = {"hermes": {"prompts": {"source": "langfuse", "names": {"mode_c": "custom-c-name"}}}}
    resolve_commander_prompt("open-rmf", cfg)
    assert fake_client.calls[0][0] == "custom-c-name"  # the override name is fetched


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["none", "open-rmf"])
def test_fallback_arg_is_mode_specific(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    # the fallback= handed to the SDK must be the SAME-MODE code constant, so an outage serves
    # the right prompt for BOTH mode_ab and mode_c (not just the mode_ab path).
    fake_client = _FakeClient(_FakePrompt("X"))
    monkeypatch.setattr(prompts, "_get_client", lambda: fake_client)
    resolve_commander_prompt(mode, {"hermes": {"prompts": {"source": "langfuse"}}})
    _, _, fallback, _ = fake_client.calls[0]
    assert fallback == build_system_prompt(mode)


# ── never-raises contract on malformed config (the BLOCKING fix) ─────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("bad_names", ["warehouse-x", ["a", "b"], 123])
def test_malformed_names_config_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, bad_names: object
) -> None:
    # a truthy NON-dict names (YAML scalar/list) must NOT raise (never-raises contract); the
    # per-mode default name is used instead.
    fake_client = _FakeClient(_FakePrompt("X"))
    monkeypatch.setattr(prompts, "_get_client", lambda: fake_client)
    cfg = {"hermes": {"prompts": {"source": "langfuse", "names": bad_names}}}
    resolved = resolve_commander_prompt("none", cfg)  # must not raise
    assert fake_client.calls[0][0] == PROMPT_NAME_MODE_AB  # fell back to the default name
    assert resolved.is_fallback is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_cfg",
    [{"hermes": "x"}, {"hermes": {"prompts": "langfuse"}}, {}, "not-a-dict"],
)
def test_malformed_config_falls_open(monkeypatch: pytest.MonkeyPatch, bad_cfg: object) -> None:
    # malformed hermes / prompts (non-dict scalars) must default safely and never raise.
    monkeypatch.setattr(prompts, "_get_client", lambda: (_ for _ in ()).throw(ImportError))
    resolved = resolve_commander_prompt("none", bad_cfg)  # must not raise
    assert resolved.text == build_system_prompt("none")
    assert resolved.is_fallback is True


# ── seed --commit success path (create_prompt loop + flush) ──────────────────────────


@pytest.mark.unit
def test_seed_commit_upserts_and_flushes(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    class _SeedClient:
        def __init__(self) -> None:
            self.created: list[dict] = []
            self.flushed = 0

        def create_prompt(self, **kw: object) -> None:
            self.created.append(kw)

        def flush(self) -> None:
            self.flushed += 1

    fake = _SeedClient()
    fake_mod = types.ModuleType("langfuse")
    fake_mod.get_client = lambda: fake  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse", fake_mod)

    rc = seed_prompts.seed(commit=True)
    assert rc == 0
    assert [c["name"] for c in fake.created] == [PROMPT_NAME_MODE_AB, PROMPT_NAME_MODE_C]
    assert all(c["type"] == "text" and "production" in c["labels"] for c in fake.created)
    assert fake.flushed == 1  # flushed exactly once (try/finally)
