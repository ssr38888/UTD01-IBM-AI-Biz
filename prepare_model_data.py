"""Build a provisional assembly-day dataset without modifying the audit cohort."""

import argparse
import json
from pathlib import Path
from typing import TypedDict

import pandas as pd


BASE = Path(__file__).resolve().parent
FEATURES = [
    "industry_code", "wcio_cause_of_injury_code",
    "wcio_nature_of_injury_code", "wcio_part_of_body_code",
    "county_of_injury", "carrier_type", "days_accident_to_assembly",
    "assembly_month",
]
SOURCE_COLUMNS = FEATURES[:-2] + [
    "claim_identifier", "assembly_date", "accident_date", "controverted_date",
]


class SplitStats(TypedDict):
    rows: int
    positives: int
    positive_rate: float | None


def prepare_chunk(frame, cutoff=None):
    """Recompute labels and features; never trust exported outcome-derived features."""
    for name in ["assembly_date", "accident_date", "controverted_date"]:
        frame[name] = pd.to_datetime(frame[name], format="%Y-%m-%d", errors="raise")
    assembly = frame["assembly_date"]
    dispute = frame["controverted_date"]
    days = (dispute - assembly).dt.days
    eligible = assembly.between("2023-01-01", "2025-12-31") & (
        dispute.isna() | dispute.gt(assembly)
    )
    if cutoff is not None:
        eligible &= assembly.add(pd.Timedelta(days=90)).le(cutoff)
    frame = frame.loc[eligible].copy()
    days = days.loc[eligible]
    frame["disputed_within_90d"] = days.between(1, 90).astype("int8")
    duration = (frame["assembly_date"] - frame["accident_date"]).dt.days
    frame["days_accident_to_assembly"] = duration.mask(duration.lt(0))
    frame["assembly_month"] = frame["assembly_date"].dt.month
    for name in FEATURES[:-2]:
        frame[name] = frame[name].str.strip().replace("", pd.NA)
    # Training outcomes must be observable before validation begins, and
    # validation outcomes before the test year begins. Purge the last 90 days.
    year = frame["assembly_date"].dt.year
    mature = frame["assembly_date"] + pd.Timedelta(days=90)
    frame["split"] = pd.NA
    frame.loc[year.eq(2023) & mature.lt(pd.Timestamp("2024-01-01")), "split"] = "train"
    frame.loc[year.eq(2024) & mature.lt(pd.Timestamp("2025-01-01")), "split"] = "validation"
    frame.loc[year.eq(2025), "split"] = "test"
    frame["lead_days_audit_only"] = days.where(days.between(1, 90))
    return frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outcome-cutoff", help="Verified complete outcome date, YYYY-MM-DD; omit for draft outputs")
    args = parser.parse_args()
    cutoff = pd.Timestamp(args.outcome_cutoff) if args.outcome_cutoff else None
    output = BASE / "data" / "modeling" / ("cutoff_" + cutoff.strftime("%Y%m%d") if cutoff is not None else "draft")
    output.mkdir(parents=True, exist_ok=True)
    # Refuse to append to a previous run or silently overwrite its artifacts.
    manifest_path = output / "manifest.json"
    if any(output.iterdir()):
        raise SystemExit(f"Output directory is not empty: {output}. Use a new directory or review existing artifacts.")
    stats: dict[str, SplitStats] = {
        name: {"rows": 0, "positives": 0, "positive_rate": None}
        for name in ["train", "validation", "test"]
    }
    leads: list[float] = []
    excluded_gap = 0
    columns = ["claim_identifier", "assembly_date"] + FEATURES + ["disputed_within_90d"]
    source = BASE / "data" / "processed" / "claimclock_cohort_provisional.csv"
    for chunk in pd.read_csv(source, usecols=SOURCE_COLUMNS, dtype="string", chunksize=100_000):
        frame = prepare_chunk(chunk, cutoff)
        excluded_gap += int(frame["split"].isna().sum())
        for split, summary in stats.items():
            part = frame.loc[frame["split"].eq(split)]
            if part.empty:
                continue
            part[columns].to_csv(
                output / f"{split}.csv.gz", index=False,
                mode="w" if summary["rows"] == 0 else "a",
                header=summary["rows"] == 0, compression="gzip", date_format="%Y-%m-%d",
            )
            summary["rows"] += len(part)
            summary["positives"] += int(part["disputed_within_90d"].sum())
            leads.extend(float(value) for value in part["lead_days_audit_only"].dropna())
    for summary in stats.values():
        summary["positive_rate"] = summary["positives"] / summary["rows"] if summary["rows"] else None
    manifest = {
        "status": "provisional: feature availability at assembly is unverified",
        "source": str(source), "outcome_cutoff": args.outcome_cutoff,
        "labels_mature_verified": cutoff is not None,
        "target": "recorded dispute 1 through 90 days after assembly",
        "features": FEATURES,
        "metadata_not_predictors": ["claim_identifier", "assembly_date"],
        "target_column": "disputed_within_90d",
        "splits": stats, "rows_purged_at_split_boundaries": excluded_gap,
        "positive_lead_days_quantiles": pd.Series(leads, dtype="float64").quantile([0, .1, .25, .5, .75, .9, 1]).to_dict(),
        "limitations": [
            "Without a confirmed cutoff, test labels are draft and must not support final performance claims.",
            "Even a supplied cutoff requires independent verification of reporting completeness.",
            "Carrier, county, industry and injury codes may reflect updates after assembly; verify against source documentation or historical snapshots.",
            "2023-2025 restriction avoids pooling across the reported 2022 change, but does not verify stable definitions.",
            "Wages, attorney representation, current status, hearing/process fields and later dates are excluded.",
            "Use manifest features explicitly; never use identifiers or target as predictors.",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    print(f"Saved modeling artifacts to {output}")


if __name__ == "__main__":
    main()
