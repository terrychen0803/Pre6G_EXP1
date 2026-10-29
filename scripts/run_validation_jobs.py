"""Preflight and collect the unprofiled, all-node ground-truth Jobs."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(args: list[str], *, required: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, timeout=45)
    if required and result.returncode:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def _kubectl(context: str, *args: str) -> list[str]:
    return ["kubectl", "--context", context, "--request-timeout=30s", *args]


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
        if "activeDeadlineSeconds" in job["spec"] or "activeDeadlineSeconds" in spec:
            raise ValueError("Ground Truth must not have activeDeadlineSeconds; regenerate the Jobs")
        if job["spec"].get("completions", 1) != 1 or "successPolicy" in job["spec"]:
            raise ValueError("Ground Truth requires one complete workload, without early successPolicy")
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


POLL_SECONDS = 30


def _save(path: Path, value: dict) -> None:
    """Atomic checkpoint: a killed monitor leaves either the old or new record."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _checkpoint(output: Path, record: dict) -> None:
    record["updated_at"] = _now()
    # Invalidate any previous evaluation export while an invocation is incomplete.
    export = output / "validation-pods.json"
    if not record.get("ground_truth_valid") and export.exists():
        payload = json.loads(export.read_text(encoding="utf-8"))
        payload["ground_truth_valid"] = False
        _save(export, payload)
    _save(output / "ground-truth-run.json", record)


def _subset(expected, actual) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(_subset(a, b) for a, b in zip(expected, actual))
    return expected == actual


def _get(context: str, namespace: str, kind: str, label: str | None = None) -> dict:
    args = _kubectl(context, "-n", namespace, "get", kind)
    if label:
        args += ["-l", label]
    return json.loads(_run([*args, "-o", "json"]).stdout)


def _poll(context: str, plan: dict, jobs: list[dict], record: dict) -> dict:
    raw = _get(context, plan["namespace"], "jobs", f"pre6g.io/validation-id={plan['validation_id']}")
    visible = {job["metadata"]["name"]: job for job in raw.get("items", [])}
    states = record.setdefault("job_states", {})
    identity_errors = []
    # Inspect the complete candidate set on every poll, including failures while peers run.
    for expected in jobs:
        metadata = expected["metadata"]
        name = metadata["name"]
        node = metadata["labels"]["pre6g.io/candidate-node"]
        state = states.setdefault(node, {"job_name": name})
        actual = visible.get(name)
        if actual is None:
            state.update(status="Missing", eligible_for_ground_truth=False)
            identity_errors.append(f"{name}: missing; no Job is created automatically on resume")
            continue
        uid = actual["metadata"].get("uid")
        live_spec = actual.get("spec", {})
        deadline_or_early_success = ("activeDeadlineSeconds" in live_spec
            or "activeDeadlineSeconds" in live_spec.get("template", {}).get("spec", {})
            or "successPolicy" in live_spec or live_spec.get("completions", 1) != 1)
        if (deadline_or_early_success or not uid or (state.get("uid") and uid != state["uid"])
                or not _subset(metadata.get("labels", {}), actual["metadata"].get("labels", {}))
                or not _subset(expected["spec"], actual.get("spec", {}))):
            state.update(status="IdentityMismatch", eligible_for_ground_truth=False)
            identity_errors.append(f"{name}: UID, labels, deadline or frozen workload changed")
            continue
        status = actual.get("status", {})
        conditions = status.get("conditions", [])
        terminal = next((c for kind in ("Failed", "Complete") for c in conditions
                         if c.get("type") == kind and c.get("status") == "True"), None)
        failing = any(c.get("type") == "FailureTarget" and c.get("status") == "True" for c in conditions)
        state.update(uid=uid, status=terminal["type"] if terminal else ("Failing" if failing else "Running"),
                     start_time=status.get("startTime"), completion_time=status.get("completionTime"),
                     conditions=conditions, observed_at=_now())
        if terminal:
            state.setdefault("terminal_observed_at", _now())
            state["terminal_transition_time"] = terminal.get("lastTransitionTime")
            state["reason"] = terminal.get("reason")
            state["message"] = terminal.get("message")
        if state["status"] != "Complete":
            state["eligible_for_ground_truth"] = False
    record["identity_errors"] = identity_errors
    return raw


