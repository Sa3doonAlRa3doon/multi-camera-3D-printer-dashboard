"use strict";

const state = {
  csrf: "",
  cameras: [],
  autostart: null,
  network: null,
  printer: null,
  currentUser: null,
  users: [],
  maxCameras: 6,
  maxUsers: 10,
  view: "cameras",
  hidden: new Set(JSON.parse(localStorage.getItem("hiddenCameras") || "[]")),
};
const recordings = new Map();
const timelapses = new Map();
const $ = (selector) => document.querySelector(selector);
const grid = $("#camera-grid");
const dialog = $("#camera-dialog");
const accountDialog = $("#account-dialog");

async function api(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = state.csrf;
  const response = await fetch(url, { ...options, headers, credentials: "same-origin" });
  if (response.status === 401) { location.href = "/login"; throw new Error("Sign in required"); }
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { const data = await response.json(); message = data.detail || message; } catch (_) { /* no JSON */ }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

function cameraFingerprint(cameras) {
  return JSON.stringify(cameras.map(({ status, ...camera }) => camera));
}

function saveHidden() { localStorage.setItem("hiddenCameras", JSON.stringify([...state.hidden])); }

function streamUrl(camera) { return `/api/cameras/${encodeURIComponent(camera.id)}/stream?t=${Date.now()}`; }

function cameraVideoSummary(camera) {
  const resolution = camera.target_width && camera.target_height ? `${camera.target_width}×${camera.target_height}` : "source size";
  const fps = camera.target_fps ? `${camera.target_fps} FPS` : "source FPS";
  const rotation = { 90: "90° right", 180: "180°", 270: "90° left" }[camera.rotation] || "no rotation";
  const flip = { horizontal: "mirrored", vertical: "vertical flip", both: "both flips" }[camera.flip] || "no flip";
  return `${resolution} · ${fps} · ${rotation} · ${flip}`;
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("show"), 3500);
}

