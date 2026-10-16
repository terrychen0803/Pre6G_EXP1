# RTX3090 runtime model: future integration

**Status: archived candidate; not connected to EXP1 inference or cross-node selection.** The current cross-node candidates remain RTX4090 and RTX5090. These files preserve the supplied frozen model and its cross-fitted evaluation without changing any runtime configuration.

| File | Purpose | SHA-256 |
|---|---|---|
| `frozen_model.json` | Original RTX3090 soft-gate model, unchanged | `6442546591bfef411e6416f1a0c73850af0b18e78d30fb801423c66bd70a144b` |
| `crossfit_metrics.csv` | Grouped leave-one-workload-out summary | `bb841e8971fb57a6668c9499c83c80faa79f6d8afb7b7f2c25dbfaa02a` |
| `crossfit_predictions.csv` | All 48 held-out predictions | `a099c0edba962ba842dd855bc1bd30d2e1f7428299014689be8e2d9cead5a9c6` |

The model contains seven trace inputs, a soft clean/high-load gate, and a ridge model with 15 expanded inputs. It was fitted from 48 condition samples covering 24 workloads. The target is the median of five unprofiled steady-window iteration times, in milliseconds. Its recorded training-source SHA-256 matches the supplied `evaluate_and_freeze.py` (`29bc7439b13860c2f1a9f3e113f618932f1a4d656959f25782d0679d2247e3e5`). The detector-source hash in the model could not be checked because that source file was not supplied.

| Condition | Samples | MAPE | Maximum APE | Regime accuracy |
|---|---:|---:|---:|---:|
| Clean | 24 | 5.02% | 12.39% | 87.50% |
| Compute load | 24 | 7.51% | 21.72% | 58.33% |
| Combined | 48 | 6.26% | 21.72% | 72.92% |

The CSV values were checked against all 48 predictions. These are held-out estimates, not a live EXP1 deployment result. The model covers the collected clean and compute-load conditions; the low loaded-regime accuracy merits a live check before promotion.

## Future integration

1. Wrap the frozen model in the EXP1 `pre6g.runtime-model/v2` metadata contract (`model_id`, `model_type`, `device_id`, `workload_family`, `detector_profile`, `detected_unit`, and role). The raw JSON here has no `schema_version` and cannot be passed directly to `scripts/predict_runtime.py`.
2. Verify the seven features from a shared RTX3090 trace against EXP1's feature extractor, then test frozen predictions on RTX3090 data.
3. If RTX3090 is to join cross-node ranking, supply its Kubernetes node binding, profile Job, and node-bound power bundle before adding it to a cross-node YAML. Keep it outside the default candidates until those checks pass.
