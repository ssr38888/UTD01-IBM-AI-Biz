// FallWatch phone page: camera -> pose (on the phone) -> fall detector -> evidence upload.
import { FallDetector, POSTURE, CAMERA_DETECTION_OPTIONS, classifyPosture } from "./detector.js?v=30";
import { FrameBuffer, KeypointLog, selectEventFrames, compactLandmarks } from "./evidence.js?v=4";

// Pinned MediaPipe build + pose model. To run without internet access to these
// CDNs, run deploy/vendor-mediapipe.sh and change these to "./vendor/...".
const MP_BUNDLE = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs";
const MP_WASM = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm";
const POSE_MODEL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task";

const ANALYZE_EVERY_MS = 100;   // frequent checks for the 0.3-second horizontal trigger
const APP_VERSION = "fallwatch-capture/0.11";

const $ = (id) => document.getElementById(id);
let lastSavedReceipt = null;
window.addEventListener('message', event => {
  if (event.origin !== location.origin || event.source !== window.parent ||
      event.data?.type !== 'fallwatch:ledger-updated' || event.data.eventId !== lastSavedReceipt?.id) return;
  setMsg('liveMsg', `Event #${lastSavedReceipt.seq} saved and visible in the ledger.`);
});
const store = {
  get: (k, d = "") => { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  del: (k) => { try { localStorage.removeItem(k); } catch { /* private mode */ } },
};

// ----------------------------------------------------------------- setup screen
const cameraDeviceId = store.get("fw.deviceId", `camera-${crypto.randomUUID().slice(0, 6)}`).trim().slice(0, 64) || `camera-${crypto.randomUUID().slice(0, 6)}`;
store.set("fw.deviceId", cameraDeviceId);
store.del("fw.deviceKey");
const refreshStart = () => { $("startBtn").disabled = running; };
$("startBtn").addEventListener("click", () => start().catch((e) => {
  console.error(e);
  setMsg("setupMsg", e.message || String(e), true);
}));
$("stopBtn").addEventListener("click", () => stop().catch((error) => setMsg("liveMsg", error.message, true)));

$("torchBtn").addEventListener('click',async()=>{
  const button=$("torchBtn"),track=stream?.getVideoTracks()[0];
  const enabled=button.getAttribute('aria-pressed')!=='true';
  button.disabled=true;
  try {
    await track.applyConstraints({advanced:[{torch:enabled}]});
    button.setAttribute('aria-pressed',String(enabled));
    button.textContent=enabled?'Torch off':'Torch on';
  } catch { setMsg('liveMsg','Torch could not be changed on this camera.',true); }
  finally { button.disabled=false; }
});


function setMsg(id, text, err = false) {
  $(id).textContent = text;
  $(id).classList.toggle("err", err);
}

// ----------------------------------------------------------------- state
let cfg, landmarker, presenceLandmarker, detector, frames, kplog, stream, wakeLock;
let running = false, analyzeTimer = 0;
let lastSnapT = 0, lastStandingSnap = null, prevPosture = null, prevPhase = "watching";
let lastSavedEventT = null;
let pendingEvents = [];         // confirmed falls still collecting "after" pictures
const grab = document.createElement("canvas");
const standingCanvas = document.createElement("canvas");

const deviceId = () => cameraDeviceId;
const api = async (path, opts = {}) => {
  let response = await fetch(path, { ...opts, credentials: "same-origin" });
  if (response.status === 401) {
    const session = await fetch("/api/camera/session", { method: "POST", credentials: "same-origin" });
    if (!session.ok) throw new Error("Could not connect this camera. Reload and try again.");
    response = await fetch(path, { ...opts, credentials: "same-origin" });
  }
  return response;
};

// ----------------------------------------------------------------- start / stop
async function start() {
  $("startBtn").disabled = true;
  try {
    store.set("fw.deviceId", deviceId());
    setMsg("setupMsg", "Connecting to server…");

    const r = await api("/api/config");
    if (r.status === 401) throw new Error("Could not connect this camera. Reload and try again.");
    if (!r.ok) throw new Error(`Server error ${r.status}`);
    cfg = await r.json();

    setMsg("setupMsg", "Loading pose model…");
    const { PoseLandmarker, FilesetResolver } = await import(MP_BUNDLE);
    const fileset = await FilesetResolver.forVisionTasks(MP_WASM);
    const opts = (delegate, runningMode = "VIDEO") => ({
      baseOptions: { modelAssetPath: POSE_MODEL, delegate },
      runningMode, numPoses: 1,
      minPoseDetectionConfidence: 0.5, minPosePresenceConfidence: 0.5, minTrackingConfidence: 0.5,
    });
    try { landmarker = await PoseLandmarker.createFromOptions(fileset, opts("GPU")); }
    catch { landmarker = await PoseLandmarker.createFromOptions(fileset, opts("CPU")); }
    // VIDEO mode can keep a plausible skeleton after its person leaves.
    // IMAGE mode forces a fresh full-frame person detection, without that ROI.
    try { presenceLandmarker = await PoseLandmarker.createFromOptions(fileset, opts("GPU", "IMAGE")); }
    catch { presenceLandmarker = await PoseLandmarker.createFromOptions(fileset, opts("CPU", "IMAGE")); }

    setMsg("setupMsg", "Starting camera…");
    const video = $("video");
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("Camera needs HTTPS. Open this page over https://");
    }
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    });
    video.srcObject = stream;
    const cameraTrack = stream.getVideoTracks()[0];
    $("torchBtn").hidden = !cameraTrack?.getCapabilities?.().torch;
    $("torchBtn").setAttribute('aria-pressed','false');
    $("torchBtn").textContent = 'Torch on';
    await video.play();

    detector = new FallDetector({...CAMERA_DETECTION_OPTIONS, otherConfirmsFall:true});
    lastSnapT = 0; lastStandingSnap = null; prevPosture = null; prevPhase = "watching";
    standingCanvas.dataset.t = "";
    const stage = document.querySelector(".stage");
    stage.style.aspectRatio = `${video.videoWidth} / ${video.videoHeight}`;
    // Hold enough pictures to cover PRE_EVENT before the person was last standing,
    // plus the descent and the confirmation wait.
    frames = new FrameBuffer(cfg.pre_event_ms + 6000);
    kplog = new KeypointLog(cfg.pre_event_ms + cfg.post_event_ms + 10000);
    pendingEvents = [];
    running = true;

    try { wakeLock = await navigator.wakeLock?.request("screen"); } catch { /* optional */ }
    $("setup").hidden = true;
    $("live").hidden = false;
    setMsg("setupMsg", "");

    analyzeTimer = setInterval(tick, ANALYZE_EVERY_MS);
    flushQueue();
  } catch (error) {
    stream?.getTracks().forEach((track) => track.stop());
    landmarker?.close?.();
    presenceLandmarker?.close?.();
    throw error;
  } finally { refreshStart(); }
}

