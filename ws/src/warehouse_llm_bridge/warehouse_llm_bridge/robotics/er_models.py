"""Single source for the Gemini Robotics-ER model identifiers (L4 Robotics Bridge, 0 actuation).

Why this module exists (Issue #690): ``gemini-robotics-er-1.6-preview`` was **shut down on
2026-08-31** (Gemini API deprecations page, read 2026-09-16) and the id was hard-coded in the
adapter default, the Hermes gateway yaml, two launcher log lines, the CLI probe and two live-test
helpers — none of them cross-checked. The next ER generation will be retired the same way
(1.5 -> 2026-04-30, 1.6 -> 2026-08-31, ~4.5 months each), so the id lives HERE and everything else
either imports it or is pinned to it by ``tests/unit/test_er_model_id_single_source.py``.

Two distinct strings — do not conflate them:

- ``ER_DIRECT_MODEL_ID`` — the **API model id** that goes on the wire
  (``.../v1beta/models/<id>:generateContent``). Overridable per deployment via the additive
  config key ``robotics.er_gateway.direct_model`` (``config/warehouse.base.yaml``) and therefore
  via ``WAREHOUSE__ROBOTICS__ER_GATEWAY__DIRECT_MODEL`` (doc19 env overlay); tests/CLI use
  ``MWR_ER_MODEL``. Docs canonical row: docs/mode-x-er/04-er-input-modalities-and-stt.md (model).
- ``ER_SOURCE_MODEL`` — the **audit tag** written to ``RawModelOutput.source_model`` /
  ``GeminiErAdapter.name``. Observation-only: the L3 core never branches on it
  (robotics_planning_core/models/boundary.py, validator/seams.py). Its value is pinned by
  existing units and MUST NOT change with the API model generation.

Current generation (Gemini API changelog 2026-07-30): ``gemini-robotics-er-2-preview`` (REST
``generateContent`` — the path this adapter uses) and ``gemini-robotics-er-2-streaming-preview``
(Live API / WebSocket only; NOT usable over REST and NOT the Hermes ``/v1/chat/completions`` path
— hence not selected here). Pointing output (``[y, x]`` normalized 0-1000) is unchanged from 1.6.
"""

from collections.abc import Mapping

ER_DIRECT_MODEL_ID = "gemini-robotics-er-2-preview"
ER_SOURCE_MODEL = "gemini-robotics-er"

# Env var honoured by the CLI probe and the live-test helpers (NOT by the production factory,
# which reads the config key so the doc19 ``WAREHOUSE__`` overlay stays the single runtime knob).
ER_MODEL_ENV = "MWR_ER_MODEL"

_CONFIG_KEY = "direct_model"


def resolve_er_direct_model(er_gateway_cfg: object | None) -> str:
    """Resolve the direct-transport model id from the ``robotics.er_gateway`` sub-tree. Pure.

    A non-Mapping sub-tree, a missing key, a non-``str`` value or an empty string all fail-safe to
    :data:`ER_DIRECT_MODEL_ID` (the same posture as ``_er_gateway_cfg`` in ``adapter_factory``:
    a malformed config never crashes construction, it degrades to the code default).
    """
    if not isinstance(er_gateway_cfg, Mapping):
        return ER_DIRECT_MODEL_ID
    value = er_gateway_cfg.get(_CONFIG_KEY)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ER_DIRECT_MODEL_ID
