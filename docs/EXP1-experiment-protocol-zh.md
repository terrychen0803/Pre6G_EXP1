# EXP1 實驗設計與結果判讀

## 研究問題與驗證單位

問題是：master 利用每個 GPU 節點的短時間 dry-run、指定的 runtime model 與 power model，能否預測同一訓練工作在 RTX4090／RTX5090 的相對耗電，並選出實測耗電較低的節點？每次 validation 必須讓**兩個候選節點都跑完整、無 profiler 的訓練**；只跑被選節點無法驗證選點。

每一筆樣本定義為「一個 batch 設定 × 一次獨立重複 × 一個節點」。一組兩節點結果共用同一份凍結的來源 YAML、資料集生成規則、映像版本、master 模型版本與 dry-run 設定。現有正式來源是 batch 16、1639 epochs、512 train images、每 epoch 32 iterations，共 52,448 iterations。舊 `formal-cross-node-ranking-input.json` 的 960 units 屬短 fixture，不得與正式來源交叉比較。

## Ground truth 的時間邊界

使用成功 Pod 中 `trainer` container 的 `state.terminated.startedAt` 和 `finishedAt`。這是完整訓練程序的起訖，涵蓋程式啟動、模型初始化、全部 epochs 與程序結束；不含 Kubernetes Job 建立、排程等待、image pull、init container 的合成資料生成或部署時間。Pod 必須 `Succeeded`、trainer exit code 0；同節點若有重試或多個 Pod，這次樣本須人工審查並排除自動彙總。

PDU 實測 gross energy 也積分**同一個 trainer 時窗**。先確認 Excel 的 Outlet 對應、時間時區、W／kW 單位與取樣語意。瞬時功率用邊界內插加梯形積分；每段平均功率用其真實區間積分。缺少完整覆蓋、重複時間戳或過大缺口時不報耗電結果。PDU 量的是整機及背景負載，另記錄 idle baseline、其他負載與 GPU sharing 狀態；不得將 gross energy 宣稱為純 GPU energy。

## 輸入凍結與重複次數

1. 先記下新 master IP、K8s context、node hostname/GPU UUID、PVC/NFS、監測端點、PDU Outlet 對應、時鐘同步、映像 digest 和 Git commit；核對已部署的叢集，不重裝 K8s。
2. 對每個 batch（建議先用 8、16、32）複製來源 Job、兩份正式 Profile Job 與 cross-node config，讓 `batch=N` 及 `pre6g.io/total-work-units=epochs × ceil(512/N)` 一致。其餘模型、資料、影像尺寸、AMP、epochs 與 profiler 120 秒保持一致。若訓練長度因 batch 而顯著變化，記錄實際差異，不能宣稱所有 batch 都是 40 分鐘。
3. 每個 batch 至少取得 3 次成功的兩節點完整配對。每次採新 run ID、validation ID 和輸出目錄，先 fresh dry-run／預測，再跑兩節點完整訓練。失敗樣本保留並列出，不用成功樣本取代而隱匿。
4. 模型切換只在 master 指令的 `--candidate-runtime-model NODE=PATH`／`--candidate-power-bundle NODE=PATH`；每次保留模型 JSON、bundle manifest、ONNX、scaler 的 hash。不能在看到該次 ground truth 後修改同次預測。

## 指標

| 指標 | 定義 | 解讀限制 |
| --- | --- | --- |
| 選點命中 | 預測選中的 node = 兩節點 PDU gross Wh 較小的 node | 只對這次候選、工作與負載條件成立 |
| 選點命中率 | 成功且 PDU 完整的配對中命中次數 ÷ 完整配對次數 | 同時報分子／分母與各 batch 結果 |
| 完整訓練時間 | `trainer.finishedAt - trainer.startedAt` | 排程與資料 init 時間不計入 |
| 穩態 runtime proxy 誤差 | `(預測 steady runtime - 實測完整 trainer 時間) / 實測時間` | 兩者邊界不同，屬診斷 proxy，不能稱為完整 Job ETA 誤差 |
| 完整訓練 ETA 誤差 | 只有模型狀態 `ready` 且提供 `predicted_total_job_runtime_s` 時才計算 | 未校準時報 `null` |
| PDU gross energy | trainer 時窗下電力積分，Wh | 包含整機與背景負載 |
| steady energy proxy 誤差 | `(預測 steady gross Wh - 實測 trainer gross Wh) / 實測 Wh` | 穩態與全程邊界不同，另記錄 idle／背景負載 |

`scripts/summarize_validation_series.py` 只納入 `evaluation_complete=true` 的結果；每個 batch 分開報告成功數、排除數、命中率與絕對誤差平均值。應另外公開每次原始誤差與失敗原因，避免平均值掩蓋節點或批次差異。是否達到預先設定的容許誤差，需在看 ground truth 前寫下門檻；本離線版不憑空宣稱模型已通過。

## 每次實驗的必要證據

保存 `cross-node-plan.json`、resolved dry-run YAML、`cross-node-run-summary.json`、`ranking-input.json`、`provisional-ranking.json`、`validation-plan.json`、`validation-jobs.yaml`、`ground-truth-run.json`、Pod JSON、兩節點 log、PDU 原始 Excel 的 SHA256／欄位說明、正規化 CSV／provenance、`validation-result.json` 和系列摘要。含站點 IP 或原始 PDU 的檔案留在私有儲存，不提交公開 repo。

目前模型 bundle 的 `validation_required` 狀態不會因程式能執行而自動解除。最後報告應區分「流程跑通」、「此工作下選點命中」、「模型誤差達事前門檻」三件事。
