(function () {
  "use strict";

  const BASE = window.location.origin;

  // ───── DOM refs ─────
  const $ = (id) => document.getElementById(id);
  const el = {
    driveLink: $("drive-link"),
    btnProcess: $("btn-process"),
    btnDlTranscript: $("btn-dl-transcript"),
    sceneFile: $("scene-file"),
    btnUpload: $("btn-upload"),
    btnDlVideo: $("btn-dl-video"),
    btnRetry: $("btn-retry"),
    progressFill: $("progress-fill"),
    progressPct: $("progress-pct"),
    progressMsg: $("progress-msg"),
    logList: $("log-list"),
    errorMsg: $("error-msg"),
  };

  let eventSource = null;
  let running = false;

  // ───── helpers ─────
  function showStep(id) {
    document.querySelectorAll(".step").forEach((s) => s.classList.remove("active"));
    const step = document.getElementById(id);
    if (step) step.classList.add("active");
  }

  function addLog(msg) {
    const d = el.logList;
    const line = document.createElement("div");
    line.className = "log-line";
    const t = new Date().toLocaleTimeString();
    line.textContent = `[${t}] ${msg}`;
    d.appendChild(line);
    d.scrollTop = d.scrollHeight;
  }

  function setProgress(pct, msg) {
    el.progressFill.style.width = pct + "%";
    el.progressPct.textContent = pct + "%";
    if (msg !== undefined) el.progressMsg.textContent = msg;
  }

  function stopSSE() {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
  }

  function startSSE() {
    stopSSE();
    eventSource = new EventSource(BASE + "/progress");
    eventSource.onmessage = function (ev) {
      try {
        var d = JSON.parse(ev.data);
        setProgress(d.percent, d.message);

        if (d.logs && d.logs.length) {
          var last = d.logs[d.logs.length - 1];
          addLog(last.replace(/^\[[^\]]*\]\s*/, ""));
        }

        if (d.status === "error") {
          showError(d.message || "Unknown error");
        }
        if (d.status === "transcript_ready") {
          // SSE detected transcript ready — show upload UI
          showTranscriptSteps();
        }
        if (d.status === "completed") {
          addLog("Video created successfully!");
          showStep("step-done");
          running = false;
          stopSSE();
        }
      } catch (e) { /* ignore parse errors */ }
    };
    eventSource.onerror = function () {
      // auto-reconnect handled by EventSource
    };
  }

  function showTranscriptSteps() {
    showStep("step-transcript");
    document.getElementById("step-transcript").style.display = "block";
    document.getElementById("step-scene").style.display = "block";
    setProgress(70, "Waiting for Scene JSON…");
  }

  function showError(msg) {
    el.errorMsg.textContent = msg;
    showStep("step-error");
    running = false;
    el.btnProcess.disabled = false;
    addLog("ERROR: " + msg);
    stopSSE();
  }

  function resetUI() {
    running = false;
    el.btnProcess.disabled = false;
    el.btnUpload.disabled = false;
    el.btnUpload.textContent = "Upload & Render";
    el.progressFill.style.width = "0%";
    el.progressPct.textContent = "0%";
    el.progressMsg.textContent = "Initialising…";
    el.logList.innerHTML = "";
    stopSSE();
    showStep("step-download");
  }

  // ───── event bindings ─────
  el.btnProcess.addEventListener("click", startProcess);
  el.driveLink.addEventListener("keydown", function (e) {
    if (e.key === "Enter") startProcess();
  });
  el.btnDlTranscript.addEventListener("click", function () {
    window.open(BASE + "/download/transcript", "_blank");
  });
  el.sceneFile.addEventListener("change", function () {
    el.btnUpload.disabled = !el.sceneFile.files.length;
  });
  el.btnUpload.addEventListener("click", uploadSceneJson);
  el.btnDlVideo.addEventListener("click", function () {
    window.open(BASE + "/download/video", "_blank");
  });
  el.btnRetry.addEventListener("click", resetUI);

  // ───── Step 1: Download ─────
  function startProcess() {
    var link = el.driveLink.value.trim();
    if (!link) {
      el.driveLink.style.borderColor = "#d63031";
      el.driveLink.focus();
      setTimeout(function () { el.driveLink.style.borderColor = ""; }, 2000);
      return;
    }

    running = true;
    el.btnProcess.disabled = true;
    showStep("step-progress");
    addLog("Starting download…");

    // SSE for real-time progress display
    startSSE();

    // POST /download
    var fd = new FormData();
    fd.append("link", link);

    fetch(BASE + "/download", { method: "POST", body: fd })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || r.statusText); });
        return r.json();
      })
      .then(function (data) {
        addLog("Movie " + data.movie_size_mb + " MB  |  Audio " + data.audio_size_mb + " MB");
        addLog("Download complete. Starting transcription…");
        transcribeAudio();
      })
      .catch(function (err) {
        showError(err.message || "Download failed");
      });
  }

  // ───── Step 2: Transcribe ─────
  function transcribeAudio() {
    fetch(BASE + "/transcribe", { method: "POST" })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || r.statusText); });
        return r.json();
      })
      .then(function (data) {
        addLog("Transcription: " + data.segments + " segments");
        showTranscriptSteps();
        stopSSE(); // close old SSE, transcript progress done
      })
      .catch(function (err) {
        showError(err.message || "Transcription failed");
      });
  }

  // ───── Step 3: Upload Scene JSON + Render ─────
  function uploadSceneJson() {
    var file = el.sceneFile.files[0];
    if (!file) return;

    el.btnUpload.disabled = true;
    el.btnUpload.textContent = "Uploading…";

    var fd = new FormData();
    fd.append("file", file);

    fetch(BASE + "/upload_scene_json", { method: "POST", body: fd })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || r.statusText); });
        return r.json();
      })
      .then(function (data) {
        addLog("Scene JSON: " + data.scenes + " scenes, " + Math.round(data.total_duration) + "s");
        el.btnUpload.textContent = "Upload & Render";
        renderVideo();
      })
      .catch(function (err) {
        showError(err.message || "Upload failed");
        el.btnUpload.disabled = false;
        el.btnUpload.textContent = "Upload & Render";
      });
  }

  // ───── Step 4: Render ─────
  function renderVideo() {
    showStep("step-progress");
    setProgress(80, "Extracting screenshots…");
    addLog("Starting render pipeline…");

    // SSE captures progress during render
    startSSE();

    fetch(BASE + "/render", { method: "POST" })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || r.statusText); });
        return r.json();
      })
      .then(function (rd) {
        addLog("Render complete — " + rd.size_mb + " MB, " + Math.round(rd.duration) + "s");
        // SSE should have already set "completed" status, but fallback:
        showStep("step-done");
        running = false;
        stopSSE();
      })
      .catch(function (err) {
        showError(err.message || "Render failed");
      });
  }

  // ───── initialise ─────
  document.getElementById("step-transcript").style.display = "none";
  document.getElementById("step-scene").style.display = "none";
})();