function safeFilename(name) {
  return name.replace(/[^a-z0-9_-]+/gi, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "camera";
}

function timestamp() { return new Date().toISOString().replace(/[:.]/g, "-"); }

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

function captureCanvas(image) {
  if (!image.naturalWidth || !image.naturalHeight) throw new Error("Wait for the camera image to appear first.");
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
  return canvas;
}

function supportedVideoFormat() {
  const choices = [
    { mimeType: "video/webm;codecs=vp9", extension: "webm" },
    { mimeType: "video/webm;codecs=vp8", extension: "webm" },
    { mimeType: "video/webm", extension: "webm" },
    { mimeType: "video/mp4;codecs=avc1.42E01E", extension: "mp4" },
    { mimeType: "video/mp4", extension: "mp4" },
  ];
  return choices.find(format => MediaRecorder.isTypeSupported(format.mimeType)) || { mimeType: "", extension: "webm" };
}

function videoExtension(mimeType, fallback = "webm") {
  return String(mimeType || "").toLowerCase().includes("mp4") ? "mp4" : fallback;
}

function saveScreenshot(camera, image) {
  try {
    const canvas = captureCanvas(image);
    canvas.toBlob(blob => {
      if (!blob) return showToast("This browser could not create the screenshot.");
      const filename = `${safeFilename(camera.name)}-${timestamp()}.jpg`;
      downloadBlob(blob, filename);
      showToast(`Screenshot saved on this viewing device: ${filename}`);
    }, "image/jpeg", 0.94);
  } catch (error) { showToast(error.message); }
}

function finishRecording(cameraId) {
  const active = recordings.get(cameraId);
  if (!active || active.recorder.state === "inactive") return;
  active.button.textContent = "Saving…";
  active.button.disabled = true;
  try { active.recorder.requestData(); } catch (_) { /* stop still flushes the final chunk */ }
  active.recorder.stop();
}

function toggleRecording(camera, image, button) {
  if (recordings.has(camera.id)) return finishRecording(camera.id);
  if (timelapses.has(camera.id)) return showToast("Stop the timelapse before starting a normal recording for this camera.");
  if (typeof MediaRecorder === "undefined") return showToast("Recording is not supported by this browser.");
  let canvas;
  try { canvas = captureCanvas(image); } catch (error) { return showToast(error.message); }
  if (typeof canvas.captureStream !== "function") return showToast("Canvas recording is not supported by this browser.");
  const stream = canvas.captureStream(15);
  const format = supportedVideoFormat();
  let recorder;
  try { recorder = new MediaRecorder(stream, format.mimeType ? { mimeType: format.mimeType } : undefined); }
  catch (_) { return showToast("This browser could not start a recording."); }
  const chunks = [];
  const draw = () => {
    if (image.naturalWidth) canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
  };
  const timer = setInterval(draw, 66);
  recorder.addEventListener("dataavailable", event => { if (event.data.size) chunks.push(event.data); });
  recorder.addEventListener("stop", () => {
    clearInterval(timer);
    stream.getTracks().forEach(track => track.stop());
    recordings.delete(camera.id);
    button.textContent = "Record";
    button.classList.remove("recording");
    button.disabled = false;
    if (!chunks.length) return showToast("The recording did not contain any video data.");
    const mimeType = recorder.mimeType || format.mimeType || "video/webm";
    const blob = new Blob(chunks, { type: mimeType });
    if (!blob.size) return showToast("Recording failed: the browser produced an empty video file.");
    const filename = `${safeFilename(camera.name)}-${timestamp()}.${videoExtension(mimeType, format.extension)}`;
    downloadBlob(blob, filename);
    showToast(`Recording saved on this viewing device: ${filename} (${Math.max(1, Math.round(blob.size / 1024))} KB)`);
  });
  recorder.addEventListener("error", event => {
    showToast(`Recording failed: ${event.error?.message || "the browser encoder stopped unexpectedly."}`);
  });
  recordings.set(camera.id, { recorder, timer, button });
  button.textContent = "Stop & save";
  button.classList.add("recording");
  recorder.start(1000);
  showToast("Recording on this viewing device. Nothing is being saved on the Pi.");
}

function timelapseOptions() {
  const bounded = (value, minimum, maximum, fallback) => {
    const number = Number(value);
    return Number.isFinite(number) ? Math.min(maximum, Math.max(minimum, Math.round(number))) : fallback;
  };
  return {
    mode: $("#timelapse-mode").value === "interval" ? "interval" : "layer",
    intervalSeconds: bounded($("#timelapse-interval").value, 2, 3600, 10),
    playbackFps: bounded($("#timelapse-fps").value, 1, 60, 30),
    maxFrames: bounded($("#timelapse-max-frames").value, 60, 10000, 3000),
  };
}

function updateTimelapseMode() {
  const layerMode = $("#timelapse-mode").value === "layer";
  $("#timelapse-interval-label").hidden = layerMode;
  $("#timelapse-mode-help").textContent = layerMode
    ? "Layer mode reads the configured Fluidd/Moonraker dashboard and captures exactly once when its current layer changes. The browser tab must remain open."
    : "Interval mode captures on a timer. The browser tab must remain open; longer jobs use more laptop/phone memory.";
}

function saveTimelapseOptions() {
  const options = timelapseOptions();
  $("#timelapse-mode").value = options.mode;
  $("#timelapse-interval").value = String(options.intervalSeconds);
  $("#timelapse-fps").value = String(options.playbackFps);
  $("#timelapse-max-frames").value = String(options.maxFrames);
  localStorage.setItem("timelapseOptions", JSON.stringify(options));
  updateTimelapseMode();
}

function updateTimelapseButton(cameraId) {
  const active = timelapses.get(cameraId);
  const button = grid.querySelector(`[data-camera-id="${CSS.escape(cameraId)}"] .timelapse`);
  if (!button) return;
  button.textContent = active ? `${active.finishing ? "Saving" : "Stop & save"} (${active.frames.length})` : "Timelapse";
  button.classList.toggle("recording", Boolean(active));
  button.disabled = Boolean(active?.finishing);
}

function captureTimelapseFrame(active) {
  if (active.captureBusy || active.finishing) return;
  active.captureBusy = true;
  let canvas;
  try { canvas = captureCanvas(active.image); }
  catch (_) { active.captureBusy = false; return; }
  active.width = canvas.width;
  active.height = canvas.height;
  canvas.toBlob(blob => {
    active.captureBusy = false;
    if (!blob || active.finishing) return;
    active.frames.push(blob);
    updateTimelapseButton(active.camera.id);
    if (active.frames.length >= active.options.maxFrames) {
      showToast(`Maximum of ${active.options.maxFrames} timelapse frames reached. Creating the video now…`);
      finishTimelapse(active.camera.id);
    }
  }, "image/jpeg", 0.88);
}

async function drawTimelapseFrame(blob, context, canvas) {
  if (typeof createImageBitmap === "function") {
    const bitmap = await createImageBitmap(blob);
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    return;
  }
  const url = URL.createObjectURL(blob);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
  } finally { URL.revokeObjectURL(url); }
}

