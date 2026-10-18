// Score the phone's fall detector on keypoint files made by extract_keypoints.py.
//   node eval/evaluate.mjs data/keypoints [--confirm-ms 3000] [--fps 5]
//
// Runs exactly the detector code the phone runs (phone/detector.js), sampled at
// the phone's analysis rate. Reports detected falls, false alarms and timings.
//
// UR Fall clips often end only 1-3 s after the person lands, shorter than the
// phone's 3 s "stays down" check. The default here therefore also counts a fall
// as caught if it reached the "possible fall" stage before the clip ended;
// --strict counts only fully confirmed events.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { FallDetector } from "../phone/detector.js";

const args = process.argv.slice(2);
const dir = args.find((a) => !a.startsWith("--")) || "data/keypoints";
const opt = (n, d) => { const i = args.indexOf(n); return i >= 0 ? Number(args[i + 1]) : d; };
const fps = opt("--fps", 5);
const confirmMs = opt("--confirm-ms", 3000);
const strict = args.includes("--strict");

const rows = [];
for (const f of readdirSync(dir).filter((f) => f.endsWith(".json")).sort()) {
  const seq = JSON.parse(readFileSync(join(dir, f), "utf8"));
  const d = new FallDetector({ confirmMs });
  const step = 1000 / fps;
  let next = 0, confirmed = null, candidateAt = null, seen = 0, used = 0;
  for (const fr of seq.frames) {
    if (fr.t < next) continue;
    next = fr.t + step;
    used++;
    if (fr.lms) seen++;
    const r = d.update(fr.t, fr.lms, seq.aspect || 4 / 3);
    if (r.phase === "candidate" && candidateAt === null) candidateAt = fr.t;
    if (r.event && !confirmed) confirmed = r.event;
  }
  const caught = strict ? !!confirmed : !!(confirmed || candidateAt !== null);
  rows.push({ name: seq.name, label: seq.label, caught, confirmed: !!confirmed, candidateAt,
              descentMs: confirmed?.descentMs ?? null, side: confirmed?.landingSide ?? null,
              seenPct: Math.round((100 * seen) / Math.max(used, 1)) });
}

const falls = rows.filter((r) => r.label === "fall"), adl = rows.filter((r) => r.label !== "fall");
const tp = falls.filter((r) => r.caught).length, fp = adl.filter((r) => r.caught).length;
console.log(`\nFallWatch detector on ${rows.length} sequences (${fps} checks/s, stays-down ${confirmMs} ms, ${strict ? "strict" : "possible-or-confirmed"})\n`);
console.table(rows.map((r) => ({ seq: r.name, label: r.label, caught: r.caught ? "yes" : "", confirmed: r.confirmed ? "yes" : "",
  "descent s": r.descentMs ? (r.descentMs / 1000).toFixed(1) : "", side: r.side || "", "person seen %": r.seenPct })));
const pct = (a, b) => (b ? `${((100 * a) / b).toFixed(1)}%` : "n/a");
console.log(`Falls caught (recall):            ${tp}/${falls.length} = ${pct(tp, falls.length)}`);
console.log(`Everyday clips flagged (false +): ${fp}/${adl.length} = ${pct(fp, adl.length)}`);
const acc = (tp + (adl.length - fp)) / Math.max(rows.length, 1);
console.log(`Accuracy:                         ${(100 * acc).toFixed(1)}%`);
