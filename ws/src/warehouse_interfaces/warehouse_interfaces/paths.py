"""Shared runtime paths and environment resolution (doc16 §4, doc19).

All processes (rclpy nodes and the non-rclpy MCP server) resolve runtime file
paths here so the LLM Bridge / MCP Server / State Cache stay in sync. The
environment is selected by ``WAREHOUSE_ENV`` (dev | stg | prod, default dev).
"""

import os
from pathlib import Path

VALID_ENVS = ("dev", "stg", "prod")


def warehouse_env() -> str:
    """Return the selected environment from ``WAREHOUSE_ENV`` (default ``dev``)."""
    env = os.environ.get("WAREHOUSE_ENV", "dev")
    if env not in VALID_ENVS:
        raise ValueError(f"invalid WAREHOUSE_ENV {env!r}; expected one of {VALID_ENVS}")
    return env


def runtime_dir() -> Path:
    """Runtime dir: ``/run/warehouse`` on prod (systemd), else ``/tmp/warehouse``.

    Overridable via ``WAREHOUSE_RUNTIME_DIR`` (e.g. for tests).
    """
    override = os.environ.get("WAREHOUSE_RUNTIME_DIR")
    if override:
        return Path(override)
    return Path("/run/warehouse") if warehouse_env() == "prod" else Path("/tmp/warehouse")


def state_path() -> Path:
    """Path to the atomic state snapshot written by State Cache (doc16 §4)."""
    return runtime_dir() / "state.json"


def gen_store_path() -> Path:
    """Path to the gen_store shared by LLM Bridge and MCP Server (doc16 §4/§6)."""
    return runtime_dir() / "gen_store"


def audit_log_path() -> Path:
    """Path to the command audit log (JSON Lines), overridable via env."""
    override = os.environ.get("WAREHOUSE_AUDIT_LOG_PATH")
    return Path(override) if override else runtime_dir() / "audit.jsonl"


def config_paths() -> list[Path]:
    """Warehouse config resolution order: base then env overlay (doc19)."""
    root = Path(os.environ.get("WAREHOUSE_CONFIG_DIR", "config"))
    return [root / "warehouse.base.yaml", root / warehouse_env() / "warehouse.yaml"]
