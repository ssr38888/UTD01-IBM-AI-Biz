# ClaimClock prototype

Predict the risk of a **recorded insurer dispute within 90 days after claim
assembly**, to prioritize early worker outreach. A dispute is not final denial,
and a low score does not establish acceptance. This repository does not model
final insurer accept/decline decisions.

## Quick start

Python 3.11–3.13:

```powershell
python -m pip install -r requirements.txt
python train_baseline.py
python score_claim.py examples/claim.json
```

The small compressed draft datasets are included. The multi-gigabyte original
and cleaned audit export are excluded. The training command regenerates the
local model artifact, metrics, and per-claim predictions. Only load model
artifacts you trust; joblib files can execute code when loaded.

## For the mainframe teammate

`examples/claim.json` is a synthetic request showing the eight input fields.
Categorical fields are strings (preserve leading zeros), or null if unknown.
Accident-to-assembly duration is a nonnegative number or null. Assembly month
is an integer from 1 to 12. All inputs must come from the record as it existed
at assembly, without later updates.

`score_claim.py` returns JSON with `dispute_within_90d_probability` and a
prototype status. Rank a batch by probability to select the top 10% for
outreach; probability 0.5 is not the evaluation threshold. The reference scorer
runs locally in Python. It demonstrates the integration contract, not deployed
Db2 scoring, NNPA acceleration, or mainframe performance. The trained baseline
is logistic regression; an MLP can replace it behind the same input contract.

## Results and handoff

See [ML_HANDOFF.md](ML_HANDOFF.md) for the ML task and [MODELING.md](MODELING.md)
for cohort rules. On the provisional 2025 test set, the baseline catches 43.6%
of recorded 90-day disputes at a 10% contact budget, with 22.1% precision.
The private-carrier rule catches 9.7% at the same budget, with random seeded
selection among tied rule scores. Metrics are in
`data/modeling/baseline_results/metrics.json`.

Historical feature availability and outcome completeness remain unverified.
These are retrospective hackathon results, not production validation or
evidence that outreach changes outcomes. Use validation for model changes;
the existing test result has already been inspected.

## Rebuild from source

Download the CSV from the [NY public dataset](https://data.ny.gov/Government-Finance/Assembled-Workers-Compensation-Claims-Beginning-20/jshw-gkgu),
using the input filename configured in `cleaning_data.py`. Run the cleaning
script, then `prepare_model_data.py`. Existing modeling output directories
must be reviewed before rebuilding. The repository includes derived data from
that public export; it is not a live claims-system connection.
