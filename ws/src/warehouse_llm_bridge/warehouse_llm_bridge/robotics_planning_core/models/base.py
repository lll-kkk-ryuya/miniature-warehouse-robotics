"""Shared pydantic base for Mode X-ER bridge-local models.

Mirrors the frozen-contract convention (``warehouse_interfaces.schemas._Model``,
schemas.py:24-25): tolerate unknown extra fields (``extra="ignore"``) so a model
output / doc evolution does not hard-fail, while required fields and types are
still validated (docs/mode-x-er/06-unfrozen-contract-resolutions.md §1:52).

These models are intentionally NOT in ``warehouse_interfaces``: they are the L4/L3
internal representation of Mode X-ER and stay bridge-local until XER1-XER2 stabilize
their shape (docs/mode-x-er/03-er-adapter-skeleton.md:5;
docs/mode-x-er/02-l3-planning-core.md:278 "avoid freezing RoboticsPlan from the start";
docs/mode-x-er/06-unfrozen-contract-resolutions.md §1).
"""

from pydantic import BaseModel, ConfigDict


class _BridgeModel(BaseModel):
    model_config = ConfigDict(extra="ignore")