async function finishTimelapse(cameraId) {
  const active = timelapses.get(cameraId);
  if (!active || active.finishing) return;
  active.finishing = true;
  clearInterval(active.timer);
  if (active.eventSource) active.eventSource.close();
  updateTimelapseButton(cameraId);
  while (active.captureBusy) await new Promise(resolve => setTimeout(resolve, 25));
  if (!active.frames.length) {
    timelapses.delete(cameraId);
    updateTimelapseButton(cameraId);
    return showToast("No timelapse frames were captured.");
  }
  if (typeof MediaRecorder === "undefined") {
    timelapses.delete(cameraId);
    updateTimelapseButton(cameraId);
    return showToast("Timelapse video creation is not supported by this browser.");
  }
  const canvas = document.createElement("canvas");
  canvas.width = active.width;
  canvas.height = active.height;
  const stream = canvas.captureStream(active.options.playbackFps);
  const format = supportedVideoFormat();
  let recorder;
  try { recorder = new MediaRecorder(stream, format.mimeType ? { mimeType: format.mimeType } : undefined); }
  catch (_) {
    stream.getTracks().forEach(track => track.stop());
    timelapses.delete(cameraId);
    updateTimelapseButton(cameraId);
    return showToast("This browser could not create the timelapse video.");
  }
  const chunks = [];
  recorder.addEventListener("dataavailable", event => { if (event.data.size) chunks.push(event.data); });
  const stopped = new Promise(resolve => recorder.addEventListener("stop", resolve, { once: true }));
  const started = new Promise(resolve => recorder.addEventListener("start", resolve, { once: true }));
  recorder.start();
  await started;
  const context = canvas.getContext("2d");
  const frameDelay = 1000 / active.options.playbackFps;
  const track = stream.getVideoTracks()[0];
  showToast(`Creating a ${active.frames.length}-frame timelapse on this viewing device…`);
  for (const frame of active.frames) {
    await drawTimelapseFrame(frame, context, canvas);
    if (typeof track?.requestFrame === "function") track.requestFrame();
    await new Promise(resolve => setTimeout(resolve, frameDelay));
  }
  await new Promise(resolve => setTimeout(resolve, Math.max(250, frameDelay)));
  try { recorder.requestData(); } catch (_) { /* stop flushes the final chunk */ }
  recorder.stop();
  await stopped;
  stream.getTracks().forEach(track => track.stop());
  timelapses.delete(cameraId);
  updateTimelapseButton(cameraId);
  if (!chunks.length) return showToast("The timelapse did not contain any video data.");
  const mimeType = recorder.mimeType || format.mimeType || "video/webm";
  const blob = new Blob(chunks, { type: mimeType });
  if (!blob.size) return showToast("Timelapse failed: the browser produced an empty video file.");
  const filename = `${safeFilename(active.camera.name)}-timelapse-${timestamp()}.${videoExtension(mimeType, format.extension)}`;
  downloadBlob(blob, filename);
  showToast(`Timelapse saved on this viewing device: ${filename} (${Math.max(1, Math.round(blob.size / 1024))} KB)`);
}

function startLayerEventStream(active) {
  const startPollingFallback = () => {
    if (!active.timer) active.timer = setInterval(() => updateLayerTimelapse(active), 1000);
  };
  if (typeof EventSource === "undefined") return startPollingFallback();
  const events = new EventSource("/api/printer/events");
  active.eventSource = events;
  events.addEventListener("message", event => {
    if (active.finishing || !timelapses.has(active.camera.id)) return;
    try { updateLayerTimelapse(active, JSON.parse(event.data)); }
    catch (_) { /* a later server event or fallback poll will retry */ }
  });
  events.addEventListener("open", () => {
    active.eventStreamConnected = true;
    if (active.timer) {
      clearInterval(active.timer);
      active.timer = null;
    }
  });
  events.addEventListener("error", () => {
    active.eventStreamConnected = false;
    startPollingFallback();
  });
}

async function initialPrinterStatus() {
  let lastError;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const status = await api("/api/printer/status");
      if (status.available) return status;
      lastError = new Error(status.error || "Printer layer status is unavailable.");
    } catch (error) { lastError = error; }
    if (attempt < 2) await new Promise(resolve => setTimeout(resolve, 750));
  }
  throw lastError || new Error("Printer layer status is unavailable.");
}

async function updateLayerTimelapse(active, suppliedStatus = null) {
  if (active.finishing || active.layerPollBusy) return;
  active.layerPollBusy = true;
  try {
    const status = suppliedStatus || await api("/api/printer/status");
    if (!status.available) {
      if (!active.statusWarningShown) showToast(status.error || "Printer layer status is unavailable.");
      active.statusWarningShown = true;
      return;
    }
    active.statusWarningShown = false;
    const stateName = String(status.state || "unknown").toLowerCase();
    if (stateName === "printing") {
      active.hasSeenPrinting = true;
      const layer = status.current_layer === null || status.current_layer === undefined
        ? null
        : Number(status.current_layer);
      const nativeLayer = Number.isInteger(layer) && layer >= 0 ? layer : null;
      const zHeight = status.z_height === null || status.z_height === undefined
        ? null
        : Number(status.z_height);
      const usableZ = Number.isFinite(zHeight) && zHeight >= 0 ? zHeight : null;
      if (nativeLayer === null && usableZ === null) {
        if (!active.layerWarningShown) {
          showToast("The printer is not reporting a layer number or Z height. Use interval timelapse for this firmware.");
          active.layerWarningShown = true;
        }
        return;
      }
      const newPrint = active.filename && status.filename && active.filename !== status.filename;
      const layerReset = nativeLayer !== null && active.lastLayer !== null && nativeLayer < active.lastLayer;
      if (newPrint || layerReset) {
        active.lastLayer = null;
        active.highestZ = null;
      }
      active.filename = status.filename || active.filename;
      if (nativeLayer !== null) {
        active.layerWarningShown = false;
        if ((active.lastLayer === null || nativeLayer > active.lastLayer) && !active.captureBusy) {
          active.lastLayer = nativeLayer;
          captureTimelapseFrame(active);
        }
      } else {
        if (!active.layerWarningShown) {
          showToast("Layer numbers are unavailable; timelapse is using each new maximum Z height as a layer change.");
          active.layerWarningShown = true;
        }
        if ((active.highestZ === null || usableZ > active.highestZ + 0.05) && !active.captureBusy) {
          active.highestZ = usableZ;
          captureTimelapseFrame(active);
        }
      }
      return;
    }
    if (active.hasSeenPrinting && ["complete", "cancelled", "error"].includes(stateName)) {
      showToast(`Print ${stateName}. Creating the layer timelapse now…`);
      await finishTimelapse(active.camera.id);
    }
  } catch (error) {
    if (!active.statusWarningShown) showToast(`Printer status: ${error.message}`);
    active.statusWarningShown = true;
  } finally {
    active.layerPollBusy = false;
  }
}

