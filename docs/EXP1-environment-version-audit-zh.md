# EXP1 工具版本與環境確認紀錄

本文件用於每次 EXP1 跨節點實驗前的環境盤點與放行。請在 **master/control、RTX4090 worker、RTX5090 worker** 各自確認，將「本次實測值」與命令輸出存到該次 run 的私有證據目錄。下列「倉庫設定」是程式目前要求；「歷史紀錄」只供比對，**不代表重灌或 master IP 變更後的現況**。

適用流程：[EXP1 重現手冊](EXP1-reproduction-zh.md) → [監測 preflight](monitoring-preflight.md) → [實驗設計](EXP1-experiment-protocol-zh.md)。若任一必要項目未通過，停止正式 dry-run；若 power telemetry 或 PDU 語意未通過，停止 energy ranking／ground-truth 結論。

## 1. 本次 run 的識別與保存方式

先建立一份不公開的紀錄。**不要提交** kubeconfig、k3s token、內網 IP、PDU 原始 Excel 或含站點資訊的輸出。使用 [site.example.env](../cluster/site.example.env) 記下站點欄位，但該檔是人工紀錄範本，不會自動修改 Job YAML。

| 欄位 | 本次實測值／證據位置 |
|---|---|
| 檢查日期、操作者、run ID | 待填 |
| EXP1 完整 Git SHA；已推送並可由 worker 取得 | 待填 |
| kube context、API server、master 新 IP | 待填，私下保存 |
| master／RTX4090／RTX5090 的實際 hostname 與 Kubernetes node name | 待填 |
| GPU 型號、UUID、PDU outlet 對應 | 待填，私下保存 |
| 資料集／來源 Job 的 SHA256、容器 image digest | 待填 |
| 各機器版本紀錄與 preflight 輸出目錄 | 待填 |

在專案根目錄記錄：

```bash
git remote -v
git rev-parse HEAD
git status --short
python3 --version
python3 -m pip --version
python3 -m pip freeze
kubectl config current-context
kubectl version -o yaml
helm version --short
```

**通過條件：**工作樹與本次要執行的程式一致；`--worker-commit` 使用完整、已推送的 40 位 SHA；本次實測版本及原始輸出可追溯。

## 2. 工具與依賴版本矩陣

「待查」表示倉庫沒有鎖定或無法從舊紀錄推定。請在三台機器及實際容器中填入本次值；不要將「倉庫設定」抄成「已安裝版本」。

| 元件 | 倉庫設定／最低要求 | 歷史紀錄（僅參考） | 本次實測值／證據 |
|---|---|---|---|
| Git、kubectl、Helm | 版本未鎖定；記錄 `git --version`、`kubectl version`、`helm version` | 待查 | 待填 |
| Python | `pyproject.toml` 要求 `>=3.10`；CI 使用 3.11 | RTX5090 舊證據為 3.12.3 | 待填 |
| PyYAML | `6.0.2` | 待查 | 待填 |
| numpy、onnxruntime | `numpy>=1.26,<3`、`onnxruntime>=1.18,<2`；master 要有 CPUExecutionProvider | 待查 | 待填 |
| openpyxl | `3.1.5`，PDU Excel 匯入用 | 待查 | 待填 |
| setuptools | build-system `>=68`，未鎖定確切版 | 待查 | 待填 |
| K3s／Kubernetes | 必須記錄實際 server/client 版本 | 舊基線 `v1.35.4+k3s1` | 待填 |
| OS／kernel | 三台各自記錄 `/etc/os-release`、`uname -r` | 舊基線：master Ubuntu 22.04.4／6.8.0-136；4090 Ubuntu 24.04.3／6.17.0-40；5090 Ubuntu 24.04.3／7.0.0-30 | 待填 |
| NVIDIA driver、GPU UUID、CUDA | 各 worker 查 `nvidia-smi`；GPU UUID 必須與本次模型綁定及 Job 一致 | 舊基線：4090 driver 595.84；5090 driver 580.173.02；5090 `nvidia-smi` 曾回報 CUDA 13.0 | 待填 |
| NVIDIA container toolkit／RuntimeClass | 實際版本待查；Job 要求 `runtimeClassName: nvidia` | 舊基線有 `nvidia` RuntimeClass | 待填 |
| NVIDIA device plugin | chart、image digest 待查；必須提供 `nvidia.com/gpu.shared` | 舊基線每 worker allocatable 為 4；新 `cluster/device-plugin-config.yaml` 範本為 `replicas: 2`，兩者不可視為同一設定 | 待填 |
| Nsight Systems | Job 固定掛載主機完整目錄 `/opt/nvidia/nsight-systems-cli/2026.4.1`，Pod 內用 `/opt/pre6g/nsight/bin/nsys` | 舊證據 `2026.4.1.191-264138605071v0` | 待填 |
| Netdata Child／Parent | chart、image digest 待查；兩 worker 的 Parent host 歷史資料須可查 | 舊基線要求重新 audit；不可沿用舊 Ready 狀態 | 待填 |
| DCGM Exporter | chart、image digest 待查；兩 worker 各需一個 Ready exporter，約 1 秒取樣 | 舊基線曾運作；Pod IP 不得固定沿用 | 待填 |
| NFS server/client、PV/PVC | NFS 版本與主機套件待查；`pre6g-artifacts` 必須是 Bound RWX NFS | 舊基線有共享 artifact store | 待填 |
| Ultralytics 訓練／profile 映像 | `ultralytics/ultralytics:8.4.104`；記錄實際 `imageID` digest、容器內 Python／PyTorch／Ultralytics 版本 | 標籤不是不可變 digest | 待填 |
| Nsight smoke 映像 | `nvidia/cuda:12.8.1-devel-ubuntu22.04`；記錄實際 digest | 待查 | 待填 |
| artifact collector 映像 | `busybox:1.36`；記錄實際 digest | 待查 | 待填 |
| power model／runtime model | 記錄 runtime JSON、manifest、ONNX、scaler 的 SHA256；node／GPU 綁定必須相符 | power bundle 仍為 `validation_required` | 待填 |
| PDU 韌體／匯出工具 | 倉庫未鎖定；記錄型號、韌體、匯出格式與時間／功率語意 | 待查 | 待填 |

