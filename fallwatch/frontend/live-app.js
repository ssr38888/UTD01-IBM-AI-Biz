import { createClaimPanel } from '/viewer/claimclock.js?v=30';
import { localDayRange, summarizeDay, formatBytes } from '/viewer/log-data.js?v=6';
import { advanceHistoryCursor } from './history-data.mjs?v=30';

const $ = id => document.getElementById(id);
const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date = value => new Date(value).toLocaleString();
const shortHash = hash => hash ? `${hash.slice(0, 8)}…${hash.slice(-6)}` : '—';
let sessionPromise, events = [], cursor = null, busy = false, refreshPending = false, selectLatestPending = false;
let selectedId = null, selectedEvent = null, generation = 0, photoIndex = 0, photoRevision = 0;
let pendingSavedReceipt = null;
const media = new Map(), objectUrls = new Set();

async function sessionFetch(path, options = {}) {
  let response = await fetch(path, {...options, credentials:'same-origin', cache:'no-store'});
  if (response.status === 401) {
    if (!sessionPromise) sessionPromise = fetch('/api/camera/session', {method:'POST', credentials:'same-origin'}).finally(() => { sessionPromise = null; });
    const session = await sessionPromise;
    if (!session.ok) throw new Error('Could not open the camera session. Refresh to retry.');
    response = await fetch(path, {...options, credentials:'same-origin', cache:'no-store'});
  }
  return response;
}
async function api(path) {
  const response = await sessionFetch(path);
  if (!response.ok) throw new Error(`Server request failed (${response.status}). Refresh to retry.`);
  return response.json();
}
const scoreEvent = createClaimPanel(sessionFetch);
function releaseMedia() {
  for (const url of objectUrls) URL.revokeObjectURL(url);
  objectUrls.clear(); media.clear();
}
async function mediaUrl(eventId, image, ticket) {
  if (media.has(image.name)) return media.get(image.name);
  const pending = (async () => {
    const response = await sessionFetch(`/api/events/${encodeURIComponent(eventId)}/files/${encodeURIComponent(image.name)}`);
    if (!response.ok) throw new Error('Evidence photo could not be loaded.');
    const blob = await response.blob();
    if (ticket !== generation) return null;
    const url = URL.createObjectURL(blob); objectUrls.add(url); return url;
  })();
  media.set(image.name, pending);
  try { return await pending; } catch (error) { media.delete(image.name); throw error; }
}
function renderHistory(total) {
  $('chain').replaceChildren();
  for (const event of events) {
    const card = document.createElement('button'); card.className = 'card'; card.type = 'button';
    card.dataset.eventId = event.id;
    card.setAttribute('aria-pressed', String(event.id === selectedId));
    card.setAttribute('aria-label', `Open event ${event.seq}`);
    card.innerHTML = `<div class="card-top"><b class="num">#${event.seq}</b><span class="src">saved event</span></div><div class="keys"><p>${event.n_images} captured photos</p></div><dl class="meta"><dt>Detected</dt><dd>${escape(date(event.detected_at_ms))}</dd><dt>Camera</dt><dd>${escape(event.device_id)}</dd><dt>Storage</dt><dd>${escape(formatBytes(event.total_bytes || 0))}</dd></dl><div class="hashes"><div><span>manifest</span><code>${escape(shortHash(event.manifest_sha256))}</code></div></div><div class="verdict">Select to inspect evidence</div>`;
    card.addEventListener('click', () => selectEvent(event.id)); $('chain').append(card);
  }
  if (!events.length) $('chain').textContent = 'No saved incidents yet. Confirmed falls will appear here automatically.';
  $('allCount').textContent = total.toLocaleString();
  $('chainStatus').textContent = `${events.length} of ${total} saved events · newest first`;
  $('loadOlder').hidden = cursor === null;
  if (selectedEvent) renderCardPreview(selectedEvent,generation);
}
function renderCardPreview(event,ticket) {
  const card=[...document.querySelectorAll('[data-event-id]')].find(card=>card.dataset.eventId===event.id);
  if(!card) return;
  const keys=card.querySelector('.keys'); keys.replaceChildren();
  const picks=[event.images.find(image=>image.role==='standing') || event.images[0],event.images.find(image=>image.role==='laid') || event.images.at(-1)].filter(Boolean);
  for(const image of new Map(picks.map(image=>[image.name,image])).values()) {
    const img=document.createElement('img'); img.alt=`${image.role} evidence`; keys.append(img);
    mediaUrl(event.id,image,ticket).then(url=>{if(url && ticket===generation) img.src=url;}).catch(()=>{});
  }
}
async function loadHistory(append = false, selectLatest = false) {
  if (busy) { refreshPending = true; selectLatestPending ||= selectLatest; return; }
  busy = true; $('refreshEvents').disabled = true; $('loadOlder').disabled = true;
  try {
    const previousNewest = events[0];
    const query = new URLSearchParams({limit:'50'});
    if (append && cursor !== null) query.set('before_seq',cursor);
    const data = await api(`/api/history?${query}`);
    const followArrival = !append && previousNewest && data.events[0]?.seq > previousNewest.seq && selectedId === previousNewest.id;
    cursor = advanceHistoryCursor({currentCursor:cursor,previousTop:events[0]?.seq,previousCount:events.length,page:data,append});
    const merged = new Map(events.map(event => [event.id,event]));
    for (const event of data.events) merged.set(event.id,event);
    const receipt = pendingSavedReceipt;
    if (receipt && !merged.has(receipt.eventId)) {
      const saved = await api(`/api/events/${encodeURIComponent(receipt.eventId)}`);
      merged.set(saved.id,{...saved,total_bytes:saved.images.reduce((sum,image)=>sum+image.bytes,0)+(saved.clip?.bytes||0)});
    }
    events = [...merged.values()].sort((a,b) => b.seq-a.seq);
    if (events.length >= data.total) cursor = null;
    renderHistory(data.total);
    const hashId = new URLSearchParams(location.hash.slice(1)).get('e');
    const next = receipt?.eventId || (selectLatest || followArrival ? events[0]?.id : selectedId || events.find(e => e.id === hashId)?.id || events[0]?.id);
    if (next && next !== selectedId) await selectEvent(next);
    else if (selectedEvent) scoreEvent(selectedEvent);
    if ((receipt || selectLatest || followArrival) && selectedEvent?.id === next) {
      const card=[...document.querySelectorAll('[data-event-id]')].find(card=>card.dataset.eventId===next);
      const strip=$('chain').parentElement;
      if(card) strip.scrollLeft += card.getBoundingClientRect().left-strip.getBoundingClientRect().left;
      if(receipt) {
        $('chainStatus').textContent=`Event #${selectedEvent.seq} saved · visible in the ledger`;
        $('cameraFrame')?.contentWindow.postMessage({type:'fallwatch:ledger-updated',eventId:receipt.eventId},location.origin);
        if(pendingSavedReceipt?.eventId===receipt.eventId) pendingSavedReceipt=null;
      }
    }
    $('connection').textContent = 'Live backend connected'; $('summaryText').textContent = 'Saved incidents and scores refresh automatically.';
  } catch (error) {
    $('connection').textContent = 'Connection interrupted'; $('chainStatus').textContent = error.message;
  } finally {
    busy = false; $('refreshEvents').disabled = false; $('loadOlder').disabled = false;
    if (refreshPending) { const latest=selectLatestPending; refreshPending = false; selectLatestPending=false; loadHistory(false,latest); }
  }
}
async function loadDaily() {
  const range = localDayRange();
  try {
    const data = await api(`/api/logs?start_ms=${range.start}&end_ms=${range.end}`);
    const summary = summarizeDay(data.events,range.start,range.end);
    $('todayCount').textContent = summary.count.toLocaleString(); $('todayStorage').textContent = formatBytes(summary.totalBytes);
  } catch { $('summaryText').textContent = 'Daily statistics are temporarily unavailable.'; }
}
async function selectEvent(id) {
  const ticket = ++generation; selectedId = id; selectedEvent = null; releaseMedia();
  scoreEvent(null); $('scoreEmpty').hidden = false; $('scoreEmpty').textContent = 'Loading incident scores…';
  $('evidenceDetail').hidden = true; $('verifyResult').textContent = '';
  document.querySelectorAll('[data-event-id]').forEach(card => card.setAttribute('aria-pressed', String(card.dataset.eventId === id)));
  for(const card of document.querySelectorAll('[data-event-id]')) {
    if(card.querySelector('.keys img')) {
      const record=events.find(event=>event.id===card.dataset.eventId), caption=document.createElement('p');
      caption.textContent=`${record?.n_images || 0} captured photos`; card.querySelector('.keys').replaceChildren(caption);
    }
  }
  try {
    const event = await api(`/api/events/${encodeURIComponent(id)}`);
    if (ticket !== generation) return;
    selectedEvent = event; photoIndex = Math.max(0,event.images.findIndex(image => image.role === 'laid'));
    renderCardPreview(event,ticket);
    $('evidenceDetail').hidden = false; $('evidenceTitle').textContent = `Event #${event.seq} · ${event.device_id}`;
    $('detectedTime').textContent = date(event.detected_at_ms); $('manifestHash').textContent = event.manifest_sha256;
    $('chainHash').textContent = event.chain_hash; $('scoreEmpty').hidden = true;
    history.replaceState(null,'',`#e=${encodeURIComponent(id)}`); scoreEvent(event);
    await showPhoto();
  } catch (error) { if (ticket === generation) { selectedId = null; $('chainStatus').textContent = error.message; $('scoreEmpty').textContent = 'Select an incident to retry loading.'; } }
}
async function showPhoto() {
  if (!selectedEvent?.images.length) return;
  const ticket = generation, photoTicket = ++photoRevision, event = selectedEvent;
  photoIndex = (photoIndex + event.images.length) % event.images.length;
  const image = event.images[photoIndex]; $('evidencePhoto').removeAttribute('src');
  $('photoMessage').textContent = 'Loading photo…'; $('photoCaption').textContent = `${photoIndex + 1} / ${event.images.length} · ${image.role} · ${date(image.captured_at_ms)}`;
  $('photoList').replaceChildren();
  const start = Math.max(0,Math.min(photoIndex-3,event.images.length-8));
  for (let i=start;i<Math.min(event.images.length,start+8);i++) {
    const item = event.images[i], button = document.createElement('button'); button.type = 'button';
    button.setAttribute('aria-label',`Photo ${i+1}: ${item.role}`); button.setAttribute('aria-current',String(i===photoIndex));
    const thumb = document.createElement('img'); thumb.alt = item.role; button.append(thumb);
    button.addEventListener('click',() => { photoIndex=i; showPhoto(); }); $('photoList').append(button);
    mediaUrl(event.id,item,ticket).then(url => { if(url && ticket===generation && photoTicket===photoRevision) thumb.src=url; }).catch(() => {});
  }
  try {
    const url = await mediaUrl(event.id,image,ticket);
    if (url && ticket===generation && photoTicket===photoRevision) { $('evidencePhoto').src=url; $('evidencePhoto').alt=`${image.role} evidence from event ${event.seq}`; $('photoMessage').textContent=''; }
  } catch(error) { if(ticket===generation && photoTicket===photoRevision) $('photoMessage').textContent=error.message; }
}
$('prevPhoto').addEventListener('click',() => { photoIndex--; showPhoto(); });
$('nextPhoto').addEventListener('click',() => { photoIndex++; showPhoto(); });
$('refreshEvents').addEventListener('click',() => { loadHistory(); loadDaily(); });
$('loadOlder').addEventListener('click',() => loadHistory(true));
$('verifySelected').addEventListener('click',async () => {
  if (!selectedEvent) return; const ticket=generation;
  $('verifyResult').textContent='Verifying saved evidence…';
  try { const result=await api(`/api/events/${selectedEvent.id}/verify`); if(ticket===generation) $('verifyResult').textContent=result.ok?'Evidence intact · verified':'Evidence integrity check failed'; }
  catch(error) { if(ticket===generation) $('verifyResult').textContent=error.message; }
});
$('verifyAll').addEventListener('click',async () => {
  $('verifyAll').disabled=true; $('chainStatus').textContent='Verifying evidence chain…';
  try { const result=await api('/api/chain/verify'); $('chainStatus').textContent=result.ok?`${result.events_checked} events verified · chain intact`:'Some events failed integrity checks'; }
  catch(error) { $('chainStatus').textContent=error.message; } finally { $('verifyAll').disabled=false; }
});
window.addEventListener('message',event => {
  if(event.origin===location.origin && event.source===$('cameraFrame')?.contentWindow && event.data?.type==='fallwatch:event-saved') {
    if(typeof event.data.eventId==='string' && /^[a-f0-9-]{36}$/i.test(event.data.eventId)) pendingSavedReceipt={eventId:event.data.eventId};
    loadHistory(false,true); loadDaily();
  }
});
setInterval(() => { if(!document.hidden) { loadHistory(); loadDaily(); } },15000);
document.addEventListener('visibilitychange',() => { if(!document.hidden) { loadHistory(); loadDaily(); } });
window.addEventListener('pagehide',releaseMedia);
loadHistory(); loadDaily();

window.addEventListener('message', event => {
  if(event.origin !== location.origin || event.source !== $('cameraFrame')?.contentWindow || event.data?.type !== 'fallwatch:camera-height') return;
  const height=event.data.height;
  if(Number.isFinite(height)) $('cameraFrame').style.height=`${Math.max(220,Math.min(1200,height))}px`;
});
