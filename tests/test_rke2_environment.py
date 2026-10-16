"""Mocked Kubernetes tests; these create no cluster resources."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from test_cross_node_dryrun import DRYRUN, CONFIG, COMMIT


class RKE2EnvironmentTests(unittest.TestCase):
    def test_portable_deployment_overrides_without_altering_frozen_binding(self):
        config = yaml.safe_load(CONFIG.read_text())
        config["namespace"] = "other-experiments"
        config["artifact_pvc"] = "other-artifacts"
        config["collector_node"] = "other-master"
        config["deployment"]["gpu_count"] = 2
        config["deployment"]["nsight_host_path"] = "/opt/site/nsight/2026.5.1"
        config["deployment"]["nsight_cli"] = "/opt/pre6g/nsight/target-linux-x64/nsys"
        config["candidates"][0]["resources"] = {"requests": {"cpu": "2", "memory": "4Gi"}}
        for c in config["candidates"]:
            c["node"] = "renamed-" + c["device_id"].lower()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = yaml.safe_load(Path(DRYRUN.ROOT / config["source_job"]).read_text())
            source["metadata"]["namespace"] = config["namespace"]
            for section in ("requests", "limits"):
                source["spec"]["template"]["spec"]["containers"][0]["resources"][section]["nvidia.com/gpu"] = "2"
            source_path = root / "source.yaml"
            source_path.write_text(yaml.safe_dump(source))
            config["source_job"] = str(source_path)
            path = root / "config.yaml"
            path.write_text(yaml.safe_dump(config))
            plan, jobs, collector = DRYRUN.prepare(path, run_id="portable", worker_commit=COMMIT)
        self.assertEqual(collector["metadata"]["namespace"], "other-experiments")
        self.assertEqual(collector["spec"]["tolerations"], config["collector_tolerations"])
        self.assertEqual(plan["candidates"][0]["power_binding_node"], config["candidates"][0]["power_binding_node"])
        for job, candidate in zip(jobs, config["candidates"]):
            spec = job["spec"]["template"]["spec"]
            self.assertEqual(spec["nodeSelector"]["kubernetes.io/hostname"], candidate["node"])
            self.assertEqual(spec["containers"][0]["resources"]["limits"]["nvidia.com/gpu"], "2")
            self.assertNotIn("nvidia.com/gpu.shared", str(spec))
            self.assertEqual(spec["volumes"][1]["persistentVolumeClaim"]["claimName"], "other-artifacts")
            command = spec["containers"][0]["command"][2]
            self.assertIn("provenance/worker.txt", command)
            self.assertIn('cp "$LOCAL/profile.nsys-rep" "$LOCAL/profile.sqlite"', command)

    def test_rejects_uuid_rebinding_and_missing_explicit_v2_deployment(self):
        config = yaml.safe_load(CONFIG.read_text())
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "config.yaml"
            bad = copy.deepcopy(config)
            bad["candidates"][0]["gpu_uuid"] = "GPU-0000"
            path.write_text(yaml.safe_dump(bad))
            with self.assertRaisesRegex(ValueError, "GPU UUID differs"):
                DRYRUN.prepare(path, run_id="wrong-uuid", worker_commit=COMMIT)
            config.pop("deployment")
            path.write_text(yaml.safe_dump(config))
            with self.assertRaisesRegex(ValueError, "explicit deployment"):
                DRYRUN.prepare(path, run_id="missing-deployment", worker_commit=COMMIT)

    def test_taint_matching_is_specific(self):
        taint = {"key": "node-role.kubernetes.io/control-plane", "effect": "NoSchedule"}
        self.assertTrue(DRYRUN._tolerates(taint, {"key": taint["key"], "operator": "Exists", "effect": "NoSchedule"}))
        self.assertFalse(DRYRUN._tolerates(taint, {"key": "unrelated", "operator": "Exists"}))
        self.assertFalse(DRYRUN._tolerates(taint, {"key": taint["key"], "operator": "Exists", "effect": "NoExecute"}))

    def test_preflight_accepts_cephfs_and_control_plane_taint(self):
        plan, jobs, _ = DRYRUN.prepare(CONFIG, run_id="ceph", worker_commit=COMMIT)
        calls = []
        def invoke(command, **kwargs):
            calls.append(command)
            if command[:3] == ["kubectl", "config", "current-context"]:
                return "ctx"
            if "pv" in command:
                return json.dumps({"status": {"phase": "Bound"}, "spec": {"accessModes": ["ReadWriteMany"],
                    "claimRef": {"namespace": plan["namespace"], "name": plan["artifact_pvc"]},
                    "csi": {"driver": "rook-ceph.cephfs.csi.ceph.com", "volumeHandle": "test-only"}}})
            if "pvc" in command:
                return json.dumps({"status": {"phase": "Bound"}, "spec": {"accessModes": ["ReadWriteMany"],
                    "volumeName": "test-ceph", "storageClassName": "ceph-filesystem"}})
            if "node" in command:
                return json.dumps({"spec": {"taints": [{"key": "node-role.kubernetes.io/control-plane", "effect": "NoSchedule"}]}
                    if plan["collector_node"] in command else {}, "status": {
                    "conditions": [{"type": "Ready", "status": "True"}], "allocatable": {"nvidia.com/gpu": "20"}}})
            if "jobs" in command:
                return '{"items": []}'
            if "pods" in command:
                return json.dumps({"items": [{"metadata": {"name": "exporter-" + str(i)}, "spec": {"nodeName": c["node"]},
                    "status": {"phase": "Running", "podIP": "192.0.2." + str(i+1),
                    "conditions": [{"type": "Ready", "status": "True"}]}} for i,c in enumerate(plan["candidates"])]})
            if "--raw" in command:
                return "\n".join(f'{metric}{{UUID="{c["gpu_uuid"]}"}} 1' for c in plan["candidates"]
                    for metric in ("DCGM_FI_DEV_GPU_UTIL", "DCGM_FI_DEV_FB_USED", "DCGM_FI_DEV_GPU_TEMP", "DCGM_FI_DEV_POWER_USAGE"))
            return "{}"
        with tempfile.TemporaryDirectory() as raw, patch.object(DRYRUN, "_invoke", side_effect=invoke), patch.object(DRYRUN, "_check_control_side_dependencies"), patch.object(DRYRUN.shutil, "which", return_value="kubectl"):
            DRYRUN._preflight(plan, jobs, "ctx", Path(raw))
            self.assertFalse(any("create" in c or "delete" in c for c in calls))
            with patch.object(DRYRUN, "_tolerates", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "blocking taints"):
                    DRYRUN._preflight(plan, jobs, "ctx", Path(raw))

    def test_exporter_wrong_uuid_fails_before_job_submission(self):
        plan, _, _ = DRYRUN.prepare(CONFIG, run_id="uuid", worker_commit=COMMIT)
        pod = {"metadata": {"name": "dcgm"}, "spec": {"nodeName": plan["candidates"][0]["node"]},
               "status": {"phase": "Running", "podIP": "192.0.2.1", "conditions": [{"type": "Ready", "status": "True"}]}}
        with tempfile.TemporaryDirectory() as raw, patch.object(DRYRUN, "_invoke", side_effect=[json.dumps({"items": [pod]}), 'DCGM_FI_DEV_GPU_UTIL{UUID="GPU-wrong"} 1']):
            with self.assertRaisesRegex(RuntimeError, "expected GPU UUID"):
                DRYRUN._resolve_dcgm_urls(plan, "ctx", Path(raw))

    def test_changed_frozen_hash_blocks_inference(self):
        plan, _, _ = DRYRUN.prepare(CONFIG, run_id="hash", worker_commit=COMMIT)
        plan["candidates"][0]["runtime_model_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as raw, patch.object(DRYRUN, "_invoke") as invoke:
            with self.assertRaisesRegex(ValueError, "frozen artifact changed"):
                DRYRUN._predict(plan, Path(raw))
            invoke.assert_not_called()

    def test_v1_legacy_environment_still_loads_with_its_original_templates(self):
        import subprocess
        baseline = "ab2a20c7ded276a179d48f48a1efcc5afdb4afad"
        def old(path):
            return subprocess.check_output(["git", "show", f"{baseline}:{path}"], cwd=DRYRUN.ROOT, text=True)
        config = yaml.safe_load(old("examples/yolo26/cross-node-dryrun.yaml"))
        with tempfile.TemporaryDirectory() as raw:
            for index, candidate in enumerate(config["candidates"]):
                template = Path(raw) / f"profile-{index}.yaml"
                template.write_text(old(candidate["profile_job_template"]))
                candidate["profile_job_template"] = str(template)
            path = Path(raw) / "legacy.yaml"
            path.write_text(yaml.safe_dump(config))
            plan, jobs, _ = DRYRUN.prepare(path, run_id="legacy", worker_commit=COMMIT)
        self.assertEqual(plan["deployment"]["gpu_resource"], "nvidia.com/gpu.shared")
        self.assertEqual(plan["deployment"]["nsight_host_path"], "/opt/nvidia/nsight-systems-cli/2026.4.1")
        self.assertIn("/opt/pre6g/nsight/bin/nsys", jobs[0]["spec"]["template"]["spec"]["containers"][0]["command"][2])

    def test_actual_csv_binding_is_checked_before_power_inference(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            start = 1_000_000_000_000
            (root / "window.json").write_text(json.dumps({"schema_version": "pre6g.application-window/v1",
                "command": ["yolo", "detect", "train"], "started_at_unix_ns": start,
                "finished_at_unix_ns": start + 11_000_000_000}))
            (root / "aligned.csv").write_text("timestamp_ns,node,gpu_uuid\n" +
                "".join(f"{start+i*1_000_000_000},wrong-node,GPU-wrong\n" for i in range(12)))
            with self.assertRaisesRegex(ValueError, "node binding mismatch"):
                DRYRUN._crop_telemetry(root / "aligned.csv", root / "window.json", root / "output.csv",
                    expected_node="expected-node", expected_uuid="GPU-correct")

    def test_published_config_requires_private_site_configuration(self):
        for name in ("cross-node-dryrun.yaml", "formal-cross-node-dryrun.yaml"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "UUID is invalid or a placeholder"):
                DRYRUN.prepare(DRYRUN.ROOT / "examples/yolo26" / name,
                    run_id="needs-config", worker_commit=COMMIT)
