"""Offline self-test for the Langfuse Phase-3 verify harness — NO SDK*, NO network, NO keys.

Run explicitly (the repo's pytest ``testpaths`` is ``tests/``; this spike lives outside it on
purpose, kickoff edit boundary = ``spike/langfuse/**`` only)::

    python3.12 -m pytest spike/langfuse/test_verify.py -q

Importing ``verify`` does NOT import ``langfuse``/``openai``: those are lazily imported only inside
the live driver (``make_traced_call`` / ``sdk_version`` / ``_require_create_fn``), so the predicate
math and the ①〜⑤ assertion logic are testable with fakes (mirrors the bridge's fake-injection
testability, doc16 §11). *The single ``importorskip`` test below exercises the real SDK's pure
``create_trace_id`` helper IFF langfuse is installed; everything else is hermetic.
"""

import pytest
from verify import (
    SDK_MAX_EXCL,
    SDK_MIN,
    _parse_env_file,
    assert_dev_only,
    check_inbound_trace_id,
    cost_is_nonzero,
    derive_trace_id,
    evaluate_trace,
    generation_cost,
    generations_of,
    grok_cost_usd,
    load_secret,
    main,
    managed_prompt_linked,
    normalize_trace_id,
    parse_sdk_version,
    sdk_version_ok,
    seed_for,
    single_generation,
    trace_ids_match,
)


# ── a deterministic fake Langfuse ``create_trace_id`` (no SDK) ─────────────────────────────
# The real ``langfuse.create_trace_id(seed=...)`` is a pure hash → 32-hex-no-dash, deterministic
# per seed. This fake reproduces that contract (md5 of the seed) so the cross-lane determinism and
# normalization logic is exercised without the SDK. #4 and #6 would both call the real helper.
def _fake_create_trace_id(*, seed: str) -> str:
    import hashlib

    return hashlib.md5(seed.encode()).hexdigest()  # noqa: S324 — test fixture, not security


def _fake_create_trace_id_dashed(*, seed: str) -> str:
    """A fake that returns a DASHED uuid-style id, to prove the harness normalizes at the boundary."""
    h = _fake_create_trace_id(seed=seed)
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


# ── normalize_trace_id (doc13:516) ─────────────────────────────────────────────────────────
def test_normalize_passthrough_valid_32hex() -> None:
    tid = "0123456789abcdef0123456789abcdef"
    assert normalize_trace_id(tid) == tid


def test_normalize_strips_dashes_and_lowercases() -> None:
    dashed = "0123ABCD-89ab-cdef-0123-456789ABCDEF"
    assert normalize_trace_id(dashed) == "0123abcd89abcdef0123456789abcdef"


def test_normalize_rejects_non_hex_and_wrong_length() -> None:
    with pytest.raises(ValueError):
        normalize_trace_id("xyz")
    with pytest.raises(ValueError):
        normalize_trace_id("0123456789abcdef")  # 16 hex — too short
    with pytest.raises(ValueError):
        normalize_trace_id("g" * 32)  # 32 chars but not hex


# ── seed + derive + cross-lane determinism (doc13:516,519) ─────────────────────────────────
def test_seed_for_format() -> None:
    assert seed_for("run_A_claude_s1_123", 7) == "run_A_claude_s1_123:7"


def test_derive_is_deterministic_per_seed() -> None:
    a = derive_trace_id("run:1", _fake_create_trace_id)
    b = derive_trace_id("run:1", _fake_create_trace_id)
    assert a == b and a is not None and len(a) == 32


def test_derive_normalizes_dashed_output() -> None:
    # A create_fn returning a dashed id must be normalized to 32-hex-no-dash (v4 rejects dashes).
    out = derive_trace_id("run:1", _fake_create_trace_id_dashed)
    assert out is not None and "-" not in out and len(out) == 32


def test_derive_fail_open_on_create_fn_error() -> None:
    def _raises(*, seed: str) -> str:
        raise RuntimeError("SDK boom")

    assert derive_trace_id("run:1", _raises) is None  # fail-open → None, caller no-ops


def test_cross_lane_legs_match_for_same_seed() -> None:
    # The crux of #73/doc13:519: #4 and #6 derive BYTE-IDENTICAL ids from the same run_id:gen_id.
    leg4 = derive_trace_id(seed_for("run_A_claude_s1", 42), _fake_create_trace_id)
    leg6 = derive_trace_id(seed_for("run_A_claude_s1", 42), _fake_create_trace_id)
    assert trace_ids_match(leg4, leg6)