async function stop() {
  running = false;
  clearInterval(analyzeTimer);
  $("stopBtn").disabled = true;
  while (ticking) await new Promise((resolve) => setTimeout(resolve, 10));
  // Save confirmed events even if monitoring stops before the post-event timer.
  const remaining = pendingEvents;
  pendingEvents = [];
  for (const event of remaining) await packageEvent(event);
  stream?.getTracks().forEach((t) => t.stop());
  stream = null;
  wakeLock?.release?.();
  landmarker?.close?.();
  presenceLandmarker?.close?.();
  $("video").srcObject = null;
  $("live").hidden = true;
  $("setup").hidden = false;
  discardBuffer();
  $("stopBtn").disabled = false;
  refreshStart();
}

function discardBuffer() {
  if (frames) { frames.frames = []; frames.totalBytes = 0; }
  if (kplog) kplog.rows = [];
  grab.width = grab.height = standingCanvas.width = standingCanvas.height = 1;
}

document.addEventListener("visibilitychange", async () => {
  if (running && document.visibilityState === "visible") {
    try { wakeLock = await navigator.wakeLock?.request("screen"); } catch { /* optional */ }
  }
});

// ----------------------------------------------------------------- main loop
let ticking = false;
async function tick() {
  if (ticking) return; // previous tick still encoding a picture
  ticking = true;
  try { await tickInner(); } catch (e) {
    console.error(e);
    setBanner("error", "Detection interrupted");
    setMsg("liveMsg", "Pose detection stopped responding. Stop and restart the camera.", true);
  } finally { ticking = false; }
}

