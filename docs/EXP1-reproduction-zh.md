# EXP1 重現手冊：現有 K8s → dry-run → master 預測 → ground truth

本版以**三台機器已部署 K8s/k3s**為起點。master IP 已改變；先查驗現況，不能直接沿用舊叢集證據中的 IP、Pod IP、GPU UUID 或 kube context。以下指令在可存取叢集的 master/control 端、專案根目錄執行。`generated/` 保存每次實驗輸出，不提交含站點資料或原始 PDU Excel 的檔案。

## 0. 凍結本次環境與程式版本

```bash
git clone https://github.com/terrychen0803/Pre6G_EXP1.git
cd Pre6G_EXP1
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-power-model.txt
python -m pip install -r requirements-evaluation.txt
python -m pip install -e .
git rev-parse HEAD
kubectl config current-context
kubectl get nodes -o wide
```

把版本與站點資料記在該次 run 的私有記錄中：`git rev-parse HEAD`、`python --version`、`kubectl version`、`helm version`、三台 `hostname`／`/etc/os-release`／NVIDIA driver 與 GPU UUID、master 新 IP、PDU outlet 對應、映像 digest、資料集識別、Nsight 版本。`cluster/site.example.env` 是記錄欄位範本，複製後填寫；**不提交**帶有站點資訊或 k3s token 的檔案。執行前先確定本地提交已推送，因為 worker Job 會從這個 repo clone 並 checkout `--worker-commit`。

## 1. 核對現有叢集，不重新安裝

```bash
kubectl get nodes -o wide
kubectl get runtimeclass nvidia
kubectl -n experiments get pvc pre6g-artifacts -o wide
kubectl get pv pre6g-artifacts-nfs -o yaml
kubectl -n netdata get pods,svc -o wide
kubectl -n gpu-monitoring get pods,ds -o wide
kubectl get pods -A -o wide
```

兩個 GPU node 必須 `Ready`、無 `DiskPressure`，且 `allocatable.nvidia.com/gpu.shared >= 1`；collector/master node 也必須 `Ready`，能排 Pod 並讀同一個 RWX PVC。`kubectl get node <name> -o json` 的 hostname 要與 `examples/yolo26/formal-cross-node-dryrun.yaml`、四份 `k8s/formal/` Job 的 nodeSelector 一致。若重灌後名稱改了，先同步更新 YAML、power manifest 的 `node_binding`，並以 `nvidia-smi --query-gpu=uuid --format=csv,noheader` 核對實際 GPU UUID；不能只改名稱而沿用舊節點的 power 模型品質主張。

