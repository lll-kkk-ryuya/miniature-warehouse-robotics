"""Offline unit tests for the ``x_er_bridge`` pure helpers (docs/mode-x-er/08 §5 step1).

No rclpy / no ROS / no network: importing the module at collection time IS the test of
the runtime-dep import guard (plain pytest must not require ROS, doc16 §11 — the same
discipline ``tests/unit/test_bringup_launch.py`` applies via importorskip).

R-26 discipline — expected values are INDEPENDENT ORACLES, never derived from the
implementation: the JSON payloads are literals written here, and the rejection
expectations come from the doc08 invariants (v0 request source = a JSON file of
``ErTaskRequest`` fields, ONLY consumed when set; the request contract is
``known_locations ⊆ KNOWN_LOCATIONS``, er_task.py:31,46-52). How each goes red under
mutation:

* weaken ``load_request_fixture`` to skip ``model_validate`` (e.g. ``model_construct``
  or returning the raw dict) -> ``test_unknown_location_is_rejected`` /
  ``test_missing_request_id_is_rejected`` stop raising -> red;
* weaken the non-object JSON guard -> ``test_non_object_json_raises`` red;
* weaken ``resolve_request_fixture_path``'s blank guard (return ``Path("")``) ->
  ``test_blank_value_returns_none`` red; drop the "ONLY consumed when set" behaviour
  (default a path when unset) -> ``test_unset_*`` red; silently ignore a present but
  non-string value -> ``test_non_string_value_is_rejected`` red.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from warehouse_llm_bridge.robotics.er_task import ErTaskRequest
from warehouse_llm_bridge.x_er_bridge import (
    load_request_fixture,
    resolve_nav2_forwarder,
    resolve_request_fixture_path,
)
from warehouse_mcp_server.nav2_client import Nav2RestForwarder

# ── resolve_request_fixture_path (mode_x_er.request_fixture, dev-only v0 source) ──────


def test_unset_mode_x_er_block_returns_none() -> None:
    assert resolve_request_fixture_path({}) is None


def test_non_mapping_mode_x_er_block_returns_none() -> None:
    assert resolve_request_fixture_path({"mode_x_er": "oops"}) is None


def test_unset_request_fixture_key_returns_none() -> None:
    # The doc08 §3 frozen keys alone (no request_fixture) => no request source.
    assert resolve_request_fixture_path({"mode_x_er": {"enabled": False}}) is None


def test_blank_value_returns_none() -> None:
    assert resolve_request_fixture_path({"mode_x_er": {"request_fixture": ""}}) is None
    assert resolve_request_fixture_path({"mode_x_er": {"request_fixture": "   "}}) is None


def test_set_value_returns_path() -> None:
    cfg = {"mode_x_er": {"request_fixture": "/tmp/warehouse/request.json"}}
    assert resolve_request_fixture_path(cfg) == Path("/tmp/warehouse/request.json")


@pytest.mark.safety
def test_non_string_value_is_rejected() -> None:
    # Present-but-malformed config is a startup refusal (fail-closed), not silently unset.
    with pytest.raises(ValueError, match="request_fixture"):
        resolve_request_fixture_path({"mode_x_er": {"request_fixture": 5}})


# ── load_request_fixture (JSON file -> validated ErTaskRequest) ───────────────────────


def _write_fixture(tmp_path: Path, payload: Any) -> Path:
    path = tmp_path / "request.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_valid_fixture_parses_to_er_task_request(tmp_path: Path) -> None:
    payload = {
        "request_id": "req-001",
        "transcript": "move the red box to shelf_2",
        "known_robots": ["bot1", "bot2"],
        "known_locations": ["shelf_1", "shelf_2"],
        "calibration_id": "cam0",
    }
    request = load_request_fixture(_write_fixture(tmp_path, payload))
    assert isinstance(request, ErTaskRequest)
    assert request.request_id == "req-001"
    assert request.transcript == "move the red box to shelf_2"
    assert request.known_robots == ["bot1", "bot2"]
    assert request.known_locations == ["shelf_1", "shelf_2"]
    assert request.calibration_id == "cam0"
    # Defaults ride the frozen contract: allowed_actions = the CommandAction vocabulary
    # (er_task.py:28,43 — derived from the enum, doc mode-x-er/03:48).
    assert "navigate" in request.allowed_actions


def test_str_path_accepted(tmp_path: Path) -> None:
    path = _write_fixture(tmp_path, {"request_id": "req-str-path"})
    request = load_request_fixture(str(path))
    assert request.request_id == "req-str-path"


def test_malformed_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "request.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):  # json.JSONDecodeError is a ValueError
        load_request_fixture(path)


def test_non_object_json_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="JSON object"):
        load_request_fixture(_write_fixture(tmp_path, ["not", "an", "object"]))


@pytest.mark.safety
def test_unknown_location_is_rejected(tmp_path: Path) -> None:
    # R-26: the fixture rides the SAME L4 input hygiene as any request —
    # known_locations ⊆ KNOWN_LOCATIONS (er_task.py:46-52), so a typo'd location is a
    # startup refusal and can never be advertised to the ER model.
    payload = {"request_id": "req-002", "known_locations": ["not_a_place"]}
    with pytest.raises(ValidationError):
        load_request_fixture(_write_fixture(tmp_path, payload))


@pytest.mark.safety
def test_unknown_action_is_rejected(tmp_path: Path) -> None:
    # allowed_actions ⊆ CommandAction (er_task.py:54-61) — same fail-closed hygiene.
    payload = {"request_id": "req-003", "allowed_actions": ["set_velocity"]}
    with pytest.raises(ValidationError):
        load_request_fixture(_write_fixture(tmp_path, payload))


def test_missing_request_id_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_request_fixture(_write_fixture(tmp_path, {"transcript": "hi"}))


# ── resolve_nav2_forwarder (mode_x_er.dispatch.forward_to_nav2, doc08 §3 / #421) ──────
#
# R-26: a motion-enabling flag must be SAFE-OFF unless it is exactly YAML `true`. Expected
# values are independent (the base ship value is false = 0 actuation; only a literal true
# flips it). How each goes red: relax `forward is not True` to `if forward` -> the truthy
# string/int cases return a forwarder -> red; drop the base_url fail-closed raise -> the
# missing-endpoint case stops raising -> red.

_BASE_URL = "http://localhost:8645"


def test_forwarder_off_when_dispatch_block_absent() -> None:
    # base.yaml ships mode_x_er without any dispatch override on some overlays => safe-OFF.
    assert resolve_nav2_forwarder({"mode_x_er": {"enabled": False}}) is None
    assert resolve_nav2_forwarder({}) is None


def test_forwarder_off_when_forward_flag_false_or_absent() -> None:
    assert resolve_nav2_forwarder({"mode_x_er": {"dispatch": {}}}) is None
    assert resolve_nav2_forwarder({"mode_x_er": {"dispatch": {"forward_to_nav2": False}}}) is None


@pytest.mark.safety
@pytest.mark.parametrize("flag", ["true", "True", 1, "yes"])
def test_forwarder_off_for_non_boolean_truthy_values(flag: Any) -> None:
    # STRICT: only the YAML boolean true enables actuation — a truthy string/int typo stays OFF.
    cfg = {
        "mode_x_er": {"dispatch": {"forward_to_nav2": flag}},
        "nav2_bridge": {"base_url": _BASE_URL},
    }
    assert resolve_nav2_forwarder(cfg) is None


def test_forwarder_on_reuses_nav2_bridge_base_url() -> None:
    cfg = {
        "mode_x_er": {"dispatch": {"forward_to_nav2": True}},
        "nav2_bridge": {"base_url": _BASE_URL},
    }
    forwarder = resolve_nav2_forwarder(cfg)
    assert isinstance(forwarder, Nav2RestForwarder)
    assert forwarder._base_url == _BASE_URL  # endpoint reused, no new key invented


@pytest.mark.safety
@pytest.mark.parametrize("nav2", [{}, {"base_url": ""}, {"base_url": None}, "oops", None])
def test_forwarder_true_without_endpoint_is_fail_closed(nav2: Any) -> None:
    # Requesting actuation with no reachable endpoint must refuse startup, not silently OFF.
    cfg: dict[str, Any] = {"mode_x_er": {"dispatch": {"forward_to_nav2": True}}}
    if nav2 is not None:
        cfg["nav2_bridge"] = nav2
    with pytest.raises(ValueError, match="base_url"):
        resolve_nav2_forwarder(cfg)