async function toggleTimelapse(camera, image) {
  if (timelapses.has(camera.id)) return finishTimelapse(camera.id);
  if (recordings.has(camera.id)) return showToast("Stop the normal recording before starting a timelapse for this camera.");
  if (typeof MediaRecorder === "undefined") return showToast("Timelapse video creation is not supported by this browser.");
  try { captureCanvas(image); } catch (error) { return showToast(error.message); }
  const options = timelapseOptions();
  let printerStatus = null;
  if (options.mode === "layer") {
    try { printerStatus = await initialPrinterStatus(); }
    catch (error) { return showToast(`Printer status: ${error.message}`); }
  }
  const active = {
    camera, image, options, frames: [], timer: null, captureBusy: false, finishing: false,
    width: 0, height: 0, layerPollBusy: false, lastLayer: null, highestZ: null, filename: "",
    hasSeenPrinting: false, statusWarningShown: false, layerWarningShown: false,
    eventSource: null, eventStreamConnected: false,
  };
  timelapses.set(camera.id, active);
  if (options.mode === "layer") {
    await updateLayerTimelapse(active, printerStatus);
    if (!timelapses.has(camera.id)) return;
    startLayerEventStream(active);
  } else {
    captureTimelapseFrame(active);
    active.timer = setInterval(() => captureTimelapseFrame(active), options.intervalSeconds * 1000);
  }
  updateTimelapseButton(camera.id);
  showToast(options.mode === "layer"
    ? "Layer timelapse armed: the Pi is watching layer changes continuously. Keep this page open."
    : `Timelapse started: one frame every ${options.intervalSeconds} seconds. Keep this page open.`);
}

function renderGrid() {
  grid.replaceChildren();
  const visible = state.cameras.filter(camera => camera.enabled && !state.hidden.has(camera.id));
  $("#empty-state").hidden = state.cameras.length !== 0;
  for (const camera of visible) {
    const card = $("#camera-card-template").content.firstElementChild.cloneNode(true);
    card.dataset.cameraId = camera.id;
    card.classList.add(camera.status?.state || "connecting");
    const image = card.querySelector(".stream");
    image.alt = `${camera.name} live camera stream`;
    image.src = streamUrl(camera);
    image.addEventListener("error", () => setTimeout(() => { image.src = streamUrl(camera); }, 2000));
    card.querySelector(".camera-title").textContent = camera.name;
    const kind = camera.source_type === "usb" ? "USB camera" : `${camera.source_type.toUpperCase()} stream`;
    card.querySelector(".camera-type").textContent = `${kind} · ${cameraVideoSummary(camera)}`;
    card.querySelector(".snapshot").addEventListener("click", () => saveScreenshot(camera, image));
    card.querySelector(".record").addEventListener("click", event => toggleRecording(camera, image, event.currentTarget));
    card.querySelector(".timelapse").addEventListener("click", () => toggleTimelapse(camera, image));
    card.querySelector(".fullscreen").addEventListener("click", () => card.requestFullscreen?.());
    grid.append(card);
    updateTimelapseButton(camera.id);
  }
  updateStatuses();
}

function updateStatuses() {
  for (const camera of state.cameras) {
    const card = grid.querySelector(`[data-camera-id="${CSS.escape(camera.id)}"]`);
    if (!card) continue;
    const status = camera.status || { state: "connecting", detail: "" };
    card.classList.remove("online", "disconnected", "connecting", "reconnecting", "disabled", "stopped");
    card.classList.add(status.state);
    const label = status.state === "online" ? "Live" : status.state;
    card.querySelector(".status-text").textContent = status.detail ? `${label}: ${status.detail}` : label;
  }
}

function renderSettings() {
  const list = $("#camera-list");
  list.replaceChildren();
  for (const camera of state.cameras) {
    const row = document.createElement("div");
    row.className = "setting-camera";
    const meta = document.createElement("div");
    const name = document.createElement("strong"); name.textContent = camera.name;
    const source = document.createElement("small"); source.textContent = camera.source || "Private stream URL configured";
    const video = document.createElement("small"); video.textContent = cameraVideoSummary(camera);
    meta.append(name, document.createElement("br"), source, document.createElement("br"), video);
    const toggle = document.createElement("input"); toggle.type = "checkbox"; toggle.checked = !state.hidden.has(camera.id); toggle.title = "Show in grid";
    toggle.addEventListener("change", () => { toggle.checked ? state.hidden.delete(camera.id) : state.hidden.add(camera.id); saveHidden(); renderGrid(); });
    const actions = document.createElement("div"); actions.className = "setting-actions";
    const edit = document.createElement("button"); edit.textContent = "Edit"; edit.addEventListener("click", () => openCameraDialog(camera));
    const remove = document.createElement("button"); remove.textContent = "Remove"; remove.className = "danger"; remove.addEventListener("click", () => removeCamera(camera));
    actions.append(edit, remove); row.append(meta, toggle, actions); list.append(row);
  }
  $("#camera-limit").textContent = `${state.cameras.length} of ${state.maxCameras} cameras configured`;
  const full = state.cameras.length >= state.maxCameras;
  $("#add-camera").disabled = full;
  $("#detect-usb").disabled = full;
  document.querySelectorAll("[data-add-camera]").forEach(button => { button.disabled = full; });
}

