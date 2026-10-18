import { localDayRange, summarizeDay, formatBytes, isoTimestamp } from "./log-data.js?v=6";

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const time = (ms) => isoTimestamp(ms) ? new Date(ms).toLocaleString([], { dateStyle: "medium", timeStyle: "medium" }) : "Time unavailable";
const shortTime = (ms) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
let current = null, events = [], photoIndex = 0, selection = 0, loading = false;
let eventRevision = null;
let historyScope = "all", historyCursor = null, historyTotal = 0, historyDay = null;
let historyBusy = false, historyRefreshPending = false;
let chartData = null;
const urls = new Map();
let wantedMedia = new Set();


function message(text, error = false) {
  $("msg").textContent = text;
  $("msg").classList.toggle("err", error);
}
async function sessionFetch(path) {
  let response = await fetch(path, { credentials: "same-origin", cache: "no-store" });
  if (response.status === 401) {
    const session = await fetch("/api/camera/session", { method: "POST", credentials: "same-origin" });
    if (!session.ok) throw new Error("Could not open logs. Refresh to try again.");
    response = await fetch(path, { credentials: "same-origin", cache: "no-store" });
  }
  return response;
}
async function api(path) {
  const response = await sessionFetch(path);
  if (!response.ok) throw new Error(`Could not load evidence (${response.status}).`);
  return response.json();
}
function releaseMedia() {
  for (const url of urls.values()) URL.revokeObjectURL(url);
  urls.clear();
  wantedMedia.clear();
}
async function mediaUrl(name, ticket) {
  if (urls.has(name)) return urls.get(name);
  const id = current.id;
  const response = await sessionFetch(`/api/events/${encodeURIComponent(id)}/files/${encodeURIComponent(name)}`);
  if (!response.ok) throw new Error(`Evidence file unavailable (${response.status}).`);
  const blob = await response.blob();
  if (selection !== ticket || !wantedMedia.has(name)) return null;
  if (!urls.has(name)) urls.set(name, URL.createObjectURL(blob));
  return urls.get(name);
}
function showDate(date = new Date()) {
  $("todayLabel").textContent = date.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", year: "numeric" });
}
function renderChart(hours, count) {
  chartData = { hours, count };
  const w = Math.max(260, Math.min(720, $("dailyChart").clientWidth));
  const h = 230, left = 30, right = 14, top = 18, bottom = 34;
  const hourLabels = w < 420 ? [0, 6, 12, 18, 23] : [0, 4, 8, 12, 16, 20, 23];
  const max = Math.max(2, ...hours);
  const x = (hour) => left + hour / 23 * (w - left - right);
  const y = (value) => h - bottom - value / max * (h - top - bottom);
  const points = hours.map((value, hour) => `${x(hour)},${y(value)}`).join(" ");
  const ticks = [...new Set([0, Math.ceil(max / 2), max])];
  $("dailyChart").innerHTML = `<svg viewBox="0 0 ${w} ${h}" role="img" aria-labelledby="chartTitle chartDesc">
    <title id="chartTitle">Today's events by local hour</title><desc id="chartDesc">${count} events today. ${hours.map((n, i) => `${i}:00: ${n}`).join("; ")}</desc>
    ${ticks.map((tick) => `<line class="grid" x1="${left}" y1="${y(tick)}" x2="${w-right}" y2="${y(tick)}"/><text x="${left-10}" y="${y(tick)+4}" text-anchor="end">${tick}</text>`).join("")}
    ${hourLabels.map((hour) => `<text x="${x(hour)}" y="${h-10}" text-anchor="middle">${String(hour).padStart(2,"0")}:00</text>`).join("")}
    <polyline class="series" points="${points}"/>
    ${hours.map((n, hour) => `<circle class="point" cx="${x(hour)}" cy="${y(n)}" r="${n ? 4 : 2}"><title>${String(hour).padStart(2,"0")}:00–${String(hour).padStart(2,"0")}:59 · ${n} event${n === 1 ? "" : "s"}</title></circle>`).join("")}
    </svg>`;
  $("chartNote").textContent = count ? "Events per hour · local time · hover a point for its count." : "No events captured today. The chart updates when new evidence arrives.";
}
function emptyGallery() {
  selection++;
  current = null;
  releaseMedia();
  $("gallery").className = "gallery-empty";
  $("gallery").innerHTML = '<div class="empty-symbol">▧</div><h3>No saved events in this view</h3><p class="muted">Photos will appear here when the camera saves an event.</p>';
  $("eventSelect").innerHTML = '<option>No events in this view</option>';
  $("eventSelect").disabled = true;
  $("eventTime").textContent = "—";
  $("eventTime").removeAttribute("datetime");
  $("receivedTime").textContent = "No event selected.";
  $("eventHash").textContent = "—";
  $("selectedStorage").textContent = "No event selected.";
  $("verifyBtn").disabled = true;
  $("verifyOut").textContent = "";
}
async function loadList() {
  if (loading) return;
  loading = true;
  $("refresh").disabled = true;
  const range = localDayRange();
  try {
    const data = await api(`/api/logs?start_ms=${range.start}&end_ms=${range.end}`);
    showDate();
    const summary = summarizeDay(data.events, range.start, range.end);
    $("eventCount").textContent = String(summary.count);
    $("storageUsed").textContent = formatBytes(summary.totalBytes);
    $("syncStatus").textContent = `Updated ${shortTime(Date.now())}`;
    renderChart(summary.hours, summary.count);
    message(`${summary.count} event${summary.count === 1 ? "" : "s"} today · saved history below · refreshes every 30 seconds`);
    await loadHistory();
  } catch (error) {
    $("syncStatus").textContent = "Refresh paused";
    message(error.message, true);
  } finally {
    loading = false;
    $("refresh").disabled = false;
  }
}

