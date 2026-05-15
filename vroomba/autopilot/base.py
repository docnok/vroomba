"""Abstract base class for autopilot implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod

from vroomba.models import DirectiveState, PlanResult, TurnResult


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
        """Return the LLM system prompt for this autopilot."""
        ...

    @abstractmethod
    async def plan(self, directive: str) -> PlanResult:
        """Given a user directive, produce ack + internal plan."""
        ...

    @abstractmethod
    async def step(self, state: DirectiveState, sensors: dict) -> TurnResult:
        """Execute one autopilot turn: read state + sensors, return controls."""
        ...

    @abstractmethod
    def build_step_messages(
        self, state: DirectiveState, sensors: dict
    ) -> list[dict]:
        """Assemble the LLM message list for a step() call."""
        ...
