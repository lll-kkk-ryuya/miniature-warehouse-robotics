"""Bridge-side Option-D path tests for ``HermesClient.decide`` (plugin-ON, OPT-IN).

Option D leaves the Hermes Langfuse plugin ON so IT mints the trace + generation. On this
opt-in path ``HermesClient`` (``langfuse_owner=hermes_plugin``):

* uses a PLAIN ``openai.AsyncOpenAI`` (NO ``langfuse.openai`` wrapper → no duplicate generation),
* sends ``extra_headers={"X-Hermes-Session-Id": H}`` with ``H = seed_for(run_id, gen_id)`` so the
  plugin seeds its trace from our deterministic join key (plugin __init__:544),
* does NOT send ``langfuse_prompt=`` (the plugin has no ``prompt=`` path),
* DRIFT-DETECTs the echoed session id read from the ``X-Hermes-Session-Id`` RESPONSE HEADER (via
  ``with_raw_response`` → ``.headers``; the ``/v1/chat/completions`` path echoes it there, not in
  the body — api_server.py:1515) — fail-open, never raises.

The DEFAULT (Pattern A, ``langfuse_owner=bridge``) is verified to be UNCHANGED: it goes through
``langfuse.openai`` and keeps ``langfuse_prompt=``.

R-26: the failure contract (transport → ``LLMUnavailableError`` → Nav2-only; malformed body →
``ValueError`` → ignore cycle) is IDENTICAL on both paths, and the drift-detect only suppresses
an OBS join — it never changes the dispatched command and never raises into the cycle.

These tests fake the ``AsyncOpenAI`` client (openai SDK is installed in the .venv but no network
is touched) so the request shape + header + fail-open behaviour are pinned without a live Hermes.
"""

import asyncio

import openai
import pytest
from eval_sdk.seed import seed_for
from warehouse_llm_bridge.hermes_client import (
    HERMES_SESSION_HEADER,
    LANGFUSE_OWNER_BRIDGE,
    LANGFUSE_OWNER_HERMES_PLUGIN,
    HermesClient,
    resolve_langfuse_owner,
)

_VALID_CONTENT = (
    '{"reasoning": "ok", "commands": [{"bot": "bot1", "action": "navigate", '
    '"destination": "shelf_A"}], "priority_explanation": "p"}'
)


class _FakeMessage:
    def __init__(self, content: object) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: object) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    """Stand-in for the OpenAI chat completion (just the ``choices`` the parser reads)."""

    def __init__(self, content: object) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeRawResponse:
    """Stand-in for ``with_raw_response`` result: ``.headers`` (echo) + ``.parse()``.

    The Option-D drift-detect reads the echoed session id from the ``X-Hermes-Session-Id``
    RESPONSE HEADER (case-insensitive httpx-like mapping), then ``.parse()`` yields the typed
    completion — exactly the OpenAI SDK shape.
    """

    def __init__(self, completion: object, headers: dict[str, str]) -> None:
        self.headers = _Headers(headers)
        self._completion = completion

    def parse(self) -> object:
        return self._completion


class _Headers:
    """Case-insensitive header mapping (mirrors httpx.Headers ``.get``)."""

    def __init__(self, data: dict[str, str]) -> None:
        self._data = {k.lower(): v for k, v in data.items()}

    def get(self, key: str, default: object = None) -> object:
        return self._data.get(key.lower(), default)


class _FakeWithRawResponse:
    def __init__(self, parent: "_FakeAsyncOpenAI") -> None:
        self._parent = parent

    async def create(self, **kwargs: object) -> object:
        self._parent.create_kwargs = kwargs
        if self._parent.raise_exc is not None:
            raise self._parent.raise_exc
        return _FakeRawResponse(self._parent.completion, self._parent.echo_headers)


