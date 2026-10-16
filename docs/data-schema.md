# 實驗資料契約與追溯

本文件描述本次程式的實際檔案契約；細節延伸見 [Netdata Contract](netdata-contract.md)、[ProfileResult Contract](profile-result-contract.md) 和 `schemas/`。歷史文件的 NFS/100Gi 與舊 hostname 不代表本次 CephFS/50Gi 配置。

## 目錄

```text
generated/<run>/                         # Master：不提交 Git
  cross-node-plan.json                   # config/source/template/model Hash、commit、Node/UUID
  dryrun-jobs.yaml                       # DCGM 未解析，不可直接提交
  dryrun-jobs-resolved.yaml               # live preflight 後 endpoint
  collector-pod.yaml
  dcgm-endpoints.json
  cross-node-run-summary.json
  run-summary.json                       # 外層 pipeline 狀態
  smoke-result.json                      # smoke-only 才有
  workload-discovery.json                # execute/predict 才有
  logs/
  artifacts/<node>/                      # Collector 從 PVC 下載
    profile.nsys-rep
    profile.sqlite
    marker-free-discovery.json
    runtime-features.json
    profile-result.json
    artifact-inventory.json               # Master 下載檔案 SHA-256 / Node / UUID / commit
    provenance/worker.txt
    telemetry/
      timestamps.json
      application-window.json
      netdata.csv
      dcgm.csv
      aligned-telemetry.csv
      alignment-quality.json
    detector-v2/                         # 可選診斷；不作模型輸入
      trajectory.json
      windows.csv
  predictions/<node>/
    runtime-prediction.json
    semantic-runtime.json
    application-aligned-telemetry.csv
    power-window-crop.json
    power-series.csv
    power-summary.json
  ranking-input.json
  provisional-ranking.json
```

PVC 對應 `/shared/results/<run>/<node>/`；Worker 的 `/work/output` 是 temporary emptyDir。PVC 還保存完整 `telemetry/*` logs，Master 預設下載上列核心原始時序與結果。Smoke marker 在 `/shared/smoke/<run>/<node>/marker.json`。Ground Truth 使用獨立 validation ID/目錄，含 `validation-plan.json`、`validation-jobs.yaml`、execution Pod/Job JSON/log/events、正規化 PDU CSV 與 `evaluation/validation-result.json`；不寫入 Profiling run 目錄。

## CSV 欄位與單位

| Artifact / 欄位 | 語意與單位 |
|---|---|
| netdata/DCGM `timestamp_ns` | UTC Unix nanoseconds，整數；保留原始時序，不用本機顯示時區替代 |
| `timestamp_utc` | 同時刻 ISO 8601 UTC，便於閱讀 |
| `node` | Kubernetes Node identity；Netdata URL 使用的 host identity 另記於 config |
| `CPU User%`, `CPU System%`, `CPU IOWait%` | Netdata system CPU 百分比，依既有 Collector 轉換，不能自行改成某個 core 的百分比 |
| `Load 1min`, `Load 5min`, `Load 15min` | Load average，無單位；不是 CPU% |
| `Mem Used(MB)`, `Mem Free(MB)` | 既有 Collector 的 MB 命名及數值尺度，保持與 Frozen Power scaler 一致，不另行改名或重新縮放 |
| `CPU Temp(°C)` | 探測到的 CPU sensor，攝氏；選用 chart 記錄於歷史查詢 metadata/log |
| `Top1 CPU%`, `Top2 CPU%`, `Top3 CPU%` | 每時刻有效 apps CPU series 的前三名；process-group 語意，不應稱為已驗證的 per-process GPU 使用率 |
| `GPU Util%` | DCGM_FI_DEV_GPU_UTIL，百分比 |
| `GPU Mem Used(MB)` | DCGM_FI_DEV_FB_USED，DCGM 原生容量尺度，保留既有 canonical MB 標籤 |
| `GPU Temp(°C)` | DCGM_FI_DEV_GPU_TEMP，攝氏 |
| `GPU Power(W)` | DCGM_FI_DEV_POWER_USAGE，瓦；Device Power 不是整機 PDU Power |
| DCGM `request_start_ns`, `request_end_ns` | 每次 HTTP request 的 wall-clock 起迄；sample timestamp 取中點 |
| DCGM `gpu_uuid`, `gpu_model`, `driver_version`, `gpu_index` | 來源 labels；driver_version label 若 exporter 沒提供可能為空，以 provenance 的 nvidia-smi 補充，不能編造 |
| aligned `dcgm_timestamp_ns`, `netdata_timestamp_ns`, `alignment_delta_ms` | 兩來源原始時間與**絕對**差，ms；主 timestamp 使用 DCGM 中點 |
| power-series `PREDICTED_POWER_W` | Frozen ONNX/scaler 輸出的整機功率估計，W；保留輸入欄位，range 超出只是診斷 |
| PDU `timestamp`, `power_w` | `normalize_pdu_excel.py` 標準輸出；UTC ISO 8601 與 W，另記原始欄位/時區/outlet/point 或 interval 語意 |

