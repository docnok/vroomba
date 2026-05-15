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
  const dotArduino     = document.getElementById("dot-arduino");
  const dotLlm         = document.getElementById("dot-llm");
  const statusMode     = document.getElementById("status-mode");
  const statusControl  = document.getElementById("status-control");
  const killBtn        = document.getElementById("kill-btn");
  const turnList       = document.getElementById("turn-list");
  const debugModal     = document.getElementById("debug-modal");
  const debugBody      = document.getElementById("debug-body");
  const debugClose     = document.getElementById("debug-close");
  const pilotPickerBtn = document.getElementById("pilot-picker-btn");
  const pilotPickerMenu= document.getElementById("pilot-picker-menu");
  const directiveBody  = document.getElementById("directive-body");
  const micBtn         = document.getElementById("mic-btn");
  const ttsBtn         = document.getElementById("tts-btn");

  // ---- state ----
  let currentMode = "idle";  // idle | auto | manual
  let hasActiveDirective = false;
  let pilotName = "VROOMBA";
  let selectedPilot = "homer";
  let pilotList = [];
  let ws = null;
  let wsReconnectTimer = null;
  let spinnerEl = null;
  let spinnerInterval = null;

  const SPINNER_FRAMES = [
    "◐ Calibrating flux capacitor",
    "◓ Checking blind spots (just kidding)",
    "◑ Consulting the road less traveled",
    "◒ Revving neural engines",
    "◐ Adjusting mirrors (don't have any)",
    "◓ Calculating scenic route",
    "◑ Warming up the hamster wheel",
    "◒ Asking for directions",
    "◐ Parallel parking in my mind",
    "◓ Dodging imaginary potholes",
    "◑ Recalculating everything",
    "◒ Engaging turbo mode (beep boop)",
  ];

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
    // Prefer the first high-quality voice available; fall back to default.
    const voices = window.speechSynthesis.getVoices();
    const preferred = voices.find(v =>
      /samantha|zoe|neural|enhanced/i.test(v.name) && /en/i.test(v.lang)
    ) || voices.find(v => /en/i.test(v.lang) && v.localService);
    if (preferred) utt.voice = preferred;
    utt.rate = 1.05;
    window.speechSynthesis.speak(utt);
  }

  // Voices load asynchronously on some browsers; just re-request on change.
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
      case "directive_start":
        clearMessages();
        break;
      case "directive_info":
        updateDirective(data.directive, data.plan);
        break;
      case "thinking":
        showSpinner();
        break;
    }
  }

  // ---- messages ----

  function addMessage(role, content) {
    clearSpinner();
    if (role === "assistant") speakText(content);
    const labels = { user: "YOU", assistant: pilotName, system: "SYSTEM" };
    const div = document.createElement("div");
    div.className = `msg msg-${role}`;
    div.innerHTML =
      `<div class="msg-label">${labels[role] || role.toUpperCase()}</div>` +
      `<div class="msg-body">${escapeHtml(content)}</div>`;
    messageList.appendChild(div);
    messageList.scrollTop = messageList.scrollHeight;
  }

  function clearMessages() {
    // Insert a divider if there's existing content
    if (messageList.children.length > 0) {
      const hr = document.createElement("div");
      hr.className = "directive-divider";
      hr.innerHTML = `<span>NEW DIRECTIVE</span>`;
      messageList.appendChild(hr);
    }
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
    card.innerHTML =
      `<div class="turn-card-header">` +
        `<span class="turn-num">#${data.turn_number}</span>` +
        `<span class="turn-arrow">${arrow}</span>` +
        `<span class="turn-elapsed">+${Number(data.elapsed_seconds).toFixed(1)}s</span>` +
      `</div>` +
      `<div class="turn-summary">${escapeHtml(data.summary)}</div>`;
    card.addEventListener("click", () => showDebug(data));
    turnList.appendChild(card);
    turnList.scrollTop = turnList.scrollHeight;
  }

  // ---- control display ----

  function updateControl(ctrl) {
    statusControl.textContent = controlArrow(ctrl);
  }

  // ---- mode ----

  function updateMode(mode) {
    currentMode = mode;
    statusMode.textContent = mode.toUpperCase();
    modeBadge.textContent = mode.toUpperCase();
    modeBadge.className = "mode-badge " + mode;
    hasActiveDirective = mode === "auto";
    killBtn.classList.toggle("active-pulse", mode === "auto");
  }

  // ---- status fetch ----

  async function fetchStatus() {
    try {
      const res = await fetch("/status");
      const s = await res.json();
      dotArduino.classList.toggle("ok", s.arduino_connected);
      dotLlm.classList.toggle("ok", s.llm_available);
      updateMode(s.mode);
      if (s.autopilot_name) {
        pilotPickerBtn.textContent = s.autopilot_name;
        pilotName = s.autopilot_name.toUpperCase();
      }
      updateControl(s.current_control);
      if (s.directive) updateDirective(s.directive, s.plan);
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

    if (currentMode === "idle" && !hasActiveDirective) {
      // New directive
      await fetch("/directive", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, autopilot: selectedPilot }),
      });
    } else {
      // Resume with message
      await fetch("/resume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
    }
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
      fetch("/kill", { method: "POST" });
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

  // ---- spinner ----

  function showSpinner() {
    if (spinnerEl) return; // already showing
    spinnerEl = document.createElement("div");
    spinnerEl.className = "msg msg-system spinner-msg";
    let idx = Math.floor(Math.random() * SPINNER_FRAMES.length);
    spinnerEl.innerHTML =
      `<div class="msg-label">SYSTEM</div>` +
      `<div class="msg-body spinner-text">${escapeHtml(SPINNER_FRAMES[idx])}</div>`;
    messageList.appendChild(spinnerEl);
    messageList.scrollTop = messageList.scrollHeight;
    spinnerInterval = setInterval(() => {
      idx = (idx + 1) % SPINNER_FRAMES.length;
      const body = spinnerEl?.querySelector(".spinner-text");
      if (body) body.textContent = SPINNER_FRAMES[idx];
    }, 1500);
  }

  function clearSpinner() {
    if (spinnerInterval) { clearInterval(spinnerInterval); spinnerInterval = null; }
    if (spinnerEl) { spinnerEl.remove(); spinnerEl = null; }
  }

  // ---- autopilot picker ----

  async function loadAutopilots() {
    try {
      const res = await fetch("/autopilots");
      pilotList = await res.json();
      buildPilotMenu();
    } catch (e) {
      console.error("Failed to load autopilots", e);
    }
  }

  function buildPilotMenu() {
    pilotPickerMenu.innerHTML = "";
    for (const p of pilotList) {
      const item = document.createElement("div");
      item.className = "pilot-option" + (p.name.toLowerCase() === selectedPilot ? " selected" : "");
      item.innerHTML =
        `<div class="pilot-option-name">${escapeHtml(p.name)}</div>` +
        `<div class="pilot-option-desc">${escapeHtml(p.description)}</div>`;
      item.addEventListener("click", (e) => {
        e.stopPropagation();
        selectedPilot = p.name.toLowerCase();
        pilotPickerBtn.textContent = p.name;
        pilotPickerMenu.classList.add("hidden");
        buildPilotMenu(); // refresh selection state
      });
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

  // ---- directive display ----

  function updateDirective(directive, plan) {
    let html = `<div class="directive-text">${escapeHtml(directive)}</div>`;
    if (plan) {
      html += `<div class="directive-plan">${escapeHtml(plan)}</div>`;
    }
    directiveBody.innerHTML = html;
  }

  // ---- kill button ----

  killBtn.addEventListener("click", () => {
    fetch("/kill", { method: "POST" });
  });

  // ---- init ----

  connectWS();
  loadAutopilots();
  // Poll status periodically
  setInterval(fetchStatus, 5000);

})();
