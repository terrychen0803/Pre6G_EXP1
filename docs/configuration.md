# 可重用配置與模型 Binding

建議以 `configs/rke2.example.yaml` 複製為被 Git 忽略的 `configs/site.local.yaml`。`examples/yolo26/cross-node-dryrun.yaml` 是匿名化短功能測試例；`formal-cross-node-dryrun.yaml` 是正式固定 workload 的匿名化例，兩者均已使用 RKE2 v2 配置。不要以舊 `cluster/site.example.env` 或 k3s 證據覆蓋新設定。

## 必改參數

| 設定 | 語意／修改方法 |
|---|---|
| `schema_version` | 新環境固定 `pre6g.cross-node-dryrun-config/v2`；必須帶 deployment。v1 為舊環境相容模式，預設 gpu.shared／2026.4.1，不能與新模板混用 |
| `namespace` / `source_job` | 所有 Job/PVC 同 namespace；若改 namespace，複製 source Job 並同步 metadata.namespace。相對路徑以 Repository 根目錄解析 |
| `experiment_stage` / `test_purpose` / `production_result` | 需與 source/template annotations 相同；固定 `production_result: false` |
| `artifact_pvc` | 已 Bound 的 RWX claim；跨節點共享，不是每節點 local-path |
| `collector_node` / `collector_image` | 能讀 PVC 的 Master；image 需有 sh、sleep、tar（kubectl cp 使用） |
| `collector_tolerations` | Kubernetes 原生 toleration list；範例只容忍 master/control-plane NoSchedule，其他 taint 需明確加入，不移除 Node taint |
| `deployment.gpu_resource` / `gpu_count` | HAMi `nvidia.com/gpu`、正整數份額；request=limit。與實體 GPU 數不同；完整 Ground Truth source Job 也需同步 |
| `deployment.runtime_class` | 此站點 `nvidia`；其他 RuntimeClass 必須自行驗證其 GPU 可見性 |
| `deployment.nsight_host_path` / `nsight_cli` | 完整 Host installation root 與容器掛載後 executable；不含 shell 空白字元，所有候選一致 |
| `deployment.storage_class` | 可選；填入時 Preflight 要求 PVC class 完全相符 |
| `dcgm_exporter.namespace` / `label_selector` / `port` | 此站點 `gpu-operator`／`app=nvidia-dcgm-exporter`／9400；selector 支援單一 key=value，Node 與 UUID 再次篩選 |
| `candidates[].node` / `device_id` / `gpu_uuid` | 真實 Kubernetes hostname、Frozen Model Device ID、實體 GPU UUID；禁止把 HAMi 份額視為實體 identity |
| `candidates[].netdata_url` | Parent `/host/<Netdata hostname>`；不要使用 load-balanced、未指定 host 的全域查詢 |
| `candidates[].profile_job_template` | 與 stage 相符的四份 `k8s/formal/` 之一；保留 YOLO26 命令及 Capture Policy |
| `candidates[].runtime_model` / `power_bundle` | 模型路徑；Runtime Device/Detector 綁定、Power ONNX/scaler/manifest Hash 寫入 plan |
| `candidates[].power_binding_node` | 可選的**歷史 hostname 對應**；必須與所選 Power manifest 原 binding 完全相同，GPU UUID 也必須完全相同 |
| `candidates[].resources.requests/limits` | 可選 CPU/memory override，例如 `requests: {cpu: "4", memory: 8Gi}`；GPU 由 deployment 統一處理，禁止在此再指定 GPU key |
| `long_detector_profile` | 可選 `yolo-v2-long`，僅診斷；Frozen Model 固定使用 `yolo-v1` |
| `full_job_eta_input` | 可選，需使用當前 node key、相同 total work units 及獨立的 overhead calibration；不因環境遷移偽造校準 |

v2 renderer 以配置更新 Namespace、Node Selector、identity env、GPU resource、PVC 及 Nsight root；生成後仍檢查 workload 與模型 binding。它不修改 Frozen Model，也不重新訓練。Source Job 是 Ground Truth 的來源，對 source 的部署欄位修改需保存在獨立檔案並記錄 Hash；正式 1639 epochs/52,448 units 不變。

## 更名與更換硬體

Power bundle 是歷史整機模型，不可直接修改 `models/**/manifest.yaml` 的 Node Binding。若確認只是**同一台機器和同一張卡更名**，才使用 `power_binding_node`；`cross-node-plan.json` 同時保留 current node、historical node、GPU UUID 及模型 Hash，Power Summary 仍輸出原始 `bound_node`。Collector/Prediction/Ranking 使用 current node。

同一 UUID 只能證明 GPU identity，不能證明 CPU/主機/PDU 環境完全沒變；操作者仍須保存 inventory/PDU 證據並標示 `validation_required`。若 GPU UUID 改變或換主機，不得使用 hostname alias 繞過模型限制；請提供已合法綁定且獨立驗證的 bundle，或停止排名、先收集診斷資料。本次不重訓、不改模型 Hash，不對新站點 power 品質作主張。

## 加入候選 GPU Node

複製相容候選 entry，填寫新 Node/UUID/Netdata host 及模型，使用相同 workload family。至少兩個不同 node；重複 node、錯 Device、非 yolo-v1 模型、跨 GPU UUID 的 Power bundle 都會拒絕。新 Device 若無相容 Frozen Runtime／Power Model，不應假借 RTX4090 或 RTX5090 ID 加入自動排名；先以獨立 smoke 收集資料。

## 模型選擇與不可變輸入

`run_experiment_pipeline.py` 可使用重複的 `--candidate-runtime-model NODE=PATH`、`--candidate-power-bundle NODE=PATH`；底層 `run_cross_node_dryrun.py` 的參數名稱是 `--runtime-model`、`--power-bundle`。不要將兩組名稱混用。

Plan 寫入 config/source/template、Runtime、Power manifest/ONNX/scaler SHA-256、worker commit、Device/Detector 與歷史 hostname。推論前重查模型 Hash；同 run 的 plan 與輸入不同會拒絕，請使用新 run ID 和目錄。Worker 必須 checkout 已 push 且含本次改動的完整 Commit。
