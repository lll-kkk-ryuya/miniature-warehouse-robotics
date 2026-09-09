"""web_bridge settings resolution + browser config contract (doc22 §5.1/§16).

Pins fail-open defaults (gateway starts before the base.yaml block lands), the base/overlay
field mapping, LAN detection, and — critically — that ``GET /config`` never leaks a secret or
server internal (doc22:244,:254). Pure, host-runnable.
"""

import pytest
from warehouse_web_bridge.settings import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_RECONNECT_MAX,
    DEFAULT_RECONNECT_WINDOW_S,
    DEFAULT_RECORDINGS_DIR,
    DEFAULT_SNAPSHOT_HZ,
    browser_config,
    resolve_settings,
)


@pytest.mark.unit
def test_fail_open_defaults_when_no_web_bridge_block():
    s = resolve_settings({})  # base.yaml block not landed yet
    assert (s.host, s.port, s.snapshot_hz) == (DEFAULT_HOST, DEFAULT_PORT, DEFAULT_SNAPSHOT_HZ)
    assert s.recordings_dir == DEFAULT_RECORDINGS_DIR
    assert s.static_dir == ""  # no SPA mounted until S3 builds it
    assert s.lan is False  # loopback default (doc22:254)
    assert s.token_required is False


@pytest.mark.unit
def test_base_and_overlay_fields_resolved():
    cfg = {
        "web_bridge": {
            "port": 8646,
            "snapshot_hz": 2,
            "host": "0.0.0.0",
            "recordings_dir": "/opt/warehouse/recordings",
            "allowed_origins": ["http://localhost:3000"],
        }
    }
    s = resolve_settings(cfg, token="sekret")
    assert s.port == 8646
    assert s.snapshot_hz == 2.0  # coerced to float
    assert s.recordings_dir == "/opt/warehouse/recordings"
    assert s.allowed_origins == ("http://localhost:3000",)
    assert s.lan is True  # non-loopback bind ⇒ LAN-exposed
    assert s.token_required is True  # token present (value not stored)


@pytest.mark.unit
def test_browser_config_is_browser_facing_only_and_leaks_no_secret():
    s = resolve_settings(
        {"web_bridge": {"host": "0.0.0.0", "recordings_dir": "/secret/path"}}, token="sup3r-secret"
    )
    cfg = browser_config(s, mode="simple")
    # EXACT equality on purpose: this is the canary that catches a field being added to the
    # browser payload without anyone asking whether it is safe to hand a browser (doc22:254).
    assert cfg == {
        "ws_path": "/ws",
        "mode": "simple",
        "lan": True,
        "token_required": True,
        "langfuse_base_url": "",
    }
    # no secret, no server internals (token / host / port / recordings_dir) ever returned
    blob = repr(cfg)
    for leak in ("sup3r-secret", "/secret/path", "8646", "0.0.0.0"):
        assert leak not in blob


@pytest.mark.unit
@pytest.mark.parametrize(
    "host, lan",
    [
        ("127.0.0.1", False),
        ("::1", False),
        ("localhost", False),
        ("192.168.1.5", True),
        ("0.0.0.0", True),
    ],
)
def test_lan_detection(host, lan):
    assert resolve_settings({"web_bridge": {"host": host}}).lan is lan


@pytest.mark.unit
def test_bounded_caps_floored_to_one():
    # a stray 0 must not disable the bounded queue / client cap that #187 relies on
    # (asyncio.Queue(maxsize<=0) is unbounded) — doc22:230-233.
    s = resolve_settings({"web_bridge": {"client_queue_max": 0, "max_clients": 0}})
    assert s.client_queue_max == 1
    assert s.max_clients == 1


@pytest.mark.unit
def test_reconnect_cap_defaults_and_override():
    s = resolve_settings({})
    assert s.reconnect_max_per_window == DEFAULT_RECONNECT_MAX
    assert s.reconnect_window_s == DEFAULT_RECONNECT_WINDOW_S
    s2 = resolve_settings({"web_bridge": {"reconnect_max_per_window": 0, "reconnect_window_s": 3}})
    assert s2.reconnect_max_per_window == 1  # floored to >=1
    assert s2.reconnect_window_s == 3.0


# --------------------------------------------------------------------------------------
# langfuse_base_url — the ONE /config field that ends up inside a browser href (doc22:194,
# :244, :332). Expected values below are written as LITERALS, never produced by calling the
# production helper, so the oracle is independent of the implementation under test.
# --------------------------------------------------------------------------------------


