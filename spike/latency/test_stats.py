"""Pure unit tests for the latency spike — NO live SDK, NO network, NO gateway.

Run explicitly (the repo's pytest ``testpaths`` is ``tests/``; this spike lives
outside it on purpose, kickoff edit boundary = ``spike/latency/**`` only)::

    python3.12 -m pytest spike/latency/test_stats.py -q

Importing ``measure`` here does NOT import ``openai``: the SDK is lazily imported
inside ``make_caller`` only, so the percentile math and the measurement loop are
testable with fakes (mirrors the bridge's fake-injection testability, doc16 §11).
"""

import pytest
from measure import (
    Sample,
    _build_report,
    _parse_env_file,
    _print_summary,
    assert_dev_only,
    load_api_server_key,
    main,
    run_measurement,
)
from stats import percentile, summarize


def test_percentile_nearest_rank_known() -> None:
    xs = list(range(1, 11))  # 1..10
    assert percentile(xs, 50) == 5  # ceil(0.5*10)=5 -> xs[4]
    assert percentile(xs, 95) == 10  # ceil(0.95*10)=10 -> xs[9]
    assert percentile(xs, 100) == 10
    assert percentile(xs, 10) == 1  # ceil(1.0)=1 -> xs[0]


def test_percentile_unsorted_input() -> None:
    assert percentile([5, 1, 3, 2, 4], 50) == 3


def test_percentile_single() -> None:
    assert percentile([42.0], 99) == 42.0


def test_percentile_errors() -> None:
    with pytest.raises(ValueError):
        percentile([], 50)
    with pytest.raises(ValueError):
        percentile([1.0], 0)
    with pytest.raises(ValueError):
        percentile([1.0], 101)


def test_summarize_n120_p99_caveat() -> None:
    xs = [float(i) for i in range(1, 121)]  # 1..120, n=120 (doc06:103)
    s = summarize(xs)
    assert s["n"] == 120
    assert s["min"] == 1.0
    assert s["max"] == 120.0
    assert s["p50"] == 60.0  # ceil(0.50*120)=60 -> xs[59]
    assert s["p95"] == 114.0  # ceil(0.95*120)=114 -> xs[113]
    # p99 -> ceil(0.99*120)=ceil(118.8)=119 -> xs[118]=119 == 2nd-largest: the
    # n=120 p99 is barely estimable (RESULT.md §0). Decision figures are p50/p95.
    assert s["p99"] == 119.0


def test_summarize_single_stdev_zero() -> None:
    s = summarize([7.0])
    assert s["stdev"] == 0.0
    assert s["mean"] == 7.0


def test_summarize_empty_raises() -> None:
    with pytest.raises(ValueError):
        summarize([])


def _fake_caller(samples: list[Sample]):
    it = iter(samples)

    def call() -> Sample:
        return next(it)

    return call


def test_run_measurement_collects_ok_and_counts_errors() -> None:
    samples = [
        Sample(0.10, True, None, 100),
        Sample(0.20, False, "Timeout: upstream", None),  # error excluded from latencies
        Sample(0.30, True, None, 110),
    ]
    res = run_measurement(_fake_caller(samples), n=3, warmup=0)
    assert res["latencies"] == [0.10, 0.30]
    assert res["errors"] == ["Timeout: upstream"]
    assert res["tokens"] == [100, 110]
    assert res["n_requested"] == 3


def test_run_measurement_discards_warmup() -> None:
    samples = [
        Sample(9.0, True, None, None),  # warmup, discarded
        Sample(9.0, True, None, None),  # warmup, discarded
        Sample(0.10, True, None, None),
        Sample(0.20, True, None, None),
    ]
    res = run_measurement(_fake_caller(samples), n=2, warmup=2)
    assert res["latencies"] == [0.10, 0.20]
    assert res["warmup"] == 2


def test_build_report_missed_cycle_rate() -> None:
    # Survivorship-bias gate (review blocking fix): a successful-but->2.5s call AND a
    # hard error both count as missed cycles (doc08:140), so survivor p95 alone cannot
    # declare the cycle viable.
    result = {
        "latencies": [0.5, 1.0, 3.0],  # 3.0s succeeded but exceeds the 2.5s in-cycle timeout
        "errors": ["Timeout: upstream"],  # one hard error
        "tokens": [],
        "n_requested": 5,
        "warmup": 0,
    }
    rep = _build_report("anthropic", "fairness-off", "http://127.0.0.1:8642", result, None, 60.0)
    assert rep["n_ok"] == 3
    assert rep["n_err"] == 1
    assert rep["n_over_in_cycle_timeout"] == 1  # the 3.0s success is a missed cycle
    assert rep["missed_cycle_rate"] == 0.4  # (1 err + 1 over) / 5 requested
    assert rep["gateway_host"] == "127.0.0.1"


