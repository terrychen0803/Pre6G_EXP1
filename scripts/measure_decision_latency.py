"""Replay master-side cross-node prediction and ranking to measure decision latency.

This standalone benchmark does not create Kubernetes resources or change the
normal cross-node execution path. Artifact copying and benchmark setup happen
before the timed interval; the timed interval is exactly dryrun._predict().
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import run_cross_node_dryrun as dryrun

PREDICTION_ARTIFACTS = (
    "profile-result.json",
    "runtime-features.json",
    "telemetry/alignment-quality.json",
    "telemetry/aligned-telemetry.csv",
    "telemetry/application-window.json",
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _summary(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {
        "mean_ms": sum(values) / len(values),
        "median_ms": _percentile(values, 0.5),
        "p95_ms": _percentile(values, 0.95),
        "max_ms": max(values),
    }


def _check_run(run_dir: Path) -> tuple[dict[str, Any], str]:
    plan = _read_json(run_dir / "cross-node-plan.json")
    summary = _read_json(run_dir / "cross-node-run-summary.json")
    ranking = _read_json(run_dir / "provisional-ranking.json")
    if plan.get("schema_version") != "pre6g.cross-node-dryrun-plan/v1":
        raise ValueError("unsupported cross-node plan schema")
    if summary.get("status") != "completed" or summary.get("run_id") != plan.get("run_id"):
        raise ValueError("benchmark requires a completed cross-node run")
    selected_node = ranking.get("selected_node")
    if not selected_node or selected_node != summary.get("selected_node") or ranking.get("task_id") != plan.get("run_id"):
        raise ValueError("completed run summary and ranking disagree")
    source_job = Path(plan["source_job"])
    if not source_job.is_file() or _digest(source_job) != plan["source_job_sha256"]:
        raise ValueError("source Job is missing or changed since the completed run")
    if plan.get("full_job_eta_input"):
        eta_path = Path(plan["full_job_eta_input"])
        if not eta_path.is_file() or _digest(eta_path) != plan["full_job_eta_input_sha256"]:
            raise ValueError("full-job ETA input is missing or changed")
    candidates = plan.get("candidates") or []
    if len(candidates) < 2:
        raise ValueError("benchmark requires at least two candidates")
    for candidate in candidates:
        node = candidate["node"]
        model = Path(candidate["runtime_model"])
        bundle = Path(candidate["power_bundle"])
        for path, field in (
            (model, "runtime_model_sha256"),
            (bundle / "manifest.yaml", "power_manifest_sha256"),
            (bundle / "model.onnx", "power_model_sha256"),
            (bundle / "scaler.json", "power_scaler_sha256"),
        ):
            if not path.is_file() or _digest(path) != candidate[field]:
                raise ValueError(f"{node}: missing or changed frozen input: {path}")
        artifacts = dryrun.ARTIFACTS + (
            dryrun.LONG_DETECTOR_ARTIFACTS if plan.get("long_detector_profile") else ()
        )
        for relative in artifacts:
            if not (run_dir / "artifacts" / node / relative).is_file():
                raise ValueError(f"{node}: missing artifact: {relative}")
    return plan, str(selected_node)


def _replay_once(plan: dict[str, Any], run_dir: Path, expected_node: str, scratch_parent: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="decision-latency-", dir=scratch_parent) as raw:
        scratch = Path(raw)
        artifacts = PREDICTION_ARTIFACTS + (("detector-v2/trajectory.json",) if plan.get("long_detector_profile") else ())
        for candidate in plan["candidates"]:
            node = candidate["node"]
            for relative in artifacts:
                source = run_dir / "artifacts" / node / relative
                destination = scratch / "artifacts" / node / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        commands: list[dict[str, Any]] = []
        original_invoke = dryrun._invoke

        def timed_invoke(command: list[str], *, cwd: Path, log: Path, env: dict[str, str] | None = None) -> str:
            started = time.perf_counter_ns()
            try:
                return original_invoke(command, cwd=cwd, log=log, env=env)
            finally:
                script = Path(command[1]).name if len(command) > 1 else Path(command[0]).name
                node = log.stem.removeprefix("predict-") if log.stem.startswith("predict-") else None
                commands.append({
                    "script": script,
                    "node": node,
                    "duration_ms": (time.perf_counter_ns() - started) / 1_000_000,
                })

        started = time.perf_counter_ns()
        with patch.object(dryrun, "_invoke", timed_invoke):
            dryrun._predict(plan, scratch)
        duration_ms = (time.perf_counter_ns() - started) / 1_000_000
        ranking = _read_json(scratch / "provisional-ranking.json")
        if ranking.get("selected_node") != expected_node:
            raise ValueError("replayed ranking differs from the completed run")
        return {
            "duration_ms": duration_ms,
            "selected_node": expected_node,
            "commands": commands,
            "other_processing_ms": max(0.0, duration_ms - sum(row["duration_ms"] for row in commands)),
        }


def benchmark(run_dir: Path, output: Path, *, repeats: int, warmups: int, threshold_ms: float) -> dict[str, Any]:
    if repeats < 1 or warmups < 0 or not math.isfinite(threshold_ms) or threshold_ms <= 0:
        raise ValueError("repeats, warmups, or threshold_ms is invalid")
    plan, selected_node = _check_run(run_dir)
    if output.parent.resolve().is_relative_to((run_dir / "artifacts").resolve()):
        raise ValueError("benchmark output must be outside the input artifacts directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index in range(warmups + repeats):
        try:
            sample = _replay_once(plan, run_dir, selected_node, output.parent)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            errors.append({"iteration": index + 1, "warmup": index < warmups, "error": str(exc)})
            break
        if index >= warmups:
            sample["iteration"] = index - warmups + 1
            samples.append(sample)
    durations = [sample["duration_ms"] for sample in samples]
    statistics = _summary(durations)
    command_groups: dict[str, list[float]] = {}
    for sample in samples:
        for command in sample["commands"]:
            label = f"{command['script']}:{command['node'] or 'shared'}"
            command_groups.setdefault(label, []).append(command["duration_ms"])
    completed = len(samples) == repeats and not errors
    report = {
        "schema_version": "pre6g.decision-latency-benchmark/v1",
        "mode": "offline-master-replay",
        "status": "completed" if completed else "failed",
        "run_id": plan["run_id"],
        "candidate_count": len(plan["candidates"]),
        "selected_node": selected_node,
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "definition": "All artifacts already local; time dryrun._predict through ranking JSON creation",
        "excluded": ["worker profiling", "Kubernetes Job polling", "collector Pod startup", "artifact transfer", "benchmark artifact copying"],
        "threshold_ms": threshold_ms,
        "warmups": warmups,
        "requested_repeats": repeats,
        "completed_repeats": len(samples),
        "statistics": statistics,
        "command_statistics": {name: _summary(values) for name, values in sorted(command_groups.items())},
        "other_processing_statistics": _summary([sample["other_processing_ms"] for sample in samples]),
        "mean_kpi_pass": bool(completed and statistics and statistics["mean_ms"] <= threshold_ms),
        "samples": samples,
        "errors": errors,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True, help="Completed cross-node output directory")
    parser.add_argument("--output", type=Path, help="Benchmark JSON; defaults inside run-dir")
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--warmups", type=int, default=0)
    parser.add_argument("--threshold-ms", type=float, default=100.0)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    output = (args.output or run_dir / "decision-latency-benchmark.json").resolve()
    try:
        report = benchmark(run_dir, output, repeats=args.repeats, warmups=args.warmups, threshold_ms=args.threshold_ms)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps({key: report[key] for key in ("status", "run_id", "completed_repeats", "statistics", "mean_kpi_pass")}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "completed" else 2


if __name__ == "__main__":
    sys.exit(main())

