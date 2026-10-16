# RKE2 研究環境：部署與版本核對

本文件是目前環境的入口。版本表來自 2026-10-16 使用者提供的站點驗證，不是本次代理重新量測；舊 `cluster-baseline.md` 和 `docs/evidence/*k3s*` 只作歷史證據。新站點須各自保存核對輸出。

## 環境矩陣

| 項目 | 目前站點 | 新站點必須記錄 |
|---|---|---|
| OS / Kernel / Architecture | 未提供各節點 OS 版本；不可推定相同 | `/etc/os-release`、`uname -a`、x86_64 |
| Kubernetes | RKE2 v1.35.6+rke2r1 | Server/Agent、containerd、CNI、kubectl |
| Nodes | 公開範例使用 `master-node`、`worker-4090`、`worker-5090`（匿名代稱） | 實際 Kubernetes hostname 和 Netdata host identity |
| GPU Driver | 4090/5090：595.99.02 | 每張實體 GPU UUID、型號、Driver/NVML |
| CUDA | Smoke image CUDA 12.8.1；YOLO image 內版本須實測 | Toolkit / PyTorch CUDA；`nvidia-smi` 的 CUDA 上限不代表 Toolkit |
| Nsight | 2026.5.1.161-265138896106v0 | 安裝包來源、SHA-256、完整版本 |
| GPU Runtime | RuntimeClass `nvidia`，HAMi `nvidia.com/gpu` | HAMi / Operator / toolkit chart 與 image digest |
| GPU Sharing | 每 Worker Allocatable 20 | **20 為虛擬份額，不是 20 張實體卡**；GPU UUID 才是實體 identity |
| Monitoring | Netdata Parent/Child；DCGM `gpu-operator` / `app=nvidia-dcgm-exporter` | chart/image、Parent route、1 Hz retention |
| Storage | `experiments/pre6g-artifacts`、CephFS `ceph-filesystem`、RWX 50Gi | provisioner、容量、quota、跨節點權限 |

## 新環境部署順序

