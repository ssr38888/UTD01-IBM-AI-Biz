"""Train the contract-2 MLP on the draft splits; keep the epoch with best validation recall@10%.

python b_platform/train_mlp.py [--epochs 6]
Writes b_platform/artifacts/{features.json, features.npz, model.pt, model.json}.
Train on CPU (any box); serving is what runs on the Telum chip.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from b_platform.model import LABEL, OUT, columns, encode, fit_spec, load, mlp, predict  # noqa: E402
from train_baseline import evaluate  # noqa: E402  same recall@10% scorer as the logistic baseline


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=6)
    args = parser.parse_args()
    torch.manual_seed(42)
    OUT.mkdir(parents=True, exist_ok=True)

    frames = {s: load(s) for s in ["train", "validation", "test"]}
    spec = fit_spec(frames["train"])
    X = {s: encode(f, spec) for s, f in frames.items()}
    y = {s: f[LABEL].to_numpy(np.int8) for s, f in frames.items()}
    (OUT / "features.json").write_text(json.dumps({**spec, "columns": columns(spec)}, indent=1))
    # Contract 1: one matrix for every split, for the benchmark and fairness work.
    np.savez_compressed(
        OUT / "features.npz",
        X=np.concatenate([X[s] for s in X]), y=np.concatenate([y[s] for s in y]),
        ids=np.concatenate([f["claim_identifier"].to_numpy(np.int64) for f in frames.values()]),
        split=np.concatenate([[s] * len(f) for s, f in frames.items()]),
    )

    k = X["train"].shape[1]
    model = mlp(k)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-5)
    loss_fn = nn.BCEWithLogitsLoss()
    xt, yt = torch.from_numpy(X["train"]), torch.from_numpy(y["train"].astype(np.float32))
    best = (-1, None, None)
    for epoch in range(args.epochs):
        model.train()
        start = time.time()
        for idx in torch.randperm(len(xt)).split(1024):
            opt.zero_grad()
            loss = loss_fn(model(xt[idx]).squeeze(1), yt[idx])
            loss.backward()
            opt.step()
        model.eval()
        val = evaluate(y["validation"], predict(model, X["validation"], "cpu"))
        print(f"epoch {epoch + 1}: val recall@10% {val['recall_at_10pct']:.3f} auc {val['roc_auc']:.3f} ({time.time() - start:.0f}s)")
        if val["recall_at_10pct"] > best[0]:
            best = (val["recall_at_10pct"], {n: t.clone() for n, t in model.state_dict().items()}, val)

    model.load_state_dict(best[1])
    torch.save(best[1], OUT / "model.pt")
    val_scores = predict(model, X["validation"], "cpu")
    test = evaluate(y["test"], predict(model, X["test"], "cpu"))
    meta = {
        "k": k, "architecture": "k-256-256-256-1 ReLU, sigmoid",
        "threshold_top10pct": float(np.quantile(val_scores, 0.9)),  # validation score that admits ~10% of claims
        "validation": best[2], "test": test,
        "note": "test evaluated once after choosing the epoch on validation",
    }
    (OUT / "model.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps({"validation": best[2], "test": test}, indent=1))


if __name__ == "__main__":
    main()
