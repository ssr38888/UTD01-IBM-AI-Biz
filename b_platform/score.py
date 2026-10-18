"""Score the 2025 test year on the Telum chip (CPU fallback) and write contract-3 scores.csv.

TORCH_NNPA_DEBUG=1 python b_platform/score.py [--device cpu]
Reasons: knock each input field out (one-hot -> all zeros, days -> average) and
report the fields whose removal lowers the score most. Model-agnostic, 9 passes.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from b_platform.model import LABEL, MANIFEST, NUMERIC, OUT, blocks, device, encode, load, load_model, predict  # noqa: E402

CODES = json.loads((Path(__file__).resolve().parent.parent / "c_product" / "wcio_codes.json").read_text())
LABELS = {
    "industry_code": ("Industry", "industry"), "wcio_cause_of_injury_code": ("Cause", "cause"),
    "wcio_nature_of_injury_code": ("Injury", "nature"), "wcio_part_of_body_code": ("Body part", "body_part"),
    "county_of_injury": ("County", None), "carrier_type": ("Carrier", None), "assembly_month": ("Filed in month", None),
}
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def reason_text(field, row):
    if field == NUMERIC:
        days = row[NUMERIC]
        return "Accident date unknown" if pd.isna(days) or days < 0 else f"Filed {int(days)} days after accident"
    label, table = LABELS[field]
    value = row[field]
    if value == "MISSING":
        return f"{label}: not reported"
    if field == "assembly_month":
        return f"Filed in {MONTHS[int(value) - 1]}"
    return f"{label}: {CODES[table].get(value, 'code ' + value) if table else value}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", choices=["cpu", "nnpa"])
    parser.add_argument("--split", default="test")
    args = parser.parse_args()
    dev = device(args.device)
    model, meta = load_model(dev)
    spec = json.loads((OUT / "features.json").read_text())
    frame = load(args.split)
    X = encode(frame, spec)

    start = time.perf_counter()
    score = predict(model, X, dev)
    seconds = time.perf_counter() - start
    print(f"scored {len(X):,} claims on {dev} in {seconds:.2f}s ({len(X) / seconds:,.0f} claims/s)")

    drops = []
    for field, sl in blocks(spec).items():
        knocked = X.copy()
        knocked[:, sl] = 0
        drops.append(score - predict(model, knocked, dev))
    drops = np.stack(drops, axis=1)
    fields = list(blocks(spec))
    order = np.argsort(-drops, axis=1)[:, :3]

    tops = [[], [], []]
    for i, row in enumerate(frame.to_dict("records")):
        for j in range(3):
            f = order[i, j]
            tops[j].append(reason_text(fields[f], row) if drops[i, f] > 0 else "")

    out = pd.DataFrame({
        "claim_id": frame["claim_identifier"], "assembly_date": frame["assembly_date"],
        "score": score.round(5), "rank_pct": (pd.Series(score).rank(pct=True) * 100).round(2),
        "top1": tops[0], "top2": tops[1], "top3": tops[2],
        "lead_days_est": int(MANIFEST["positive_lead_days_quantiles"]["0.5"]),  # cohort median, not per claim
        "county": frame["county_of_injury"], "carrier_type": frame["carrier_type"],
        "industry_code": frame["industry_code"], "cause_code": frame["wcio_cause_of_injury_code"],
        "nature_code": frame["wcio_nature_of_injury_code"], "body_part_code": frame["wcio_part_of_body_code"],
        "days_accident_to_assembly": frame[NUMERIC], "y": frame[LABEL],
    })
    out.to_csv(OUT / "scores.csv", index=False)
    meta.update(device=dev, scored_claims=len(X), score_seconds=round(seconds, 3))
    (OUT / "model.json").write_text(json.dumps(meta, indent=1))
    print(f"wrote {OUT / 'scores.csv'}")


if __name__ == "__main__":
    main()