@pytest.mark.unit
def test_langfuse_base_url_defaults_to_empty_meaning_no_deep_link():
    # Unconfigured is the normal case: the console must fall back to showing a copyable
    # trace_id, which is the same no-link state doc22:152 defines for a null trace_id.
    assert resolve_settings({}).langfuse_base_url == ""
    assert resolve_settings({"web_bridge": {}}).langfuse_base_url == ""


@pytest.mark.unit
@pytest.mark.parametrize(
    "configured, expected",
    [
        # kept verbatim — this is the shape doc22:346 documents
        ("https://cloud.langfuse.com/project/abc123", "https://cloud.langfuse.com/project/abc123"),
        ("http://localhost:3001/project/dev", "http://localhost:3001/project/dev"),
        # trailing slashes stripped so `{base}/traces/{id}` never doubles the separator
        ("https://cloud.langfuse.com/project/abc123/", "https://cloud.langfuse.com/project/abc123"),
        (
            "https://cloud.langfuse.com/project/abc123///",
            "https://cloud.langfuse.com/project/abc123",
        ),
        # surrounding whitespace is a copy-paste artifact, not a rejection
        ("  https://lf.example/project/p1  ", "https://lf.example/project/p1"),
        # scheme match is case-insensitive; the value itself is NOT lowercased (path case matters)
        ("HTTPS://LF.example/project/AbC", "HTTPS://LF.example/project/AbC"),
    ],
)
def test_langfuse_base_url_accepted_values_are_normalized(configured, expected):
    s = resolve_settings({"web_bridge": {"langfuse_base_url": configured}})
    assert s.langfuse_base_url == expected
    assert browser_config(s, mode="none")["langfuse_base_url"] == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "hostile",
    [
        # React renders a javascript:/data: href without blocking it — the gateway must not
        # hand one to the browser in the first place.
        "javascript:alert(document.cookie)",
        "JaVaScRiPt:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        # protocol-relative: would silently inherit the page scheme and leave the origin
        "//evil.example/project/p1",
        # not a browsable URL at all
        "ftp://lf.example/project/p1",
        "file:///etc/passwd",
        "cloud.langfuse.com/project/p1",  # scheme-less
        # credentials would be published to every browser that GETs /config (doc22:254)
        "https://user:pa55w0rd@lf.example/project/p1",
        "https://token@lf.example/project/p1",
        # mangled config: no host, or embedded whitespace/newline/NUL
        "https://",
        "https:///project/p1",
        "https://lf.example/pro ject",
        "https://lf.example/p1\nX-Injected: 1",
        "https://evil.example\x00.lf.example/project/p1",
        # HOMOGRAPH HOSTS. Each reads as "lf.example" in a config review, but browsers apply
        # UTS-46 and resolve the host to lf.example.evil.example. Rejecting these is the whole
        # reason this validator exists rather than a bare startswith("https://").
        "https://lf.example。evil.example/project/p1",  # U+3002 ideographic full stop
        "https://lf.example．evil.example/project/p1",  # U+FF0E fullwidth full stop
        "https://lf.example｡evil.example/project/p1",  # U+FF61 halfwidth ideographic stop
        # BACKSLASH: a browser normalises \ to /, so the real origin is evil.example even though
        # a hand parser reads the host as "lf.example".
        "https://lf.example\\@evil.example/project/p1",
        "https:/\\/\\evil.example/project/p1",
        "https://\\evil.example/project/p1",
        # query / fragment would swallow the /traces/<id> the console appends
        "https://lf.example/project/p1?next=",
        "https://evil.example/phish#",
    ],
)
def test_langfuse_base_url_rejects_anything_not_a_plain_http_url(hostile):
    s = resolve_settings({"web_bridge": {"langfuse_base_url": hostile}})
    assert s.langfuse_base_url == ""  # fail-CLOSED: no link beats a dangerous link
    # and it must not reach the browser payload in any form
    assert browser_config(s, mode="none")["langfuse_base_url"] == ""
    assert hostile not in repr(browser_config(s, mode="none"))


