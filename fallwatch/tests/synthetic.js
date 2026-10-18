// Builds fake MediaPipe-style poses so the detector can be tested without a camera.
// Units: normalized image coords (0..1), y down, square frame (aspect 1).

function blank() {
  return Array.from({ length: 33 }, () => ({ x: 0.5, y: 0.5, visibility: 0 }));
}

function set(lms, i, x, y) {
  lms[i] = { x, y, visibility: 0.95 };
}

/**
 * kind: "standing" | "lying" | "sitting" | "bending"
 * t in [0,1] lets a "falling" pose interpolate between standing and lying.
 */
export function pose(kind, { cx = 0.5, side = "right" } = {}) {
  const lms = blank();
  const torso = 0.22, leg = 0.38, shW = 0.08;
  if (kind === "standing") {
    const hipY = 0.55;
    set(lms, 0, cx, hipY - torso - 0.08);
    set(lms, 11, cx - shW / 2, hipY - torso); set(lms, 12, cx + shW / 2, hipY - torso);
    set(lms, 23, cx - 0.03, hipY); set(lms, 24, cx + 0.03, hipY);
    set(lms, 25, cx - 0.03, hipY + leg / 2); set(lms, 26, cx + 0.03, hipY + leg / 2);
    set(lms, 27, cx - 0.03, hipY + leg); set(lms, 28, cx + 0.03, hipY + leg);
  } else if (kind === "lying") {
    // Horizontal body near the floor, head to the left.
    const y = 0.88;
    // Person lying on their LEFT side => left shoulder is underneath (lower).
    // Shoulder separation ~0.6 x torso length, about what a real side-lying body shows.
    const under = side === "left" ? 11 : 12;
    const over = side === "left" ? 12 : 11;
    set(lms, 0, cx - torso - 0.08, y - 0.01);
    set(lms, under, cx - torso, y + 0.07);
    set(lms, over, cx - torso, y - 0.07);
    set(lms, 23, cx, y + 0.015); set(lms, 24, cx, y - 0.015);
    set(lms, 25, cx + leg / 2, y + 0.01); set(lms, 26, cx + leg / 2, y - 0.01);
    set(lms, 27, cx + leg, y + 0.01); set(lms, 28, cx + leg, y - 0.01);
  } else if (kind === "sitting") {
    const hipY = 0.68;
    set(lms, 0, cx, hipY - torso - 0.08);
    set(lms, 11, cx - shW / 2, hipY - torso); set(lms, 12, cx + shW / 2, hipY - torso);
    set(lms, 23, cx - 0.03, hipY); set(lms, 24, cx + 0.03, hipY);
    set(lms, 25, cx + 0.15, hipY); set(lms, 26, cx + 0.15, hipY + 0.01);   // thighs forward
    set(lms, 27, cx + 0.15, hipY + 0.18); set(lms, 28, cx + 0.15, hipY + 0.19);
  } else if (kind === "bending") {
    // Torso tipped forward ~70 deg, but legs straight so the box stays tall.
    const hipY = 0.55;
    set(lms, 0, cx + torso + 0.05, hipY + 0.05);
    set(lms, 11, cx + torso * 0.94, hipY - torso * 0.34 - 0.02);
    set(lms, 12, cx + torso * 0.94, hipY - torso * 0.34 + 0.02);
    set(lms, 23, cx - 0.01, hipY); set(lms, 24, cx + 0.01, hipY);
    set(lms, 25, cx, hipY + leg / 2); set(lms, 26, cx, hipY + leg / 2);
    set(lms, 27, cx, hipY + leg); set(lms, 28, cx, hipY + leg);
  } else if (kind === "empty") {
    return blank();
  } else {
    throw new Error("unknown pose " + kind);
  }
  return lms;
}

/** Mid-fall frame: blend of standing and lying (torso tilting). */
export function falling(frac, opts = {}) {
  const a = pose("standing", opts), b = pose("lying", opts);
  return a.map((p, i) => ({
    x: p.x + (b[i].x - p.x) * frac,
    y: p.y + (b[i].y - p.y) * frac,
    visibility: Math.max(p.visibility, b[i].visibility),
  }));
}

/**
 * Turn a script like [["standing", 2000], ["fall", 600], ["lying", 5000]]
 * into timed frames at `fps`.
 */
export function timeline(script, fps = 5, opts = {}) {
  const frames = [];
  let t = 0;
  const dt = 1000 / fps;
  for (const [kind, ms] of script) {
    const n = Math.max(1, Math.round(ms / dt));
    for (let i = 0; i < n; i++) {
      let lms;
      if (kind === "fall") lms = falling((i + 1) / (n + 1), opts);
      else if (kind === "slowdown") lms = falling((i + 1) / (n + 1), opts);
      else lms = pose(kind, opts);
      frames.push({ t, lms });
      t += dt;
    }
  }
  return frames;
}
