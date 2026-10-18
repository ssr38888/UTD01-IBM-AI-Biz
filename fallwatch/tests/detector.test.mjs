import { test } from "node:test";
import assert from "node:assert/strict";
import { classifyPosture, FallDetector, POSTURE, CAMERA_DETECTION_OPTIONS } from "../phone/detector.js";
import { pose, timeline } from "./synthetic.js";

test("camera never logs startup, presence, or an already-horizontal person", () => {
  for (const script of [[["empty", 2000], ["standing", 8000]], [["lying", 12000]], [["sitting", 3000], ["lying", 8000]]]) {
    assert.equal(run(script, CAMERA_DETECTION_OPTIONS).events.length, 0);
  }
});

test("camera records only an observed rapid standing-to-floor transition", () => {
  const { events } = run([["standing", 2000], ["fall", 600], ["lying", 4000]], CAMERA_DETECTION_OPTIONS);
  assert.equal(events.length, 1);
  assert.equal(events[0].observedFall, true);
  assert.equal(events[0].eventType, "fall");
  assert.ok(events[0].confirmedT - events[0].lieT >= 300);
  assert.ok(events[0].confirmedT - events[0].lieT < 600);
  assert.equal(run([["standing", 3000], ["bending", 4000]], CAMERA_DETECTION_OPTIONS).events.length, 0);
  assert.equal(run([["standing", 3000], ["slowdown", 8000], ["lying", 4000]], CAMERA_DETECTION_OPTIONS).events.length, 0);
});

test("horizontal policy triggers at 0.3 seconds without a prior standing frame", () => {
  const d = new FallDetector({ horizontalConfirmMs: 300, horizontalTorsoOnly: true });
  assert.equal(d.update(0, pose("lying")).event, null);
  assert.equal(d.update(299, pose("lying")).event, null);
  const result = d.update(300, pose("lying"));
  assert.equal(result.event?.eventType, "suspected_fall");
  assert.equal(result.event?.observedFall, false);
  assert.equal(d.update(2000, pose("lying")).event, null);
});

test("horizontal torso is the deciding signal even when the body box is tall", () => {
  const lms = pose("lying");
  lms[0].y = 0.1;
  assert.equal(classifyPosture(lms).posture, POSTURE.OTHER);
  assert.equal(classifyPosture(lms, 1, { horizontalTorsoOnly: true }).posture, POSTURE.LYING);
});

test("horizontal confirmation resets on upright or missing poses and rearms after recovery", () => {
  const opts = { horizontalConfirmMs: 300, horizontalTorsoOnly: true };
  assert.equal(run([["lying", 200], ["empty", 200], ["lying", 200], ["standing", 2000]], opts).events.length, 0);
  assert.equal(run([["standing", 5000], ["sitting", 2000]], opts).events.length, 0);
  const { events } = run([["lying", 2000], ["standing", 2000], ["lying", 2000]], opts);
  assert.equal(events.length, 2);
  assert.ok(events.every((e) => e.eventType === "suspected_fall"));
});

test("camera fallback records sustained lying without inventing a standing-to-floor fall", () => {
  const { events } = run([["lying", 9000]], { personDownMs: 5000 });
  assert.equal(events.length, 1);
  assert.equal(events[0].eventType, "person_down");
  assert.equal(events[0].observedFall, false);
  assert.equal(events[0].descentMs, 0);
  assert.equal(events[0].hipSpeedBodyHeightsPerSec, 0);
});

test("camera fallback catches a missed transition but labels it person down", () => {
  const { events } = run([["standing", 2000], ["sitting", 4000], ["lying", 8000]], { personDownMs: 5000 });
  assert.equal(events.length, 1);
  assert.equal(events[0].eventType, "person_down");
});

test("brief lying is not logged by the fallback and recovery permits a second event", () => {
  assert.equal(run([["lying", 3000], ["standing", 3000]], { personDownMs: 5000 }).events.length, 0);
  const { events } = run([["lying", 8000], ["standing", 4000], ["sitting", 3000], ["lying", 8000]], { personDownMs: 5000 });
  assert.equal(events.length, 2);
});

test("normal fall still confirms once before the sustained-down fallback", () => {
  const { events } = run([["standing", 2000], ["fall", 600], ["lying", 9000]], { personDownMs: 5000 });
  assert.equal(events.length, 1);
  assert.equal(events[0].eventType, "fall");
  assert.equal(events[0].observedFall, true);
});