function renderAutostart(info) {
  state.autostart = info;
  $("#autostart-description").textContent = info.description;
  const repairNeeded = info.platform === "Linux" && info.enabled && !info.healthy;
  $("#autostart-status").textContent = repairNeeded
    ? `Repair required: Automatic is enabled for boot, but the service is ${info.service_state}. Run the terminal command below.`
    : `Current mode: ${info.enabled ? "Automatic" : "Manual"} (${info.status})`;
  $("#autostart-auto").textContent = repairNeeded ? "Automatic — repair needed" : "Automatic";
  $("#autostart-auto").classList.toggle("selected", info.enabled);
  $("#autostart-manual").classList.toggle("selected", !info.enabled);
  $("#autostart-auto").disabled = !info.supported || (info.enabled && !repairNeeded);
  $("#autostart-manual").disabled = !info.supported || !info.enabled;
  const showCommand = Boolean(info.command);
  $("#autostart-command-wrap").hidden = !showCommand;
  $("#autostart-command").textContent = info.command || "";
}

async function refreshAutostart() {
  try { renderAutostart(await api("/api/autostart")); }
  catch (error) { $("#autostart-status").textContent = error.message; }
}

async function changeAutostart(enabled) {
  const buttons = [$("#autostart-auto"), $("#autostart-manual")];
  buttons.forEach(button => { button.disabled = true; });
  $("#autostart-status").textContent = enabled ? "Enabling automatic startup…" : "Switching to manual startup…";
  try {
    const info = await api("/api/autostart", { method: "POST", body: JSON.stringify({ enabled }) });
    renderAutostart(info);
    showToast(info.changed ? info.message : (info.requires_admin ? "Run the displayed terminal command to finish this change." : info.message));
  } catch (error) {
    $("#autostart-status").textContent = error.message;
    if (state.autostart) renderAutostart(state.autostart);
  }
}

function renderNetwork(info) {
  state.network = info;
  $("#network-port").value = String(info.saved_port);
  $("#network-status").textContent = `Active port: ${info.active_port} · Saved preferred port: ${info.saved_port} · Bind address: ${info.bind_host}`;
  const urls = $("#network-urls");
  urls.replaceChildren();
  const groups = [
    ["Local", info.local_urls],
    ["LAN", info.lan_urls],
    ["Tailscale", info.tailscale_urls],
  ];
  for (const [label, values] of groups) {
    if (!values.length) {
      const item = document.createElement("li"); item.textContent = `${label}: not detected`; urls.append(item); continue;
    }
    for (const value of values) {
      const item = document.createElement("li");
      const link = document.createElement("a"); link.href = value; link.textContent = `${label}: ${value}`; link.target = "_blank"; link.rel = "noreferrer";
      item.append(link); urls.append(item);
    }
  }
  $("#network-restart").hidden = !info.restart_required;
  $("#network-restart").textContent = info.restart_required
    ? `Restart Multi Camera Printer Dashboard to begin using port ${info.saved_port}. This page remains available on port ${info.active_port} until then.`
    : "";
  if (info.message) showToast(info.message);
}

async function refreshNetwork() {
  try { renderNetwork(await api("/api/network")); }
  catch (error) { $("#network-status").textContent = error.message; }
}

async function saveNetwork(mode) {
  const buttons = [$("#network-save"), $("#network-auto")];
  buttons.forEach(button => { button.disabled = true; });
  try {
    const info = await api("/api/network", {
      method: "POST",
      body: JSON.stringify({ mode, port: $("#network-port").value }),
    });
    renderNetwork(info);
  } catch (error) { $("#network-status").textContent = error.message; }
  finally { buttons.forEach(button => { button.disabled = false; }); }
}

function renderPrinter(info) {
  state.printer = info;
  $("#printer-name").value = info.name || "3D Printer";
  $("#printer-url").value = info.url || "";
  $("#printer-api-url").value = info.api_url || "";
  $("#printer-title").textContent = info.name || "3D Printer";
  $("#printer-view-button").textContent = info.name || "3D printer";
  $("#printer-empty").hidden = info.configured;
  $("#printer-frame-wrap").hidden = !info.configured;
  const open = $("#printer-open");
  open.hidden = !info.configured;
  if (info.configured) {
    open.href = info.url;
    if ($("#printer-frame").src !== info.url) $("#printer-frame").src = info.url;
    $("#printer-status").textContent = "Printer dashboard configured. It is available after signing in to this app.";
  } else {
    open.removeAttribute("href");
    $("#printer-frame").removeAttribute("src");
    $("#printer-status").textContent = "No printer dashboard URL is configured.";
  }
}

async function refreshPrinter() {
  try { renderPrinter(await api("/api/printer")); }
  catch (error) { $("#printer-status").textContent = error.message; }
}