master IP 改變後，檢查 kubeconfig 的 server 位址、NFS PV `spec.nfs.server`、NFS export／防火牆，以及各 node 的 Netdata Parent 連線。**DCGM 的 Pod IP 不寫回 YAML**：cross-node preflight 會依 node 尋找 Ready exporter，執行前即時解析。若上述資源缺失，停止進入 dry-run；`cluster/` 有 GPU sharing 與 NFS 的參考範本，重新部署時再用。套件安裝請遵循 [K3s 官方文件](https://docs.k3s.io/quick-start)、[NVIDIA device plugin](https://github.com/NVIDIA/k8s-device-plugin#shared-access-to-gpus)、[Netdata Kubernetes](https://learn.netdata.cloud/docs/netdata-agent/installation/kubernetes) 與 [DCGM Exporter](https://docs.nvidia.com/datacenter/dcgm/latest/gpu-telemetry/dcgm-exporter.html)，並凍結當次實際版本；本次已安裝的叢集不需重做安裝。

各 GPU node 核對 `nvidia-smi`、`/opt/nvidia/nsight-systems-cli/2026.4.1/bin/nsys --version`；Job 需要完整 Nsight installation root 可在 `/opt/pre6g/nsight` 掛載。核對 Netdata Parent 對兩個 hostname 的歷史查詢、DCGM 四項 GPU 指標與約 1 秒 cadence，以及 master/worker 時鐘同步。詳細欄位和閾值見 [監測 preflight](monitoring-preflight.md)。若任何模型要求的 telemetry 欄位缺失，停止 power ranking。

## 2. 凍結同一份 workload 與 master 模型

正式 40 分鐘來源檔為 `examples/yolo26/formal-40min-source-job.yaml`，固定 1639 epochs、512 張合成 train 圖、batch 16、52,448 training iterations；兩份 `formal-40min-dryrun-120s.yaml` 用**同一完整 workload**，Nsight 只擷取前 120 秒。來源、兩份模板的 `model`、`batch`、`epochs`、`imgsz`、`amp`、`val`、`save`、`patience` 必須一致。此 repo 原有的 960-unit 比較屬另一個短 fixture，不能拿來驗證這份 52,448-unit Job。

master 的模型由 CLI 決定。每個 candidate 都要指定相容 runtime JSON 和 node-bound power bundle；若省略 override，使用 cross-node YAML 的預設值。更換模型請用**新的 run ID 和輸出目錄**，保留原始模型檔與訓練來源。`cross-node-plan.json` 會記錄 runtime JSON、power manifest、ONNX、scaler 的 hash。現有模型仍為 deployment-smoke／`validation_required`，因此此排名明確是研究用 provisional 排名。

```bash
export CTX="$(kubectl config current-context)"
export COMMIT="$(git rev-parse HEAD)"
export RUN=exp1-b16-r01
CROSS_ARGS=(
  --mode cross-node --cross-node-config examples/yolo26/formal-cross-node-dryrun.yaml
  --run-id "$RUN" --worker-commit "$COMMIT" --output-dir "generated/$RUN"
  --candidate-runtime-model iccl-s3-251230=models/runtime/RTX4090_yolo_trace_only_v1.json
  --candidate-runtime-model mirc516-20250605=models/runtime/RTX5090_yolo_trace_only_v1.json
  --candidate-power-bundle iccl-s3-251230=models/power/bundles/pdu1-outlet1-20260416-20260612
  --candidate-power-bundle mirc516-20250605=models/power/bundles/pdu1-outlet7-20260416-20260612
)
python scripts/run_experiment_pipeline.py "${CROSS_ARGS[@]}"
```

此時只產生計畫，請核對 `cross-node-plan.json`、`dryrun-jobs.yaml`、模型 hash 與 worker commit。請勿直接提交含 `DCGM_ENDPOINT_UNRESOLVED` 的計畫 YAML。

## 3. Preflight → smoke → 真正的跨節點 dry-run

沿用第 2 步的 `CROSS_ARGS`，依序執行下列三個命令；同一 run 的輸入不能在三步間變更：

```bash
python scripts/run_experiment_pipeline.py "${CROSS_ARGS[@]}" --preflight-only --kube-context "$CTX"
python scripts/run_experiment_pipeline.py "${CROSS_ARGS[@]}" --smoke-only --kube-context "$CTX"
python scripts/run_experiment_pipeline.py "${CROSS_ARGS[@]}" --execute --kube-context "$CTX"
```

Preflight 做依賴、context、node、PVC、模型載入、DCGM endpoint 和 Kubernetes server-side dry-run 檢查；smoke 建立短暫工具 Pod 核對 GPU UUID、Nsight、NFS、Netdata/DCGM 連線。`--execute` 才同時建立 RTX4090／RTX5090 的 120 秒 Profile Jobs；完成後 master 收回 PVC 產物並輸出 `ranking-input.json` 與 `provisional-ranking.json`。必須檢查 `cross-node-run-summary.json.status=completed`、兩個候選都有 artifacts、時間對齊 gate 通過，且 `ranking-input.total_work_units=52448`。失敗時保留 Pod/log/trace，改用新 run ID，不把半套結果送入驗證。

## 4. 產生未開 profiler 的完整 ground-truth Jobs

```bash
export VALIDATION=exp1-b16-full-r01
python scripts/run_experiment_pipeline.py \
  --mode validation --job examples/yolo26/formal-40min-source-job.yaml \
  --ranking-input "generated/$RUN/ranking-input.json" \
  --validation-id "$VALIDATION" --target-minutes 40 \
  --output-dir "generated/$VALIDATION"
```

檢查 `validation-plan.json` 的兩個 node、`planned_total_work_units=52448`、selected node、模型限制，以及 `validation-jobs.yaml` 中的 `trainer` 沒有 Nsight。兩個完整 Jobs 使用相同來源 YAML、資料集和 batch；都執行完才能判斷選點是否正確。`predicted_steady_runtime_s` 只涵蓋 steady work，不可當作已校準的整個 Job ETA。

```bash
python scripts/run_validation_jobs.py \
  --plan "generated/$VALIDATION/validation-plan.json" \
  --jobs "generated/$VALIDATION/validation-jobs.yaml" \
  --kube-context "$CTX" --output-dir "generated/$VALIDATION/preflight"

python scripts/run_validation_jobs.py \
  --plan "generated/$VALIDATION/validation-plan.json" \
  --jobs "generated/$VALIDATION/validation-jobs.yaml" \
  --kube-context "$CTX" --output-dir "generated/$VALIDATION/execution" \
  --timeout-seconds 10800 --execute
```

第二個命令會真正啟動兩個完整訓練。它先做 server-side dry-run、兩節點工作內容一致性與 Job 名稱檢查，再建立 Jobs，等待完成，保存 `validation-pods.json`、各節點 log、events 和輸入 hash。若任何 Job 失敗或重試，不把它算作成功樣本。時間 ground truth 使用 `trainer` container 的 `startedAt`／`finishedAt`；K8s 排程、Pod 建立與 init container 資料生成不列入模型誤差。PDU 也只積分相同的 trainer 時窗。

## 5. 匯入 PDU Excel 並做實測耗電比較

先取得去識別化範例，確認 Excel 的工作表、欄名、Outlet ↔ node 對應、功率單位、時間時區，以及**每列是瞬時功率或一段期間的平均功率**。原始 `.xlsx` 連同檔案 hash 私下保存；不要把未知欄位直接當成 W 或把無時區時間當 UTC。

例如確認每列是帶時間戳的瞬時 W 值後，對兩個 outlet 各匯出一份標準 CSV：

```bash
python scripts/normalize_pdu_excel.py --input pdu-original.xlsx \
  --sheet SHEET_NAME --timestamp-column TIME_COLUMN --power-column POWER_COLUMN \
  --outlet-column OUTLET_COLUMN --outlet-value OUTLET_FOR_4090 \
  --timezone-offset +08:00 --unit W \
  --output "generated/$VALIDATION/pdu-4090.csv"

python scripts/normalize_pdu_excel.py --input pdu-original.xlsx \
  --sheet SHEET_NAME --timestamp-column TIME_COLUMN --power-column POWER_COLUMN \
  --outlet-column OUTLET_COLUMN --outlet-value OUTLET_FOR_5090 \
  --timezone-offset +08:00 --unit W \
  --output "generated/$VALIDATION/pdu-5090.csv"

python scripts/run_experiment_pipeline.py --mode validation-evaluate \
  --validation-plan "generated/$VALIDATION/validation-plan.json" \
  --pods-json "generated/$VALIDATION/execution/validation-pods.json" \
  --pdu "iccl-s3-251230=generated/$VALIDATION/pdu-4090.csv" \
  --pdu "mirc516-20250605=generated/$VALIDATION/pdu-5090.csv" \
  --pdu-mode point-sample --pdu-max-gap-seconds ACTUAL_MAX_ALLOWED_GAP \
  --output-dir "generated/$VALIDATION/evaluation"
```

`ACTUAL_MAX_ALLOWED_GAP` 必須依電表真實取樣週期設定。point-sample 以相鄰功率做線性插值／梯形積分，要求覆蓋完整 trainer 時窗且無超過門檻的缺口。若 Excel 每列其實是**前／後五分鐘平均值**，改用 `--pdu-mode interval-average --pdu-interval end` 或 `start`；不能沿用瞬時模式。`validation-result.json` 中 `evaluation_complete=true` 才能報告 `selection_match`。請同時記錄節點其他負載、GPU occupancy 與 idle baseline，因 PDU 是整機 gross energy。

## 6. 更換 batch 與重複實驗

每個 batch 使用獨立的來源 Job、4090/5090 Profile 模板與 cross-node config；可直接複製 YAML，整合程式不用改。同步修改 `batch=N`、來源註記 `pre6g.io/total-work-units=epochs × ceil(512/N)`、兩節點 Profile 命令，並確認資料集、模型、`imgsz`、AMP 等其他條件一致。為每個 batch、每次重複給不同 run ID／validation ID；先重新 dry-run 與預測，再跑完整 Job。不能把 batch 16 的模型預測直接配 batch 32 的完整 Job。建議至少 3 個 batch 設定、每個設定 3 次成功重複；在固定其他條件下收集選點命中率、steady runtime/energy proxy error 分布與失敗原因。先用短 fixture 檢查流程，正式 ground truth 必須使用已凍結的完整 Job。

```bash
python scripts/summarize_validation_series.py \
  --run 16=generated/exp1-b16-full-r01/evaluation/validation-result.json \
  --run 16=generated/exp1-b16-full-r02/evaluation/validation-result.json \
  --run 32=generated/exp1-b32-full-r01/evaluation/validation-result.json \
  --output generated/validation-series.json
```

摘要只計入 `evaluation_complete=true` 的 run，逐 batch 報告選點命中率與 proxy 誤差。完整紀錄必須保留每次輸入 hash、模型 ID、叢集版本、原始 Pod JSON、PDU 原檔、正規化 CSV、錯誤樣本與執行時間，才能複核架構是否可行。

## 離線版尚需現場補齊

1. master 新 IP、目前 kube context、三台實際 hostname/GPU UUID 與 NFS/Netdata URL 的現況核對。
2. 真實 PDU Excel 範例與欄位／取樣語意，才能把上面的欄名與 gap 閾值改成已驗證的值。
3. 一次 clean-cluster preflight、兩節點 dry-run、每節點完整 Job 與 PDU 覆蓋實測。離線測試無法替代這些證據。
