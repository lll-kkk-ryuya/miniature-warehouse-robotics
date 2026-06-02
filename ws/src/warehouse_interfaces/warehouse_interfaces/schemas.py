"""Pydantic contract schemas for the LLM Bridge (frozen contract).

Source of truth for the JSON shapes exchanged over ``std_msgs/String`` topics
(doc16 §3):
- ``Situation``  — built by State Cache + LLM Bridge (doc mode-a/08a)
- ``Command``    — produced by the commander LLM (doc mode-a/08a)
- ``Proposal``   — produced by character-LLM negotiation (doc14)

``gen_id`` carries the B-3 same-generation guard (doc08 §同時発火制御 / doc15);
``CommandItem.idempotency_key`` adds the per-tool-call idempotency layer (R-35).
Models tolerate unknown extra fields (``extra="ignore"``) so LLM output / doc
evolution does not hard-fail; required fields, types and known locations are
still validated. Extending these models is a contract change (rules §4).
"""

import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from warehouse_interfaces.locations import KNOWN_LOCATIONS


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Position(_Model):
    x: float
    y: float


class Velocity(_Model):
    linear: float
    angular: float


class RobotState(_Model):
    """Per-robot state inside a ``Situation`` (LLM Bridge L1 input to the commander LLM).

    Mode A/B populate every field (the LLM uses velocity/heading for deadlock and
    predicted_position reasoning). Mode C delegates traffic to Open-RMF and sends only the
    strategic fields (position/status/current_task/battery), omitting velocity, heading,
    predicted_position_3s, obstacle_ahead and obstacle_distance to save ~200 tokens
    (doc mode-c/08c §省略). velocity/heading are Optional so one frozen model serves both
    modes; the State Cache producer (``RobotSnapshot``) keeps them required — odom supplies them.

    Mode C wire shape: build with only the strategic fields set, then serialize with
    ``model_dump(exclude_unset=True)``. ``exclude_none=True`` is insufficient because
    ``obstacle_ahead`` defaults to ``False`` (not None) and would otherwise remain in the JSON.
    """

    position: Position
    velocity: Velocity | None = None
    heading: float | None = None
    status: str
    battery: int
    predicted_position_3s: Position | None = None
    current_task: str | None = None
    obstacle_ahead: bool = False
    obstacle_distance: float | None = None


class RobotSnapshot(_Model):
    """Per-robot raw state written by State Cache (pre-computation).

    ``obstacle_distance`` is the nearest-obstacle distance [m] State Cache
    aggregates from ``/bot{n}/scan``; it shares the name of Situation's
    ``RobotState.obstacle_distance`` so the LLM Bridge needs no field rename when
    enriching (doc mode-a/08a). ``battery`` is a percentage in [0, 100].
    """

    position: Position
    velocity: Velocity
    heading: float
    status: str
    battery: int
    obstacle_distance: float | None = None

    @field_validator("battery")
    @classmethod
    def _battery_in_range(cls, value: int) -> int:
        if not 0 <= value <= 100:
            raise ValueError(f"battery {value} out of range [0, 100]")
        return value

    @field_validator("obstacle_distance")
    @classmethod
    def _obstacle_non_negative(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError(f"obstacle_distance {value} must be >= 0")
        return value


class StateSnapshot(_Model):
    """Aggregated raw state that State Cache writes to ``state.json`` (doc12 State Cache).

    The LLM Bridge reads this and builds a ``Situation`` (adding the computed
    ``predicted_position_3s`` and ``obstacle_ahead`` per doc mode-a/08a). This is
    the L2(producer) ↔ L1(consumer) contract; change via rules §4 (contract).
    """

    timestamp: str
    robots: dict[str, RobotSnapshot]


class PendingTask(_Model):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    from_: str = Field(alias="from")
    to: str


class HistoryEntry(_Model):
    turn: int
    action: str
    result: str


class Warehouse(_Model):
    layout: str


class Situation(_Model):
    timestamp: str
    turn: int
    gen_id: int
    warehouse: Warehouse
    robots: dict[str, RobotState]
    pending_tasks: list[PendingTask] = Field(default_factory=list)
    history: list[HistoryEntry] = Field(default_factory=list)


class CommandAction(StrEnum):
    NAVIGATE = "navigate"
    WAIT = "wait"
    STOP = "stop"
    YIELD = "yield"
    CHARGE = "charge"


class CommandItem(_Model):
    bot: str
    action: CommandAction
    destination: str | None = None
    via: str | None = None
    duration: float | None = None
    retreat_to: str | None = None
    # Per-tool-call idempotency key (R-35, doc08/15 §同時発火制御). Minted by the
    # Bridge (NOT echoed by the LLM); one fresh UUID per tool call. Distinct keys
    # in the same gen_id (e.g. navigate bot1 + bot2) are all accepted; replay of
    # the same key is an idempotent reject at the MCP server (check_and_add).
    # Optional for backward-compat: absent/None until the Bridge mints it.
    idempotency_key: str | None = None

    @field_validator("destination", "retreat_to")
    @classmethod
    def _known_location(cls, value: str | None) -> str | None:
        if value is not None and value not in KNOWN_LOCATIONS:
            raise ValueError(f"unknown location {value!r}")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def _valid_uuid(cls, value: str | None) -> str | None:
        if value is not None:
            uuid.UUID(value)  # raises ValueError if not a parseable UUID
        return value


class Command(_Model):
    reasoning: str
    commands: list[CommandItem] = Field(default_factory=list)
    priority_explanation: str | None = None


class AgreedAction(_Model):
    action: CommandAction
    by: str
    to: str | None = None
    duration: float | None = None


class TranscriptLine(_Model):
    speaker: str
    text: str


class Proposal(_Model):
    negotiation_id: str
    gen_id: int
    agreed_action: AgreedAction
    transcript: list[TranscriptLine] = Field(default_factory=list)
    reached_at: float