async function savePrinter(clear = false) {
  const button = $("#printer-save");
  button.disabled = true;
  try {
    const info = await api("/api/printer", {
      method: "POST",
      body: JSON.stringify({
        name: $("#printer-name").value || "3D Printer",
        url: clear ? "" : $("#printer-url").value,
        api_url: clear ? "" : $("#printer-api-url").value,
      }),
    });
    renderPrinter(info);
    showToast(clear ? "Printer dashboard removed from this app." : "Printer dashboard saved in private configuration.");
  } catch (error) { $("#printer-status").textContent = error.message; }
  finally { button.disabled = false; }
}

async function testPrinterConnection() {
  const button = $("#printer-test");
  button.disabled = true;
  $("#printer-status").textContent = "Testing Moonraker layer data…";
  try {
    const status = await api("/api/printer/status");
    if (!status.available) throw new Error(status.error || "Printer status is unavailable.");
    const detail = status.layer_source === "native"
      ? `native layer ${status.current_layer}${status.total_layer === null ? "" : ` of ${status.total_layer}`}`
      : status.layer_source === "z_height"
        ? `Z-height fallback at ${status.z_height} mm`
        : "no usable layer or Z-height data";
    $("#printer-status").textContent = `Connected. Printer state: ${status.state}; ${detail}.`;
  } catch (error) { $("#printer-status").textContent = error.message; }
  finally { button.disabled = false; }
}

function isAdmin() { return state.currentUser?.role === "admin"; }

function applyPermissions() {
  document.querySelectorAll("[data-admin-only]").forEach(element => { element.hidden = !isAdmin(); });
  $("#printer-configure").hidden = !isAdmin();
  $("#signed-in-user").textContent = `${state.currentUser?.username || "account"} (${state.currentUser?.role || "viewer"})`;
}

function renderUsers() {
  const list = $("#account-list");
  list.replaceChildren();
  for (const user of state.users) {
    const row = document.createElement("div"); row.className = "setting-account";
    const meta = document.createElement("div");
    const name = document.createElement("strong"); name.textContent = user.username;
    const detail = document.createElement("small");
    detail.textContent = `${user.role === "admin" ? "Administrator" : "Viewer"} · ${user.enabled ? "enabled" : "disabled"}${user.id === state.currentUser?.id ? " · current account" : ""}`;
    meta.append(name, document.createElement("br"), detail);
    const actions = document.createElement("div"); actions.className = "setting-actions";
    const edit = document.createElement("button"); edit.textContent = "Edit / reset password"; edit.addEventListener("click", () => openAccountDialog(user));
    const remove = document.createElement("button"); remove.textContent = "Remove"; remove.className = "danger"; remove.disabled = user.id === state.currentUser?.id; remove.addEventListener("click", () => removeUser(user));
    actions.append(edit, remove); row.append(meta, actions); list.append(row);
  }
  $("#account-limit").textContent = `${state.users.length} of ${state.maxUsers} accounts configured`;
  $("#account-add").disabled = state.users.length >= state.maxUsers;
}

async function refreshUsers() {
  if (!isAdmin()) return;
  try {
    const data = await api("/api/users");
    state.users = data.users;
    state.maxUsers = data.max_users || 10;
    renderUsers();
  } catch (error) { $("#account-status").textContent = error.message; }
}

function openAccountDialog(user = null) {
  if (!user && state.users.length >= state.maxUsers) return showToast(`This dashboard supports up to ${state.maxUsers} accounts.`);
  $("#account-form").reset();
  $("#account-form-error").textContent = "";
  $("#account-id").value = user?.id || "";
  $("#account-dialog-title").textContent = user ? "Edit account" : "Add account";
  $("#account-username").value = user?.username || "";
  $("#account-password").required = !user;
  $("#account-password-hint").textContent = user ? "leave blank to keep the current password" : "at least 8 characters";
  $("#account-role").value = user?.role || "viewer";
  $("#account-enabled").checked = user?.enabled ?? true;
  accountDialog.showModal();
}

async function saveUser(event) {
  event.preventDefault();
  const id = $("#account-id").value;
  const payload = {
    username: $("#account-username").value,
    password: $("#account-password").value,
    role: $("#account-role").value,
    enabled: $("#account-enabled").checked,
  };
  try {
    const saved = await api(id ? `/api/users/${id}` : "/api/users", { method: id ? "PUT" : "POST", body: JSON.stringify(payload) });
    if (saved.id === state.currentUser?.id) state.currentUser = saved;
    accountDialog.close();
    applyPermissions();
    await refreshUsers();
    showToast(id ? "Account updated." : "Account added.");
  } catch (error) { $("#account-form-error").textContent = error.message; }
}

async function removeUser(user) {
  if (!confirm(`Remove account “${user.username}”?`)) return;
  try {
    await api(`/api/users/${user.id}`, { method: "DELETE" });
    await refreshUsers();
    showToast("Account removed.");
  } catch (error) { $("#account-status").textContent = error.message; }
}

async function changeOwnPassword() {
  const currentPassword = $("#current-password").value;
  const newPassword = $("#new-password").value;
  const confirmation = $("#confirm-password").value;
  if (newPassword.length < 8) return $("#password-status").textContent = "The new password must contain at least 8 characters.";
  if (newPassword !== confirmation) return $("#password-status").textContent = "The two new-password entries do not match.";
  try {
    await api("/api/account/password", { method: "POST", body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) });
    $("#current-password").value = $("#new-password").value = $("#confirm-password").value = "";
    $("#password-status").textContent = "Password changed successfully.";
    showToast("Your password was changed.");
  } catch (error) { $("#password-status").textContent = error.message; }
}

