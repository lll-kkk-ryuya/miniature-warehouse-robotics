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
    assert cfg == {"ws_path": "/ws", "mode": "simple", "lan": True, "token_required": True}
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
