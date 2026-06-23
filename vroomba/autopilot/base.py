"""Abstract base class for autopilot implementations."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from vroomba.models import SessionState, TurnResult


TurnResultT = TypeVar("TurnResultT", bound=TurnResult, covariant=True)


class Autopilot(ABC, Generic[TurnResultT]):
    """Interface that every autopilot must implement."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    def sequential(self) -> bool:
        """If True, runner waits for action to complete before snapping next frame."""
        return False

    @abstractmethod
    def system_prompt(self) -> str:
        """Return the LLM identity/world-model system prompt."""
        ...

    @abstractmethod
    async def step(self, state: SessionState, frame_b64: str | None = None) -> TurnResultT:
        """Execute one autopilot turn: read session state, return controls."""
        ...

    @abstractmethod
    def build_step_messages(self, state: SessionState) -> list[dict]:
        """Assemble the LLM message list for a step() call."""
        ...
