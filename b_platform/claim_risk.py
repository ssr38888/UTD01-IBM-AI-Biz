"""Claim risk model: claim + industry baselines + claimant history + fall-video evidence.

python b_platform/claim_risk.py      (~2 min on a laptop CPU)

Two models, same real labels (recorded dispute within 90 days), same 2023/2024/2025 time split:
  real      - the 8 public claim fields + peer baselines (train-year dispute rate of the claim's
              industry, carrier, county, injury, body part, cause). Every number here is real.
  scenario  - real + SIMULATED claimant history and fall-video features. The public data has no
              person or video fields, so these are drawn per claim under the stated ASSUMPTIONS below.
              Its metrics show the pipeline works; they are not evidence about real claimants.
Error margins: 95% bootstrap intervals on the 2025 test year; per-claim ranges from a 5-model bag.
Writes b_platform/artifacts/claim_risk.csv (per-claim analytics for the dashboard) and claim_risk_metrics.json.
"""
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from train_baseline import evaluate  # noqa: E402  the teammate's recall@10% scorer, unchanged

DATA = ROOT / "data" / "modeling" / "draft"
OUT = ROOT / "b_platform" / "artifacts"
CODES = ["industry_code", "wcio_cause_of_injury_code", "wcio_nature_of_injury_code",
         "wcio_part_of_body_code", "county_of_injury", "carrier_type"]
PEERS = {"industry_code": "industry", "carrier_type": "carrier", "county_of_injury": "county",
         "wcio_nature_of_injury_code": "injury", "wcio_part_of_body_code": "body_part",
         "wcio_cause_of_injury_code": "cause"}
FALL_CAUSES = {"25", "26", "27", "28", "29", "31", "32", "33"}  # same rule as c_product/app.py
LEVELS = ["none", "low", "medium", "high"]
ASSUMPTIONS = {
    "prior_disputes_5y": "Poisson mean 0.10 if not disputed, 0.35 if disputed (repeat-dispute pattern)",
    "prior_claims_5y": "prior_disputes + Poisson mean 0.6",
    "fall_video": "fall-cause claim and md5(claim_id) % 3 == 0, label-independent (matches dashboard card)",
    "fall_intensity": "only with a fall video; P(low,medium,high) = .30/.35/.35 if not disputed, .45/.35/.20 if disputed "
                      "(a clearly recorded hard fall is harder to contest; mirrors FallWatch's 10-35% assumed reduction)",
}
SEED = 42


def load(split):
    f = pd.read_csv(DATA / f"{split}.csv.gz", dtype={c: str for c in CODES})
    f[CODES] = f[CODES].fillna("MISSING")
    return f


def peer_rates(train):
    """Smoothed dispute rate in the fitting years per category (shrunk toward the overall rate below ~200 claims)."""
    prior, k = train["disputed_within_90d"].mean(), 200
    rates = {}
    for col in PEERS:
        g = train.groupby(col)["disputed_within_90d"].agg(["sum", "count"])
        rates[col] = ((g["sum"] + k * prior) / (g["count"] + k)).to_dict()
    return rates, float(prior)


def simulate(frame):
    """SIMULATED claimant history + video features, conditioned on the real label (see ASSUMPTIONS)."""
    y = frame["disputed_within_90d"].to_numpy()
    seeds = [int(hashlib.md5(str(c).encode()).hexdigest(), 16) for c in frame["claim_identifier"]]
    rng = np.random.default_rng(SEED + len(frame))
    disputes = rng.poisson(np.where(y == 1, .35, .10))
    video = frame["wcio_cause_of_injury_code"].isin(FALL_CAUSES).to_numpy() & (np.array([s % 3 for s in seeds]) == 0)
    u = rng.random(len(frame))
    cut_lo, cut_mid = np.where(y == 1, .45, .30), np.where(y == 1, .80, .65)
    level = np.where(u < cut_lo, 1, np.where(u < cut_mid, 2, 3)) * video
    return pd.DataFrame({
        "prior_disputes_5y": disputes, "prior_claims_5y": disputes + rng.poisson(.6, len(frame)),
        "fall_video": video.astype(int), "fall_intensity": level,
        "descent_ms": np.where(video, np.select([level == 3, level == 2], [450, 900], 1500) + rng.normal(0, 120, len(frame)), np.nan),
    }, index=frame.index)


