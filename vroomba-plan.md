# Plan: Vroomba LLM Autopilot + Retro UI

## TL;DR
Build an LLM-powered turn-based autopilot that controls the RC car via the existing Arduino serial interface, plus a retro-terminal-styled web UI (FastAPI + WebSocket). Manual state management with Pydantic structured outputs, OpenAI-compatible LLM client (ollama locally), abstract Autopilot interface with "Homer" (blind/dead-reckoning) as first implementation.

### Architecture

```
Browser (retro terminal UI)
    ↕ WebSocket + HTTP
FastAPI Server
    ├── AutopilotRunner (turn loop)
    │       ↕ OpenAI-compatible API
    │   Ollama (local LLM)
    └── CarInterface (serial)
            ↕ USB Serial (bitmask protocol — unchanged)
        Arduino (remote_hijack.ino — unchanged)
```

The Arduino firmware and serial protocol stay exactly as-is. The new system replaces only the Python side.

---

### Steps

**Phase 1: Core Infrastructure**

1. **Package structure & deps** — Create `vroomba/` Python package. Add `fastapi`, `uvicorn[standard]`, `openai`, `pydantic-settings` to `pyproject.toml`. Keep `controller.py` as standalone manual tool.

2. **Pydantic models** (`vroomba/models.py`) — `ControlCommand` (throttle: forward/idle/reverse × steering: left/idle/right), `TurnResult` (LLM structured output: control + summary + optional user_message + yield_to_user + task_complete), `TurnRecord` (persisted turn with timestamp + elapsed), `Message` (role + content + timestamp), `DirectiveState` (directive + plan + turns + messages), `SystemStatus` (connections + mode).

3. **Car interface** (`vroomba/car.py`) — Extract serial logic from `controller.py`. Maps `ControlCommand` → existing bitmask (BIT_FWD=0x01, BIT_REV=0x02, BIT_LEFT=0x04, BIT_RIGHT=0x08). Thread-safe, handles connect/disconnect/auto-detect.

4. **LLM client** (`vroomba/llm.py`) — Async wrapper using `openai.AsyncOpenAI(base_url="http://localhost:11434/v1")`. `complete(messages, response_model) -> TurnResult` with JSON response_format. Single retry on parse failure. Health check method.

5. **Configuration** (`vroomba/config.py`) — Pydantic Settings: serial port (auto-detect), model name, LLM base URL, turn timeout (3s default), send Hz (20), server port (8420). Reads from env / `.env`.

**Phase 2: Autopilot System**

6. **Abstract base** (`vroomba/autopilot/base.py`) — ABC with: `name`, `description`, `system_prompt()`, `async plan(directive) -> str`, `async step(state, sensors) -> TurnResult`, `build_step_messages(state, sensors) -> list[dict]`. Sensors param is `dict` — empty for Homer, extensible for future autopilots.

7. **Homer autopilot** (`vroomba/autopilot/homer.py`) — Blind/dead-reckoning. System prompt explains: digital controls (on/off, no speed), no sensors, must reason about elapsed time to estimate distance/rotation, output structured JSON matching `TurnResult` schema. `build_step_messages` assembles system prompt → directive → plan → compact turn history → current turn context (timestamp, elapsed). Simple context window management: if history exceeds token budget, keep first 3 + last N turns.

8. **Autopilot runner** (`vroomba/autopilot/runner.py`) — Core orchestrator. `start_directive()` creates state, calls `plan()`, starts async turn loop. Each turn: record timestamp → `autopilot.step()` with `asyncio.wait_for(timeout)` → on success: send control to car + start 20Hz keepalive task → append TurnRecord → check yield/complete → broadcast via WebSocket. `kill()`: immediately idle car, cancel LLM call. `manual_control()`: direct control bypass. `resume()`: add message to history, restart loop.

**Phase 3: Web Server** *(parallel with Phase 4)*

9. **FastAPI app** (`vroomba/server.py`) — REST: `POST /directive`, `POST /kill`, `POST /resume`, `POST /manual`, `GET /status`, `GET /state`, `GET /autopilots`. WebSocket: `WS /ws` pushes turn/message/status/control events. Serves static files from `vroomba/static/`.

10. **Entry point** (`vroomba/__main__.py`) — `python -m vroomba` starts uvicorn, auto-opens browser.

**Phase 4: Frontend** *(parallel with Phase 3)*

11. **HTML** (`vroomba/static/index.html`) — Two-column layout (~80/20). Left: message history panel + text input bar. Right: status panel (connection indicators, mode, autopilot name) + turn history (compact cards with directional arrows + truncated summary, click to expand) + Kill Switch button.

