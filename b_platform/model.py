"""Shared pieces for stream B: feature encoding, the MLP, and the device (Telum NNPA or CPU)."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch import nn

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "modeling" / "draft"
OUT = ROOT / "b_platform" / "artifacts"
MANIFEST = json.loads((DATA / "manifest.json").read_text())
CATEGORICAL = MANIFEST["features"][:6] + ["assembly_month"]  # month one-hot: seasonality is not linear
NUMERIC = "days_accident_to_assembly"
LABEL = MANIFEST["target_column"]


def load(split):
    frame = pd.read_csv(DATA / f"{split}.csv.gz", dtype={c: str for c in CATEGORICAL})
    frame[CATEGORICAL] = frame[CATEGORICAL].fillna("MISSING")
    return frame


def fit_spec(train):
    """Vocab and scaling learned on train only (contract: features.json)."""
    days = np.log1p(train[NUMERIC].where(train[NUMERIC] >= 0))
    return {
        "categorical": {c: sorted(train[c].unique().tolist()) for c in CATEGORICAL},
        "numeric": NUMERIC,
        "days_log_mean": float(days.mean()),
        "days_log_std": float(days.std()),
    }


def columns(spec):
    names = [f"{c}={v}" for c in CATEGORICAL for v in spec["categorical"][c]]
    return names + ["days_log_z", "days_missing"]


def blocks(spec):
    """Column slice per input field, used to knock a field out when explaining a score."""
    out, start = {}, 0
    for c in CATEGORICAL:
        out[c] = slice(start, start + len(spec["categorical"][c]))
        start = out[c].stop
    out[NUMERIC] = slice(start, start + 2)
    return out


def encode(frame, spec):
    """One-hot categoricals (unseen value -> all zeros), standardized log days + missing flag."""
    k = len(columns(spec))
    X = np.zeros((len(frame), k), dtype=np.float32)
    rows = np.arange(len(frame))
    for c, sl in blocks(spec).items():
        if c == NUMERIC:
            continue
        codes = pd.Categorical(frame[c].astype(str), categories=spec["categorical"][c]).codes.astype(np.int64)
        hit = codes >= 0
        X[rows[hit], sl.start + codes[hit]] = 1
    days = pd.to_numeric(frame[NUMERIC], errors="coerce")
    days = days.where(days >= 0)  # negative durations are data errors: treat as missing
    z = (np.log1p(days) - spec["days_log_mean"]) / spec["days_log_std"]
    X[:, -2] = z.fillna(0).to_numpy(np.float32)
    X[:, -1] = days.isna().to_numpy(np.float32)
    return X


def mlp(k):
    """Architecture frozen in the plan's contract 2: k -> 256 -> 256 -> 256 -> 1."""
    return nn.Sequential(
        nn.Linear(k, 256), nn.ReLU(),
        nn.Linear(256, 256), nn.ReLU(),
        nn.Linear(256, 256), nn.ReLU(),
        nn.Linear(256, 1),
    )


def device(force=None):
    """'nnpa' when the Telum plugin loads, else 'cpu'. TORCH_NNPA_DEBUG=1 logs ops placed on the chip."""
    if force:
        return force
    try:
        import torch_nnpa  # noqa: F401  registers the 'nnpa' device on IBM z16/z17
        torch.zeros(1).to("nnpa")
        return "nnpa"
    except Exception:
        return "cpu"


def load_model(dev):
    meta = json.loads((OUT / "model.json").read_text())
    model = mlp(meta["k"])
    model.load_state_dict(torch.load(OUT / "model.pt", map_location="cpu"))
    return model.eval().to(dev), meta


@torch.inference_mode()
def predict(model, X, dev, batch=4096):
    """Probabilities for float32 X; batches of 4096 keep the NNPA busy without big host copies."""
    out = np.empty(len(X), dtype=np.float32)
    for i in range(0, len(X), batch):
        xb = torch.from_numpy(X[i:i + batch]).to(dev)
        out[i:i + batch] = torch.sigmoid(model(xb)).float().cpu().numpy().ravel()
    return out
