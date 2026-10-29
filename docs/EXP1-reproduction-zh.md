# EXP1 重現手冊：現有 K8s → dry-run → master 預測 → ground truth

目前 RKE2/HAMi/Nsight 2026.5.1 的環境、配置與 Preflight/Profiling 操作以 [Environment Setup](environment-setup.md)、[Configuration](configuration.md)、[Data Collection Guide](data-collection-guide.md) 為準。本文保留完整 Ground Truth、PDU 匯入和重複實驗流程；本次沒有執行實機 GPU Job，4090 同 Trace 特徵一致性仍待驗證。以下指令於 Master/control 端的 Repository 根目錄執行。
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

把版本與站點資料記在該次 run 的私有記錄中：`git rev-parse HEAD`、`python --version`、`kubectl version`、`helm version`、三台 `hostname`／`/etc/os-release`／NVIDIA driver 與 GPU UUID、master 新 IP、PDU outlet 對應、映像 digest、資料集識別、Nsight 版本。`configs/site.local.yaml` 依配置說明填寫；**不提交**帶有站點資訊或叢集 Token 的檔案。執行前先確定本地提交已推送，因為 worker Job 會從這個 repo clone 並 checkout `--worker-commit`。

## 1. 核對現有叢集

依 [Environment Setup](environment-setup.md) 核對 RKE2、HAMi `nvidia.com/gpu`、Nsight 2026.5.1 `target-linux-x64/nsys`、Netdata/DCGM 1 Hz、CephFS RWX 與 Collector tolerations。複製 `configs/rke2.example.yaml` 為 `configs/site.local.yaml` 並填妥真實 identity。不要修改 Frozen Power manifest 的歷史 Node Binding；同機同卡更名使用明確 deployment alias，詳見配置說明。`cluster/` 舊 device-plugin/NFS 範本不可直接套用到現有 HAMi。

## 2. 凍結同一份 workload 與 master 模型

以下 node override/PDU 範例使用研究室具名節點；若採通用配置，請全部替換為 `candidates[].node`。Override 非必要，亦可直接用 config 中模型。

正式 40 分鐘來源檔為 `examples/yolo26/formal-40min-source-job.yaml`，固定 1639 epochs、512 張合成 train 圖、batch 16、52,448 training iterations；兩份 `formal-40min-dryrun-120s.yaml` 用**同一完整 workload**，Nsight 只擷取前 120 秒。來源、兩份模板的 `model`、`batch`、`epochs`、`imgsz`、`amp`、`val`、`save`、`patience` 必須一致。此 repo 原有的 960-unit 比較屬另一個短 fixture，不能拿來驗證這份 52,448-unit Job。

master 的模型由 CLI 決定。每個 candidate 都要指定相容 runtime JSON 和 node-bound power bundle；若省略 override，使用 cross-node YAML 的預設值。更換模型請用**新的 run ID 和輸出目錄**，並記錄訓練來源與模型 SHA-256。`cross-node-plan.json` 會記錄 runtime JSON、power manifest、ONNX、scaler 的 hash。4090 預設改用 clean／compute-load soft-gate 模型，但尚缺同一筆 trace 的特徵抽取比對；現有模型仍為 deployment-smoke／`validation_required`，因此此排名明確是研究用 provisional 排名。

```bash
export CTX="$(kubectl config current-context)"
export COMMIT="$(git rev-parse HEAD)"
export RUN=exp1-b16-r01
CROSS_ARGS=(
  --mode cross-node --cross-node-config configs/site.local.yaml
  --run-id "$RUN" --worker-commit "$COMMIT" --output-dir "generated/$RUN"
  --candidate-runtime-model worker-4090=models/runtime/RTX4090_yolo_soft_gate_v1.json
  --candidate-runtime-model worker-5090=models/runtime/RTX5090_yolo_trace_only_v1.json
  --candidate-power-bundle worker-4090=models/power/bundles/pdu1-outlet1-20260416-20260612
  --candidate-power-bundle worker-5090=models/power/bundles/pdu1-outlet7-20260416-20260612
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

Preflight 做依賴、context、node、PVC、模型載入、DCGM endpoint 和 Kubernetes server-side dry-run 檢查；smoke 會要求 GPU resource 並建立短暫工具 Pod（不執行 YOLO 或 CUDA trace） 核對 GPU UUID、Nsight、RWX PVC、Netdata/DCGM 連線。`--execute` 才同時建立 RTX4090／RTX5090 的 120 秒 Profile Jobs；完成後 master 收回 PVC 產物並輸出 `ranking-input.json` 與 `provisional-ranking.json`。必須檢查 `cross-node-run-summary.json.status=completed`、兩個候選都有 artifacts、時間對齊 gate 通過，且 `ranking-input.total_work_units=52448`。失敗時保留 Pod/log/trace，改用新 run ID，不把半套結果送入驗證。

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
  --execute
```

第二個命令預設沒有監控期限，Job 也沒有固定三小時 deadline。中斷或逾時後用同一 execution 目錄及 `--resume` 補收集；不要再次 execute。詳細操作見 [長時間 Ground Truth 手冊](ground-truth-collection.md)。

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
  --pdu "worker-4090=generated/$VALIDATION/pdu-4090.csv" \
  --pdu "worker-5090=generated/$VALIDATION/pdu-5090.csv" \
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

1. 每次 run 的 kube context、真實 GPU UUID、Netdata host route、版本/digest 與 CephFS/CSI 狀態核對。
2. 真實 PDU Excel 範例與欄位／取樣語意，才能把上面的欄名與 gap 閾值改成已驗證的值。
3. 一次 clean-cluster preflight、兩節點 dry-run、每節點完整 Job 與 PDU 覆蓋實測。離線測試無法替代這些證據。
