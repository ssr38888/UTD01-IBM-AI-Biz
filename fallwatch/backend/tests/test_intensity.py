import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.intensity import classify_intensity


def observation(duration, drop):
    start = 1760000000000
    descriptor = {"standT": start, "lieT": start + duration, "descentMs": duration,
                  "hipDropBodyHeights": drop, "hipSpeedBodyHeightsPerSec": drop / (duration / 1000)}
    count = max(3, math.ceil(duration / 100) + 1)
    points = [[.5, .5, .9] for _ in range(9)]
    rows = [{"t": start + round(duration * i / (count - 1)),
             "p": "standing" if i == 0 else "lying" if i == count - 1 else "other",
             "h": .4 + .3 * i / (count - 1), "k": points} for i in range(count)]
    return descriptor, rows


@pytest.mark.parametrize("duration,drop,level,reduction", [
    (300, .45, "high", .35), (800, .4, "medium", .25), (1800, .3, "low", .10)])
def test_observed_intensity_levels(duration, drop, level, reduction):
    result = classify_intensity(*observation(duration, drop))
    assert result["level"] == level
    assert result["assumed_relative_reduction"] == reduction
    assert result["speed_body_heights_per_second"] == pytest.approx(drop / (duration / 1000), abs=.0005)


def test_fast_unobserved_transition_is_not_assigned_high_intensity():
    descriptor, rows = observation(300, .45)
    result = classify_intensity(descriptor, [rows[0], rows[-1]])
    assert result["level"] == "insufficient_visibility"
    assert result["assumed_relative_reduction"] is None


def test_brief_occlusion_preserves_an_estimated_intensity():
    descriptor, rows = observation(625, 1.71)
    for row in rows:
        if 0 < row["t"] - descriptor["standT"] < 400:
            row["p"] = "unknown"
            row["h"] = None
            row["k"] = None
    result = classify_intensity(descriptor, rows)
    assert result["level"] == "high"
    assert result["observation_quality"] == "limited_visibility"
    assert result["label"] == "Estimated high fall intensity"
    assert result["speed_body_heights_per_second"] == 2.736


def test_gaps_and_low_confidence_cannot_establish_intensity():
    descriptor, rows = observation(800, .4)
    assert classify_intensity(descriptor, rows[:1] + rows[-2:])["level"] == "insufficient_visibility"
    for row in rows:
        row["k"] = [[.5, .5, .1] for _ in range(9)]
    assert classify_intensity(descriptor, rows)["level"] == "insufficient_visibility"


@pytest.mark.parametrize("field,value", [("descentMs", 0), ("hipDropBodyHeights", float('nan')),
                                         ("hipSpeedBodyHeightsPerSec", 100)])
def test_invalid_or_inconsistent_measurements_are_unknown(field, value):
    descriptor, rows = observation(800, .4)
    descriptor[field] = value
    assert classify_intensity(descriptor, rows)["level"] == "insufficient_visibility"


def test_oversized_json_numbers_cannot_break_scoring():
    descriptor, rows = observation(800, .4)
    rows[2]["t"] = 10 ** 400
    assert classify_intensity(descriptor, rows)["level"] == "insufficient_visibility"
    descriptor["hipDropBodyHeights"] = 10 ** 400
    assert classify_intensity(descriptor, rows)["level"] == "insufficient_visibility"
