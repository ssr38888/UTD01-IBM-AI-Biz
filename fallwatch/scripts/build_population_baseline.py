"""Compute the no-input baseline from training labels only."""
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path


def build(source: Path, destination: Path):
    count = disputes = 0
    with gzip.open(source, "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            label = row["disputed_within_90d"]
            if label not in {"0", "1"}:
                raise ValueError("Training labels must be binary")
            count += 1
            disputes += int(label)
    if not count:
        raise ValueError("No training records")
    result = {"probability": disputes / count, "training_claims": count,
              "recorded_disputes": disputes, "period": "2023",
              "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "target": "recorded_dispute_within_90_days"}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("training_csv", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.training_csv, args.output)
