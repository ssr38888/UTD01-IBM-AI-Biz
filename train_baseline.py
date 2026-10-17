"""Run a reproducible hackathon baseline and fixed-budget evaluation."""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import joblib
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

BASE = Path(__file__).resolve().parent
DATA = BASE / "data" / "modeling" / "draft"
OUTPUT = BASE / "data" / "modeling" / "baseline_results"


def evaluate(y, scores):
    """Exactly ceil(10% of claims); fixed seed breaks equal-score ties fairly."""
    y = np.asarray(y)
    budget = math.ceil(len(y) * .10)
    ties = np.random.default_rng(42).random(len(y))
    selected = np.lexsort((ties, -np.asarray(scores)))[:budget]
    caught = int(y[selected].sum())
    rate = float(y.mean())
    return {
        "claims": len(y), "disputed_claims": int(y.sum()),
        "contact_budget": budget, "disputed_claims_reached": caught,
        "recall_at_10pct": caught / int(y.sum()) if y.sum() else None,
        "precision_at_10pct": caught / budget,
        "lift_over_random": (caught / budget) / rate if rate else None,
        "roc_auc": float(roc_auc_score(y, scores)),
        "average_precision": float(average_precision_score(y, scores)),
    }


def main():
    manifest = json.loads((DATA / "manifest.json").read_text())
    features = manifest["features"]
    categorical = features[:6]
    numeric = features[6:]
    def load(split):
        f = pd.read_csv(DATA / f"{split}.csv.gz", dtype={c: str for c in categorical})
        f[categorical] = f[categorical].fillna("MISSING")
        return f
    train = load("train")
    preprocessing = ColumnTransformer([
        ("category", OneHotEncoder(handle_unknown="ignore"), categorical),
        ("numeric", make_pipeline(SimpleImputer(strategy="median"), StandardScaler()), numeric),
    ])
    model = make_pipeline(preprocessing, LogisticRegression(solver="liblinear", max_iter=500, random_state=42))
    model.fit(train[features], train["disputed_within_90d"])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "features": features}, OUTPUT / "model.joblib")
    results = {"status": "Provisional hackathon benchmark; availability at assembly and outcome completeness unverified", "metrics": {}}
    for split in ["validation", "test"]:
        frame = load(split)
        y = frame["disputed_within_90d"].to_numpy()
        scores = model.predict_proba(frame[features])[:, 1]
        # Private carrier alone prioritizes a large tied group. The evaluator
        # randomly selects within that group using a reproducible seed.
        rule = frame["carrier_type"].eq("1A. PRIVATE").astype(float).to_numpy()
        results["metrics"][split] = {
            "logistic_regression": evaluate(y, scores),
            "private_carrier_rule": evaluate(y, rule),
        }
        pd.DataFrame({
            "claim_identifier": frame["claim_identifier"],
            "assembly_date": frame["assembly_date"],
            "actual_disputed_within_90d": y,
            "predicted_dispute_probability": scores,
        }).to_csv(OUTPUT / f"{split}_predictions.csv.gz", index=False, compression="gzip")
    (OUTPUT / "metrics.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    print(f"Results: {OUTPUT}")


if __name__ == "__main__":
    main()
