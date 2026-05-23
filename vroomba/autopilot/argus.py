"""Argus — the all-seeing autopilot. Uses camera frames for navigation."""

from vroomba.autopilot.base import Autopilot
from vroomba.models import AutopilotMessage, SessionState, TurnResult, UserMessage
from vroomba import llm

_IDENTITY = """\
You are Argus, autopilot of a small RC car. You have a camera mounted on the car.

Controls: throttle (fwd/idle/rev) × steering (left/idle/right). All on/off, no speed control.
Use what you see in each camera frame together with elapsed time to navigate.
Car speed: ~1-2 ft/s. Full-lock steering while moving = wide arc. Idle throttle + steering = slow rotate.\
"""

_STEP_INSTRUCTIONS = """\
Each turn you receive: elapsed seconds since last turn, and a camera frame showing what's ahead.

Respond with JSON:
{"control":{"throttle":"fwd","steering":"idle"},"summary":"...","scene":"...","msg":null,"done":false}
- scene: 1-2 sentences. Describe what you see in the camera frame — obstacles, surfaces, open space, walls, objects, people. Be specific about spatial layout (left/center/right). This description is saved for future turns so you can track your environment over time.
- summary: 1 sentence max. What you're doing and why, referencing what you see. Respond to a user message with a plan for future rounds.
- msg: optional message to the user, set to respond to user or to give an occasional status update.
- done: true when the task is complete or when you need further instructions. Set control to idle/idle when done.
Be conservative. Avoid obstacles. Prefer open space.\
"""


class ArgusAutopilot(Autopilot):
    @property
    def name(self) -> str:
        return "Argus"

    @property
    def description(self) -> str:
        return "Vision autopilot. Uses camera frames to see and navigate"

    def system_prompt(self) -> str:
        return _IDENTITY

    async def step(self, state: SessionState, elapsed: float, frame_b64: str | None = None) -> TurnResult:
        messages = self.build_step_messages(state, elapsed, frame_b64)
        return await llm.complete(messages)

    def build_step_messages(self, state: SessionState, elapsed: float, frame_b64: str | None = None) -> list[dict]:
        turn_num = sum(1 for m in state.messages if isinstance(m, AutopilotMessage)) + 1

        # Convert typed session messages to chat format
        history = []
        for m in state.messages:
            if isinstance(m, UserMessage):
                history.append({"role": "user", "content": m.content})
            elif isinstance(m, AutopilotMessage):
                history.append({"role": "assistant", "content": m.to_plaintext()})

        # Build the current turn's user message with optional image
        turn_text = f"T{turn_num}. +{elapsed:.1f}s. JSON:"

        if frame_b64 is not None:
            user_content = [
                {"type": "text", "text": turn_text},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"},
                },
            ]
        else:
            user_content = f"[no camera frame available] {turn_text}"

        return [
            {"role": "system", "content": f"{_IDENTITY}\n\n{_STEP_INSTRUCTIONS}"},
            *history,
            {"role": "user", "content": user_content},
        ]
