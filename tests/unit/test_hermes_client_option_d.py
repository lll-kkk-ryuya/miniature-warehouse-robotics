"""Bridge-side Option-D path tests for ``HermesClient.decide`` (plugin-ON, OPT-IN).

Option D leaves the Hermes Langfuse plugin ON so IT mints the trace + generation. On this
opt-in path ``HermesClient`` (``langfuse_owner=hermes_plugin``):

* uses a PLAIN ``openai.AsyncOpenAI`` (NO ``langfuse.openai`` wrapper → no duplicate generation),
* sends ``extra_headers={"X-Hermes-Session-Id": H}`` with ``H = seed_for(run_id, gen_id)`` so the
  plugin seeds its trace from our deterministic join key (plugin __init__:544),
* does NOT send ``langfuse_prompt=`` (the plugin has no ``prompt=`` path),
* DRIFT-DETECTs the echoed ``session_id`` (api_server.py:1515) — fail-open, never raises.

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
    """Stand-in for the OpenAI chat completion; optionally carries an echoed ``session_id``."""

    def __init__(self, content: object, *, session_id: object = "__unset__") -> None:
        self.choices = [_FakeChoice(content)]
        if session_id != "__unset__":
            self.session_id = session_id


class _FakeCompletions:
    def __init__(self, parent: "_FakeAsyncOpenAI") -> None:
        self._parent = parent

    async def create(self, **kwargs: object) -> object:
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
    """

    instances: list["_FakeAsyncOpenAI"] = []
    completion: object = _FakeCompletion(_VALID_CONTENT)
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
    _FakeAsyncOpenAI.raise_exc = None


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
    _FakeAsyncOpenAI.completion = _FakeCompletion(_VALID_CONTENT, session_id=seed_for("run-7", 42))
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
    _FakeAsyncOpenAI.completion = _FakeCompletion(_VALID_CONTENT, session_id="run-7:1")
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
    # Echoed session_id != H → the obs join would orphan → warn + skip, but the command is
    # STILL returned and NO exception is raised (R-26: obs never changes actuation).
    _patch_plain_openai(monkeypatch)
    _FakeAsyncOpenAI.completion = _FakeCompletion(_VALID_CONTENT, session_id="DIFFERENT")
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
    # No session_id on the response (older/raw path) → treated as drift → still returns command.
    _patch_plain_openai(monkeypatch)
    _FakeAsyncOpenAI.completion = _FakeCompletion(_VALID_CONTENT)  # no session_id
    client = HermesClient(
        "http://hermes:8642", langfuse_owner=LANGFUSE_OWNER_HERMES_PLUGIN, run_id="run-7"
    )
    cmd = _run(client, {"gen_id": 42})
    assert cmd["commands"][0]["action"] == "navigate"


@pytest.mark.unit
def test_matching_session_echo_no_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_plain_openai(monkeypatch)
    _FakeAsyncOpenAI.completion = _FakeCompletion(_VALID_CONTENT, session_id="run-7:42")
    client = HermesClient(
        "http://hermes:8642", langfuse_owner=LANGFUSE_OWNER_HERMES_PLUGIN, run_id="run-7"
    )
    cmd = _run(client, {"gen_id": 42})  # H == echoed → no drift
    assert cmd["commands"][0]["action"] == "navigate"


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
    _FakeAsyncOpenAI.completion = _FakeCompletion("not json", session_id="run-7:1")
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
