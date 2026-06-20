"""Per-IP reconnect-rate cap (doc22 §10:235) — sliding window, per-IP isolation, clamp."""

import pytest
from warehouse_web_bridge.ratelimit import ReconnectRateLimiter


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


@pytest.mark.unit
def test_allows_up_to_cap_then_rejects_within_window():
    lim = ReconnectRateLimiter(max_per_window=3, window_s=10.0, clock=_Clock())
    assert [lim.allow("1.1.1.1") for _ in range(3)] == [True, True, True]
    assert lim.allow("1.1.1.1") is False  # 4th within the window is reconnect-storm capped


@pytest.mark.unit
def test_window_slides_so_old_hits_expire():
    clk = _Clock()
    lim = ReconnectRateLimiter(max_per_window=2, window_s=10.0, clock=clk)
    assert lim.allow("ip") and lim.allow("ip")
    assert lim.allow("ip") is False
    clk.t = 11.0  # advance past the window
    assert lim.allow("ip") is True  # the two old hits aged out


@pytest.mark.unit
def test_per_ip_isolation():
    lim = ReconnectRateLimiter(max_per_window=1, window_s=10.0, clock=_Clock())
    assert lim.allow("a") is True
    assert lim.allow("a") is False
    assert lim.allow("b") is True  # a different source IP is unaffected


@pytest.mark.unit
def test_cap_floored_to_one():
    lim = ReconnectRateLimiter(max_per_window=0, window_s=5.0, clock=_Clock())
    assert lim.allow("x") is True  # max(1, 0) == 1
    assert lim.allow("x") is False
