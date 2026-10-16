# 資料收集：從離線計畫到正式驗證

所有指令在 Repository 根目錄、已啟用 venv 的 Master/control 端執行。此文件說明可操作流程；本次適配沒有執行下列 live Smoke、Profiling 或完整訓練。完整 PDU 評估與重複實驗接續 [EXP1 重現手冊](EXP1-reproduction-zh.md) 第 4 節起。

## 1. 收集前檢查與凍結

先完成 [Environment Setup](environment-setup.md) 和 [Handoff Checklist](handoff-checklist.md)，填妥 `configs/site.local.yaml`。核對兩候選 Node/UUID、Power 歷史主機對應、Netdata host route、CephFS 剩餘容量，以及 PDU outlet。記錄 image digest、背景負載與時間同步誤差。

```bash
export CTX="$(kubectl config current-context)"
export COMMIT="$(git rev-parse HEAD)"
export RUN=exp1-b16-r01
CROSS_ARGS=(
  --mode cross-node --cross-node-config configs/site.local.yaml
  --run-id "$RUN" --worker-commit "$COMMIT" --output-dir "generated/$RUN"
)
python scripts/run_experiment_pipeline.py "${CROSS_ARGS[@]}"
```

此步離線，不建立資源；檢查 `cross-node-run-summary.json.status=planned`。確認 Worker Commit 已 push，且含本次 RKE2 適配。若 config 是正式例，`source_epochs=1639`、`source_total_work_units=52448`；短功能例是 30 epochs/960 units。不要將兩組結果混用。每個 run ID 必須唯一；變更模型/設定/Commit 後使用新目錄。

## 2. Read-only Preflight

```bash
python scripts/run_experiment_pipeline.py "${CROSS_ARGS[@]}" \
  --preflight-only --kube-context "$CTX"
```

不建立持久資源：檢查 control-side NumPy/ONNX CPU provider、context、Ready/DiskPressure、RuntimeClass、HAMi Allocatable、PVC/PV Bound/RWX/CSI 或 NFS、StorageClass、Collector Taint、既有 Job 名稱，再依 Node/UUID 尋找 exporter。最後對生成 Job/Collector 做 `apply --dry-run=server`。這不會跑 CUDA，也不會證明 HostPath 的 binary 可用。

應得到 `status=preflight_passed`、`dryrun-jobs-resolved.yaml` 和 `dcgm-endpoints.json`。Exporter Pod proxy 的四個 metric 都需唯一符合 GPU UUID；這仍需下一步確認 Worker 能直連。禁止直接 apply 含 `DCGM_ENDPOINT_UNRESOLVED` 的原始模板或計畫。

## 3. 工具 Smoke 與 Nsight CUDA Smoke

以下會建立 GPU resource Pod，但不執行 YOLO 訓練；正式操作前由研究人員決定合適時段：

```bash
python scripts/run_experiment_pipeline.py "${CROSS_ARGS[@]}" \
  --smoke-only --kube-context "$CTX"
```

核對 `nvidia-smi` 可見 UUID、Nsight `--version`、GitHub 存取、Netdata/DCGM HTTP、兩 Worker 共享 PVC 互讀。成功後工具 Pods 會刪除，logs 與 `smoke-result.json` 留在 Master；PVC marker 保留。`nfs_peer_read` 是為舊結果相容保留的欄名，現在表示 RWX backend 的 peer-read，CephFS 同樣適用。這個工具 Smoke **沒有執行 Nsight CUDA Trace**。

`k8s/nsys2026-rtx-smoke.yaml` 是另行審閱後執行的 CUDA/Nsight 診斷 Job：已更新 HAMi resource、Node 及 2026.5.1 CLI，會編譯及執行 CUDA vector-add。它用 `emptyDir`，Report/SQLite 必須在 Pod 刪除前 `kubectl cp` 保存；Node 是匿名代稱；Namespace/Path 是部署範例，其他站點先複製調整。此 Job 會佔用 GPU，**本次未執行**。`--sample=none --cpuctxsw=none` 不代表 GPU Metrics 已收集，後者另需實測。

Master readback 可使用計畫中的 read-only Collector（只在 Master 建 Pod，沒有 GPU request）：

```bash
kubectl --context "$CTX" create -f "generated/$RUN/collector-pod.yaml"
# 使用 cross-node-plan.json 中的 collector_pod 名稱和 candidates[].node
kubectl --context "$CTX" -n experiments wait pod/COLLECTOR_NAME --for=condition=Ready --timeout=5m
kubectl --context "$CTX" -n experiments exec COLLECTOR_NAME -c collector -- \
  cat "/shared/smoke/$RUN/NODE_NAME/marker.json"
kubectl --context "$CTX" -n experiments delete pod COLLECTOR_NAME --wait=true
```

Namespace 改過時同步替換 `experiments`。核對兩個 marker 的 run/node/uuid，保存 readback 紀錄；沒有此證據不要把 Worker peer-read 稱為 Master readback。

## 4. Profiling、時序收集與 Prediction

以下會同時建立兩個 120 秒 GPU Profile Jobs：

```bash
python scripts/run_experiment_pipeline.py "${CROSS_ARGS[@]}" \
  --execute --kube-context "$CTX"
```

