# RKE2/HAMi/Nsight 適配：2026-10-16 離線驗證

## 基準與範圍

本地原 main `6aef3b6`，遠端 main `ab2a20c7ded276a179d48f48a1efcc5afdb4afad`；先 fetch、fast-forward，再建立 `feat/rke2-hami-nsys-2026-5-1`。保留原未追蹤 `netdata-audit.json`、`scripts/query_netdata_window.py.bak`，不納入 Commit。未使用 reset --hard／force push，未修改叢集部署、Driver/HAMi/Operator、Node 或 PVC。

此紀錄只描述離線軟體測試。使用者回報的 RKE2、1 Hz monitoring 及共享儲存驗證與本次 regression 是不同證據來源；本次未呼叫 kubectl 建立實際 Pod/Job，也未執行 GPU 工作負載。

## 執行結果

專案 venv：Python 3.12；PyYAML 6.0.2、NumPy 2.5.4、ONNX Runtime 1.31.0、openpyxl 3.1.5。主機最初缺 pip/ensurepip，使用官方 PyPA bootstrap 僅安裝到專案 `.venv`，沒有安裝系統套件。

```bash
python -m unittest discover -s tests -v
```

**103 tests，全部 PASS，沒有 skipped。** 包含 `test_runtime_model.py`、`test_cross_node_dryrun.py`、pipeline、validation/GT、Netdata history、telemetry alignment、4090/5090 CLI Feature Artifact integration，以及新 RKE2/CephFS/Taint/UUID/Hash/v1 相容測試。

Frozen Reference：4090 clean score=0.1815933454732066、runtime=46.492214348373416ms；high-load score=0.8184066545267936、runtime=65.75360910796043ms；5090 C03 runtime=109.25171448874927ms。CLI integration 同時核對 Feature/Prediction/Semantic Runtime Schema、Model ID、Device、Detector 與可選 score。4090 fixture 為 frozen centroid 構造向量，5090 為既有 C03 regression 向量，**均不是 RKE2 新 Trace 驗證**。

```bash
python scripts/check_manifests.py k8s/formal/*.yaml \
  k8s/nsys2026-rtx-smoke.yaml examples/yolo26/*job.yaml \
  configs/artifacts-pvc.example.yaml --kubernetes-schema-dir .testdeps/kubernetes
```

**10 份靜態 manifests PASS**：YAML 重複 key、Bash syntax、Kubernetes v1.35.0 standalone strict schema。目標是 1.35.6 RKE2；靜態 upstream schema 不能取代目標 RKE2 admission/RuntimeClass/HAMi 的 live server-side dry-run。