def test_build_report_in_cycle_boundary_is_strict() -> None:
    # 2.5s EXACTLY is within the cycle (strict ``>`` in measure.py); only 2.5001 misses.
    result = {"latencies": [2.5, 2.5001], "errors": [], "tokens": [], "n_requested": 2, "warmup": 0}
    rep = _build_report("openai", "default", "http://127.0.0.1:8642", result, None, 60.0)
    assert rep["n_over_in_cycle_timeout"] == 1  # only the 2.5001s sample
    assert rep["missed_cycle_rate"] == 0.5  # (0 err + 1 over) / 2 requested


def test_build_report_zero_requests_no_div0() -> None:
    # n_requested==0 must not ZeroDivisionError; miss_rate falls back to 0.0 and no summary block.
    result = {"latencies": [], "errors": [], "tokens": [], "n_requested": 0, "warmup": 0}
    rep = _build_report("google", "unknown", "http://127.0.0.1:8642", result, 0.012, 60.0)
    assert rep["missed_cycle_rate"] == 0.0
    assert rep["gateway_floor_s"] == 0.012  # floor passthrough
    assert "summary_s" not in rep  # no latencies -> no percentile block


def test_build_report_all_errors_summary_absent() -> None:
    # 100% errors: every cycle missed, and there is no latency distribution to summarise.
    result = {"latencies": [], "errors": ["E1", "E2"], "tokens": [], "n_requested": 2, "warmup": 0}
    rep = _build_report("xai", "fairness-off", "http://127.0.0.1:8642", result, None, 60.0)
    assert rep["n_ok"] == 0
    assert rep["n_err"] == 2
    assert rep["missed_cycle_rate"] == 1.0
    assert "summary_s" not in rep


def test_build_report_clean_run_no_miss() -> None:
    # Fast, error-free run: no missed cycles, percentile block present.
    result = {
        "latencies": [0.5, 1.0, 2.0],
        "errors": [],
        "tokens": [],
        "n_requested": 3,
        "warmup": 0,
    }
    rep = _build_report("anthropic", "fairness-off", "http://127.0.0.1:8642", result, None, 60.0)
    assert rep["n_over_in_cycle_timeout"] == 0
    assert rep["missed_cycle_rate"] == 0.0
    assert "summary_s" in rep


def _report(latencies: list[float], errors: list[str], n_requested: int) -> dict:
    """Build a report from the given measurement shape (fairness-off, no floor)."""
    result = {
        "latencies": list(latencies),
        "errors": list(errors),
        "tokens": [],
        "n_requested": n_requested,
        "warmup": 0,
    }
    return _build_report("anthropic", "fairness-off", "http://127.0.0.1:8642", result, None, 60.0)


def test_print_summary_cycle_holds(capsys) -> None:
    # p95 fast (≤2.5s) AND no missed cycles -> the 3s Mode-A cycle holds (doc06:104).
    _print_summary(_report([0.5] * 20, [], 20))
    out = capsys.readouterr().out
    assert "3s Mode-A cycle holds" in out
    assert "does NOT hold" not in out
    assert "WARN: high missed-cycle rate" not in out


def test_print_summary_p95_over(capsys) -> None:
    # p95 = 3000ms > 2.5s -> cycle does NOT hold, p95 stated as a reason (doc08:140).
    _print_summary(_report([3.0] * 20, [], 20))
    out = capsys.readouterr().out
    assert "does NOT hold" in out
    assert "p95=3000ms > 2.5s" in out


def test_print_summary_miss_over_via_errors(capsys) -> None:
    # Survivorship-bias guard: p95 is fast (≤2.5s) but a 10% ERROR fraction still misses cycles,
    # so the verdict must fail on missed-rate alone and emit the WARN. p95 is NOT a stated reason.
    _print_summary(_report([0.5] * 18, ["E", "E"], 20))
    out = capsys.readouterr().out
    assert "does NOT hold" in out
    assert "missed 10.0% > 5%" in out
    assert "ms > 2.5s" not in out  # p95 fast -> not a stated failure reason
    assert "WARN: high missed-cycle rate" in out