function showView(view) {
  state.view = view;
  const cameras = view === "cameras";
  $("#camera-view").hidden = !cameras;
  $("#printer-view").hidden = cameras;
  $("#grid-control").hidden = !cameras;
  $("#add-camera").hidden = !cameras || !isAdmin();
  $("#cameras-view-button").classList.toggle("selected", cameras);
  $("#printer-view-button").classList.toggle("selected", !cameras);
  localStorage.setItem("dashboardView", view);
}

async function refreshCameras(force = false) {
  const next = await api("/api/cameras");
  const changed = force || cameraFingerprint(next) !== cameraFingerprint(state.cameras);
  state.cameras = next;
  if (changed) { renderGrid(); renderSettings(); } else updateStatuses();
}

function setDrawer(open) {
  $("#settings").classList.toggle("open", open);
  $("#settings").setAttribute("aria-hidden", String(!open));
  $("#settings-toggle").setAttribute("aria-expanded", String(open));
  $("#scrim").hidden = !open;
}

function updateTypeHelp() {
  const type = $("#camera-type").value;
  $("#credentials").hidden = type === "usb";
  $("#source-help").textContent = type === "usb"
    ? "Windows usually uses an index such as 0. Linux can use /dev/video0."
    : type === "rtsp"
      ? "Example: rtsp://192.168.1.50:554/stream1. Put credentials in the fields below."
      : "Use an HTTP/HTTPS MJPEG or OpenCV-compatible video stream URL.";
  $("#camera-source").placeholder = type === "usb" ? "0" : `${type}://camera-address/stream`;
}

function updateResolutionFields() {
  $("#custom-resolution").hidden = $("#camera-resolution").value !== "custom";
}

function openCameraDialog(camera = null) {
  if (!camera && state.cameras.length >= state.maxCameras) {
    return showToast(`This dashboard supports up to ${state.maxCameras} cameras. Remove one before adding another.`);
  }
  $("#camera-form").reset(); $("#form-error").textContent = "";
  $("#camera-id").value = camera?.id || "";
  $("#dialog-title").textContent = camera ? "Edit camera" : "Add camera";
  $("#camera-name").value = camera?.name || "";
  $("#camera-type").value = camera?.source_type || "usb";
  $("#camera-source").value = camera?.source || "";
  $("#camera-source").required = !camera;
  $("#camera-source").placeholder = camera?.source_configured ? "Leave blank to keep the saved private URL" : "0";
  $("#camera-enabled").checked = camera?.enabled ?? true;
  const resolution = camera?.target_width && camera?.target_height ? `${camera.target_width}x${camera.target_height}` : "0x0";
  const resolutionOption = [...$("#camera-resolution").options].some(option => option.value === resolution);
  $("#camera-resolution").value = resolutionOption ? resolution : "custom";
  $("#camera-width").value = camera?.target_width || 1280;
  $("#camera-height").value = camera?.target_height || 720;
  $("#camera-fps").value = camera?.target_fps ?? 0;
  $("#camera-rotation").value = String(camera?.rotation ?? 0);
  $("#camera-flip").value = camera?.flip || "none";
  updateTypeHelp(); updateResolutionFields(); dialog.showModal();
}

async function saveCamera(event) {
  event.preventDefault();
  const id = $("#camera-id").value;
  const clear = $("#clear-credentials").checked;
  const selectedResolution = $("#camera-resolution").value;
  const [width, height] = selectedResolution === "custom"
    ? [Number($("#camera-width").value), Number($("#camera-height").value)]
    : selectedResolution.split("x").map(Number);
  const payload = {
    name: $("#camera-name").value,
    source_type: $("#camera-type").value,
    source: $("#camera-source").value,
    username: $("#camera-username").value,
    password: $("#camera-password").value,
    enabled: $("#camera-enabled").checked,
    target_width: width,
    target_height: height,
    target_fps: Number($("#camera-fps").value),
    rotation: Number($("#camera-rotation").value),
    flip: $("#camera-flip").value,
    clear_username: clear,
    clear_password: clear,
  };
  try {
    await api(id ? `/api/cameras/${id}` : "/api/cameras", { method: id ? "PUT" : "POST", body: JSON.stringify(payload) });
    dialog.close(); await refreshCameras(true);
  } catch (error) { $("#form-error").textContent = error.message; }
}

async function removeCamera(camera) {
  if (!confirm(`Remove “${camera.name}”? This removes its locally saved settings.`)) return;
  if (timelapses.has(camera.id)) await finishTimelapse(camera.id);
  if (recordings.has(camera.id)) finishRecording(camera.id);
  await api(`/api/cameras/${camera.id}`, { method: "DELETE" });
  state.hidden.delete(camera.id); saveHidden(); await refreshCameras(true);
}

