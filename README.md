# Pre6G EXP1：跨節點預測與實測驗證

Pre6G_EXP1 是 Kubernetes GPU Profiling 與整機耗能選點的研究專案：Master 同時派發候選 GPU Worker 的 120 秒 Nsight dry-run，收回 Trace Features 與 Netdata/DCGM 時序，使用 Frozen Runtime／Power Models 產生 provisional 排名，再以所有候選節點的相同、未開 profiler 完整 YOLO26 Job 與 PDU 電力驗證結果。

## 目前狀態與研究邊界

- 本次適配以 `ab2a20c7ded276a179d48f48a1efcc5afdb4afad` 為模型基準，已整合 `main` 的 PR #4（RTX3090 Archived Candidate），支援 RKE2、HAMi `nvidia.com/gpu`、Nsight 2026.5.1 與 CephFS RWX。
- 站點回報已驗證：RKE2 v1.35.6+rke2r1、Driver 595.99.02、Netdata/DCGM 1 Hz、100% 對齊覆蓋及 Master/4090/5090 PVC 讀寫。詳細版本與證據界線見[環境設定](docs/environment-setup.md)。
- **本次僅執行離線測試，沒有執行 GPU Job。** Nsight CUDA Trace／GPU Metrics 的 RKE2 Job、4090 模型與 EXP1 同 Trace 特徵一致性、完整跨節點 Prediction／Scheduling 仍待實機驗證。Mock PASS 不代表正式 Profiling PASS。
- RTX3090 因 Driver/NVML mismatch 排除；GPU Operator Flux HelmRelease 的 ClusterPolicy readiness 問題仍存在，不能宣稱整體 Operator 健康。Top1/Top2 GPU% 尚無已驗證 Process-level Collector；power bundles 仍為 `validation_required`。

## Quick Start：先離線產生計畫

以下從 Repository 根目錄執行，不建立 Kubernetes 資源：

```bash
git clone https://github.com/terrychen0803/Pre6G_EXP1.git
cd Pre6G_EXP1
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt -e .
python -m unittest discover -s tests -v
cp configs/rke2.example.yaml configs/site.local.yaml
```

依[配置說明](docs/configuration.md)填寫 `configs/site.local.yaml` 的 Node、GPU UUID、Netdata Parent Host、Collector、PVC、Exporter 與模型 binding。通用範例含 placeholder，填妥前會拒絕產生計畫。`examples/yolo26/*cross-node-dryrun.yaml` 也已匿名化；Node/UUID/Netdata route 與歷史 Power binding 請只填在私有 local config，**必須現場核對**。

```bash
python scripts/run_experiment_pipeline.py \
  --mode cross-node --cross-node-config configs/site.local.yaml \
  --run-id exp1-b16-r01 --worker-commit "$(git rev-parse HEAD)" \
  --output-dir generated/exp1-b16-r01
```

預期產生 `cross-node-plan.json`、`dryrun-jobs.yaml`、`collector-pod.yaml` 和摘要；`status=planned` 才表示離線計畫成功。Worker Commit 必須已推送且含本次適配，才能進行 live preflight。完整的 Preflight → Smoke → Profiling → Prediction → Ground Truth 操作與啟動 GPU 的界線見[資料收集手冊](docs/data-collection-guide.md)。

## 文件導覽

| 要做的事 | 文件 |
|---|---|
| 理解 Master／Worker 與資料流 | [Architecture](docs/architecture.md) |
| 部署與核對環境版本、HAMi、Netdata、DCGM、CephFS | [Environment Setup](docs/environment-setup.md) |
| 修改設定、加入候選 Node、保留歷史模型 binding | [Configuration](docs/configuration.md) |
| Preflight、Smoke、Profiling、匯出及下載資料 | [Data Collection Guide](docs/data-collection-guide.md) |
| CSV 單位、JSON Schema、Timestamp、目錄與缺失值 | [Data Schema](docs/data-schema.md) |
| 完整 Job、PDU 匯入、重複實驗及評估 | [EXP1 重現手冊](docs/EXP1-reproduction-zh.md)、[研究 Protocol](docs/EXP1-experiment-protocol-zh.md) |
| 定位失敗 | [Troubleshooting](docs/troubleshooting.md) |
| 正式收集前驗收 | [Handoff Checklist](docs/handoff-checklist.md) |
| 本次離線檢查與未完成項目 | [適配驗證紀錄](docs/evidence/rke2-adaptation-offline-20261016.md) |

`docs/evidence/` 保存歷史觀測，不代表目前叢集狀態；舊 k3s/NFS/sharing 安裝範本在 `cluster/`，不得直接套用到 RKE2/HAMi。新的共享儲存範例為 [configs/artifacts-pvc.example.yaml](configs/artifacts-pvc.example.yaml)。

## 目前正式候選：RTX4090／RTX5090 Frozen Runtime Models

| Device | Artifact | Schema / Type | Detector |
|---|---|---|---|
| RTX4090 | `models/runtime/RTX4090_yolo_soft_gate_v1.json` | v2 / `trace_soft_regime_interactions_crossfit_gate` | `yolo-v1` |
| RTX5090 | `models/runtime/RTX5090_yolo_trace_only_v1.json` | v1 / `ridge_log_runtime` | `yolo-v1` |

模型訓練來源、Frozen Parameters、Model ID 及 SHA-256 保持原樣。`yolo-v2-long` 僅作額外診斷，不可替換 Frozen Model 輸入。4090 的 `inferred_high_load_score` 保留於 Prediction、Semantic Runtime 及排名輸入。模型 override 可重複傳入 `--candidate-runtime-model NODE=PATH`／`--candidate-power-bundle NODE=PATH`，節點必須存在於 config；每次更換用新的 run ID，模型 Hash 寫入計畫並於推論前重查。測試參考向量不構成同 Trace 特徵一致性證據。

## RTX3090 Archived Candidate：尚未整合

PR #4 新增的 [RTX3090 Frozen Model 與研究資料](models/runtime/candidates/RTX3090/README.md) 原樣保留，包括 `frozen_model.json`、`crossfit_metrics.csv`、`crossfit_predictions.csv` 與原始說明；係數、模型資料及檔案 SHA-256 均未修改。

RTX3090 **不在目前正式候選清單**，尚未接入 EXP1 推論或跨節點配置。封存 JSON 沒有 `schema_version`，不可直接傳入 `scripts/predict_runtime.py`，也不會因整合 main 而自動轉為 v2。未來仍需 metadata contract、同 Trace 特徵一致性、RTX3090 實測、Node/GPU binding、profile Job 與 Power bundle 等獨立整合；目前 Driver/NVML mismatch 限制也未處理。封存的 crossfit 評估不是正式部署結果。

公開範例已移除站點 hostname、GPU UUID 與內部 Netdata route。Frozen Models 及其原始歷史 Binding 依研究保護規則保持 byte-for-byte 不變；本次沒有清除 Git 既有歷史，私有站點設定不納入 PR。