def test_cross_lane_differs_for_different_gen_id() -> None:
    leg_a = derive_trace_id(seed_for("run_A", 1), _fake_create_trace_id)
    leg_b = derive_trace_id(seed_for("run_A", 2), _fake_create_trace_id)
    assert not trace_ids_match(leg_a, leg_b)


def test_trace_ids_match_rejects_none_and_empty() -> None:
    assert not trace_ids_match(None, "abc")
    assert not trace_ids_match("abc", None)
    assert not trace_ids_match("", "")


# ── ① inbound trace_id (doc13:520①) ────────────────────────────────────────────────────────
def test_check_inbound_match_and_mismatch() -> None:
    expected = "0123456789abcdef0123456789abcdef"
    assert check_inbound_trace_id(expected, expected)
    assert not check_inbound_trace_id("f" * 32, expected)


def test_check_inbound_normalizes_both_sides() -> None:
    # A dashed observed id still matches a no-dash expected id (both normalized before compare).
    expected = "0123456789abcdef0123456789abcdef"
    observed = "01234567-89ab-cdef-0123-456789abcdef"
    assert check_inbound_trace_id(observed, expected)


def test_check_inbound_none_is_false() -> None:
    assert not check_inbound_trace_id(None, "a" * 32)
    assert not check_inbound_trace_id("a" * 32, None)


# ── ② Grok offline cost arithmetic (doc08:505, prices INJECTED — doc08:508) ─────────────────
def test_grok_cost_arithmetic() -> None:
    # 1500 in * 1.25e-6 + 300 out * 2.50e-6 = 0.001875 + 0.00075 = 0.002625 USD (grok-4.3 example).
    usage = {"input": 1500, "output": 300}
    assert grok_cost_usd(usage, 1.25e-6, 2.50e-6) == pytest.approx(0.002625)


def test_grok_cost_alias_keys_and_bool_excluded() -> None:
    # OpenAI-compatible aliases parsed; a stray bool must NOT count as a token (bool ⊂ int).
    usage = {"prompt_tokens": 1000, "completion_tokens": 200, "cached": True}
    assert grok_cost_usd(usage, 1e-6, 2e-6) == pytest.approx(1000e-6 + 400e-6)


def test_grok_cost_zero_tokens_is_zero() -> None:
    assert grok_cost_usd({}, 1.25e-6, 2.5e-6) == 0.0


# ── generation_cost / ② cost_is_nonzero (doc13:520② / doc08:506) ────────────────────────────
def test_generation_cost_native_present() -> None:
    assert generation_cost({"cost": 0.0042}) == 0.0042


def test_generation_cost_grok_fallback_when_no_native() -> None:
    gen = {"model": "grok-4.3", "usage_details": {"input": 1000, "output": 100}}
    assert generation_cost(gen, grok_prices=(1.25e-6, 2.5e-6)) == pytest.approx(
        1000e-6 * 1.25 + 100e-6 * 2.5
    )


def test_generation_cost_unpriceable_is_none() -> None:
    # No native cost and no injected price → None ("unpriceable"), distinct from a real 0.0.
    assert generation_cost({"usage_details": {"input": 10}}) is None


def test_cost_is_nonzero_native_positive_and_zero() -> None:
    assert cost_is_nonzero({"cost": 0.01})
    assert not cost_is_nonzero({"cost": 0.0})


def test_cost_is_nonzero_grok_fallback() -> None:
    gen = {"model": "grok-4.3", "usage_details": {"input": 500, "output": 50}}
    assert cost_is_nonzero(gen, grok_prices=(1.25e-6, 2.5e-6))
    assert not cost_is_nonzero(gen)  # no price injected → unpriceable → not nonzero


# ── ③ single generation / generations_of (doc13:520③) ──────────────────────────────────────
def test_single_generation_true_for_one() -> None:
    assert single_generation({"generations": [{"model": "claude"}]})


def test_double_generation_is_false() -> None:
    # Two generations on one cycle trace = the double-generation failure (Hermes plugin left on).
    assert not single_generation({"generations": [{"model": "claude"}, {"model": "claude"}]})