1. 依 [RKE2 Requirements/Quick Start](https://docs.rke2.io/install/quickstart) 準備 OS、時間同步、網路及 Server/Agent，使用 `INSTALL_RKE2_VERSION` 明確凍結目標版本；Token 與 kubeconfig 私下分發。保存每節點安裝紀錄。現有站點不要重新安裝。
2. 由環境管理員依 [NVIDIA Driver 文件](https://docs.nvidia.com/datacenter/tesla/driver-installation-guide/) 與 [Container Toolkit 文件](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) 安裝相容 Driver/Runtime，對 RKE2 containerd 的實際路徑與設定進行核對。依既有 Operator/Flux 管理方式維護，不在研究腳本內修改 ClusterPolicy。
3. 依 [HAMi Configuration](https://project-hami.io/docs/v2.7.0/userguide/configure) 凍結 HAMi release 和 Node 註冊設定，核對 resource 名稱及每卡份額。HAMi 和 NVIDIA 官方 device plugin 的重複資源註冊可能衝突，見 [HAMi FAQ](https://project-hami.io/docs/faq)；既有 GPU Operator readiness 必須獨立調查，不能用兩張卡可用替代整體健康判斷。
4. 依 [Nsight Installation Guide](https://docs.nvidia.com/nsight-systems/InstallationGuide/) 安裝完整 x86_64 CLI 根目錄。此站點根目錄為 `/opt/nvidia/nsight-systems-cli/2026.5.1`，掛載進容器 `/opt/pre6g/nsight`，CLI 為 `/opt/pre6g/nsight/target-linux-x64/nsys`。HostPath 不只掛單一 executable，還需相依 library/resources。
5. 部署 Netdata Parent/Child、DCGM 及 RWX Storage，完成以下核對，才進入 Profiling。需要新增部署時由管理員依已選定的 chart 版本安裝；本次未改動任何部署。

各節點在 Host 上執行並保存到該 run 的私有 `environment/`：

```bash
cat /etc/os-release
uname -a
hostname
nvidia-smi --query-gpu=name,uuid,driver_version,memory.total --format=csv
/opt/nvidia/nsight-systems-cli/2026.5.1/target-linux-x64/nsys --version
sysctl kernel.perf_event_paranoid
timedatectl status
```

Master 執行：

```bash
kubectl version -o yaml
kubectl get nodes -o wide
kubectl get runtimeclass nvidia -o yaml
kubectl get nodes -o json
kubectl -n experiments get pvc pre6g-artifacts -o yaml
kubectl -n gpu-operator get pods -l app=nvidia-dcgm-exporter -o wide
```

不要保存 `kubectl config view --raw` 或 Secret。CPU Sampling 目前受 `perf_event_paranoid=4` 限制，維持 `--sample=none --cpuctxsw=none`；本次不降低系統限制，也不主張 GPU Metrics 已成功。參數語意依 [Nsight User Guide](https://docs.nvidia.com/nsight-systems/UserGuide/index.html)。

## Netdata Parent/Child

依 [Netdata Kubernetes 安裝文件](https://learn.netdata.cloud/docs/netdata-agent/installation/kubernetes) 選定 chart/image 版本。Child 需啟用 system CPU、memory、temperature 與 apps CPU，持續向 Parent streaming；Parent 的 retention 需覆蓋整個實驗及回查時間，目標 DB cadence 1 Hz。Streaming API key 存 Kubernetes Secret，不提交 Git。不要為研究查詢而直接公開 Child port。

每個候選的 `netdata_url` 必須是 Parent 的 `/host/<實際 Netdata hostname>`。Kubernetes Node 名稱與 Netdata host 可能不同，不能只按新 hostname 拼 URL 就視為完成遷移。

```bash
python scripts/audit_netdata.py \
  --node worker-4090=http://PARENT:19999/host/NETDATA_HOST_4090 \
  --node worker-5090=http://PARENT:19999/host/NETDATA_HOST_5090 \
  --output generated/netdata-readiness.json
```

以錄下的絕對時間範圍查 `/api/v1/data`，核對每秒 DB timestamp、13 項 system/CPU 欄位、至少三個可用 app CPU series；單次 `/allmetrics` 成功不能證明歷史時序完整。`system.load` 原始 5 秒點轉為 1 Hz 的既有政策保持不變；詳見 [Netdata Contract](netdata-contract.md)。Top GPU process 欄位仍缺已驗證 Collector。

## DCGM 1 Hz 與 Discovery

依 [DCGM Exporter 文件](https://docs.nvidia.com/datacenter/dcgm/latest/gpu-telemetry/dcgm-exporter.html) 在 GPU Worker 部署；核對 exporter container 的 `DCGM_EXPORTER_INTERVAL=1000`（ms）、四項 GPU metric、每筆 `UUID` label。Client 輪詢另設 `--interval-ms 1000`，兩者都需 1 Hz。

程式按 config namespace/selector 找到指定 Kubernetes Node 上唯一 Running/Ready、非 terminating 的 Pod，再透過 Kubernetes API Pod proxy 核對該實體 GPU UUID 的四項 metric。當次 Pod IP 寫入 `dcgm-endpoints.json` 和 resolved Job，不寫回模板。Pod 重建後需重新 Preflight。API proxy 驗證不代表 Worker 網路可直連 Pod IP，仍須 Smoke。

## CephFS 或其他共享儲存

先有已部署且健康的 CephFS CSI StorageClass，再由管理員參考 `configs/artifacts-pvc.example.yaml` 建立 RWX PVC；本次沒有 apply，也不會刪除現有 PVC。動態 CSI PV 必須 Bound、RWX 且 claimRef 對應 Namespace/PVC；Preflight 同時支援傳統 NFS PV。`deployment.storage_class` 可核對指定 StorageClass；改用 NFS 時填實際 class 或移除該欄，不能沿用 Ceph class 名稱。

容量 50Gi 是 PVC 請求，實際 Ceph quota/可用空間另行核對。完整 `.nsys-rep`／SQLite 會保存到共享目錄；正式收集前估算每 run 用量、下載及備份，不自動清理舊研究資料。Master 與兩 Worker 都需掛載相同 Namespace 的同一 claim 並驗證讀寫。Smoke 的 Worker 互讀與 Collector readback 是不同驗收項。

## 權限與未完成環境項目

研究入口使用操作者 kube context。需可讀 Nodes、RuntimeClasses、PV/PVC；在 exporter namespace 可 list Pods 與 get `pods/proxy`；在 experiments 可 create/get/list/watch/delete Jobs/Pods、get Pod logs 及 exec/cp（`pods/exec`）。Server-side dry-run 也需要相應權限。`k8s/namespace-rbac.yaml` 僅是原始應用端範本，**不是完整的操作者 RBAC**，請環境管理員依 namespace 最小授權，不授予廣域 cluster-admin 作通用預設。

目前仍待填：各節點 OS、Toolkit/PyTorch CUDA、HAMi/Operator/Netdata/Ceph chart/image digest、Netdata retention 與 PDU 欄位/取樣語意。RTX3090 mismatch 暫不處理；ClusterPolicy readiness 仍未解決。這些缺口必須保留於交接記錄。