版本來源：[pyproject.toml](../pyproject.toml)、[requirements.txt](../requirements.txt)、[requirements-power-model.txt](../requirements-power-model.txt)、[requirements-evaluation.txt](../requirements-evaluation.txt)、[CI 設定](../.github/workflows/cross-node-tests.yml)、[舊叢集基線](cluster-baseline.md)、[RTX5090 舊證據](evidence/rtx5090-k3s-profile-e2e.md)、[Nsight smoke Job](../k8s/nsys2026-rtx-smoke.yaml)、[正式來源 Job](../examples/yolo26/formal-40min-source-job.yaml)。每次實驗以本次採集值為準。

## 3. 三台主機與叢集檢查

在三台主機各執行並保存輸出：

```bash
hostname
cat /etc/os-release
uname -r
timedatectl status
git --version
python3 --version
```

GPU worker 另執行：

```bash
nvidia-smi --query-gpu=name,uuid,driver_version,memory.total --format=csv,noheader
nvidia-smi
/opt/nvidia/nsight-systems-cli/2026.4.1/bin/nsys --version
nvidia-container-cli --version
```

在 master/control 執行並保存完整 JSON／YAML：

```bash
k3s --version
kubectl get nodes -o wide
kubectl get nodes -o json
kubectl get runtimeclass nvidia -o yaml
kubectl get pods -A -o wide
kubectl -n experiments get pvc pre6g-artifacts -o yaml
kubectl get pv pre6g-artifacts-nfs -o yaml
kubectl -n netdata get pods,svc -o wide
kubectl -n gpu-monitoring get pods,ds -o wide
helm list -A
```

若 master 不安裝 `k3s` CLI，從對應 K3s server／agent 主機記錄版本。若 `nvidia-container-cli` 不存在，記錄安裝套件與 runtime 設定，並以 Pod GPU smoke 驗證實際可用性。

**放行條件：**

- [ ] 三台 node 為 Ready，無 DiskPressure；master 可排 collector Pod，兩個 worker 各有至少 1 個可分配的 `nvidia.com/gpu.shared`。
- [ ] 實際 GPU sharing resource 名、replicas、chart/image 版本已記錄；新範本的 `replicas: 2` 與舊基線的 allocatable 4 差異已解釋。
- [ ] `nvidia` RuntimeClass 可用，GPU Pod 內能看見與 power manifest 相同的 UUID；未把 `nvidia.com/gpu` 靜默改成 `nvidia.com/gpu.shared` 或反向改動。
- [ ] kubeconfig server 指向本次 master；NFS PV 的 server/export path 是本次有效值，PVC 為 Bound、RWX，兩 worker 與 collector 可讀同一檔案。
- [ ] 三台時鐘同步，並保存偏移／時間來源；Pod、Netdata、DCGM、PDU 時間能以 UTC 對齊。
- [ ] worker 有足夠磁碟與 imagefs 空間。舊基線曾因拉取約 4.6 GB 的訓練映像而發生 DiskPressure eviction。
- [ ] Job 的 `nodeSelector`、`GPU_UUID`、Netdata `/host/<hostname>`、Nsight hostPath 與現況一致；大小寫也須核對。舊證據顯示 4090 的 Netdata Parent host route 使用大寫 hostname。

## 4. 容器、GPU 與 Nsight 的實際版本

容器標籤只說明要求，不足以固定位元內容。在 Pod 存在期間記錄每個相關 Pod 的 `status.containerStatuses[].imageID`；若同一標籤在兩個 worker 對應不同 digest，先停止比較。範例：