function eventLabel(event) {
  return event.event_type === "suspected_fall" ? "Suspected fall" : event.event_type === "person_down" ? "Person down" : "Fall";
}
function renderHistory() {
  $("historyCount").textContent = `${events.length} of ${historyTotal} saved event${historyTotal === 1 ? "" : "s"} · ${historyScope === "all" ? "all history, latest saved first" : "today, local time"}`;
  $("historyList").innerHTML = events.length ? events.map((event) => `<button class="history-row" data-event-id="${esc(event.id)}" aria-current="${current?.id === event.id}" aria-label="Open event ${event.seq}, ${esc(time(event.detected_at_ms))}">
    <span><span class="history-title">#${event.seq} · ${esc(eventLabel(event))}</span><span class="history-device">${esc(event.device_id)}</span></span>
    <time class="history-time" datetime="${isoTimestamp(event.detected_at_ms) || ""}">${esc(time(event.detected_at_ms))}</time>
    <span class="history-size">${event.n_images} photo${event.n_images === 1 ? "" : "s"}${event.has_clip ? " + clip" : ""} · ${formatBytes(event.total_bytes)}</span><span class="history-arrow" aria-hidden="true">›</span>
    </button>`).join("") : `<p class="history-empty muted">${historyScope === "all" ? "No saved events yet. Captured events will stay in this history." : "No events today. Choose All history to see earlier evidence."}</p>`;
  $("historyList").querySelectorAll("[data-event-id]").forEach((row) => row.addEventListener("click", () => {
    $("eventSelect").value = row.dataset.eventId;
    openEvent(row.dataset.eventId);
  }));
  $("loadMore").hidden = historyCursor === null;
  $("loadMore").disabled = historyBusy;
}
async function loadHistory(append = false) {
  if (historyBusy) { historyRefreshPending = true; return; }
  historyBusy = true;
  $("loadMore").disabled = true;
  const scope = historyScope;
  const range = localDayRange();
  if (scope === "today" && historyDay !== range.start) { events = []; historyCursor = null; }
  try {
    const query = new URLSearchParams({ limit: "50" });
    if (scope === "today") { query.set("start_ms", range.start); query.set("end_ms", range.end); }
    if (append && historyCursor !== null) query.set("before_seq", historyCursor);
    const data = await api(`/api/history?${query}`);
    if (scope !== historyScope) { historyRefreshPending = true; return; }
    const oldTop = events[0]?.seq;
    if (append || !events.length || (data.events.length && data.events.at(-1).seq > oldTop)) historyCursor = data.next_cursor;
    const merged = new Map(events.map((event) => [event.id, event]));
    for (const event of data.events) merged.set(event.id, event);
    events = [...merged.values()].sort((a, b) => b.seq - a.seq);
    historyTotal = data.total;
    historyDay = range.start;
    if (events.length >= historyTotal) historyCursor = null;
    renderHistory();
    if (!events.length) { emptyGallery(); return; }
    const previous = current?.id || $("eventSelect").value;
    $("eventSelect").disabled = false;
    $("eventSelect").innerHTML = events.map((event) => `<option value="${esc(event.id)}">#${event.seq} · ${esc(time(event.detected_at_ms))} · ${esc(eventLabel(event))}</option>`).join("");
    const hashId = new URLSearchParams(location.hash.slice(1)).get("e");
    const selected = events.some((event) => event.id === previous) ? previous : events.some((event) => event.id === hashId) ? hashId : events[0].id;
    $("eventSelect").value = selected;
    const revision = events.find((event) => event.id === selected).manifest_sha256;
    if (current?.id !== selected || eventRevision !== revision) await openEvent(selected);
  } catch (error) {
    message(error.message, true);
  } finally {
    historyBusy = false;
    $("loadMore").disabled = false;
    if (historyRefreshPending) { historyRefreshPending = false; loadHistory(); }
  }
}
function setHistoryScope(scope) {
  if (scope === historyScope) return;
  historyScope = scope;
  events = []; historyCursor = null; historyTotal = 0;
  $("historyAll").setAttribute("aria-pressed", String(scope === "all"));
  $("historyToday").setAttribute("aria-pressed", String(scope === "today"));
  $("historyCount").textContent = "Loading saved events…";
  $("historyList").innerHTML = '<p class="history-empty muted">Loading history…</p>';
  loadHistory();
}
$("historyAll").addEventListener("click", () => setHistoryScope("all"));
$("historyToday").addEventListener("click", () => setHistoryScope("today"));
$("loadMore").addEventListener("click", () => loadHistory(true));