def test_print_summary_no_successful_samples(capsys) -> None:
    # All calls errored: no percentiles to compute, cycle not viable (100% missed).
    _print_summary(_report([], ["E"] * 5, 5))
    out = capsys.readouterr().out
    assert "NO successful samples" in out
    assert "p50" not in out  # returns before the percentile table


def test_print_summary_clean_run_negative_markers(capsys) -> None:
    # Negative-assertion counterpart to test_print_summary_p95_over: a fast, error-free
    # run prints the PASS verdict and NONE of the FAIL/over markers. This pins that the
    # `if p95_over or miss_over` branch is NOT taken on a clean run (mirrors
    # test_print_summary_cycle_holds, which only asserts the "holds" reason text).
    _print_summary(_report([0.5] * 20, [], 20))
    out = capsys.readouterr().out
    assert "3s Mode-A cycle holds" in out
    # FAIL / over markers must be absent on a clean run.
    assert "does NOT hold" not in out
    assert "> 2.5s" not in out  # no p95-over reason
    assert "> 5%" not in out  # no missed-rate-over reason
    assert "WARN: high missed-cycle rate" not in out


def test_print_summary_p95_and_miss_are_coupled(capsys) -> None:
    # Intentional COUPLING (NOT a separable isolation): the missed-cycle gate and the p95
    # gate share the SAME 2.5s in-cycle timeout (IN_CYCLE_TIMEOUT_S, measure.py:55). A run
    # whose p95 > 2.5s therefore NECESSARILY has miss_rate ≥ 5% (every >2.5s sample is a
    # missed cycle, _build_report counts it in n_over → missed_cycle_rate). So `p95_over`
    # can never fire WITHOUT `miss_over` co-firing under `if p95_over or miss_over`
    # (measure.py:344). Constructing p95_over-but-not-miss_over is mathematically
    # IMPOSSIBLE by design; we pin that BOTH reasons surface together instead.
    # 20 samples all at 3.0s: p95 = 3000ms > 2.5s AND 100% of cycles missed.
    _print_summary(_report([3.0] * 20, [], 20))
    out = capsys.readouterr().out
    assert "does NOT hold" in out
    assert "p95=3000ms > 2.5s" in out  # p95 reason surfaces
    assert "missed 100.0% > 5%" in out  # miss reason ALSO surfaces (coupled)
    assert "WARN: high missed-cycle rate" in out  # miss_over WARN co-fires


# ---------------------------------------------------------------------------
# Spend-guard regression pins (review of PR #202 / follow-up #200): the existing
# tests above cover only the pure math/report path; the fail-closed guards that
# prevent ACCIDENTAL paid API spend (~480 calls/run) had no regression pin. The
# tests below pin assert_dev_only / load_api_server_key / _parse_env_file and the
# two main() spend gates (--dry-run fires NO call; key-absent REFUSES before any call).
# All are hermetic: WAREHOUSE_ENV is always explicitly set/cleared, env-file points
# at a tmp_path (never the real config/dev/.env), and make_caller is monkeypatched
# to raise if the network path is ever reached.
# ---------------------------------------------------------------------------


def test_assert_dev_only_refuses_prod(monkeypatch) -> None:
    # WAREHOUSE_ENV=prod is fail-closed (safety.md / environments.md): this spike is dev-only.
    monkeypatch.setenv("WAREHOUSE_ENV", "prod")
    with pytest.raises(SystemExit) as exc:
        assert_dev_only("http://127.0.0.1:8642", allow_remote=False)
    assert "prod" in str(exc.value)


def test_assert_dev_only_refuses_non_loopback(monkeypatch) -> None:
    # A non-loopback gateway host without --allow-remote is refused (could be a prod/GCP gateway).
    monkeypatch.delenv("WAREHOUSE_ENV", raising=False)  # default dev
    with pytest.raises(SystemExit) as exc:
        assert_dev_only("http://34.4.104.112:8642", allow_remote=False)
    assert "non-loopback" in str(exc.value)


def test_assert_dev_only_non_loopback_with_allow_remote_warns(monkeypatch, capsys) -> None:
    # --allow-remote bypasses the loopback guard but WARNs loudly on stderr (dev-only override).
    monkeypatch.delenv("WAREHOUSE_ENV", raising=False)  # default dev
    assert assert_dev_only("http://34.4.104.112:8642", allow_remote=True) is None  # no exit
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "--allow-remote" in err


