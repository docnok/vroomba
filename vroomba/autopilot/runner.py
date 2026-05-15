"""AutopilotRunner — orchestrates the turn-based autopilot loop."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from vroomba.autopilot.base import Autopilot
from vroomba.car import CarInterface
from vroomba.config import settings
from vroomba.models import (
    ControlCommand,
    DirectiveState,
    Message,
    Mode,
    TurnRecord,
    TurnResult,
)

log = logging.getLogger(__name__)


class AutopilotRunner:
    """Manages the directive lifecycle and turn loop."""

    def __init__(self, car: CarInterface):
        self.car = car
        self.autopilot: Autopilot | None = None
        self.state: DirectiveState | None = None
        self.mode: Mode = Mode.idle
        self.current_control = ControlCommand()

        self._task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._listeners: list = []  # callables: async fn(event_type, data)

    # -- event broadcasting ----------------------------------------------------

    def add_listener(self, fn) -> None:
        self._listeners.append(fn)

    async def _emit(self, event_type: str, data: dict) -> None:
        for fn in self._listeners:
            try:
                await fn(event_type, data)
            except Exception:
                log.exception("Listener error")

    # -- directive lifecycle ---------------------------------------------------

    async def start_directive(self, directive: str, autopilot: Autopilot) -> None:
        """Begin a new directive session."""
        # Cancel any existing run
        await self.kill()

        self.autopilot = autopilot
        self.state = DirectiveState(directive=directive)
        self.mode = Mode.auto

        await self._emit("status", {"mode": self.mode.value, "autopilot": autopilot.name})
        await self._emit("directive_start", {})
        await self._emit("message", {"role": "user", "content": directive})

        # Planning phase
        log.info("Planning directive: %s", directive)
        await self._emit("thinking", {})
        try:
            plan_result = await autopilot.plan(directive)
        except Exception:
            log.exception("Planning failed")
            from vroomba.models import PlanResult
            plan_result = PlanResult(ack="Understood. Let me try.", plan="(planning failed — proceeding turn by turn)")

        self.state.plan = plan_result.plan
        self.state.messages.append(
            Message(role="assistant", content=plan_result.ack)
        )
        await self._emit("message", {"role": "assistant", "content": plan_result.ack})
        await self._emit("directive_info", {
            "directive": directive,
            "plan": plan_result.plan,
        })

        # Start turn loop
        self._task = asyncio.create_task(self._turn_loop())

    async def kill(self) -> None:
        """Emergency stop: idle the car, cancel the turn loop."""
        # Cancel tasks
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Idle the car
        self.car.idle()
        self.current_control = ControlCommand()
        self.mode = Mode.idle

        await self._emit("control", self.current_control.model_dump())
        await self._emit("status", {"mode": self.mode.value})

    async def manual_control(self, cmd: ControlCommand) -> None:
        """Direct control bypass (manual mode)."""
        self.mode = Mode.manual
        self.current_control = cmd
        self.car.set_control(cmd)
        await self._emit("control", cmd.model_dump())

    async def resume(self, message: str | None = None) -> None:
        """Resume autopilot after yield/kill, optionally with a user message."""
        if self.state is None or self.autopilot is None:
            return

        if message:
            self.state.messages.append(
                Message(role="user", content=message)
            )
            await self._emit("message", {"role": "user", "content": message})

        self.state.active = True
        self.mode = Mode.auto
        await self._emit("status", {"mode": self.mode.value})
        self._task = asyncio.create_task(self._turn_loop())

    # -- turn loop -------------------------------------------------------------

    async def _turn_loop(self) -> None:
        """Core turn-based autopilot loop."""
        last_turn_time = time.monotonic()

        while self.state and self.state.active and self.mode == Mode.auto:
            now = time.monotonic()
            elapsed = now - last_turn_time
            last_turn_time = now
            turn_num = len(self.state.turns) + 1

            log.info("Turn %d (elapsed %.1fs)", turn_num, elapsed)
            await self._emit("thinking", {})

            # Call autopilot with timeout
            try:
                result: TurnResult = await asyncio.wait_for(
                    self.autopilot.step(self.state, {}),
                    timeout=settings.turn_timeout_seconds,
                )
            except asyncio.TimeoutError:
                log.warning("Turn %d: LLM timeout (%.1fs)", turn_num, settings.turn_timeout_seconds)
                self.car.idle()
                self.current_control = ControlCommand()
                await self._emit("control", self.current_control.model_dump())
                await self._emit(
                    "message",
                    {"role": "system", "content": f"⚠ Turn {turn_num}: LLM timeout — idled, retrying"},
                )
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Turn %d: autopilot error", turn_num)
                self.car.idle()
                self.current_control = ControlCommand()
                await self._emit("control", self.current_control.model_dump())
                await self._emit(
                    "message",
                    {"role": "system", "content": f"⚠ Turn {turn_num}: {type(exc).__name__} — idled, retrying"},
                )
                continue

            # Apply control
            self.current_control = result.control
            self.car.set_control(result.control)

            # Start keepalive (re-send control at SEND_HZ)
            if self._keepalive_task and not self._keepalive_task.done():
                self._keepalive_task.cancel()
            self._keepalive_task = asyncio.create_task(
                self._keepalive(result.control)
            )

            # Record turn
            record = TurnRecord(
                turn_number=turn_num,
                timestamp=datetime.now(),
                elapsed_seconds=round(elapsed, 2),
                control=result.control,
                summary=result.summary,
            )
            self.state.turns.append(record)

            turn_data = record.model_dump(mode="json")
            turn_data["result"] = result.model_dump(mode="json")
            await self._emit("turn", turn_data)
            await self._emit("control", result.control.model_dump())

            # Optional user message from LLM
            if result.msg:
                self.state.messages.append(
                    Message(role="assistant", content=result.msg)
                )
                await self._emit(
                    "message",
                    {"role": "assistant", "content": result.msg},
                )

            # Check yield / complete
            if result.done:
                log.info("Directive complete at turn %d", turn_num)
                self.car.idle()
                self.current_control = ControlCommand()
                self.state.active = False
                self.mode = Mode.idle
                completion_msg = result.msg or "Directive complete."
                if not result.msg:
                    self.state.messages.append(
                        Message(role="assistant", content=completion_msg)
                    )
                    await self._emit(
                        "message", {"role": "assistant", "content": completion_msg}
                    )
                await self._emit("control", self.current_control.model_dump())
                await self._emit("status", {"mode": self.mode.value})
                break

            if result.yield_to_user:
                log.info("Yielding to user at turn %d", turn_num)
                self.car.idle()
                self.current_control = ControlCommand()
                self.state.active = False
                self.mode = Mode.idle
                await self._emit("control", self.current_control.model_dump())
                await self._emit("status", {"mode": self.mode.value})
                break

    async def _keepalive(self, cmd: ControlCommand) -> None:
        """Re-send control command at SEND_HZ to keep Arduino connection alive."""
        interval = 1.0 / settings.turn_send_hz
        try:
            while True:
                await asyncio.sleep(interval)
                self.car.set_control(cmd)
        except asyncio.CancelledError:
            pass