class _FakeCompletions:
    def __init__(self, parent: "_FakeAsyncOpenAI") -> None:
        self._parent = parent
        self.with_raw_response = _FakeWithRawResponse(parent)

    async def create(self, **kwargs: object) -> object:
        # Pattern A path (langfuse.openai wrapper) calls .create() directly (no raw response).
        self._parent.create_kwargs = kwargs
        if self._parent.raise_exc is not None:
            raise self._parent.raise_exc
        return self._parent.completion


class _FakeChat:
    def __init__(self, parent: "_FakeAsyncOpenAI") -> None:
        self.completions = _FakeCompletions(parent)


class _FakeAsyncOpenAI:
    """Records construction kwargs + the create() kwargs; returns a canned completion.

    ``instances`` (class-level) collects every constructed client so a test can assert WHICH
    SDK class was used (plain ``openai`` vs ``langfuse.openai``) by which factory was patched.
    ``echo_headers`` is the response-header set the Option-D path drift-detects against.
    """

    instances: list["_FakeAsyncOpenAI"] = []
    completion: object = _FakeCompletion(_VALID_CONTENT)
    echo_headers: dict[str, str] = {}
    raise_exc: BaseException | None = None

    def __init__(self, **kwargs: object) -> None:
        self.init_kwargs = kwargs
        self.create_kwargs: dict | None = None
        self.chat = _FakeChat(self)
        type(self).instances.append(self)


@pytest.fixture(autouse=True)
def _reset_fake() -> None:
    _FakeAsyncOpenAI.instances = []
    _FakeAsyncOpenAI.completion = _FakeCompletion(_VALID_CONTENT)
    _FakeAsyncOpenAI.echo_headers = {}
    _FakeAsyncOpenAI.raise_exc = None


def _echo_session(value: str) -> None:
    """Configure the X-Hermes-Session-Id RESPONSE HEADER the plugin path drift-detects on."""
    _FakeAsyncOpenAI.echo_headers = {HERMES_SESSION_HEADER: value}


