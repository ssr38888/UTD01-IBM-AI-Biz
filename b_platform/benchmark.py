"""Full-book rescore benchmark: chip (NNPA) vs CPU, same model, same threads, same batches.

python b_platform/benchmark.py [--rows 5600000]
Cycles the encoded 2025 claims up to --rows (NY's whole claim history is ~5.6 M) and
writes b_platform/artifacts/benchmark.json for the dashboard and slides.
"""
import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from b_platform.model import OUT, device, encode, load, load_model, predict  # noqa: E402


def run(dev, X, rows, batch):
    model, _ = load_model(dev)
    predict(model, X[:batch], dev, batch)  # warm-up: first NNPA call stages weights on the chip
    done, start = 0, time.perf_counter()
    while done < rows:
        n = min(len(X), rows - done)
        predict(model, X[:n], dev, batch)
        done += n
    seconds = time.perf_counter() - start
    return {"device": dev, "rows": rows, "seconds": round(seconds, 2), "claims_per_second": round(rows / seconds)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=5_600_000)
    parser.add_argument("--batch", type=int, default=4096)
    args = parser.parse_args()
    X = encode(load("test"), json.loads((OUT / "features.json").read_text()))
    results = [run("cpu", X, args.rows, args.batch)]
    if device() == "nnpa":
        results.append(run("nnpa", X, args.rows, args.batch))
    report = {
        "machine": platform.machine(), "torch_threads": torch.get_num_threads(), "cpus": os.cpu_count(),
        "batch": args.batch, "results": results,
        "note": "Same MLP, batch and thread count on both devices; NNPA only on z16/z17 with torch_nnpa.",
    }
    if len(results) == 2:
        report["speedup"] = round(results[0]["seconds"] / results[1]["seconds"], 2)
    (OUT / "benchmark.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
