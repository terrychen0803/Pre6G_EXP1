# 選點決策延遲量測（未來整合）

**狀態：獨立量測工具，尚未接入正式 `--execute` 流程。** `scripts/measure_decision_latency.py` 會重播一次已完成的跨節點實驗，在 master 上重新執行現有的資料檢查與裁切、runtime 預測、power 預測和排序。它不會建立 Kubernetes Job，也不會改寫原實驗的預測或排名產物。

## KPI 定義

本工具量測「master 決策處理延遲」：所有候選節點的必要 artifacts 已在 master 本機後，從開始執行 `run_cross_node_dryrun._predict()` 到 `provisional-ranking.json` 寫完。每次重播用同一個 master 程序的 `time.perf_counter_ns()` 取差值，單位為毫秒。預設判定為 **30 次成功重播的平均值 ≤ 100 ms**；報告另列中位數、P95、最大值及每個外部指令耗時。

測量包含：workload discovery、兩個候選節點的驗證與 telemetry 裁切、各節點的 runtime/aggregate/power Python 程序啟動與模型載入、ONNX 推論、產物寫入、排序程序與排名檔寫入。量測前複製 artifacts 到獨立暫存目錄的時間不計入；這對應到「master 已收到資料」的起點。

測量**不含** worker profiling、Job 每 5 秒輪詢、collector Pod 建立與等待、`kubectl cp` 傳輸。因此它不是「worker 完成到選點」的端到端延遲，更不是「任務提交到執行」的等待時間；不能把這三個值混用。

## 執行

在原來執行跨節點實驗的 master/control 環境，使用仍可讀取的模型、power bundle 和完整 `generated/<run-id>`：

```bash
python scripts/measure_decision_latency.py \
  --run-dir generated/<run-id> \
  --repeats 30 \
  --warmups 0 \
  --threshold-ms 100
```

預設輸出 `generated/<run-id>/decision-latency-benchmark.json`。如需另存，使用 `--output <path>`。程式會先檢查該 run 已完成、原排名與 run ID 相符、模型和 power bundle 的 SHA-256 未變、兩個候選的 artifacts 齊全；每次重播必須選出與原 run 相同的節點。任一重播失敗會在報告中列出錯誤，整次 KPI 判定失敗。

`--warmups` 可排除指定次數的暖機重播；報告會保留暖機次數。比較實驗必須固定候選數、模型、master 硬體、依賴版本、暖機設定和輸入 run。若要評估冷啟動，使用預設 `--warmups 0`。平均值通過之外，也應檢查 P95、最大值與失敗數，避免少數慢任務被平均值掩蓋。

## 未來接入正式流程

1. 在 `_collect()` 最後一份必要檔案完成複製時記錄 `master_received`；在正式 `_predict()` 排名寫出後記錄 `selected`，以同一個 master 單調時鐘計算線上決策延遲。本工具的重播結果只作為事前性能基線。
2. 若要衡量 worker 完成到選點，讓每個 worker 在所有 profiling/特徵/telemetry 產物寫完後記錄 `bundle_ready` UTC 時間，並確認節點時鐘同步；同時分段記錄 Job 偵測、collector 啟動、傳輸與決策耗時。
3. 若平均值超過 100 ms，先依報告的 `command_statistics` 找出耗時階段，再評估是否需要常駐 master 程序、預載 ONNX session 或改變候選節點的處理方式。只有正式流程真的採用預載時，才應把其初始化排除在 KPI 之外。

