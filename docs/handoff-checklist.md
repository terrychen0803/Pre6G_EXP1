# 正式資料收集前 Handoff Checklist

每次交接以新 run ID 保存勾選證據、操作者、時間、檔案路徑；單純勾選不能替代資料。離線測試 PASS 只完成軟體層驗收。

## Repository 與 Frozen Models

- [ ] Clone 的 commit 已推送、包含 `ab2a20c7` 及 RKE2 適配；工作樹修改/依賴版本另記錄。
- [ ] 4090 soft-gate v2 與 5090 ridge v1 的 Model ID、Device、Detector yolo-v1、SHA-256 正確；沒有重訓或改 frozen parameters。
- [ ] 執行 `python -m unittest discover -s tests -v`，包括兩個 Frozen Reference、Trace Feature Artifact CLI integration 與跨節點測試；保存完整 log。
- [ ] YAML/Bash syntax 與 Kubernetes Schema 檢查通過；live server-side dry-run 尚須在目標 API/admission 重做。
- [ ] 確認 4090 同 Trace 特徵抽取一致性仍未完成；沒有將 regression fixture 當新 Trace 證據。

## 環境與配置

- [ ] OS/Kernel、RKE2/containerd/CNI、Driver/CUDA、Nsight、HAMi/Operator、Netdata/DCGM、Ceph image/chart/digest 保存。
- [ ] Master/4090/5090 Ready、無 DiskPressure；核對 RuntimeClass、HAMi `nvidia.com/gpu` 和虛擬份額，不誤報實體 GPU 數。
- [ ] 每個 candidate 的 Node/Device/實體 GPU UUID、Netdata host route 現場確認；歷史 power hostname alias 有同機同卡 inventory/PDU 證據。
- [ ] GPU Operator readiness 與 3090 mismatch 仍獨立記錄；沒有宣稱整個 Operator 已健康。
- [ ] config 的 namespace/source/PVC/exporter/Collector tolerations/resources/paths 一致；不混用舊 k3s/gpu.shared/NFS 範本。
- [ ] 計畫的 source/template/config/model Hash、worker commit 已凍結；正式 workload 的 batch/dataset/epochs/GT 定義不變。
- [ ] Master 和 Worker 時鐘同步、timezone 與 UTC ns 規則確認。
- [ ] Netdata Parent/Child 1 Hz history、所需 CPU/apps/temperature 指標與 retention 足夠；Top GPU process 欄位缺口保留。
- [ ] DCGM exporter server interval=1000ms、Client poll=1000ms，Node/UUID 的四項 metric 正確。
- [ ] CephFS RWX PVC Bound/class/容量/權限；Master 與兩 Worker 讀寫 evidence 保存，trace retention/備份空間足夠。

## 分階段實機 Gate

- [ ] plan-only 成功；檢查 summary 真正 status，不只看外層 pipeline completed。
- [ ] live preflight/context/model dependencies/exporter UUID/server dry-run 通過，未建立正式 GPU Job。
- [ ] 工具 Smoke 的 Nsight version/GPU UUID/HTTP/GitHub/Worker peer-read 通過且 Pods 已刪；Master Collector readback 另有證據。
- [ ] CUDA/Nsight Job Smoke 已另行驗證，Report/SQLite 已備份；GPU Metrics 若未驗證維持 pending。
- [ ] 兩個候選 Profile Jobs 成功、identity 一致、至少三個完整 cycles、quality gate 通過，raw reports/CSV/provenance 齊全。
- [ ] Runtime/Power 推論與 provisional 排名輸出可追溯；4090 score 保留、model Hash 未變；仍不稱正式模型有效。
- [ ] 完成 4090 training pipeline 與 EXP1 同 Trace features consistency 後，才重新評估正式 Runtime Prediction 主張。
- [ ] 所有候選的完整 unprofiled trainer 相同 workload、自然結束、無 retry；Pod JSON/起迄/exit code 保存。
- [ ] 真實 PDU Excel 欄位/outlet/單位/時區/取樣語意確認，覆蓋相同 trainer 時窗；`evaluation_complete=true` 才報 selection_match。
- [ ] 記錄背景負載、profiling overhead、power validation_required 與 steady/full-job/gross energy 的差異。

## 交接包

- [ ] Git commit/PR、模型 Hash 清單、私有 site config、環境 inventory、完整 run 目錄與 SHA-256 備份齊全。
- [ ] 保存失敗樣本與 logs/events，不刪除以粉飾成功率；不足 gate 明確列 pending。
- [ ] 確認沒有 Token/Password/Secret/kubeconfig/PDU 私有資料進入 Git；共享資料備份及 retention 的負責人已指定。
- [ ] 下一位研究人員依 README → environment-setup → configuration → data-collection-guide 可從新 clone 完成離線計畫，再依許可時段驗證 live gates。

## 長時間 Ground Truth

- [ ] 來源與生成的完整訓練 Jobs 都沒有 `activeDeadlineSeconds`；epochs/batch/work units 不變，舊 run 不混用新 plan。
- [ ] `--timeout-seconds 0` 是無整體監控期限；正數 timeout 不會停止 Kubernetes Jobs；120 秒 dry-run／Smoke 限制仍保留。
- [ ] 持久 output directory、plan/jobs Hash、context、Job UID 已記錄；僅一個 monitor process；中斷後使用 `--resume`，不重複建立 Jobs。
- [ ] 每 node 的 Complete/Failed/FailureTarget 與錯誤證據獨立追蹤；API 中斷時 Jobs 可能仍在執行。
- [ ] 完整 trainer 起迄、Pod JSON、所有 init/app logs、events/metadata 保存；`ground_truth_valid=true` 才交付評估，不使用失敗／不完整快照。
- [ ] 安排 GPU/CPU/memory、Node、log/storage/Netdata retention、PDU 長時窗覆蓋與備份值班；不提前清除 Job/Pod 證據。
- [ ] 已讀 [Ground Truth 收集與恢復手冊](ground-truth-collection.md)；Mock PASS 不代表實際長時間 GPU 驗證完成。