async function openEvent(id) {
  const ticket = ++selection;
  $("verifyBtn").disabled = true;
  $("gallery").className = "gallery-empty";
  $("gallery").innerHTML = '<p class="muted">Loading event evidence…</p>';
  try {
    const event = await api(`/api/events/${encodeURIComponent(id)}`);
    if (ticket !== selection) return;
    releaseMedia();
    current = event;
    renderHistory();
    eventRevision = event.manifest_sha256;
    photoIndex = Math.max(0, event.images.findIndex((image) => image.role === "laid"));
    history.replaceState(null, "", `#e=${encodeURIComponent(id)}`);
    const saved = events.find((e) => e.id === id);
    $("selectedStorage").textContent = `Selected event: ${formatBytes(saved?.total_bytes || 0)} · ${event.images.length} photos${event.clip ? " + video" : ""}`;
    $("eventTime").textContent = time(event.detected_at_ms);
    $("eventTime").dateTime = isoTimestamp(event.detected_at_ms) || "";
    $("receivedTime").textContent = `Server received ${time(Date.parse(event.received_at))}`;
    $("eventHash").textContent = event.manifest_sha256;
    $("verifyOut").textContent = "";
    $("verifyBtn").disabled = false;
    $("gallery").className = "";
    $("gallery").innerHTML = `<div class="photo-stage"><img id="mainPhoto" alt=""><span id="photoStatus" class="photo-status">Loading photo…</span></div>
      <div class="photo-caption"><span id="photoCaption"></span><div class="photo-actions"><button id="prevPhoto" aria-label="Previous photo">‹</button><span id="photoPosition"></span><button id="nextPhoto" aria-label="Next photo">›</button></div></div>
      <div id="thumbnails" class="thumbnails" aria-label="Captured photos"></div>
      ${event.clip ? '<details id="clipDetails" class="clip-details"><summary>Play event video</summary><video id="eventClip" controls playsinline preload="metadata"></video><p class="small muted">Clip begins at the possible fall. Photos include the lead-up.</p></details>' : ""}`;
    $("prevPhoto").addEventListener("click", () => { photoIndex--; renderPhoto(ticket); });
    $("nextPhoto").addEventListener("click", () => { photoIndex++; renderPhoto(ticket); });
    if (event.clip) $("clipDetails").addEventListener("toggle", async () => {
      if (!$("clipDetails")?.open || $("eventClip").src) return;
      try {
        const url = await mediaUrl(event.clip.name, ticket);
        if (url && ticket === selection) $("eventClip").src = url;
      } catch (error) { message(error.message, true); }
    });
    await renderPhoto(ticket);
  } catch (error) {
    if (ticket === selection) {
      current = null;
      eventRevision = null;
      releaseMedia();
      $("gallery").className = "gallery-empty";
      $("gallery").textContent = "Evidence could not be loaded. Refresh to try again.";
      message(error.message, true);
    }
  }
}
let photoRevision = 0;
async function renderPhoto(ticket) {
  const revision = ++photoRevision;
  const images = current.images;
  if (!images.length) { $("photoStatus").textContent = "No photos saved for this event."; return; }
  photoIndex = Math.max(0, Math.min(photoIndex, images.length - 1));
  const image = images[photoIndex];
  const begin = Math.max(0, Math.min(photoIndex - 3, images.length - 8));
  const visible = images.slice(begin, begin + 8);
  const keep = new Set([image.name, ...visible.map((im) => im.name), current.clip?.name]);
  wantedMedia = keep;
  for (const [name, url] of urls) if (!keep.has(name)) { URL.revokeObjectURL(url); urls.delete(name); }
  $("photoStatus").textContent = "Loading photo…";
  $("mainPhoto").removeAttribute("src");
  $("mainPhoto").alt = `${image.role} evidence photo captured ${time(image.captured_at_ms)}`;
  $("photoCaption").textContent = `${image.role === "laid" ? "On the floor" : image.role === "standing" ? "Standing" : image.role === "pre" ? "Before event" : image.role === "post" ? "After event" : "During event"} · ${time(image.captured_at_ms)}`;
  $("photoPosition").textContent = `${photoIndex + 1} / ${images.length}`;
  $("prevPhoto").disabled = photoIndex === 0;
  $("nextPhoto").disabled = photoIndex === images.length - 1;
  $("thumbnails").innerHTML = visible.map((im, index) => `<button class="thumbnail" data-index="${begin+index}" aria-label="Photo ${begin+index+1}: ${esc(im.role)}" aria-pressed="${begin+index === photoIndex}"><img alt="${esc(im.role)}"><span>${esc(im.role)}</span></button>`).join("");
  $("thumbnails").querySelectorAll("button").forEach((button) => button.addEventListener("click", () => { photoIndex = Number(button.dataset.index); renderPhoto(ticket); }));
  const target = $("mainPhoto");
  try {
    const url = await mediaUrl(image.name, ticket);
    if (ticket !== selection || revision !== photoRevision) return;
    if (url) { target.src = url; $("photoStatus").textContent = ""; }
  } catch (error) { if (ticket === selection && revision === photoRevision) $("photoStatus").textContent = error.message; }
  const thumbs = [...$("thumbnails").querySelectorAll("img")];
  await Promise.all(visible.map(async (im, index) => {
    try {
      if (ticket !== selection || revision !== photoRevision) return;
      const url = await mediaUrl(im.name, ticket);
      if (url && ticket === selection && revision === photoRevision) thumbs[index].src = url;
    } catch { /* Keep the role label visible when an individual image is missing. */ }
  }));
}