def test_assert_dev_only_loopback_dev_ok(monkeypatch) -> None:
    # The default loopback gateway in a dev env passes the guard (returns None, no exit).
    monkeypatch.setenv("WAREHOUSE_ENV", "dev")
    assert assert_dev_only("http://127.0.0.1:8642", allow_remote=False) is None


def test_load_api_server_key_env_wins_without_reading_file(monkeypatch, tmp_path) -> None:
    # The environment variable takes precedence and the (non-existent) env_file is NOT read.
    monkeypatch.setenv("API_SERVER_KEY", "env-key")
    missing = tmp_path / "does_not_exist.env"
    assert load_api_server_key(missing) == "env-key"
    assert not missing.exists()  # confirm the file was never created/required


def test_load_api_server_key_reads_env_file(monkeypatch, tmp_path) -> None:
    # With the env var unset, the value is parsed from the env file: quotes stripped,
    # comment + blank lines skipped (minimal KEY=VALUE parser, _parse_env_file).
    monkeypatch.delenv("API_SERVER_KEY", raising=False)
    env_file = tmp_path / "dev.env"
    env_file.write_text(
        '# a comment line\n\nAPI_SERVER_KEY="secret"\n',
        encoding="utf-8",
    )
    assert load_api_server_key(env_file) == "secret"


def test_load_api_server_key_none_file_returns_empty(monkeypatch) -> None:
    # No env var and no env file -> "" (caller refuses BEFORE any call when empty).
    monkeypatch.delenv("API_SERVER_KEY", raising=False)
    assert load_api_server_key(None) == ""


def test_parse_env_file_strips_quotes_skips_comments(tmp_path) -> None:
    # Direct pin on the minimal parser: comment / blank lines skipped, surrounding quotes stripped.
    env_file = tmp_path / "dev.env"
    env_file.write_text(
        "# header comment\n"
        "\n"
        "OTHER=ignored\n"
        "API_SERVER_KEY='single-quoted'\n",
        encoding="utf-8",
    )
    assert _parse_env_file(env_file, "API_SERVER_KEY") == "single-quoted"
    assert _parse_env_file(env_file, "OTHER") == "ignored"
    assert _parse_env_file(env_file, "ABSENT") == ""  # missing key -> ""


def test_parse_env_file_missing_path_returns_empty(tmp_path) -> None:
    # A non-existent file is tolerated (OSError -> ""), never raised.
    assert _parse_env_file(tmp_path / "nope.env", "API_SERVER_KEY") == ""


def test_main_dry_run_fires_no_api_call(monkeypatch, tmp_path, capsys) -> None:
    # --dry-run validates config and prints the plan WITHOUT any paid API call (spend guard).
    # make_caller is replaced with a poison that raises if invoked; it must NEVER be reached.
    def _poison(*_args, **_kwargs):
        raise AssertionError("network call attempted")

    monkeypatch.setattr("measure.make_caller", _poison)
    monkeypatch.delenv("API_SERVER_KEY", raising=False)  # key ABSENT
    monkeypatch.setenv("WAREHOUSE_ENV", "dev")
    missing_env = tmp_path / "absent.env"  # never read on --dry-run
    rc = main(["-p", "anthropic", "--dry-run", "--env-file", str(missing_env)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "=== DRY RUN (no API calls) ===" in out
    assert "API_SERVER_KEY  : ABSENT" in out  # key absent, plan still printed
    # _poison was never called (it would have raised) -> no network path taken.


def test_main_key_absent_refuses_before_any_call(monkeypatch, tmp_path) -> None:
    # No --dry-run + no API_SERVER_KEY (env unset, env_file absent) -> REFUSED before any call.
    # The poison make_caller proves the refusal happens BEFORE make_caller is ever built.
    def _poison(*_args, **_kwargs):
        raise AssertionError("network call attempted")

    monkeypatch.setattr("measure.make_caller", _poison)
    monkeypatch.delenv("API_SERVER_KEY", raising=False)
    monkeypatch.setenv("WAREHOUSE_ENV", "dev")
    missing_env = tmp_path / "absent.env"  # not present -> key resolves to ""
    with pytest.raises(SystemExit) as exc:
        main(["-p", "anthropic", "--env-file", str(missing_env)])
    msg = str(exc.value)
    assert "REFUSED" in msg
    assert "API_SERVER_KEY" in msg
