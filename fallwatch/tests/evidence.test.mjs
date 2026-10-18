import { test } from "node:test";
import assert from "node:assert/strict";
import { FrameBuffer, selectEventFrames } from "../phone/evidence.js";

const ev = { standT: 20_000, lieT: 20_800, confirmedT: 23_800 };

test("person-down evidence never labels a photo as an observed standing pose", () => {
  const b = buffer(0, 34000);
  const out = selectEventFrames(b.frames, { ...ev, standT: ev.lieT, observedFall: false }, { preMs: 10000, postMs: 10000 });
  assert.equal(out.filter((f) => f.role === "standing").length, 0);
  assert.equal(out.filter((f) => f.role === "laid").length, 1);
});
function buffer(fromT, toT, step = 500) {
  const b = new FrameBuffer(16_000);
  for (let t = fromT; t <= toT; t += step) b.add({ t, blob: null, bytes: 70_000, flags: [] });
  return b;
}

test("buffer drops old pictures and keeps memory bounded", () => {
  const b = buffer(0, 60_000);
  b.prune(60_000);
  assert.ok(b.frames[0].t >= 44_000);
  assert.equal(b.totalBytes, b.frames.length * 70_000);
  assert.ok(b.totalBytes < 3_000_000);
});

test("buffer holds pictures a pending event still needs", () => {
  const b = buffer(0, 40_000);
  b.prune(40_000, 10_000);
  assert.equal(b.frames[0].t, 10_000);
});

test("event pictures get the right roles and key frames", () => {
  const b = buffer(0, 34_000);
  b.add({ t: 19_950, flags: ["standKey"], bytes: 1 });
  b.add({ t: 20_800, flags: ["lieKey"], bytes: 1 });
  b.frames.sort((x, y) => x.t - y.t);
  const out = selectEventFrames(b.frames, ev, { preMs: 10_000, postMs: 10_000 });
  const by = (r) => out.filter((f) => f.role === r);
  assert.equal(by("standing").length, 1);
  assert.equal(by("standing")[0].t, 19_950);
  assert.equal(by("laid").length, 1);
  assert.equal(by("laid")[0].t, 20_800);
  assert.ok(by("pre").length >= 19 && by("post").length >= 19);
  assert.ok(out[0].t >= 10_000 && out.at(-1).t <= 33_800);
  assert.equal(new Set(out.map((f) => f.name)).size, out.length);
});

test("over the image cap, pre/post are thinned but key + burst are kept", () => {
  const b = buffer(0, 34_000, 100);
  const out = selectEventFrames(b.frames, ev, { preMs: 10_000, postMs: 10_000, maxImages: 60 });
  assert.ok(out.length <= 60, `${out.length}`);
  assert.equal(out.filter((f) => f.role === "standing").length, 1);
  assert.equal(out.filter((f) => f.role === "laid").length, 1);
  const burst = b.frames.filter((f) => f.t > ev.standT && f.t <= ev.confirmedT).length;
  assert.ok(out.filter((f) => f.role === "burst").length >= burst - 2);
});