每次執行會再做 Preflight。Worker checkout 固定 Commit，先保存實際版本與 GPU inventory、檢查 UUID，生成原有 YOLO26 合成資料，再由 wrapper 完成：

1. 5 秒 pre-roll，DCGM 主動 1 Hz 輪詢。
2. Nsight `--trace=cuda,nvtx,osrt --sample=none --cpuctxsw=none --duration=120 --kill=sigterm` 執行原始 workload；application wrapper 記錄訓練命令起迄，不把 profiler finalization 當成 application。
3. 2 秒 post-roll，依錄下的時間窗回查 Netdata Parent 歷史資料；不以每秒 `/allmetrics` HTTP 取代歷史 DB timestamp。
4. 以 750ms nearest timestamp 對齊；最低 coverage 90%、兩來源 max gap 2s。實驗目標為已驗證環境的 100%，門檻 PASS 不能自動稱為 100%。
5. Nsight `.nsys-rep` 匯出 SQLite；`yolo-v1` marker-free detector → `runtime-features.json` → `profile-result.json`。不使用 iterations.csv/NVTX marker/框架 callback 作 Frozen Model 輸入。
6. 共享 PVC 保存 raw reports、telemetry、features、provenance；Master Collector readback 到 `generated/$RUN/artifacts/<node>/`。
7. Master 重查 Frozen Model Hash，執行 Runtime、semantic aggregation、application-window power prediction 和 provisional ranking；4090 額外負載分數保留。

如需獨立重跑對齊（不使用 GPU）：

```bash
python scripts/align_telemetry.py \
  --netdata generated/RUN/artifacts/NODE/telemetry/netdata.csv \
  --dcgm generated/RUN/artifacts/NODE/telemetry/dcgm.csv \
  --output generated/RUN/recheck/aligned-telemetry.csv \
  --quality-output generated/RUN/recheck/alignment-quality.json \
  --tolerance-ms 750 --min-coverage 0.90 --max-gap-s 2
```

手動匯出保留的 Nsight report，需有相容的 Nsight CLI：

```bash
/opt/nvidia/nsight-systems-cli/2026.5.1/target-linux-x64/nsys export \
  --type=sqlite --output=generated/RUN/recheck/profile.sqlite \
  generated/RUN/artifacts/NODE/profile.nsys-rep
```

記錄工具版本，不覆寫原 SQLite。完整資料格式、檔名與單位見 [Data Schema](data-schema.md)。

## 5. 輸出驗收、備份與失敗保存

`run-summary.json.status=completed` 只代表 pipeline 階段完成；**計畫模式也可出現此值**。必須查看 `cross-node-run-summary.json.status`，分別為 `planned`、`preflight_passed`、`smoke_passed` 或真正執行後的 `completed`。即使最後是 completed，也只代表程式流程完成，不能替代研究準確度／正式 Scheduling 驗證。

驗收兩 node 都有 ProfileResult/Feature identity、Detector 至少三 complete cycles、alignment quality.pass、正且有限 Runtime、Power binding/UUID、相同 total work units、Hash 與 `production_ready=false`。正式完整 Job 與 PDU 尚未做時，不能宣稱選點正確。4090 同 Trace 特徵一致性仍是獨立未完成 gate。

將 `generated/$RUN` 複製至研究備份存放區；成功執行已包含 Master download 的 raw report/SQLite。保存仍在 PVC 的其餘 telemetry logs 與失敗 Job 輸出時，可重新建立 read-only Collector 後使用 `kubectl cp`，不刪 PVC：

```bash
kubectl --context "$CTX" create -f "generated/$RUN/collector-pod.yaml"
kubectl --context "$CTX" -n experiments wait pod/COLLECTOR_NAME --for=condition=Ready --timeout=5m
kubectl --context "$CTX" -n experiments cp \
  "COLLECTOR_NAME:/shared/results/$RUN" "generated/$RUN/shared-backup" -c collector
kubectl --context "$CTX" -n experiments delete pod COLLECTOR_NAME --wait=true
find "generated/$RUN" -type f -print0 | sort -z | xargs -0 sha256sum > "generated/$RUN.sha256"
tar -czf "generated/$RUN.tar.gz" "generated/$RUN" "generated/$RUN.sha256"
```

備份屬私有研究資料，可能含 Node/URL/trace symbol/PDU 資料，不提交 Git。Worker 成功後已將 raw trace 保存到 PVC；若在 copy 前失敗，原始 trace 可能仍只在 Pod emptyDir，**先下載再處理 Pod**。收集 logs、events、Pod JSON、exit code；禁止把失敗/不完整樣本加入成功集合。程式不自動刪研究 Job 或 PVC，清理須由研究人員依 retention policy 操作。

## 6. Ground Truth 與重複實驗

使用 [EXP1 重現手冊](EXP1-reproduction-zh.md) 第 4–6 節：凍結同一 source Job，所有候選執行相同 unprofiled `trainer`，以 container startedAt/finishedAt 和同窗 PDU energy 評估。短 fixture、steady runtime、整個 Job ETA、PDU gross energy 是不同量，不能互換。缺真實 PDU 欄位/時區/取樣語意或未完成完整訓練時，`evaluation_complete` 不應視為已達成。