def features(frame, rates, prior, scenario):
    X = pd.DataFrame(index=frame.index)
    for col in CODES:
        X[col] = frame[col].astype("category")
    X["days_accident_to_assembly"] = pd.to_numeric(frame["days_accident_to_assembly"]).where(lambda d: d >= 0)
    X["assembly_month"] = frame["assembly_month"]
    for col, name in PEERS.items():
        X[f"peer_{name}"] = frame[col].map(rates[col]).fillna(prior).astype(float)
    if scenario:
        X = X.join(simulate(frame))
    return X


def align(X, cats):
    for col in CODES:
        X[col] = pd.Categorical(X[col].astype(str), categories=cats[col])
    return X


GRID = [  # small search, chosen on 2024 validation recall@10%
    {"learning_rate": .08, "max_leaf_nodes": 31, "min_samples_leaf": 100},
    {"learning_rate": .05, "max_leaf_nodes": 63, "min_samples_leaf": 200},
    {"learning_rate": .05, "max_leaf_nodes": 15, "min_samples_leaf": 50},
    {"learning_rate": .10, "max_leaf_nodes": 63, "min_samples_leaf": 400},
]


def fit(X, y, seed, sample=None, params=GRID[0]):
    model = HistGradientBoostingClassifier(max_iter=400, l2_regularization=1.0, categorical_features="from_dtype",
                                           early_stopping=True, validation_fraction=.1, random_state=seed, **params)
    idx = sample if sample is not None else slice(None)
    return model.fit(X.iloc[idx], y[idx])


def mlp_scores(frame):
    """The stream-B MLP (trained on 2023 only) as a second opinion for blending."""
    from b_platform.model import encode, load_model, predict
    model, _ = load_model("cpu")
    return predict(model, encode(frame.assign(assembly_month=frame["assembly_month"].astype(str)), json.loads((OUT / "features.json").read_text())), "cpu")


def bootstrap(y, scores, n=200):
    """95% interval for recall@10% and AUC by resampling test claims."""
    rng = np.random.default_rng(SEED)
    recall, auc = [], []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        budget = math.ceil(len(i) * .10)
        top = i[np.argsort(-scores[i], kind="stable")[:budget]]
        recall.append(y[top].sum() / y[i].sum())
        auc.append(roc_auc_score(y[i], scores[i]))
    lo_hi = lambda v: [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)]
    return {"recall_at_10pct_95ci": lo_hi(recall), "roc_auc_95ci": lo_hi(auc)}


def calibration(y, p, bins=10):
    """Mean predicted vs observed rate per score decile; max gap is the calibration error margin."""
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    which = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, bins - 1)
    rows = [{"predicted": float(p[which == b].mean()), "observed": float(y[which == b].mean())} for b in range(bins)]
    return {"deciles": rows, "max_abs_gap": max(abs(r["predicted"] - r["observed"]) for r in rows),
            "brier": float(brier_score_loss(y, p))}