async function detectUsb() {
  if (state.cameras.length >= state.maxCameras) return showToast(`The ${state.maxCameras}-camera limit has been reached.`);
  const button = $("#detect-usb"); button.disabled = true; button.textContent = "Detecting…";
  const target = $("#detected-list"); target.replaceChildren();
  try {
    const cameras = await api("/api/detect-usb");
    if (!cameras.length) { target.textContent = "No available USB camera was detected."; return; }
    for (const detected of cameras) {
      const row = document.createElement("div"); row.className = "detected-camera";
      const text = document.createElement("div"); const title = document.createElement("strong"); title.textContent = detected.name; const source = document.createElement("small"); source.textContent = detected.source; text.append(title, document.createElement("br"), source);
      const add = document.createElement("button"); add.textContent = "Add"; add.addEventListener("click", () => { openCameraDialog(); $("#camera-name").value = detected.name; $("#camera-source").value = detected.source; });
      row.append(text, add); target.append(row);
    }
  } catch (error) { target.textContent = error.message; }
  finally { button.disabled = false; button.textContent = "Detect USB cameras"; }
}

async function initialise() {
  try {
    const session = await api("/api/session");
    state.csrf = session.csrf_token;
    state.maxCameras = session.max_cameras || 6;
    state.maxUsers = session.max_users || 10;
    state.currentUser = session.user;
    $("#version").textContent = `v${session.version}`;
    applyPermissions();
    const savedColumns = Number(localStorage.getItem("gridColumns") || 2); $("#grid-size").value = String(savedColumns); document.documentElement.style.setProperty("--grid-columns", savedColumns); $("#grid-output").textContent = `${savedColumns} column${savedColumns === 1 ? "" : "s"}`;
    let savedTimelapse = {};
    try { savedTimelapse = JSON.parse(localStorage.getItem("timelapseOptions") || "{}"); } catch (_) { /* keep defaults */ }
    $("#timelapse-mode").value = savedTimelapse.mode === "interval" ? "interval" : "layer";
    $("#timelapse-interval").value = String(savedTimelapse.intervalSeconds || 10);
    $("#timelapse-fps").value = String(savedTimelapse.playbackFps || 30);
    $("#timelapse-max-frames").value = String(savedTimelapse.maxFrames || 3000);
    saveTimelapseOptions();
    const startupTasks = [refreshCameras(true), refreshAutostart(), refreshNetwork(), refreshPrinter()];
    if (isAdmin()) startupTasks.push(refreshUsers());
    await Promise.all(startupTasks);
    showView(localStorage.getItem("dashboardView") === "printer" ? "printer" : "cameras");
    setInterval(() => refreshCameras().catch(console.error), 2500);
  } catch (error) { console.error(error); }
}

$("#grid-size").addEventListener("input", (event) => { const columns = event.target.value; document.documentElement.style.setProperty("--grid-columns", columns); $("#grid-output").textContent = `${columns} column${columns === "1" ? "" : "s"}`; localStorage.setItem("gridColumns", columns); });
$("#cameras-view-button").addEventListener("click", () => showView("cameras")); $("#printer-view-button").addEventListener("click", () => showView("printer"));
$("#settings-toggle").addEventListener("click", () => setDrawer(true)); $("#settings-close").addEventListener("click", () => setDrawer(false)); $("#scrim").addEventListener("click", () => setDrawer(false));
$("#add-camera").addEventListener("click", () => openCameraDialog()); document.querySelectorAll("[data-add-camera]").forEach(button => button.addEventListener("click", () => openCameraDialog()));
$("#camera-type").addEventListener("change", updateTypeHelp); $("#camera-resolution").addEventListener("change", updateResolutionFields); $("#camera-form").addEventListener("submit", saveCamera); $("#dialog-close").addEventListener("click", () => dialog.close()); $("#dialog-cancel").addEventListener("click", () => dialog.close());
$("#autostart-auto").addEventListener("click", () => changeAutostart(true)); $("#autostart-manual").addEventListener("click", () => changeAutostart(false)); $("#autostart-refresh").addEventListener("click", refreshAutostart);
$("#network-save").addEventListener("click", () => saveNetwork("custom")); $("#network-auto").addEventListener("click", () => saveNetwork("automatic")); $("#network-refresh").addEventListener("click", refreshNetwork);
for (const control of [$("#timelapse-mode"), $("#timelapse-interval"), $("#timelapse-fps"), $("#timelapse-max-frames")]) control.addEventListener("change", saveTimelapseOptions);
$("#printer-save").addEventListener("click", () => savePrinter(false)); $("#printer-clear").addEventListener("click", () => savePrinter(true));
$("#printer-test").addEventListener("click", testPrinterConnection);
$("#printer-configure").addEventListener("click", () => setDrawer(true));
$("#printer-reload").addEventListener("click", () => { if (state.printer?.url) $("#printer-frame").src = state.printer.url; });
$("#account-add").addEventListener("click", () => openAccountDialog());
$("#account-form").addEventListener("submit", saveUser);
$("#account-dialog-close").addEventListener("click", () => accountDialog.close());
$("#account-dialog-cancel").addEventListener("click", () => accountDialog.close());
$("#change-password").addEventListener("click", changeOwnPassword);
$("#detect-usb").addEventListener("click", detectUsb); $("#logout").addEventListener("click", async () => { await api("/logout", { method: "POST" }); location.href = "/login"; });
window.addEventListener("beforeunload", event => {
  if (!recordings.size && !timelapses.size) return;
  event.preventDefault();
  event.returnValue = "";
});
initialise();
