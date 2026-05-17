"""FastAPI server — REST + WebSocket + static file serving."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from vroomba.autopilot import get_autopilot
from vroomba.autopilot.runner import AutopilotRunner
from vroomba.car import CarInterface
from vroomba.config import settings
from vroomba.models import ControlCommand, Mode

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# -- globals (initialized in lifespan) ----------------------------------------

car: CarInterface
runner: AutopilotRunner
ws_clients: set[WebSocket] = set()


async def broadcast(event_type: str, data: dict) -> None:
    """Push an event to all connected WebSocket clients."""
    msg = json.dumps({"type": event_type, "data": data})
    dead: list[WebSocket] = []
    for ws in ws_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_clients.discard(ws)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global car, runner
    car = CarInterface(port=settings.serial_port, baud=settings.serial_baud)
    runner = AutopilotRunner(car)
    runner.add_listener(broadcast)

    # Default autopilot
    pilot = get_autopilot("homer")
    runner.set_autopilot(pilot)

    # Try to connect to Arduino (non-fatal if not plugged in)
    connected = car.connect()
    if connected:
        log.info("Arduino connected")
    else:
        log.warning("Arduino not connected — will operate without hardware")

    yield

    await runner.pause()
    car.disconnect()


app = FastAPI(title="Vroomba", lifespan=lifespan)


# -- request models -----------------------------------------------------------

class MessageRequest(BaseModel):
    text: str


# -- REST endpoints -----------------------------------------------------------

@app.post("/message")
async def send_message(req: MessageRequest):
    asyncio.create_task(runner.send_message(req.text))
    return {"status": "ok"}


@app.post("/pause")
async def pause():
    await runner.pause()
    return {"status": "paused"}


@app.post("/resume")
async def resume():
    await runner.resume()
    return {"status": "resumed"}


@app.post("/reset")
async def reset():
    await runner.reset()
    return {"status": "reset"}


@app.post("/manual")
async def manual_control(cmd: ControlCommand):
    await runner.manual_control(cmd)
    return {"status": "ok"}


@app.get("/status")
async def get_status():
    from vroomba import llm as llm_mod

    return {
        "arduino_connected": car.is_connected,
        "llm_available": await llm_mod.is_available(),
        "mode": runner.mode.value,
        "autopilot_name": runner.autopilot.name if runner.autopilot else None,
        "current_control": runner.current_control.model_dump(),
    }


@app.get("/state")
async def get_state():
    return {"state": runner.state.model_dump(mode="json")}


@app.get("/autopilots")
async def list_autopilots():
    from vroomba.autopilot import AUTOPILOTS

    return [
        {"name": cls().name, "description": cls().description}
        for cls in AUTOPILOTS.values()
    ]


# -- WebSocket ----------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.add(ws)
    log.info("WebSocket client connected (%d total)", len(ws_clients))
    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "pause":
                    await runner.pause()
                elif msg.get("type") == "manual":
                    cmd = ControlCommand.model_validate(msg.get("data", {}))
                    await runner.manual_control(cmd)
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(ws)
        log.info("WebSocket client disconnected (%d remaining)", len(ws_clients))


# -- static files (serve index.html at root) ----------------------------------

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
