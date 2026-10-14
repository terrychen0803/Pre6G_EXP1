# Pre6G EXP1：跨節點預測與實測驗證

這個專案從 [Pre6G_experiment](https://github.com/terrychen0803/Pre6G_experiment) 的 `cc8d0abf2573e7388fcaff7043132d393f2dafe0` 基線延伸。目標是在已部署 K8s 的三台機器上核對環境，對 RTX4090／RTX5090 同時做 dry-run，在 master 選擇 runtime 與 power model 並排名，接著讓**所有候選節點**執行同一份未開 profiler 的完整 Job，用 `trainer` 容器完整執行時間與同期 PDU 電力資料檢驗預測及節點選擇；K8s 排程與部署等待不列入模型誤差。

## 目前狀態

- **離線可檢查：**程式、固定 YOLO26 YAML、模型檔、叢集範本、執行步驟及結果摘要已收錄。
- **尚待實機驗證：**master 新 IP、現有 K8s 的節點與 GPU/Nsight/Netdata/DCGM/NFS 狀態，以及 PDU Excel 真實欄位與取樣語意。沒有實機資料時，不能宣稱 ground truth 已完成。
- 現有 power bundle 仍標記 `validation_required`。跨節點排名是研究用 provisional 結果；不等於正式 production 自動選點已通過品質門檻。

## 從這裡開始

1. [完整重現與驗證手冊](docs/EXP1-reproduction-zh.md)：從核對現有 K8s 到 PDU 評估的逐步指令與每步通過條件。
2. [實驗設計與結果判讀](docs/EXP1-experiment-protocol-zh.md)：batch、重複次數、ground truth 定義與不可混用的資料。
3. [原專案架構](docs/architecture.md)及[先前證據](docs/project-assessment-zh.md)：作為歷史基線，日期與舊叢集狀態不能代表重灌後現況。

## Master 端模型切換

跨節點入口可在**同一個指令**逐節點指定 runtime JSON 與 power bundle 目錄，不必修改整合程式或測試 YAML：

```bash
python scripts/run_experiment_pipeline.py \
  --mode cross-node \
  --cross-node-config examples/yolo26/formal-cross-node-dryrun.yaml \
  --run-id exp1-b16-r01 \
  --worker-commit "$(git rev-parse HEAD)" \
  --candidate-runtime-model iccl-s3-251230=models/runtime/RTX4090_yolo_trace_only_v1.json \
  --candidate-runtime-model mirc516-20250605=models/runtime/RTX5090_yolo_trace_only_v1.json \
  --candidate-power-bundle iccl-s3-251230=models/power/bundles/pdu1-outlet1-20260416-20260612 \
  --candidate-power-bundle mirc516-20250605=models/power/bundles/pdu1-outlet7-20260416-20260612 \
  --output-dir generated/exp1-b16-r01
```

以上預設只產生計畫，不會建立 Kubernetes Job。切換新模型時指定新路徑即可；程式會檢查節點/GPU、YOLO26 `yolo-v1` 相容性，並把所選模型及 scaler 雜湊寫入 `cross-node-plan.json`。目前 runtime 引擎接受 `pre6g.runtime-model/v1` 的 `ridge_log_runtime`；其他模型格式需要新增 adapter 後才能載入。

完整訓練、PDU Excel 匯入與多次結果彙整請依[重現手冊](docs/EXP1-reproduction-zh.md)操作。
