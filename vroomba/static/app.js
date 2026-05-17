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
  const pilotPickerBtn = document.getElementById("pilot-picker-btn");
  const pilotPickerMenu= document.getElementById("pilot-picker-menu");
  const micBtn         = document.getElementById("mic-btn");
  const ttsBtn         = document.getElementById("tts-btn");

  // Footer status bar refs
  const ftDotArduino   = document.getElementById("ft-dot-arduino");
  const ftDotLlm       = document.getElementById("ft-dot-llm");
  const ftMode         = document.getElementById("ft-mode");
  const ftCtrl         = document.getElementById("ft-ctrl");
  const headerSpinner  = document.getElementById("header-spinner");

  // ---- state ----
  let currentMode = "idle";  // idle | auto | manual
  let pilotName = "VROOMBA";
  let ws = null;
  let wsReconnectTimer = null;
  let spinnerFrame = 0;
  let spinnerInterval = null;
  let spinnerDirection = 1;
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

  function speakText(text) {
    if (!ttsEnabled || !window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const utt = new SpeechSynthesisUtterance(text);
    const voices = window.speechSynthesis.getVoices();
    const preferred = voices.find(v =>
      /samantha|zoe|neural|enhanced/i.test(v.name) && /en/i.test(v.lang)
    ) || voices.find(v => /en/i.test(v.lang) && v.localService);
    if (preferred) utt.voice = preferred;
    utt.rate = 1.05;
    window.speechSynthesis.speak(utt);
  }

  if (window.speechSynthesis) {
    window.speechSynthesis.onvoiceschanged = () => {};
  } else {
    ttsBtn.classList.add("hidden");
  }

  ttsBtn.addEventListener("click", () => {
    ttsEnabled = !ttsEnabled;
    ttsBtn.classList.toggle("tts-on", ttsEnabled);
    if (!ttsEnabled) window.speechSynthesis && window.speechSynthesis.cancel();
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
          pilotPickerBtn.textContent = data.autopilot;
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
    const ts = timeNow();
    card.innerHTML =
      `<div class="turn-card-header">` +
        `<span class="turn-num">#${data.turn_number}</span>` +
        `<span class="turn-arrow">${arrow}</span>` +
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
      updateMode(s.mode);
      if (s.autopilot_name) {
        pilotPickerBtn.textContent = s.autopilot_name;
        pilotName = s.autopilot_name.toUpperCase();
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
    headerSpinner.innerHTML = "";
  }

  // ---- autopilot picker ----

  async function loadAutopilots() {
    try {
      const res = await fetch("/autopilots");
      const pilotList = await res.json();
      buildPilotMenu(pilotList);
    } catch (e) {
      console.error("Failed to load autopilots", e);
    }
  }

  function buildPilotMenu(pilotList) {
    pilotPickerMenu.innerHTML = "";
    for (const p of pilotList) {
      const item = document.createElement("div");
      item.className = "pilot-option";
      item.innerHTML =
        `<div class="pilot-option-name">${escapeHtml(p.name)}</div>` +
        `<div class="pilot-option-desc">${escapeHtml(p.description)}</div>`;
      pilotPickerMenu.appendChild(item);
    }
  }

  pilotPickerBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    pilotPickerMenu.classList.toggle("hidden");
  });

  document.addEventListener("click", () => {
    pilotPickerMenu.classList.add("hidden");
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
  loadAutopilots();
  setInterval(fetchStatus, 5000);

})();
