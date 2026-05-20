"""Abstract base class for autopilot implementations."""

from abc import ABC, abstractmethod

from vroomba.models import SessionState, TurnResult


class Autopilot(ABC):
    """Interface that every autopilot must implement."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @abstractmethod
    def system_prompt(self) -> str:
        """Return the LLM identity/world-model system prompt."""
        ...

    @abstractmethod
    async def step(self, state: SessionState, elapsed: float, frame_b64: str | None = None) -> TurnResult:
        """Execute one autopilot turn: read session state, return controls."""
        ...

    @abstractmethod
    def build_step_messages(self, state: SessionState, elapsed: float) -> list[dict]:
        """Assemble the LLM message list for a step() call."""
        ...
