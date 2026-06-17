"""Guard the real Langfuse SDK surface used by ``eval_sdk.tracer``.

This test intentionally uses the installed SDK rather than a fake. It is gated by
``WAREHOUSE_RUN_LANGFUSE_API_CONTRACT=1`` so the default unit suite remains
Langfuse-free; the dedicated CI job installs the optional extra and runs this file.
No network or credentials are required. A disabled Langfuse client still exposes
the local call surface we depend on.
"""

from __future__ import annotations

import os
import re

import pytest

_RUN_CONTRACT = os.environ.get("WAREHOUSE_RUN_LANGFUSE_API_CONTRACT") == "1"
pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(
        not _RUN_CONTRACT,
        reason="Langfuse API contract guard is opt-in; set WAREHOUSE_RUN_LANGFUSE_API_CONTRACT=1",
    ),
]


def _langfuse_module():
    import langfuse

    return langfuse


_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _version_tuple(version: str) -> tuple[int, int]:
    parts = version.split(".")
    return int(parts[0]), int(parts[1])


def test_langfuse_optional_extra_is_new_enough_for_tracer_seam() -> None:
    """The optional extra must match the v4.9+ surface pinned in setup.py/docs."""
    langfuse = _langfuse_module()
    version = getattr(langfuse, "__version__", "0.0")
    assert _version_tuple(version) >= (4, 9)


def test_langfuse_client_exposes_trace_observation_api_without_credentials() -> None:
    """``LangfuseTracer`` needs these calls even when the client is disabled."""
    langfuse = _langfuse_module()
    from eval_sdk.seed import seed_for

    client = langfuse.get_client()

    trace_id = client.create_trace_id(seed=seed_for("run_contract", 7))
    assert _TRACE_ID_RE.fullmatch(trace_id)

    cm = client.start_as_current_observation(
        name="api-contract-probe",
        as_type="span",
        trace_context={"trace_id": trace_id},
    )
    span = cm.__enter__()
    try:
        assert span is not None
    finally:
        cm.__exit__(None, None, None)


def test_langfuse_exposes_trace_attribute_propagation_api() -> None:
    """Trace labels use ``propagate_attributes`` around the active observation."""
    langfuse = _langfuse_module()
    client = langfuse.get_client()
    propagate = getattr(client, "propagate_attributes", None)
    if propagate is None:
        propagate = getattr(langfuse, "propagate_attributes", None)

    assert callable(propagate), "Langfuse propagate_attributes API is missing"

    cm = propagate(
        session_id="session_contract",
        tags=["provider_contract", "none"],
        metadata={"gen_id": 7, "trace_id": "0123456789abcdef0123456789abcdef"},
    )
    cm.__enter__()
    try:
        assert cm is not None
    finally:
        cm.__exit__(None, None, None)
