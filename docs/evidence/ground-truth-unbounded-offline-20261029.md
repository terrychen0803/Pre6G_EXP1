# Ground Truth 無期限監控：2026-10-29 離線驗證

基準為最新 main `0fba8914795828d7e43b2123b7ebe59c578c55a5`（PR #7）。原工作區有四份已修改檔案與一份未追蹤腳本，因此在獨立 worktree／`feat/ground-truth-unbounded` 開發，未覆寫既有修改。

## 軟體驗證

- `PYTHONPATH=src python -m unittest discover -s tests -v`：**126 項全部 PASS，無 skipped**。包含原模型／跨節點／評估 regression，以及 18 項新的 Ground Truth monitor API 模擬。
- 新測試涵蓋模擬持續 Running 超過 4 小時後 Complete、第二候選在第一候選 Running 時 Failed、FailureTarget→Failed、Complete 的 trainer 時窗/Job completionTime、失敗/重啟 logs、API 暫時中斷、可選 65 秒 monitor deadline、Ctrl-C/SIGTERM、提交與首輪查詢之間中斷、相同 UID 恢復、拒絕更換 UID/修改 input Hash、timeout 後補收集、logs 缺失後重收、無效結果阻止評估。
- 預設 CLI timeout=0、負數拒絕；不用 `kubectl wait --timeout=0s`，也不發出 delete/patch/suspend。resume/attach 不建立 Jobs。
- 10 份靜態 manifests 加 2 份實際 planner CLI 生成的完整 Ground Truth Jobs：YAML duplicate-key、Bash syntax、Kubernetes v1.35.0 standalone strict Schema 全部 PASS。Schema 預先下載快取，檢查本身離線執行；不是 live RKE2 admission 測試。
- 真正 `discover_work.py`／`plan_validation_runs.py` 離線 CLI PASS；生成命令改為 execute/resume，無固定 3h wait。文件本地連結與監控 CLI help 核對通過。

## 不變條件

兩個 Ground Truth source YAML 只移除 `activeDeadlineSeconds: 10800`；正式 epochs=1639、batch=16、52,448 training iterations、dataset/hyperparameters、patience=0 與 trainer startedAt/finishedAt 的時間定義不變。

`models/**`、`src/pre6g_experiment/runtime_model.py`、`k8s/**`、`scripts/run_cross_node_dryrun.py` 均與 main 無差異：4090 Soft Gate v2、5090 Ridge v1、3090 Archived Candidate 全部保留，沒有重訓／修改模型 Hash，120 秒 Nsight 擷取和 Smoke 限制也不變。

新增的 Pod export `ground_truth_valid` marker 只防止不完整或失敗的收集結果進入評估；原始 Kubernetes Pod JSON 保存在 evidence snapshots，runtime/energy 計算公式不變。正常完成是所有固定 workload 自然 Complete 且證據有效，沒有新增 profiler 或訓練 callback 來推定 Work Units。

## 仍待實機驗證

本次沒有呼叫 kubectl 建立工作負載、沒有執行 GPU／改動叢集。長時間 HAMi workload、真實 Complete/Failed/Signal/網路故障恢復、Pod/log retention、PDU 整時窗覆蓋與資源值班流程仍須現場驗證。Mock PASS 不代表正式 Ground Truth 或模型準確率通過。
