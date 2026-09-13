(() => {
  const $ = (id) => document.getElementById(id);

  const orb = $("orb");
  const stateLabel = $("stateLabel");
  const stateSub = $("stateSub");
  const startBtn = $("startBtn");
  const stopBtn = $("stopBtn");
  const activityList = $("activityList");
  const wsDot = $("wsDot");
  const rowMic = $("rowMic");
  const rowLlm = $("rowLlm");
  const rowTts = $("rowTts");

  const STATE_LABELS = {
    STOPPED:    ["Stopped",    "Noy belum berjalan"],
    STARTING:   ["Starting",  "Menyiapkan Noy Agent..."],
    ONLINE:     ["Online",    "Noy siap digunakan"],
    LISTENING:  ["Listening", "Noy sedang mendengarkan"],
    PROCESSING: ["Processing","Noy sedang memproses"],
    RESPONDING: ["Responding","Noy sedang menjawab"],
    OFFLINE:    ["Offline",   "Noy berhenti tak terduga"],
    STOPPING:   ["Stopping",  "Menghentikan Noy..."],
  };

  function applyState(state) {
    const key = (state || "STOPPED").toUpperCase();
    const [label, sub] = STATE_LABELS[key] || [key, ""];
    orb.dataset.state = key.toLowerCase();
    stateLabel.textContent = label;
    stateSub.textContent = sub;

    const running = !["STOPPED", "OFFLINE"].includes(key);
    startBtn.disabled = running;
    stopBtn.disabled = !running;

    const connected = running;
    setRow(rowMic, connected ? "on" : "idle", connected ? "Connected" : "Menunggu");
    setRow(rowLlm, connected ? "on" : "idle", connected ? "Connected" : "Menunggu");
    setRow(rowTts, connected ? "on" : "idle", connected ? "Ready" : "Menunggu");
  }

  function setRow(el, kind, text) {
    el.innerHTML = `<i class="dot dot--${kind}"></i>${text}`;
  }

  function addActivity(line) {
    if (activityList.querySelector(".activity__empty")) {
      activityList.innerHTML = "";
    }
    const li = document.createElement("li");
    const time = new Date().toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    li.innerHTML = `<span class="t">${time}</span>${escapeHtml(line)}`;
    activityList.appendChild(li);
    // keep last 60 in DOM
    while (activityList.children.length > 60) {
      activityList.removeChild(activityList.firstChild);
    }
    activityList.scrollTop = activityList.scrollHeight;
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  // ---------------- REST actions ----------------

  async function fetchStatus() {
    try {
      const res = await fetch("/api/status");
      const data = await res.json();
      applyState(data.state);
    } catch (e) {
      applyState("STOPPED");
    }
  }

  startBtn.addEventListener("click", async () => {
    startBtn.disabled = true;
    try {
      const res = await fetch("/api/start", { method: "POST" });
      const data = await res.json();
      if (data.status === "already_running") {
        addActivity("Noy sudah berjalan.");
      }
      applyState(data.state || "STARTING");
    } catch (e) {
      addActivity("Gagal menghubungi Web Control backend.");
      startBtn.disabled = false;
    }
  });

  stopBtn.addEventListener("click", async () => {
    stopBtn.disabled = true;
    try {
      const res = await fetch("/api/stop", { method: "POST" });
      const data = await res.json();
      applyState(data.state || "STOPPED");
    } catch (e) {
      addActivity("Gagal menghubungi Web Control backend.");
    }
  });

  // ---------------- Mode switch (UI-only preference, saved to settings) ----------------

  document.querySelectorAll(".mode-pill").forEach((btn) => {
    btn.addEventListener("click", async () => {
      document.querySelectorAll(".mode-pill").forEach((b) => b.classList.remove("mode-pill--active"));
      btn.classList.add("mode-pill--active");
      await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ listening_mode: btn.dataset.mode }),
      });
    });
  });

  // ---------------- Settings panel ----------------

  const settingsPanel = $("settingsPanel");
  $("settingsToggle").addEventListener("click", () => {
    settingsPanel.classList.toggle("open");
  });

  const ttsVolume = $("ttsVolume");
  ttsVolume.addEventListener("input", () => {
    $("ttsVolumeValue").textContent = ttsVolume.value;
  });

  async function loadSettings() {
    try {
      const res = await fetch("/api/settings");
      const s = await res.json();
      $("pttShortcut").value = s.push_to_talk_shortcut || "";
      $("silenceTimeout").value = s.stt_silence_timeout_ms ?? 1200;
      ttsVolume.value = s.tts_volume ?? 80;
      $("ttsVolumeValue").textContent = ttsVolume.value;
      $("debugMode").checked = !!s.debug_mode;

      document.querySelectorAll(".mode-pill").forEach((b) => {
        b.classList.toggle("mode-pill--active", b.dataset.mode === s.listening_mode);
      });

      if (s.microphone) {
        const opt = document.createElement("option");
        opt.value = s.microphone;
        opt.textContent = s.microphone;
        opt.selected = true;
        $("micSelect").appendChild(opt);
      }
    } catch (e) {
      /* backend belum siap, biarkan default */
    }
  }

  async function loadMicrophones() {
    try {
      const res = await fetch("/api/microphones");
      const data = await res.json();
      if (data.available && data.microphones.length) {
        const select = $("micSelect");
        data.microphones.forEach((m) => {
          const opt = document.createElement("option");
          opt.value = m.name;
          opt.textContent = m.name;
          select.appendChild(opt);
        });
      }
    } catch (e) { /* opsional, abaikan */ }
  }

  $("saveSettings").addEventListener("click", async () => {
    const payload = {
      push_to_talk_shortcut: $("pttShortcut").value,
      stt_silence_timeout_ms: parseInt($("silenceTimeout").value || "1200", 10),
      microphone: $("micSelect").value,
      tts_volume: parseInt(ttsVolume.value, 10),
      debug_mode: $("debugMode").checked,
    };
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const note = $("settingsSavedNote");
    note.textContent = "Tersimpan.";
    setTimeout(() => (note.textContent = ""), 2000);
  });

  // ---------------- WebSocket realtime ----------------

  function connectWs() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/status`);

    ws.onopen = () => wsDot.classList.add("conn-dot--on");
    ws.onclose = () => {
      wsDot.classList.remove("conn-dot--on");
      setTimeout(connectWs, 2000); // auto-reconnect
    };
    ws.onerror = () => ws.close();

    ws.onmessage = (evt) => {
      const msg = JSON.parse(evt.data);
      if (msg.type === "status") {
        applyState(msg.data.state);
      } else if (msg.type === "log") {
        addActivity(msg.line);
      }
    };
  }

  // ---------------- init ----------------

  fetchStatus();
  loadSettings();
  loadMicrophones();
  connectWs();

  // fallback polling kalau websocket gagal total
  setInterval(fetchStatus, 5000);
})();