def _patch_plain_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``from openai import AsyncOpenAI`` (Option-D path) yield the fake."""
    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI, raising=False)


def _run(client: HermesClient, situation: dict) -> dict:
    return asyncio.run(client.decide(situation))


# ── resolve_langfuse_owner: default + precedence + safe fallback ──────────────


@pytest.mark.unit
def test_owner_default_is_bridge_pattern_a() -> None:
    assert resolve_langfuse_owner({}, env={}) == LANGFUSE_OWNER_BRIDGE


@pytest.mark.unit
def test_owner_env_selects_plugin() -> None:
    owner = resolve_langfuse_owner({}, env={"WAREHOUSE_LANGFUSE_OWNER": "hermes_plugin"})
    assert owner == LANGFUSE_OWNER_HERMES_PLUGIN


@pytest.mark.unit
def test_owner_env_overrides_config() -> None:
    # env wins over config (env-over-config, like the other Bridge run-level labels).
    cfg = {"hermes": {"langfuse_owner": "hermes_plugin"}}
    assert resolve_langfuse_owner(cfg, env={"WAREHOUSE_LANGFUSE_OWNER": "bridge"}) == "bridge"


@pytest.mark.unit
def test_owner_config_used_when_env_absent() -> None:
    cfg = {"hermes": {"langfuse_owner": "hermes_plugin"}}
    assert resolve_langfuse_owner(cfg, env={}) == LANGFUSE_OWNER_HERMES_PLUGIN


@pytest.mark.unit
def test_owner_unknown_value_fails_safe_to_bridge() -> None:
    # An unknown/typo value NEVER silently enables Option D — it falls back to Pattern A.
    assert resolve_langfuse_owner({}, env={"WAREHOUSE_LANGFUSE_OWNER": "plugin"}) == "bridge"
    assert resolve_langfuse_owner({"hermes": "x"}, env={}) == "bridge"  # malformed block


@pytest.mark.unit
def test_owner_blank_env_falls_through_to_config_then_default() -> None:
    cfg = {"hermes": {"langfuse_owner": "hermes_plugin"}}
    assert resolve_langfuse_owner(cfg, env={"WAREHOUSE_LANGFUSE_OWNER": "  "}) == "hermes_plugin"
    assert resolve_langfuse_owner({}, env={"WAREHOUSE_LANGFUSE_OWNER": "  "}) == "bridge"


# ── Option-D decide(): header, no wrapper, no prompt link ─────────────────────


@pytest.mark.unit
def test_plugin_path_sends_session_header_h(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_plain_openai(monkeypatch)
    client = HermesClient(
        "http://hermes:8642",
        langfuse_owner=LANGFUSE_OWNER_HERMES_PLUGIN,
        run_id="run-7",
    )
    _echo_session(seed_for("run-7", 42))
    cmd = _run(client, {"gen_id": 42})
    assert cmd["commands"][0]["action"] == "navigate"
    create_kwargs = _FakeAsyncOpenAI.instances[0].create_kwargs
    # H = seed_for(run_id, gen_id) is sent in the canonical session header.
    assert create_kwargs["extra_headers"] == {HERMES_SESSION_HEADER: "run-7:42"}
    # The langfuse.openai prompt-link kwarg is NOT sent on the Option-D path.
    assert "langfuse_prompt" not in create_kwargs


@pytest.mark.unit
def test_plugin_path_drops_langfuse_prompt_even_if_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Even if a langfuse_prompt object is wired, the Option-D path must NOT send it (the plugin
    # has no prompt= path) — the managed-prompt regression is explicit, never silent.
    _patch_plain_openai(monkeypatch)
    client = HermesClient(
        "http://hermes:8642",
        langfuse_owner=LANGFUSE_OWNER_HERMES_PLUGIN,
        run_id="run-7",
        langfuse_prompt=object(),
    )
    _echo_session("run-7:1")
    _run(client, {"gen_id": 1})
    assert "langfuse_prompt" not in _FakeAsyncOpenAI.instances[0].create_kwargs


@pytest.mark.unit
def test_plugin_path_no_run_id_omits_header_but_still_decides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Blank run_id → H non-joinable → no header (obs join skipped) but the cycle STILL decides.
    _patch_plain_openai(monkeypatch)
    client = HermesClient(
        "http://hermes:8642", langfuse_owner=LANGFUSE_OWNER_HERMES_PLUGIN, run_id=""
    )
    cmd = _run(client, {"gen_id": 9})
    assert cmd["commands"][0]["action"] == "navigate"
    assert "extra_headers" not in _FakeAsyncOpenAI.instances[0].create_kwargs


@pytest.mark.unit
def test_plugin_path_missing_gen_id_omits_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_plain_openai(monkeypatch)
    client = HermesClient(
        "http://hermes:8642", langfuse_owner=LANGFUSE_OWNER_HERMES_PLUGIN, run_id="run-7"
    )
    _run(client, {})  # no gen_id
    assert "extra_headers" not in _FakeAsyncOpenAI.instances[0].create_kwargs


# ── Option-D drift-detect: fail-open, never raises into the cycle ─────────────


@pytest.mark.unit
def test_drift_detected_logs_but_returns_command(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Echoed session header != H → the obs join would orphan → warn + skip, but the command is
    # STILL returned and NO exception is raised (R-26: obs never changes actuation).
    _patch_plain_openai(monkeypatch)
    _echo_session("DIFFERENT")
    client = HermesClient(
        "http://hermes:8642", langfuse_owner=LANGFUSE_OWNER_HERMES_PLUGIN, run_id="run-7"
    )
    with caplog.at_level("WARNING"):
        cmd = _run(client, {"gen_id": 42})
    assert cmd["commands"][0]["action"] == "navigate"
    assert any("session drift" in r.message.lower() or "drift" in r.message for r in caplog.records)


@pytest.mark.unit
def test_missing_session_echo_treated_as_drift_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No echo header on the response → treated as drift → still returns command (fail-open).
    _patch_plain_openai(monkeypatch)
    # echo_headers stays {} (reset fixture) → no X-Hermes-Session-Id header.
    client = HermesClient(
        "http://hermes:8642", langfuse_owner=LANGFUSE_OWNER_HERMES_PLUGIN, run_id="run-7"
    )
    cmd = _run(client, {"gen_id": 42})
    assert cmd["commands"][0]["action"] == "navigate"


@pytest.mark.unit
def test_matching_session_echo_no_drift(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_plain_openai(monkeypatch)
    _echo_session("run-7:42")
    client = HermesClient(
        "http://hermes:8642", langfuse_owner=LANGFUSE_OWNER_HERMES_PLUGIN, run_id="run-7"
    )
    with caplog.at_level("WARNING"):
        cmd = _run(client, {"gen_id": 42})  # H == echoed header → no drift
    assert cmd["commands"][0]["action"] == "navigate"
    # The echoed header matched H, so NO drift warning is emitted (case-insensitive read).
    assert not any("drift" in r.message.lower() for r in caplog.records)


@pytest.mark.unit
def test_matching_session_echo_case_insensitive_header(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # HTTP headers are case-insensitive (httpx.Headers); a gateway echoing a different case
    # (e.g. lowercase) must STILL match H so we never report false drift on the live path.
    _patch_plain_openai(monkeypatch)
    _FakeAsyncOpenAI.echo_headers = {HERMES_SESSION_HEADER.lower(): "run-7:42"}
    client = HermesClient(
        "http://hermes:8642", langfuse_owner=LANGFUSE_OWNER_HERMES_PLUGIN, run_id="run-7"
    )
    with caplog.at_level("WARNING"):
        cmd = _run(client, {"gen_id": 42})
    assert cmd["commands"][0]["action"] == "navigate"
    assert not any("drift" in r.message.lower() for r in caplog.records)


# ── R-26: identical failure contract on the Option-D path ─────────────────────


@pytest.mark.unit
def test_plugin_path_transport_error_is_llmunavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from warehouse_llm_bridge.llm_client import LLMUnavailableError

    _patch_plain_openai(monkeypatch)
    _FakeAsyncOpenAI.raise_exc = openai.APIConnectionError(request=None)  # type: ignore[arg-type]
    client = HermesClient(
        "http://hermes:8642", langfuse_owner=LANGFUSE_OWNER_HERMES_PLUGIN, run_id="run-7"
    )
    with pytest.raises(LLMUnavailableError):
        _run(client, {"gen_id": 1})


@pytest.mark.unit
def test_plugin_path_malformed_content_is_valueerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_plain_openai(monkeypatch)
    _FakeAsyncOpenAI.completion = _FakeCompletion("not json")
    _echo_session("run-7:1")
    client = HermesClient(
        "http://hermes:8642", langfuse_owner=LANGFUSE_OWNER_HERMES_PLUGIN, run_id="run-7"
    )
    with pytest.raises(ValueError):
        _run(client, {"gen_id": 1})


# ── Default path is Pattern A (UNCHANGED): langfuse.openai wrapper + prompt link ─


@pytest.mark.unit
def test_default_owner_uses_langfuse_wrapper_and_keeps_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Default (langfuse_owner unset → bridge) must go through langfuse.openai and KEEP
    # langfuse_prompt= — and must NOT send the Option-D session header.
    import langfuse.openai as lf_openai

    monkeypatch.setattr(lf_openai, "AsyncOpenAI", _FakeAsyncOpenAI, raising=False)
    sentinel_prompt = object()
    client = HermesClient("http://hermes:8642", langfuse_prompt=sentinel_prompt)
    assert client._langfuse_owner == LANGFUSE_OWNER_BRIDGE  # default
    _run(client, {"gen_id": 5})
    create_kwargs = _FakeAsyncOpenAI.instances[0].create_kwargs
    assert create_kwargs["langfuse_prompt"] is sentinel_prompt
    assert "extra_headers" not in create_kwargs