```bash
kubectl -n experiments get pod <POD_NAME> -o jsonpath='{range .status.containerStatuses[*]}{.name}{"\t"}{.image}{"\t"}{.imageID}{"\n"}{end}'
kubectl -n experiments exec <RUNNING_POD_NAME> -c <CONTAINER_NAME> -- python3 -c 'import sys, torch, ultralytics; print(sys.version); print(torch.__version__); print(ultralytics.__version__)'
```

在 Nsight smoke Pod 執行期間確認 `/opt/pre6g/nsight/bin/nsys --version`、GPU UUID、CUDA trace 與 SQLite export。僅在主機找到 `nsys` 不足以證明 Pod 內的完整 installation root、權限及 GPU tracing 可用。不要使用 PATH 中可能較舊的 `nsys` 取代 Job 的絕對路徑。

## 5. Netdata、DCGM 與電力模型的放行條件

- [ ] Netdata Child 在兩 worker Ready，Parent 可分別以**本次實際 host 名**查詢 `/host/<hostname>/api/v1/allmetrics?format=json`。
- [ ] Parent 歷史查詢有 `system.cpu`、`system.load`、`system.ram`、CPU 溫度與至少 3 條可用的 app CPU chart；兩個 power model 要求的欄位均可由 Netdata/DCGM 產生。只看到非空 `allmetrics` 不算通過。
- [ ] 每個 worker 正好找到 1 個 Running/Ready DCGM Exporter Pod；四項指標 `DCGM_FI_DEV_GPU_UTIL`、`DCGM_FI_DEV_FB_USED`、`DCGM_FI_DEV_GPU_TEMP`、`DCGM_FI_DEV_POWER_USAGE` 含正確 GPU UUID；取樣約 1 秒。
- [ ] 監測窗口對齊品質通過：Netdata、DCGM 最大 gap 各不超過 2 秒，且應用程式時窗兩端有足夠覆蓋。採用 [監測 preflight](monitoring-preflight.md) 的完整欄位與品質門檻。
- [ ] Master 能載入兩個 ONNX bundle；runtime model 的 GPU／YOLO26／detector profile 與候選相符；保留 runtime JSON、manifest、ONNX、scaler 的 hash。若實際 node 或 GPU UUID 更換，不沿用舊 node-bound 模型的品質宣稱。
- [ ] `validation_required` 的 power bundle 只可作研究用 provisional 排名；不得標示為已驗證 production 自動選點。

先執行 [重現手冊第 2–3 步](EXP1-reproduction-zh.md) 的計畫、preflight、smoke，再執行正式 `--execute`。Preflight 會即時解析 DCGM Pod IP，**不要把舊 Pod IP 寫回 YAML**。保存 `cross-node-plan.json`、`dcgm-endpoints.json`、resolved YAML、`smoke-result.json` 與 kubectl logs。

## 6. PDU 與 ground truth 確認

- [ ] PDU outlet 與兩個實際 node 的對應、整機／GPU 量測範圍、韌體版本及背景負載已記錄。
- [ ] 原始 Excel 的 SHA256、工作表、時間欄、功率欄、W／kW、時區和取樣週期已確認；原檔留在私有儲存。
- [ ] 每列是「瞬時功率」或「區間平均」已確認。瞬時資料用 `point-sample` 與真實最大缺口；區間平均資料只有在**確定每列代表 300 秒**時，才能使用現有 `interval-average` 評估程式。
- [ ] PDU 完整覆蓋兩個 `trainer` container 的 `startedAt` 至 `finishedAt`，無重複時間戳或不可接受缺口；完整 Job 無重試且兩節點使用同一 workload。
- [ ] `validation-result.json` 的 `evaluation_complete=true` 後，才報告 `selection_match`；穩態 runtime proxy 與完整 Job ETA 分開報告。

## 7. 最終判定

| Gate | 狀態（PASS／FAIL／待查） | 證據路徑／失敗原因 |
|---|---|---|
| Git、Python、K3s/Kubernetes、OS、driver、映像 digest 已凍結 | 待查 | 待填 |
| 三 node／GPU sharing／RuntimeClass／磁碟與時鐘合格 | 待查 | 待填 |
| NFS RWX 與 Nsight Pod 內 smoke 合格 | 待查 | 待填 |
| Netdata 歷史欄位／DCGM UUID 與 cadence／模型依賴合格 | 待查 | 待填 |
| 雙節點 120 秒 profile、artifact 與 alignment gate 合格 | 待查 | 待填 |
| 雙節點無 profiler 完整 Job 與 PDU 覆蓋合格 | 待查 | 待填 |
| 模型事前誤差門檻與選點結果合格 | 待查 | 待填 |

**判定規則：**前四項未全數 PASS，不啟動正式 profile；第五項未 PASS，不產生有效跨節點排名；第六項未 PASS，不報 ground truth；第七項未 PASS，不宣稱模型完成驗證。CI 單元測試成功僅代表離線程式測試成功，不能取代上述實機證據。
