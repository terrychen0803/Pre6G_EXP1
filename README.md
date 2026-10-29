# Pre6G EXP1：跨節點預測與實測驗證

Pre6G_EXP1 是 Kubernetes GPU Profiling 與整機耗能選點的研究專案：Master 同時派發候選 GPU Worker 的 120 秒 Nsight dry-run，收回 Trace Features 與 Netdata/DCGM 時序，使用 Frozen Runtime／Power Models 產生 provisional 排名，再以所有候選節點的相同、未開 profiler 完整 YOLO26 Job 與 PDU 電力驗證結果。

## 目前狀態與研究邊界

- 本次適配以 `ab2a20c7ded276a179d48f48a1efcc5afdb4afad` 為模型基準，已整合 `main` 的 PR #4（RTX3090 Archived Candidate），支援 RKE2、HAMi `nvidia.com/gpu`、Nsight 2026.5.1 與 CephFS RWX。
- 站點回報已驗證：RKE2 v1.35.6+rke2r1、Driver 595.99.02、Netdata/DCGM 1 Hz、100% 對齊覆蓋及 Master/4090/5090 PVC 讀寫。詳細版本與證據界線見[環境設定](docs/environment-setup.md)。
- RTX4090 非特權 Pod 已驗證 Nsight 2026.5.1 CUDA Trace 與 SQLite CUPTI kernel；CephFS 單節點讀寫及兩個 Host 的 Netdata 查詢也已通過。RTX5090 的雙節點工具 Smoke 受現有工作負載與 HAMi 配額限制，尚未完成。正式整套 YOLO Profiling、4090 模型與 EXP1 同 Trace 特徵一致性、完整跨節點 Prediction／Scheduling 仍待實機驗證；不可將部分 Smoke 視為全部通過。
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

## Master 選點決策延遲量測（未來整合）

[量測程式](scripts/measure_decision_latency.py)可在 master 上重播**已完成**的跨節點實驗，計算資料已收齊後，執行 workload discovery、runtime／power 模型預測到寫出節點排名的時間。先確認 `generated/<run-id>/cross-node-run-summary.json` 的 `status` 為 `completed`，且原實驗使用的 Job、模型與 power bundle 路徑仍可讀；上面的 `status=planned` 離線計畫不能用於量測。

從 Repository 根目錄執行（參數示範為預設值）：

```bash
python scripts/measure_decision_latency.py \
  --run-dir generated/<run-id> \
  --repeats 30 \
  --warmups 0 \
  --threshold-ms 100
```

結果寫入 `generated/<run-id>/decision-latency-benchmark.json`；可用 `--output <path>` 改位置。查看 `statistics.mean_ms`、`p95_ms`、`max_ms`、`mean_kpi_pass` 及 `command_statistics`：預設以 30 次重播的**平均值 ≤ 100 ms** 判定。程式會核對原實驗模型雜湊及選中節點，在暫存目錄重播，不會啟動 GPU Job 或覆寫原排名。

這是離線的 **master 決策處理延遲**，不含 worker profiling、Kubernetes 輪詢、collector 啟動及資料傳輸；不能直接代表「worker 完成至選點」或「任務提交至執行」的等待時間。正式流程的線上量測仍待整合，邊界與後續步驟見[量測說明](docs/decision-latency-benchmark-zh.md)。

## 文件導覽

| 要做的事 | 文件 |
|---|---|
| 理解 Master／Worker 與資料流 | [Architecture](docs/architecture.md) |
| 部署與核對環境版本、HAMi、Netdata、DCGM、CephFS | [Environment Setup](docs/environment-setup.md) |
| 修改設定、加入候選 Node、保留歷史模型 binding | [Configuration](docs/configuration.md) |
| Preflight、Smoke、Profiling、匯出及下載資料 | [Data Collection Guide](docs/data-collection-guide.md) |
| CSV 單位、JSON Schema、Timestamp、目錄與缺失值 | [Data Schema](docs/data-schema.md) |
| 完整 Job、PDU 匯入、重複實驗及評估 | [EXP1 重現手冊](docs/EXP1-reproduction-zh.md)、[研究 Protocol](docs/EXP1-experiment-protocol-zh.md) |
| 離線量測 Master 選點決策延遲（未來整合） | [量測說明](docs/decision-latency-benchmark-zh.md) |
| 定位失敗 | [Troubleshooting](docs/troubleshooting.md) |
| 正式收集前驗收 | [Handoff Checklist](docs/handoff-checklist.md) |
| 本次離線檢查與未完成項目 | [適配驗證紀錄](docs/evidence/rke2-adaptation-offline-20261016.md) |

`docs/evidence/` 保存歷史觀測，不代表目前叢集狀態；舊 k3s/NFS/sharing 安裝範本在 `cluster/`，不得直接套用到 RKE2/HAMi。新的共享儲存範例為 [configs/artifacts-pvc.example.yaml](configs/artifacts-pvc.example.yaml)。

## 長時間 Ground Truth（預設無時間上限）

完整 Ground Truth source Job 已移除固定三小時 `activeDeadlineSeconds`。`scripts/run_validation_jobs.py --timeout-seconds` 預設為 `0`，每 30 秒同時檢查所有候選的 Complete/Failed，直到所有預定 workload 自然結束。正數 timeout 只停止本次監控；Ctrl-C、timeout 或重新連接都不刪除／終止 Jobs。可用相同 plan/jobs/context/output-dir 的 `--resume` 恢復並補收集，或用 `--attach` 連接已存在 Jobs。

失敗、中斷及缺少證據的結果不會標記為有效 Ground Truth。epochs、batch、52,448 iterations、trainer 時窗定義、Frozen Models、120 秒 Nsight dry-run 及 Smoke 限制保持原樣。執行命令、metadata、恢復流程與長時間資源監控請見 [Ground Truth 收集手冊](docs/ground-truth-collection.md)。

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