def recall10(y, p):
    return evaluate(y, p)["recall_at_10pct"]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    train, val, test = (load(s) for s in ["train", "validation", "test"])
    y = {s: f["disputed_within_90d"].to_numpy() for s, f in [("train", train), ("validation", val), ("test", test)]}
    report = {"target": "recorded dispute 1-90 days after assembly",
              "protocol": "settings and blend weight chosen on 2024 validation (fit on 2023); final model refit on 2023+2024; "
                          "2025 test scored once",
              "simulated_feature_assumptions": ASSUMPTIONS, "models": {}, "selection": {}}
    baseline = json.loads((ROOT / "data/modeling/baseline_results/metrics.json").read_text())["metrics"]
    report["models"]["logistic_baseline (teammate)"] = {s: {k: baseline[s]["logistic_regression"][k] for k in ("recall_at_10pct", "roc_auc")}
                                                       for s in ("validation", "test")}

    # 1. Select tree settings and MLP blend weight on validation, real features only.
    rates, prior = peer_rates(train)
    Xtr = features(train, rates, prior, False)
    cats = {c: Xtr[c].cat.categories for c in CODES}
    Xva = align(features(val, rates, prior, False), cats)
    tried = []
    for params in GRID:
        p = fit(Xtr, y["train"], SEED, params=params).predict_proba(Xva)[:, 1]
        tried.append((recall10(y["validation"], p), params, p))
        print("validation", params, round(tried[-1][0], 4))
    best_recall, best, p_val = max(tried, key=lambda t: t[0])
    mlp_val = mlp_scores(val)
    blends = {w: recall10(y["validation"], (1 - w) * p_val + w * mlp_val) for w in (0, .25, .5, .75)}
    weight = max(blends, key=blends.get)
    report["selection"] = {"grid": [{**t[1], "val_recall_at_10pct": t[0]} for t in tried], "chosen": best,
                           "mlp_blend_val_recall": blends, "mlp_weight": weight}
    print("blend weights on validation", blends, "-> mlp weight", weight)

    # 2. Refit on 2023+2024 and score 2025 once.
    trval = pd.concat([train, val], ignore_index=True)
    y_trval = np.concatenate([y["train"], y["validation"]])
    rates, prior = peer_rates(trval)
    mlp_test = mlp_scores(test)
    bag = {}
    for name, scenario in [("real", False), ("scenario", True)]:
        Xfit = features(trval, rates, prior, scenario)
        cats = {c: Xfit[c].cat.categories for c in CODES}
        Xte = align(features(test, rates, prior, scenario), cats)
        model = fit(Xfit, y_trval, SEED, params=best)
        p_test = model.predict_proba(Xte)[:, 1]
        if name == "real":  # the MLP never saw simulated columns, so only the real model is blended
            report["models"]["real_trees_only"] = {"test": {"recall_at_10pct": recall10(y["test"], p_test),
                                                            **bootstrap(y["test"], p_test)}}
            p_test = (1 - weight) * p_test + weight * mlp_test
        report["models"][name] = {
            "test": {**{k: evaluate(y["test"], p_test)[k] for k in ("recall_at_10pct", "precision_at_10pct", "lift_over_random", "roc_auc")},
                     **bootstrap(y["test"], p_test), "calibration": calibration(y["test"], p_test)},
            "features": list(Xfit.columns), "iterations": int(model.n_iter_),
        }
        print(name, json.dumps(report["models"][name]["test"] | {"calibration": None}))
        bag[name] = (Xfit, Xte, p_test)

    # Per-claim range: main model + 5 models on bootstrap resamples (scenario model, which the dashboard shows).
    Xfit, Xte, p_test = bag["scenario"]
    rng = np.random.default_rng(SEED)
    members = np.stack([fit(Xfit, y_trval, SEED + i, rng.integers(0, len(Xfit), len(Xfit)), best).predict_proba(Xte)[:, 1]
                        for i in range(5)] + [p_test])  # main model included so the range always contains its score
    sim = Xte[["prior_claims_5y", "prior_disputes_5y", "fall_video", "fall_intensity", "descent_ms"]]
    out = pd.DataFrame({
        "claim_id": test["claim_identifier"], "risk": p_test.round(5), "risk_real_only": bag["real"][2].round(5),
        "risk_low": members.min(0).round(5), "risk_high": members.max(0).round(5),
        **{f"peer_{n}": Xte[f"peer_{n}"].round(5) for n in PEERS.values()},
        "overall_rate": round(prior, 5),
        "prior_claims_5y": sim["prior_claims_5y"], "prior_disputes_5y": sim["prior_disputes_5y"],
        "fall_video": sim["fall_video"], "fall_intensity": [LEVELS[int(v)] for v in sim["fall_intensity"]],
        "descent_ms": sim["descent_ms"].round(0),
    })
    out.to_csv(OUT / "claim_risk.csv", index=False)
    width = out["risk_high"] - out["risk_low"]
    report["per_claim_range"] = {"members": 6, "median_width": float(width.median()), "p90_width": float(width.quantile(.9))}
    (OUT / "claim_risk_metrics.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report["per_claim_range"]), f"wrote {OUT / 'claim_risk.csv'}")

    # Self-checks: simulated columns only in the scenario model; ranges contain the shown score.
    assert "prior_disputes_5y" not in report["models"]["real"]["features"]
    assert ((out.risk_low <= out.risk) & (out.risk <= out.risk_high)).all() and out["claim_id"].is_unique


if __name__ == "__main__":
    main()
