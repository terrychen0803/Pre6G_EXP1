# 故障定位

先讀 `cross-node-run-summary.json` 的 `phase/error` 及 `logs/`；外層 `run-summary=completed` 不代表 GPU 已執行。依 run ID 保存原始資料與錯誤，不重用失敗 run 作成功樣本。

| 現象 | 檢查與處理 |
|---|---|
| `venv` 失敗、缺 ensurepip | 由環境管理員安裝對應 Python venv 套件，或使用可用的隔離環境；不要對系統 Python 使用 sudo pip |
| NumPy / ONNX / CPUExecutionProvider 缺失 | 啟用 `.venv`，`python -m pip install -r requirements-dev.txt -e .`；Preflight 在 GPU Job 前載入 ONNX |
| v2 deployment 缺失、placeholder UUID | 依 configuration.md 補完整 deployment 和 verified UUID；不要將 schema 改成 v1 以跳過檢查 |
| 模板／source workload 不同 | 核對 stage、epochs、batch、train samples、amp、val/save/patience；保留 formal 1639 epochs/52448 units；改部署不要改研究條件 |
| Power bound to another node / UUID differs | 核對硬體 inventory；只在同機同卡更名時填歷史 `power_binding_node`，不可修改 Frozen manifest 或假造 UUID |
| Model/Feature device 或 detector mismatch | 4090 使用 soft-gate v2、5090 使用 ridge v1，均 yolo-v1；v2-long 只作診斷；不要改 features 迎合模型 |
| Frozen artifact changed | 先比較 plan Hash、`git diff -- models/`、備份 Hash；恢復已核准的 Artifact 來源，使用新 run，不改 checksum 掩蓋變更 |
| Collector Pending / blocking taints | `kubectl describe node MASTER`、`kubectl -n experiments describe pod COLLECTOR`；檢查 Ready、DiskPressure、unschedulable、PVC 與明確 tolerations；不要移除 Node taint |
| GPU Job Pending | 檢查 RuntimeClass、HAMi 註冊 resource、requests=limits、Allocatable、scheduler event；20 是虛擬份額，非實體卡數。不同 scheduler/份額策略需現場驗證 |
| Exporter 找不到／不唯一／not Ready | 核對 namespace/selector、spec.nodeName、Pod rollout/deletionTimestamp；不要寫死 Pod IP；Pod 重建後重新 Preflight |
| Pod proxy Forbidden | 操作者需 exporter namespace 的 list Pods/get pods/proxy；請管理員最小授權，既有 namespace-rbac 不是完整操作者權限 |
| DCGM UUID metric 缺失 | 在選中 Node 的 exporter 核對四項 metrics 的 UUID label、實體卡可見性和 DCGM/NVML；不讀另一個 node 的 Service 流量替代 |
| Preflight 過但 Worker HTTP 失敗 | API proxy 和 Worker → exporter Pod 網路路徑不同；查 NetworkPolicy、CNI、port、URL，重跑工具 Smoke |
| Nsight binary/library 不存在 | 核對 Host `/opt/nvidia/nsight-systems-cli/2026.5.1` 與容器 `/opt/pre6g/nsight/target-linux-x64/nsys`；掛完整 root。不要把舊 bin/nsys 與新 target-linux-x64 混用 |
| Nsight CPU Sampling permission | 已知 perf_event_paranoid=4；維持 sample=none/cpuctxsw=none；不調低 sysctl、不宣稱 CPU Sampling 已驗證 |
| CUDA Trace／GPU Metrics 不可用 | 保存 Nsight version、GPU/Driver、stderr/report；分別驗證 CUDA Trace 與 GPU Metrics，不能用 --version PASS 取代 |
| PVC/PV 非 Bound/RWX 或 class 不符 | 查實際 claimRef、CSI driver/volumeHandle、StorageClass 和容量；此版本接受 RWX CSI/CephFS 或 NFS。不要刪 PVC 重新測試 |
| Worker 寫入／Master readback 失敗 | 查 mount 權限、Ceph quota、Node taint/網路；用 smoke marker、read-only Collector 驗證；emptyDir/local-path 不等於共享儲存 |
| Netdata history 缺 CPU/app chart | 核對 Parent host route、streaming、retention、1 Hz DB 與 apps collector；瞬間 allmetrics 成功不足。至少三個有效 app CPU series，缺值不補零 |
| alignment coverage/gap 失敗 | 保存兩份 raw CSV、request latency、DB timestamps、NTP 偏差；核對 1 Hz cadence、750ms tolerance、2s gap。不放寬門檻來宣稱舊結果通過 |
| `kubectl cp` 失敗 | Collector image 需 tar；確認 Pod Ready、namespace、container 名稱及權限；刪 Pod 前先保留失敗資料 |
| Profile timeout / SIGTERM | Capture 120s 可接受 wrapper 所定義的 143；不能據此接受其他錯誤或稱完整訓練成功。讀 command/telemetry return codes 和 detector quality |
| Ground Truth 失敗／重試 | 只接受所有候選自然完成且無重試的相同 trainer workload；保留 Pod JSON/exit reason，不將排程等待放進 runtime error |
| PDU evaluation 不完整 | 核對 outlet、W/kW、時區、point vs interval-average、完整 trainer 時窗覆蓋與實際 gap；缺實測資料不補 Mock |

目前 RTX3090 Driver/NVML mismatch 與 GPU Operator ClusterPolicy readiness 是獨立環境問題。此專案不自行重啟節點、調整 Driver/HAMi/Operator 或刪儲存資料；由環境管理員另行處理並保存紀錄。
