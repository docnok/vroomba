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
Each turn you receive a timestamped history of user instructions and your previous actions (encoded as assistant messages that contain your previous summary assessments and control actions). Consider carefully the user instructions and your previous actions to decide what to do next.

Your response on the first turn after a new user message should include a detailed plan for how to accomplish the user task over multiple turns referencing the user instructions and your previous actions. Plans must always contain concrete "done" critieria --- when will you set done=true, even if you get no further instructions from the user? In particular, if you intend to refuse the task and cannot create a plan, always set done=true and give a message explaining why you can't do the task. On subsequent turns, you should briefly assess your progress towards the user task and determine what to do next.

After determining what to do next, specify your control action for this turn. Your control holds until next turn. Turn duration varies (1-3s typical); factor this into distance estimates.

Respond with JSON, e.g.: {"summary":"...","control":{"throttle":"fwd","steering":"idle"},"msg":null,"done":false}
- summary: An assessment of the current situation and your plans about what to do next. First message after a new user message should include a detailed plan, otherwise give a brief (1-2 sentence max) assessment of your progress and next steps.
- control: Your control action for this turn, which will hold until the next turn. Choose from throttle (fwd/idle/rev) × steering (left/idle/right). All on/off, no speed control.
- msg: Usually null message to the user, set to respond to user messages or to give periodic status updates. Always give a status update when you receive a new user message or set done=true.
- done: True when the user task is complete or when you intend to wait for further instructions. Set control to idle/idle when done.
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

    async def step(self, state: SessionState, frame_b64: str | None = None) -> TurnResult:
        messages = self.build_step_messages(state)
        return await llm.complete(messages)

    def build_step_messages(self, state: SessionState) -> list[dict]:
        history = []
        for m in state.messages:
            if isinstance(m, UserMessage):
                history.append({"role": "user", "content": f"({m.time}) {m.content}"})
            elif isinstance(m, AutopilotMessage):
                history.append({"role": "assistant", "content": f"({m.time}) {m.to_plaintext()}"})
        return [
            {"role": "system", "content": f"{_IDENTITY}\n\n{_STEP_INSTRUCTIONS}"},
            *history,
        ]