test("side-on person remains detectable with the far shoulder and hip occluded", () => {
  const d = new FallDetector();
  let detected;
  for (const f of timeline([["standing", 2000], ["fall", 600], ["lying", 5000]])) {
    f.lms[12].visibility = 0.1;
    f.lms[24].visibility = 0.1;
    const r = d.update(f.t, f.lms);
    if (r.event) detected = r.event;
  }
  assert.ok(detected, "occluded side should not prevent automatic fall capture");
});

test("invalid landmark coordinates are rejected", () => {
  const lms = pose("standing");
  lms[11].x = NaN;
  lms[12].x = NaN;
  assert.equal(classifyPosture(lms).posture, POSTURE.UNKNOWN);
});

function run(script, opts) {
  const d = new FallDetector(opts);
  const events = [];
  for (const f of timeline(script)) {
    const r = d.update(f.t, f.lms, 1);
    if (r.event) events.push(r.event);
  }
  return { d, events };
}

test("postures are classified", () => {
  assert.equal(classifyPosture(pose("standing")).posture, POSTURE.STANDING);
  assert.equal(classifyPosture(pose("lying")).posture, POSTURE.LYING);
  assert.equal(classifyPosture(pose("sitting")).posture, POSTURE.OTHER);
  assert.equal(classifyPosture(pose("bending")).posture, POSTURE.OTHER);
  assert.equal(classifyPosture(pose("empty")).posture, POSTURE.UNKNOWN);
});

test("fast fall that stays down is detected once", () => {
  const { events } = run([["standing", 3000], ["fall", 600], ["lying", 6000]]);
  assert.equal(events.length, 1);
  const e = events[0];
  assert.ok(e.descentMs > 0 && e.descentMs <= 2000, `descent ${e.descentMs}`);
  assert.ok(e.hipSpeedBodyHeightsPerSec > 0);
  assert.ok(e.lieT > e.standT);
});

test("landing side estimate", () => {
  for (const side of ["left", "right"]) {
    const d = new FallDetector();
    let ev;
    for (const f of timeline([["standing", 2000], ["fall", 400], ["lying", 4000]], 5, { side })) {
      const r = d.update(f.t, f.lms, 1);
      if (r.event) ev = r.event;
    }
    assert.equal(ev.landingSide, side);
  }
});

test("landing side holds on a wide 16:9 camera", () => {
  const d = new FallDetector();
  let ev;
  for (const f of timeline([["standing", 2000], ["fall", 600], ["lying", 4000]], 5, { side: "left" })) {
    const r = d.update(f.t, f.lms, 16 / 9);
    if (r.event) ev = r.event;
  }
  assert.equal(ev?.landingSide, "left");
});

test("lying down on purpose (via sitting) is not a fall", () => {
  const { events } = run([["standing", 3000], ["sitting", 3000], ["lying", 6000]]);
  assert.equal(events.length, 0);
});

test("very slow tip-over (8 s) is not a fall", () => {
  const { events } = run([["standing", 3000], ["slowdown", 8000], ["lying", 6000]]);
  assert.equal(events.length, 0);
});

test("quick tumble and immediate stand-up is not confirmed", () => {
  const { events } = run([["standing", 3000], ["fall", 400], ["lying", 1500], ["standing", 3000]]);
  assert.equal(events.length, 0);
});

test("sitting down and bending over never trigger", () => {
  assert.equal(run([["standing", 2000], ["sitting", 8000]]).events.length, 0);
  assert.equal(run([["standing", 2000], ["bending", 8000], ["standing", 2000]]).events.length, 0);
});

test("brief detection drop-out while down does not cancel", () => {
  const { events } = run([["standing", 2000], ["fall", 400], ["lying", 1200], ["empty", 800], ["lying", 4000]]);
  assert.equal(events.length, 1);
});

test("floor time is measured until the person gets up", () => {
  const { d, events } = run([["standing", 2000], ["fall", 400], ["lying", 8000], ["standing", 3000]]);
  assert.equal(events.length, 1);
  const e = d.events[0];
  assert.equal(e.recovered, true);
  assert.ok(e.floorMs >= 7500 && e.floorMs <= 9000, `floor ${e.floorMs}`);
});

test("two separate falls give two events", () => {
  const { events } = run([
    ["standing", 2000], ["fall", 400], ["lying", 5000],
    ["standing", 4000], ["fall", 400], ["lying", 5000],
  ]);
  assert.equal(events.length, 2);
});