Schema 來源：[kubernetes-json-schema 的 versioned directory](https://github.com/yannh/kubernetes-json-schema/tree/master/v1.35.0-standalone-strict)，預先下載到被忽略的 `.testdeps/kubernetes`，檢查本身離線執行。準備方法：

```bash
mkdir -p .testdeps/kubernetes
SCHEMA_BASE=https://raw.githubusercontent.com/yannh/kubernetes-json-schema/master/v1.35.0-standalone-strict
curl -fsSL "$SCHEMA_BASE/job-batch-v1.json" -o .testdeps/kubernetes/job.json
curl -fsSL "$SCHEMA_BASE/pod-v1.json" -o .testdeps/kubernetes/pod.json
curl -fsSL "$SCHEMA_BASE/persistentvolumeclaim-v1.json" -o .testdeps/kubernetes/pvc.json
sha256sum .testdeps/kubernetes/*.json
```

另以真正 pipeline 的 plan-only 模式生成正式 52,448-unit workload 計畫；兩份 Job + 一份 Collector 的 Schema/Bash 檢查 PASS，沒有建立 Kubernetes 資源。檢查 current Node、HAMi key、Nsight 2026.5.1 root/CLI、Ceph PVC、Collector tolerations、兩模型版本/Hash 與 detector binding。既有 model source/parameters 計算路徑保持原樣。

## Frozen Artifacts 未變

`git diff -- models/ src/pre6g_experiment/runtime_model.py` 為空；完整 Runtime Artifact SHA-256：

| Artifact | SHA-256 |
|---|---|
| RTX4090_yolo_soft_gate_v1.json | `a89e3f980a9836129b65d78d09bb8cb9b6e6336b272751b382e34a8ade2db75f` |
| RTX5090_yolo_trace_only_v1.json | `f6bc6800424c1d524863f441c8bfe7674116aba58c4b9e6dcaf8c376f4d3aa53` |

環境適配沒有改 Runtime Model loader、Model Schema、Frozen Parameters、Model ID、Detector 輸入或計算結果。Semantic Runtime 僅增加可選負載分數的原值傳遞與 Schema range 描述；5090 仍不輸出此欄。Master 另外檢查模型檔在 planning 後沒有變更。

## 未執行／仍待驗證

- Live RKE2 preflight/admission、操作者 pods/proxy 權限、Worker 到 exporter 網路。
- 此次設定中的 GPU UUID/Netdata host route 與同機同卡/PDU 對應核對；alias 不代表整機模型有效。
- 新 Nsight 安裝在 Kubernetes 的 CUDA Trace、GPU Metrics、HAMi 互動與 overhead。
- 新配置的 Worker peer-read 與 Master Collector readback 實測；使用者提供的先前共享測試不代替本次 run evidence。
- 4090 Frozen Soft Gate 訓練抽取器與 EXP1 同 Trace 特徵一致性。
- 真正 Profiling → Runtime/Power Prediction → Ranking → 全候選完整 Ground Truth/PDU 的研究驗證。
- RTX3090 Driver/NVML mismatch 與 GPU Operator ClusterPolicy readiness，未處理。

因此不能宣稱正式 Runtime Prediction、跨節點 Scheduling 或整條研究流程已成功重現。

Schema cache SHA-256（本次實際檢查）：

- `job.json`：`514d04ed5540a4d6354bff1ace0bd5155c98c58bcd33a030d1500427387fa97f`
- `pod.json`：`ab3eec94c85e6b166a62312ddb919fb747a53d1f5701a77befb17419361b52b0`
- `pvc.json`：`6136871ac0337f11a48da5231a2807407c7386967e3432f344141b902f6a538c`

## 公開資訊處理

依使用者要求，公開設定、模板、文件及測試中的站點 hostname/UUID/Netdata route 已匿名化；IP mocks 使用 IANA documentation address。測試只在 temporary config 中讀取原有 Frozen Power manifest 的 binding。未修改任何 Frozen Artifact 或原始歷史 Binding，也未改寫 Git 歷史；原有模型檔及既有歷史內容仍存在於 main。正式執行須私下配置 verified identity。

## GitHub CI history requirement

PR 第一輪 CI 的 checkout 預設 depth=1，無法讀取 `ab2a20c7` 的舊 v1 模板，故 legacy compatibility test 失敗。Workflow 已設 `fetch-depth: 0`，保留完整歷史供 frozen baseline regression 使用；本地完整 Repository 的 103 項測試通過。這是 Git fixture 可取得性問題，不是模型推論變更。

## PR #4 main 同步與衝突處理

PR #3 功能分支以 merge commit 整合 `main` 的 PR #4。唯一 content conflict 在根目錄 README 的狀態段落：保留 RKE2/HAMi/Nsight 2026.5.1/CephFS、匿名化 Quick Start 與交接文件，另外明列 RTX4090/RTX5090 為目前正式候選、RTX3090 為 Archived Candidate，尚未接入 EXP1 推論／跨節點選點。

`models/runtime/candidates/RTX3090/` 的四份檔案直接取自 main，保留原始 bytes（含換行格式）與 SHA-256，沒有調整係數、Schema 或 crossfit predictions。4090/5090 Artifact 與 `runtime_model.py` 也保持原樣。整合後重新執行完整單元測試與靜態／生成 YAML 檢查；不執行 GPU 工作負載，不合併 PR 到 main。