def test_zero_generations_is_false() -> None:
    assert not single_generation({"generations": []})


def test_generations_of_malformed_is_empty() -> None:
    assert generations_of({"generations": "not-a-list"}) == []
    assert generations_of({}) == []


# ── ④ managed prompt (doc13:520④, defensive keys) ───────────────────────────────────────────
def test_managed_prompt_linked_variants() -> None:
    assert managed_prompt_linked({"prompt": {"name": "commander", "version": 3}})
    assert managed_prompt_linked({"promptName": "commander"})
    assert managed_prompt_linked({"prompt_name": "commander"})


def test_managed_prompt_absent_is_false() -> None:
    assert not managed_prompt_linked({"model": "claude"})
    assert not managed_prompt_linked({"prompt": None})


# ── ⑤ SDK version (doc13:514) ───────────────────────────────────────────────────────────────
def test_parse_sdk_version() -> None:
    assert parse_sdk_version("4.7.1") == (4, 7)
    assert parse_sdk_version("4.7") == (4, 7)
    assert parse_sdk_version("garbage") is None


def test_sdk_version_ok_range() -> None:
    assert sdk_version_ok("4.7.1")  # the pinned 4.7.1 (doc13:514)
    assert sdk_version_ok("4.9.0")
    assert not sdk_version_ok("4.6.9")  # below 4.7
    assert not sdk_version_ok("5.0.0")  # 5.x excluded
    assert not sdk_version_ok("3.99.0")
    assert not sdk_version_ok("nonsense")


def test_sdk_bounds_are_the_documented_pins() -> None:
    assert SDK_MIN == (4, 7) and SDK_MAX_EXCL == (5, 0)


# ── evaluate_trace aggregate (the full ①〜④ readback verdict) ────────────────────────────────
def _good_readback(trace_id: str) -> dict:
    return {
        "trace_id": trace_id,
        "generations": [
            {
                "provider": "xai",
                "model": "grok-4.3",
                "usage_details": {"input": 1000, "output": 100},
                "prompt": {"name": "commander", "version": 1},
            }
        ],
    }


def test_evaluate_trace_all_pass() -> None:
    tid = "0123456789abcdef0123456789abcdef"
    ev = evaluate_trace(_good_readback(tid), expected_trace_id=tid, grok_prices=(1.25e-6, 2.5e-6))
    assert ev["check1_inbound_trace_id"]
    assert ev["check2_all_costs_nonzero"]
    assert ev["check3_single_generation"]
    assert ev["check4_any_managed_prompt"]
    assert ev["generations"][0]["cost"] == pytest.approx(1000e-6 * 1.25 + 100e-6 * 2.5)


def test_evaluate_trace_double_generation_fails_check3() -> None:
    tid = "0123456789abcdef0123456789abcdef"
    rb = _good_readback(tid)
    rb["generations"].append(dict(rb["generations"][0]))  # second generation = double
    ev = evaluate_trace(rb, expected_trace_id=tid, grok_prices=(1.25e-6, 2.5e-6))
    assert not ev["check3_single_generation"]


def test_evaluate_trace_id_mismatch_fails_check1() -> None:
    rb = _good_readback("a" * 32)
    ev = evaluate_trace(rb, expected_trace_id="b" * 32, grok_prices=(1.25e-6, 2.5e-6))
    assert not ev["check1_inbound_trace_id"]


def test_evaluate_trace_no_price_fails_cost_check() -> None:
    # Grok with no native cost and NO injected price → cost check fails (the comparison-break case).
    tid = "0123456789abcdef0123456789abcdef"
    ev = evaluate_trace(_good_readback(tid), expected_trace_id=tid, grok_prices=None)
    assert not ev["check2_all_costs_nonzero"]


# ── live guards (hermetic: no network, no real keys) ────────────────────────────────────────
def test_assert_dev_only_refuses_prod(monkeypatch) -> None:
    monkeypatch.setenv("WAREHOUSE_ENV", "prod")
    with pytest.raises(SystemExit) as exc:
        assert_dev_only("http://127.0.0.1:8642", allow_remote=False)
    assert "prod" in str(exc.value)


def test_assert_dev_only_refuses_non_loopback(monkeypatch) -> None:
    monkeypatch.delenv("WAREHOUSE_ENV", raising=False)
    with pytest.raises(SystemExit) as exc:
        assert_dev_only("http://34.4.104.112:8642", allow_remote=False)
    assert "non-loopback" in str(exc.value)


