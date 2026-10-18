// FallWatch fall detector: pose keypoints -> posture -> fall event.
// Pure logic, no browser APIs, so it runs on the phone and in Node tests/eval.
//
// Input landmarks follow MediaPipe Pose (33 points, x/y normalized 0..1,
// y pointing down, optional `visibility` 0..1).

export const LM = {
  NOSE: 0,
  L_SHOULDER: 11, R_SHOULDER: 12,
  L_HIP: 23, R_HIP: 24,
  L_KNEE: 25, R_KNEE: 26,
  L_ANKLE: 27, R_ANKLE: 28,
};

export const POSTURE = {
  STANDING: "standing",
  LYING: "lying",
  OTHER: "other",     // sitting, crouching, bending, mid-fall
  UNKNOWN: "unknown", // nobody / key points not visible
};

export const DEFAULTS = {
  minVisibility: 0.5,
  standMaxTorsoDeg: 30,   // torso within 30 deg of vertical
  standMinLegRatio: 1.25, // hip->ankle height vs torso length (sitting is lower)
  standMinBoxRatio: 1.4,  // body box height/width when ankles not visible
  lieMinTorsoDeg: 60,     // torso within 30 deg of horizontal
  lieMaxBoxRatio: 1.0,    // body box wider than tall
  minStandMs: 300,        // standing must be steady this long to count
  fallWindowMs: 2000,     // last standing -> first lying must be faster than this
  confirmMs: 3000,        // must stay down this long to confirm the event
  unknownGraceMs: 1500,   // tolerate brief detection drop-outs while down
  recoverStandMs: 1000,   // standing this long after an event = got up
  personDownMs: 0,        // optional camera fallback when the transition was not observed
  horizontalConfirmMs: 0,
  horizontalTorsoOnly: false,
  minHipDropBodyHeights: null,
};

export const CAMERA_DETECTION_OPTIONS = Object.freeze({ confirmMs: 300, horizontalTorsoOnly: true,
                                                        minHipDropBodyHeights: 0.15 });

const vis = (p, min) => p && Number.isFinite(p.x) && Number.isFinite(p.y) &&
  (p.visibility === undefined || p.visibility >= min);
const mid = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });

/**
 * Classify one frame. `aspect` = frame width / height, used so that an
 * angle measured in normalized coords matches the real picture.
 */
export function classifyPosture(lms, aspect = 1, opts = DEFAULTS) {
  const o = { ...DEFAULTS, ...opts };
  if (!lms || lms.length < 29) return { posture: POSTURE.UNKNOWN };
  const ls = lms[LM.L_SHOULDER], rs = lms[LM.R_SHOULDER];
  const lh = lms[LM.L_HIP], rh = lms[LM.R_HIP];
  // Side-lying people often occlude the far shoulder/hip. Use the visible
  // side consistently rather than averaging low-confidence coordinates.
  const sides = [[ls, lh], [rs, rh]].filter((pair) => pair.every((p) => vis(p, o.minVisibility)));
  if (!sides.length) {
    return { posture: POSTURE.UNKNOWN };
  }
  const sx = (p) => ({ x: p.x * aspect, y: p.y }); // undo the aspect squeeze
  const S = sides.length === 2 ? mid(sx(ls), sx(rs)) : sx(sides[0][0]);
  const H = sides.length === 2 ? mid(sx(lh), sx(rh)) : sx(sides[0][1]);
  const dx = S.x - H.x, dy = S.y - H.y;
  const torsoLen = Math.hypot(dx, dy) || 1e-6;
  const torsoDeg = (Math.atan2(Math.abs(dx), Math.abs(dy)) * 180) / Math.PI;

  // Bounding box over all confidently visible points.
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const p of lms) {
    if (!vis(p, o.minVisibility)) continue;
    const q = sx(p);
    x0 = Math.min(x0, q.x); x1 = Math.max(x1, q.x);
    y0 = Math.min(y0, q.y); y1 = Math.max(y1, q.y);
  }
  const boxW = Math.max(x1 - x0, 1e-6), boxH = Math.max(y1 - y0, 1e-6);
  const boxRatio = boxH / boxW;

  // Legs: how far the ankles sit below the hips, relative to torso length.
  const ankles = [lms[LM.L_ANKLE], lms[LM.R_ANKLE]].filter((p) => vis(p, o.minVisibility));
  const legRatio = ankles.length
    ? (Math.max(...ankles.map((p) => p.y)) - H.y) / torsoLen
    : null;
  const A = ankles.length
    ? ankles.map(sx).reduce((sum,p)=>({x:sum.x+p.x/ankles.length,y:sum.y+p.y/ankles.length}),{x:0,y:0})
    : null;
  const axisDeg = (from,to) => Math.atan2(Math.abs(to.x-from.x),Math.abs(to.y-from.y))*180/Math.PI;
  const bodyAxisDeg = A ? axisDeg(S,A) : null;
  const legAxisDeg = A ? axisDeg(H,A) : null;
  // Perspective can tilt a prone torso even when the rest of the body is
  // horizontal. Require visible, extended horizontal legs as corroboration.
  const wholeBodyHorizontal = o.horizontalTorsoOnly && A && torsoDeg >= 45 &&
    bodyAxisDeg >= 60 && legAxisDeg >= 60 && boxRatio <= 1 &&
    (S.x-H.x)*(A.x-H.x) + (S.y-H.y)*(A.y-H.y) < 0 &&
    Math.hypot(A.x-H.x,A.y-H.y) >= torsoLen*.8;

  let posture = POSTURE.OTHER;
  if ((torsoDeg >= o.lieMinTorsoDeg && (o.horizontalTorsoOnly || boxRatio <= o.lieMaxBoxRatio)) || wholeBodyHorizontal) {
    posture = POSTURE.LYING;
  } else if (torsoDeg <= o.standMaxTorsoDeg) {
    const legsOk = legRatio !== null ? legRatio >= o.standMinLegRatio : boxRatio >= o.standMinBoxRatio;
    if (legsOk) posture = POSTURE.STANDING;
  }

  return {
    posture,
    torsoDeg,
    bodyAxisDeg,
    legAxisDeg,
    horizontalBasis: posture === POSTURE.LYING ? (torsoDeg >= o.lieMinTorsoDeg ? 'torso' : 'whole_body') : null,
    boxRatio,
    legRatio,
    hipY: H.y,
    bodyH: boxH,
    torsoLen,
    // Raw shoulder heights, for the landing-side estimate.
    lShoulderY: sides.length === 2 ? ls.y : undefined,
    rShoulderY: sides.length === 2 ? rs.y : undefined,
  };
}

