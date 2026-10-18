import { test } from "node:test";
import assert from "node:assert/strict";
import { localDayRange, summarizeDay, formatBytes, isoTimestamp } from "../viewer/log-data.js";

test("invalid saved timestamps cannot crash the history renderer", () => {
  assert.equal(isoTimestamp(8640000000000001), null);
  assert.equal(isoTimestamp(NaN), null);
  assert.equal(isoTimestamp(0), "1970-01-01T00:00:00.000Z");
});

process.env.TZ = "America/Chicago";

test("today uses local midnight boundaries including a 25-hour DST day", () => {
  const { start, end } = localDayRange(new Date(2026, 10, 1, 15));
  assert.equal(new Date(start).getHours(), 0);
  assert.equal(new Date(end).getDate(), 2);
  assert.equal(end - start, 25 * 3600000);
});

test("daily count, storage and hourly series use only events inside the day", () => {
  const { start, end } = localDayRange(new Date(2026, 9, 17, 15));
  const events = [
    { detected_at_ms: start - 1, total_bytes: 999 },
    { detected_at_ms: start, total_bytes: 100 },
    { detected_at_ms: new Date(2026, 9, 17, 14, 30).getTime(), total_bytes: 200 },
    { detected_at_ms: end, total_bytes: 999 },
  ];
  const result = summarizeDay(events, start, end);
  assert.equal(result.count, 2);
  assert.equal(result.totalBytes, 300);
  assert.equal(result.hours[0], 1);
  assert.equal(result.hours[14], 1);
  assert.equal(result.hours.reduce((a, b) => a + b), 2);
});

test("an empty day is zero and storage is formatted without inventing usage", () => {
  assert.deepEqual(summarizeDay([], 0, 1000), { count: 0, totalBytes: 0, hours: Array(24).fill(0) });
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(1536), "1.5 KB");
});
