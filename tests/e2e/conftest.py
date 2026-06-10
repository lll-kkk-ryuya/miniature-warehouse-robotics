"""Pytest configuration for the slice2/3 end-to-end integration harness (#156).

Two responsibilities, both kept INSIDE ``tests/e2e/`` so this lane touches no
shared file (parallel-workflow.md §7.1 — ``pyproject.toml`` is skeleton-owned):

* register the ``e2e`` marker locally via :func:`pytest_configure`
  (``config.addinivalue_line`` — no edit to the shared ``pyproject.toml`` markers
  list), so ``pytest -m e2e`` / ``-m "not e2e"`` select the integration layer and
  no ``PytestUnknownMarkWarning`` is raised;
* the :func:`e2e_runtime` fixture that redirects every shared runtime path under a
  per-test ``tmp_path`` so the production-mirror wiring (default-constructed
  file-backed stores, llm_bridge.py:110-123) stays hermetic.
"""

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``e2e`` marker without editing the shared pyproject.toml."""
    config.addinivalue_line(
        "markers",
        "e2e: end-to-end integration of the commander-cycle wiring (real "
        "SituationBuilder + real Hermes parser, fake LLM brain + recording Nav2 "
        "transport). Host-runnable, no ROS / network / Gazebo (doc16 §11).",
    )


@pytest.fixture
def e2e_runtime(tmp_path, monkeypatch):
    """Redirect state / gen_store / idempotency_store / audit under ``tmp_path``.

    ``paths.py`` resolves these env vars at CALL time, so setting them before the
    stores are default-constructed is enough to keep each test hermetic while still
    exercising production's default-path wiring (rules/environments.md: paths come
    from config/env, never hardcoded). Returns the redirected runtime dir.
    """
    monkeypatch.setenv("WAREHOUSE_ENV", "dev")
    monkeypatch.setenv("WAREHOUSE_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("WAREHOUSE_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    return tmp_path
