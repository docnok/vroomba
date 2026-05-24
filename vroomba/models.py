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


# TODO: should the bitmasks be here or in the car.py interface?
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

    summary: str = Field(description="Brief reasoning for this turn")
    scene: str | None = Field(
        default=None,
        description="Description of what the camera sees (vision autopilots only)",
    )
    control: ControlCommand = Field(description="Control command for this turn")
    msg: str | None = Field(
        default=None,
        description="Optional message to display to the user",
    )
    done: bool = Field(default=False, description="Whether the task is complete")

class UserMessage(BaseModel):
    """Wrapper for user messages"""
    time: str = Field(description="Message timestamp")
    content: str = Field(description="Message content")

class AutopilotMessage(BaseModel):
    """Wrapper for autopilot messages"""
    time: str = Field(description="Message timestamp")
    turn: int = Field(description="Autopilot turn number in this session")
    result: TurnResult = Field(description="Autopilot decision this turn")

    def to_plaintext(self) -> str:
        """Concise plaintext rendering for the LLM's conversation history."""
        r = self.result
        parts = [f"[Turn {self.turn}] Summary: {r.summary}, Control: ({r.control.throttle.value}, {r.control.steering.value})"]
        if r.scene:
            parts.append(f"Scene: {r.scene}")
        if r.done:
            parts.append("(done)")
        return "\n".join(parts)

class SessionState(BaseModel):
    """Session state: just a message list (same format the LLM consumes)."""

    messages: list[UserMessage | AutopilotMessage] = Field(default_factory=list, description="User and autopilot messages for this session")
    active: bool = True


class Mode(str, Enum):
    idle = "idle"
    auto = "auto"
    manual = "manual"
