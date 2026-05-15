"""Shared Pydantic models for the vroomba autopilot system."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Throttle(str, Enum):
    fwd = "fwd"
    idle = "idle"
    rev = "rev"


class Steering(str, Enum):
    left = "left"
    idle = "idle"
    right = "right"


class ControlCommand(BaseModel):
    """Car control: throttle × steering. Both default to idle."""

    throttle: Throttle = Throttle.idle
    steering: Steering = Steering.idle

    def to_bitmask(self) -> int:
        """Convert to the Arduino bitmask protocol (matches remote_hijack.ino)."""
        mask = 0
        if self.throttle == Throttle.fwd:
            mask |= 0x01  # BIT_FWD
        elif self.throttle == Throttle.rev:
            mask |= 0x02  # BIT_REV
        if self.steering == Steering.left:
            mask |= 0x04  # BIT_LEFT
        elif self.steering == Steering.right:
            mask |= 0x08  # BIT_RIGHT
        return mask

    @property
    def arrow(self) -> str:
        """Compact directional arrow for display."""
        _MAP = {
            ("fwd", "left"): "↖",
            ("fwd", "idle"): "↑",
            ("fwd", "right"): "↗",
            ("idle", "left"): "←",
            ("idle", "idle"): "·",
            ("idle", "right"): "→",
            ("rev", "left"): "↙",
            ("rev", "idle"): "↓",
            ("rev", "right"): "↘",
        }
        return _MAP[(self.throttle.value, self.steering.value)]


class TurnResult(BaseModel):
    """Structured output returned by the LLM each turn."""

    control: ControlCommand
    summary: str = Field(description="Brief reasoning for this turn")
    msg: str | None = Field(
        default=None,
        description="Message to display to the user (usually null; always set if yield_to_user or done is true)",
    )
    yield_to_user: bool = False
    done: bool = False


class PlanResult(BaseModel):
    """Structured output from the planning phase."""

    ack: str = Field(description="Short spoken acknowledgment for the user (1 sentence)")
    plan: str = Field(description="Internal plan for executing the directive")


class TurnRecord(BaseModel):
    """Persisted record of one autopilot turn."""

    turn_number: int
    timestamp: datetime
    elapsed_seconds: float = Field(description="Seconds since previous turn")
    control: ControlCommand
    summary: str


class Message(BaseModel):
    """A message in the conversation history."""

    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)


class DirectiveState(BaseModel):
    """Full state for one directive session."""

    directive: str
    plan: str = ""
    messages: list[Message] = Field(default_factory=list)
    turns: list[TurnRecord] = Field(default_factory=list)
    active: bool = True


class Mode(str, Enum):
    idle = "idle"
    auto = "auto"
    manual = "manual"


class SystemStatus(BaseModel):
    """Current system status for the UI."""

    arduino_connected: bool = False
    llm_available: bool = False
    mode: Mode = Mode.idle
    autopilot_name: str | None = None
    current_control: ControlCommand = Field(default_factory=ControlCommand)
