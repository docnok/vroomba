"""Tiresias — the blind autopilot. Dead reckoning only, no sensors."""

from vroomba.autopilot.base import Autopilot
from vroomba.models import SessionState, TurnResult
from vroomba import llm

_IDENTITY = """\
You are Tiresias, autopilot of a small RC car. You are blind; no sensors. You respond to a user message giving you instructions.

Controls: throttle (fwd/idle/rev) × steering (left/idle/right). All on/off, no speed control.
Dead reckoning only: use elapsed time per turn to estimate distance/rotation.
Car speed: ~1-2 ft/s. Full-lock steering while moving = wide arc. Idle throttle + steering = slow rotate.\
"""

_STEP_INSTRUCTIONS = """\
Each turn you receive elapsed seconds since last turn. Your control holds until next turn.
Turn duration varies (1-3s typical); factor this into distance estimates.

Respond with JSON: {"control":{"throttle":"fwd","steering":"idle"},"summary":"...","msg":null,"done":false}
- summary: 1 sentence max. What you're doing and why. Respond to a user message with a plan for future rounds.
- msg: optional message to the user, set to respond to user or to give a status update.
- done: true when the task is complete or when you're stuck about what to do next. Set control to idle/idle when done.
Be conservative. Undershoot rather than overshoot.\
"""


class TiresiasAutopilot(Autopilot):
    @property
    def name(self) -> str:
        return "Tiresias"

    @property
    def description(self) -> str:
        return "Blind autopilot. Dead reckoning only, no sensors"

    def system_prompt(self) -> str:
        return _IDENTITY

    async def step(self, state: SessionState, elapsed: float, frame_b64: str | None = None) -> TurnResult:
        messages = self.build_step_messages(state, elapsed)
        return await llm.complete(messages)

    def build_step_messages(self, state: SessionState, elapsed: float) -> list[dict]:
        turn_num = sum(1 for m in state.messages if m["role"] == "assistant") + 1
        return [
            {"role": "system", "content": f"{_IDENTITY}\n\n{_STEP_INSTRUCTIONS}"},
            *state.messages,
            {"role": "user", "content": f"T{turn_num}. +{elapsed:.1f}s. JSON:"},
        ]
