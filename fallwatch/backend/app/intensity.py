"""Heuristic image-plane motion intensity, not injury severity or physical impact force."""
import math


def finite(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def visible(row):
    points = row.get("k")
    if not isinstance(points, list) or len(points) < 5:
        return False
    def point(index):
        p = points[index]
        return isinstance(p, list) and len(p) >= 3 and all(finite(x) for x in p[:3]) and 0 <= p[0] <= 1 and 0 <= p[1] <= 1 and p[2] >= .5
    return (point(1) and point(3)) or (point(2) and point(4))


def classify_intensity(descriptor, rows):
    unknown = {"level": "insufficient_visibility", "label": "Insufficient visibility",
               "assumed_relative_reduction": None, "speed_body_heights_per_second": None,
               "descent_ms": None, "max_pose_gap_ms": None,
               "reason": "A continuous, visible standing-to-floor transition is required.",
               "observation_quality": "insufficient", "method": "pose_descent_v2"}
    if not isinstance(descriptor, dict) or not isinstance(rows, list):
        return unknown
    start, end, duration = (descriptor.get(k) for k in ("standT", "lieT", "descentMs"))
    drop, speed = (descriptor.get(k) for k in ("hipDropBodyHeights", "hipSpeedBodyHeightsPerSec"))
    if not all(finite(x) for x in (start, end, duration, drop, speed)):
        return unknown
    if not (100 <= duration <= 2000 and abs((end - start) - duration) <= 2 and .15 <= drop <= 2):
        return unknown
    measured_speed = drop / (duration / 1000)
    if abs(speed - measured_speed) > max(.02, measured_speed * .05):
        return unknown
    if any(not isinstance(r, dict) or not finite(r.get("t")) for r in rows):
        return unknown
    segment = [r for r in rows if start <= r["t"] <= end]
    segment.sort(key=lambda r: r["t"])
    if len(segment) < 3 or segment[0]["t"] != start or segment[-1]["t"] != end:
        return unknown
    if any(b["t"] <= a["t"] for a, b in zip(segment, segment[1:])):
        return unknown
    total_samples = len(segment)
    segment = [r for r in segment if finite(r.get("h")) and r.get("p") != "unknown" and visible(r)]
    if len(segment) < 3 or len(segment) / total_samples < .5 or segment[0]["t"] != start or segment[-1]["t"] != end:
        return unknown
    if segment[0].get("p") != "standing" or segment[-1].get("p") != "lying":
        return unknown
    if segment[-1]["h"] <= segment[0]["h"]:
        return unknown
    gaps = [b["t"] - a["t"] for a, b in zip(segment, segment[1:])]
    if min(gaps) <= 0 or max(gaps) > min(500, duration * .75):
        return unknown
    limited = len(segment) != total_samples or max(gaps) > 250
    if measured_speed >= 1:
        level, label, reduction = "high", "High fall intensity", .35
    elif measured_speed >= .4:
        level, label, reduction = "medium", "Medium fall intensity", .25
    else:
        level, label, reduction = "low", "Low fall intensity", .10
    if limited:
        label = f"Estimated {label.lower()}"
    return {"level": level, "label": label, "assumed_relative_reduction": reduction,
            "speed_body_heights_per_second": round(measured_speed, 3), "descent_ms": duration,
            "max_pose_gap_ms": max(gaps), "reason": "Observed pose descent speed and duration.",
            "visible_pose_samples": len(segment), "total_pose_samples": total_samples,
            "observation_quality": "limited_visibility" if limited else "continuous",
            "method": "pose_descent_v2"}
