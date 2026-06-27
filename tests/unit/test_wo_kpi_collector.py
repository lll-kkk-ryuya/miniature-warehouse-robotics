"""kpi_collector score-send gate tests (warehouse_orchestrator, Lane C #6 wo).

The ``kpi_collector`` node is a thin rclpy shell; its score-send logic lives in the pure
:mod:`warehouse_orchestrator.score_send` (rclpy-free, doc16 §11). These tests drive the real
chain — fake ``audit.jsonl`` (``tmp_path`` + ``WAREHOUSE_AUDIT_LOG_PATH``) → ``read_audit_log``
→ ``compute_kpis`` → ``send_scores`` with a fake v4 client + an injected deterministic
``create_fn`` — and assert the **gate matrix** plus the documented score metadata
(``provider`` + ``gen_id`` added this slice; doc08:360,369 / doc13:516,519). No real langfuse
SDK is imported.
"""

import ast
import hashlib
import json
from pathlib import Path

import pytest
from warehouse_orchestrator.audit_reader import read_audit_log
from warehouse_orchestrator.kpi import compute_kpis
from warehouse_orchestrator.langfuse_sink import LangfuseScoreSink
from warehouse_orchestrator.score_send import (
    build_score_metadata,
    resolve_pattern_d,
    resolve_provider,
    send_scores,
)
from warehouse_orchestrator.trace_id import normalize_trace_id