async function tickInner() {
  const video = $("video");
  if (!running || video.readyState < 2 || video.paused && !video.ended) return;
  if (video.ended) {
    const remaining = pendingEvents;
    pendingEvents = [];
    for (const event of remaining) await packageEvent(event);
    discardBuffer();
    setBanner("watching", "Video file finished");
    return;
  }

  const now = Date.now();
  const res = landmarker.detectForVideo(video, performance.now());
  let lms = res.landmarks?.[0] || null;
  const aspect = video.videoWidth / video.videoHeight || 4 / 3;
  const potential = classifyPosture(lms, aspect, detector.o);
  if (!detector.active && (potential.posture === POSTURE.OTHER || potential.posture === POSTURE.LYING &&
      (detector.candidate || detector.lastStand && now - detector.lastStand.t <= detector.o.fallWindowMs))) {
    const fresh = presenceLandmarker.detect(video);
    lms = fresh.landmarks?.[0] || null;
    // A miss cannot confirm a fall. Keep the detector's short dropout grace
    // so one blurred frame does not erase an otherwise observed transition.
  }
  const r = detector.update(now, lms, aspect);

  kplog.add({ t: now, p: r.posture, a: round1(r.metrics.torsoDeg), h: round3(r.metrics.hipY),
              ba: round1(r.metrics.bodyAxisDeg), hb: r.metrics.horizontalBasis,
              k: compactLandmarks(lms) });

  // Keep an un-encoded copy of the latest standing view (cheap): becomes key frame 1.
  if (r.posture === POSTURE.STANDING) {
    drawTo(standingCanvas, video);
    standingCanvas.dataset.t = String(now);
  } else if (prevPosture === POSTURE.STANDING && standingCanvas.dataset.t) {
    // Just left standing: save that last standing picture now.
    const t = +standingCanvas.dataset.t;
    lastStandingSnap = t;
    await saveCanvas(standingCanvas, t, ["standKey"]);
  }

  // First lying frame of a possible fall: take key frame 2 immediately.
  if (r.event || r.posture === POSTURE.LYING && prevPosture !== POSTURE.LYING) {
    await snap(now, ["lieKey"]);
  }

  // Regular pictures: every SNAPSHOT_INTERVAL, faster while someone is going down.
  const goingDown = r.phase === "candidate" ||
    (r.phase !== "down" && r.posture !== POSTURE.STANDING && lastStandingSnap && now - lastStandingSnap < 3000);
  const interval = goingDown ? cfg.burst_interval_ms : cfg.snapshot_interval_ms;
  if (now - lastSnapT >= interval) await snap(now, []);

  if (r.event) {
    pendingEvents.push({ ev: r.event, clientId: crypto.randomUUID(), sendAt: r.event.lieT + cfg.post_event_ms });
  }

  // Finished collecting "after" pictures? Package and queue the event.
  for (const p of pendingEvents.filter((p) => now >= p.sendAt)) {
    pendingEvents = pendingEvents.filter((x) => x !== p);
    await packageEvent(p);
  }

  const holdFrom = pendingEvents.length
    ? Math.min(...pendingEvents.map((p) => p.ev.standT - cfg.pre_event_ms)) : Infinity;
  frames.prune(now, holdFrom);

  prevPosture = r.posture;
  prevPhase = r.phase;
  drawOverlay(lms, r);
  updateStats(r);
}

function drawTo(canvas, video) {
  const w = Math.min(cfg.frame_width, video.videoWidth || cfg.frame_width);
  const h = Math.round(w * (video.videoHeight / video.videoWidth || 0.75));
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  canvas.getContext("2d").drawImage(video, 0, 0, w, h);
}

async function snap(t, flags) {
  drawTo(grab, $("video"));
  await saveCanvas(grab, t, flags);
}

