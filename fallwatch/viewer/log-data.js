export function isoTimestamp(ms) {
  const date = new Date(ms);
  return Number.isFinite(date.getTime()) ? date.toISOString() : null;
}

export function localDayRange(date = new Date()) {
  const start = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const end = new Date(start);
  end.setDate(end.getDate() + 1);
  return { start: start.getTime(), end: end.getTime() };
}

export function summarizeDay(events, start, end) {
  const hours = Array(24).fill(0);
  let count = 0, totalBytes = 0;
  for (const event of events) {
    if (event.detected_at_ms < start || event.detected_at_ms >= end) continue;
    count++;
    totalBytes += event.total_bytes || 0;
    hours[new Date(event.detected_at_ms).getHours()]++;
  }
  return { count, totalBytes, hours };
}

export function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const power = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${Number((bytes / 1024 ** power).toFixed(1))} ${units[power]}`;
}
