# ClaimClock: handoff for ML and evaluation

## What you have

The big source CSV is the public claims export. `cleaning_data.py` cleans it
and creates a 2016–2025 audit cohort. You do not need to rerun it to start ML.

`prepare_model_data.py` has already made smaller files in `data/modeling/draft`:

| File | Purpose | Rows |
|---|---|---:|
| train.csv.gz | Fit model and preprocessing | 206,460 |
| validation.csv.gz | Compare models and tune settings | 206,377 |
| test.csv.gz | Final evaluation | 268,295 |
| manifest.json | Feature names, target, and assumptions | |

Pandas reads `.csv.gz` directly. All three splits together are about 7 MB.
Train is 2023, validation is 2024, test is 2025. Late-year training and
validation claims are removed so their 90-day outcomes do not overlap the next year.

## Run the starter

From the project directory:

```powershell
python -m pip install pandas numpy scikit-learn
python train_baseline.py
```

The starter fits a logistic regression using only the manifest's eight features.
It compares the model against a private-carrier rule at the same 10% contact
budget. Ties are broken with seed 42, including the rule's large tied group.
It writes metrics and prediction files to `data/modeling/baseline_results`.
Existing baseline results are overwritten when rerun.

## Your modeling task

Predict `disputed_within_90d`: 1 means a dispute was recorded 1–90 days after
assembly. This is not a prediction of every eventual dispute.

Use `manifest.json` -> `features` as X. Claim identifier and assembly date
are metadata. Fit all preprocessing on training data only. Keep code fields
as strings. Do not add attorney, hearing, current status, highest process,
controverted date, or other outcome fields from the larger CSV.

Start by reproducing the baseline. Try your MLP or another tabular model next,
using validation for every modeling choice. The starter's test result is already
visible; treat it as a benchmark and do not tune against it. A fresh future
holdout would be needed for a stronger independent assessment.

## Metrics for the demo

- Recall at 10%: among all disputed claims, how many did our contact list catch?
- Precision at 10%: among workers selected for contact, how many were disputed?
- Lift: precision divided by the overall dispute rate.
- Compare both models with exactly the same contact budget.

Random contact should capture about 10% of disputes on average. Accuracy is
misleading because about 95% of these claims are negative.

Lead time is not yet calculated for model-selected workers: the modeling files
deliberately exclude dispute dates. To add it, join prediction IDs back to the
audit cohort for evaluation only and calculate dispute date minus assembly date
among selected true positives. Do not feed that joined information into X.
The manifest's lead-time summary is cohort-level, not model-specific.

## What to say honestly

"This is a retrospective hackathon prototype predicting recorded disputes within
90 days. We use time-based splits and exclude obvious outcome fields. Historical
feature availability and outcome completeness still need validation with the Board."

The current public snapshot may contain later revisions to injury, industry,
county, or carrier fields. Missing dispute dates are provisional negatives.
The 2023–2025 restriction avoids pooling across the reported 2022 change, but
does not verify its definition. Do not present these scores as deployment-ready
or claim that worker outreach improves outcomes from this experiment alone.
