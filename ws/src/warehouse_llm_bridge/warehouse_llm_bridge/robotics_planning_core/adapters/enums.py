"""Observation-only transport / provider enums (bridge-local, NOT a contract).

These are Langfuse ``model_call`` observation tags — they record *which* transport and
provider produced a result for audit/comparison. They are deliberately kept out of
``warehouse_interfaces`` (the frozen contract hub) and, critically, are NEVER used as an
execution-branch key: that is the same rule that keeps ``source_model`` audit-only
(docs/mode-x-er/03-er-adapter-skeleton.md:75). A safety-gate box's transport is ``n/a``
(``docs/productization/01-commercial-box-map.md:53``) — writing ``hermes`` there would be a
category error (motion gate "v-pierced" by a transport line), so transport is expressed
as an observation tag, not a branch.

Values are docs-sourced, not invented:
- ``ProviderType``: docs/productization/02-l4-robotics-bridge-box.md:111,
  docs/productization/01-commercial-box-map.md:17-19,62
- ``Transport``: docs/productization/02-l4-robotics-bridge-box.md:112,264,
  docs/productization/01-commercial-box-map.md:16
- decision/rationale: docs/mode-x-er/06-unfrozen-contract-resolutions.md §2

Pattern mirrors the frozen ``CommandAction`` StrEnum (schemas.py:135) but the layer is
different (bridge-local, observation-only). Mode X-ER on its own does not use ``WORKER``
(that is Mode X-ER-VLA's GPU/VLA runtime, docs/productization/01:163); the value is kept
so the enum stays stable when VLA arrives.
"""

from enum import StrEnum


class ProviderType(StrEnum):
    """Audit/observation tag for the kind of model behind a call. NOT a branch key."""

    LLM = "llm"
    ER = "er"
    VLA = "vla"
    STT = "stt"


class Transport(StrEnum):
    """Audit/observation tag for how a model was reached. NOT a branch key.

    A safety-gate box has no meaningful transport (expressed as unset/None, not a value).
    """

    HERMES = "hermes"
    DIRECT = "direct"
    WORKER = "worker"
