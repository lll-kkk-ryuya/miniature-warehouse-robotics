"""YAML -> :class:`RunManifest` loader (doc09:48 ``out/runs/<run_id>/manifest.yaml`` shape).

Errors are NEVER swallowed — every malformed input raises before a run can start:

- unreadable file            -> ``OSError`` (propagates from ``Path.read_text``);
- YAML syntax error          -> ``yaml.YAMLError`` (propagates from ``yaml.safe_load``);
- non-mapping document root  -> ``ValueError`` (explicit, raised here);
- schema violation           -> ``pydantic.ValidationError`` (propagates from
  ``RunManifest.model_validate`` — includes the fail-closed unknown ``schema_version``).

``yaml.safe_load`` is used (never ``yaml.load``): a manifest is data, not code.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from warehouse_llm_bridge.robotics.composition.manifest import RunManifest


def load_run_manifest_text(text: str) -> RunManifest:
    """Parse a YAML document string into a validated :class:`RunManifest`.

    Raises:
        yaml.YAMLError: the text is not valid YAML.
        ValueError: the YAML root is not a mapping (e.g. a list, a scalar, or empty).
        pydantic.ValidationError: the mapping violates the ``run_manifest.v1`` schema.
    """
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"run manifest root must be a YAML mapping, got {type(data).__name__}")
    return RunManifest.model_validate(data)


def load_run_manifest(path: Path) -> RunManifest:
    """Read and validate a run manifest YAML file (see :func:`load_run_manifest_text`).

    Raises:
        OSError: the file cannot be read (missing file, permission, ...).
        yaml.YAMLError | ValueError | pydantic.ValidationError: as in
            :func:`load_run_manifest_text`.
    """
    return load_run_manifest_text(path.read_text(encoding="utf-8"))
