# Stream B: ClaimClock on the LinuxONE box

Everything runs on the VM after `git pull`. Python 3 + torch (+ `torch_nnpa` on z16/z17), pandas, numpy, scikit-learn.

| Step | Command | Output |
|---|---|---|
| 1. Platform proof (screenshot it) | `sh b_platform/hour_zero.sh` | machine type, RAM, NNPA device test, Granite timing |
| 2. Train MLP (optional, CPU, ~1 min) | `python3 b_platform/train_mlp.py` | `artifacts/model.pt`, `model.json`, `features.json`, `features.npz` |
| 3. Score 2025 on the chip | `TORCH_NNPA_DEBUG=1 python3 b_platform/score.py` | `artifacts/scores.csv` (contract 3), device + timing in `model.json` |
| 4. Full-book benchmark | `python3 b_platform/benchmark.py` | `artifacts/benchmark.json`: CPU vs NNPA on 5.6 M rows |
| 5. Dashboard | `python3 c_product/app.py --port 8000` | advocate screen at http://localhost:8000 |

`model.pt` is committed, so step 2 can be skipped. `score.py` falls back to CPU when `torch_nnpa` is missing and records which device it used; never say "Telum" on a slide unless `model.json` says `"device": "nnpa"`.

Reaching the dashboard: only Jupyter (38888) may be open. Either `ssh -L 8000:localhost:8000 <user>@<vm>` and open http://localhost:8000 locally, or, if `jupyter-server-proxy` is installed, open `https://<vm>:38888/proxy/8000/`.

Granite (the letter writer, `c_product/granite.py`):
- Best: run llama.cpp's `llama-server -m <granite big-endian .gguf> --port 8080` and `export GRANITE_URL=http://127.0.0.1:8080`.
- Or `pip install llama-cpp-python` and `export GRANITE_GGUF=/path/model.gguf`.
- s390x is big-endian: the GGUF must be big-endian (`head -c4 model.gguf` reads `GGUF`). Reuse the Granite setup from the IBM "Predictive AI + Generative AI" lab if it is already on the VM.
- With neither set, a stub returns the template unchanged, so the demo never stalls.

Model (2025 held-out year, 10% outreach budget): MLP recall 49.9% vs logistic baseline 43.6% vs private-carrier rule 9.7%. Validation picked the epoch; test was read once. Months 11–12 never appear in training (late-year rows are purged), so they encode as "unseen".

## Claim risk model (`b_platform/claim_risk.py`, ~2 min on CPU)

Gradient-boosted trees on the 8 claim fields plus peer baselines (dispute rate of the claim's industry, carrier, county, injury, body part, cause), blended 75/25 with the stream-B MLP. Settings and blend weight chosen on 2024 validation, final model refit on 2023+2024, 2025 test scored once. Test, 10% budget: recall 51.0% (95% CI 50.4–51.8%), AUC 0.844; logistic baseline 43.6%. A "scenario" variant adds SIMULATED claimant history and fall-video intensity (assumptions in the script and `artifacts/claim_risk_metrics.json`); its higher numbers come from those assumptions and are not a result. Writes `artifacts/claim_risk.csv`, which the dashboard shows under Analytics.
