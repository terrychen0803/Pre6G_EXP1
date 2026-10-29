# 長時間 Ground Truth：完成全部 Work Units 後收集

此入口只用於未開 profiler 的完整 Ground Truth Jobs。正式 YOLO26 仍為固定 1639 epochs、batch 16、52,448 training iterations；`patience=0`、`val=False` 等研究定義保持原樣。正常完成依 Kubernetes Job `Complete=True` 判定，不以三小時或預估 runtime 截斷。模型、120 秒 Nsight dry-run 與 Smoke 限制均不變。

## 期限分成兩層

- **Job 執行期限：**兩份 Ground Truth source YAML 不再設定 `activeDeadlineSeconds`，planner/executor 也會拒絕仍含 Job／Pod deadline 的舊來源或生成檔案。這避免 Kubernetes 在三小時後終止訓練。舊 run 的已提交 Job 若仍帶 deadline，不會被此工具自動 patch；必須由研究人員另行檢查，不可把既有截斷樣本當成完整 Ground Truth。
- **監控期限：**`--timeout-seconds` 預設 **0（無整體等待期限）**。正整數只限制本次監控 attachment 的等待時間；到期保存證據並退出，不刪除、終止或 suspend Jobs。恢復時可重新設定本次等待期限。API 請求另有 30s request timeout／45s subprocess bound，並不代表 workload timeout。

