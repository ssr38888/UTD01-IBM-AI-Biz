"""Local integration reference: python score_claim.py examples/claim.json."""
import argparse
import json
from pathlib import Path

import joblib
import pandas as pd

BASE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("claim", type=Path)
    args = parser.parse_args()
    bundle = joblib.load(BASE / "data/modeling/baseline_results/model.joblib")
    payload = json.loads(args.claim.read_text(encoding="utf-8"))
    missing = set(bundle["features"]) - payload.keys()
    if missing:
        raise SystemExit(f"Missing input fields: {sorted(missing)}")
    frame = pd.DataFrame([{name: payload[name] for name in bundle["features"]}])
    for name in bundle["features"][:6]:
        if payload[name] is not None and not isinstance(payload[name], str):
            raise SystemExit(f"{name} must be a string or null; preserve code leading zeros")
        frame[name] = frame[name].fillna("MISSING")
    for name in bundle["features"][6:]:
        frame[name] = pd.to_numeric(frame[name], errors="raise").astype(float)
    duration = frame.loc[0, "days_accident_to_assembly"]
    if pd.notna(duration) and duration < 0:
        raise SystemExit("Negative accident-to-assembly duration: use null")
    month = frame.loc[0, "assembly_month"]
    if pd.isna(month) or not 1 <= month <= 12 or month != int(month):
        raise SystemExit("assembly_month must be an integer from 1 to 12")
    probability = float(bundle["model"].predict_proba(frame)[:, 1][0])
    print(json.dumps({
        "dispute_within_90d_probability": probability,
        "status": "provisional_research_prototype",
        "meaning": "Risk of a recorded dispute after assembly within 90 days; not a final claim acceptance or denial decision",
    }, indent=2))


if __name__ == "__main__":
    main()