$("eventSelect").addEventListener("change", () => openEvent($("eventSelect").value));
$("verifyBtn").addEventListener("click", async () => {
  if (!current) return;
  const ticket = selection;
  $("verifyOut").textContent = "Checking…";
  try {
    const result = await api(`/api/events/${current.id}/verify`);
    if (ticket === selection) $("verifyOut").innerHTML = result.ok ? '<span class="badge ok">Intact · verified</span>' : '<span class="badge bad">Integrity check failed</span>';
  } catch (error) { if (ticket === selection) $("verifyOut").textContent = error.message; }
});
$("refresh").addEventListener("click", loadList);
$("chain").addEventListener("click", async () => {
  try {
    const result = await api("/api/chain/verify");
    message(result.ok ? `${result.events_checked} events verified · chain intact` : "Some events failed integrity checks.", !result.ok);
  } catch (error) { message(error.message, true); }
});
showDate();
new ResizeObserver(() => { if (chartData) renderChart(chartData.hours, chartData.count); }).observe($("dailyChart"));
loadList();
setInterval(() => { if (!document.hidden) loadList(); }, 30000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadList(); });
window.addEventListener("message", (event) => {
  if (event.origin === location.origin && event.source === window.parent && event.data?.type === "fallwatch:event-saved") loadList();
});
window.addEventListener("pagehide", releaseMedia);