async function saveCanvas(canvas, t, flags) {
  const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", cfg.jpeg_quality));
  if (!blob) return;
  frames.add({ t, blob, bytes: blob.size, flags });
  frames.frames.sort((a, b) => a.t - b.t);
  if (!flags.includes("standKey")) lastSnapT = t;
}

// ----------------------------------------------------------------- packaging + upload queue
async function packageEvent({ ev, clientId }, test = false) {
  const picked = selectEventFrames(frames.frames, ev, {
    preMs: cfg.pre_event_ms, postMs: Math.max(0, ev.lieT + cfg.post_event_ms - ev.confirmedT), maxImages: cfg.max_images,
  });
  if (!picked.length) return;
  // Floor time keeps counting while the person is still down at upload time.
  const live = detector.events.find((e) => e.confirmedT === ev.confirmedT) || ev;
  const descriptor = { ...live };
  const meta = {
    device_id: deviceId(),
    client_event_id: clientId,
    detected_at_ms: ev.confirmedT,
    uploaded_at_ms: Date.now(),
    app_version: APP_VERSION,
    test,
    settings: { snapshot_interval_ms: cfg.snapshot_interval_ms, burst_interval_ms: cfg.burst_interval_ms,
                pre_event_ms: cfg.pre_event_ms, post_event_ms: cfg.post_event_ms, analyze_every_ms: ANALYZE_EVERY_MS },
    descriptor,
    frames: picked.map(({ name, t, role }) => ({ name, t, role })),
  };
  const kp = kplog.between(ev.standT - cfg.pre_event_ms, ev.lieT + cfg.post_event_ms);
  await queue.put({
    id: clientId, meta,
    images: picked.map(({ name, blob }) => ({ name, blob })),
    keypoints: new Blob([JSON.stringify(kp)], { type: "application/json" }),
  });
  setBanner("down", "Fall captured · saving event…");
  flushQueue();
}

let flushing = false, backoffMs = 2000;
async function flushQueue() {
  if (flushing) return;
  flushing = true;
  try {
    for (const item of await queue.all()) {
      const fd = new FormData();
      fd.append("meta", JSON.stringify(item.meta));
      for (const im of item.images) fd.append("images", im.blob, im.name);
      fd.append("keypoints", item.keypoints, "keypoints.json");
      let r;
      try { r = await api("/api/events", { method: "POST", body: fd }); }
      catch { throw new Error("offline"); }
      if (r.ok) {
        const receipt = await r.json();
        if (typeof receipt.id !== 'string' || !Number.isInteger(receipt.seq)) throw new Error('invalid save receipt');
        await queue.del(item.id);
        lastSavedReceipt = receipt;
        lastSavedEventT = item.meta.detected_at_ms;
        setMsg("liveMsg", `Event #${receipt.seq} saved. ${window.parent !== window ? 'Syncing ledger…' : 'Open Logs to review the evidence.'}`);
        const savedMessage = {type:"fallwatch:event-saved", eventId:receipt.id, seq:receipt.seq};
        window.dispatchEvent(new CustomEvent("fallwatch:event-saved", {detail:savedMessage}));
        if (window.parent !== window) window.parent.postMessage(savedMessage,location.origin);
        backoffMs = 2000;
        setBanner("sent", "Event saved");
        setTimeout(() => setBanner(prevPhase, null), 6000);
      } else if (r.status >= 400 && r.status < 500 && r.status !== 408 && r.status !== 429) {
        // The server will never accept this one; keep it on the phone, flag it.
        setMsg("liveMsg", `Server rejected an event (${r.status}). It stays on this phone.`, true);
        item.rejected = r.status;
        await queue.put(item);
      } else {
        throw new Error(`server ${r.status}`);
      }
    }
  } catch (e) {
    setMsg("liveMsg", `Upload paused (${e.message}); retrying in ${Math.round(backoffMs / 1000)} s`, true);
    setTimeout(flushQueue, backoffMs);
    backoffMs = Math.min(backoffMs * 2, 60000);
  } finally {
    flushing = false;
    // Upload diagnostics belong on the future log page.
  }
}