程式每 30 秒以 validation label 一次取得所有候選 Job，再逐一檢查狀態；不會先等第一個節點完成才看第二個。API 暫時失聯會記錄 poll error 並重試，不假稱 Job 已失敗。間隔是正常查詢週期；API 延遲與證據下載會增加實際間隔，請另外監控叢集健康。Job `FailureTarget` 會立即標記 Failing 並保存證據，仍等待真正 `Failed`；`SuccessCriteriaMet` 不冒充 `Complete`。語意依 [Kubernetes Job terminal conditions](https://kubernetes.io/docs/concepts/workloads/controllers/job/#terminal-job-conditions)。

## Preflight 與無期限執行

先依 [重現手冊](EXP1-reproduction-zh.md) 產生 `validation-plan.json`／`validation-jobs.yaml`，確認來源不含 deadline，使用新的 validation ID，並保存 input Hash。以下在 Repository 根目錄、啟用 venv 後執行。`CTX` 必須是目標 context，`VALIDATION` 替換為本次 ID。

```bash
export CTX="$(kubectl config current-context)"
export VALIDATION=exp1-b16-full-r01

# 只檢查，不建立 GPU 工作負載
python scripts/run_validation_jobs.py \
  --plan "generated/$VALIDATION/validation-plan.json" \
  --jobs "generated/$VALIDATION/validation-jobs.yaml" \
  --kube-context "$CTX" --output-dir "generated/$VALIDATION/preflight"

# 以下才會建立真正的完整訓練 Jobs；省略 timeout 等同 --timeout-seconds 0
python scripts/run_validation_jobs.py \
  --plan "generated/$VALIDATION/validation-plan.json" \
  --jobs "generated/$VALIDATION/validation-jobs.yaml" \
  --kube-context "$CTX" --output-dir "generated/$VALIDATION/execution" \
  --execute
```

`--execute` 只在新 output record、相同 Job 名稱不存在時提交一次。提交之前寫入原子 checkpoint，所以 create 部分完成或監控中斷時仍可追溯。若部分候選根本沒有建立，會報 Missing，**不會**由 resume 補建，以免重複訓練或混合批次。

## 中斷、逾時與重新連接

Ctrl-C（SIGINT）／SIGTERM 只停止監控與證據收集。程式保存 `status=interrupted`，退出碼 130；Jobs 繼續由 Kubernetes 管理。正數 timeout 退出碼 2、`status=timed_out`，同樣不改 Jobs。使用持久目錄和同一份 plan/jobs/context 恢復：

```bash
python scripts/run_validation_jobs.py \
  --plan "generated/$VALIDATION/validation-plan.json" \
  --jobs "generated/$VALIDATION/validation-jobs.yaml" \
  --kube-context "$CTX" --output-dir "generated/$VALIDATION/execution" \
  --resume
```

`--resume` **不重新提交**、不做要求 GPU 空閒的排程 preflight，會核對原始 plan/jobs SHA-256、context、Job labels、完整 frozen Job spec 及已記錄 UID。相同名字被刪除再建立的 Job 會拒絕。若 interruption 前尚未觀察 UID，會在恢復時依完整 spec/labels 綁定，須人工確認不是另一次實驗。監控結束前已經完成的 Jobs 也可直接補收集。每次 invocation 的期限從該次 monitor 開始計算，不改原始 workload。

可選地只監控兩小時，之後恢復為無期限：

```bash
# 用於既有紀錄；7200 是本次監控期限，不是 Job deadline
python scripts/run_validation_jobs.py \
  --plan "generated/$VALIDATION/validation-plan.json" \
  --jobs "generated/$VALIDATION/validation-jobs.yaml" \
  --kube-context "$CTX" --output-dir "generated/$VALIDATION/execution" \
  --resume --timeout-seconds 7200
```

如果 Jobs 是先前用其他工具提交、或原本使用舊版 metadata，可以在**新輸出目錄**使用 `--attach`（參數與上方相同，換新的 output-dir）。它也只讀取／監控／收集，會核對當前 Job spec 和 labels，並記錄 UID。舊版帶 `activeDeadlineSeconds` 的 plan/jobs 必須保留原始證據，不能只刪本機 YAML deadline 後謊稱與叢集 Job 一致。

使用相同 output directory 時只允許一個 monitor process。SIGKILL、斷電、磁碟故障不能保證最後一次 logs 收集，但原子 checkpoint 不會被半寫的 JSON 取代；下次 `--resume` 可補收集。請使用 `tmux`／服務管理器及持久輸出磁碟，不依賴 SSH 連線存活。不要重複執行 `--execute` 或刪除仍在工作的 Jobs。

## 成功、失敗與證據

- 任一 Job `Complete` 或 `Failed`，立即保存目前所有候選的證據；即使一個失敗，仍監控其他候選直到各自終止。失敗的 reason/message 不會被另一個節點的 Running 掩蓋。
- 所有 Job Complete、每個 Job 恰好一個相同 UID 所有權的 Pod、Pod Succeeded、trainer exitCode=0/restartCount=0、Node 正確且 trainer 起迄有效，並且證據下載成功，才產生 `jobs_completed` 和 `ground_truth_valid=true`（退出碼 0）。這是收集有效性，不等於 PDU evaluation 或模型準確率已通過。
- 任一 Failed 結束後為 `jobs_failed`；Job Missing／被替換／workload 變更為 `monitoring_failed`；Complete 但缺 Pod/logs 等證據為 `collection_failed`。都不是有效 Ground Truth，可先保留資料並用 resume 補收集可恢復的缺口。
- Runtime 定義保持 trainer container `startedAt` → `finishedAt`；排程等待、init container 不列入 runtime。監控開始／完成時間和 Job 的 completionTime 都是額外 metadata，不拿來取代 trainer 時窗。

輸出目錄：

```text
execution/
  ground-truth-run.json       # 原子 checkpoint、attempts、每 node UID/status/conditions/時間、validity
  validation-jobs.json        # 最新 Job JSON
  validation-pods.json        # 最新 Pod List + ground_truth_valid 評估 gate
  events.json
  <node>.log                 # 各 init/app container logs 合併，附 Pod/container 標籤
  evidence/000001/            # 每次收集均有新目錄，不覆寫先前證據
    validation-jobs.json
    validation-pods.json     # 原始 API Pod JSON
    events.json
    <pod>.<container>.log
    <node>.log
    ground-truth-metadata.json
```

`ground-truth-run.json.finished_at` 是本次監控 invocation 結束，不是 workload 完成時間。Job completionTime／terminal transition 與 trainer 起迄分別存於 `job_states`。`attempts` 記錄中斷、timeout、resume 等操作；API/collection errors 保留，失敗快照不刪除。

評估時使用 `execution/validation-pods.json`，不要拿 `evidence/` 內原始快照跳過有效性 gate。`evaluate_validation_runs.py` 會拒絕 `ground_truth_valid=false` 的收集結果；原先 Pod exit status/PDU 完整覆蓋等條件仍保留。使用原生 kubectl Pod JSON 的舊流程沒有此新增 marker，研究人員仍須自行核對 Job Complete。沒有量測每一 Work Unit 的新增 profiler/marker；固定 epochs、patience=0、同一完整 workload 與自然 Complete 是正常完成條件，不因預測誤差而縮短工作量。

## 長時間資源監控與交接

無期限不代表無需看管：安排操作者檢查 Pending／無進展的 Running、Node/Driver 健康、GPU sharing 背景負載、CPU/memory、磁碟、Pod log retention、Netdata retention、PDU 全時窗覆蓋和備份容量。保留 Job/Pod，避免 TTL 或人工清理先刪掉已完成證據。API 失聯期間工作可能繼續運行，不要因為客戶端 timeout 重啟訓練。是否終止異常 workload 是操作者另行決定，本工具不自動終止。

正式執行前核對 [Handoff Checklist](handoff-checklist.md)。本次只做 Mock/離線測試，不代表長時間 GPU/PDU 資料收集已實機驗證。
