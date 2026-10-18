# Tune, blend and refit with the decided features
Label: `wayfinder:task` · type AFK · blocked by: 03, 04

## Question

With the feature set and metric decided, search tree settings on 2024 validation, choose the MLP blend weight on validation, refit on 2023+2024, score 2025 once, and write `b_platform/artifacts/claim_risk.csv` + metrics. Record the 95% intervals.

## Resolution (closed)

6 tree settings searched on 2024 validation (best: learning rate .05, 15 leaves, min 50 per leaf, 58.7%). MLP blend weight chosen = 0 (the MLP no longer helps once filing-date and insurer features exist). Refit on 2023+2024, 2025 scored once: recall@10% 58.4% (95% CI 57.8–59.3), precision 29.6%, AUC 0.868 (0.865–0.871), worst-decile calibration gap 0.45 points. Logistic baseline 43.6%. Simulated-feature scenario 61.3%: assumption-driven, not a result.