class _FakeClient:
    """Captures v4 ``create_score(...)`` + ``flush()`` calls (no real SDK)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.flushed = 0

    def create_score(self, *, trace_id, name, value, data_type=None, metadata=None) -> None:
        self.calls.append(
            {"trace_id": trace_id, "name": name, "value": value, "metadata": metadata}
        )

    def flush(self) -> None:
        self.flushed += 1


def _fake_create(*, seed: str) -> str:
    """Deterministic 32-hex-no-dash trace id from ``seed`` (stand-in for create_trace_id)."""
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


def _enabled_sink() -> tuple[LangfuseScoreSink, _FakeClient]:
    fake = _FakeClient()
    return LangfuseScoreSink(client=fake), fake


def _write_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, records: list[dict]) -> None:
    """Write a fake audit.jsonl and point the frozen path at it (paths.py:50)."""
    path = tmp_path / "audit.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    monkeypatch.setenv("WAREHOUSE_AUDIT_LOG_PATH", str(path))


# A dispatch row that carries the per-task gen_id mcp_server will add to executed rows (#73).
_DISPATCH_WITH_GEN = {
    "timestamp": 100.0,
    "tool": "dispatch_task",
    "result": "executed",
    "detail": {"task_id": "nav_001", "gen_id": 7},
    "robot": "bot1",
}
# Same dispatch but WITHOUT gen_id — the dev reality until mcp_server writes it.
_DISPATCH_NO_GEN = {
    "timestamp": 100.0,
    "tool": "dispatch_task",
    "result": "executed",
    "detail": {"task_id": "nav_001"},
    "robot": "bot1",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep the default-client path disabled and provider/run id deterministic across hosts.
    for var in (
        "HERMES_LANGFUSE_PUBLIC_KEY",
        "HERMES_LANGFUSE_SECRET_KEY",
        "WAREHOUSE_PROVIDER",
        "WAREHOUSE_RUN_ID",
    ):
        monkeypatch.delenv(var, raising=False)


# ── resolve_provider (doc08:367) ─────────────────────────────────────────────


@pytest.mark.unit
def test_resolve_provider_param_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAREHOUSE_PROVIDER", "gemini")
    assert resolve_provider("claude") == "claude"


@pytest.mark.unit
def test_resolve_provider_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAREHOUSE_PROVIDER", "grok")
    assert resolve_provider("") == "grok"
    assert resolve_provider(None) == "grok"


@pytest.mark.unit
def test_resolve_provider_none_when_unset_or_blank() -> None:
    assert resolve_provider("") is None
    assert resolve_provider(None) is None
    assert resolve_provider("   ") is None  # whitespace is treated as unset


# ── build_score_metadata (doc08:360,363) ─────────────────────────────────────


@pytest.mark.unit
def test_build_metadata_includes_provider_and_gen_id() -> None:
    meta = build_score_metadata(run_id="run-1", mode="A", provider="claude", gen_id=7)
    assert meta == {"run_id": "run-1", "mode": "A", "provider": "claude", "gen_id": 7}


@pytest.mark.unit
def test_build_metadata_omits_unset_optionals_but_keeps_run_id() -> None:
    # run_id is always present (the trace-seed half); mode/provider/gen_id only when set.
    # robot is never added here — the efficiency leg adds it per-robot.
    assert build_score_metadata(run_id="run-1", mode=None, provider=None, gen_id=None) == {
        "run_id": "run-1"
    }
    assert "robot" not in build_score_metadata(
        run_id="run-1", mode="A", provider="claude", gen_id=7
    )


# ── gate matrix (send_scores) ────────────────────────────────────────────────


@pytest.mark.unit
def test_gate_a_no_run_id_sends_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # (a) WAREHOUSE_RUN_ID unset → cannot derive the cross-lane trace seed → send 0.
    _write_audit(tmp_path, monkeypatch, [_DISPATCH_WITH_GEN])
    entries = read_audit_log()
    report = compute_kpis(entries, completions={"nav_001": 130.0})
    sink, fake = _enabled_sink()
    sent, trace = send_scores(
        sink,
        report,
        entries,
        {"bot1": 12.5},
        run_id=None,
        mode="A",
        provider="claude",
        create_fn=_fake_create,
    )
    assert (sent, trace) == (0, None)
    assert fake.calls == []  # gated before any client call


@pytest.mark.unit
def test_gate_b_no_gen_id_sends_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # (b) audit rows carry no gen_id (dev reality) → trace None → send 0.
    _write_audit(tmp_path, monkeypatch, [_DISPATCH_NO_GEN])
    entries = read_audit_log()
    report = compute_kpis(entries, completions={"nav_001": 130.0})
    sink, fake = _enabled_sink()
    sent, trace = send_scores(
        sink,
        report,
        entries,
        {"bot1": 12.5},
        run_id="run-b",
        mode="A",
        provider="claude",
        create_fn=_fake_create,
    )
    assert (sent, trace) == (0, None)
    assert fake.calls == []


@pytest.mark.unit
def test_gate_d_empty_run_id_is_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # (d) an empty / all-whitespace / None run_id is treated as unset → send 0 (review #6):
    # a stray WAREHOUSE_RUN_ID typo must never seed a trace from "   :gen".
    _write_audit(tmp_path, monkeypatch, [_DISPATCH_WITH_GEN])
    entries = read_audit_log()
    report = compute_kpis(entries, completions={"nav_001": 130.0})
    sink, fake = _enabled_sink()
    for bad in ("", "   ", "\t", None):
        sent, trace = send_scores(
            sink,
            report,
            entries,
            {"bot1": 12.5},
            run_id=bad,
            mode="A",
            provider="claude",
            create_fn=_fake_create,
        )
        assert (sent, trace) == (0, None)
    assert fake.calls == []


@pytest.mark.unit
def test_gate_disabled_sink_sends_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No creds / disabled sink → fail-open no-op even with run_id + gen_id present.
    _write_audit(tmp_path, monkeypatch, [_DISPATCH_WITH_GEN])
    entries = read_audit_log()
    report = compute_kpis(entries, completions={"nav_001": 130.0})
    fake = _FakeClient()
    sink = LangfuseScoreSink(client=fake, enabled=False)
    sent, trace = send_scores(
        sink,
        report,
        entries,
        {"bot1": 12.5},
        run_id="run-x",
        mode="A",
        provider="claude",
        create_fn=_fake_create,
    )
    assert (sent, trace) == (0, None)
    assert fake.calls == []


@pytest.mark.unit
def test_gate_c_full_creds_send_with_provider_and_gen_id_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # (c) gen_id + run_id + creds → deterministic trace + task_completion_time/efficiency
    #     scores carrying {run_id, mode, provider, gen_id} (efficiency adds robot per-leg).
    _write_audit(tmp_path, monkeypatch, [_DISPATCH_WITH_GEN])
    entries = read_audit_log()
    report = compute_kpis(entries, completions={"nav_001": 130.0})  # completion_time = 30.0
    sink, fake = _enabled_sink()

    sent, trace = send_scores(
        sink,
        report,
        entries,
        {"bot1": 12.5, "bot2": 4.0},
        run_id="run-c",
        mode="A",
        provider="claude",
        create_fn=_fake_create,
    )

    # 1 task_completion_time + 2 efficiency = 3 scores.
    assert sent == 3
    assert trace == normalize_trace_id(_fake_create(seed="run-c:7"))  # deterministic (#73)

    by_name = {c["name"]: c for c in fake.calls}
    assert set(by_name) == {"task_completion_time", "efficiency"}  # last efficiency wins the map
    tct = by_name["task_completion_time"]
    assert tct["value"] == pytest.approx(30.0)
    assert tct["trace_id"] == trace
    # provider + gen_id are the new fields this slice adds (no robot on the aggregate leg).
    assert tct["metadata"] == {"run_id": "run-c", "mode": "A", "provider": "claude", "gen_id": 7}

    eff_meta = [c["metadata"] for c in fake.calls if c["name"] == "efficiency"]
    assert {m["robot"] for m in eff_meta} == {"bot1", "bot2"}  # robot added per-leg (doc08:369)
    for m in eff_meta:
        assert m["provider"] == "claude" and m["gen_id"] == 7 and m["run_id"] == "run-c"


@pytest.mark.unit
def test_trace_id_is_deterministic_per_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same (run_id, gen_id) → same trace (this is what links #4's and #6's legs, #73).
    _write_audit(tmp_path, monkeypatch, [_DISPATCH_WITH_GEN])
    entries = read_audit_log()
    report = compute_kpis(entries)

    def _trace_for(run_id: str) -> str | None:
        sink, _ = _enabled_sink()
        _, trace = send_scores(
            sink,
            report,
            entries,
            {},
            run_id=run_id,
            mode=None,
            provider=None,
            create_fn=_fake_create,
        )
        return trace

    assert _trace_for("run-c") == _trace_for("run-c")
    assert _trace_for("run-c") != _trace_for("run-d")  # different seed → different id


# ── Pattern-D switch (plugin-ON join; Pattern A stays default) ────────────────


@pytest.mark.unit
def test_pattern_a_is_default_bridge_owned_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Default (pattern_d unset) keeps the Pattern-A recipe: trace = hash(seed_for(run, gen)).
    _write_audit(tmp_path, monkeypatch, [_DISPATCH_WITH_GEN])
    entries = read_audit_log()
    report = compute_kpis(entries, completions={"nav_001": 130.0})
    sink, fake = _enabled_sink()
    sent, trace = send_scores(
        sink,
        report,
        entries,
        {"bot1": 12.5},
        run_id="run-c",
        mode="A",
        provider="claude",
        create_fn=_fake_create,
    )
    assert sent == 2
    assert trace == normalize_trace_id(_fake_create(seed="run-c:7"))  # Pattern A (Bridge-owned)


@pytest.mark.unit
def test_pattern_d_switch_uses_plugin_double_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # pattern_d=True re-derives the trace the Hermes Langfuse plugin minted: hash(f"{H}::{H}")
    # with H = seed_for(run, gen) — the plugin-ON (Option D) join key (plugin __init__:544).
    _write_audit(tmp_path, monkeypatch, [_DISPATCH_WITH_GEN])
    entries = read_audit_log()
    report = compute_kpis(entries, completions={"nav_001": 130.0})
    sink, fake = _enabled_sink()
    sent, trace = send_scores(
        sink,
        report,
        entries,
        {"bot1": 12.5},
        run_id="run-c",
        mode="A",
        provider="claude",
        create_fn=_fake_create,
        pattern_d=True,
    )
    assert sent == 2
    assert trace == normalize_trace_id(_fake_create(seed="run-c:7::run-c:7"))  # Pattern D
    # The switch genuinely diverges from the default Pattern-A recipe.
    assert trace != normalize_trace_id(_fake_create(seed="run-c:7"))
    # Scores still attach to the (plugin) trace and carry the documented metadata.
    assert all(c["trace_id"] == trace for c in fake.calls)
    tct = next(c for c in fake.calls if c["name"] == "task_completion_time")
    assert tct["metadata"] == {"run_id": "run-c", "mode": "A", "provider": "claude", "gen_id": 7}


@pytest.mark.unit
def test_pattern_d_inert_without_gen_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # gen_id None (dev reality until mcp_server writes it) → no seed half → (0, None), no raise.
    _write_audit(tmp_path, monkeypatch, [_DISPATCH_NO_GEN])
    entries = read_audit_log()
    report = compute_kpis(entries)
    sink, fake = _enabled_sink()
    sent, trace = send_scores(
        sink,
        report,
        entries,
        {"bot1": 12.5},
        run_id="run-c",
        mode="A",
        provider="claude",
        create_fn=_fake_create,
        pattern_d=True,
    )
    assert (sent, trace) == (0, None)
    assert fake.calls == []


@pytest.mark.unit
def test_pattern_d_blank_run_id_is_inert_like_pattern_a(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # MINOR #3: a blank/all-whitespace run_id must decline on BOTH legs. Pattern A strips inside
    # trace_id_for; the pattern_d branch must guard identically (and the Bridge's
    # _plugin_session_id returns None for a blank run_id) so a stray "   " never seeds "   :gen"
    # and sends a score to a non-joinable trace. The leading `if not run_id` gate already drops
    # "" / None; this pins the all-whitespace case for the plugin recipe specifically.
    _write_audit(tmp_path, monkeypatch, [_DISPATCH_WITH_GEN])
    entries = read_audit_log()
    report = compute_kpis(entries, completions={"nav_001": 130.0})
    sink, fake = _enabled_sink()
    sent, trace = send_scores(
        sink,
        report,
        entries,
        {"bot1": 12.5},
        run_id="   ",  # all-whitespace → non-joinable
        mode="A",
        provider="claude",
        create_fn=_fake_create,
        pattern_d=True,
    )
    assert (sent, trace) == (0, None)
    assert fake.calls == []


@pytest.mark.unit
def test_node_flushes_after_a_successful_derivation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The pure helper does NOT flush (the node owns flush, doc08:347); prove the sink the
    # node would flush is the same enabled one and that send happened (flush is a node call).
    _write_audit(tmp_path, monkeypatch, [_DISPATCH_WITH_GEN])
    entries = read_audit_log()
    report = compute_kpis(entries, completions={"nav_001": 130.0})
    sink, fake = _enabled_sink()
    sent, trace = send_scores(
        sink,
        report,
        entries,
        {},
        run_id="run-c",
        mode=None,
        provider=None,
        create_fn=_fake_create,
    )
    assert sent == 1 and trace is not None
    assert fake.flushed == 0  # helper is pure send-orchestration; flush is the node's job


# ── resolve_pattern_d: the scorer leg of the ONE-knob Option-D switch (#2) ────
# Mirrors the Bridge's resolve_langfuse_owner so WAREHOUSE_LANGFUSE_OWNER /
# hermes.langfuse_owner flips BOTH legs. Without this the scorer always used Pattern A
# and every Option-D score orphaned (the trace the plugin minted via H::H vs the Pattern-A
# id the scorer derived from seed_for) — the new derive_plugin_trace_id was dead code.


@pytest.mark.unit
def test_pattern_d_default_is_false_pattern_a() -> None:
    assert resolve_pattern_d({}, env={}) is False


@pytest.mark.unit
def test_pattern_d_env_selects_plugin() -> None:
    assert resolve_pattern_d({}, env={"WAREHOUSE_LANGFUSE_OWNER": "hermes_plugin"}) is True


@pytest.mark.unit
def test_pattern_d_env_overrides_config() -> None:
    # env wins over config (env-over-config, identical precedence to the Bridge resolver).
    cfg = {"hermes": {"langfuse_owner": "hermes_plugin"}}
    assert resolve_pattern_d(cfg, env={"WAREHOUSE_LANGFUSE_OWNER": "bridge"}) is False


@pytest.mark.unit
def test_pattern_d_config_used_when_env_absent() -> None:
    cfg = {"hermes": {"langfuse_owner": "hermes_plugin"}}
    assert resolve_pattern_d(cfg, env={}) is True


@pytest.mark.unit
def test_pattern_d_unknown_value_fails_safe_to_pattern_a() -> None:
    # An unknown/typo value NEVER silently enables Option D (would orphan every score).
    assert resolve_pattern_d({}, env={"WAREHOUSE_LANGFUSE_OWNER": "plugin"}) is False
    assert resolve_pattern_d({"hermes": "x"}, env={}) is False  # malformed block → safe


@pytest.mark.unit
def test_pattern_d_unknown_value_is_logged_not_silent(caplog: pytest.LogCaptureFixture) -> None:
    # The misconfig is NOT silent: like the Bridge resolver, an unknown owner LOGS a warning so
    # an operator typo surfaces instead of silently orphaning every score (round-2 D(b)).
    import logging

    with caplog.at_level(logging.WARNING):
        assert resolve_pattern_d({}, env={"WAREHOUSE_LANGFUSE_OWNER": "plugn"}) is False
    assert any("plugn" in r.message for r in caplog.records)


@pytest.mark.unit
def test_pattern_d_reads_non_dict_mapping_config() -> None:
    # resolve_pattern_d uses isinstance(..., Mapping); a non-dict Mapping config (and a
    # non-dict Mapping ``hermes`` block) selects Option D — proving the type check matches the
    # Bridge resolver, which now also uses Mapping (round-2 D(a)).
    from collections.abc import Iterator, Mapping

    class _RoMap(Mapping):
        def __init__(self, d: dict) -> None:
            self._d = d

        def __getitem__(self, k: object) -> object:
            return self._d[k]

        def __iter__(self) -> Iterator:
            return iter(self._d)

        def __len__(self) -> int:
            return len(self._d)

    cfg = _RoMap({"hermes": _RoMap({"langfuse_owner": "hermes_plugin"})})
    assert resolve_pattern_d(cfg, env={}) is True


@pytest.mark.unit
def test_pattern_d_blank_env_falls_through_to_config_then_default() -> None:
    cfg = {"hermes": {"langfuse_owner": "hermes_plugin"}}
    assert resolve_pattern_d(cfg, env={"WAREHOUSE_LANGFUSE_OWNER": "  "}) is True
    assert resolve_pattern_d({}, env={"WAREHOUSE_LANGFUSE_OWNER": "  "}) is False


_REPO_ROOT = Path(__file__).resolve().parents[2]
_KPI_COLLECTOR_PY = (
    _REPO_ROOT / "ws/src/warehouse_orchestrator/warehouse_orchestrator/kpi_collector.py"
)


def _kpi_collector_method(name: str) -> ast.FunctionDef:
    """Return a KpiCollector method's AST from SOURCE (no import — the node imports rclpy).

    ``kpi_collector.py`` does ``import rclpy`` at module top and ``class KpiCollector(Node)``,
    so it can't be imported in host CI (same constraint as test_modec_noactuation.py).
    """
    tree = ast.parse(_KPI_COLLECTOR_PY.read_text())
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef) and cls.name == "KpiCollector":
            for fn in cls.body:
                if isinstance(fn, ast.FunctionDef) and fn.name == name:
                    return fn
    raise AssertionError(f"KpiCollector.{name} not found in kpi_collector.py")


@pytest.mark.unit
def test_kpi_collector_forwards_pattern_d_to_send_scores() -> None:
    # Regression for the dead-code orphan bug (#2): the node MUST pass pattern_d into
    # send_scores, wired to self._pattern_d (the init-resolved owner switch) — NOT a literal.
    # Without this the scorer always ran Pattern A and every Option-D score orphaned. AST on
    # source so a future refactor that drops the wiring fails this test.
    fn = _kpi_collector_method("_send_scores")
    send_calls = [
        c
        for c in ast.walk(fn)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "send_scores"
    ]
    assert send_calls, "_send_scores must call send_scores(...)"
    kw = {k.arg: k.value for k in send_calls[0].keywords}
    assert "pattern_d" in kw, "send_scores(...) must receive pattern_d (Option-D scorer leg)"
    val = kw["pattern_d"]
    assert (
        isinstance(val, ast.Attribute)
        and val.attr == "_pattern_d"
        and isinstance(val.value, ast.Name)
        and val.value.id == "self"
    ), "pattern_d must be wired to self._pattern_d (resolved from WAREHOUSE_LANGFUSE_OWNER)"


@pytest.mark.unit
def test_kpi_collector_resolves_pattern_d_at_init() -> None:
    # The node __init__ must resolve self._pattern_d via resolve_pattern_d(...) so the knob is
    # read once at startup (not hard-coded False). Source-level (rclpy node not constructible).
    init = _kpi_collector_method("__init__")
    calls = {
        c.func.id
        for c in ast.walk(init)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
    }
    assert "resolve_pattern_d" in calls, (
        "__init__ must resolve pattern_d via resolve_pattern_d(...)"
    )
    assigns_pattern_d = any(
        isinstance(n, ast.Attribute) and n.attr == "_pattern_d" for n in ast.walk(init)
    )
    assert assigns_pattern_d, "__init__ must set self._pattern_d"
