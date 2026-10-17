from datetime import datetime
from pathlib import Path

import pandas as pd


# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

INPUT_PATH = BASE_DIR / (
    "Assembled_Workers'_Compensation_Claims___Beginning_2000_20261017.csv"
)

OUTPUT_DIR = BASE_DIR / "data" / "processed"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

COHORT_START = "2016-01-01"
COHORT_END = "2025-12-31"
TARGET_WINDOW_DAYS = 90

# This cohort remains provisional until the source's actual
# outcome coverage cutoff and reporting completeness are verified.
# Latest assembly date alone does not establish that cutoff.


def save_csv(frame, filename, include_index=False):
    """Save a CSV; use another name if Windows locks the original."""
    path = OUTPUT_DIR / filename

    try:
        frame.to_csv(
            path,
            index=include_index,
            date_format="%Y-%m-%d",
            chunksize=100_000,
        )
    except PermissionError:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = path.with_name(
            f"{path.stem}_{timestamp}{path.suffix}"
        )

        frame.to_csv(
            path,
            index=include_index,
            date_format="%Y-%m-%d",
            chunksize=100_000,
            mode="x",
        )

    print("Saved:", path)
    return path


def main():
    # --------------------------------------------------------
    # 1. Read the original data.
    # Preserve identifiers, codes, and ZIP codes as strings.
    # --------------------------------------------------------

    print("Reading:", INPUT_PATH)

    df = pd.read_csv(
        INPUT_PATH,
        dtype={
            "Claim Identifier": "string",
            "Industry Code": "string",
            "WCIO Cause of Injury Code": "string",
            "WCIO Nature of Injury Code": "string",
            "WCIO Part Of Body Code": "string",
            "OIICS Event Exposure Code": "string",
            "OIICS Injury Source Code": "string",
            "OIICS Nature of Injury Code": "string",
            "OIICS Part Of Body Code": "string",
            "OIICS Secondary Source Code": "string",
            "Zip Code": "string",
        },
        low_memory=False,
    )

    print("Original dataset shape:", df.shape)

    # --------------------------------------------------------
    # 2. Standardize column names and trim text.
    # --------------------------------------------------------

    df.columns = (
        df.columns.str.strip()
        .str.lower()
        .str.replace(r"[^a-z0-9]+", "_", regex=True)
        .str.strip("_")
    )

    if df.columns.duplicated().any():
        raise ValueError("Duplicate column names after normalization.")

    text_columns = df.select_dtypes(
        include=["object", "string"]
    ).columns

    for column in text_columns:
        df[column] = (
            df[column]
            .astype("string")
            .str.strip()
            .replace("", None)
        )

    # Preserve placeholders such as UNKNO until reviewed.
    # Preserve code leading zeros and OIICS asterisks.

    # --------------------------------------------------------
    # 3. Parse dates ONCE, supporting both known formats.
    # --------------------------------------------------------

    date_columns = [
        column for column in df.columns
        if column.endswith("_date")
    ]

    date_audit_rows = []

    print("\nDATE PARSING AUDIT")

    for column in date_columns:
        original = df[column].astype("string").str.strip()

        parsed = pd.to_datetime(
            original,
            format="%m/%d/%Y",
            errors="coerce",
        )

        remaining = original.notna() & parsed.isna()

        parsed.loc[remaining] = pd.to_datetime(
            original.loc[remaining],
            format="%Y-%m-%d",
            errors="coerce",
        )

        invalid = original.notna() & parsed.isna()

        date_audit_rows.append({
            "column": column,
            "original_populated": int(original.notna().sum()),
            "parsed_count": int(parsed.notna().sum()),
            "invalid_count": int(invalid.sum()),
        })

        print(
            f"{column}: "
            f"{original.notna().sum():,} populated, "
            f"{parsed.notna().sum():,} parsed, "
            f"{invalid.sum():,} invalid"
        )

        if invalid.any():
            print(
                "Invalid examples:",
                original.loc[invalid].head(10).tolist(),
            )
            raise ValueError(
                f"Unresolved dates in {column}. "
                "Inspect them before continuing."
            )

        df[column] = parsed

    save_csv(
        pd.DataFrame(date_audit_rows),
        "date_parsing_audit.csv",
    )

    # --------------------------------------------------------
    # 4. Convert wages to numeric.
    # Preserve zero and extreme values for review.
    # --------------------------------------------------------

    wage_column = "average_weekly_wage_aww"

    wage_text = df[wage_column].str.replace(
        r"[$,\s]",
        "",
        regex=True,
    )

    wage_numeric = pd.to_numeric(
        wage_text,
        errors="coerce",
    )

    invalid_wage = (
        df[wage_column].notna()
        & wage_numeric.isna()
    )

    if invalid_wage.any():
        print(
            "Unparseable wage examples:",
            df.loc[invalid_wage, wage_column].head(10).tolist(),
        )
        raise ValueError("Unresolved wage values.")

    df[wage_column] = wage_numeric

    print("\nWAGE AUDIT")
    print("Zero wages:", f"{df[wage_column].eq(0).sum():,}")
    print("Negative wages:", f"{df[wage_column].lt(0).sum():,}")
    print(
        df[wage_column]
        .describe(percentiles=[0.5, 0.9, 0.99])
        .to_string()
    )

    # --------------------------------------------------------
    # 5. Validate claim identifiers.
    # Do not silently discard duplicate claims.
    # --------------------------------------------------------

    missing_ids = df["claim_identifier"].isna()
    duplicate_ids = df["claim_identifier"].duplicated(keep=False)

    print("\nIDENTIFIER AUDIT")
    print("Missing IDs:", f"{missing_ids.sum():,}")
    print("Rows sharing an ID:", f"{duplicate_ids.sum():,}")

    if missing_ids.any() or duplicate_ids.any():
        save_csv(
            df.loc[
                missing_ids | duplicate_ids,
                [
                    "claim_identifier",
                    "assembly_date",
                    "controverted_date",
                ],
            ],
            "claim_identifier_issues.csv",
        )
        raise ValueError(
            "Resolve missing or duplicate claim IDs before continuing."
        )

    if df["assembly_date"].isna().any():
        raise ValueError(
            "Missing assembly dates require review before cohort creation."
        )

    # --------------------------------------------------------
    # 6. Calculate dispute timing.
    # These columns are outcomes/audit fields, NOT predictors.
    # --------------------------------------------------------

    assembly = df["assembly_date"]
    dispute = df["controverted_date"]

    df["dispute_timing"] = pd.Series(
        "no_recorded_dispute",
        index=df.index,
        dtype="string",
    )

    df.loc[
        dispute.lt(assembly),
        "dispute_timing",
    ] = "before_assembly"

    df.loc[
        dispute.eq(assembly),
        "dispute_timing",
    ] = "same_day_as_assembly"

    df.loc[
        dispute.gt(assembly),
        "dispute_timing",
    ] = "after_assembly"

    df["days_assembly_to_dispute"] = (
        dispute - assembly
    ).dt.days

    print("\nDISPUTE TIMING")
    timing_report = (
        df["dispute_timing"]
        .value_counts(dropna=False)
        .rename_axis("dispute_timing")
        .reset_index(name="claim_count")
    )
    print(timing_report.to_string(index=False))
    save_csv(timing_report, "dispute_timing_report.csv")

    print("\nPOSITIVE LEAD TIME — ALL YEARS")
    print(
        df.loc[
            df["dispute_timing"].eq("after_assembly"),
            "days_assembly_to_dispute",
        ]
        .describe(percentiles=[0.1, 0.25, 0.5, 0.75, 0.9])
        .to_string()
    )

    # --------------------------------------------------------
    # 7. Calculate accident timing.
    # Retain records; mask invalid negative duration features.
    # --------------------------------------------------------

    df["days_accident_to_assembly"] = (
        assembly - df["accident_date"]
    ).dt.days

    df["flag_accident_after_assembly"] = (
        df["accident_date"].gt(assembly)
    )

    df["days_accident_to_assembly_clean"] = (
        df["days_accident_to_assembly"].mask(
            df["flag_accident_after_assembly"]
        )
    )

    print("\nOTHER DATE CHECKS")
    print("Earliest assembly:", assembly.min())
    print("Latest assembly:", assembly.max())
    print(
        "Accident after assembly:",
        f"{df['flag_accident_after_assembly'].sum():,}",
    )

    # --------------------------------------------------------
    # 8. Flag numeric values that need interpretation.
    # These flags do not establish that values are invalid.
    # --------------------------------------------------------

    df["flag_zero_wage"] = df[wage_column].eq(0)
    df["flag_zero_birth_year"] = df["birth_year"].eq(0)
    df["flag_zero_age"] = df["age_at_injury"].eq(0)

    print("\nAGE AND BIRTH YEAR")
    print(
        df[["age_at_injury", "birth_year"]]
        .describe()
        .to_string()
    )

    print("\nCOMMON ZIP VALUES")
    print(
        df["zip_code"]
        .value_counts(dropna=False)
        .head(15)
        .to_string()
    )

    # --------------------------------------------------------
    # 9. Save full-dataset missingness and coding coverage.
    # --------------------------------------------------------

    missing_report = pd.DataFrame({
        "missing_count": df.isna().sum(),
        "missing_percent": (
            df.isna().mean().mul(100).round(2)
        ),
    }).sort_values("missing_percent", ascending=False)

    print("\nFULL-DATASET MISSING VALUES")
    print(missing_report.to_string())

    save_csv(
        missing_report.rename_axis("column").reset_index(),
        "missing_value_report.csv",
    )

    coverage_columns = [
        "oiics_nature_of_injury_code",
        "wcio_nature_of_injury_code",
        "industry_code",
    ]

    # Group a compact availability table to avoid copying
    # the entire dataset just to calculate yearly coverage.
    available = df[coverage_columns].notna()

    coverage = (
        available.groupby(df["assembly_date"].dt.year)
        .mean()
        .mul(100)
        .round(1)
    )
    coverage.index.name = "assembly_year"

    print("\nNONMISSING COVERAGE BY ASSEMBLY YEAR (%)")
    print(coverage.to_string())

    save_csv(
        coverage.reset_index(),
        "coding_coverage_by_year.csv",
    )

    # --------------------------------------------------------
    # 10. Create the provisional 2016–2025 cohort.
    # Exclude disputes on/before assembly.
    # --------------------------------------------------------

    in_period = assembly.between(
        COHORT_START,
        COHORT_END,
    )

    already_disputed = (
        dispute.notna()
        & dispute.le(assembly)
    )

    eligible = in_period & ~already_disputed

    model_df = df.loc[eligible].copy()

    model_df["disputed_within_90d"] = (
        model_df["days_assembly_to_dispute"].gt(0)
        & model_df["days_assembly_to_dispute"].le(
            TARGET_WINDOW_DAYS
        )
    ).astype("int8")

    # A dispute after day 90 is negative for this target.
    # Missing dispute dates are provisional negatives,
    # conditional on sufficient outcome coverage.

    print("\nPROVISIONAL MODELING COHORT")
    print("Period:", COHORT_START, "through", COHORT_END)
    print("Claims in period:", f"{in_period.sum():,}")
    print(
        "Already disputed in period:",
        f"{(in_period & already_disputed).sum():,}",
    )
    print("Eligible claims:", f"{len(model_df):,}")
    print(model_df["disputed_within_90d"].value_counts())

    print(
        "90-day dispute rate:",
        f"{model_df['disputed_within_90d'].mean():.2%}",
    )

    flag_columns = [
        "flag_zero_wage",
        "flag_zero_birth_year",
        "flag_zero_age",
        "flag_accident_after_assembly",
    ]

    quality_report = (
        model_df[flag_columns]
        .sum()
        .rename_axis("flag")
        .reset_index(name="claim_count")
    )

    print("\nCOHORT QUALITY FLAGS")
    print(quality_report.to_string(index=False))
    save_csv(quality_report, "cohort_quality_flags.csv")

    cohort_summary = (
        model_df.assign(
            assembly_year=model_df["assembly_date"].dt.year
        )
        .groupby("assembly_year")
        .agg(
            claim_count=("claim_identifier", "size"),
            disputed_within_90d_count=(
                "disputed_within_90d", "sum"
            ),
            dispute_rate=("disputed_within_90d", "mean"),
        )
        .reset_index()
    )

    print("\nCOHORT SUMMARY BY YEAR")
    print(cohort_summary.to_string(index=False))
    save_csv(cohort_summary, "cohort_summary_by_year.csv")

    cohort_missing = pd.DataFrame({
        "missing_count": model_df.isna().sum(),
        "missing_percent": (
            model_df.isna().mean().mul(100).round(2)
        ),
    }).sort_values("missing_percent", ascending=False)

    save_csv(
        cohort_missing.rename_axis("column").reset_index(),
        "cohort_missing_value_report.csv",
    )

    # --------------------------------------------------------
    # 11. Save the cohort for audit and feature selection.
    # IMPORTANT: this contains outcome columns.
    # Do not train on every column except the target.
    # --------------------------------------------------------

    save_csv(
        model_df,
        "claimclock_cohort_provisional.csv",
    )

    print("\nFinished.")
    print(
        "Use an explicit predictor list for ML. "
        "Exclude dispute outcomes, hearing/process fields, "
        "and other information unavailable at assembly."
    )
    print(
        "Confirm the source outcome cutoff before treating "
        "the target labels as final."
    )


if __name__ == "__main__":
    main()