@pytest.mark.unit
@pytest.mark.parametrize("wrong_type", [123, 3.5, True, ["https://lf.example"], {"url": "x"}, None])
def test_langfuse_base_url_of_the_wrong_type_degrades_instead_of_crashing(wrong_type):
    # A hand-edited overlay must never stop the gateway from starting. (Scoped claim: this field
    # is total; the numeric fields in this module still raise on a non-numeric value.)
    assert (
        resolve_settings({"web_bridge": {"langfuse_base_url": wrong_type}}).langfuse_base_url == ""
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "pathological",
    [
        "https://" + "a" * 4000 + ".example/project/p1",  # over the length cap
        "https://[not-a-v6/project/p1",  # urlsplit raises ValueError here
        "https://exam＠ple.com/project/p1",  # NFKC-invalid netloc — urlsplit raises
    ],
)
def test_langfuse_base_url_never_raises_on_a_pathological_value(pathological):
    # normalize_langfuse_base_url is documented as total: urlsplit DOES raise for some inputs,
    # and an exception here would abort gateway startup from a mere cosmetic config key.
    assert (
        resolve_settings({"web_bridge": {"langfuse_base_url": pathological}}).langfuse_base_url
        == ""
    )


@pytest.mark.unit
def test_a_rejected_value_is_logged_so_the_operator_can_see_why_there_is_no_link(caplog):
    # "fail-CLOSED **and logged**" is half the contract (settings.py docstring): silently
    # swallowing a typo'd URL turns a config mistake into an unexplained missing feature.
    # Precedent for pinning the log half: test_web_trace.py::...fails_safe_to_pattern_a_and_logs.
    with caplog.at_level("WARNING"):
        assert resolve_settings({"web_bridge": {"langfuse_base_url": "javascript:alert(1)"}})
    assert any("langfuse_base_url" in r.message for r in caplog.records)


@pytest.mark.unit
def test_a_non_project_scoped_url_is_accepted_but_warned_about(caplog):
    # https://cloud.langfuse.com is exactly what LANGFUSE_HOST holds (config/dev/.env.example),
    # so it is the likeliest operator mistake: it passes every safety rule yet deep-links to a
    # 404. Warn (self-hosted layouts differ) rather than reject.
    with caplog.at_level("WARNING"):
        s = resolve_settings({"web_bridge": {"langfuse_base_url": "https://cloud.langfuse.com"}})
    assert s.langfuse_base_url == "https://cloud.langfuse.com"  # accepted
    assert any("/project/" in r.message or "/project/" in str(r.args) for r in caplog.records)


@pytest.mark.unit
def test_a_good_project_url_produces_no_warning_at_all(caplog):
    # The counterpart that stops the warning from being unconditional noise.
    with caplog.at_level("WARNING"):
        s = resolve_settings(
            {"web_bridge": {"langfuse_base_url": "https://cloud.langfuse.com/project/abc123"}}
        )
    assert s.langfuse_base_url == "https://cloud.langfuse.com/project/abc123"
    assert [r for r in caplog.records if "langfuse_base_url" in r.message] == []


@pytest.mark.unit
def test_langfuse_base_url_is_the_only_new_browser_field_and_carries_no_secret():
    s = resolve_settings(
        {
            "web_bridge": {
                "host": "0.0.0.0",
                "recordings_dir": "/secret/path",
                "langfuse_base_url": "https://cloud.langfuse.com/project/abc123",
            }
        },
        token="sup3r-secret",
    )
    cfg = browser_config(s, mode="open-rmf")
    assert cfg == {
        "ws_path": "/ws",
        "mode": "open-rmf",
        "lan": True,
        "token_required": True,
        "langfuse_base_url": "https://cloud.langfuse.com/project/abc123",
    }
    blob = repr(cfg)
    for leak in ("sup3r-secret", "/secret/path", "8646", "0.0.0.0"):
        assert leak not in blob


@pytest.mark.unit
def test_trace_deep_link_the_console_builds_is_a_well_formed_langfuse_url():
    # Compose the join shape TraceLink.tsx uses (`${base}/traces/${trace_id}`) and pin the result
    # as a literal, to show WHY trailing-slash stripping is load-bearing and not cosmetic: the
    # join must not produce `//traces/`. NB this re-implements the join in Python — it pins the
    # server half only, and would not notice TraceLink.tsx changing `/traces/` to `/trace/`
    # (web/console has no unit-test runner today; see the PR residuals).
    s = resolve_settings(
        {"web_bridge": {"langfuse_base_url": "https://cloud.langfuse.com/project/abc123/"}}
    )
    trace_id = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
    assert (
        f"{s.langfuse_base_url}/traces/{trace_id}"
        == "https://cloud.langfuse.com/project/abc123/traces/0f1e2d3c4b5a69788796a5b4c3d2e1f0"
    )