12. **CSS** (`vroomba/static/style.css`) — Monospace font, dark background (#0a0a0a), green/amber/cyan text, CRT scanline overlay, text-shadow glow, blinking cursor, pulsing red kill switch.

13. **JavaScript** (`vroomba/static/app.js`) — Vanilla JS, no framework. WebSocket client (auto-reconnect), render messages/turns, keyboard handler (Escape = kill → manual mode, arrow keys = manual control, Enter = send text), click handlers for buttons, debug modal for full turn state.

**Phase 5: Ollama Setup**

14. **Install & pull model** — `brew install ollama`, `ollama pull gemma3:27b` (or target model). Verify structured JSON output support. Model name configurable via `VROOMBA_LLM_MODEL` env var.

---

### File Structure

**Unchanged:**
- `remote_hijack/remote_hijack.ino` — Arduino firmware, bitmask protocol (BIT_FWD=0x01, BIT_REV=0x02, BIT_LEFT=0x04, BIT_RIGHT=0x08)
- `controller.py` — Reference for `find_serial_port()`, serial keepalive pattern, bitmask encoding

**Modified:**
- `pyproject.toml` — Add: fastapi, uvicorn[standard], openai, pydantic-settings

**New (16 files):**
```
vroomba/
├── __init__.py
├── __main__.py           ← CLI entry point
├── config.py             ← Pydantic Settings
├── models.py             ← All shared Pydantic models
├── car.py                ← CarInterface (serial communication)
├── llm.py                ← Async OpenAI-compatible LLM client
├── autopilot/
│   ├── __init__.py       ← Registry of available autopilots
│   ├── base.py           ← Abstract Autopilot ABC
│   ├── homer.py          ← Homer (blind) autopilot
│   └── runner.py         ← AutopilotRunner (turn loop orchestration)
├── server.py             ← FastAPI app (REST + WebSocket + static)
└── static/
    ├── index.html        ← Single-page UI
    ├── style.css         ← Retro terminal CSS
    └── app.js            ← WebSocket client + keyboard + UI
```

---

### Verification

1. **Unit: CarInterface** — mock serial, verify `ControlCommand(forward, left)` → bitmask `0x05`, idle → `0x00`
2. **Unit: Models** — `TurnResult` JSON round-trip, invalid input rejection
3. **Unit: LLM client** — mock HTTP, verify structured output parsing + retry logic
4. **Integration: AutopilotRunner** — mock LLM + mock car, run 3-turn directive, verify state accumulation, timeout → idle, kill() → immediate idle
5. **Integration: WebSocket** — connect client, start directive, verify turn/message events pushed
6. **E2E: Full loop** — ollama + Arduino + browser → "go forward for two seconds" → car moves → stops → completion message
7. **E2E: Kill switch** — Escape during directive → car immediately stops, mode = MANUAL
8. **E2E: Manual mode** — arrow keys control car, type message to resume autopilot

---

### Decisions

- **No LangGraph/ADK**: The turn loop is ~100 lines of explicit Python. A framework adds opacity and dependency weight without meaningful simplification for what is essentially a single loop with one conditional branch (yield to user). History assembly is explicit in `build_step_messages()` — fully transparent.
- **OpenAI-compatible client**: `openai` Python package pointed at ollama's `/v1` endpoint. Swap to any backend (vLLM, litellm, cloud APIs) by changing `base_url`. No vendor lock-in.
- **FastAPI + browser**: Retro aesthetic is trivial in CSS. Future video/audio are native web APIs. Portable (phone/tablet control on same network). Minimal JS — vanilla DOM, no framework.
- **Digital controls only**: The car has on/off switches, no analog. Dead reckoning = timing. LLM receives elapsed time per turn.
- **20Hz keepalive**: Runner re-sends current control at 20Hz between LLM turns (matches existing controller.py pattern).
- **In-memory state only**: No persistence across directives. Fresh state each time.
- **3s safety timeout (configurable)**: Car auto-idles if LLM inference exceeds timeout.

---

### Further Considerations

1. **Turn duration ↔ inference time coupling**: A forward command during 2s inference = ~2ft travel, but 500ms = ~6in. Homer's prompt must explain this. Alternatively, we could add a `duration_seconds` field to `ControlCommand` — the runner applies the control for N seconds, then idles while waiting for the next LLM call. This decouples control duration from inference latency. Worth testing after the basic loop works — recommend starting coupled (simpler) and decoupling if the LLM struggles with timing.

2. **Structured output reliability**: ollama's JSON mode with gemma3 occasionally produces invalid JSON. The retry approach handles it. If too flaky, ollama supports grammar-constrained decoding via the `format` parameter with a full JSON schema — worth enabling from the start if the model supports it.

3. **Concurrent access**: Only one browser should drive at a time. Simple approach: reject `POST /directive` if one is already active. Multiple read-only viewers are fine (all get WebSocket events).
 