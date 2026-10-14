"""Preflight and collect the unprofiled, all-node ground-truth Jobs."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(args: list[str], *, required: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if required and result.returncode:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def _kubectl(context: str, *args: str) -> list[str]:
    return ["kubectl", "--context", context, *args]


def check_inputs(plan: dict, jobs: list[dict]) -> tuple[str, list[tuple[str, str]]]:
    if plan.get("schema_version") != "pre6g.validation-plan/v1":
        raise ValueError("expected pre6g.validation-plan/v1")
    planned = {row["node"]: row["job_name"] for row in plan["jobs"]}
    if len(planned) < 2 or len(planned) != len(plan["jobs"]) or len(jobs) != len(planned):
        raise ValueError("ground truth requires exactly one Job for each of at least two nodes")
    namespace = None
    seen = set()
    common_workload = None
    for job in jobs:
        if job.get("apiVersion") != "batch/v1" or job.get("kind") != "Job":
            raise ValueError("validation file must contain only batch/v1 Jobs")
        metadata = job["metadata"]
        node = metadata.get("labels", {}).get("pre6g.io/candidate-node")
        if node not in planned or metadata.get("name") != planned[node] or node in seen:
            raise ValueError("validation Job name/node does not match the frozen plan")
        if metadata.get("labels", {}).get("pre6g.io/validation-id") != plan["validation_id"]:
            raise ValueError("validation ID differs from the frozen plan")
        spec = job["spec"]["template"]["spec"]
        if spec.get("nodeSelector", {}).get("kubernetes.io/hostname") != node:
            raise ValueError("validation Job is pinned to the wrong node")
        if len(spec.get("containers", [])) != 1 or spec["containers"][0].get("name") != "trainer":
            raise ValueError("validation Job must have one trainer container")
        if "nsys" in str(spec["containers"][0]).lower():
            raise ValueError("ground-truth trainer must not contain Nsight")
        if job["spec"].get("backoffLimit") != 0:
            raise ValueError("validation Job must set backoffLimit=0 to prevent retries")
        if namespace is None:
            namespace = metadata.get("namespace")
        if namespace != metadata.get("namespace"):
            raise ValueError("validation Jobs span namespaces")
        comparable = copy.deepcopy(job)
        comparable["metadata"].pop("name", None)
        comparable["metadata"].get("labels", {}).pop("pre6g.io/candidate-node", None)
        comparable["spec"]["template"].get("metadata", {}).get("labels", {}).pop("pre6g.io/candidate-node", None)
        comparable["spec"]["template"]["spec"].get("nodeSelector", {}).pop("kubernetes.io/hostname", None)
        if common_workload is not None and comparable != common_workload:
            raise ValueError("candidate validation Jobs have different workloads")
        common_workload = comparable
        seen.add(node)
    if not namespace or namespace != plan.get("namespace") or seen != set(planned):
        raise ValueError("validation Job namespace or candidate set is incomplete")
    return namespace, sorted(planned.items())


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight or run frozen all-node ground truth Jobs and collect Kubernetes evidence")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--kube-context", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=10800)
    parser.add_argument("--execute", action="store_true", help="Create the real unprofiled training Jobs")
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    record_path = output / "ground-truth-run.json"
    if record_path.exists():
        parser.error("output directory already has a ground-truth record; use a new directory")
    record = {"schema_version": "pre6g.ground-truth-run/v1", "started_at": _now(), "status": "started",
              "validation_plan_sha256": _sha256(args.plan), "validation_jobs_sha256": _sha256(args.jobs),
              "kube_context": args.kube_context, "executed": args.execute}
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        jobs = list(yaml.safe_load_all(args.jobs.read_text(encoding="utf-8")))
        namespace, candidates = check_inputs(plan, jobs)
        record.update({"validation_id": plan["validation_id"], "namespace": namespace, "candidates": [node for node, _ in candidates]})
        current = _run(["kubectl", "config", "current-context"]).stdout.strip()
        if current != args.kube_context:
            raise RuntimeError(f"current kube context {current!r} differs from requested {args.kube_context!r}")
        for node, _ in candidates:
            data = json.loads(_run(_kubectl(args.kube_context, "get", "node", node, "-o", "json")).stdout)
            ready = any(item.get("type") == "Ready" and item.get("status") == "True" for item in data.get("status", {}).get("conditions", []))
            if not ready or int(data.get("status", {}).get("allocatable", {}).get("nvidia.com/gpu.shared", "0")) < 1:
                raise RuntimeError(f"{node}: not Ready or no shared GPU is allocatable")
        _run(_kubectl(args.kube_context, "-n", namespace, "apply", "--dry-run=server", "-f", str(args.jobs)))
        existing = json.loads(_run(_kubectl(args.kube_context, "-n", namespace, "get", "jobs", "-o", "json")).stdout)
        names = {item.get("metadata", {}).get("name") for item in existing.get("items", [])}
        if any(name in names for _, name in candidates):
            raise RuntimeError("a validation Job name already exists; use a new validation ID")
        record["status"] = "preflight_passed"
        if args.execute:
            _run(_kubectl(args.kube_context, "-n", namespace, "create", "-f", str(args.jobs)))
            record["status"] = "submitted"
            for _, name in candidates:
                _run(_kubectl(args.kube_context, "-n", namespace, "wait", "--for=condition=complete", f"job/{name}", f"--timeout={args.timeout_seconds}s"))
            record["status"] = "jobs_completed"
    except (OSError, ValueError, KeyError, RuntimeError, json.JSONDecodeError) as exc:
        record.update({"status": "failed", "error": str(exc)})
    finally:
        if record.get("executed") and record.get("status") in {"submitted", "jobs_completed", "failed"} and record.get("namespace"):
            namespace = record["namespace"]
            label = f"pre6g.io/validation-id={record['validation_id']}"
            errors = []
            for filename, kind, selector in (("validation-pods.json", "pods", True),
                                             ("validation-jobs.json", "jobs", True),
                                             ("events.json", "events", False)):
                try:
                    command = _kubectl(args.kube_context, "-n", namespace, "get", kind)
                    if selector:
                        command.extend(["-l", label])
                    result = _run([*command, "-o", "json"], required=False)
                    if result.returncode:
                        errors.append(f"{kind}: {result.stderr.strip()}")
                    else:
                        (output / filename).write_text(result.stdout, encoding="utf-8")
                except OSError as exc:
                    errors.append(f"{kind}: {exc}")
            for node, name in candidates:
                try:
                    logs = _run(_kubectl(args.kube_context, "-n", namespace, "logs", f"job/{name}", "--all-containers=true"), required=False)
                    (output / f"{node}.log").write_text(logs.stdout + logs.stderr, encoding="utf-8")
                except OSError as exc:
                    errors.append(f"{node} logs: {exc}")
            if errors:
                record["collection_errors"] = errors
                record["status"] = "failed"
        record["finished_at"] = _now()
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0 if record["status"] in {"preflight_passed", "jobs_completed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