def test_assert_dev_only_allow_remote_warns(monkeypatch, capsys) -> None:
    monkeypatch.delenv("WAREHOUSE_ENV", raising=False)
    assert assert_dev_only("http://34.4.104.112:8642", allow_remote=True) is None
    assert "WARNING" in capsys.readouterr().err


def test_assert_dev_only_loopback_dev_ok(monkeypatch) -> None:
    monkeypatch.setenv("WAREHOUSE_ENV", "dev")
    assert assert_dev_only("http://127.0.0.1:8642", allow_remote=False) is None


def test_load_secret_env_wins(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("API_SERVER_KEY", "env-key")
    missing = tmp_path / "absent.env"
    assert load_secret("API_SERVER_KEY", missing) == "env-key"
    assert not missing.exists()


def test_load_secret_reads_env_file(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("HERMES_LANGFUSE_PUBLIC_KEY", raising=False)
    env_file = tmp_path / "dev.env"
    env_file.write_text('# c\n\nHERMES_LANGFUSE_PUBLIC_KEY="pk-123"\n', encoding="utf-8")
    assert load_secret("HERMES_LANGFUSE_PUBLIC_KEY", env_file) == "pk-123"


def test_parse_env_file_strips_quotes_skips_comments(tmp_path) -> None:
    env_file = tmp_path / "dev.env"
    env_file.write_text("# h\n\nOTHER=ignored\nAPI_SERVER_KEY='sq'\n", encoding="utf-8")
    assert _parse_env_file(env_file, "API_SERVER_KEY") == "sq"
    assert _parse_env_file(env_file, "ABSENT") == ""


# ── main() spend gates (no paid call ever happens in tests) ─────────────────────────────────
def _poison(*_args, **_kwargs):
    raise AssertionError("network/SDK path reached in an offline test")


def test_main_dry_run_fires_no_call(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr("verify.make_traced_call", _poison)
    monkeypatch.setattr("verify.sdk_version", lambda: "4.7.1")
    monkeypatch.delenv("API_SERVER_KEY", raising=False)
    monkeypatch.setenv("WAREHOUSE_ENV", "dev")
    missing = tmp_path / "absent.env"
    rc = main(["-p", "xai", "--dry-run", "--env-file", str(missing)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "=== DRY RUN (no API calls) ===" in out
    assert "API_SERVER_KEY  : ABSENT" in out


def test_main_refuses_when_keys_absent(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("verify.make_traced_call", _poison)
    monkeypatch.setattr("verify.sdk_version", lambda: "4.7.1")
    for k in ("API_SERVER_KEY", "HERMES_LANGFUSE_PUBLIC_KEY", "HERMES_LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("WAREHOUSE_ENV", "dev")
    missing = tmp_path / "absent.env"
    with pytest.raises(SystemExit) as exc:
        main(["-p", "xai", "--run-id", "run_x", "--env-file", str(missing)])
    assert "REFUSED" in str(exc.value)


def test_main_refuses_without_run_id(monkeypatch, tmp_path) -> None:
    # Keys present (faked) but no run id → cannot seed the deterministic trace id → REFUSED.
    monkeypatch.setattr("verify.make_traced_call", _poison)
    monkeypatch.setattr("verify.load_secret", lambda *_a, **_k: "fake")
    monkeypatch.setattr("verify.sdk_version", lambda: "4.7.1")
    monkeypatch.setenv("WAREHOUSE_ENV", "dev")
    monkeypatch.delenv("WAREHOUSE_RUN_ID", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["-p", "xai", "--run-id", "", "--env-file", str(tmp_path / "x.env")])
    assert "run id" in str(exc.value)


# ── real SDK helper (skipped if langfuse absent) ────────────────────────────────────────────
def test_real_create_trace_id_is_deterministic_32hex() -> None:
    pytest.importorskip("langfuse", reason="install with: pip install 'langfuse>=4.7,<5'")
    from langfuse import Langfuse

    a = derive_trace_id("run:1", Langfuse.create_trace_id)
    b = derive_trace_id("run:1", Langfuse.create_trace_id)
    assert a is not None and len(a) == 32 and a == b  # deterministic, normalized 32-hex
