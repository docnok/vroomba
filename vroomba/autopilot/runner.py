"""AutopilotRunner — orchestrates the turn-based autopilot loop."""

import asyncio
import json
import logging
import time

from vroomba.autopilot.base import Autopilot
from vroomba.camera import CameraManager
from vroomba.car import CarInterface
from vroomba.config import settings
from vroomba.models import (
    DURATION_SECONDS,
    AutopilotMessage,
    ControlCommand,
    Mode,
    SessionState,
    TurnResult,
    UserMessage,
)

log = logging.getLogger(__name__)


class AutopilotRunner:
    """Manages the session lifecycle and turn loop."""

    def __init__(self, car: CarInterface):
        self.car = car
        self.autopilot: Autopilot | None = None
        self.camera: CameraManager | None = None
        self.state = SessionState()
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

    # -- session lifecycle -----------------------------------------------------

    def set_autopilot(self, autopilot: Autopilot) -> None:
        self.autopilot = autopilot

    async def send_message(self, text: str) -> None:
        """Append a user message to the session. Starts the loop if not running."""
        if self.autopilot is None:
            return

        self.state.messages.append(UserMessage(time=time.strftime("%H:%M:%S"), content=text))
        await self._emit("message", {"role": "user", "content": text})

        # If already running, the loop will see the message on the next turn
        if self.mode == Mode.auto and self._task and not self._task.done():
            return

        # Otherwise start/restart the loop
        await self._start_loop()

    async def resume(self) -> None:
        """Resume the turn loop without adding a message."""
        if self.autopilot is None:
            return
        if self.mode == Mode.auto and self._task and not self._task.done():
            return  # already running
        await self._start_loop()

    async def pause(self) -> None:
        """Stop the turn loop and idle the car. Session state is preserved."""
        await self._stop_loop()
        self.car.idle()
        self.current_control = ControlCommand()
        self.mode = Mode.idle

        await self._emit("control", self.current_control.model_dump())
        await self._emit("status", {"mode": self.mode.value})

    async def reset(self) -> None:
        """Stop the loop and clear all session history."""
        await self.pause()
        self.state = SessionState()
        await self._emit("reset", {})

    async def manual_control(self, cmd: ControlCommand) -> None:
        """Direct control bypass — pauses auto mode."""
        if self.mode == Mode.auto:
            await self._stop_loop()
        self.mode = Mode.manual
        self.current_control = cmd
        self.car.set_control(cmd)
        await self._emit("control", cmd.model_dump())
        await self._emit("status", {"mode": self.mode.value})

    # -- internal helpers ------------------------------------------------------

    async def _start_loop(self) -> None:
        await self._stop_loop()
        self.state.active = True
        self.mode = Mode.auto
        await self._emit("status", {"mode": self.mode.value, "autopilot": self.autopilot.name})
        self._task = asyncio.create_task(self._turn_loop())

    async def _stop_loop(self) -> None:
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # -- turn loop -------------------------------------------------------------

    async def _turn_loop(self) -> None:
        """Core turn-based autopilot loop."""
        last_turn_time = time.monotonic()

        while self.state.active and self.mode == Mode.auto:
            now = time.monotonic()
            elapsed = now - last_turn_time
            last_turn_time = now
            turn_num = sum(1 for m in self.state.messages if isinstance(m, AutopilotMessage)) + 1

            log.info("Turn %d (elapsed %.1fs)", turn_num, elapsed)
            await self._emit("thinking", {})

            # Grab camera frame (if available)
            frame_b64: str | None = None
            if self.camera is not None:
                frame_b64 = self.camera.get_frame_b64()

            # Call autopilot with timeout
            try:
                result: TurnResult = await asyncio.wait_for(
                    self.autopilot.step(self.state, frame_b64=frame_b64),
                    timeout=settings.turn_timeout_seconds,
                )
            except Exception as exc:
                err_label = "LLM timeout" if isinstance(exc, asyncio.TimeoutError) else type(exc).__name__
                log.warning("Turn %d: %s", turn_num, err_label, exc_info=not isinstance(exc, asyncio.TimeoutError))
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

            # Start keepalive (re-send control at SEND_HZ, respecting duration cap)
            if self._keepalive_task and not self._keepalive_task.done():
                self._keepalive_task.cancel()
            max_seconds = DURATION_SECONDS[result.duration]
            self._keepalive_task = asyncio.create_task(
                self._keepalive(result.control, max_seconds=max_seconds)
            )

            # Store structured autopilot message in session history
            self.state.messages.append(
                AutopilotMessage(time=time.strftime("%H:%M:%S"), turn=turn_num, result=result)
            )

            # Emit turn event for the UI
            turn_data = {
                "turn_number": turn_num,
                "elapsed_seconds": round(elapsed, 2),
                "control": result.control.model_dump(),
                "duration": result.duration.value,
                "summary": result.summary,
                "scene": result.scene,
                "msg": result.msg,
                "done": result.done,
            }
            await self._emit("turn", turn_data)
            await self._emit("control", result.control.model_dump())

            # Optional LLM message to user
            if result.msg:
                await self._emit(
                    "message",
                    {"role": "assistant", "content": result.msg},
                )

            # Done = pause (car idles, loop stops, can resume/reset/message)
            if result.done:
                log.info("LLM signaled done at turn %d", turn_num)
                self.car.idle()
                self.current_control = ControlCommand()
                self.state.active = False
                self.mode = Mode.idle
                await self._emit("control", self.current_control.model_dump())
                await self._emit("status", {"mode": self.mode.value})
                break

    async def _keepalive(self, cmd: ControlCommand, max_seconds: float | None = None) -> None:
        """Re-send control at SEND_HZ. After max_seconds, idle the car."""
        interval = 1.0 / settings.turn_send_hz
        elapsed = 0.0
        try:
            while True:
                await asyncio.sleep(interval)
                elapsed += interval
                if max_seconds is not None and elapsed >= max_seconds:
                    self.car.idle()
                    self.current_control = ControlCommand()
                    await self._emit("control", self.current_control.model_dump())
                    # Keep the task alive (still re-send idle) so it can be cancelled normally
                    while True:
                        await asyncio.sleep(interval)
                        self.car.idle()
                else:
                    self.car.set_control(cmd)
        except asyncio.CancelledError:
            pass
