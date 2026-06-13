/* ================================================================
   VROOMBA — frontend logic (vanilla JS)
   ================================================================ */

(() => {
  "use strict";

  // ---- DOM refs ----
  const messageList    = document.getElementById("message-list");
  const inputForm      = document.getElementById("input-form");
  const inputBox       = document.getElementById("input-box");
  const modeBadge      = document.getElementById("mode-badge");
  const pauseBtn       = document.getElementById("pause-btn");
  const resumeBtn      = document.getElementById("resume-btn");
  const resetBtn       = document.getElementById("reset-btn");
  const turnList       = document.getElementById("turn-list");
  const debugModal     = document.getElementById("debug-modal");
  const debugBody      = document.getElementById("debug-body");
  const debugClose     = document.getElementById("debug-close");
  const micBtn         = document.getElementById("mic-btn");
  const ttsBtn         = document.getElementById("tts-btn");

  // Autopilot panel refs
  const personaToggle  = document.getElementById("persona-toggle");
  const personaArrow   = document.getElementById("persona-arrow");
  const personaValue   = document.getElementById("persona-value");
  const personaList    = document.getElementById("persona-list");
  const cameraToggle   = document.getElementById("camera-toggle");
  const cameraArrow    = document.getElementById("camera-arrow");
  const cameraValue    = document.getElementById("camera-value");
  const cameraList     = document.getElementById("camera-list");

  // Footer status bar refs
  const ftDotArduino   = document.getElementById("ft-dot-arduino");
  const ftDotLlm       = document.getElementById("ft-dot-llm");
  const ftDotCamera    = document.getElementById("ft-dot-camera");
  const ftMode         = document.getElementById("ft-mode");
  const ftCtrl         = document.getElementById("ft-ctrl");
  const headerSpinner  = document.getElementById("header-spinner");
  const cameraFeed     = document.getElementById("camera-feed");
  const cameraOffline  = document.getElementById("camera-offline");

  // ---- state ----
  let currentMode = "idle";  // idle | auto | manual
  let pilotName = "VROOMBA";
  let ws = null;
  let wsReconnectTimer = null;
  let spinnerFrame = 0;
  let spinnerInterval = null;
  let spinnerDirection = 1;

  // ---- camera feed helpers ----

  function reloadCameraFeed() {
    // Force the browser to re-establish the MJPEG stream connection
    cameraFeed.src = "/camera/stream?" + Date.now();
    cameraFeed.classList.remove("hidden");
    cameraOffline.classList.add("hidden");
  }

  cameraFeed.addEventListener("error", () => {
    cameraFeed.classList.add("hidden");
    cameraOffline.classList.remove("hidden");
  });
  const DOT_COUNT = 8;

  // ---- arrow map ----
  const ARROW_MAP = {
    "fwd/left": "↖", "fwd/idle": "↑", "fwd/right": "↗",
    "idle/left": "←",  "idle/idle": "·",  "idle/right": "→",
    "rev/left": "↙", "rev/idle": "↓", "rev/right": "↘",
  };

  function controlArrow(ctrl) {
    const t = ctrl.throttle || "idle";
    const s = ctrl.steering || "idle";
    return ARROW_MAP[t + "/" + s] || "·";
  }

  // ---- TTS ----

  let ttsEnabled = true;
  let ttsResumeTimer = null;
  let ttsVoice = null;

  // Preload voice selection (voices may load async)
  function pickVoice() {
    const voices = window.speechSynthesis.getVoices();
    if (!voices.length) return;
    ttsVoice = voices.find(v =>
      /samantha|zoe|neural|enhanced/i.test(v.name) && /en/i.test(v.lang)
    ) || voices.find(v => /en/i.test(v.lang) && v.localService) || voices[0];
  }

  // Chrome freezes speechSynthesis after ~15s of continuous speech.
  // A periodic resume() call prevents the engine from stalling.
  function startResumeTimer() {
    stopResumeTimer();
    ttsResumeTimer = setInterval(() => {
      if (window.speechSynthesis.speaking) {
        window.speechSynthesis.pause();
        window.speechSynthesis.resume();
      }
    }, 5000);
  }

  function stopResumeTimer() {
    if (ttsResumeTimer) { clearInterval(ttsResumeTimer); ttsResumeTimer = null; }
  }

  function speakText(text) {
    if (!ttsEnabled || !window.speechSynthesis) return;

    // Hard reset: cancel + small delay to let Chrome's internal state settle
    window.speechSynthesis.cancel();
    stopResumeTimer();

    // Use a small delay after cancel to avoid the cancel/speak race condition
    setTimeout(() => {
      if (!ttsVoice) pickVoice();

      const utt = new SpeechSynthesisUtterance(text);
      if (ttsVoice) utt.voice = ttsVoice;
      utt.rate = 1.05;
      utt.onerror = (e) => {
        console.warn("TTS error:", e.error);
        window.speechSynthesis.cancel();
        stopResumeTimer();
      };
      utt.onend = () => stopResumeTimer();
      window.speechSynthesis.speak(utt);

      startResumeTimer();
    }, 50);
  }

  if (window.speechSynthesis) {
    window.speechSynthesis.onvoiceschanged = () => pickVoice();
    pickVoice(); // try immediately (Firefox has voices ready synchronously)
  } else {
    ttsBtn.classList.add("hidden");
  }

  ttsBtn.addEventListener("click", () => {
    ttsEnabled = !ttsEnabled;
    ttsBtn.classList.toggle("tts-on", ttsEnabled);
    if (!ttsEnabled && window.speechSynthesis) {
      window.speechSynthesis.cancel();
      stopResumeTimer();
    }
  });

  // ---- STT ----

  let recognition = null;

  (function initSTT() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { micBtn.classList.add("hidden"); return; }

    recognition = new SR();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = "en-US";

    recognition.onresult = (e) => {
      inputBox.value = e.results[0][0].transcript;
      micBtn.classList.remove("listening");
      sendMessage();
    };
    recognition.onerror = () => micBtn.classList.remove("listening");
    recognition.onend   = () => micBtn.classList.remove("listening");
  })();

  micBtn.addEventListener("click", () => {
    if (!recognition) return;
    if (micBtn.classList.contains("listening")) {
      recognition.stop();
    } else {
      micBtn.classList.add("listening");
      recognition.start();
    }
  });

  // ---- WebSocket ----

  function connectWS() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);

    ws.onopen = () => {
      console.log("WS connected");
      fetchStatus();
      loadAutopilots();
    };

    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      handleEvent(msg.type, msg.data);
    };

    ws.onclose = () => {
      console.log("WS disconnected");
      wsReconnectTimer = setTimeout(connectWS, 2000);
    };
  }

  function handleEvent(type, data) {
    switch (type) {
      case "message":
        addMessage(data.role, data.content);
        break;
      case "turn":
        clearSpinner();
        addTurn(data);
        showSpinner();
        break;
      case "control":
        updateControl(data);
        break;
      case "status":
        updateMode(data.mode);
        if (data.autopilot) {
          pilotName = data.autopilot.toUpperCase();
          personaValue.textContent = data.autopilot;
          currentPersona = data.autopilot;
          refreshPersonaHighlight();
        }
        if (data.mode !== "auto") clearSpinner();
        break;
      case "reset":
        clearAll();
        break;
      case "thinking":
        showSpinner();
        break;
    }
  }

  // ---- helpers ----

  function timeNow() {
    const d = new Date();
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  }

  // ---- messages ----

  function addMessage(role, content) {
    clearSpinner();
    if (role === "assistant") speakText(content);
    const div = document.createElement("div");
    div.className = `msg msg-${role}`;
    div.innerHTML =
      `<span class="msg-time">${timeNow()}</span>` +
      `<span class="msg-body">${escapeHtml(content)}</span>`;
    messageList.appendChild(div);
    messageList.scrollTop = messageList.scrollHeight;
  }

  function clearAll() {
    messageList.innerHTML = "";
    turnList.innerHTML = "";
  }

  function escapeHtml(str) {
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
  }

  // ---- turns ----

  function addTurn(data) {
    const card = document.createElement("div");
    card.className = "turn-card";
    const arrow = controlArrow(data.control);
    const dur = data.duration || "normal";
    const ts = timeNow();
    card.innerHTML =
      `<div class="turn-card-header">` +
        `<span class="turn-num">#${data.turn_number}</span>` +
        `<span class="turn-arrow">${arrow}</span>` +
        `<span class="turn-duration dur-${dur}">${dur}</span>` +
        `<span class="turn-elapsed">${ts} · +${Number(data.elapsed_seconds).toFixed(1)}s</span>` +
      `</div>` +
      `<div class="turn-summary">${escapeHtml(data.summary)}</div>`;
    card.addEventListener("click", () => showDebug(data));
    turnList.appendChild(card);
    turnList.scrollTop = turnList.scrollHeight;
  }

  // ---- control display ----

  function updateControl(ctrl) {
    ftCtrl.textContent = controlArrow(ctrl);
  }

  // ---- mode ----

  function updateMode(mode) {
    currentMode = mode;
    modeBadge.textContent = "[" + mode.toUpperCase() + "]";
    modeBadge.className = "mode-badge " + mode;
    ftMode.textContent = mode;
    ftMode.className = "footer-mode " + mode;
    pauseBtn.classList.toggle("active-pulse", mode === "auto");
    resumeBtn.classList.toggle("btn-highlight", mode !== "auto");
  }

  // ---- status fetch ----

  async function fetchStatus() {
    try {
      const res = await fetch("/status");
      const s = await res.json();
      ftDotArduino.classList.toggle("ok", s.arduino_connected);
      ftDotLlm.classList.toggle("ok", s.llm_available);
      ftDotCamera.classList.toggle("ok", !!s.camera_available);
      cameraOffline.classList.toggle("hidden", !!s.camera_available);
      cameraFeed.classList.toggle("hidden", !s.camera_available);
      // Start stream if camera just became available and img has no src
      if (s.camera_available && !cameraFeed.src.includes("/camera/stream")) {
        reloadCameraFeed();
      }
      if (s.camera_source_id) {
        currentCamSourceId = s.camera_source_id;
        refreshCameraHighlight();
      }
      updateMode(s.mode);
      if (s.autopilot_name) {
        personaValue.textContent = s.autopilot_name;
        pilotName = s.autopilot_name.toUpperCase();
        currentPersona = s.autopilot_name;
        refreshPersonaHighlight();
      }
      updateControl(s.current_control);
    } catch (e) {
      console.error("Status fetch failed", e);
    }
  }

  // ---- debug modal ----

  function showDebug(data) {
    debugBody.textContent = JSON.stringify(data, null, 2);
    debugModal.classList.remove("hidden");
  }

  debugClose.addEventListener("click", () => debugModal.classList.add("hidden"));
  debugModal.addEventListener("click", (e) => {
    if (e.target === debugModal) debugModal.classList.add("hidden");
  });

  // ---- input form ----

  async function sendMessage() {
    const text = inputBox.value.trim();
    if (!text) return;
    inputBox.value = "";

    await fetch("/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
  }

  inputForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    await sendMessage();
  });

  // ---- keyboard ----

  document.addEventListener("keydown", (e) => {
    // Don't intercept when typing in input
    if (document.activeElement === inputBox && e.key !== "Escape") return;

    if (e.key === "Escape") {
      e.preventDefault();
      fetch("/pause", { method: "POST" });
      return;
    }

    if (e.key === "r" && document.activeElement !== inputBox) {
      e.preventDefault();
      fetch("/resume", { method: "POST" });
      return;
    }

    // Manual arrow key control (only when not typing)
    if (document.activeElement === inputBox) return;

    const arrowCmds = {
      ArrowUp:    { throttle: "fwd",  steering: "idle" },
      ArrowDown:  { throttle: "rev",  steering: "idle" },
      ArrowLeft:  { throttle: "idle", steering: "left" },
      ArrowRight: { throttle: "idle", steering: "right" },
    };

    if (arrowCmds[e.key]) {
      e.preventDefault();
      fetch("/manual", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(arrowCmds[e.key]),
      });
    }
  });

  document.addEventListener("keyup", (e) => {
    if (document.activeElement === inputBox) return;
    if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(e.key)) {
      e.preventDefault();
      fetch("/manual", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ throttle: "idle", steering: "idle" }),
      });
    }
  });

  // ---- spinner (footer dot-sweep) ----

  function renderDots() {
    let s = "";
    for (let i = 0; i < DOT_COUNT; i++) {
      const dist = Math.abs(i - spinnerFrame);
      if (dist === 0) s += '<span class="dot-bright">●</span>';
      else if (dist === 1) s += '<span class="dot-mid">●</span>';
      else s += '<span class="dot-dim">·</span>';
    }
    headerSpinner.innerHTML = s;
  }

  function showSpinner() {
    if (spinnerInterval) return;
    spinnerFrame = 0;
    spinnerDirection = 1;
    headerSpinner.classList.remove("hidden");
    renderDots();
    spinnerInterval = setInterval(() => {
      spinnerFrame += spinnerDirection;
      if (spinnerFrame >= DOT_COUNT - 1) spinnerDirection = -1;
      if (spinnerFrame <= 0) spinnerDirection = 1;
      renderDots();
    }, 120);
  }

  function clearSpinner() {
    if (spinnerInterval) { clearInterval(spinnerInterval); spinnerInterval = null; }
    headerSpinner.classList.add("hidden");
  }

  // ---- generic expand/collapse helper ----

  function toggleExpand(arrow, listEl, otherArrow, otherList) {
    const opening = listEl.classList.contains("hidden");
    // collapse the other one first
    otherList.classList.add("hidden");
    otherArrow.classList.remove("open");
    // toggle this one
    listEl.classList.toggle("hidden", !opening);
    arrow.classList.toggle("open", opening);
  }

  // ---- persona (autopilot) picker ----

  let currentPersona = null;

  function refreshPersonaHighlight() {
    personaList.querySelectorAll(".ap-item").forEach(el => {
      el.classList.toggle("selected", el.querySelector(".ap-item-name").textContent === currentPersona);
    });
  }

  async function loadAutopilots() {
    try {
      const res = await fetch("/autopilots");
      const pilotList = await res.json();
      buildPersonaList(pilotList);
    } catch (e) {
      console.error("Failed to load autopilots", e);
    }
  }

  function buildPersonaList(pilotList) {
    personaList.innerHTML = "";
    for (const p of pilotList) {
      const item = document.createElement("div");
      item.className = "ap-item" + (currentPersona === p.name ? " selected" : "");
      item.innerHTML =
        `<div class="ap-item-name">${escapeHtml(p.name)}</div>` +
        `<div class="ap-item-desc">${escapeHtml(p.description)}</div>`;
      item.addEventListener("click", async () => {
        await fetch("/autopilot", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: p.name.toLowerCase() }),
        });
        currentPersona = p.name;
        personaValue.textContent = p.name;
        pilotName = p.name.toUpperCase();
        // collapse
        personaList.classList.add("hidden");
        personaArrow.classList.remove("open");
        // refresh selected
        personaList.querySelectorAll(".ap-item").forEach(el => {
          el.classList.toggle("selected", el.querySelector(".ap-item-name").textContent === p.name);
        });
        fetchStatus();
      });
      personaList.appendChild(item);
    }
  }

  personaToggle.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleExpand(personaArrow, personaList, cameraArrow, cameraList);
  });

  // ---- camera picker ----

  let currentCamSourceId = null;

  function refreshCameraHighlight() {
    cameraList.querySelectorAll(".ap-item").forEach(el => {
      el.classList.toggle("selected", el.dataset.sourceId === currentCamSourceId);
    });
  }

  async function loadCameraDevices() {
    try {
      const res = await fetch("/camera/devices");
      const devices = await res.json();
      buildCameraList(devices);
    } catch (e) {
      console.error("Failed to load camera devices", e);
    }
  }

  function buildCameraList(devices) {
    cameraList.innerHTML = "";
    if (devices.length === 0) {
      cameraValue.textContent = "none";
      return;
    }
    const currentDevice = devices.find(d => d.id === currentCamSourceId);
    if (currentDevice) {
      cameraValue.textContent = currentDevice.name;
    } else {
      currentCamSourceId = devices[0].id;
      cameraValue.textContent = devices[0].name;
    }
    for (const d of devices) {
      const item = document.createElement("div");
      item.className = "ap-item" + (d.id === currentCamSourceId ? " selected" : "");
      item.dataset.sourceId = d.id;
      item.innerHTML =
        `<div class="ap-item-name">${escapeHtml(d.name)}</div>` +
        `<div class="ap-item-desc">${escapeHtml(d.description || "")}</div>`;
      item.addEventListener("click", async () => {
        await fetch("/camera/select", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source_id: d.id }),
        });
        currentCamSourceId = d.id;
        cameraValue.textContent = d.name;
        // reload the stream connection
        reloadCameraFeed();
        // collapse
        cameraList.classList.add("hidden");
        cameraArrow.classList.remove("open");
        refreshCameraHighlight();
        fetchStatus();
      });
      cameraList.appendChild(item);
    }
    refreshCameraHighlight();
  }

  cameraToggle.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleExpand(cameraArrow, cameraList, personaArrow, personaList);
    // lazy-load device list on first open
    if (!cameraList.hasChildNodes()) loadCameraDevices();
  });

  // collapse both on outside click
  document.addEventListener("click", () => {
    personaList.classList.add("hidden");
    personaArrow.classList.remove("open");
    cameraList.classList.add("hidden");
    cameraArrow.classList.remove("open");
  });

  // ---- pause / reset buttons ----

  pauseBtn.addEventListener("click", () => {
    fetch("/pause", { method: "POST" });
  });

  resumeBtn.addEventListener("click", () => {
    fetch("/resume", { method: "POST" });
  });

  resetBtn.addEventListener("click", () => {
    fetch("/reset", { method: "POST" });
  });

  // ---- init ----

  connectWS();
  setInterval(fetchStatus, 5000);

})();
