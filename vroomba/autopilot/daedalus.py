"""Daedalus — the prudently cautious autopilot.

Unlike Polyphemus, Daedalus always uses cautious (0.5s) control duration and
operates with a sequential observe→think→act cycle: the runner waits for the
previous action to complete before snapping a new frame, so the LLM always sees
the world as it actually is post-manoeuvre.
"""

from pydantic import BaseModel, Field

from vroomba.autopilot.base import Autopilot
from vroomba.models import (
    AutopilotMessage,
    ControlCommand,
    Duration,
    SessionState,
    UserMessage,
    VisionSessionState,
    VisionTurnResult,
)
from vroomba import llm
from vroomba.speech import language_instruction


class _DaedalusLLMResult(BaseModel):
    """What the LLM actually outputs — no duration or speed to worry about."""

    scene: str = Field(description="Description of what the camera sees")
    summary: str = Field(description="Brief reasoning for this turn")
    control: ControlCommand = Field(description="Control command for this turn")
    msg: str | None = Field(default=None, description="Optional message to the user")
    done: bool = Field(default=False, description="Whether the task is complete")


_IDENTITY = """\
You are Daedalus, autopilot of a small RC car with a forward-facing camera.
You are named for the master craftsman — methodical, precise, and prudent.

Controls: throttle (fwd/idle/rev) × steering (left/idle/right). All binary, no analog.
Each command executes for exactly 0.5 seconds, then the car stops and a fresh camera frame is taken.
Car speed: ~1-2 ft/s. Steering only works while moving (fwd or rev); idle throttle + steering does nothing.
Steering while moving produces a wide arc. Reversing + steering also arcs (useful for recovery).

KEY ADVANTAGE: Because you always see the world AFTER your previous action has finished,
your camera frame is never stale. What you see is where you are. Use this to make precise,
deliberate manoeuvres — inch forward, observe, correct, repeat.\
"""

_STEP_INSTRUCTIONS = """\
Each turn you receive your timestamped action history and a current camera frame \
(taken AFTER the previous command finished). Respond with JSON:
{"scene":"...","summary":"...","control":{"throttle":"fwd","steering":"idle"},"msg":null,"done":false}

Fields:
- scene: 1-2 sentences. What you see: obstacles, surfaces, open space, walls, objects, people. \
Note spatial layout (left/center/right, near/far). These are your memory — write them so you \
can reconstruct your surroundings from past scenes.
- summary: Your assessment. First turn after a new user message: full plan with concrete "done" \
criteria. Later turns: 1-3 sentence progress check. Always note what you've learned about the \
environment. If you can't do the task, set done=true and explain in msg.
- control: throttle × steering for this turn (executes for 0.5s then stops).
- msg: Give a status update when responding to user messages, if stuck, if you find something \
interesting, or when done=true. Otherwise null.
- done: true when task complete or awaiting instructions. Set idle/idle control.

PLANNING: Make a plan on your first turn with specific done-criteria. Each later turn, restate \
your current sub-goal and check progress. If a sub-goal isn't working after ~5 turns, try \
something else. Use your scene history to avoid revisiting the same area.

PRECISION: You always see the result of your previous action. Use this:
- After turning, confirm you're now facing the right direction before going forward.
- After advancing, check if you're closer to the target or if new obstacles appeared.
- Make small corrections rather than big sweeping moves.

STUCK DETECTION — you are stuck if:
- Your scene looks nearly identical to 2+ previous consecutive turns despite movement commands
- You keep alternating between the same two actions with no progress
- An obstacle fills most of the frame and isn't moving

STUCK RECOVERY (in order):
1. Blocked ahead → rev + steer away from the obstacle for 2-3 turns, then try a new heading
2. Blocked behind (rev not working) → fwd + hard steer for 2-3 turns
3. Stuck in a loop → idle, reassess everything, pick a completely different direction
4. Dead end → three-point turn: rev+steer one way, then fwd+steer the other
CRITICAL: Commit to recovery for multiple turns. If you can't get unstuck, set done=true and \
ask for help in msg.

GENERAL: Be conservative near obstacles. Prefer open space. Start turns early. \
If the image is blurry or dark, idle and report in msg.\
"""


class DaedalusAutopilot(Autopilot[VisionTurnResult]):
    @property
    def name(self) -> str:
        return "Daedalus"

    @property
    def description(self) -> str:
        return "Prudent, cautious, not like his son."

    @property
    def sequential(self) -> bool:
        return True

    def system_prompt(self) -> str:
        return _IDENTITY

    async def step(self, state: SessionState, frame_b64: str | None = None) -> VisionTurnResult:
        messages = self.build_step_messages(state, frame_b64)
        raw = await llm.complete(messages, result_model=_DaedalusLLMResult)
        # Wrap into VisionTurnResult with duration always cautious
        return VisionTurnResult(
            scene=raw.scene,
            summary=raw.summary,
            control=raw.control,
            duration=Duration.cautious,
            msg=raw.msg,
            done=raw.done,
        )

    def build_step_messages(self, state: SessionState, frame_b64: str | None = None) -> list[dict]:
        history = []
        frames = state.frames_b64 if isinstance(state, VisionSessionState) else []
        frame_idx = 0
        for m in state.messages:
            if isinstance(m, UserMessage):
                history.append({"role": "user", "content": f"({m.time}) {m.content}"})
            elif isinstance(m, AutopilotMessage):
                past_frame = frames[frame_idx] if frame_idx < len(frames) else None
                if past_frame is not None:
                    history.append({"role": "system", "content": [
                        {"type": "text", "text": f"Turn {m.turn}. Camera frame (after previous action completed):"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{past_frame}"}},
                    ]})
                else:
                    history.append({"role": "system", "content": f"Turn {m.turn}. [no camera frame available]"})
                history.append({"role": "assistant", "content": f"({m.time}) {m.to_plaintext()}"})
                frame_idx += 1

        turn_num = frame_idx + 1

        if frame_b64 is not None:
            user_content = [
                {"type": "text", "text": f"Turn {turn_num}. Current camera frame (after previous action completed):"},
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
