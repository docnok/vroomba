"""Tiresias, the blind autopilot. Dead reckoning only, no sensors."""

from vroomba.autopilot.base import Autopilot
from vroomba.models import AutopilotMessage, SessionState, TurnResult, UserMessage
from vroomba import llm

#TODO: steering instructions aren't right, the steering command only rotates the wheels
#TODO: get speed and turning radius estimates for the car
_IDENTITY = """\
You are Tiresias, autopilot of a small RC car. You are blind and operate via dead reckoning. You
respond to user messages specifying a task.

Controls: throttle (fwd/idle/rev) × steering (left/idle/right). All on/off, no speed control.
Dead reckoning only: use elapsed time per turn to estimate distance/rotation.
Car speed: ~1-2 ft/s. Full-lock steering while moving = wide arc. Idle throttle + steering = slow rotate.\
"""

_STEP_INSTRUCTIONS = """\
Each turn you receive a history of user instructions and previous actions, plus the total elapsed
seconds since last turn. Your control holds until next turn. Turn duration varies (1-3s typical); factor this
into distance estimates.

Respond with JSON: {"control":{"throttle":"fwd","steering":"idle"},"summary":"...","msg":null,"done":false}
- summary: 1 sentence max. What you're doing and why. After receiving user directions write a short plan for future rounds.
- msg: usually null message to the user, set to respond to user messages or to give periodic status updates.
- done: true when the task is complete or when you need further instructions. Set control to idle/idle when done.
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
        turn_num = sum(1 for m in state.messages if isinstance(m, AutopilotMessage)) + 1
        history = []
        for m in state.messages:
            if isinstance(m, UserMessage):
                history.append({"role": "user", "content": m.content})
            elif isinstance(m, AutopilotMessage):
                history.append({"role": "assistant", "content": m.to_plaintext()})
        return [
            {"role": "system", "content": f"{_IDENTITY}\n\n{_STEP_INSTRUCTIONS}"},
            *history,
            {"role": "user", "content": f"Elapsed: {elapsed:.1f}s"},
        ]