/**
 * 2D landing-side estimate. When someone lies on a side, the shoulder on
 * that side is underneath (lower in the picture). Only an estimate: on the
 * back/front, or when the camera looks along the body, it is "unclear".
 */
export function estimateLandingSide(m) {
  if (!m || m.lShoulderY === undefined) return "unclear";
  const diff = m.lShoulderY - m.rShoulderY; // + => left shoulder lower
  if (Math.abs(diff) < 0.3 * m.torsoLen) return "unclear";
  return diff > 0 ? "left" : "right";
}

/**
 * Stateful detector. Call update() for every analyzed frame, in time order.
 * Returns { posture, metrics, phase, event } where `event` is set only on
 * the frame the fall is confirmed.
 */
export class FallDetector {
  constructor(opts = {}) {
    this.o = { ...DEFAULTS, ...opts };
    this.reset();
  }

  reset() {
    this.standSince = null;   // start of the current steady-standing run
    this.lastStand = null;    // last frame that counted as steady standing
    this.candidate = null;    // a fall in progress, not yet confirmed
    this.active = null;       // confirmed event, person still down
    this.events = [];
    this.downObservation = null;
  }

  update(t, lms, aspect = 1) {
    const m = classifyPosture(lms, aspect, this.o);
    const p = m.posture;
    let event = null;

    // Track steady standing.
    if (p === POSTURE.STANDING) {
      if (this.standSince === null) this.standSince = t;
      if (t - this.standSince >= this.o.minStandMs) {
        this.lastStand = { t, hipY: m.hipY, bodyH: m.bodyH };
      }
    } else {
      this.standSince = null;
    }

    if (this.o.otherConfirmsFall && p === POSTURE.OTHER && !this.active) {
      event = this._confirm({ standT:t, standHipY:m.hipY, standBodyH:1,
        lieT:t, lieHipY:m.hipY, sideVotes:{left:0,right:0,unclear:1} },t,'suspected_fall');
      return {posture:p,metrics:m,phase:'down',event,eventType:event.eventType,confirmedT:t,downForMs:null};
    }
    if (this.o.horizontalConfirmMs > 0) return this._updateHorizontal(t, m);

    if (this.active) {
      // Person is down after a confirmed fall: wait for them to get up.
      if (p === POSTURE.STANDING && this.standSince !== null &&
          t - this.standSince >= this.o.recoverStandMs) {
        this.active.floorMs = this.standSince - this.active.lieT;
        this.active.recovered = true;
        this.active = null;
        this.candidate = null;
      } else if (p === POSTURE.LYING) {
        this.active.floorMs = t - this.active.lieT;
      }
    } else if (this.candidate) {
      const c = this.candidate;
      if (p === POSTURE.LYING) {
        c.lastLyingT = t;
        c.sideVotes[estimateLandingSide(m)]++;
        if (t - c.lieT >= this.o.confirmMs) {
          event = this._confirm(c, t);
        }
      } else if (p === POSTURE.STANDING) {
        this.candidate = null; // stood back up: not a fall (e.g. stretch, tumble-recover)
      } else if (t - c.lastLyingT > this.o.unknownGraceMs) {
        this.candidate = null; // left the lying posture for too long
      }
    } else if (p === POSTURE.LYING && this.lastStand &&
               t - this.lastStand.t <= this.o.fallWindowMs &&
               (this.o.minHipDropBodyHeights === null ||
                (m.hipY - this.lastStand.hipY) / (this.lastStand.bodyH || 1) >= this.o.minHipDropBodyHeights)) {
      // Went from steady standing to lying fast enough: possible fall.
      this.candidate = {
        standT: this.lastStand.t,
        standHipY: this.lastStand.hipY,
        standBodyH: this.lastStand.bodyH,
        lieT: t,
        lieHipY: m.hipY,
        lastLyingT: t,
        sideVotes: { left: 0, right: 0, unclear: 0 },
      };
      this.candidate.sideVotes[estimateLandingSide(m)]++;
    }

    // A recognized lying posture must not remain unlogged just because the
    // camera missed the preceding standing frame. Keep this distinct from a
    // witnessed fall, since deliberate lying can look identical in one frame.
    if (this.o.personDownMs > 0) {
      if (this.active || p === POSTURE.STANDING) {
        this.downObservation = null;
      } else if (p === POSTURE.LYING) {
        if (!this.downObservation || t - this.downObservation.lastLyingT > this.o.unknownGraceMs) {
          this.downObservation = { lieT: t, lastLyingT: t, lieHipY: m.hipY,
                                   sideVotes: { left: 0, right: 0, unclear: 0 } };
        }
        const down = this.downObservation;
        down.lastLyingT = t;
        down.sideVotes[estimateLandingSide(m)]++;
        if (t - down.lieT >= this.o.personDownMs) {
          event = this._confirm({ ...down, standT: down.lieT, standHipY: down.lieHipY, standBodyH: 1 }, t, "person_down");
          this.downObservation = null;
        }
      } else if (this.downObservation && t - this.downObservation.lastLyingT > this.o.unknownGraceMs) {
        this.downObservation = null;
      }
    }

    return {
      posture: p,
      metrics: m,
      phase: this.active ? "down" : this.candidate ? "candidate" : "watching",
      event,
      eventType: this.active?.eventType ?? null,
      confirmedT: this.active?.confirmedT ?? null,
      downForMs: this.downObservation ? t - this.downObservation.lieT : null,
    };
  }

