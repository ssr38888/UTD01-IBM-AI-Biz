# Provisional modeling dataset

Run `python prepare_model_data.py` to generate compressed draft train,
validation, and test CSVs under `data/modeling/draft`. The source audit CSV
is unchanged. The script reads it in chunks rather than loading it all at once.
Existing output directories must be reviewed and cleared manually before rerunning.

The target is a recorded dispute 1–90 days after assembly, rather than any
eventual dispute. No recorded dispute is provisionally treated as negative.
Claims disputed on or before assembly are excluded.

Training uses 2023, validation 2024, and testing 2025. Training and validation
exclude assembly dates whose 90-day label windows reach the following year.
This supports a January 1 evaluation boundary without using future training
outcomes. The recent period avoids pooling across the reported 2022 definition
change, whose details still need verification.

Load a split with `pd.read_csv(path, dtype={name: "string" for name in
manifest["features"][:6]})`, then select `X = frame[manifest["features"]]`
and `y = frame["disputed_within_90d"]`. Fit imputers, categorical encoders,
scalers, and models on training data only. Claim identifier and assembly date
are metadata, not predictors. Preserve categorical code leading zeros.

The manifest records row counts, class rates, lead-time quantiles, and the
explicit feature allowlist. Zero/negative accident-to-assembly durations are
handled differently: zero is valid, negative is missing. Missing categorical
values need a training-time missing-category policy.

Before reporting final performance:

- Verify which candidate fields actually existed at assembly. The latest
  public snapshot may contain later updates, even in injury and carrier fields.
- Establish an outcome completeness cutoff independently of download time.
  Then run `python prepare_model_data.py --outcome-cutoff YYYY-MM-DD` using
  that confirmed date. This restricts all claims to complete 90-day windows;
  the command itself cannot verify the date or reporting completeness.
- Confirm that the recent period uses a consistent dispute definition.
- Select model settings on validation data, then evaluate the test set once.
  Report recall and precision at a 10% outreach budget, a carrier-type rule
  baseline, and lead time among true positives selected for outreach.
- Inspect the exact carrier-type values before defining a private-carrier rule.
  An attorney-based baseline requires historical attorney information that
  this export does not establish.

The generated data is a research candidate, not evidence of deployment readiness.
