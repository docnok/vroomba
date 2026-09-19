"""FastAPI server — REST + WebSocket + static file serving."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from vroomba.autopilot import get_autopilot
from vroomba.autopilot.runner import AutopilotRunner
from vroomba.camera import CameraManager
from vroomba.car import CarInterface
from vroomba.config import settings
from vroomba.models import ControlCommand, Mode

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# -- globals (initialized in lifespan) ----------------------------------------

car: CarInterface
runner: AutopilotRunner
camera: CameraManager
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
    global car, runner, camera
    car = CarInterface(port=settings.serial_port, baud=settings.serial_baud)
    runner = AutopilotRunner(car)
    runner.add_listener(broadcast)

    # Camera
    camera = CameraManager()
    if settings.camera_enabled:
        if camera.start():
            runner.camera = camera
        else:
            log.warning("Camera not available — running without video")

    # Default autopilot
    pilot = get_autopilot("polyphemus")
    runner.set_autopilot(pilot)

    # Try to connect to Arduino (non-fatal if not plugged in)
    connected = car.connect()
    if connected:
        log.info("Arduino connected")
    else:
        log.warning("Arduino not connected — will operate without hardware")

    yield

    await runner.pause()
    camera.stop()
    car.disconnect()


app = FastAPI(title="Vroomba", lifespan=lifespan)


# -- request models -----------------------------------------------------------

class MessageRequest(BaseModel):
    text: str


class LanguageRequest(BaseModel):
    language: str


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
        "camera_available": camera.is_running,
        "camera_source_id": camera.source_id,
        "mode": runner.mode.value,
        "autopilot_name": runner.autopilot.name if runner.autopilot else None,
        "output_language": settings.output_language,
        "stt_language": settings.stt_language,
        "current_control": runner.current_control.model_dump(),
    }


@app.get("/state")
async def get_state():
    return {"state": runner.state.model_dump(mode="json")}


# -- Speech (STT / TTS) -------------------------------------------------------

@app.post("/stt")
async def speech_to_text(request: Request):
    from vroomba import speech

    body = await request.body()
    if not body:
        return Response(status_code=400, content="No audio data")
    loop = asyncio.get_running_loop()
    text = await loop.run_in_executor(None, speech.transcribe, body)
    return {"text": text}


@app.get("/tts")
async def text_to_speech(text: str):
    from vroomba import speech

    if not text:
        return Response(status_code=400, content="No text provided")
    loop = asyncio.get_running_loop()
    wav_bytes = await loop.run_in_executor(None, speech.synthesize, text)
    return Response(content=wav_bytes, media_type="audio/wav")


class AutopilotRequest(BaseModel):
    name: str


@app.post("/autopilot")
async def set_autopilot(req: AutopilotRequest):
    pilot = get_autopilot(req.name)
    await runner.pause()
    runner.set_autopilot(pilot)
    return {"status": "ok", "autopilot": pilot.name}


@app.get("/autopilots")
async def list_autopilots():
    from vroomba.autopilot import AUTOPILOTS

    return [
        {"name": cls().name, "description": cls().description}
        for cls in AUTOPILOTS.values()
    ]


@app.get("/languages")
async def list_languages():
    from vroomba.speech import LANGUAGE_NAMES, VOICE_MAP

    return {
        "current": settings.output_language,
        "languages": [
            {"code": code, "name": LANGUAGE_NAMES.get(code, code), "voice": voice}
            for code, voice in VOICE_MAP.items()
        ],
    }


@app.post("/language")
async def set_language(req: LanguageRequest):
    from fastapi import HTTPException
    from vroomba.speech import LANGUAGE_NAMES, VOICE_MAP

    language = req.language.lower()
    if language not in VOICE_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported language: {req.language!r}. Available: {list(VOICE_MAP)}",
        )

    # Keep speech input and output aligned when changed from the UI. The
    # environment settings remain independently configurable for advanced use.
    settings.output_language = language
    settings.stt_language = language
    await broadcast(
        "status",
        {
            "mode": runner.mode.value,
            "output_language": language,
            "stt_language": language,
        },
    )
    return {
        "status": "ok",
        "language": language,
        "name": LANGUAGE_NAMES.get(language, language),
    }


# -- WebSocket ----------------------------------------------------------------

@app.get("/camera/snapshot")
async def camera_snapshot():
    frame = camera.get_frame()
    if frame is None:
        return Response(status_code=503, content="Camera not available")
    return Response(content=frame, media_type="image/jpeg")


@app.get("/camera/stream")
async def camera_stream():
    if not camera.is_running:
        return Response(status_code=503, content="Camera not running")

    async def generate():
        idle_ticks = 0
        max_idle = int(settings.camera_fps * 5)  # give up after ~5 s with no frames
        while True:
            frame = camera.get_frame()
            if frame is not None:
                idle_ticks = 0
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                )
            else:
                idle_ticks += 1
                if idle_ticks >= max_idle:
                    return
            await asyncio.sleep(1.0 / settings.camera_fps)

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/camera/status")
async def camera_status():
    return {
        "available": camera.is_running,
        "resolution": list(camera.resolution) if camera.resolution else None,
    }


@app.get("/camera/devices")
async def camera_devices():
    loop = asyncio.get_running_loop()
    devices = await loop.run_in_executor(None, CameraManager.list_sources)
    return devices


class CameraSelectRequest(BaseModel):
    source_id: str


@app.post("/camera/select")
async def camera_select(req: CameraSelectRequest):
    from fastapi import HTTPException

    try:
        source = CameraManager.source_from_id(req.source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    loop = asyncio.get_running_loop()
    success = await loop.run_in_executor(None, camera.switch, source)
    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to open camera source {req.source_id}")
    runner.camera = camera
    return {"status": "ok", "source_id": req.source_id}


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