def _pod_result(pods: list[dict], node: str) -> dict:
    result = {"eligible_for_ground_truth": False, "pod_count": len(pods)}
    if len(pods) != 1:
        result["invalid_reason"] = "requires exactly one Pod; missing Pods/retries require review"
        return result
    pod = pods[0]
    trainer = next((s for s in pod.get("status", {}).get("containerStatuses", []) if s.get("name") == "trainer"), {})
    terminated = trainer.get("state", {}).get("terminated", {})
    result.update(pod_name=pod["metadata"]["name"], trainer_exit_code=terminated.get("exitCode"),
                  trainer_started_at=terminated.get("startedAt"), trainer_finished_at=terminated.get("finishedAt"))
    try:
        start = datetime.fromisoformat(result["trainer_started_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(result["trainer_finished_at"].replace("Z", "+00:00"))
        valid_time = start.utcoffset() is not None and end.utcoffset() is not None and end > start
    except (TypeError, ValueError, AttributeError):
        valid_time = False
    result["eligible_for_ground_truth"] = (pod.get("status", {}).get("phase") == "Succeeded"
        and terminated.get("exitCode") == 0 and trainer.get("restartCount", 0) == 0
        and pod.get("spec", {}).get("nodeName") == node and valid_time)
    if valid_time:
        result["trainer_elapsed_s"] = (end - start).total_seconds()
    return result


def collect_evidence(context: str, plan: dict, output: Path, record: dict) -> None:
    """Save raw evidence even for failure/interruption; never mutate cluster resources."""
    sequence = record.get("evidence_sequence", 0) + 1
    record["evidence_sequence"] = sequence
    folder = output / "evidence" / f"{sequence:06d}"
    folder.mkdir(parents=True, exist_ok=True)
    errors = []
    objects = {}
    label = f"pre6g.io/validation-id={plan['validation_id']}"
    for kind, filename in (("jobs", "validation-jobs.json"), ("pods", "validation-pods.json"), ("events", "events.json")):
        try:
            value = _get(context, plan["namespace"], kind, label if kind != "events" else None)
            objects[kind] = value
            _save(folder / filename, value)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{kind}: {exc}")
    pods = objects.get("pods", {}).get("items", [])
    states = record.get("job_states", {})
    for row in plan["jobs"]:
        node, name = row["node"], row["job_name"]
        state = states.setdefault(node, {"job_name": name, "status": "Unknown"})
        selected = [p for p in pods if any(o.get("uid") == state.get("uid") and o.get("kind") == "Job"
                    for o in p.get("metadata", {}).get("ownerReferences", [])) and state.get("uid")]
        result = _pod_result(selected, node)
        live = next((j for j in objects.get("jobs", {}).get("items", []) if j.get("metadata", {}).get("name") == name), {})
        live_complete = (live.get("metadata", {}).get("uid") == state.get("uid")
                         and any(c.get("type") == "Complete" and c.get("status") == "True"
                                 for c in live.get("status", {}).get("conditions", []))
                         and not any(c.get("type") in {"Failed", "FailureTarget"} and c.get("status") == "True"
                                     for c in live.get("status", {}).get("conditions", [])))
        state.pop("invalid_reason", None)
        state.update({k: v for k, v in result.items() if k != "eligible_for_ground_truth"})
        state["eligible_for_ground_truth"] = state.get("status") == "Complete" and live_complete and result["eligible_for_ground_truth"]
        combined = []
        evidence_pods = [p for p in pods if p.get("metadata", {}).get("labels", {}).get("job-name") == name]
        # Capture every Pod (including failed attempts) and every init/app container.
        for pod in evidence_pods:
            pod_name = pod["metadata"]["name"]
            for container in [*pod.get("spec", {}).get("initContainers", []), *pod.get("spec", {}).get("containers", [])]:
                container_name = container["name"]
                log_name = f"{pod_name}.{container_name}.log"
                try:
                    logs = _run(_kubectl(context, "-n", plan["namespace"], "logs", pod_name, "-c", container_name, "--timestamps=true"), required=False)
                    text = logs.stdout + logs.stderr
                    (folder / log_name).write_text(text, encoding="utf-8")
                    combined.append(f"=== {pod_name}/{container_name} ===\n" + text)
                    if logs.returncode:
                        errors.append(f"{pod_name}/{container_name}: log collection failed: {logs.stderr.strip()}")
                    statuses = [*pod.get("status", {}).get("initContainerStatuses", []),
                                *pod.get("status", {}).get("containerStatuses", [])]
                    if any(c.get("name") == container_name and c.get("restartCount", 0) > 0 for c in statuses):
                        previous = _run(_kubectl(context, "-n", plan["namespace"], "logs", pod_name,
                            "-c", container_name, "--timestamps=true", "--previous"), required=False)
                        previous_text = previous.stdout + previous.stderr
                        (folder / f"{pod_name}.{container_name}.previous.log").write_text(previous_text, encoding="utf-8")
                        combined.append(f"=== {pod_name}/{container_name} previous ===\n" + previous_text)
                        if previous.returncode:
                            errors.append(f"{pod_name}/{container_name}: previous log collection failed")
                except (OSError, subprocess.TimeoutExpired) as exc:
                    errors.append(f"{pod_name}/{container_name} logs: {exc}")
        (folder / f"{node}.log").write_text("\n".join(combined), encoding="utf-8")
        (output / f"{node}.log").write_text("\n".join(combined), encoding="utf-8")
    record["collection_errors"] = errors
    record["ground_truth_valid"] = (not errors and not record.get("identity_errors")
        and record.get("status") == "jobs_completed"
        and all(states.get(row["node"], {}).get("eligible_for_ground_truth") for row in plan["jobs"]))
    if "pods" in objects:
        # Raw API response is retained in evidence/. This marked copy is the evaluation input.
        _save(output / "validation-pods.json", {**objects["pods"], "ground_truth_valid": record["ground_truth_valid"]})
    for kind, filename in (("jobs", "validation-jobs.json"), ("events", "events.json")):
        if kind in objects:
            _save(output / filename, objects[kind])
    _save(folder / "ground-truth-metadata.json", record)
    record["last_evidence_directory"] = str(folder.relative_to(output))


def monitor(context: str, plan: dict, jobs: list[dict], output: Path, record: dict, timeout_seconds: int = 0) -> str:
    """Monitor all Jobs together; timeout bounds this attachment, never Job lifetime."""
    started = time.monotonic()
    record["status"] = "monitoring"
    record["ground_truth_valid"] = False
    _checkpoint(output, record)
    while True:
        if timeout_seconds and time.monotonic() - started >= timeout_seconds:
            record["status"] = "timed_out"
            return "timed_out"
        tick = time.monotonic()
        before = {node: state.get("status") for node, state in record.get("job_states", {}).items()}
        try:
            raw = _poll(context, plan, jobs, record)
            _save(output / "validation-jobs.json", raw)
            record["last_poll_error"] = None
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            record["last_poll_error"] = str(exc)
            record["poll_error_count"] = record.get("poll_error_count", 0) + 1
            _checkpoint(output, record)
        else:
            states = record["job_states"]
            terminal = all(states[row["node"]]["status"] in {"Complete", "Failed"} for row in plan["jobs"])
            if record["identity_errors"]:
                record["status"] = "monitoring_failed"
            elif terminal:
                record["status"] = "jobs_completed" if all(s["status"] == "Complete" for s in states.values()) else "jobs_failed"
            else:
                record["status"] = "monitoring"
            _checkpoint(output, record)
            changed = any(s["status"] in {"Complete", "Failed", "Failing"} and before.get(n) != s["status"] for n, s in states.items())
            if changed or terminal or record["identity_errors"]:
                collect_evidence(context, plan, output, record)
                _checkpoint(output, record)
            print("[ground-truth] " + ", ".join(f"{n}={s['status']}" for n, s in states.items()), flush=True)
            if terminal or record["identity_errors"]:
                if record["status"] == "jobs_completed" and not record["ground_truth_valid"]:
                    record["status"] = "collection_failed"
                return record["status"]
        elapsed = time.monotonic() - started
        if timeout_seconds and elapsed >= timeout_seconds:
            record["status"] = "timed_out"
            return "timed_out"
        delay = max(0, POLL_SECONDS - (time.monotonic() - tick))
        if timeout_seconds:
            delay = min(delay, max(0, timeout_seconds - elapsed))
        time.sleep(delay)


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight, monitor or reconnect to all-node Ground Truth Jobs without terminating them")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--kube-context", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=0, help="Monitor deadline per invocation; 0 waits indefinitely. Never deletes Jobs.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Create frozen Jobs, then monitor all candidates")
    mode.add_argument("--resume", action="store_true", help="Reconnect using the same output record and frozen inputs; never creates Jobs")
    mode.add_argument("--attach", action="store_true", help="Monitor existing matching Jobs into a new output directory; never creates Jobs")
    args = parser.parse_args()
    if args.timeout_seconds < 0:
        parser.error("--timeout-seconds must be non-negative (0 = unlimited)")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = output / "ground-truth-run.json"
    hashes = {"validation_plan_sha256": _sha256(args.plan), "validation_jobs_sha256": _sha256(args.jobs), "kube_context": args.kube_context}
    if args.resume:
        if not path.exists():
            parser.error("--resume requires an existing ground-truth-run.json; use --attach for a new record")
        record = json.loads(path.read_text(encoding="utf-8"))
        if any(record.get(k) != v for k, v in hashes.items()) or not record.get("executed") or record.get("monitoring_version") != 2:
            parser.error("resume requires the same frozen plan/jobs/context and a v2 monitoring record")
    else:
        if path.exists():
            parser.error("record already exists; use --resume or a new output directory")
        record = {"schema_version": "pre6g.ground-truth-run/v1", "monitoring_version": 2,
                  "started_at": _now(), "status": "started", "executed": bool(args.execute or args.attach), **hashes}
    record.pop("error", None)
    record["ground_truth_valid"] = False
    record["timeout_seconds"] = args.timeout_seconds
    record.setdefault("attempts", []).append({"started_at": _now(), "timeout_seconds": args.timeout_seconds,
                                            "mode": "resume" if args.resume else "attach" if args.attach else "execute" if args.execute else "preflight"})
    record.pop("finished_at", None)
    plan = None
    observing = bool(args.execute or args.resume or args.attach)
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    previous_sigterm = signal.signal(signal.SIGTERM, interrupted)
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        jobs = list(yaml.safe_load_all(args.jobs.read_text(encoding="utf-8")))
        namespace, candidates = check_inputs(plan, jobs)
        record.update(validation_id=plan["validation_id"], namespace=namespace, candidates=[n for n, _ in candidates],
                      planned_total_work_units=plan.get("planned_total_work_units"),
                      runtime_definition="trainer container startedAt/finishedAt; excludes scheduling and init containers")
        _checkpoint(output, record)
        if _run(["kubectl", "config", "current-context"]).stdout.strip() != args.kube_context:
            raise RuntimeError("current kube context differs from requested context")
        if not args.resume and not args.attach:
            for node, job_name in candidates:
                data = json.loads(_run(_kubectl(args.kube_context, "get", "node", node, "-o", "json")).stdout)
                ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in data.get("status", {}).get("conditions", []))
                job = next(j for j in jobs if j["metadata"]["name"] == job_name)
                resources = job["spec"]["template"]["spec"]["containers"][0].get("resources", {})
                gpu = {k: v for k, v in resources.get("requests", {}).items() if k in {"nvidia.com/gpu", "nvidia.com/gpu.shared"}}
                if len(gpu) != 1 or any(resources.get("limits", {}).get(k) != v for k, v in gpu.items()):
                    raise ValueError("validation Job must request and limit one consistent GPU resource")
                key, quantity = next(iter(gpu.items()))
                if int(quantity) < 1 or not ready or int(data.get("status", {}).get("allocatable", {}).get(key, 0)) < int(quantity):
                    raise RuntimeError(f"{node}: not Ready or requested GPU resource is unavailable")
            _run(_kubectl(args.kube_context, "-n", namespace, "apply", "--dry-run=server", "-f", str(args.jobs)))
            names = {j["metadata"]["name"] for j in _get(args.kube_context, namespace, "jobs").get("items", [])}
            if any(name in names for _, name in candidates):
                raise RuntimeError("validation Job already exists; use --attach, never resubmit")
            record["status"] = "preflight_passed"
            _checkpoint(output, record)
            if args.execute:
                # Checkpoint first so even a partial create or hard kill can be reattached safely.
                record["status"] = "submitting"
                _checkpoint(output, record)
                _run(_kubectl(args.kube_context, "-n", namespace, "create", "-f", str(args.jobs)))
        if observing:
            record["status"] = monitor(args.kube_context, plan, jobs, output, record, args.timeout_seconds)
    except KeyboardInterrupt:
        record.update(status="interrupted", ground_truth_valid=False)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
        record.update(status="monitoring_failed" if observing else "failed", error=str(exc), ground_truth_valid=False)
    finally:
        # No delete, patch, suspend, cancellation, or workload timeout is issued here.
        _checkpoint(output, record)
        if observing and plan is not None and record.get("namespace"):
            try:
                # A signal/transport failure can occur between creation and the first poll.
                # Refresh identity/status without resubmitting or changing monitor exit status.
                _poll(args.kube_context, plan, jobs, record)
            except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                record["last_poll_error"] = str(exc)
            try:
                collect_evidence(args.kube_context, plan, output, record)
            except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                record.setdefault("collection_errors", []).append(str(exc))
                record["ground_truth_valid"] = False
            if record["status"] == "jobs_completed" and not record.get("ground_truth_valid"):
                record["status"] = "collection_failed"
        record["finished_at"] = _now()  # End of monitoring invocation, never trainer runtime.
        record["attempts"][-1].update(finished_at=record["finished_at"], status=record["status"], error=record.get("error"))
        _checkpoint(output, record)
        signal.signal(signal.SIGTERM, previous_sigterm)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    if record["status"] == "interrupted":
        return 130
    if record["status"] == "preflight_passed":
        return 0
    return 0 if record["status"] == "jobs_completed" and record.get("ground_truth_valid") else 2


if __name__ == "__main__":
    raise SystemExit(main())