  _updateHorizontal(t, m) {
    let event = null;
    if (this.active) {
      if (m.posture === POSTURE.LYING) this.active.floorMs = t - this.active.lieT;
      if (m.posture === POSTURE.STANDING && this.standSince !== null && t - this.standSince >= this.o.recoverStandMs) {
        this.active.floorMs = this.standSince - this.active.lieT;
        this.active.recovered = true;
        this.active = null;
      }
    } else if (m.posture === POSTURE.LYING) {
      if (!this.downObservation || t - this.downObservation.lastLyingT > this.o.unknownGraceMs) {
        this.downObservation = { lieT: t, lastLyingT: t, lieHipY: m.hipY,
                                 sideVotes: { left: 0, right: 0, unclear: 0 } };
      }
      const down = this.downObservation;
      down.lastLyingT = t;
      down.sideVotes[estimateLandingSide(m)]++;
      if (t - down.lieT >= this.o.horizontalConfirmMs) {
        event = this._confirm({ ...down, standT: down.lieT, standHipY: down.lieHipY, standBodyH: 1 }, t, "suspected_fall");
        this.downObservation = null;
      }
    } else {
      this.downObservation = null; // Require continuously recognized horizontal posture.
    }
    return { posture: m.posture, metrics: m, event,
             phase: this.active ? "down" : this.downObservation ? "candidate" : "watching",
             eventType: this.active?.eventType ?? null,
             confirmedT: this.active?.confirmedT ?? null,
             downForMs: this.downObservation ? t - this.downObservation.lieT : null };
  }

  _confirm(c, t, eventType = "fall") {
    const observedFall = eventType === "fall";
    const descentMs = observedFall ? Math.max(c.lieT - c.standT, 1) : 0;
    const hipDrop = observedFall ? (c.lieHipY - c.standHipY) / (c.standBodyH || 1) : 0;
    const ev = {
      eventType,
      observedFall,
      standT: c.standT,          // last steady-standing frame (key frame 1)
      lieT: c.lieT,              // first lying frame (key frame 2)
      confirmedT: t,
      descentMs,
      hipDropBodyHeights: round(hipDrop, 3),
      hipSpeedBodyHeightsPerSec: observedFall ? round(hipDrop / (descentMs / 1000), 3) : 0,
      landingSide: sideFromVotes(c.sideVotes), // estimate, see estimateLandingSide()
      floorMs: t - c.lieT,
      recovered: false,
    };
    this.active = ev;
    this.candidate = null;
    this.events.push(ev);
    return ev;
  }
}

/** Majority over the lying frames; "unclear" unless one side clearly wins. */
function sideFromVotes(v) {
  const decided = v.left + v.right;
  if (decided === 0 || decided < v.unclear / 2) return "unclear";
  if (v.left >= 2 * v.right) return "left";
  if (v.right >= 2 * v.left) return "right";
  return "unclear";
}

function round(x, n) {
  const f = 10 ** n;
  return Math.round(x * f) / f;
}