// IndexedDB queue so events survive a dropped connection or a page reload.
const queue = (() => {
  let dbp;
  const db = () => dbp ??= new Promise((res, rej) => {
    const r = indexedDB.open("fallwatch", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("outbox", { keyPath: "id" });
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
  const tx = async (mode, fn) => {
    const d = await db();
    return new Promise((res, rej) => {
      const t = d.transaction("outbox", mode);
      const out = fn(t.objectStore("outbox"));
      t.oncomplete = () => res(out?.result);
      t.onerror = () => rej(t.error);
    });
  };
  return {
    put: (item) => tx("readwrite", (s) => s.put(item)),
    del: (id) => tx("readwrite", (s) => s.delete(id)),
    all: async () => ((await tx("readonly", (s) => s.getAll())) || []).filter((x) => !x.rejected),
  };
})();

// ----------------------------------------------------------------- UI
const BONES = [[11, 12], [11, 23], [12, 24], [23, 24], [23, 25], [25, 27], [24, 26], [26, 28],
               [11, 13], [13, 15], [12, 14], [14, 16]];

function drawOverlay(lms, r) {
  const c = $("overlay"), v = $("video");
  const rect = c.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  if (c.width !== Math.round(rect.width * dpr)) { c.width = Math.round(rect.width * dpr); c.height = Math.round(rect.height * dpr); }
  const g = c.getContext("2d");
  g.clearRect(0, 0, c.width, c.height);
  if (!lms || !v.videoWidth) return;
  // Match object-fit: contain.
  const s = Math.min(c.width / v.videoWidth, c.height / v.videoHeight);
  const w = v.videoWidth * s, h = v.videoHeight * s, ox = (c.width - w) / 2, oy = (c.height - h) / 2;
  const P = (i) => [ox + lms[i].x * w, oy + lms[i].y * h];
  g.lineWidth = 4 * dpr;
  g.strokeStyle = r.phase === "down" ? "#ef4b4b" : r.phase === "candidate" ? "#f2a33a"
    : r.posture === POSTURE.STANDING ? "#2fbf71" : "#4f8cff";
  for (const [a, b] of BONES) {
    if ((lms[a].visibility ?? 1) < 0.5 || (lms[b].visibility ?? 1) < 0.5) continue;
    g.beginPath(); g.moveTo(...P(a)); g.lineTo(...P(b)); g.stroke();
  }
}

function setBanner(phase, text) {
  const b = $("banner");
  b.className = "banner " + (phase || "watching");
  b.textContent = text ?? ({ watching: "Watching", candidate: "Possible fall…", down: "Fall detected · person is down" }[phase] || "Watching");
}

function updateStats(r) {
  $("sPosture").textContent = r.posture;
  if ($("banner").classList.contains("sent")) return;
  const label = r.eventType === "suspected_fall" ? "Suspected fall detected" : r.eventType === "person_down" ? "Person down detected" : "Fall detected";
  if (r.phase === "down") {
    const pending = pendingEvents.find((event) => event.ev.confirmedT === r.confirmedT);
    if (pending) {
      const seconds = Math.max(0, Math.ceil((pending.sendAt - Date.now()) / 1000));
      setBanner("down", `${label} · collecting evidence · ${seconds}s`);
    } else {
      setBanner("down", lastSavedEventT === r.confirmedT ? `${label} · event saved` : `${label} · saving event…`);
    }
  } else if (r.phase === "candidate") {
    setBanner("candidate", "Possible fall · checking transition…");
  } else if (r.posture === POSTURE.LYING && r.downForMs !== null) {
    const seconds = Math.max(0, Math.ceil((CAMERA_DETECTION_OPTIONS.confirmMs - r.downForMs) / 1000));
    setBanner("candidate", `Person lying down · checking · ${seconds}s`);
  } else if (r.posture === POSTURE.UNKNOWN) {
    setBanner("watching", "No clear person detected");
  } else {
    setBanner("watching", r.posture === POSTURE.LYING ? "No fall transition observed" : "Watching");
  }
}

const round1 = (x) => (x === undefined ? null : Math.round(x * 10) / 10);
const round3 = (x) => (x === undefined ? null : Math.round(x * 1000) / 1000);

refreshStart();
