// Snapshot ring buffer and evidence selection. Pure logic (no DOM), tested in Node.
//
// A "frame" is { t, blob, bytes, flags } where t is ms since epoch and flags
// may contain "standKey" (the last steady-standing picture) or "lieKey"
// (the picture taken the instant the person was first seen lying).

export class FrameBuffer {
  constructor(keepMs) {
    this.keepMs = keepMs;
    this.frames = [];
    this.totalBytes = 0;
  }

  add(frame) {
    this.frames.push(frame);
    this.totalBytes += frame.bytes || 0;
  }

  /** Drop pictures older than keepMs, unless a pending event still needs them. */
  prune(now, holdFrom = Infinity) {
    const cutoff = Math.min(now - this.keepMs, holdFrom);
    let i = 0;
    while (i < this.frames.length && this.frames[i].t < cutoff) {
      this.totalBytes -= this.frames[i].bytes || 0;
      i++;
    }
    if (i) this.frames.splice(0, i);
  }

  between(t0, t1) {
    return this.frames.filter((f) => f.t >= t0 && f.t <= t1);
  }
}

/** Keep the newest `keepMs` of keypoint samples (tiny JSON, recorded at analysis rate). */
export class KeypointLog {
  constructor(keepMs) {
    this.keepMs = keepMs;
    this.rows = [];
  }
  add(row) {
    this.rows.push(row);
    const cutoff = row.t - this.keepMs;
    let i = 0;
    while (i < this.rows.length && this.rows[i].t < cutoff) i++;
    if (i) this.rows.splice(0, i);
  }
  between(t0, t1) {
    return this.rows.filter((r) => r.t >= t0 && r.t <= t1);
  }
}

function nearest(frames, t, pred = () => true) {
  let best = null;
  for (const f of frames) {
    if (!pred(f)) continue;
    if (!best || Math.abs(f.t - t) < Math.abs(best.t - t)) best = f;
  }
  return best;
}

function evenly(list, n) {
  if (n <= 0) return [];
  if (list.length <= n) return list;
  const out = [];
  for (let i = 0; i < n; i++) out.push(list[Math.round((i * (list.length - 1)) / Math.max(n - 1, 1))]);
  return [...new Set(out)];
}

/**
 * Pick the pictures that make up the evidence for one fall and label them:
 *   pre      before the person started going down
 *   standing key frame 1: last steady standing picture
 *   burst    the descent and the first seconds on the floor
 *   laid     key frame 2: first picture lying down
 *   post     after the fall was confirmed
 * If there are more than maxImages, pre/post are thinned evenly; key and
 * burst pictures are always kept.
 */
export function selectEventFrames(frames, ev, { preMs, postMs, maxImages = 150 }) {
  const t0 = ev.standT - preMs, t1 = ev.confirmedT + postMs;
  const inWin = frames.filter((f) => f.t >= t0 && f.t <= t1);
  if (!inWin.length) return [];

  const stand = ev.observedFall === false ? null : nearest(inWin, ev.standT, (f) => f.flags?.includes("standKey"))
    || nearest(inWin, ev.standT, (f) => f.t <= ev.standT)
    || inWin[0];
  const lie = nearest(inWin, ev.lieT, (f) => f.flags?.includes("lieKey") && f !== stand)
    || inWin.find((f) => f.t >= ev.lieT && f !== stand)
    || null;

  const roleOf = (f) => {
    if (f === stand) return "standing";
    if (f === lie) return "laid";
    if (f.t < ev.standT) return "pre";
    if (f.t <= ev.confirmedT) return "burst";
    return "post";
  };
  let picked = inWin.map((f) => ({ frame: f, role: roleOf(f) }));

  if (picked.length > maxImages) {
    const must = picked.filter((p) => p.role !== "pre" && p.role !== "post");
    const pre = picked.filter((p) => p.role === "pre");
    const post = picked.filter((p) => p.role === "post");
    const room = Math.max(maxImages - must.length, 0);
    const nPre = Math.round(room * (pre.length / Math.max(pre.length + post.length, 1)));
    picked = [...evenly(pre, nPre), ...must, ...evenly(post, room - nPre)]
      .sort((a, b) => a.frame.t - b.frame.t)
      .slice(0, Math.max(maxImages, must.length));
  }

  const used = new Set();
  return picked.map(({ frame, role }) => {
    let name = `f${frame.t}.jpg`;
    for (let k = 1; used.has(name); k++) name = `f${frame.t}_${k}.jpg`;
    used.add(name);
    return { name, t: frame.t, role, blob: frame.blob, bytes: frame.bytes };
  });
}

/** Keep only the body points the descriptor needs, rounded, so the log stays small. */
export const LOG_POINTS = [0, 11, 12, 23, 24, 25, 26, 27, 28];
export function compactLandmarks(lms) {
  if (!lms) return null;
  return LOG_POINTS.map((i) => {
    const p = lms[i];
    return p ? [+p.x.toFixed(3), +p.y.toFixed(3), +(p.visibility ?? 1).toFixed(2)] : null;
  });
}
