"""Pydantic contract schemas for the LLM Bridge (frozen contract).

Source of truth for the JSON shapes exchanged over ``std_msgs/String`` topics
(doc16 §3):
- ``Situation``  — built by State Cache + LLM Bridge (doc mode-a/08a)
- ``Command``    — produced by the commander LLM (doc mode-a/08a)
- ``Proposal``   — produced by character-LLM negotiation (doc14)

``gen_id`` carries the B-3 same-generation guard (doc08 §同時発火制御 / doc15).
Models tolerate unknown extra fields (``extra="ignore"``) so LLM output / doc
evolution does not hard-fail; required fields, types and known locations are
still validated. Extending these models is a contract change (rules §4).
"""

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
    position: Position
    velocity: Velocity
    heading: float
    status: str
    battery: int
    predicted_position_3s: Position | None = None
    current_task: str | None = None
    obstacle_ahead: bool = False
    obstacle_distance: float | None = None


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

    @field_validator("destination", "retreat_to")
    @classmethod
    def _known_location(cls, value: str | None) -> str | None:
        if value is not None and value not in KNOWN_LOCATIONS:
            raise ValueError(f"unknown location {value!r}")
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
