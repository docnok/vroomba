"""Argus — the all-seeing autopilot. Uses camera frames for navigation."""

from vroomba.autopilot.base import Autopilot
from vroomba.models import AutopilotMessage, SessionState, UserMessage, VisionTurnResult
from vroomba import llm

_IDENTITY = """\
You are Argus, autopilot of a small RC car. You have a camera mounted on the car.

Controls: throttle (fwd/idle/rev) × steering (left/idle/right). All on/off, no analog speed control.
Duration: each turn you choose how long the control holds before auto-idling:
  - "cautious" (0.25s) — inch forward, good for tight spaces or near obstacles
  - "normal" (0.5s) — balanced movement
  - "full" — hold until next turn (use in open space or for long runs)
Use what you see in each camera frame together with elapsed time to navigate.
Car speed: ~1-2 ft/s. Steering turns the front wheels; the car must be moving (fwd/rev) to actually turn.
Steering while moving = wide arc. Steering with idle throttle has no effect.\
"""

_STEP_INSTRUCTIONS = """\
Each turn you receive a timestamped history of user instructions and your previous actions (encoded as assistant messages that contain your previous scene descriptions, summary assessments, and control actions), plus a current camera frame showing what's ahead. Consider carefully the user instructions, your previous actions, and what you see to decide what to do next.

Your response on the first turn after a new user message should include a detailed plan for how to accomplish the user task over multiple turns referencing the user instructions and your previous actions. Plans must always contain concrete "done" criteria --- when will you set done=true, even if you get no further instructions from the user? In particular, if you intend to refuse the task and cannot create a plan, always set done=true and give a message explaining why you can't do the task. On subsequent turns, you should briefly assess your progress towards the user task and determine what to do next.

After determining what to do next, specify your control action for this turn. Your control holds until next turn. Turn duration varies (1-3s typical); factor this into distance estimates.

Respond with JSON, e.g.: {"summary":"...","scene":"...","control":{"throttle":"fwd","steering":"idle"},"duration":"normal","msg":null,"done":false}
- summary: An assessment of the current situation and your plans about what to do next. First message after a new user message should include a detailed plan, otherwise give a brief (1-2 sentence max) assessment of your progress and next steps.
- scene: 1-2 sentences. Describe what you see in the camera frame — obstacles, surfaces, open space, walls, objects, people. Be specific about spatial layout (left/center/right). This description is saved for future turns so you can track your environment over time.
- control: Your control action for this turn, which will hold until the next turn. Choose from throttle (fwd/idle/rev) × steering (left/idle/right). All on/off, no speed control.
- duration: How long the control holds: "cautious" (0.5s), "normal" (1.0s), or "full" (until next turn). Use cautious near obstacles or for precise positioning, normal for routine movement, full for long straight runs in open space.
- msg: Usually null message to the user, set to respond to user messages or to give periodic status updates. Always give a status update when you receive a new user message or set done=true.
- done: True when the user task is complete or when you intend to wait for further instructions. Set control to idle/idle when done.
Be conservative. Avoid obstacles. Prefer open space. If you get stuck and/or see the same frame for multiple turns, try backing up.\
"""


class ArgusAutopilot(Autopilot[VisionTurnResult]):
    @property
    def name(self) -> str:
        return "Argus"

    @property
    def description(self) -> str:
        return "Vision autopilot. Uses camera frames to see and navigate"

    def system_prompt(self) -> str:
        return _IDENTITY

    async def step(self, state: SessionState, frame_b64: str | None = None) -> VisionTurnResult:
        messages = self.build_step_messages(state, frame_b64)
        return await llm.complete(messages, result_model=VisionTurnResult)

    def build_step_messages(self, state: SessionState, frame_b64: str | None = None) -> list[dict]:
        # Convert typed session messages to chat format
        history = []
        for m in state.messages:
            if isinstance(m, UserMessage):
                history.append({"role": "user", "content": f"({m.time}) {m.content}"})
            elif isinstance(m, AutopilotMessage):
                history.append({"role": "assistant", "content": f"({m.time}) {m.to_plaintext()}"})

        # Build the current turn's user message with optional image
        if frame_b64 is not None:
            user_content = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"},
                },
            ]
        else:
            user_content = "[no camera frame available] JSON:"

        return [
            {"role": "system", "content": f"{_IDENTITY}\n\n{_STEP_INSTRUCTIONS}"},
            *history,
            {"role": "user", "content": user_content},
        ]
