"""Shared Pydantic models for the vroomba autopilot system."""

from datetime import datetime
from enum import Enum

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
    scene: str | None = Field(
        default=None,
        description="Description of what the camera sees (vision autopilots only)",
    )
    msg: str | None = Field(
        default=None,
        description="Optional message to display to the user",
    )
    done: bool = False


class SessionState(BaseModel):
    """Session state: just a message list (same format the LLM consumes)."""

    messages: list[dict] = Field(default_factory=list)
    active: bool = True


class Mode(str, Enum):
    idle = "idle"
    auto = "auto"
    manual = "manual"
