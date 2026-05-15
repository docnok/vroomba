"""Homer — the blind autopilot. Dead reckoning only, no sensors."""

from __future__ import annotations

from vroomba.autopilot.base import Autopilot
from vroomba.models import DirectiveState, PlanResult, TurnResult
from vroomba import llm

_SYSTEM_PROMPT = """\
You are Homer, autopilot of a small RC car. You are blind; no sensors.

Controls: throttle (fwd/idle/rev) × steering (left/idle/right). All on/off, no speed control.
Dead reckoning only: use elapsed time per turn to estimate distance/rotation.
Car speed: ~1-2 ft/s. Full-lock steering while moving = wide arc. Idle throttle + steering = slow rotate.

Each turn you receive elapsed seconds since last turn. Your control holds until next turn.
Turn duration varies (1-3s typical); factor this into distance estimates.

Respond with JSON: {"control":{"throttle":"fwd","steering":"idle"},"summary":"...","msg":null,"yield_to_user":false,"done":false}
- summary: 1 sentence max. What you're doing and why.
- msg: only set if you must tell the user something. Usually null, always set if yield_to_user or done is true.
- yield_to_user: true to pause for user input. Rare, use in cases of high uncertainty.
- done: true when directive is complete. Set control to idle/idle when done.
Be conservative. Undershoot rather than overshoot.\
"""


class HomerAutopilot(Autopilot):
    @property
    def name(self) -> str:
        return "Homer"

    @property
    def description(self) -> str:
        return "Blind autopilot. Dead reckoning only, no sensors"

    def system_prompt(self) -> str:
        return _SYSTEM_PROMPT

    async def plan(self, directive: str) -> PlanResult:
        messages = [
            {"role": "system", "content": self.system_prompt()},
            {
                "role": "user",
                "content": (
                    f"Directive: {directive}\n\n"
                    'Respond with JSON: {"ack":"...","plan":"..."}\n'
                    "ack: Short friendly acknowledgment (1 sentence, like a robot assistant would say aloud).\n"
                    "plan: Your internal execution plan (2-3 sentences)."
                ),
            },
        ]
        return await llm.complete_plan(messages)

    async def step(self, state: DirectiveState, sensors: dict) -> TurnResult:
        messages = self.build_step_messages(state, sensors)
        return await llm.complete(messages)

    def build_step_messages(
        self, state: DirectiveState, sensors: dict
    ) -> list[dict]:
        msgs: list[dict] = [{"role": "system", "content": self.system_prompt()}]

        # User directive
        msgs.append({"role": "user", "content": f"Directive: {state.directive}"})

        # Plan (internal)
        if state.plan:
            msgs.append({"role": "assistant", "content": f"Plan: {state.plan}"})

        # Inject any user messages that arrived mid-session
        for m in state.messages:
            if m.role == "user":
                msgs.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                msgs.append({"role": "assistant", "content": m.content})

        # Turn history — compact
        turns = state.turns
        if len(turns) > 25:
            kept = turns[:3] + turns[-20:]
            omitted = len(turns) - 23
            history_lines = [f"[{omitted} turns omitted]"]
        else:
            kept = turns
            history_lines = []

        for t in kept:
            history_lines.append(
                f"T{t.turn_number} +{t.elapsed_seconds:.1f}s "
                f"{t.control.arrow} {t.control.throttle.value}/{t.control.steering.value} "
                f"— {t.summary}"
            )

        if history_lines:
            msgs.append({
                "role": "assistant",
                "content": "\n".join(history_lines),
            })

        # Current turn prompt
        turn_num = len(turns) + 1
        if turns:
            elapsed_info = "Elapsed: measuring"
        else:
            elapsed_info = "First turn."

        msgs.append({
            "role": "user",
            "content": f"T{turn_num}. {elapsed_info} JSON:",
        })

        return msgs