Top1 GPU%/Top2 GPU% 尚無已驗證 Collector，不能補 0 後當作觀測。CSV 是 UTF-8、含 header，欄名包含空白與 `%`／`°C`，讀取時保留原字串。Memory 的歷史訓練尺度有明確相容性限制，不能因名稱 MB 而自行換算為另一種二進位/十進位單位。

## JSON Metadata 與 Schema

| 檔案 | schema_version / 檢查 |
|---|---|
| config | `pre6g.cross-node-dryrun-config/v1`（舊）或 `/v2`（明確 deployment），由 prepare 驗證 |
| cross-node-plan | `pre6g.cross-node-dryrun-plan/v1`，含 deployment/current node/historical power_binding_node/UUID/Hash |
| timestamps | wrapper 錄下 pre/profile/post Unix ns；application/steady 不能從未知 callback 偽造 |
| application-window | `pre6g.application-window/v1`，原始 yolo command、started_at_unix_ns、finished_at_unix_ns |
| runtime-features | `pre6g.runtime-features/v1`；[Schema](../schemas/runtime-features.schema.json)：Node、Device、detector、七個 trace features、順序 vector、input policy |
| profile-result | `pre6g.profile-result/v1`；[Schema](../schemas/profile-result.schema.json)：task/node/device、detector、完整 runtime_features、transport contract |
| runtime-prediction | `pre6g.runtime-prediction/v1`；[Schema](../schemas/runtime-prediction.schema.json)：model_id/device_id、predicted_runtime_ms、feature_count、可選 inferred_high_load_score |
| semantic-runtime | `pre6g.semantic-runtime/v1`；[Schema](../schemas/semantic-runtime.schema.json)：execution_cycle → training_iteration binding，steady total 與可選 score |
| alignment-quality | `pre6g.telemetry-alignment-quality/v1`，sample counts、coverage、median/max delta、兩來源 max gap、pass |
| power-summary | [Power Adapter](../src/pre6g_experiment/power_adapter.py) 的 binding/feature/range/status/observed window；bound_node 保持模型歷史名稱 |

模型 Artifact 的 schema 與 Prediction schema 不同：4090 model 為 `pre6g.runtime-model/v2`，5090 model 為 `/v1`；兩者 Prediction 都是 `/v1`。4090 輸出 score 0–1；5090 沒有 score 欄位，不用 null 或 0 冒充。

七個 Runtime features 依 Frozen JSON 的 `feature_names` 排序：`log_detected_period_ms`、`log_kernel_event_rate_hz`、`log_unique_kernel_count`、`log_median_kernel_duration_us`、`log_mean_kernel_duration_us`、`log_kernel_busy_fraction`、`anchor_robust_cv`。log 是既有自然對數轉換，輸入基礎量分別為 ms、Hz、count、µs、µs、fraction；CV 無單位。本次不改任何抽取/模型公式。4090 測試 centroid fixture 與 5090 C03 reference 用於軟體 regression，不能當作 RKE2 新 Trace。

## Timestamp 與品質門檻

Netdata 使用 DB timestamp；DCGM 使用 request midpoint。nearest-neighbor 對齊容忍 750ms，coverage=aligned/DCGM rows，最低 0.90，兩來源 max gap ≤2s。UTC nanoseconds 用整數處理以免 float 精度流失；Nsight event 內部相對時間不能直接拿來當 Unix wall-clock。

Power 輸入裁切到原始 application window；至少 10 筆、window ≥10s、頭尾各距邊界 ≤2s、內部 gap ≤2s。完整 Ground Truth 時間取 Kubernetes trainer container startedAt/finishedAt，排程等待、init container 與 Nsight finalization 不計入。不同時窗的 energy 不可直接比較。

缺失 metric/feature、非有限數值、錯 Node/Device/UUID、model Hash 變動、Detector profile 不符時應停止該階段。保存錯誤 summary/log；不補零、不插造 Trace features、不把舊 run 複製進新 run。Scaler range warning 不改 Frozen preprocessing，也不能自行 clipping。Telemetry quality PASS 與模型 accuracy PASS 是不同 gate。

## 重現性與 Profiling Overhead

Plan 保存 runtime JSON、power manifest/ONNX/scaler、source/template/config SHA-256 與 worker commit；worker.txt 保存實際 checkout、Python、Kernel、GPU/Driver、Nsight 版本。另由操作者保存 image digest、OS、HAMi/Operator/monitoring/storage 版本、PDU 原始檔 Hash 與 outlet mapping。生成檔案用 `sha256sum` 清單備份。

Nsight tracing、DCGM poll、資料生成與 shared copy 都會造成負載；copy 在 application window 之後，但會增加整個 Job wall time。Profile observed period 不是 unprofiled runtime ground truth。完整 unprofiled trainer、整機 PDU 與 background load 必須另外保存；本次未重新測量 overhead，也未完成 4090 同 Trace 抽取一致性。
