"""Polyphemus — the one-eyed, simple-minded autopilot. Uses camera frames for navigation."""

from vroomba.autopilot.base import Autopilot
from vroomba.models import (
    AutopilotMessage,
    SessionState,
    UserMessage,
    VisionSessionState,
    VisionTurnResult,
)
from vroomba import llm
from vroomba.speech import language_instruction

_IDENTITY = """\
You are Polyphemus, autopilot of a small RC car with a forward-facing camera.

Controls: throttle (fwd/idle/rev) × steering (left/idle/right). All binary, no analog.
Duration per turn: "cautious" (0.5s), "normal" (1.0s), or "full" (hold until next turn).
Car speed: ~1-2 ft/s. Steering only works while moving (fwd or rev); idle throttle + steering does nothing.
Steering while moving produces a wide arc. Reversing + steering also arcs (useful for recovery).\
"""

_STEP_INSTRUCTIONS = """\
Each turn you get your timestamped action history and a current camera frame. Respond with JSON:
{"summary":"...","scene":"...","control":{"throttle":"fwd","steering":"idle"},"duration":"normal","msg":null,"done":false}

Fields:
- scene: 1-2 sentences. What you see: obstacles, surfaces, open space, walls, objects, people. Note spatial layout (left/center/right, near/far). These are your memory — write them so you can reconstruct your surroundings from past scenes. NOTE: due to inference latency, the camera frame you see is taken at the start of the previous control command, not at the time you respond. Use your scene history and the most recent throttle/steering commands to estimate the current scene, but be aware of this timing issue when interpreting scenes.
- summary: Your assessment. First turn after new user message: full plan with concrete "done" criteria. Later turns: 1-3 sentence progress check referencing your plan. Always note what you've learned about the environment (dead ends, landmarks, open paths). If you can't do the task, set done=true and explain in msg.
- control: throttle × steering for this turn.
- duration: cautious near obstacles or when unsure, normal for routine, full for open straights.
- msg: Always give a status update when responding to user messages, if you get stuck, if you find something interesting, or when done=true. Otherwise usually null.
- done: true when task complete or awaiting instructions. Set idle/idle control.

PLANNING: Make a plan on your first turn with specific done-criteria; when will you set done=True?. Each later turn, restate your current sub-goal and check progress. If a sub-goal isn't working after ~5 turns, abandon it and try something else. Use your scene history to track what's around you and avoid revisiting the same area.

STUCK DETECTION — you are stuck if:
- Your scene looks nearly identical to 2+ previous consecutive turns despite movement commands
- You keep alternating between the same two actions with no progress
- An obstacle fills most of the frame and isn't moving
Watch for these patterns actively by comparing your current scene to your previous scenes. If you become stuck note it in your summary and initiate recovery.

STUCK RECOVERY (in order):
1. Blocked ahead → rev + steer away from the obstacle for 2-3 turns, then try a new heading
2. Blocked behind (rev not working) → fwd + hard steer for 2-3 turns
3. Stuck in a loop → idle, reassess everything, pick a completely different direction
4. Dead end → three-point turn: rev+steer one way, then fwd+steer the other
CRITICAL: Commit to recovery for multiple turns. Doing one turn of reverse then immediately going forward again causes oscillation. If you can't get unstuck after trying all recovery strategies, set done=true and ask for help in msg.

GENERAL: Be conservative near obstacles. Prefer open space. Start turns early (not last-moment). Use cautious when unsure or near objects. If the image is blurry or dark, slow down.\
"""


class PolyphemusAutopilot(Autopilot[VisionTurnResult]):
    @property
    def name(self) -> str:
        return "Polyphemus"

    @property
    def description(self) -> str:
        return "One eye. Kinda stupid."

    def system_prompt(self) -> str:
        return _IDENTITY

    async def step(self, state: SessionState, frame_b64: str | None = None) -> VisionTurnResult:
        messages = self.build_step_messages(state, frame_b64)
        return await llm.complete(messages, result_model=VisionTurnResult)

    def build_step_messages(self, state: SessionState, frame_b64: str | None = None) -> list[dict]:
        # Convert typed session messages to chat format, including past frames
        history = []
        frames = state.frames_b64 if isinstance(state, VisionSessionState) else []
        frame_idx = 0
        for m in state.messages:
            if isinstance(m, UserMessage):
                history.append({"role": "user", "content": f"({m.time}) {m.content}"})
            elif isinstance(m, AutopilotMessage):
                # Include the stored frame from this past turn
                past_frame = frames[frame_idx] if frame_idx < len(frames) else None
                if past_frame is not None:
                    history.append({"role": "system", "content": [
                        {"type": "text", "text": f"Turn {m.turn}. Camera frame:"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{past_frame}"}},
                    ]})
                else:
                    history.append({"role": "system", "content": f"Turn {m.turn}. [no camera frame available]"})
                history.append({"role": "assistant", "content": f"({m.time}) {m.to_plaintext()}"})
                frame_idx += 1

        turn_num = frame_idx + 1

        # Build the current turn's user message with optional image
        if frame_b64 is not None:
            user_content = [
                {"type": "text", "text": f"Turn {turn_num}. Current camera frame:"},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"},
                },
            ]
        else:
            user_content = f"Turn {turn_num}. [no camera frame available] JSON:"

        return [
            {"role": "system", "content": f"{_IDENTITY}\n\n{_STEP_INSTRUCTIONS}{language_instruction()}"},
            *history,
            {"role": "system", "content": user_content},
        ]
